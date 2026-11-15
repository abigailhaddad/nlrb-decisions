"""Write the Hugging Face dataset card (out/README.md) from out/decisions.parquet, out/report.json and
out/gaps.json, so every number on the card comes from the files being published.

    python src/card.py 2026-11-15     # the date the listings were read
"""
import json, sys
import pandas as pd

read_on = sys.argv[1]
df = pd.read_parquet("out/decisions.parquet")
rep = json.load(open("out/report.json"))
gaps = {int(k): v for k, v in json.load(open("out/gaps.json")).items()}
o = rep["overall"]
n = lambda x: f"{x:,}"
by = df.groupby("listing")["issuance_date"].agg(["min", "max", "size"])
b = df[df["listing"] == "board"]
slip = b[b["citation_form"] == "slip opinion number"]
page = b[b["citation_form"] == "bound volume page"]
recent = {v: g for v, g in gaps.items() if v >= 366}
missing_recent = sum(len(g["missing"]) for g in recent.values())
repeated_recent = sum(len(g["repeated"]) for g in recent.values())
miss_list = "; ".join(f"{v}: No. " + ", ".join(map(str, g["missing"])) for v, g in sorted(recent.items()) if g["missing"])
rb, ru, ra = rep["board"], rep["unpublished"], rep["alj"]
u = df[df["listing"] == "unpublished"]
u_pre2000, u_nodate = int((u["issuance_date"] < "2000").sum()), int(u["issuance_date"].isna().sum())
a_0001 = int((df["listing"].eq("alj") & df["issuance_date_raw"].eq("01/01/0001")).sum())
assert ru["only_in_full_pass"] == u_pre2000 + u_nodate and ru["only_in_second_pass"] == 0, "unpublished passes: explain the new gap"
b_nocite = int((df["listing"].eq("board") & df["citation"].fillna("").eq("")).sum())
assert rb["only_in_full_pass"] == b_nocite and rb["only_in_second_pass"] == 0, "Board passes: explain the new gap"
sample = json.load(open(f"checks/sample_{read_on}.json"))
slipcheck = json.load(open(f"checks/slip_gaps_live_{read_on}.json"))
assert all(x["live_rows_with_this_citation"] == (0 if x["kind"] == "missing" else 2) for x in slipcheck)
assert ra["only_in_full_pass"] == a_0001 and ra["only_in_second_pass"] == 0, "ALJ passes: explain the new gap"

