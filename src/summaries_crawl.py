"""Fetch NLRB's "Summary of NLRB Decisions" weekly pages from www.nlrb.gov and keep the raw HTML, gzipped,
in raw/summaries/ (untracked).

    python src/summaries_crawl.py index    # every page of the weekly-summaries index (20 weeks a page)
    python src/summaries_crawl.py weeks    # every week page the saved index pages link to
    python src/summaries_crawl.py all      # both

Resumable: one file per page, written to a temp file and renamed, so an interrupted run never leaves a
half-written page. A page already saved is skipped. Every URL is checked against nlrb.gov's robots.txt
before it is fetched (apps.nlrb.gov is never fetched). One request about every 1.1 seconds, generic
User-Agent, stops on 403/429.
"""
import gzip, os, re, sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(__file__))
import crawl

BASE = "https://www.nlrb.gov"
INDEX = BASE + "/cases-decisions/decisions/weekly-summaries-decisions"
WEEK_PREFIX = "/cases-decisions/weekly-summaries-decisions/"
RAW = "raw/summaries"
_rules = []


def load_robots():
    """Google-style robots.txt rules for User-agent: * as (allow, regex) pairs."""
    txt = crawl.get(BASE + "/robots.txt")
    rules, active = [], False
    for line in txt.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        if k.lower() == "user-agent":
            active = v == "*"
        elif active and k.lower() in ("allow", "disallow") and v:
            pat = re.escape(v).replace(r"\*", ".*")
            if pat.endswith(r"\$"):
                pat = pat[:-2] + "$"
            rules.append((k.lower() == "allow", len(v), re.compile(pat)))
    return rules


def allowed(url):
    u = urlparse(url)
    if u.netloc != "www.nlrb.gov":
        return False
    if not _rules:
        _rules.extend(load_robots())
    path = u.path + ("?" + u.query if u.query else "")
    hits = [(n, a) for a, n, rx in _rules if rx.match(path)]
    if not hits:
        return True
    n = max(h[0] for h in hits)
    return any(a for m, a in hits if m == n)  # longest match wins; allow wins a tie


def save(path, url):
    if os.path.exists(path):
        return gzip.open(path, "rt", encoding="utf-8").read()
    if not allowed(url):
        sys.exit(f"STOP: robots.txt disallows {url}")
    page = crawl.get(url)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, path)
    return page


def week_links(index_html):
    """(post date, title, slug) for each week on an index page, in page order."""
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", index_html, re.S):
        m = re.search(r'href="%s([^"]+)"[^>]*>(.*?)</a>' % re.escape(WEEK_PREFIX), row, re.S)
        if not m:
            continue
        d = re.search(r'<time datetime="([^"]+)"', row)
        out.append((d.group(1) if d else None, crawl.nlrb.text(m.group(2)), m.group(1)))
    return out


def index_pages():
    os.makedirs(f"{RAW}/index", exist_ok=True)
    first = save(f"{RAW}/index/0000.html.gz", INDEX + "?page=0")
    last = crawl.last_page(first)
    for p in range(1, last + 1):
        save(f"{RAW}/index/{p:04d}.html.gz", INDEX + f"?page={p}")
    return last + 1


def all_weeks():
    weeks = []
    for f in sorted(os.listdir(f"{RAW}/index")):
        if f.endswith(".html.gz"):
            weeks += week_links(gzip.open(f"{RAW}/index/{f}", "rt", encoding="utf-8").read())
    return weeks


def week_pages():
    os.makedirs(f"{RAW}/weeks", exist_ok=True)
    weeks = all_weeks()
    slugs = list(dict.fromkeys(w[2] for w in weeks))
    for i, s in enumerate(slugs):
        if "/" in s or s.startswith("."):
            sys.exit(f"STOP: odd slug {s!r}")
        save(f"{RAW}/weeks/{s}.html.gz", BASE + WEEK_PREFIX + s)
        if i % 25 == 0:
            print(f"week {i + 1}/{len(slugs)} {s}", flush=True)
    print(f"DONE weeks: {len(weeks)} index entries, {len(slugs)} distinct slugs", flush=True)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what in ("index", "all"):
        print("DONE index:", index_pages(), "pages", flush=True)
    if what in ("weeks", "all"):
        week_pages()
    if what not in ("index", "weeks", "all"):
        sys.exit(__doc__)
