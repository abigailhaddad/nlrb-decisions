"""Fetch NLRB decision listing pages and keep the raw HTML, gzipped (raw/ is untracked).

    python src/crawl.py all          # the three listings, newest first, every page
    python src/crawl.py volumes      # Board decisions again, one volume at a time (second pass)
    python src/crawl.py years        # unpublished and ALJ decisions again, one year at a time (second pass)

Resumable: a page already saved is skipped. One request per PAUSE seconds (robots.txt sets no crawl
delay), a generic User-Agent, and it stops on any non-200.
"""
import gzip, os, re, sys, time
import requests

sys.path.insert(0, os.path.dirname(__file__))
import nlrb

UA = "Mozilla/5.0 (compatible; research-data-collector/1.0)"
PAUSE = 1.1
PER_PAGE = 100
_last = [0.0]


def get(url, rejected_ok=False):
    wait = PAUSE - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        _last[0] = time.time()
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=(20, 60))
        except requests.RequestException as e:
            print("retry after", e, flush=True)
            time.sleep(30)
            continue
        if r.status_code == 200 and "<title>Request Rejected</title>" not in r.text:
            return r.text
        if r.status_code == 200 and rejected_ok:
            return None
        if r.status_code == 200:  # the site's firewall page, served with a 200
            print("retry after a 'Request Rejected' page", url, flush=True)
            time.sleep(30)
            continue
        if r.status_code in (403, 429):
            sys.exit(f"STOP: {r.status_code} on {url}")
        print("retry after", r.status_code, url, flush=True)
        time.sleep(30)
    sys.exit(f"STOP: gave up on {url}")


def last_page(page_html):
    """Highest page index the pager links to (0-based). 0 if there is no pager."""
    nums = [int(n) for n in re.findall(r'[?&](?:amp;)?page=(\d+)', page_html)]
    return max(nums) if nums else 0


def crawl(listing, outdir, extra=""):
    os.makedirs(outdir, exist_ok=True)
    url = nlrb.LISTINGS[listing] + f"?{extra}items_per_page={PER_PAGE}&page={{}}"
    p, last = 0, None
    while last is None or p <= last:
        path = os.path.join(outdir, f"{p:04d}.html.gz")
        if os.path.exists(path):
            page = gzip.open(path, "rt", encoding="utf-8").read()
        else:
            page = get(url.format(p))
            with gzip.open(path, "wt", encoding="utf-8") as f:
                f.write(page)
        if last is None:
            last = last_page(page)
        p += 1
    return last + 1


def main(what):
    if what == "all":
        for listing in nlrb.LISTINGS:
            n = crawl(listing, f"raw/{listing}")
            print("DONE", listing, n, "pages", flush=True)
    elif what == "volumes":
        first = gzip.open("raw/board/0000.html.gz", "rt", encoding="utf-8").read()
        for v in nlrb.options(first, "volume"):
            if v == "-1":
                continue
            n = crawl("board", f"raw/board_by_volume/{v}", f"volume={v}&")
            print("volume", v, n, "pages", flush=True)
    elif what == "years":
        for listing in ("unpublished", "alj"):
            first = gzip.open(f"raw/{listing}/0000.html.gz", "rt", encoding="utf-8").read()
            for y in nlrb.options(first, "year"):
                if y == "all":
                    continue
                n = crawl(listing, f"raw/{listing}_by_year/{y}", f"year={y}&")
                print(listing, "year", y, n, "pages", flush=True)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