card = f"""---
license: cc0-1.0
pretty_name: NLRB decisions (listing metadata)
tags:
- labor
- law
- government
size_categories:
- 10K<n<100K
configs:
- config_name: default
  data_files: decisions.parquet
---

# NLRB decisions: Board, unpublished Board, and administrative law judge

Metadata for every entry on the National Labor Relations Board's three public decision listings, with links to each decision document and case page. Not affiliated with the NLRB. Listings read {read_on}; a daily job adds new entries (see the commit history for the latest refresh).

`decisions.parquet` has {n(len(df))} rows, one per listing entry:

| listing | `listing` | rows | issued |
|---|---|---|---|
| [Board Decisions](https://www.nlrb.gov/cases-decisions/decisions/board-decisions) (published; bound volumes and slip opinions) | `board` | {n(by.loc['board','size'])} | {by.loc['board','min']} to {by.loc['board','max']} |
| [Unpublished Board Decisions](https://www.nlrb.gov/cases-decisions/decisions/unpublished-board-decisions) | `unpublished` | {n(by.loc['unpublished','size'])} | {by.loc['unpublished','min']} to {by.loc['unpublished','max']} |
| [Administrative Law Judge Decisions](https://www.nlrb.gov/cases-decisions/decisions/administrative-law-judge-decisions) | `alj` | {n(by.loc['alj','size'])} | {by.loc['alj','min']} to {by.loc['alj','max']} |

Of the Board decisions, {n(len(page))} have a bound-volume citation (e.g. `300 NLRB 1065`) and {n(len(slip))} a slip-opinion citation (e.g. `375 NLRB No. 68`), as the listing shows them.

## Columns

- `record_id`: listing plus NLRB's document id, unique per row
- `listing`, `listing_title`: which of the three listings the entry is on. This is the only decision type the listings give. Each case page's docket has its own label for the document; in the spot check, published Board decisions were all "Board Decision" and unpublished ones included "Order Adopting in the Absence of Exceptions" and "Amended Board Decision". Those labels are not in this table
- `citation`, `volume`, `citation_form`, `citation_number`: Board decisions only. `citation` is as listed; the other three are read from it
- `case_name`, `case_number`: as listed. `case_number_usual_form` is false for {n(o['case_number_not_usual_form'])} case numbers that don't look like `13-CA-393585` (old formats such as `AD-50`, `00-C-000005`, or typos)
- `issuance_date` (YYYY-MM-DD) and `issuance_date_raw` (as listed), `year`
- `jd_number`, `judge`, `board_outcome`: ALJ decisions only, as listed. `board_outcome` is blank for {n(int((df['listing'].eq('alj') & df['board_outcome'].fillna('').eq('')).sum()))} ALJ rows
- `document_url`, `document_id`: the decision document on apps.nlrb.gov
- `case_url`: the case page on nlrb.gov

## How complete it is

Each listing was read twice: page by page (100 rows a page, newest first), and again filtered one volume at a time (Board) or one year at a time (unpublished, ALJ). The two passes were joined on the decision document link.

- Board: the page-by-page pass read {n(rb['full_pass_rows'])} rows with no repeats, which is what NLRB's pager implies ({rb['full_pass_pages']} pages, {n(rb['nlrb_pager_implied_total'])} rows). The by-volume pass read {n(rb['second_pass_rows'])} rows over {rb['second_pass_filters']} volume filters. {n(rb['only_in_full_pass'])} rows were only in the first pass and {n(rb['only_in_second_pass'])} only in the second. The {b_nocite} only in the first pass are the {b_nocite} Board decisions with no citation, which no volume filter returns.
- Unpublished: {n(ru['full_pass_rows'])} rows, matching the pager. The by-year pass found {n(ru['second_pass_rows'])}. The {ru['only_in_full_pass']} it missed are the {u_pre2000} dated before 2000 (the year menu starts at 2000) and the {u_nodate} with no date. Nothing was only in the by-year pass.
- ALJ: {n(ra['full_pass_rows'])} rows, matching the pager. The by-year pass found {n(ra['second_pass_rows'])}. The {ra['only_in_full_pass']} it missed are exactly the rows NLRB dates 01/01/0001, which no year filter returns. Nothing was only in the by-year pass.
- Slip-opinion numbers in volumes 366 to 375 should run from 1 up without gaps. {missing_recent} numbers are absent from the listing and {repeated_recent} number appears twice ({miss_list}). Each was looked up again on the live listing by volume and slip number: none of the missing numbers is there, and the repeated one shows two decisions. Volumes 365 and earlier are mostly listed by bound-volume page, so their slip numbers can't be checked this way.
- A live spot check of {len(sample)} rows (25 random, 10 random from 2011 on, and {len(sample) - 35} edge cases) is in the GitHub repo, `checks/sample_{read_on}.md`. Each row was looked up again in its listing, filtered to that one decision: {sum(x['all_listing_fields_match'] for x in sample)} of {len(sample)} matched on every field, and the one difference is the 01/01/0001 date this table blanks on purpose. Each row's case page was also read: {sum(x['docket_match'] for x in sample)} docket entries link the same document on the same date. {sum(x['case_page_empty'] for x in sample)} case pages (mostly decisions before 2010) have no docket to compare, and for the rest see the file.

## Caveats

- No decision text and no Board members. The documents sit under `apps.nlrb.gov/link/`, which NLRB's robots.txt disallows, so they were not fetched.
- {n(o['date_shown_as_01/01/0001'])} ALJ decisions are dated 01/01/0001 on the listing; `issuance_date` is blank for those (and for {n(o['no_date'] - o['date_shown_as_01/01/0001'])} rows the listing gives no date). {o['board_date_over_2_years_from_volume_median']} Board decisions have a date more than two years from the median date of their volume, including {o['date_before_nlra_1935-07-05']} dated before the NLRA was signed (1935-07-05). They are kept as listed; they look like data-entry errors on NLRB's side.
- Long case names are often cut off. {n(o['case_name_exactly_100_chars'])} names are exactly 100 characters long, against a few dozen at each nearby length, and the ones I looked at stop mid-word (e.g. "...Red Rock Station, Sant"). Treat a 100-character name as truncated. The cut isn't applied everywhere: {n(o['case_name_over_100_chars'])} names are longer than 100.
- {o['board_blank_citation']} Board decisions have no citation and {o['board_placeholder_citation']} show `00 NLRB No. 00`. {n(o['board_citation_shared_by_2plus_documents'])} rows share a citation with at least one other document (NLRB lists more than one document under that citation; I didn't check why).
- The table never shrinks: an entry NLRB later removes from a listing stays.

Code, checks and the refresh workflow: https://github.com/abigailhaddad/nlrb-decisions
"""
open("out/README.md", "w").write(card)
print(card)
