"""Parse NLRB's weekly "Summary of NLRB Decisions" pages into one record per summarized decision.

A week page's body is a run of paragraphs. Bold paragraphs that name a kind of decision ("Summarized
Board Decisions", "Unpublished Board Decisions ...", "Decisions of Administrative Law Judges",
"Appellate Court Decisions") start a section. Within a section, an entry starts with a paragraph whose
text opens with the case name followed by a parenthesis holding case numbers, e.g.

    Ralphs Grocery Company ( 21-CA-073942; 375 NLRB No. 25 ) Compton, CA, August 10, 2026. The Board ...

and runs until the next entry, heading, or "***" separator. Appellate entries use their own header,
"Name, Board No. 16-CA-363860 (reported at 374 NLRB No. 36) (5th Cir. Nov. 3, 2026)".

Text is kept as NLRB wrote it apart from whitespace: tags removed, entities decoded, runs of whitespace
(including non-breaking spaces) turned into one space. Paragraphs of one entry are joined with a blank line.
Anything in a section that is neither a heading nor part of an entry comes back in `problems`.
"""
import html as htmllib
import re

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(r"(%s)\.?\s+(\d{1,2})\s*,?\s*(\d{4})" % MONTHS)
MONTH_NO = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}
DASH = "-‐‑‒–—"
CASE_TOKEN = r"\d(?: ?\d)?\s*[%s]\s*[A-Z]{1,2}\s*[%s]\s*\d{1,6}" % (DASH, DASH)
CASE_FULL = re.compile(r"(\d(?: ?\d)?)\s*[%s]\s*([A-Z]{1,2})\s*[%s]\s*(\d{1,6})" % (DASH, DASH))
CITE = re.compile(r"(\d{1,3})\s+NLRB\s+(No\.\s*)?(\d{1,5})")
JD = re.compile(r"JD(?:\s*\(\s*[A-Z]{2,4}\s*\))?[%s\s]*\d+\s*[%s]\s*\d{2,4}" % (DASH, DASH))
NOISE = re.compile(r"^(\*+|No .{0,60}(Issued|Decisions)\.?|None\.?)$", re.I)
END = re.compile(r"^To have the NLRB.s Weekly Summary|^To have the NLRB.s|^Subscribe", re.I)


def clean(fragment):
    t = re.sub(r"<br\s*/?>", " ", fragment)
    t = htmllib.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\s+", " ", t.replace(" ", " ").replace("​", "")).strip()


def body_html(page):
    """The week's body field (the first body field after the weekly-summary node marker)."""
    i = page.find("node--type-weekly-summary")
    if i < 0:
        return None
    m = re.search(r'<div class="[^"]*field--name-body[^"]*"[^>]*>', page[i:])
    if not m:
        return None
    start = i + m.end()
    end = page.find("</article>", start)
    return page[start:end]


def blocks(body):
    """Paragraph-level blocks as (text, html). Headings, list items and table cells count as blocks."""
    b = re.sub(r"(?i)<(/?)(p|div|h[1-6]|li|tr|td|th|table|ul|ol|blockquote)\b[^>]*>", "\n\x00", body)
    out = []
    for chunk in b.split("\n\x00"):
        t = clean(chunk)
        if t:
            out.append((t, chunk))
    return out


def section_of(t):
    """Section name if this block is a section heading, else None."""
    if len(t) > 140:
        return None
    s = t.lower()
    if "appellate" in s or ("court" in s and "decision" in s):
        return "appellate"
    if "administrative law judge" in s and ("decision" in s or s.startswith("administrative")):
        return "alj"
    if "unpublished" in s:
        return "unpublished"
    if "two-member board" in s or "two member board" in s:
        return "board"
    if ("summarized" in s or "published" in s or s.startswith("board decisions")) and "decision" in s:
        return "board"
    return None


def is_bold(chunk):
    """Block whose visible text is all inside <strong>/<b> (a heading)."""
    inner = re.sub(r"(?is)<(strong|b)\b[^>]*>.*?</\1>", "", chunk)
    return clean(inner) == "" and clean(chunk) != ""


