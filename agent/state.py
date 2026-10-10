"""Remember which items were already drafted, so nothing is suggested twice."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEEP_DAYS = 120


def load_seen(path: str | Path) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_seen(path: str | Path, seen: dict[str, str], new_keys: list[str]) -> None:
    now = datetime.now(timezone.utc)
    for k in new_keys:
        seen[k] = now.isoformat()
    cutoff = now - timedelta(days=KEEP_DAYS)
    seen = {k: v for k, v in seen.items() if datetime.fromisoformat(v) >= cutoff}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")
