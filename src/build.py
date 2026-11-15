"""Turn the saved listing pages into out/decisions.parquet and out/report.json.

    python src/build.py

Two passes of each listing are read: the full listing newest-first (raw/<listing>/), and the same listing
filtered one volume (Board decisions) or one year (unpublished and ALJ decisions) at a time
(raw/<listing>_by_volume|year/). Rows are keyed by listing + decision document URL and the passes are
unioned, so a row the paging skipped in one pass is still caught by the other. The report says how many
rows each pass found, how many were only in one pass, and what NLRB's pager implies the total is.
"""
import glob, gzip, json, os, re, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import nlrb

LISTING_TITLE = {"board": "Board Decisions", "unpublished": "Unpublished Board Decisions",
                 "alj": "Administrative Law Judge Decisions"}
SECOND = {"board": "board_by_volume", "unpublished": "unpublished_by_year", "alj": "alj_by_year"}
PER_PAGE = 100


def read_dir(d, listing):
    """All rows from the pages in d, plus the count NLRB's pager implies for that listing or filter."""
    paths = sorted(glob.glob(os.path.join(d, "*.html.gz")))
    rows, problems = [], []
    for i, path in enumerate(paths):
        page = gzip.open(path, "rt", encoding="utf-8").read()
        r, p = nlrb.parse_page(page, listing, os.path.relpath(path, "raw"))
        for x in r:
            x["page_file"] = os.path.relpath(path, "raw")
        rows += r
        problems += p
        if i == 0:
            first = page
    implied = None
    if paths:
        # pages are 0..last; every page but the last holds PER_PAGE rows
        last = int(os.path.basename(paths[-1])[:4])
        last_rows = len(nlrb.parse_page(gzip.open(paths[-1], "rt", encoding="utf-8").read(), listing)[0])
        implied = last * PER_PAGE + last_rows
        if len(paths) != last + 1:
            problems.append({"where": d, "why": f"{len(paths)} page files but last page index is {last}"})
    return rows, problems, implied


def key(r):
    return r["listing"] + "|" + (r["document_url"] or ("nolink:" + r["case_number"] + "|" + (r["citation"] or "") + "|" + r["issuance_date_raw"]))


def main():
    os.makedirs("out", exist_ok=True)
    report, frames = {}, []
    for listing in nlrb.LISTINGS:
        main_rows, problems, implied = read_dir(f"raw/{listing}", listing)
        sub_rows, sub_counts = [], {}
        for d in sorted(glob.glob(f"raw/{SECOND[listing]}/*")):
            r, p, n = read_dir(d, listing)
            sub_rows += r
            problems += p
            sub_counts[os.path.basename(d)] = {"rows": len(r), "implied": n}
        a = {key(r): r for r in main_rows}
        b = {}
        for r in sub_rows:
            b.setdefault(key(r), r)
        only_main = sorted(set(a) - set(b))
        only_second = sorted(set(b) - set(a))
        merged = dict(b)
        merged.update(a)  # the full-listing copy wins; same row either way
        df = pd.DataFrame(list(merged.values()))
        df["listing_title"] = LISTING_TITLE[listing]
        frames.append(df)
        report[listing] = {
            "listing_url": nlrb.LISTINGS[listing],
            "full_pass_pages": len(glob.glob(f"raw/{listing}/*.html.gz")),
            "full_pass_rows": len(main_rows),
            "full_pass_unique": len(a),
            "full_pass_repeats": len(main_rows) - len(a),
            "nlrb_pager_implied_total": implied,
            "second_pass_filters": len(sub_counts),
            "second_pass_rows": len(sub_rows),
            "second_pass_unique": len(b),
            "second_pass_implied_total": sum(v["implied"] or 0 for v in sub_counts.values()),
            "second_pass_filters_where_rows_differ_from_pager": {k: v for k, v in sub_counts.items() if v["rows"] != v["implied"]},
            "only_in_full_pass": len(only_main), "only_in_full_pass_sample": only_main[:20],
            "only_in_second_pass": len(only_second), "only_in_second_pass_sample": only_second[:20],
            "decisions": len(df),
            "problems": problems[:50], "n_problems": len(problems),
        }
    df = finalize(pd.concat(frames, ignore_index=True))
    df.to_parquet("out/decisions.parquet", index=False)

    overall = {"rows": len(df), "by_listing": df["listing"].value_counts().to_dict(),
               "date_min": df["issuance_date"].min(), "date_max": df["issuance_date"].max(),
               "no_document_url": int(df["document_url"].isna().sum()),
               "no_case_url": int(df["case_url"].isna().sum()),
               "no_date": int(df["issuance_date"].isna().sum()),
               "board_blank_citation": int(((df["listing"] == "board") & (df["citation"].fillna("") == "")).sum()),
               "board_placeholder_citation": int((df["citation_form"] == "placeholder").sum()),
               "board_citation_unparsed": int(((df["listing"] == "board") & df["citation_form"].isna() & (df["citation"].fillna("") != "")).sum()),
               "board_citation_shared_by_2plus_documents": int(df[df["volume"].notna()].duplicated("citation", keep=False).sum()),
               "case_name_exactly_100_chars": int((df["case_name"].str.len() == 100).sum()),
               "case_name_over_100_chars": int((df["case_name"].str.len() > 100).sum()),
               "case_number_not_usual_form": int((~df["case_number_usual_form"]).sum()),
               "case_number_blank": int((df["case_number"].fillna("") == "").sum()),
               "document_url_in_more_than_one_listing": int(df.dropna(subset=["document_id"]).groupby("document_id")["listing"].nunique().gt(1).sum()),
               "document_url_repeated_within_listing": int(df.dropna(subset=["document_id"]).duplicated(["listing", "document_id"]).sum())}
    overall["date_shown_as_01/01/0001"] = int((df["issuance_date_raw"] == "01/01/0001").sum())
    overall["date_before_nlra_1935-07-05"] = int((df["issuance_date"] < "1935-07-05").sum())
    far = far_from_volume(df)
    overall["board_date_over_2_years_from_volume_median"] = len(far)
    far.to_csv("out/date_outliers.csv", index=False)
    report["overall"] = overall
    json.dump(report, open("out/report.json", "w"), indent=1, default=str)
    for k, v in report.items():
        print(k, json.dumps({a: b for a, b in v.items() if not a.endswith("sample") and a != "problems"}, default=str))


