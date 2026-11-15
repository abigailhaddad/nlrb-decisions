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


# ---- weekly summaries ----
import summaries as S
import summaries_crawl as SC


def _fx(name):
    return open(os.path.join(os.path.dirname(__file__), "fixtures", name), encoding="utf-8").read()


def test_summary_week_2011():
    entries, problems = S.parse_week(_fx("summary_week_2011.html"), "2011")
    assert [e["section"] for e in entries] == ["board"] * 5 + ["unpublished"] * 4 + ["alj"] * 2
    src = entries[0]
    assert src["case_numbers"] == ["30-CA-16577", "30-CA-16813"]  # "30-CA-16577, 16813"
    assert src["citation"] == "356 NLRB No. 74" and src["place"] == "Milwaukee, WI" and src["date"] == "2011-01-05"
    assert "Chairman Liebman and Members Pearce and Hayes participated." in src["summary_text"]  # second paragraph kept
    assert src["summary_text"].count("\n\n") == 1
    assert entries[3]["citation"] == "356 NLRB No. 71"  # comma instead of semicolon before the citation
    assert entries[8]["date"] == "1011-01-07"  # NLRB's typo, kept as written
    horton = entries[9]
    assert horton["jd_number"] == "JD(ATL)-32-10" and horton["place"] == "Deerfield Beach, FL"
    assert horton["date"] == "2011-01-03" and horton["date_source"] == "summary text"
    # the explanatory note under "two-member Board" is reported, not dropped
    assert len(problems) == 1 and "two-member Board" in problems[0]["text"]


def test_summary_week_2026():
    entries, problems = S.parse_week(_fx("summary_week_2026.html"), "2026")
    assert problems == []
    assert [(e["section"], e["subsection"]) for e in entries] == [
        ("unpublished", "R Cases"), ("unpublished", "R Cases"), ("unpublished", "C Cases"), ("appellate", None)]
    assert entries[2]["header_parenthetical"] == "09-CA-359348, et al."
    app = entries[3]
    assert app["reported_at"] == "374 NLRB No. 36" and app["date"] == "2026-11-03" and app["citation"] is None
    assert app["summary_text"].startswith("In an unpublished opinion")


def test_summary_headers():
    h = S.parse_header("Nexstar Media Group, Inc. (14-RM-374657) St. Louis, MO, August 25, 2026. The Board denied", "unpublished")
    assert h["place"] == "St. Louis, MO" and h["date"] == "2026-08-25" and h["rest"] == "The Board denied"
    h = S.parse_header("GE Appliances, a Haier Company 09–CA–332521) Louisville, KY, July 1, 2026. The Board", "unpublished")
    assert h["case_numbers"] == ["09-CA-332521"] and h["case_name"] == "GE Appliances, a Haier Company"
    assert S.case_numbers("12-RM-330540 and 1 2-CA-334715; JD–27–26") == ["12-RM-330540", "12-CA-334715"]
    assert S.case_numbers("16-CA-290036, et al.; JD(SF) 01-26") == ["16-CA-290036"]
    a = S.parse_header("Oncor Electric Delivery Company, LLC, Board Case 16-CA-103387 (reported at 373 NLRB No. 80) "
                       "(D.C. Cir. April 28, 2026).", "appellate")
    assert a["date"] == "2026-04-28" and a["reported_at"] == "373 NLRB No. 80"


def test_summary_index():
    weeks = SC.week_links(_fx("summary_index_page0.html"))
    assert len(weeks) == 20
    assert weeks[0][2] == "summary-of-nlrb-decisions-for-week-of-november-2-6-2026" and weeks[0][0].startswith("2026-11-12")


def test_robots_rules():
    import re as _re
    SC._rules[:] = [(False, 9, _re.compile(r"/search/")), (False, 7, _re.compile(r"/core/")),
                    (True, 12, _re.compile(r"/core/.*\.css$"))]
    assert SC.allowed("https://www.nlrb.gov/cases-decisions/weekly-summaries-decisions/x")
    assert not SC.allowed("https://www.nlrb.gov/search/foo")
    assert SC.allowed("https://www.nlrb.gov/core/a.css") and not SC.allowed("https://www.nlrb.gov/core/a.js")
    assert not SC.allowed("https://apps.nlrb.gov/link/document.aspx/x")
    SC._rules.clear()


def test_summary_match():
    import pandas as pd
    import summaries_build as B
    dec = pd.DataFrame([
        {"record_id": "board|a", "listing": "board", "citation": "356 NLRB No. 74", "case_number": "30-CA-016577", "issuance_date": "2011-01-05"},
        {"record_id": "board|b", "listing": "board", "citation": "375 NLRB No. 38", "case_number": "18-CD-352973", "issuance_date": "2026-09-11"},
        {"record_id": "unpublished|c", "listing": "unpublished", "citation": None, "case_number": "28-RC-380779", "issuance_date": "2026-09-21"},
        {"record_id": "unpublished|d", "listing": "unpublished", "citation": None, "case_number": "28-RC-380779", "issuance_date": "2026-09-21"},
    ])
    e = lambda **k: dict({"section": "board", "case_numbers": [], "citation": None, "date": None}, **k)
    es = B.match([e(case_numbers=["30-CA-16577"], citation="356 NLRB No. 74", date="2011-01-05"),
                  e(case_numbers=["18-CD-352973"], citation="374 NLRB No. 38", date="2026-09-11"),   # summary's citation typo
                  e(section="unpublished", case_numbers=["28-RC-380779"], date="2026-09-21"),      # two rows qualify
                  e(section="unpublished", case_numbers=["28-RC-380779"], date="2026-09-22"),
                  e(section="appellate", case_numbers=["30-CA-16577"], date="2011-01-05")], dec)
    # the summary's 374 NLRB No. 38 is not compatible with the row's 375 NLRB No. 38: left unmatched
    assert [x["record_id"] for x in es] == ["board|a", None, None, None, None]
    assert "has citation 375 NLRB No. 38" in es[1]["match_note"]
    assert B.citations_compatible("358 NLRB No. 95", "358 NLRB 1087") and B.citations_compatible("357 NLRB No. 7", "358 NLRB 27")
    assert B.citations_compatible("372 NLRB 64", "372 NLRB No. 64") and not B.citations_compatible("368 NLRB No. 100", "368 NLRB No. 97")
    assert "ambiguous" in es[2]["match_note"] and "no row" in es[3]["match_note"]
