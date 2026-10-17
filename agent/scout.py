"""Poll the configured feeds and return new candidate items."""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

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
    # [{"url": ..., "label": ...}]; two entries means a before/after pair
    images: list = field(default_factory=list)
    origin_url: str = ""  # where a featured map itself lives (Maps Mania posts)
    # filled in by images.py
    square_path: str = ""
    image_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_sources(path: str | Path, env: dict | None = None) -> list[dict]:
    """Enabled sources. A source with `urls_env` is expanded into one source per
    URL listed (one per line) in that environment variable, so private feed
    URLs such as Google Alerts can live in a GitHub secret instead of the repo."""
    env = os.environ if env is None else env
    out = []
    for s in load_config(path).get("sources", []):
        if not s.get("enabled", True):
            continue
        var = s.get("urls_env")
        if not var:
            out.append(s)
            continue
        urls = [u.strip() for u in re.split(r"[\s,]+", env.get(var, "")) if u.strip()]
        if not urls:
            out.append({**s, "url": "", "not_configured": f"secret {var} is empty"})
        for n, u in enumerate(urls, 1):
            out.append({**s, "id": f"{s['id']}_{n}", "name": f"{s['name']} #{n}", "url": u})
    return out


def domain(url: str) -> str:
    d = urlparse(url).netloc.lower()
    return d[4:] if d.startswith("www.") else d


_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ocid$|cmpid$)")


def unwrap_link(url: str) -> str:
    """Return the real article URL behind Google Alerts and Bing News redirects,
    without tracking parameters or #fragments."""
    p = urlparse(url)
    q = parse_qs(p.query)
    host = p.netloc.lower()
    if ("google." in host and p.path == "/url") or ("bing.com" in host and "apiclick" in p.path):
        target = (q.get("url") or q.get("q") or [""])[0]
        if target.startswith("http"):
            return unwrap_link(target)
    keep = [(k, v) for k, vs in q.items() for v in vs if not _TRACKING.match(k)]
    return urlunparse(p._replace(query=urlencode(keep), fragment=""))


def link_key(url: str) -> str:
    """Normalised form used to spot the same article arriving via two feeds."""
    p = urlparse(unwrap_link(url))
    return (domain(url) + p.path.rstrip("/") + ("?" + p.query if p.query else "")).lower()


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


def _full_size(url: str) -> str:
    return re.sub(r"/s72-c/", "/s1600/", url)


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


class FeedEmpty(RuntimeError):
    pass


def sanitise(raw: bytes) -> bytes:
    """Repair the XML mistakes seen in real feeds before parsing. NASA's feed
    has been sent with two namespace declarations run together
    (`..apod/"xmlns:media=..`), which makes feedparser return no entries."""
    raw = re.sub(rb'"(xmlns:)', rb'" \1', raw)
    return re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)


def _fetch(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    feed = feedparser.parse(sanitise(r.content))
    if not feed.entries:
        ctype = r.headers.get("content-type", "?").split(";")[0]
        head = re.sub(r"\s+", " ", r.text[:100]).strip()
        raise FeedEmpty(f"HTTP 200 but no feed entries (content-type {ctype}, starts: {head!r})")
    return sanitise(r.content)


def parse_feed(source: dict, raw: bytes | str | None = None) -> list[Item]:
    """Parse one feed. Tries `url`, then each of `fallback_urls`.
    `raw` lets tests pass fixture content."""
    if raw is None:
        errors = []
        for url in [source["url"], *source.get("fallback_urls", [])]:
            try:
                raw = _fetch(url)
                break
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        if raw is None:
            raise RuntimeError(" | ".join(errors))
    feed = feedparser.parse(sanitise(raw.encode() if isinstance(raw, str) else raw))
    items = []
    for e in feed.entries:
        link = unwrap_link(e.get("link", ""))
        if not link:
            continue
        pub = _published(e)
        items.append(Item(
            id=link if source.get("type") == "alerts" else (e.get("id") or link),
            source_id=source["id"],
            source_name=source["name"],
            source_type=source.get("type", ""),
            licence=source.get("licence", "link_only"),
            title=strip_html(e.get("title", "")),
            link=link,
            published=pub.isoformat() if pub else "",
            summary=strip_html(e.get("summary", ""))[:1200],
            image_url=_full_size(_first_image(e)),
            credit_hint=source.get("credit_hint", ""),
            source_tag=source.get("tag", ""),
        ))
    return items


def scout(sources: list[dict], seen: set[str], max_age_days: int = 7,
          now: datetime | None = None, report: dict | None = None,
          exclude_domains: list[str] | None = None) -> list[Item]:
    """New items from all sources, newest first within each source.
    Drops items already seen, older than max_age_days, from excluded sites,
    beyond a source's max_new, or already found via an earlier source."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    excluded = {d.lower().removeprefix("www.") for d in exclude_domains or []}
    seen_keys = {link_key(k) for k in seen if k.startswith("http")}
    taken: set[str] = set()
    fresh: list[Item] = []
    for src in sources:
        if src.get("not_configured"):
            if report is not None:
                report[src["name"]] = f"not configured ({src['not_configured']})"
            continue
        try:
            items = parse_feed(src)
        except Exception as exc:  # one bad feed must not kill the run
            print(f"[scout] {src['id']}: failed ({exc})")
            if report is not None:
                report[src["name"]] = f"failed: {exc}"
            continue
        items.sort(key=lambda i: i.published or "", reverse=True)
        kept = dup = skip = 0
        cap = src.get("max_new")
        for it in items:
            key = link_key(it.link)
            if it.link in seen or it.id in seen or key in seen_keys:
                continue
            if it.published and datetime.fromisoformat(it.published) < cutoff:
                continue
            if any(domain(it.link) == d or domain(it.link).endswith("." + d) for d in excluded):
                skip += 1
                continue
            if key in taken:
                dup += 1
                continue
            if cap and kept >= cap:
                break
            taken.add(key)
            fresh.append(it)
            kept += 1
        note = f"{len(items)} in feed, {kept} new"
        if dup:
            note += f", {dup} already found via another source"
        if skip:
            note += f", {skip} from excluded sites"
        print(f"[scout] {src['id']}: {note}")
        if report is not None:
            report[src["name"]] = note
    return fresh
