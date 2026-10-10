"""Open each shortlisted item's page and pull the facts a post needs:
the full caption text, the lead image and, above all, the real credit and
licence line. The drafting step may only state what is found here."""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from .scout import HEADERS, Item

MAX_TEXT = 6000


def _meta(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return (tag.get("content") or "").strip() if tag else ""


def extract(item: Item, page_html: str) -> Item:
    soup = BeautifulSoup(page_html, "html.parser")
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
        return item
