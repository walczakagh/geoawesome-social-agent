"""Mechanical checks for the Geoawesome post template.
Returns plain-language violations so they can be sent back to Claude
for a fix, or shown to the editor."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"(?<![\w&])#(\w+)")
X_URL_LEN = 23


def load_rules(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def x_length(text: str) -> int:
    return len(URL_RE.sub("x" * X_URL_LEN, text))


def _cta_line_index(lines: list[str], ctas: list[str]) -> int:
    for i, line in enumerate(lines):
        if any(line.strip().startswith(c) for c in ctas):
            return i
    return -1


def check_post(platform: str, text: str, rules: dict, link: str, image_ok: bool) -> list[str]:
    problems: list[str] = []
    spec = rules["platforms"].get(platform, {})
    low = text.lower()
    lines = [l for l in text.splitlines() if l.strip()]

    if not text.strip():
        return ["post is empty"]

    if "—" in text:
        problems.append("contains an em dash (—)")

    for phrase in rules["banned_phrases"]:
        if re.search(r"(?<![\w-])" + re.escape(phrase.lower()) + r"(?![\w-])", low):
            problems.append(f'uses banned phrase "{phrase}"')

    first = lines[0].lower() if lines else ""
    for opener in rules["banned_openers"]:
        if first.startswith(opener):
            problems.append(f'opens with "{opener}"')
    if "link in bio" in low:
        problems.append('says "link in bio" (use the direct link)')

    # CTA + link
    ctas = rules["cta_options"]
    cta_i = _cta_line_index(lines, ctas)
    if platform == "x":
        if not any(l.strip() == link or l.strip().startswith(link) for l in lines):
            problems.append("link is not on its own line")
    else:
        if cta_i < 0:
            problems.append("no CTA line (" + " / ".join(ctas) + ")")
        elif link not in lines[cta_i]:
            problems.append("CTA line does not carry the source link")
    if link not in text:
        problems.append("source link missing")

    # Hashtags
    tags = HASHTAG_RE.findall(text)
    allow = {a.lower() for a in rules.get("hashtag_allowlist", [])}
    lo, hi = spec.get("hashtags_min", 0), spec.get("hashtags_max", 99)
    if not (lo <= len(tags) <= hi):
        want = f"{lo}" if lo == hi else f"{lo}-{hi}"
        problems.append(f"has {len(tags)} hashtags, needs {want}")
    if "geoawesome" not in {t.lower() for t in tags}:
        problems.append("missing #Geoawesome")
    for t in tags:
        if t.lower() in allow:
            continue
        if "_" in t or re.search(r"[a-z][A-Z]", t):
            problems.append(f"#{t} is not a single word")
    # at least one hashtag used inside the body (before the CTA / link line)
    body_end = cta_i if cta_i >= 0 else len(lines) - 1
    if platform == "x":
        body_end = next((i for i, l in enumerate(lines) if link in l), len(lines) - 1)
    body = "\n".join(lines[:body_end])
    if tags and not HASHTAG_RE.search(body):
        problems.append("no hashtag used naturally inside the body text")

    # Image credit
    has_credit = any(l.strip().lower().startswith("image:") for l in lines)
    if image_ok and not has_credit:
        problems.append('image is used but there is no "Image: <credit>" line')
    if not image_ok and has_credit:
        problems.append("image credit present although the image may not be reused")

    # Length
    n = x_length(text) if platform == "x" else len(text)
    if n > spec.get("max_chars", 10_000):
        problems.append(f"{n} characters, limit {spec['max_chars']}")

    return problems


def check_draft(draft: dict, rules: dict, link: str) -> dict[str, list[str]]:
    image_ok = bool(draft.get("image_ok"))
    out = {}
    for platform in rules["platforms"]:
        text = (draft.get("posts") or {}).get(platform, "")
        probs = check_post(platform, text, rules, link, image_ok)
        if probs:
            out[platform] = probs
    return out
