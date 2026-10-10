"""Render the day's drafts as a Markdown digest (posted as a GitHub issue)."""
from __future__ import annotations

PLATFORM_LABELS = {
    "facebook": "Facebook", "instagram": "Instagram", "x": "X",
    "youtube": "YouTube Community", "linkedin": "LinkedIn",
}


def render(results: list[dict], run_date: str, sources: dict | None = None) -> str:
    ready = [r for r in results if r["draft"].get("worth_posting", True)]
    skipped = [r for r in results if not r["draft"].get("worth_posting", True)]

    out = [f"# Geoawesome social drafts, {run_date}", ""]
    if not ready:
        out.append("No items worth posting today.")
    else:
        out.append(f"{len(ready)} item(s) drafted. Nothing has been posted or scheduled. "
                   "Copy what you want, or ask Claude to send a numbered item to Metricool for review.")
    out.append("")

    for n, r in enumerate(ready, 1):
        it, d, probs = r["item"], r["draft"], r["problems"]
        reuse = "image can be used with credit" if d.get("image_ok") else \
            "LINK ONLY: ask the creator before attaching the image"
        out += [
            "---", "",
            f"## {n}. {it['title']}",
            f"**Source:** [{it['source_name']}]({it['link']}) · shortlist score {r['score']}/10 · {r['why']}",
            "",
        ]
        if it.get("image_url"):
            out += [f"<img src=\"{it['image_url']}\" width=\"480\">", ""]
        out += [
            f"**Image rights:** {reuse}",
            f"**Credit line:** {d.get('image_credit') or it.get('image_credit') or 'not found, check the page'}",
            f"**Licence on page:** {it.get('image_licence') or 'not stated'}",
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
            out.append("**Template rule problems left after one fix attempt:**")
            for p, v in probs.items():
                out.append(f"- {PLATFORM_LABELS.get(p, p)}: {'; '.join(v)}")
            out.append("")
        for p, label in PLATFORM_LABELS.items():
            text = (d.get("posts") or {}).get(p, "")
            if not text:
                continue
            flag = " ⚠️" if p in probs else ""
            out += [f"<details><summary><b>{label}</b>{flag}</summary>", "", "```text", text, "```",
                    "", "</details>", ""]

    if skipped:
        out += ["---", "", "### Skipped after reading the page"]
        out += [f"- [{r['item']['title']}]({r['item']['link']}): {r['draft'].get('skip_reason', '')}"
                for r in skipped]
    if sources:
        out += ["", "---", "", "### Sources checked"]
        out += [f"- {name}: {status}" for name, status in sources.items()]
    return "\n".join(out).rstrip() + "\n"
