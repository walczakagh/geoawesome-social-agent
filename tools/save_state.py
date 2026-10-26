"""Commit and push what a workflow run changed, merging the JSON state files
instead of failing when another run pushed first.

Usage (from a workflow step):
    python tools/save_state.py "Commit message" state drafts

Why: a run checks out the repo when it starts, but another run (a box ticked
a few seconds later, the 10-minute publish check, the daily drafts) may push
new state before this one finishes. A plain `git pull --rebase` then stops on
a conflict in state/queue.json and the run's changes are lost.

How: the files this run changed are applied on top of the latest origin. For
state/*.json a three-way merge (common start, this run, origin) keeps both
sides: queue entries added or removed by either run, publish records from
either run (a post that went out is never forgotten, so it cannot be posted
twice), and every seen item. Other files this run created or changed are
taken as they are.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BRANCH_REMOTE = "origin"


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def show(rev: str, path: str):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


# ------------------------------------------------------------------ merges

def merge_queue(base, ours, theirs) -> list:
    base, ours, theirs = base or [], ours or [], theirs or []
    b = {q["post_id"] for q in base}
    o = {q["post_id"]: q for q in ours}
    out = [q for q in theirs if not (q["post_id"] in b and q["post_id"] not in o)]  # we removed it
    have = {q["post_id"] for q in out}
    out += [q for pid, q in o.items() if pid not in b and pid not in have]          # we added it
    return sorted(out, key=lambda q: q.get("slot", ""))


def _merge_record(o: dict, t: dict) -> dict:
    rec = {**o, **t}  # network URLs and "done" from either side survive
    att = dict(t.get("attempts", {}))
    for net, n in o.get("attempts", {}).items():
        att[net] = max(n, att.get(net, 0))
    rec["attempts"] = att
    if o.get("errors") or t.get("errors"):
        rec["errors"] = {**o.get("errors", {}), **t.get("errors", {})}
    return rec


def merge_published(base, ours, theirs) -> dict:
    base, ours, theirs = base or {}, ours or {}, theirs or {}
    out = dict(theirs)
    for pid, rec in ours.items():
        if pid not in out or out[pid] == base.get(pid):
            out[pid] = rec
        elif rec != base.get(pid):
            out[pid] = _merge_record(rec, out[pid])
    return out


def merge_seen(base, ours, theirs) -> dict:
    out = dict(theirs or {})
    for k, v in (ours or {}).items():
        out[k] = max(v, out.get(k, v))
    return out


def merge_generic(base, ours, theirs):
    if ours == base:
        return theirs
    if theirs == base or theirs is None:
        return ours
    if isinstance(ours, dict) and isinstance(theirs, dict):
        return {**theirs, **ours}
    return ours


MERGERS = {"state/queue.json": merge_queue, "state/published.json": merge_published,
           "state/seen.json": merge_seen}


def dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")


# ------------------------------------------------------------------ main

def changed_files(base: str, paths: list[str]) -> list[str]:
    tracked = git("diff", "--name-only", base, "--", *paths).split()
    untracked = git("ls-files", "--others", "--exclude-standard", "--", *paths).split()
    return sorted(set(tracked) | set(untracked))


def save(message: str, paths: list[str], attempts: int = 4) -> int:
    base = git("rev-parse", "HEAD").strip()
    files = changed_files(base, paths)
    if not files:
        print("[save] nothing changed")
        return 0
    ours = {f: Path(f).read_bytes() if Path(f).exists() else None for f in files}
    print(f"[save] {len(files)} changed file(s): {', '.join(files)}")

    for n in range(1, attempts + 1):
        git("fetch", "--quiet", BRANCH_REMOTE, "HEAD")
        remote = git("rev-parse", "FETCH_HEAD").strip()
        # index and HEAD = latest origin; working tree keeps this run's files
        git("reset", "--quiet", "--mixed", remote)
        for f, data in ours.items():
            p = Path(f)
            if data is None:  # this run deleted it
                if p.exists():
                    p.unlink()
                git("rm", "--quiet", "--cached", "--ignore-unmatch", f)
                continue
            if f.startswith("state/") and f.endswith(".json") and remote != base:
                merged = MERGERS.get(f, merge_generic)(show(base, f), json.loads(data), show(remote, f))
                dump(p, merged)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            git("add", "--", f)
        if not git("diff", "--cached", "--name-only").strip():
            print("[save] nothing left to commit after merging")
            return 0
        git("commit", "--quiet", "-m", message)
        r = subprocess.run(["git", "push", "--quiet", BRANCH_REMOTE, "HEAD"], capture_output=True, text=True)
        if r.returncode == 0:
            note = " (merged with newer state)" if remote != base else ""
            print(f"[save] pushed{note}")
            return 0
        print(f"[save] push attempt {n} rejected, retrying: {r.stderr.strip()[:200]}")
        base = remote  # our commit now sits on `remote`; merge again from there
        ours = {f: Path(f).read_bytes() if Path(f).exists() else None for f in files}
        git("reset", "--quiet", "--mixed", remote)
    print("[save] could not push after retries", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: save_state.py MESSAGE PATH [PATH ...]")
    sys.exit(save(sys.argv[1], sys.argv[2:]))
