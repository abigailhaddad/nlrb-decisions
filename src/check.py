"""Second checks on out/decisions.parquet, independent of the crawl that built it.

    python src/check.py gaps            # slip-opinion numbering gaps per volume -> out/gaps.json
    python src/check.py sample [n]      # n random rows + edge cases, re-fetched live -> checks/sample_<date>.md
    python src/check.py slipgaps        # ask the live listing about each gap in volumes 366+ -> checks/slip_gaps_live_<date>.json

The live sample asks nlrb.gov two questions per row, neither of them the paged listing the table came from:
  1. the same listing, filtered to that one decision (volume + slip number or page for Board decisions,
     the case number as the search term otherwise);
  2. the case's own page (nlrb.gov/case/<number>), whose docket should list the same decision document.
Each field is compared with what the live pages show.
"""
import datetime, html as htmllib, json, os, re, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import crawl, nlrb

TODAY = datetime.date.today().isoformat()


def gaps(df):
    """Within each volume of slip opinions, numbers should run 1..max. Missing and repeated numbers are listed."""
    s = df[df["citation_form"] == "slip opinion number"]
    out = {}
    for vol, g in s.groupby("volume"):
        nums = g["citation_number"].astype(int)
        mx = int(nums.max())
        missing = sorted(set(range(1, mx + 1)) - set(nums))
        repeated = sorted(nums[nums.duplicated()].unique().tolist())
        out[int(vol)] = {"rows": len(g), "max": mx, "missing": missing, "repeated": repeated}
    return out


def edge_cases(df, rng):
    picks = {}
    def add(label, sub):
        sub = sub[~sub["record_id"].isin(picks)]
        if len(sub):
            picks[sub.sample(1, random_state=rng).iloc[0]["record_id"]] = label
    b = df[df["listing"] == "board"]
    add("oldest decision in the table", df[df["issuance_date"] == df["issuance_date"].min()])
    add("newest Board decision", b[b["issuance_date"] == b["issuance_date"].max()])
    add("Board decision, blank citation", b[b["citation"].fillna("") == ""])
    add("Board decision, placeholder citation 00 NLRB No. 00", b[b["citation_form"] == "placeholder"])
    add("Board decision, citation shared with another document", b[b["volume"].notna() & b.duplicated("citation", keep=False)])
    add("case name exactly 100 characters (possible truncation)", df[df["case_name"].str.len() == 100])
    add("case name with an ampersand or apostrophe", df[df["case_name"].str.contains(r"[&']", regex=True)])
    add("lowest bound volume", b[b["volume"] == b["volume"].min()])
    u, a = df[df["listing"] == "unpublished"], df[df["listing"] == "alj"]
    add("oldest unpublished Board decision", u[u["issuance_date"] == u["issuance_date"].min()])
    add("oldest dated ALJ decision", a[a["issuance_date"] == a["issuance_date"].min()])
    add("ALJ decision dated 01/01/0001 on the listing", a[a["issuance_date_raw"] == "01/01/0001"])
    add("unpublished decision with no date", u[u["issuance_date"].isna()])
    add("Board decision dated before 1935-07-05", b[b["issuance_date"] < "1935-07-05"])
    add("case number not in the usual form", df[~df["case_number_usual_form"]])
    add("ALJ decision with blank Board outcome", df[(df["listing"] == "alj") & (df["board_outcome"].fillna("") == "")])
    add("no document link", df[df["document_url"].isna()])
    add("no case link", df[df["case_url"].isna()])
    return picks


def live_listing(r):
    if r["listing"] == "board" and pd.notna(r["volume"]):
        f = "slip_opinion_number" if r["citation_form"] == "slip opinion number" else "page_number"
        q = f"volume={int(r['volume'])}&{f}={int(r['citation_number'])}&"
    else:
        q = f"search_term={r['case_number']}&"
    url = nlrb.LISTINGS[r["listing"]] + f"?{q}items_per_page=100"
    rows, problems = nlrb.parse_page(crawl.get(url), r["listing"])
    hit = [x for x in rows if x["document_url"] == r["document_url"]] if r["document_url"] else \
          [x for x in rows if x["case_number"] == r["case_number"] and x["issuance_date"] == r["issuance_date"]]
    return url, (hit[0] if hit else None), len(rows)


def docket_rows(page):
    docket = page[page.find("docket-activity-table"):]
    docket = docket[:docket.find("</table>")]
    return [tr for tr in re.findall(r"<tr>(.*?)</tr>", docket, re.S) if "<td" in tr]


