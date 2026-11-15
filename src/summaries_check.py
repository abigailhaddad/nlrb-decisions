"""Checks on out/summaries.parquet.

    python src/summaries_check.py coverage [decisions.parquet]   # match rates, unmatched reasons -> checks/summaries_coverage_<date>.json
    python src/summaries_check.py sample [n] [decisions.parquet]  # n random matched pairs + edge cases, re-fetched live
                                                                  # -> checks/summaries_sample_<date>.md and .json

The live sample asks nlrb.gov two things per entry, neither of them the saved copy the table came from:
  1. the week page, fetched again: is the entry's header and summary text on it (compared after collapsing
     whitespace)?
  2. for a matched entry, NLRB's decision listing filtered to that one decision (check.live_listing): does the
     live row show a case number from the entry's header, the entry's date, and its citation if it has one?
"""
import datetime, html as htmllib, json, os, re, sys
from collections import Counter
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import check, crawl, summaries as S, summaries_build as B

TODAY = datetime.date.today().isoformat()


def load(dec_path):
    s = pd.read_parquet("out/summaries.parquet")
    d = pd.read_parquet(dec_path)
    return s, d


def reason(note):
    if note is None:
        return None
    for k in ("court decision", "citation conflict", "ambiguous", "no date", "no row with this case number and date"):
        if k in note:
            return k
    return note


def coverage(s, d):
    s = s.copy()
    s["year"] = s["date"].str[:4]
    s["matched"] = s["record_id"].notna()
    by_sec = s.groupby("section")["matched"].agg(["size", "sum"]).rename(columns={"size": "entries", "sum": "matched"})
    by_year = s[s["section"] != "appellate"].groupby("year")["matched"].agg(["size", "sum"])
    un = s[~s["matched"] & (s["section"] != "appellate")].copy()
    un["reason"] = un["match_note"].map(reason)
    # near misses: same case number on the table at a different date
    d = d.copy()
    d["case_k"] = d["case_number"].map(B.case_key)
    by_case = d.dropna(subset=["case_k"]).groupby("case_k")
    idx = {k: g for k, g in by_case}
    near = []
    for r in un.itertuples():
        best = None
        for c in r.case_numbers.split("; "):
            g = idx.get(B.case_key(c))
            if g is None or not r.date:
                continue
            for x in g.itertuples():
                if not x.issuance_date:
                    continue
                try:
                    gap = (datetime.date.fromisoformat(x.issuance_date) - datetime.date.fromisoformat(r.date)).days
                except ValueError:
                    continue
                if best is None or abs(gap) < abs(best[0]):
                    best = (gap, x.listing)
        cases_in_table = any(B.case_key(c) in idx for c in r.case_numbers.split("; "))
        near.append({"summary_id": r.summary_id, "section": r.section, "reason": r.reason,
                     "case_number_in_table": cases_in_table,
                     "nearest_gap_days": best[0] if best else None, "nearest_listing": best[1] if best else None})
    near = pd.DataFrame(near)
    un = un.merge(near[["summary_id", "case_number_in_table", "nearest_gap_days", "nearest_listing"]], on="summary_id", how="left")

    def bucket(r):
        if r["reason"] == "no date":
            return "entry has no date in its header or summary"
        if r["reason"] == "ambiguous":
            return "more than one row qualifies"
        if not r["case_number_in_table"]:
            return "none of the entry's case numbers is in the table"
        g = r["nearest_gap_days"]
        if g is not None and pd.notna(g) and abs(g) <= 7:
            return "case number in the table, date differs by 1 to 7 days"
        return "case number in the table, but only at dates more than 7 days away"
    un["bucket"] = un.apply(bucket, axis=1)

    lo, hi = s["date"].dropna().min(), s["date"].dropna().max()
    real = s["date"].dropna()
    real = real[real >= "2010"]
    lo, hi = real.min(), real.max()
    matched_ids = set(s["record_id"].dropna())
    inrange = d[(d["issuance_date"] >= lo) & (d["issuance_date"] <= hi)]
    nosum = inrange[~inrange["record_id"].isin(matched_ids)]
    rev = inrange.assign(has_summary=inrange["record_id"].isin(matched_ids)).groupby(
        ["listing", inrange["issuance_date"].str[:4]])["has_summary"].agg(["size", "sum"])
    dup = s["record_id"].dropna()
    out = {
        "as_of": TODAY, "entries": len(s), "weeks": int(s["week_url"].nunique()),
        "entry_dates": [lo, hi],
        "by_section": {k: {"entries": int(v.entries), "matched": int(v.matched)} for k, v in by_sec.iterrows()},
        "by_year_excluding_appellate": {k: {"entries": int(v["size"]), "matched": int(v["sum"])} for k, v in by_year.iterrows()},
        "match_method": s["match_method"].value_counts().to_dict(),
        "matched_with_citation_note": int((s["record_id"].notna() & s["match_note"].notna()).sum()),
        "unmatched_excluding_appellate": len(un),
        "unmatched_reason": un["reason"].value_counts().to_dict(),
        "unmatched_bucket": un["bucket"].value_counts().to_dict(),
        "unmatched_bucket_by_section": un.groupby(["section", "bucket"]).size().rename("n").reset_index().to_dict("records"),
        "records_matched_by_more_than_one_entry": int(dup.duplicated().sum()),
        "rows_in_entry_date_range": len(inrange), "rows_in_range_with_summary": int(len(inrange) - len(nosum)),
        "rows_in_range_without_summary_by_listing": nosum["listing"].value_counts().to_dict(),
        "rows_in_range_by_listing_year": [{"listing": k[0], "year": k[1], "rows": int(v["size"]), "with_summary": int(v["sum"])}
                                          for k, v in rev.iterrows()],
    }
    os.makedirs("checks", exist_ok=True)
    json.dump(out, open(f"checks/summaries_coverage_{TODAY}.json", "w"), indent=1)
    un.to_csv(f"checks/summaries_unmatched_{TODAY}.csv", index=False,
              columns=["summary_id", "week_url", "section", "case_name", "case_numbers", "citation", "date", "match_note",
                       "bucket", "nearest_gap_days", "nearest_listing"])
    print(json.dumps({k: v for k, v in out.items() if k not in ("rows_in_range_by_listing_year", "unmatched_bucket_by_section")}, indent=1))
    return out, un, nosum


