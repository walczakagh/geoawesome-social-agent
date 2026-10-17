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
    # Organisation-level keys must say which workspace to use; workspace-scoped
    # keys don't need this, so the header is only sent when the secret is set.
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    headers = {"anthropic-workspace-id": ws} if ws else None
    return anthropic.Anthropic(default_headers=headers)  # reads ANTHROPIC_API_KEY


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


BATCH = 20  # items per shortlist call; small batches keep each reply short

PICKS_TOOL = {
    "name": "submit_picks",
    "description": "Submit the scored items that reach the minimum score, best first.",
    "input_schema": {
        "type": "object",
        "properties": {"picks": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"},
                           "score": {"type": "number"},
                           "why": {"type": "string", "description": "under 15 words"}},
            "required": ["id", "score", "why"]}}},
        "required": ["picks"],
    },
}


def _ask_picks(model: str, system: str, messages: list[dict], max_tokens: int = 4000) -> str:
    """Shortlist call that forces Claude to answer through a tool, so the reply
    is always valid JSON (a plain-text reply was cut off mid-string on 17 Oct
    2026 and stopped the run). Returns the picks as a JSON string."""
    resp = _client().messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=messages,
        tools=[PICKS_TOOL], tool_choice={"type": "tool", "name": "submit_picks"})
    for b in resp.content:
        if getattr(b, "type", "") == "tool_use":
            if resp.stop_reason == "max_tokens":
                raise ValueError("shortlist reply hit max_tokens")
            return json.dumps(b.input)
    raise ValueError(f"no tool call in shortlist reply (stop_reason {resp.stop_reason})")


def _listing(items: list[Item], history: dict | None) -> str:
    from . import history as hist
    blocks = []
    for it in items:
        lines = [f"id: {it.id}", f"source: {it.source_name}", f"site: {hist.domain(it.link)}",
                 f"title: {it.title}"]
        h = hist.note(it.link, history or {})
        if h:
            lines.append(f"editor history: {h}")
        lines += [f"has_image: {bool(it.image_url)}", f"image: {it.image_url[:160]}",
                  f"summary: {it.summary[:500]}"]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def shortlist(items: list[Item], limit: int, ask=None, history: dict | None = None,
              min_score: float = 6) -> list[dict]:
    """Return [{"id", "score", "why"}] for the best items, best first.
    Large days are scored in batches, then the best of all batches are kept."""
    if not items:
        return []
    ask = ask or _ask_picks
    system = (PROMPTS / "shortlist_system.md").read_text(encoding="utf-8")
    known = {it.id for it in items}
    picks: list[dict] = []
    failed = 0
    batches = range(0, len(items), BATCH)
    for start in batches:
        batch = items[start:start + BATCH]
        msg = [{"role": "user", "content": f"New items:\n\n{_listing(batch, history)}\n\n"
                                           f"Score every item; return those scoring {min_score:g} or more."}]
        for attempt in (1, 2):  # one retry, then skip this batch rather than the whole run
            try:
                reply = ask(SHORTLIST_MODEL, system, msg, 4000)
                picks += [p for p in parse_json(reply).get("picks", []) if p.get("id") in known]
                break
            except ValueError as exc:
                print(f"[shortlist] batch {start // BATCH + 1}, attempt {attempt}: {exc}")
        else:
            failed += 1
    if failed and failed == len(batches):
        raise RuntimeError(f"all {failed} shortlist batches failed")
    best: dict[str, dict] = {}
    for p in picks:
        try:
            p["score"] = float(p.get("score", 0))
        except (TypeError, ValueError):
            continue
        if p["score"] >= min_score and p["score"] > best.get(p["id"], {}).get("score", -1):
            best[p["id"]] = p
    return sorted(best.values(), key=lambda p: -p["score"])[:limit]


def source_block(it: Item) -> str:
    parts = [
        f"TITLE: {it.title}",
        f"PUBLISHER: {it.source_name}" + (f" (handle {it.source_tag})" if it.source_tag else ""),
        f"LINK: {it.link}",
        f"PUBLISHED: {it.published}",
        f"SOURCE TYPE: {it.source_type} (reuse policy: {it.licence})",
        f"IMAGE CREDIT (verbatim from page): {it.image_credit or 'NOT FOUND, use: ' + it.credit_hint}",
        f"IMAGE LICENCE: {it.image_licence or 'not stated on page'}",
    ]
    if it.origin_url:
        parts.append(f"FEATURED MAP LIVES AT: {it.origin_url} (credit its maker/site)")
    parts.append(f"IMAGE NOTE: {it.image_note or 'square crop of the lead image'}")
    if len(it.images) > 1:
        parts.append("PAIR LABELS: top = " + it.images[0].get("label", "")
                     + ", bottom = " + it.images[1].get("label", ""))
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
                "\n\nFix only what is "
                "listed. Keep everything else and keep the facts grounded in the source. "
                "Return the full JSON object again."},
        ]
        try:
            result = parse_json(ask(DRAFT_MODEL, system, messages))
            problems = check_fn(result, rules, it.link)
        except ValueError as exc:
            print(f"[draft] repair pass failed to parse: {exc}")
    return result, problems
