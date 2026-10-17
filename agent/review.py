"""Render the day's reposts as a Markdown digest (posted as a GitHub issue)."""
from __future__ import annotations

import os
from pathlib import Path


LABELS = {"facebook": "Facebook", "instagram": "Instagram",
          "youtube": "YouTube Community", "x": "X (via Buffer)"}


def platform_text(draft: dict, kind: str) -> str:
    """The text for one platform; drafts written before 17 Oct 2026 had one
    shared "caption" for Facebook, Instagram and YouTube."""
    return draft.get(kind) or (draft.get("caption", "") if kind != "x" else "")


def image_urls(path: str) -> tuple[str, str]:
    """(url to show in the issue, raw url to download / hand to Metricool)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo or not path:
        return path, path
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    return (f"https://github.com/{repo}/blob/{branch}/{path}?raw=true",
            f"https://raw.githubusercontent.com/{repo}/{branch}/{path}")


def render(results: list[dict], run_date: str, sources: dict | None = None) -> str:
    ready = [r for r in results if r["draft"].get("worth_posting", True)]
    skipped = [r for r in results if not r["draft"].get("worth_posting", True)]

    out = [f"# Geoawesome reposts, {run_date}", ""]
    if not ready:
        out.append("No items worth posting today.")
    else:
        out.append(f"{len(ready)} post(s) ready. Each has a 1080×1080 image and its own text for "
                   "Facebook, Instagram, YouTube Community and X. Nothing has been posted.")
        out += ["", "**To publish:** tick the box under a post. It is queued for the next free "
                "time slot and posted to Facebook and Instagram; a comment here confirms the time. "
                "Edit a text in this issue first if you want to change it (what is in the box is "
                "what gets posted). Untick before the slot to cancel. When it is live, a comment "
                "@mentions you with the YouTube Community text and the X text for Buffer."]
    out.append("")

    for n, r in enumerate(ready, 1):
        it, d, probs = r["item"], r["draft"], r["problems"]
        show, raw = image_urls(it.get("square_path", ""))
        rights = ("Publisher allows reuse with credit." if it.get("licence") == "reuse" else
                  "Someone else's map: the credit line names them; ask them first if unsure.")
        out += ["---", "", f"## {n}. {it['title']}", ""]
        if it.get("square_path"):
            out += [f"<!-- post:{Path(it['square_path']).stem} -->",
                    "- [ ] **Publish on Facebook + Instagram**", ""]
        if show:
            out += [f'<img src="{show}" width="420">', "",
                    f"[Download square image]({raw}) · {it.get('image_note', '')}", ""]
        else:
            out += [f"⚠️ No square image: {it.get('image_note') or 'none found'}. "
                    f"Screenshot it from the [source page]({it['link']}).", ""]
        out += [
            f"**Source:** [{it['source_name']}]({it['link']}) · score {r['score']}/10 · {r['why']}",
            f"**Rights:** {rights} Licence on page: {it.get('image_licence') or 'not stated'}.",
            "",
            f"**What it shows:** {d.get('what_it_shows', '')}",
            "",
        ]
        for w in it.get("extra_images") or []:
            out.append(f"> ⚠️ {w}")
        unc = d.get("uncertainties") or []
        if unc:
            out.append("**Check before posting:**")
            out += [f"- {u}" for u in unc]
            out.append("")
        if probs:
            out.append("**Rule problems left after one fix attempt:**")
            for k, v in probs.items():
                out.append(f"- {LABELS.get(k, k)}: {'; '.join(v)}")
            out.append("")
        for k in ("facebook", "instagram", "youtube", "x"):
            out += [f"**{LABELS[k]}**", "", "```text", platform_text(d, k), "```", ""]
        if d.get("alt_text"):
            out += [f"**Alt text:** {d['alt_text']}", ""]

    if skipped:
        out += ["---", "", "### Skipped after reading the page"]
        out += [f"- [{r['item']['title']}]({r['item']['link']}): {r['draft'].get('skip_reason', '')}"
                for r in skipped]
    if sources:
        out += ["", "---", "", "### Sources checked"]
        out += [f"- {name}: {status}" for name, status in sources.items()]
    return "\n".join(out).rstrip() + "\n"