def live_docket(r):
    """The docket entry on the case page that links to the same document. The plain page shows the newest
    10 entries; if the document isn't there, ask for all of them (?items_per_page=1 means "All"). The
    site's firewall rejects that parameter on empty case pages, so the plain page is read first."""
    if not r["case_url"]:
        return None, None, None
    url = r["case_url"]
    page = crawl.get(url)
    if "<title>| National Labor Relations Board" in page:
        return url, {"date": None, "label": "(the case page is empty: no case data for this number)", "by": ""}, page
    rows = docket_rows(page)
    if not rows:
        return url, {"date": None, "label": "(the case page has no docket entries)", "by": ""}, page
    if not any(r["document_id"] in tr for tr in rows) and len(rows) >= 10:
        full = crawl.get(url + "?items_per_page=1", rejected_ok=True)
        if full:
            url, rows = url + "?items_per_page=1", docket_rows(full)
    for tr in rows:
        if r["document_id"] and r["document_id"] in tr:
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            return url, {"date": nlrb.iso_date(nlrb.text(tds[0])), "label": nlrb.text(tds[1]),
                         "by": nlrb.text(tds[2]) if len(tds) > 2 else ""}, page
    return url, None, page


def blank(v):
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else v


def esc(v):
    return str(v if v is not None and not (isinstance(v, float) and pd.isna(v)) else "").replace("|", "\\|")


def sample(df, n):
    rng = 20261115
    picks = {rid: "random" for rid in df.sample(n, random_state=rng)["record_id"]}
    # most rows are old Board decisions whose case pages are empty, so add rows recent enough to have a docket
    recent = df[(df["issuance_date"] >= "2011-01-01") & ~df["record_id"].isin(picks)]
    for rid in recent.sample(10, random_state=rng)["record_id"]:
        picks[rid] = "random, issued 2011 or later"
    for rid, label in edge_cases(df, rng).items():
        picks.setdefault(rid, label)
    fields = ["issuance_date", "citation", "case_name", "case_number", "document_url", "jd_number", "judge", "board_outcome"]
    results, md = [], []
    for rid, why in picks.items():
        r = df[df["record_id"] == rid].iloc[0].to_dict()
        lurl, live, nrows = live_listing(r)
        durl, dock, page = live_docket(r)
        comp = {}
        for f in fields:
            ours = r.get(f)
            if r["listing"] != "alj" and f in ("jd_number", "judge", "board_outcome"):
                continue
            if r["listing"] != "board" and f == "citation":
                continue
            theirs = live.get(f) if live else None
            comp[f] = (ours, theirs, blank(ours) == blank(theirs))
        comp["case_name_on_case_page"] = (r["case_name"], None, bool(page) and htmllib.escape(r["case_name"][:40], quote=False) in page
                                          or bool(page) and r["case_name"][:40] in htmllib.unescape(page))
        comp["docket_date"] = (r["issuance_date"], dock["date"] if dock else None, bool(dock) and dock["date"] == r["issuance_date"])
        if dock and dock["date"] is None and "empty" in dock["label"]:
            comp.pop("case_name_on_case_page")
        ok_listing = live is not None and all(v[2] for k, v in comp.items() if k in fields)
        results.append({"record_id": rid, "why": why, "listing_url": lurl, "listing_found": live is not None,
                        "listing_rows_returned": nrows, "case_url": durl, "docket_entry": dock,
                        "all_listing_fields_match": ok_listing,
                        "docket_match": bool(dock) and dock["date"] == r["issuance_date"],
                        "case_page_empty": bool(dock) and dock["date"] is None,
                        "no_docket_entry_for_document": dock is None,
                        "comparison": {k: [esc(a), esc(b), c] for k, (a, b, c) in comp.items()}})
        print(rid, why, "listing:", ok_listing, "docket:", results[-1]["docket_match"], flush=True)
    os.makedirs("checks", exist_ok=True)
    json.dump(results, open(f"checks/sample_{TODAY}.json", "w"), indent=1)
    write_md(results, n, rng, TODAY)
    return results


