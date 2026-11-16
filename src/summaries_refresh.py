"""Scheduled refresh of summaries.parquet: read the first PAGES pages of NLRB's weekly-summaries index, fetch
any week not already in the Hugging Face table, parse it, match every entry again against the current
decisions table, upload.

    python src/summaries_refresh.py            # uploads if HF_TOKEN is set and a new week was added
    python src/summaries_refresh.py --dry-run  # fetch, parse, match, write out/summaries.parquet, upload nothing

Uses out/decisions.parquet if refresh.py just wrote it, otherwise the copy on Hugging Face. Weeks already in
the table are not fetched again, so a later edit NLRB makes to an old week page is not picked up. The run
stops without uploading if the table would shrink, an existing match would change, or a new week parses to
nothing (a week with no decisions says so in words, and those words are recognized).
"""
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import crawl, summaries as S, summaries_build as B, summaries_crawl as C

REPO = "abigailhaddad/nlrb-decisions"
FILE = "summaries.parquet"
PAGES = 2  # 20 weeks a page


def main():
    dry = "--dry-run" in sys.argv
    old = pd.read_parquet(f"https://huggingface.co/datasets/{REPO}/resolve/main/{FILE}")
    dec_path = "out/decisions.parquet" if os.path.exists("out/decisions.parquet") else \
        f"https://huggingface.co/datasets/{REPO}/resolve/main/decisions.parquet"
    dec = pd.read_parquet(dec_path)
    have = set(old["week_url"])
    weeks = []
    for p in range(PAGES):
        url = C.INDEX + f"?page={p}"
        if not C.allowed(url):
            sys.exit(f"STOP: robots.txt disallows {url}")
        weeks += C.week_links(crawl.get(url))
    if len(weeks) != 20 * PAGES:
        sys.exit(f"STOP: the index pages listed {len(weeks)} weeks, expected {20 * PAGES}; nothing uploaded")
    new_entries, problems = [], []
    for posted, title, slug in weeks:
        url = C.BASE + C.WEEK_PREFIX + slug
        if url in have:
            continue
        if not C.allowed(url):
            sys.exit(f"STOP: robots.txt disallows {url}")
        page = crawl.get(url)
        entries, probs = S.parse_week(page, url)
        problems += probs
        body = S.body_html(page) or ""
        if not entries and "Issued" not in body:
            sys.exit(f"STOP: {url} parsed to no entries; nothing uploaded")
        for i, e in enumerate(entries):
            e.update(week_url=url, week_title=title, week_posted=(posted or "")[:10] or None, entry_index=i,
                     summary_id=f"{slug}#{i}")
        new_entries += entries
        print("new week", url, len(entries), "entries", flush=True)
    for p in problems:
        print("PARSE PROBLEM (kept, not an entry):", p, flush=True)
    # match old and new entries against the current decisions table
    olde = old.astype(object).where(old.notna(), None).to_dict("records")
    for e in olde:
        e["case_numbers"] = e["case_numbers"].split("; ") if e["case_numbers"] else []
    before = {e["summary_id"]: e["record_id"] for e in olde}
    allm = B.mark_duplicates(B.match(olde + new_entries, dec))
    changed = [e["summary_id"] for e in allm if e["summary_id"] in before and before[e["summary_id"]] is not None
               and e["record_id"] != before[e["summary_id"]]]
    if changed:
        sys.exit(f"STOP: {len(changed)} existing matches would change, e.g. {changed[:3]}; nothing uploaded")
    gained = sum(1 for e in allm if e["summary_id"] in before and before[e["summary_id"]] is None and e["record_id"])
    df = pd.DataFrame(allm)
    df["case_numbers"] = df["case_numbers"].map(lambda x: "; ".join(x))
    df = df[B.COLS]
    if len(df) < len(old) or not df["summary_id"].is_unique:
        sys.exit("STOP: the table would shrink or summary_id is not unique; nothing uploaded")
    df = df.sort_values(["week_posted", "week_url", "entry_index"], ascending=[False, True, True]).reset_index(drop=True)
    os.makedirs("out", exist_ok=True)
    df.to_parquet(f"out/{FILE}", index=False)
    print({"rows": len(df), "new_entries": len(new_entries), "old_entries_newly_matched": gained,
           "matched": int(df["record_id"].notna().sum())})
    if dry:
        print("dry run: wrote out/summaries.parquet, uploaded nothing")
        return
    if not new_entries and not gained:
        print("nothing new; nothing uploaded")
        return
    if not os.environ.get("HF_TOKEN"):
        print("SKIP UPLOAD: the HF_TOKEN secret is empty. Nothing was uploaded.")
        return
    from huggingface_hub import HfApi
    HfApi().upload_file(path_or_fileobj=f"out/{FILE}", path_in_repo=FILE, repo_id=REPO, repo_type="dataset",
                        commit_message=f"Refresh summaries: +{len(new_entries)} entries, {gained} newly matched")


if __name__ == "__main__":
    main()
