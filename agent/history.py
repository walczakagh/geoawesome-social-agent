"""What the editor has picked before, per site, from the Google Alerts sheet.
Gives the shortlist a prior: a site whose alerts were highlighted 7 times out
of 10 deserves a closer look than one picked 0 times out of 40."""
from __future__ import annotations

from pathlib import Path

import yaml

from .scout import domain


def load(path: str | Path) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("sites", {}) or {}


def note(url: str, sites: dict[str, dict]) -> str:
    """'picked 18 of 20' for the item's site (or its parent domain), else ''."""
    d = domain(url)
    while d:
        h = sites.get(d)
        if h:
            return f"picked {h['picked']} of {h['seen']} alerts from {d}"
        d = d.partition(".")[2] if d.count(".") > 1 else ""
    return ""