OPEN = re.compile(r"\(\s*(?:%s|[A-Z]{1,2}\s*[%s]\s*\d)" % (CASE_TOKEN, DASH))


class _M:
    """Minimal match object for split_header, so callers can use .group(name)."""
    def __init__(self, d):
        self.d = d

    def group(self, k):
        return self.d[k]


def split_header(t):
    """Split 'Name (case numbers; citation) rest' at the first parenthesis that opens with a case number,
    honoring nested parentheses such as JD(SF)-54-10. None if there is no such parenthesis."""
    m = OPEN.search(t)
    if not m or m.start() == 0:
        # NLRB sometimes drops the opening parenthesis: "GE Appliances, a Haier Company 09-CA-332521) ..."
        m2 = re.match(r"^(.+?)\s+((?:%s)[^()]{0,80})\)\s*,?\s*(.*)$" % CASE_TOKEN, t, re.S)
        if m2 and len(m2.group(1)) < 200:
            return _M({"name": m2.group(1).rstrip(" ,"), "paren": m2.group(2), "rest": m2.group(3)})
        return None
    depth, i = 0, m.start()
    for j in range(i, len(t)):
        if t[j] == "(":
            depth += 1
        elif t[j] == ")":
            depth -= 1
            if depth == 0:
                return _M({"name": t[:i].rstrip(" ,"), "paren": t[i + 1:j], "rest": t[j + 1:].lstrip(" ,")})
    return None


class HEAD:
    match = staticmethod(split_header)


APPELLATE = re.compile(r"^(?P<name>.+?),?\s*(?:NLRB\s+)?Board\s+(?:Case\s+)?(?:Nos?\.?\s*)?(?P<paren>(?:%s).*?)\s*\((?P<court>[^()]*?\d{4}[^()]*?)\)\s*\.?\s*(?P<rest>.*)$" % CASE_TOKEN, re.S)


def case_numbers(paren):
    """Full case numbers in a header's parenthesis. '30-CA-16577, 16813' -> 30-CA-16577, 30-CA-16813."""
    paren = JD.sub(" ", CITE.sub(" ", paren))
    out, last = [], None
    for tok in re.finditer(r"%s|\b\d{3,6}\b" % CASE_TOKEN, paren):
        m = CASE_FULL.fullmatch(tok.group(0))
        if m:
            last = (m.group(1).replace(" ", ""), m.group(2))
            out.append(f"{int(last[0]):02d}-{m.group(2)}-{int(m.group(3))}")
        elif last:  # bare number after a full case number: same region and type
            out.append(f"{int(last[0]):02d}-{last[1]}-{int(tok.group(0))}")
    return list(dict.fromkeys(out))


def iso(m):
    return f"{int(m.group(3)):04d}-{MONTH_NO[m.group(1)]:02d}-{int(m.group(2)):02d}"


