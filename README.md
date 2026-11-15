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

## Scheduled refresh

`.github/workflows/refresh.yml` runs daily at 11:25 UTC. It reads the first 3 pages (100 rows each) of each listing, merges them into the Hugging Face table by `record_id`, and uploads only if a row was added or changed. Rows are never dropped, so a decision NLRB removes from a listing stays in the table.

**You have to add the repository secret `HF_TOKEN`** (a Hugging Face token with write access to `abigailhaddad/nlrb-decisions`). Until you do, the workflow fetches and checks, then prints that the secret is empty and skips the upload.

If more than 300 entries were added to one listing between runs, some would be past page 3; rerun the full crawl and `build.py` to catch up.
