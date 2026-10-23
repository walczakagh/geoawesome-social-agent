"""List every entry of each Google Alerts feed (numbered as in the secret).
Prints alert number, date, domain, title and the real article link.
Feed URLs are never printed."""
from __future__ import annotations

import os
import re

import feedparser

from agent.scout import _fetch, domain, strip_html, unwrap_link

urls = [u.strip() for u in re.split(r"[\s,]+", os.environ.get("GOOGLE_ALERTS_FEEDS", "")) if u.strip()]
for n, url in enumerate(urls, 1):
    try:
        f = feedparser.parse(_fetch(url))
        entries = f.entries
        title = strip_html(f.feed.get("title", ""))
    except Exception as exc:
        entries, title = [], f"(empty or error: {str(exc)[:60].replace(url, '<url>')})"
    print(f"=== ALERT {n} | {title} | {len(entries)} entries")
    for e in entries:
        link = unwrap_link(e.get("link", ""))
        date = (e.get("published") or e.get("updated") or "")[:10]
        print(f"{date} | {domain(link)} | {strip_html(e.get('title', ''))} | {link}")
