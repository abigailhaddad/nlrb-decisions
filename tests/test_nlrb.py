import os, sys
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import build, crawl, nlrb, refresh

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(n):
    return open(os.path.join(FX, n), encoding="utf-8").read()


def test_board_page():
    d, p = nlrb.parse_page(load("board_page0.html"), "board")
    assert len(d) == 100 and p == []
    r = d[0]
    assert r["citation"] == "375 NLRB No. 68" and r["issuance_date"] == "2026-11-12"
    assert r["case_name"] == "Blick Art Materials, LLC" and r["case_number"] == "13-CA-393585"
    assert r["document_url"] == "https://apps.nlrb.gov/link/document.aspx/09031d45843ede05"
    assert r["case_url"] == "https://www.nlrb.gov/case/13-CA-393585"


def test_old_volume_page_uses_page_citations():
    d, p = nlrb.parse_page(load("board_vol300_page0.html"), "board")
    assert len(d) == 100 and p == []
    assert d[0]["citation"] == "300 NLRB 1065" and d[0]["issuance_date"] == "1990-12-31"
    assert nlrb.parse_citation(d[0]["citation"]) == (300, "page", 1065)


def test_unpublished_and_alj_pages():
    d, p = nlrb.parse_page(load("unpublished_page0.html"), "unpublished")
    assert len(d) == 20 and p == [] and d[0]["citation"] is None
    assert d[0]["case_number"] == "19-CB-311844" and d[0]["issuance_date"] == "2026-11-12"
    d, p = nlrb.parse_page(load("alj_page0.html"), "alj")
    assert len(d) == 20 and p == []
    assert (d[0]["jd_number"], d[0]["judge"], d[0]["board_outcome"]) == ("JD-75-26", "GEOFFREY CARTER", "Pending Board Decision")


def test_wrong_listing_columns_are_reported_not_dropped():
    d, p = nlrb.parse_page(load("unpublished_page0.html"), "alj")
    assert d == [] and p and "missing columns" in p[0]["why"]


def test_short_row_is_reported():
    page = load("board_page0.html").replace("<td>375 NLRB No. 68</td>", "", 1)
    d, p = nlrb.parse_page(page, "board")
    assert len(d) == 99 and len(p) == 1 and "cells" in p[0]["why"]


def test_citations_and_dates():
    assert nlrb.parse_citation("375 NLRB No. 68") == (375, "slip", 68)
    assert nlrb.parse_citation("00 NLRB No. 00") == (None, "placeholder", None)
    assert nlrb.parse_citation("") == (None, None, None)
    assert nlrb.iso_date(" 02/29/2024 ") == "2024-02-29"
    assert nlrb.iso_date("13/01/2024") is None


def test_pager_and_options():
    assert crawl.last_page(load("board_page0.html")) == 666
    assert crawl.last_page(load("board_vol300_page0.html")) == 1
    assert nlrb.options(load("board_page0.html"), "volume")[:2] == ["-1", "375"]
    assert nlrb.options(load("alj_page0.html"), "year")[:2] == ["all", "2026"]


def test_finalize_and_merge():
    d, _ = nlrb.parse_page(load("board_page0.html"), "board")
    df = build.finalize(pd.DataFrame(d))
    assert df["record_id"].is_unique and df.loc[0, "record_id"] == "board|09031d45843ede05"
    assert df.loc[0, "volume"] == 375 and df.loc[0, "citation_form"] == "slip opinion number"
    old = pd.DataFrame({"record_id": ["a", "b"], "v": [1, 1]})
    new = pd.DataFrame({"record_id": ["b", "c"], "v": [2, 2]})
    m = refresh.merge(old, new).set_index("record_id")
    assert sorted(m.index) == ["a", "b", "c"] and m.loc["b", "v"] == 2


def test_changed_rows_ignores_blank_types():
    old = pd.DataFrame({"record_id": ["a", "b"], "listing": ["alj", "alj"], "citation": [None, None]})
    new = pd.DataFrame({"record_id": ["a", "c"], "listing": ["alj", "alj"], "citation": pd.Series([float("nan"), None], dtype=object)})
    assert refresh.changed_rows(old, new) == (1, 0)
    new.loc[0, "citation"] = "x"
    assert refresh.changed_rows(old, new) == (1, 1)


def test_refresh_skips_upload_without_token(monkeypatch, tmp_path, capsys):
    d, _ = nlrb.parse_page(load("board_page0.html"), "board")
    old = build.finalize(pd.DataFrame(d))
    new = old.head(3).copy()
    new.loc[0, "case_name"] = "changed"
    monkeypatch.setattr(refresh.pd, "read_parquet", lambda url: old)
    monkeypatch.setattr(refresh, "fetch_new", lambda: new)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["refresh.py"])
    monkeypatch.chdir(tmp_path)
    refresh.main()
    out = capsys.readouterr().out
    assert "'updated': 1" in out and "SKIP UPLOAD: the HF_TOKEN secret is empty" in out
