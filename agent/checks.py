"""Mechanical checks for the Geoawesome repost format.
Returns plain-language problems so they can be sent back to Claude
for a fix, or shown to the editor."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"(?<![\w&/])#(\w+)")
X_URL_LEN = 23


def load_rules(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def x_length(text: str) -> int:
    return len(URL_RE.sub("x" * X_URL_LEN, text))


def check_text(kind: str, text: str, rules: dict, link: str) -> list[str]:
    """kind is 'caption' (Facebook/Instagram/YouTube) or 'x'."""
    problems: list[str] = []
    spec = rules["platforms"][kind]
    low = text.lower()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ["text is empty"]

    if "—" in text:
        problems.append("contains an em dash (—)")
    for phrase in rules["banned_phrases"]:
        if re.search(r"(?<![\w-])" + re.escape(phrase.lower()) + r"(?![\w-])", low):
            problems.append(f'uses banned phrase "{phrase}"')
    for opener in rules["banned_openers"]:
        if lines[0].lower().startswith(opener):
            problems.append(f'opens with "{opener}"')
    if "link in bio" in low:
        problems.append('says "link in bio" (use the direct link)')

    # Link
    if link not in text:
        problems.append("source link missing")
    elif kind == "x":
        if not any(l == link for l in lines):
            problems.append("link is not on its own line")
    else:
        ctas = rules["cta_options"]
        cta = [l for l in lines if any(l.startswith(c) for c in ctas)]
        if not cta:
            problems.append("no CTA line (" + " / ".join(ctas) + ")")
        elif link not in cta[0]:
            problems.append("CTA line does not carry the source link")

    # Credit
    prefixes = tuple(p.lower() for p in rules["credit_prefixes"])
    if not any(l.lower().startswith(prefixes) for l in lines):
        problems.append("no credit line (" + " / ".join(rules["credit_prefixes"]) + ")")

    # Hashtags
    tags = HASHTAG_RE.findall(text)
    allow = {a.lower() for a in rules.get("hashtag_allowlist", [])}
    lo, hi = spec["hashtags_min"], spec["hashtags_max"]
    if not (lo <= len(tags) <= hi):
        problems.append(f"has {len(tags)} hashtags, needs {lo}-{hi}")
    if "geoawesome" not in {t.lower() for t in tags}:
        problems.append("missing #Geoawesome")
    for t in tags:
        if t.lower() not in allow and ("_" in t or re.search(r"[a-z][A-Z]", t)):
            problems.append(f"#{t} is not a single word")

    # Length
    n = x_length(text) if kind == "x" else len(text)
    if n > spec["max_chars"]:
        problems.append(f"{n} characters, limit {spec['max_chars']}")
    return problems


def check_draft(draft: dict, rules: dict, link: str) -> dict[str, list[str]]:
    out = {}
    for kind in rules["platforms"]:
        probs = check_text(kind, draft.get(kind, "") or "", rules, link)
        if probs:
            out[kind] = probs
    return out
