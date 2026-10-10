"""Daily run: scout -> shortlist -> enrich -> draft -> check -> digest.

Usage:
    python -m agent.main                 # normal run
    python -m agent.main --max-items 3   # draft at most 3 items
    python -m agent.main --dry-run       # do not update state/seen.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import checks, enrich, llm, review, scout, state

ROOT = Path(__file__).resolve().parent.parent


def run(max_items: int, max_age_days: int, dry_run: bool, out_dir: Path) -> Path:
    rules = checks.load_rules(ROOT / "config/style_rules.yaml")
    sources = scout.load_sources(ROOT / "config/sources.yaml")
    seen_path = ROOT / "state/seen.json"
    seen = state.load_seen(seen_path)

    report: dict = {}
    items = scout.scout(sources, set(seen), max_age_days=max_age_days, report=report)
    print(f"[main] {len(items)} new items")

    try:
        picks = llm.shortlist(items, limit=max_items)
    except Exception as exc:
        sys.exit(f"ERROR: shortlist call to Claude failed ({type(exc).__name__}): {exc}")
    by_id = {it.id: it for it in items}
    print(f"[main] shortlisted {len(picks)}")

    results = []
    for p in picks:
        it = enrich.enrich(by_id[p["id"]])
        try:
            d, probs = llm.draft(it, rules, checks.check_draft)
        except Exception as exc:
            print(f"[main] draft failed for {it.link}: {exc}")
            continue
        results.append({"item": it.to_dict(), "score": p.get("score"), "why": p.get("why", ""),
                        "draft": d, "problems": probs})

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_date}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
    digest = out_dir / f"{run_date}.md"
    digest.write_text(review.render(results, run_date, report), encoding="utf-8")

    if not dry_run:
        # Everything that was looked at today is marked seen, drafted or not,
        # so tomorrow's run only considers genuinely new items.
        state.save_seen(seen_path, seen, [it.link for it in items])
    print(f"[main] wrote {digest}")
    return digest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-items", type=int, default=4)
    ap.add_argument("--max-age-days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "drafts"))
    a = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        sys.exit("ERROR: ANTHROPIC_API_KEY is empty. Add it under Settings > Secrets and "
                 "variables > Actions > Repository secrets, named exactly ANTHROPIC_API_KEY.")
    run(a.max_items, a.max_age_days, a.dry_run, Path(a.out))


if __name__ == "__main__":
    main()