def parse_header(t, section):
    """Header fields of an entry's first paragraph, or None if it doesn't look like an entry header."""
    if section == "appellate":
        m = APPELLATE.match(t)
        if not m or len(m.group("name")) > 250:
            return None
        cite = CITE.search(m.group("paren"))
        d = DATE.search(m.group("court"))
        abbrev = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})", m.group("court"))
        date = iso(d) if d else None
        if not date and abbrev:
            mon = {"Sept": 9}.get(abbrev.group(1)) or ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                                      "Nov", "Dec"].index(abbrev.group(1)[:3]) + 1
            date = f"{int(abbrev.group(3)):04d}-{mon:02d}-{int(abbrev.group(2)):02d}"
        return {"case_name": m.group("name").strip(" ,"), "header_parenthetical": m.group("paren").strip(),
                "case_numbers": case_numbers(m.group("paren")), "citation": None,
                "reported_at": cite.group(0) if cite else None, "jd_number": None,
                "place": None, "court": m.group("court").strip(), "date": date,
                "date_source": "header" if date else None,
                "header_date_text": m.group("court").strip(), "rest": m.group("rest")}
    m = HEAD.match(t)
    if not m or len(m.group("name")) > 300:
        return None
    paren = m.group("paren")
    cn = case_numbers(paren)
    if not cn:
        return None
    cite = CITE.search(paren)
    jd = JD.search(paren)
    rest = m.group("rest")
    # place and date: "Compton, CA, August 10, 2026. The Board ..." (ALJ headers often have no date)
    d = DATE.search(rest[:200])
    place, date, date_text, after = None, None, None, rest
    abbrev = re.compile(r"\b(St|Ste|Ft|Mt|Pt|Mt|Ave|[A-Z])\.(?=\s)")  # "St. Louis", "D. C."
    if d and d.start() < 150 and not re.search(r"\.\s", abbrev.sub("", rest[:d.start()])):
        place = rest[:d.start()].strip(" ,.")
        date, date_text = iso(d), d.group(0)
        after = rest[d.end():].lstrip(" .")
    else:
        stop = re.search(r"\.\s", rest)
        if stop and stop.start() < 150:
            place = rest[:stop.start()].strip(" ,.")
            after = rest[stop.end():]
    date_source = "header" if date else None
    if not date:
        d2 = re.search(r"issued (?:his|her|the|their|a)?\s*(?:bench )?decisions?\s+(?:on\s+)?" + DATE.pattern, after)
        if d2:
            date, date_text, date_source = iso(DATE.search(d2.group(0))), DATE.search(d2.group(0)).group(0), "summary text"
    return {"case_name": m.group("name").strip(" ,"), "header_parenthetical": paren.strip(),
            "date_source": date_source, "case_numbers": cn, "citation": cite.group(0) if cite else None, "reported_at": None,
            "jd_number": jd.group(0) if jd else None, "place": place or None, "court": None, "date": date,
            "header_date_text": date_text, "rest": after}


def starts_with_name(chunk):
    """Entry headers open with the case name in bold or italics."""
    s = re.sub(r"^\s*(<(a|span|div|p|br)\b[^>]*>\s*)*", "", chunk, flags=re.I)
    return bool(re.match(r"(?i)<(strong|b|em|i)\b", s))


def parse_week(page, where=None):
    """(entries, problems) for one week page."""
    body = body_html(page)
    if body is None:
        return [], [{"where": where, "why": "no body found"}]
    entries, problems = [], []
    section, sub, cur = None, None, None

    def close():
        nonlocal cur
        if cur:
            cur["summary_text"] = "\n\n".join(p for p in cur.pop("_paras") if p)
            entries.append(cur)
        cur = None

    for t, chunk in blocks(body):
        if END.match(t):
            close()
            section = "end"
            continue
        sec = section_of(t) if is_bold(chunk) or len(t) < 90 else None
        if sec and (is_bold(chunk) or not HEAD.match(t)):
            close()
            section, sub = sec, None
            continue
        if section in (None, "end"):
            continue  # disclaimer before the first heading, subscription text after the end
        if NOISE.match(t):
            close()
            continue
        if is_bold(chunk) and len(t) < 60 and not HEAD.match(t):
            close()
            sub = t  # e.g. "R Cases", "C Cases"
            continue
        h = parse_header(t, section) if (starts_with_name(chunk) or section == "appellate") else None
        if h is None and section != "appellate":
            h2 = parse_header(t, section)  # header without bold name; accept only a clean match
            if h2 and h2["date"] and len(h2["case_name"]) < 150:
                h = h2
        if h:
            close()
            rest = h.pop("rest")
            cur = dict(section=section, subsection=sub, header=t if section != "appellate" else t[:len(t) - len(rest)].strip(),
                       _paras=[rest.strip()] if rest.strip() else [], **h)
            if section == "appellate":
                cur["header"] = t[:len(t) - len(rest)].strip()
            else:
                cur["header"] = t[:len(t) - len(rest)].strip() if rest else t
            continue
        if cur:
            cur["_paras"].append(t)
        else:
            problems.append({"where": where, "section": section, "why": "text outside any entry", "text": t[:300]})
    close()
    return entries, problems
