"""Re-run enrich + square image for the items in a day's drafts JSON and
write the squares to tools/probe_results/images/. No Claude calls.

    python tools/recheck_images.py drafts/2026-10-17.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from agent import enrich, images
from agent.scout import Item

OUT = Path("tools/probe_results/images")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for n, r in enumerate(json.load(open(sys.argv[1])), 1):
        d = r["item"]
        it = Item(**{k: d[k] for k in ("id", "source_id", "source_name", "source_type", "licence",
                                       "title", "link", "published", "summary", "image_url",
                                       "credit_hint", "source_tag")})
        it = enrich.enrich(it)
        images.build(it, OUT / f"{n}.jpg")
        print(f"{n}. {it.title}\n   pick: {it.images[0]['url'] if it.images else '-'}\n   {it.image_note}")


if __name__ == "__main__":
    main()