def norm(t):
    return re.sub(r"\s+", " ", t).strip()


def live_week_text(url, cache):
    if url not in cache:
        page = crawl.get(url)
        body = S.body_html(page) or ""
        cache[url] = norm(" ".join(t for t, _ in S.blocks(body)))
    return cache[url]


def pick(s, n, rng=20261115):
    m = s[s["record_id"].notna()]
    picks = {sid: "random matched pair" for sid in m.sample(n, random_state=rng)["summary_id"]}

    def add(label, sub, k=1):
        sub = sub[~sub["summary_id"].isin(picks)]
        for sid in sub.sample(min(k, len(sub)), random_state=rng)["summary_id"]:
            picks[sid] = label
    multi = m[m["case_numbers"].str.contains(";")]
    add("matched, more than one case number in the header", multi, 2)
    add("matched, no citation (case number + date)", m[m["citation"].isna()], 2)
    add("matched, ALJ section", m[m["section"] == "alj"], 2)
    add("matched, unpublished section", m[m["section"] == "unpublished"], 2)
    add("matched, Board section by citation", m[m["match_method"] == "citation"], 1)
    add("matched by case number + date although the summary's citation disagrees", m[m["match_note"].notna()], 2)
    add("matched, ALJ date read from the summary text", m[m["date_source"] == "summary text"], 1)
    oldest = s[s["week_posted"] == s["week_posted"].min()]
    newest = s[s["week_posted"] == s["week_posted"].max()]
    add("oldest week, matched", oldest[oldest["record_id"].notna()], 1)
    add("oldest week, unmatched", oldest[oldest["record_id"].isna()], 1)
    add("newest week, matched", newest[newest["record_id"].notna()], 1)
    add("newest week, any", newest, 1)
    un = s[s["record_id"].isna() & (s["section"] != "appellate")]
    add("unmatched: no row with this case number and date", un[un["match_note"].fillna("").str.contains("no row")], 2)
    add("unmatched: ambiguous", un[un["match_note"].fillna("").str.contains("ambiguous")], 1)
    add("unmatched: no date", un[un["match_note"].fillna("").str.contains("no date")], 1)
    add("appellate court entry (never matched)", s[s["section"] == "appellate"], 1)
    return picks