NOTES = """Notes on the differences below:

- `docket_date` reads "n/a" when the case page has nothing to compare: most decisions from before about 2010 have an
  empty case page, and a few have a case page with no docket entries.
- `ALJ decision dated 01/01/0001`: the live listing shows 01/01/0001; the table leaves `issuance_date` blank on purpose
  (`issuance_date_raw` keeps "01/01/0001").
- `unpublished decision with no date`: the listing has no date; the case page docket dates the same document 2011-02-17.
  The table keeps the listing's blank.
- `ALJ decision with blank Board outcome` (07-CA-052862): the ALJ listing has two documents for this case dated
  2010-12-03; the docket links only the other one.
- Lowe's HIW (21-RC-020900): the listing spells it "Lowe’s HIW, Inc." (curly apostrophe), the case page "Lowe's HIW Inc.",
  and that case page has no docket entries.
"""


def write_md(results, n, rng, day):
    md = [f"# Live spot check, {day}\n"]
    md.append(f"{n} random rows (seed {rng}), 10 random rows issued 2011 or later, and {len(results) - n - 10} edge cases "
              "from `out/decisions.parquet`, each re-fetched from nlrb.gov on the date above. For each row: the listing "
              "filtered to that one decision, and the case page's docket entry for the same document. `ours` is the table, "
              "`live` is the page.\n")
    md.append(f"Listing fields all match: {sum(x['all_listing_fields_match'] for x in results)} of {len(results)}. "
              f"Docket entry found with the same date: {sum(x['docket_match'] for x in results)} of {len(results)}; "
              f"case page empty or with no docket (nothing to compare): {sum(x['case_page_empty'] for x in results)}; "
              f"docket does not list this document: {sum(x['no_docket_entry_for_document'] for x in results)}.\n")
    md.append(NOTES)
    for x in results:
        md.append(f"## {x['record_id']} ({x['why']})\n")
        md.append(f"Filtered listing: {x['listing_url']} ({x['listing_rows_returned']} rows returned, "
                  f"{'match found' if x['listing_found'] else 'NO MATCH FOUND'})  ")
        md.append(f"Case page: {x['case_url']}  ")
        d = x["docket_entry"]
        if d and d["date"]:
            md.append(f"Docket entry: {d['date']} · {d['label']} · {d['by']}\n")
        else:
            md.append(f"Docket entry: {d['label'] if d else 'this document is not on the docket'}\n")
        md.append("| field | ours | live | same |\n|---|---|---|---|")
        for k, (a, b, c) in x["comparison"].items():
            same = "yes" if c else "NO"
            if k == "docket_date" and x["case_page_empty"]:
                same, b = "n/a", "(no docket)"
            if k == "case_name_on_case_page":
                b = "(searched the case page)"
            md.append(f"| {k} | {a} | {b} | {same} |")
        md.append("")
    open(f"checks/sample_{day}.md", "w").write("\n".join(md))


if __name__ == "__main__":
    df = pd.read_parquet("out/decisions.parquet")
    if sys.argv[1:2] == ["gaps"]:
        g = gaps(df)
        json.dump(g, open("out/gaps.json", "w"), indent=1)
        for v, x in sorted(g.items()):
            print(v, x["rows"], "max", x["max"], "missing", len(x["missing"]), x["missing"][:15], "repeated", x["repeated"][:10])
    elif sys.argv[1:2] == ["slipgaps"]:
        # ask the live listing, filtered by volume and slip number, about every gap and repeat in volumes 366+
        g = json.load(open("out/gaps.json"))
        out = []
        for v, x in sorted(g.items(), key=lambda kv: int(kv[0])):
            if int(v) < 366:
                continue
            for num in x["missing"] + x["repeated"]:
                url = nlrb.LISTINGS["board"] + f"?volume={v}&slip_opinion_number={num}&items_per_page=100"
                rows, _ = nlrb.parse_page(crawl.get(url), "board")
                exact = [r for r in rows if r["citation"] == f"{v} NLRB No. {num}"]
                out.append({"citation": f"{v} NLRB No. {num}", "kind": "missing" if num in x["missing"] else "repeated",
                            "live_rows_with_this_citation": len(exact), "live_rows_returned": len(rows),
                            "live": [(r["case_name"], r["issuance_date"], r["document_url"]) for r in exact], "url": url})
                print(out[-1]["citation"], out[-1]["kind"], "live:", len(exact), flush=True)
        json.dump(out, open(f"checks/slip_gaps_live_{TODAY}.json", "w"), indent=1)
    elif sys.argv[1:2] == ["md"]:   # rewrite the proof file from its saved JSON
        day = sys.argv[2]
        write_md(json.load(open(f"checks/sample_{day}.json")), 25, 20261115, day)
    elif sys.argv[1:2] == ["sample"]:
        sample(df, int(sys.argv[2]) if len(sys.argv) > 2 else 25)
    else:
        sys.exit(__doc__)
