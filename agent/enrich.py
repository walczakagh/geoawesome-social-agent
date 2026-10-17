"""Open each shortlisted item's page and pull the facts a post needs:
the full caption text, the lead image and, above all, the real credit and
licence line. The drafting step may only state what is found here."""
from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .scout import HEADERS, Item, _full_size

MAX_TEXT = 6000


def _meta(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return (tag.get("content") or "").strip() if tag else ""


def _clean(url: str) -> str:
    return url.split("?")[0] if "assets.science.nasa.gov" in url else url


# Images that are never the visual a post wants: CMS thumbnails, locator maps,
# logos, and news-agency photos (Reuters, AP, Getty) that nobody may repost.
_BAD = re.compile(
    r"cq5dam\.thumbnail|/renditions/|[_-]th\.(?:jpe?g|png)|locator|logo|favicon|avatar|"
    r"headshot|portrait|RTRMADP|reuters|getty|gettyimages|shutterstock|apimages|/AP\d|"
    r"[_-]AP[_-]|alamy|afp[_-]",
    re.I)


def usable(url: str) -> bool:
    return bool(url) and not _BAD.search(url)


def _story_slug(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1].lower()


def pick_images(item: Item, soup: BeautifulSoup) -> list[dict]:
    """Ranked image candidates for a square post. If the first two carry
    labels they are a before/after pair; otherwise the first is the pick and
    the rest are fallbacks (images.py moves on when one is too small)."""
    base = item.link

    # NASA before/after slider: <img-comparison-slider><img slot="first"> <img slot="second">
    slider = soup.find("img-comparison-slider")
    if slider:
        imgs = [i.get("src") for i in slider.find_all("img") if i.get("src")]
        labels = [l.get_text(" ", strip=True) for l in soup.select(".curtain-view-label")][:2]
        if len(imgs) >= 2:
            labels = labels if len(labels) == 2 else ["Before", "After"]
            return [{"url": urljoin(base, _clean(imgs[0])), "label": labels[0]},
                    {"url": urljoin(base, _clean(imgs[1])), "label": labels[1]}]

    found: list[str] = []

    # ESA image pages: "HI-RES JPG" download link
    for a in soup.find_all("a", href=True):
        if "HI-RES JPG" in a.get_text(" ", strip=True).upper():
            found.append(urljoin(base, a["href"]))

    # NASA: the feed's lead image is the story's main image at full size.
    # Page figures only count when they sit in this story's own asset folder
    # (pages also show related stories' thumbnails and a small locator map).
    if "science.nasa.gov" in base:
        slug = _story_slug(base)
        if "assets.science.nasa.gov" in item.image_url:
            found.append(_clean(item.image_url))
        for fig in soup.select("figure"):
            cred = fig.select_one(".hds-credits")
            img = fig.find("img")
            src = img.get("src", "") if img else ""
            if (src and "assets.science.nasa.gov" in src and slug in src.lower()
                    and (cred is None or "NASA" in cred.get_text())):
                found.append(urljoin(base, _clean(src)))

    # Everything else: the feed's own image first (for Bellingcat or Maps Mania
    # it is the satellite screenshot or the map), then the page's preview image.
    found.append(_full_size(item.image_url))
    og = _meta(soup, "og:image")
    if og:
        found.append(urljoin(base, og))
    for img in soup.select("article img, main img, .entry-content img"):
        src = img.get("src") or img.get("data-src") or ""
        if src.startswith(("http", "/")):
            found.append(urljoin(base, src))

    out, seen = [], set()
    for u in found:
        if usable(u) and u not in seen:
            seen.add(u)
            out.append({"url": u, "label": ""})
    return out


def _origin_link(soup: BeautifulSoup) -> str:
    """Blogger posts wrap the featured screenshot in a link to the map itself."""
    sep = soup.select_one(".separator a[href]")
    if sep and sep.find("img"):
        return sep["href"]
    return ""


def extract(item: Item, page_html: str) -> Item:
    soup = BeautifulSoup(page_html, "html.parser")
    ranked = pick_images(item, soup)
    if len(ranked) >= 2 and ranked[0]["label"] and ranked[1]["label"]:
        item.images, item.alt_images = ranked[:2], []
    else:
        item.images, item.alt_images = ranked[:1], ranked[1:6]
    if item.source_type == "maps":
        item.origin_url = _origin_link(soup)
    for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        t.decompose()

    og_image = _meta(soup, "og:image")
    if og_image and not item.image_url:
        item.image_url = og_image

    # Body text: paragraphs are enough and keep the prompt small.
    paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = "\n".join(p for p in paras if len(p) > 40)
    item.page_text = text[:MAX_TEXT]

    credit, licence = "", ""

    # NASA Earth Observatory: <div class="hds-credits">NASA Earth Observatory / Name</div>
    nasa = [c.get_text(" ", strip=True) for c in soup.select(".hds-credits")]
    if nasa:
        # first credit belongs to the lead image; later ones may be third-party photos
        credit = nasa[0]
        third_party = sorted({c for c in nasa if "NASA" not in c})
        if third_party:
            item.extra_images = [f"Third-party image on page, not reusable without permission: {c}"
                                 for c in third_party]
        licence = "NASA imagery, generally free to reuse with credit"
        imgline = re.search(r"NASA Earth Observatory images? by [^.]+\.", text)
        if imgline:
            credit = imgline.group(0).rstrip(".")

    # ESA: "CREDIT <text> LICENCE <text>" blocks on image pages
    full = soup.get_text("\n", strip=True)
    m = re.search(r"\bCREDIT\b\s*\n(.+?)\n\s*\bLICENCE\b\s*\n(.+?)\n", full, re.S)
    if m:
        credit = re.sub(r"\s+", " ", m.group(1)).strip()
        licence = re.sub(r"\s+", " ", m.group(2)).strip()

    # Generic fallbacks
    if not credit:
        m = re.search(r"(?:Image credit|Credit|Copyright)\s*[:©]\s*([^\n]{3,160})", full, re.I)
        if m:
            credit = m.group(1).strip()
    if not licence:
        m = re.search(r"(CC[ -]BY[^\n]{0,30})", full)
        if m:
            licence = m.group(1).strip()

    item.image_credit = credit
    item.image_licence = licence
    return item


def enrich(item: Item, timeout: int = 20) -> Item:
    try:
        r = requests.get(item.link, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return extract(item, r.text)
    except Exception as exc:
        print(f"[enrich] {item.link}: {exc}")
        item.page_text = item.summary
        if usable(item.image_url):
            item.images = [{"url": _full_size(item.image_url), "label": ""}]
        return item
