"""Scheduled refresh: read the first pages of NLRB's three decision listings, merge into the Hugging Face
table, upload.

    python src/refresh.py            # uploads if HF_TOKEN is set and something changed
    python src/refresh.py --dry-run  # fetch and merge, write out/decisions.parquet, upload nothing

The listings are sorted newest first, so the first PAGES pages (100 rows each) hold anything new. Rows are
keyed by record_id (listing + decision document id); a fresh copy of a row replaces the old one, and rows
no longer on the first pages are kept. A page that parses to an empty/odd "no results" page is retried a
few times (NLRB's site can serve one of those transiently); the run stops without uploading if a fetch
fails, a page still parses to nothing or with problems after retries, or the merge would shrink the table.
"""
import os, sys, time
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import build, crawl, nlrb
from hf_retry import hf_retry

REPO = "abigailhaddad/nlrb-decisions"
FILE = "decisions.parquet"
PAGES = 3
# A page that returns 200 but parses to 0 rows (e.g. "There are no Board Decisions
# matching the...") is NLRB's site serving an empty-results page, not a network error,
# so crawl.get()'s own retry (which only catches non-200s and firewall pages) never
# sees it. Retry that case here, on the chance it's a transient glitch on their end.
EMPTY_PAGE_RETRIES = 3
EMPTY_PAGE_WAIT = 60


def merge(old, new):
    """New wins by record_id; rows only in old are kept."""
    kept = old[~old["record_id"].isin(set(new["record_id"]))]
    return pd.concat([new, kept], ignore_index=True)


def fetch_page(listing, url, p):
    """One listing page, retrying an empty/odd-row-count result a few times before giving up
    (NLRB's site can briefly serve a 200 'no results' page instead of real rows)."""
    for attempt in range(1, EMPTY_PAGE_RETRIES + 1):
        page = crawl.get(f"{url}?items_per_page={crawl.PER_PAGE}&page={p}")
        d, problems = nlrb.parse_page(page, listing, f"{listing}/{p}")
        if len(d) == crawl.PER_PAGE and not problems:
            return d
        if attempt < EMPTY_PAGE_RETRIES:
            print(f"retry {listing} page {p}: parsed to {len(d)} rows with problems {problems[:3]}", flush=True)
            time.sleep(EMPTY_PAGE_WAIT)
    sys.exit(f"STOP: {listing} page {p} parsed to {len(d)} rows with problems {problems[:3]} "
             f"after {EMPTY_PAGE_RETRIES} tries; nothing uploaded")


def fetch_new():
    rows = []
    for listing, url in nlrb.LISTINGS.items():
        for p in range(PAGES):
            rows += fetch_page(listing, url, p)
    df = pd.DataFrame(rows).drop_duplicates(["listing", "document_url"])
    return build.finalize(df)


def changed_rows(old, new):
    """How many fetched rows are new or differ from the stored copy."""
    cols = [c for c in build.COLS if c in old.columns and c in new.columns]
    norm = lambda d: d.set_index("record_id")[cols[1:]].astype(object).where(lambda x: x.notna(), "").astype(str)
    o, n = norm(old), norm(new)
    added = n.index.difference(o.index)
    both = n.index.intersection(o.index)
    diff = (o.loc[both] != n.loc[both]).any(axis=1)
    return len(added), int(diff.sum())


def main():
    dry = "--dry-run" in sys.argv
    old = hf_retry(pd.read_parquet, f"https://huggingface.co/datasets/{REPO}/resolve/main/{FILE}")
    new = fetch_new()
    added, updated = changed_rows(old, new)
    merged = merge(old, new)[old.columns.tolist()]
    if len(merged) < len(old):
        sys.exit("STOP: the merge would shrink the table; nothing uploaded")
    assert merged["record_id"].is_unique
    merged = merged.sort_values(["issuance_date", "listing", "citation", "case_number"],
                                ascending=[False, True, True, True], na_position="last").reset_index(drop=True)
    print({"rows": len(merged), "added": added, "updated": updated,
           "by_listing": merged["listing"].value_counts().to_dict()})
    os.makedirs("out", exist_ok=True)
    merged.to_parquet(f"out/{FILE}", index=False)
    if dry:
        print("dry run: wrote out/decisions.parquet, uploaded nothing")
        return
    if added == 0 and updated == 0:
        print("nothing new; nothing uploaded")
        return
    if not os.environ.get("HF_TOKEN"):
        print("SKIP UPLOAD: the HF_TOKEN secret is empty. Add a Hugging Face write token as the repository "
              "secret HF_TOKEN to publish. Nothing was uploaded.")
        return
    from huggingface_hub import HfApi
    hf_retry(HfApi().upload_file, path_or_fileobj=f"out/{FILE}", path_in_repo=FILE, repo_id=REPO, repo_type="dataset",
             commit_message=f"Refresh: +{added} new, {updated} updated")


if __name__ == "__main__":
    main()
