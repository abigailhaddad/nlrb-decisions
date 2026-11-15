"""Scheduled refresh: read the first pages of NLRB's three decision listings, merge into the Hugging Face
table, upload.

    python src/refresh.py            # uploads if HF_TOKEN is set and something changed
    python src/refresh.py --dry-run  # fetch and merge, write out/decisions.parquet, upload nothing

The listings are sorted newest first, so the first PAGES pages (100 rows each) hold anything new. Rows are
keyed by record_id (listing + decision document id); a fresh copy of a row replaces the old one, and rows
no longer on the first pages are kept. The run stops without uploading if a fetch fails, a page parses to
nothing or with problems, or the merge would shrink the table.
"""
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import build, crawl, nlrb

REPO = "abigailhaddad/nlrb-decisions"
FILE = "decisions.parquet"
PAGES = 3


def merge(old, new):
    """New wins by record_id; rows only in old are kept."""
    kept = old[~old["record_id"].isin(set(new["record_id"]))]
    return pd.concat([new, kept], ignore_index=True)


def fetch_new():
    rows = []
    for listing, url in nlrb.LISTINGS.items():
        for p in range(PAGES):
            page = crawl.get(f"{url}?items_per_page={crawl.PER_PAGE}&page={p}")
            d, problems = nlrb.parse_page(page, listing, f"{listing}/{p}")
            if len(d) != crawl.PER_PAGE or problems:
                sys.exit(f"STOP: {listing} page {p} parsed to {len(d)} rows with problems {problems[:3]}; nothing uploaded")
            rows += d
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
    old = pd.read_parquet(f"https://huggingface.co/datasets/{REPO}/resolve/main/{FILE}")
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
    HfApi().upload_file(path_or_fileobj=f"out/{FILE}", path_in_repo=FILE, repo_id=REPO, repo_type="dataset",
                        commit_message=f"Refresh: +{added} new, {updated} updated")


if __name__ == "__main__":
    main()