def far_from_volume(df, days=730):
    """Board decisions whose date is more than `days` from the median date of their volume."""
    b = df[(df["listing"] == "board") & df["volume"].notna() & df["issuance_date"].notna()].copy()
    d = pd.to_datetime(b["issuance_date"], errors="coerce")
    med = d.groupby(b["volume"]).transform("median")
    out = b[(d - med).abs() > pd.Timedelta(days=days)].copy()
    out["volume_median_date"] = med[out.index].dt.date.astype(str)
    return out[["record_id", "citation", "case_name", "case_number", "issuance_date", "volume_median_date", "document_url"]]


COLS = ["record_id", "listing", "listing_title", "citation", "volume", "citation_form", "citation_number",
        "case_name", "case_number", "case_number_usual_form", "issuance_date", "issuance_date_raw", "year", "jd_number",
        "judge", "board_outcome", "document_url", "document_id", "case_url"]


def finalize(df):
    """Parsed rows (one listing or several) -> the published table."""
    df = df.copy()
    if "listing_title" not in df:
        df["listing_title"] = df["listing"].map(LISTING_TITLE)
    df["document_id"] = df["document_url"].str.extract(r"document\.aspx/([0-9a-fA-F]+)")[0]
    vol = df["citation"].apply(nlrb.parse_citation)
    df["volume"] = vol.str[0].astype("Int64")
    df["citation_form"] = vol.str[1].map({"slip": "slip opinion number", "page": "bound volume page",
                                          "placeholder": "placeholder"})
    df["citation_number"] = vol.str[2].astype("Int64")
    # NLRB shows 01/01/0001 for some ALJ decisions with no date; that is a blank, not a date
    df.loc[df["issuance_date"] == "0001-01-01", "issuance_date"] = None
    df["year"] = df["issuance_date"].str[:4]
    df["record_id"] = df.apply(lambda r: key(r).replace("https://apps.nlrb.gov/link/document.aspx/", ""), axis=1)
    df["case_number_usual_form"] = df["case_number"].fillna("").str.match(nlrb.CASE_NO.pattern)
    df = df.sort_values(["issuance_date", "listing", "citation", "case_number"], ascending=[False, True, True, True],
                        na_position="last").reset_index(drop=True)
    assert df["record_id"].is_unique
    extra = [c for c in ("extra_document_urls",) if c in df]
    for c in extra:
        df[c] = df[c].apply(lambda x: "; ".join(x) if isinstance(x, list) else None)
    return df[COLS + extra]


if __name__ == "__main__":
    main()
