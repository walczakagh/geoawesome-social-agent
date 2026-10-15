"""Check which candidate feeds answer from a GitHub runner, and with which
User-Agent. Prints one line per (url, agent) pair. No API key needed.

    python tools/probe_sources.py tools/probe_urls.txt
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import feedparser
import requests

AGENTS = {
    "current": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0 Safari/537.36 GeoawesomeSocialAgent/1.0 (+https://geoawesome.com)"),
    "browser": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"),
    "feedreader": "Feedly/1.0 (+http://www.feedly.com/fetcher.html; like FeedFetcher-Google)",
}


def probe(url: str, ua: str) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": ua,
                                       "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"},
                         timeout=25)
    except Exception as exc:
        return f"ERR {type(exc).__name__}"
    f = feedparser.parse(r.content)
    n = len(f.entries)
    newest = ""
    dates = [e.get("published_parsed") or e.get("updated_parsed") for e in f.entries]
    dates = [d for d in dates if d]
    if dates:
        newest = datetime(*max(dates)[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
    week = sum(1 for d in dates
               if (datetime.now(timezone.utc) - datetime(*d[:6], tzinfo=timezone.utc)).days <= 7)
    ctype = r.headers.get("content-type", "?").split(";")[0]
    return f"{r.status_code} {ctype} entries={n} last7d={week} newest={newest} bytes={len(r.content)}"


def main() -> None:
    urls = [l.strip() for l in open(sys.argv[1]) if l.strip() and not l.startswith("#")]
    for url in urls:
        for name, ua in AGENTS.items():
            print(f"{name:10s} {probe(url, ua):70s} {url}", flush=True)
            time.sleep(0.5)


if __name__ == "__main__":
    main()
