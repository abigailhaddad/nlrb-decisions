"""Parse every saved weekly summary page, match entries to decisions.parquet rows, write out/summaries.parquet
and out/summaries_report.json.

    python src/summaries_build.py [path/to/decisions.parquet]   # default out/decisions.parquet

Matching, in order. A match is only made when exactly one row qualifies; otherwise the entry stays
unmatched and `match_note` says why.
  1. citation: the entry's NLRB citation equals the row's (volume and number compared as numbers), and the
     row also shares a case number or the issuance date with the entry. If the citation matches rows but
     none agrees on case number or date, nothing is matched ("citation conflict").
  2. case number + date: one of the entry's case numbers equals the row's case number (compared as region,
     type, number) and the row's issuance date equals the entry's date. If more than one row qualifies,
     rows on the listing that corresponds to the entry's section are preferred; still more than one, no match.
Appellate court entries describe court decisions, which are not in decisions.parquet, so they are not
matched.
"""
import gzip, json, os, re, sys
from collections import Counter, defaultdict
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import summaries as S
import summaries_crawl as C

SECTION_LISTING = {"board": "board", "unpublished": "unpublished", "alj": "alj"}
COLS = ["summary_id", "record_id", "match_method", "match_note", "week_url", "week_title", "week_posted",
        "section", "subsection", "entry_index", "case_name", "case_numbers", "header_parenthetical", "citation",
        "jd_number", "reported_at", "court", "place", "date", "date_source", "header_date_text", "header",
        "summary_text"]


def cite_key(c):
    m = S.CITE.search(c) if isinstance(c, str) else None
    if not m or int(m.group(1)) == 0:
        return None
    return (int(m.group(1)), bool(m.group(2)), int(m.group(3)))


def case_key(c):
    m = S.CASE_FULL.fullmatch(c.strip()) if isinstance(c, str) else None
    return (int(m.group(1).replace(" ", "")), m.group(2), int(m.group(3))) if m else None


def load_entries():
    weeks = C.all_weeks()
    seen, rows, problems, week_counts = set(), [], [], []
    for posted, title, slug in weeks:
        if slug in seen:
            continue
        seen.add(slug)
        path = f"{C.RAW}/weeks/{slug}.html.gz"
        url = C.BASE + C.WEEK_PREFIX + slug
        if not os.path.exists(path):
            problems.append({"where": url, "why": "week page not fetched"})
            continue
        entries, probs = S.parse_week(gzip.open(path, "rt", encoding="utf-8").read(), url)
        problems += probs
        week_counts.append({"week_url": url, "entries": len(entries)})
        for i, e in enumerate(entries):
            e.update(week_url=url, week_title=title, week_posted=(posted or "")[:10] or None, entry_index=i,
                     summary_id=f"{slug}#{i}")
            rows.append(e)
    return rows, problems, week_counts


def match(entries, dec):
    dec = dec.copy()
    dec["cite_k"] = dec["citation"].map(cite_key)
    dec["case_k"] = dec["case_number"].map(case_key)
    by_cite, by_case_date = defaultdict(list), defaultdict(list)
    for r in dec[["record_id", "cite_k", "case_k", "issuance_date", "listing"]].itertuples(index=False):
        if r.cite_k:
            by_cite[r.cite_k].append(r)
        if r.case_k and r.issuance_date:
            by_case_date[(r.case_k, r.issuance_date)].append(r)
    for e in entries:
        e["record_id"], e["match_method"], e["match_note"] = None, None, None
        if e["section"] == "appellate":
            e["match_note"] = "court decision; not in decisions.parquet"
            continue
        cases = {case_key(c) for c in e["case_numbers"]} - {None}
        ck = cite_key(e.get("citation"))
        note = None
        if ck and by_cite.get(ck):
            cands = by_cite[ck]
            agree = [r for r in cands if r.case_k in cases or (e["date"] and r.issuance_date == e["date"])]
            if len(agree) > 1:
                agree = [r for r in agree if r.case_k in cases and r.issuance_date == e["date"]] or agree
            if len(agree) == 1:
                e["record_id"], e["match_method"] = agree[0].record_id, "citation"
                continue
            note = ("citation conflict: the row(s) with this citation share no case number or date" if not agree
                    else f"ambiguous: {len(agree)} rows with this citation")
        elif ck:
            note = "citation not in table"
        if not e["date"]:
            e["match_note"] = (note + "; " if note else "") + "no date"
            continue
        cands = list({r.record_id: r for c in cases for r in by_case_date.get((c, e["date"]), [])}.values())
        if len(cands) > 1:
            same = [r for r in cands if r.listing == SECTION_LISTING.get(e["section"])]
            cands = same if same else cands
        if len(cands) == 1:
            e["record_id"], e["match_method"], e["match_note"] = cands[0].record_id, "case number + date", note
        elif len(cands) > 1:
            e["match_note"] = (note + "; " if note else "") + f"ambiguous: {len(cands)} rows with this case number and date"
        else:
            e["match_note"] = (note + "; " if note else "") + "no row with this case number and date"
    return entries


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "out/decisions.parquet"
    dec = pd.read_parquet(src)
    entries, problems, week_counts = load_entries()
    entries = match(entries, dec)
    df = pd.DataFrame(entries)
    df["case_numbers"] = df["case_numbers"].map(lambda x: "; ".join(x))
    df = df[COLS].sort_values(["week_posted", "week_url", "entry_index"], ascending=[False, True, True]).reset_index(drop=True)
    assert df["summary_id"].is_unique
    os.makedirs("out", exist_ok=True)
    df.to_parquet("out/summaries.parquet", index=False)
    rep = {
        "decisions_source": src, "weeks_listed": len(C.all_weeks()), "weeks_parsed": len(week_counts),
        "weeks_with_no_entries": [w["week_url"] for w in week_counts if w["entries"] == 0],
        "entries": len(df), "by_section": df["section"].value_counts().to_dict(),
        "matched": int(df["record_id"].notna().sum()),
        "matched_by_method": df["match_method"].value_counts().to_dict(),
        "problems": problems,
    }
    json.dump(rep, open("out/summaries_report.json", "w"), indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in rep.items() if k != "problems"}, indent=1))
    print("problems:", len(problems), Counter(p["why"] for p in problems))


if __name__ == "__main__":
    main()
