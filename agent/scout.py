"""Poll the configured feeds and return new candidate items."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
import yaml

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0 Safari/537.36 GeoawesomeSocialAgent/1.0 (+https://geoawesome.com)")
HEADERS = {"User-Agent": USER_AGENT,
           "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/html;q=0.8, */*;q=0.5",
           "Accept-Language": "en-GB,en;q=0.9"}


@dataclass
class Item:
    id: str
    source_id: str
    source_name: str
    source_type: str
    licence: str
    title: str
    link: str
    published: str
    summary: str
    image_url: str = ""
    credit_hint: str = ""
    source_tag: str = ""
    # filled in by enrich.py
    page_text: str = ""
    image_credit: str = ""
    image_licence: str = ""
    extra_images: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def load_sources(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [s for s in data.get("sources", []) if s.get("enabled", True)]


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    text = _TAG_RE.sub(" ", text or "")
    text = html.unescape(text)
    text = re.sub(r"The post .*? appeared first on .*?\.", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _first_image(entry) -> str:
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key, []) or []:
            if m.get("url"):
                return m["url"]
    for enc in entry.get("enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image"):
            return enc.get("href", "")
    blob = entry.get("summary", "") or ""
    for c in entry.get("content", []) or []:
        blob += c.get("value", "")
    m = re.search(r'<img[^>]+src="([^"]+)"', blob)
    return html.unescape(m.group(1)) if m else ""


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def parse_feed(source: dict, raw: bytes | str | None = None) -> list[Item]:
    """Parse one feed. `raw` lets tests pass fixture content."""
    if raw is None:
        r = requests.get(source["url"], headers=HEADERS, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} from {source['url']}")
        raw = r.content
    feed = feedparser.parse(raw)
    if not feed.entries:
        why = getattr(feed, "bozo_exception", None)
        print(f"[scout] {source['id']}: feed parsed but has no entries"
              + (f" ({type(why).__name__}: {why})" if why else "")
              + (f"; starts with: {raw[:120]!r}" if isinstance(raw, bytes) else ""))
    items = []
    for e in feed.entries:
        link = e.get("link", "")
        if not link:
            continue
        pub = _published(e)
        items.append(Item(
            id=e.get("id") or link,
            source_id=source["id"],
            source_name=source["name"],
            source_type=source.get("type", ""),
            licence=source.get("licence", "link_only"),
            title=strip_html(e.get("title", "")),
            link=link,
            published=pub.isoformat() if pub else "",
            summary=strip_html(e.get("summary", ""))[:1200],
            image_url=_first_image(e),
            credit_hint=source.get("credit_hint", ""),
            source_tag=source.get("tag", ""),
        ))
    return items


def scout(sources: list[dict], seen: set[str], max_age_days: int = 7,
          now: datetime | None = None) -> list[Item]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    fresh: list[Item] = []
    for src in sources:
        try:
            items = parse_feed(src)
        except Exception as exc:  # one bad feed must not kill the run
            print(f"[scout] {src['id']}: failed ({exc})")
            continue
        kept = 0
        for it in items:
            if it.link in seen or it.id in seen:
                continue
            if it.published and datetime.fromisoformat(it.published) < cutoff:
                continue
            fresh.append(it)
            kept += 1
        print(f"[scout] {src['id']}: {len(items)} in feed, {kept} new")
    return fresh
