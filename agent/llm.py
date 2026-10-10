"""Claude calls: shortlist, draft, and one repair pass when checks fail."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .scout import Item

SHORTLIST_MODEL = os.environ.get("SHORTLIST_MODEL", "claude-haiku-5-5")
DRAFT_MODEL = os.environ.get("DRAFT_MODEL", "claude-opus-5-5")
PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def _client():
    import anthropic
    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY


def parse_json(text: str) -> dict:
    """Pull the first JSON object out of a reply, tolerating code fences."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def _ask(model: str, system: str, messages: list[dict], max_tokens: int = 4000) -> str:
    resp = _client().messages.create(model=model, max_tokens=max_tokens,
                                     system=system, messages=messages)
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def shortlist(items: list[Item], limit: int, ask=None) -> list[dict]:
    """Return [{"id", "score", "why"}] for the best items, best first."""
    if not items:
        return []
    ask = ask or _ask
    listing = "\n\n".join(
        f"id: {it.id}\nsource: {it.source_name}\ntitle: {it.title}\n"
        f"has_image: {bool(it.image_url)}\nsummary: {it.summary[:500]}"
        for it in items
    )
    system = (PROMPTS / "shortlist_system.md").read_text(encoding="utf-8")
    reply = ask(SHORTLIST_MODEL, system,
                [{"role": "user", "content": f"New items:\n\n{listing}\n\nPick at most {limit}."}],
                1500)
    picks = parse_json(reply).get("picks", [])
    known = {it.id for it in items}
    return [p for p in picks if p.get("id") in known][:limit]


def source_block(it: Item) -> str:
    parts = [
        f"TITLE: {it.title}",
        f"PUBLISHER: {it.source_name}" + (f" (handle {it.source_tag})" if it.source_tag else ""),
        f"LINK: {it.link}",
        f"PUBLISHED: {it.published}",
        f"REUSE POLICY: {it.licence}",
        f"IMAGE CREDIT (verbatim from page): {it.image_credit or 'NOT FOUND, use: ' + it.credit_hint}",
        f"IMAGE LICENCE: {it.image_licence or 'not stated on page'}",
    ]
    if it.extra_images:
        parts.append("WARNING: " + "; ".join(it.extra_images))
    parts.append("\nPAGE TEXT:\n" + (it.page_text or it.summary))
    return "SOURCE MATERIAL\n\n" + "\n".join(parts)


def draft(it: Item, rules: dict, check_fn, ask=None) -> tuple[dict, dict]:
    """Draft posts, run checks, give Claude one chance to fix violations.
    Returns (draft, remaining_problems)."""
    ask = ask or _ask
    system = (PROMPTS / "draft_system.md").read_text(encoding="utf-8")
    messages = [{"role": "user", "content": source_block(it)}]
    reply = ask(DRAFT_MODEL, system, messages)
    result = parse_json(reply)
    if not result.get("worth_posting", True):
        return result, {}

    problems = check_fn(result, rules, it.link)
    if problems:
        listing = "\n".join(f"- {p}: {'; '.join(v)}" for p, v in problems.items())
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content":
                "These versions break the template rules:\n" + listing +
                "\n\nFix only what is listed. Keep everything else, keep the facts "
                "grounded in the source. Return the full JSON object again."},
        ]
        try:
            result = parse_json(ask(DRAFT_MODEL, system, messages))
            problems = check_fn(result, rules, it.link)
        except ValueError as exc:
            print(f"[draft] repair pass failed to parse: {exc}")
    return result, problems