def esc(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).replace("|", "\\|").replace("\n", " ")


def sample(s, d, n):
    picks = pick(s, n)
    cache, results = {}, []
    dd = d.set_index("record_id")
    for sid, why in picks.items():
        e = s[s["summary_id"] == sid].iloc[0].to_dict()
        live = live_week_text(e["week_url"], cache)
        header_on_page = norm(e["header"]) in live
        text_on_page = norm(e["summary_text"]) in live if e["summary_text"] else None
        res = {"summary_id": sid, "why": why, "week_url": e["week_url"], "section": e["section"], "header": e["header"],
               "summary_start": e["summary_text"][:300], "record_id": e["record_id"], "match_method": e["match_method"],
               "match_note": e["match_note"], "header_on_live_page": header_on_page, "summary_on_live_page": text_on_page}
        if e["record_id"]:
            r = dd.loc[e["record_id"]].to_dict()
            r["record_id"] = e["record_id"]
            lurl, row, nrows = check.live_listing(r)
            cases = {B.case_key(c) for c in e["case_numbers"].split("; ")}
            res.update(listing_url=lurl, listing_found=row is not None,
                       live_case_number=row and row["case_number"], live_date=row and row["issuance_date"],
                       live_citation=row and row.get("citation"), live_case_name=row and row["case_name"],
                       document_url=r["document_url"])
            res["case_number_agrees"] = bool(row) and B.case_key(row["case_number"]) in cases
            res["date_agrees"] = bool(row) and row["issuance_date"] == e["date"]
            res["citation_agrees"] = None if not e["citation"] else bool(row) and B.cite_key(row.get("citation")) == B.cite_key(e["citation"])
        results.append(res)
        print(sid, why, header_on_page, text_on_page, res.get("case_number_agrees"), res.get("date_agrees"), flush=True)
    json.dump(results, open(f"checks/summaries_sample_{TODAY}.json", "w"), indent=1, ensure_ascii=False)
    write_md(results)
    return results


