"""Daily run: scout -> shortlist -> enrich -> square image -> caption -> check -> digest.

Usage:
    python -m agent.main                 # normal run
    python -m agent.main --max-items 3   # draft at most 3 items (default: config/drafts.yaml)
    python -m agent.main --dry-run       # do not update state/seen.json
    python -m agent.main --scout-only    # list today's candidates, no Claude calls
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import re

from . import checks, enrich, history, images, llm, review, scout, state

ROOT = Path(__file__).resolve().parent.parent


def scout_only(max_age_days: int) -> None:
    """Print what today's run would hand to the shortlist. No API key needed."""
    config = scout.load_config(ROOT / "config/sources.yaml")
    sources = scout.load_sources(ROOT / "config/sources.yaml")
    seen = state.load_seen(ROOT / "state/seen.json")
    past = history.load(ROOT / "config/editor_history.yaml")
    report: dict = {}
    items = scout.scout(sources, set(seen), max_age_days=max_age_days, report=report,
                        exclude_domains=config.get("exclude_domains"))
    print("\n## Sources")
    for name, status in report.items():
        print(f"- {name}: {status}")
    print(f"\n## {len(items)} candidate items")
    for it in items:
        h = history.note(it.link, past)
        print(f"- [{it.source_name}] {it.title}  ({it.published[:10]}){'  · ' + h if h else ''}\n  {it.link}"
              + (f"\n  image: {it.image_url}" if it.source_id.startswith("reddit") else ""))


def draft_settings() -> dict:
    """config/drafts.yaml: drafts_per_day and min_score."""
    import yaml
    p = ROOT / "config/drafts.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {"drafts_per_day": int(cfg.get("drafts_per_day", 6)),
            "min_score": float(cfg.get("min_score", 6))}


def run(max_items: int, max_age_days: int, dry_run: bool, out_dir: Path,
        min_score: float = 6) -> Path:
    rules = checks.load_rules(ROOT / "config/style_rules.yaml")
    config = scout.load_config(ROOT / "config/sources.yaml")
    sources = scout.load_sources(ROOT / "config/sources.yaml")
    past = history.load(ROOT / "config/editor_history.yaml")
    seen_path = ROOT / "state/seen.json"
    seen = state.load_seen(seen_path)

    report: dict = {}
    items = scout.scout(sources, set(seen), max_age_days=max_age_days, report=report,
                        exclude_domains=config.get("exclude_domains"))
    print(f"[main] {len(items)} new items")

    try:
        picks = llm.shortlist(items, limit=max_items, history=past, min_score=min_score)
    except Exception as exc:
        sys.exit(f"ERROR: shortlist call to Claude failed ({type(exc).__name__}): {exc}")
    by_id = {it.id: it for it in items}
    print(f"[main] shortlisted {len(picks)}")

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []
    for n, p in enumerate(picks, 1):
        it = enrich.enrich(by_id[p["id"]])
        slug = re.sub(r"[^a-z0-9]+", "-", it.title.lower())[:40].strip("-")
        img_path = out_dir / "images" / f"{run_date}-{n}-{slug}.jpg"
        images.build(it, img_path)
        if it.square_path:
            try:  # store repo-relative so GitHub URLs work in the issue
                it.square_path = str(Path(it.square_path).resolve().relative_to(ROOT))
            except ValueError:
                pass
        print(f"[main] image {n}: {it.image_note}")
        try:
            d, probs = llm.draft(it, rules, checks.check_draft)
        except Exception as exc:
            print(f"[main] draft failed for {it.link}: {exc}")
            continue
        if not d.get("worth_posting", True) and it.square_path:
            Path(ROOT / it.square_path).unlink(missing_ok=True)
            it.square_path = ""
        entry = {"item": it.to_dict(), "score": p.get("score"), "why": p.get("why", ""),
                 "draft": d, "problems": probs}
        entry["image_url"] = review.image_urls(it.square_path)[1]
        results.append(entry)

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
    ap.add_argument("--max-items", type=int, default=None,
                    help="override drafts_per_day from config/drafts.yaml")
    ap.add_argument("--max-age-days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scout-only", action="store_true",
                    help="list today's candidates and stop (no Claude calls, nothing written)")
    ap.add_argument("--out", default=str(ROOT / "drafts"))
    a = ap.parse_args()
    if a.scout_only:
        scout_only(a.max_age_days)
        return
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        sys.exit("ERROR: ANTHROPIC_API_KEY is empty. Add it under Settings > Secrets and "
                 "variables > Actions > Repository secrets, named exactly ANTHROPIC_API_KEY.")
    ds = draft_settings()
    run(a.max_items or ds["drafts_per_day"], a.max_age_days, a.dry_run, Path(a.out),
        min_score=ds["min_score"])


if __name__ == "__main__":
    main()
