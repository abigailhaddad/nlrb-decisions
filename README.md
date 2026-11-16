# nlrb-decisions

A table of the decisions on the National Labor Relations Board's three public decision listings, on Hugging Face: https://huggingface.co/datasets/abigailhaddad/nlrb-decisions. Not affiliated with the NLRB.

- [Board Decisions](https://www.nlrb.gov/cases-decisions/decisions/board-decisions): published decisions, bound volumes and slip opinions
- [Unpublished Board Decisions](https://www.nlrb.gov/cases-decisions/decisions/unpublished-board-decisions)
- [Administrative Law Judge Decisions](https://www.nlrb.gov/cases-decisions/decisions/administrative-law-judge-decisions)

One row per listing entry: which listing it is on, citation, case name, case number, issuance date, the link to the decision document, the link to the case page, and for ALJ decisions the JD number, judge and Board outcome. Metadata only. There is no decision text and no list of Board members, because the decision documents live under `apps.nlrb.gov/link/`, which NLRB's robots.txt disallows. The dataset card on Hugging Face has the counts, the as-of date and the caveats.

## Run it

    pip install -r requirements-dev.txt
    python -m pytest tests
    python src/crawl.py all        # every page of the three listings, ~2 s a page, raw HTML to raw/ (gzipped)
    python src/crawl.py years      # unpublished and ALJ listings again, one year at a time
    python src/crawl.py volumes    # Board listing again, one volume at a time
    python src/build.py            # raw/ -> out/decisions.parquet and out/report.json
    python src/check.py gaps       # slip-opinion numbering gaps per volume
    python src/check.py sample 25  # re-fetch 25 random rows plus edge cases live -> checks/sample_<date>.md
    python src/check.py slipgaps   # look up each missing slip number on the live listing
    python src/card.py 2026-11-15  # the Hugging Face dataset card, numbers read from out/ and checks/
    python src/refresh.py --dry-run

The full crawl reads each listing twice, once page by page and once filtered by volume or year, and unions the two by decision document. `out/report.json` says what each pass found and what NLRB's pager says the total should be. The copies in `checks/` are from the first full build, read on 2026-11-15: 77,709 rows (66,645 Board, 5,592 unpublished, 5,472 ALJ), every gap between the passes explained on the dataset card.

## Weekly summaries

`summaries.parquet` (also on Hugging Face) has the text of NLRB's weekly [Summary of NLRB Decisions](https://www.nlrb.gov/cases-decisions/decisions/weekly-summaries-decisions) pages, one row per summarized decision, matched to `decisions.parquet` rows where exactly one row fits. This is NLRB's own short summary, not the decision text. The pages are on www.nlrb.gov, which robots.txt allows; each URL is checked against robots.txt before it is fetched.

    python src/summaries_crawl.py all                  # 42 index pages and every week page (828 on 2026-11-15) to raw/summaries/
    python src/summaries_build.py out/decisions.parquet # parse, match -> out/summaries.parquet, out/summaries_report.json
    python src/summaries_check.py parsecheck           # a looser second read of the pages, to catch merged or split entries
    python src/summaries_check.py coverage out/decisions.parquet
    python src/summaries_check.py sample 30 out/decisions.parquet   # live spot check -> checks/summaries_sample_<date>.md
    python src/card.py 2026-11-15 2026-11-15

The 2026-11-15 run: 11,696 entries from 828 weekly pages (824 with at least one entry), January 2011 to November 2026. 9,907 of the 10,915 Board, unpublished and ALJ entries are matched; the 781 appellate court entries are never matched. Unmatched entries and their reasons are in `checks/summaries_unmatched_2026-11-15.csv`.

## Scheduled refresh

`.github/workflows/refresh.yml` runs daily at 11:25 UTC. It reads the first 3 pages (100 rows each) of each listing, merges them into the Hugging Face table by `record_id`, and uploads only if a row was added or changed. Rows are never dropped, so a decision NLRB removes from a listing stays in the table.

**You have to add the repository secret `HF_TOKEN`** (a Hugging Face token with write access to `abigailhaddad/nlrb-decisions`). Until you do, the workflow fetches and checks, then prints that the secret is empty and skips the upload.

A second step (`src/summaries_refresh.py`) reads the first 2 pages of the weekly-summaries index, fetches any week not yet in `summaries.parquet`, matches every entry again against the decisions table, and uploads if something is new. It never re-fetches a week already in the table, so a later correction NLRB makes to an old week page is not picked up.

If more than 300 entries were added to one listing between runs, some would be past page 3; rerun the full crawl and `build.py` to catch up.

## Data dictionary

[`docs/data_dictionary.md`](docs/data_dictionary.md) lists every column of both tables with its type, a description, the share of rows that have a value, and an example, plus a diagram of how the tables join. Types, counts and examples are read from the published parquet files; descriptions are in [`docs/tables.toml`](docs/tables.toml), written from the code that builds each column. Regenerate it with

    python src/data_docs.py fetch data   # download the current files from Hugging Face
    python src/data_docs.py write data   # write docs/data_dictionary.md

`src/card.py` puts the same section on the Hugging Face dataset card. Both stop with an error if a parquet column has no description in `docs/tables.toml` or a described column is gone, and the daily workflow runs that check too.

## SQLite download

The daily workflow builds `nlrb-decisions.sqlite` from the files on Hugging Face (tables `decisions` and `summaries`, plus `_source`, which records the Hugging Face commit) and attaches it to the GitHub release [`latest-data`](https://github.com/abigailhaddad/nlrb-decisions/releases/tag/latest-data). The release is replaced whenever the Hugging Face commit changes. To build it yourself:

    python src/build_sqlite.py data nlrb-decisions.sqlite