def write_md(results):
    m = [x for x in results if x["record_id"]]
    md = [f"# Live spot check of summaries.parquet, {TODAY}", "",
          "Each entry below was checked against two live nlrb.gov pages, fetched again for this check (not the saved copies "
          "the table was built from): the weekly summary page, and for matched entries NLRB's decision listing filtered to "
          "that one decision. 'Header on page' and 'summary on page' mean the text in our table appears on the live week page "
          "after collapsing whitespace. For matched entries, the live listing row's case number, date and citation are compared "
          "with the summary header.", "",
          f"- entries checked: {len(results)} ({sum(x['why'] == 'random matched pair' for x in results)} random matched pairs, the rest edge cases)",
          f"- header found on the live week page: {sum(x['header_on_live_page'] for x in results)} of {len(results)}",
          f"- summary text found on the live week page: {sum(bool(x['summary_on_live_page']) for x in results)} of {sum(x['summary_on_live_page'] is not None for x in results)} with text",
          f"- matched entries: {len(m)}; live listing row found: {sum(x['listing_found'] for x in m)}; case number agrees: "
          f"{sum(x['case_number_agrees'] for x in m)}; date agrees: {sum(x['date_agrees'] for x in m)}; citation agrees: "
          f"{sum(x['citation_agrees'] is True for x in m)} of {sum(x['citation_agrees'] is not None for x in m)} with a citation", "",
          "| # | why picked | week page | header (from the week page) | match | live listing row | case no. | date | citation | text on page |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    yn = lambda v: "n/a" if v is None else ("yes" if v else "**NO**")
    for i, x in enumerate(results, 1):
        live = (f"[{esc(x.get('live_case_name'))} / {esc(x.get('live_case_number'))} / {esc(x.get('live_date'))} / "
                f"{esc(x.get('live_citation'))}]({x['listing_url']})" if x["record_id"] else "")
        match = f"{x['match_method']}" if x["record_id"] else f"unmatched: {esc(x['match_note'])}"
        if x["record_id"] and x["match_note"]:
            match += f" (note: {esc(x['match_note'])})"
        md.append(f"| {i} | {esc(x['why'])} | [week]({x['week_url']}) | {esc(x['header'])} | {match} | {live} | "
                  f"{yn(x.get('case_number_agrees')) if x['record_id'] else ''} | {yn(x.get('date_agrees')) if x['record_id'] else ''} | "
                  f"{yn(x.get('citation_agrees')) if x['record_id'] else ''} | {yn(x['header_on_live_page'])} / {yn(x['summary_on_live_page'])} |")
    md += ["", "## Summary text of each entry (first 300 characters)", ""]
    for i, x in enumerate(results, 1):
        md.append(f"{i}. {esc(x['summary_start'])}")
    open(f"checks/summaries_sample_{TODAY}.md", "w").write("\n".join(md) + "\n")


def parsecheck(s):
    """A second, looser read of the saved week pages, to find entries the parser may have merged or split.

    For each week, count paragraphs that look like an entry header by a loose rule (a parenthesis opening
    with a case number within the first 700 characters, any formatting), and compare with the parser's
    non-appellate entries. Also flag paragraphs inside a summary that look like a header (a missed split) and
    case names that read like prose (a false split)."""
    import gzip
    loose = re.compile(r"^.{3,700}?\(\s*\d{1,2}\s*[%s]\s*[A-Z]{1,2}" % S.DASH, re.S)
    out = {"weeks_where_counts_differ": [], "summary_paragraphs_that_look_like_headers": [], "prose_like_case_names": []}
    for url, g in s.groupby("week_url"):
        slug = url.rsplit("/", 1)[1]
        page = gzip.open(f"raw/summaries/weeks/{slug}.html.gz", "rt", encoding="utf-8").read()
        body = S.body_html(page) or ""
        sec, n_loose = None, 0
        for t, chunk in S.blocks(body):
            sx = S.section_of(t)
            if sx:
                sec = sx
                continue
            if sec in ("board", "unpublished", "alj") and loose.match(t) and not re.match(r"(?i)(charges? filed|petitioner|administrative law judge|the board|in )", t):
                n_loose += 1
        n_parsed = int((g["section"] != "appellate").sum())
        if n_loose != n_parsed:
            out["weeks_where_counts_differ"].append({"week_url": url, "loose_header_paragraphs": n_loose, "parsed_entries": n_parsed})
    for r in s.itertuples():
        for para in (r.summary_text or "").split("\n\n")[1:]:
            if loose.match(para) and S.HEAD.match(para) and S.DATE.search(para[:400]) and r.section != "appellate":
                out["summary_paragraphs_that_look_like_headers"].append({"summary_id": r.summary_id, "text": para[:200]})
        if re.search(r"\b(the Board|found|adopted|denied|granted|issued)\b", r.case_name or ""):
            out["prose_like_case_names"].append({"summary_id": r.summary_id, "case_name": r.case_name[:200]})
    json.dump(out, open(f"checks/summaries_parsecheck_{TODAY}.json", "w"), indent=1, ensure_ascii=False)
    print({k: len(v) for k, v in out.items()})
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "coverage":
        s, d = load(sys.argv[2] if len(sys.argv) > 2 else "out/decisions.parquet")
        coverage(s, d)
    elif what == "parsecheck":
        parsecheck(pd.read_parquet("out/summaries.parquet"))
    elif what == "sample":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        s, d = load(sys.argv[3] if len(sys.argv) > 3 else "out/decisions.parquet")
        sample(s, d, n)
    else:
        sys.exit(__doc__)
