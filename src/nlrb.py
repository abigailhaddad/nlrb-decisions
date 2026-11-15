"""Parse NLRB's three decision listings on nlrb.gov.

  board        https://www.nlrb.gov/cases-decisions/decisions/board-decisions
               Issuance Date | Citation | Case Name (linked to the decision document) | Case Number
  unpublished  https://www.nlrb.gov/cases-decisions/decisions/unpublished-board-decisions
               Case Number | Case Name (linked) | Issuance Date
  alj          https://www.nlrb.gov/cases-decisions/decisions/administrative-law-judge-decisions
               Case Number | JD Number | Case Name (linked) | Judge | Issuance Date | Board Outcome

Columns are found by the header's link class, not by position. A row that doesn't have the expected
cells comes back in `problems` instead of being dropped.
"""
import html as htmllib
import re
from datetime import datetime
from urllib.parse import urljoin

BASE = "https://www.nlrb.gov"
LISTINGS = {
    "board": BASE + "/cases-decisions/decisions/board-decisions",
    "unpublished": BASE + "/cases-decisions/decisions/unpublished-board-decisions",
    "alj": BASE + "/cases-decisions/decisions/administrative-law-judge-decisions",
}
# header link class -> our column name
HEADERS = {"date-issued": "issuance_date", "citation": "citation", "case-name": "case_name",
           "case-number": "case_number", "jd-number": "jd_number", "judge": "judge",
           "board-outcome": "board_outcome"}
NEEDED = {"board": {"issuance_date", "citation", "case_name", "case_number"},
          "unpublished": {"issuance_date", "case_name", "case_number"},
          "alj": {"issuance_date", "case_name", "case_number", "jd_number", "judge", "board_outcome"}}
CASE_NO = re.compile(r"^\d{2}-[A-Z]{1,2}-\d{3,6}$")  # the usual form, e.g. 13-CA-393585


def text(fragment):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def iso_date(s):
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def options(page_html, name):
    m = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % name, page_html, re.S)
    return re.findall(r'<option value="([^"]*)"', m.group(1)) if m else []


def table(page_html):
    """Return (column names, list of rows as lists of raw cell HTML) for the decisions table."""
    m = re.search(r'<table class="views-table case-decisions-table.*?</table>', page_html, re.S)
    if not m:
        return None, []
    t = m.group(0)
    head = re.search(r"<thead>(.*?)</thead>", t, re.S).group(1)
    cols = []
    for th in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S):
        c = re.search(r'class="use-ajax ([a-z-]+)"', th)
        cols.append(HEADERS.get(c.group(1), c.group(1)) if c else text(th))
    body = re.search(r"<tbody>(.*?)</tbody>", t, re.S)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S) if body else []
    return cols, [re.findall(r"<td[^>]*>(.*?)</td>", r, re.S) for r in rows]


def parse_page(page_html, listing, where=None):
    """Rows of one listing page. `where` (e.g. 'board/0003') is stored on problems only."""
    cols, rows = table(page_html)
    decisions, problems = [], []
    if cols is None:
        if "views-table" in page_html or "case-decisions-table" in page_html:
            problems.append({"where": where, "why": "table found but not parsed"})
        return decisions, problems
    missing = NEEDED[listing] - set(cols)
    if missing:
        problems.append({"where": where, "why": f"missing columns {sorted(missing)}", "cols": cols})
        return decisions, problems
    for i, cells in enumerate(rows):
        if len(cells) == 1 and text(cells[0]).startswith("There are currently no"):
            continue  # the listing's own "nothing for this filter" message
        if len(cells) != len(cols):
            problems.append({"where": where, "row": i, "why": f"{len(cells)} cells, expected {len(cols)}",
                             "text": text(" | ".join(cells))[:300]})
            continue
        c = dict(zip(cols, cells))
        name_links = re.findall(r'href="([^"]+)"', c["case_name"])
        case_links = re.findall(r'href="([^"]+)"', c["case_number"])
        case_text = text(c["case_number"])
        rec = {
            "listing": listing,
            "issuance_date": iso_date(text(c["issuance_date"])),
            "issuance_date_raw": text(c["issuance_date"]),
            "citation": text(c["citation"]) if "citation" in c else None,
            "case_name": text(c["case_name"]),
            "case_number": case_text,
            "case_url": urljoin(BASE, htmllib.unescape(case_links[0])) if case_links else None,
            "document_url": htmllib.unescape(name_links[0]) if name_links else None,
            "jd_number": text(c["jd_number"]) if "jd_number" in c else None,
            "judge": text(c["judge"]) if "judge" in c else None,
            "board_outcome": text(c["board_outcome"]) if "board_outcome" in c else None,
        }
        if len(name_links) > 1:
            rec["extra_document_urls"] = [htmllib.unescape(u) for u in name_links[1:]]
        if not rec["issuance_date"] and rec["issuance_date_raw"]:
            problems.append({"where": where, "row": i, "why": "unreadable date", "text": rec["issuance_date_raw"]})
        decisions.append(rec)
    return decisions, problems


CITE = re.compile(r"^(\d+) NLRB (No\. )?(\d+)")


def parse_citation(c):
    """'375 NLRB No. 68' -> (375, 'slip', 68); '300 NLRB 1065' -> (300, 'page', 1065); else (None, None, None)."""
    m = CITE.match(c or "")
    if not m:
        return None, None, None
    if int(m.group(1)) == 0:  # NLRB shows "00 NLRB No. 00" for some decisions with no number
        return None, "placeholder", None
    return int(m.group(1)), ("slip" if m.group(2) else "page"), int(m.group(3))
