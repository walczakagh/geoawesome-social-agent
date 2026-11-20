"""Publish ticked posts from a daily issue to the Facebook Page and Instagram.

    python -m agent.publish enqueue   # run on an issue edit: queue ticked posts, cancel unticked
    python -m agent.publish run       # run hourly: publish posts whose slot has come
    python -m agent.publish check     # verify META_PAGE_TOKEN without posting anything

How it works
- Each post in a daily issue has a hidden marker <!-- post:ID --> and a box
  "- [ ] **Publish on Facebook + Instagram**".
- Ticking the box (an issue edit) puts the post into the next free slot from
  config/publish.yaml and comments the time on the issue. Unticking before the
  slot cancels it.
- At publish time the issue is read again, so caption edits made in the issue
  up to then are what gets posted, and a box unticked in the meantime stops it.
- state/queue.json holds queued posts, state/published.json what went out.
- An image dropped into the issue by hand (GitHub's user-attachments link)
  replaces the agent's square. Meta cannot use those links directly (PNG,
  redirects, odd sizes), so the run downloads it, makes a 1080x1080 JPEG in
  drafts/images/ and posts that.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "state/queue.json"
PUBLISHED = ROOT / "state/published.json"

# ---------------------------------------------------------------- issue parsing

_MARK = re.compile(r"<!-- post:([\w.-]+) -->")
_BOX = re.compile(r"^- \[( |x|X)\] \*\*Publish on Facebook \+ Instagram\*\*", re.M)
_CAPTION = re.compile(r"```text\n(.*?)\n?```", re.S)
# a labelled text box: **Facebook** / **Instagram** / **YouTube Community** / **X (via Buffer)**
# (issues before 17 Oct 2026 had one box "**Facebook · Instagram · YouTube Community**")
_BLOCK = re.compile(r"\*\*(Facebook|Instagram|YouTube Community|X)\b[^*\n]*\*\*\s*\n\s*```text\n(.*?)\n?```", re.S)
_KEYS = {"Facebook": "facebook", "Instagram": "instagram", "YouTube Community": "youtube", "X": "x"}
# "[Download square image](...)" from the agent; any "[Download ... image](...)"
# wording is accepted so a renamed link still works.
_IMAGE = re.compile(r"\[Download [^\]\n]*?image\]\((\S+?)\)", re.I)
# images pasted into the issue by hand: <img src="..."> or ![...](...)
_EMBED = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"|!\[[^\]\n]*\]\((\S+?)\)')
_ATTACHMENT = re.compile(r"github\.com/user-attachments/|user-images\.githubusercontent\.com/")
_ALT = re.compile(r"\*\*Alt text:\*\*\s*(.+)")
_TITLE = re.compile(r"^## \d+\. (.+)$", re.M)


def parse_issue(body: str) -> dict[str, dict]:
    """{post_id: {checked, title, texts{facebook,instagram,youtube,x}, caption,
    image_url, alt_text}} for every post in the issue that carries a publish box.
    `caption` is the Facebook text (kept for older callers)."""
    body = (body or "").replace("\r\n", "\n")
    marks = list(_MARK.finditer(body))
    posts = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        chunk = body[m.end():end]
        cut = re.search(r"\n---\n|\n### ", chunk)  # end of this post's section
        if cut:
            chunk = chunk[:cut.start()]
        titles = _TITLE.findall(body[:m.start()])
        box = _BOX.search(chunk)
        cap = _CAPTION.search(chunk)
        texts = {}
        for label, text in _BLOCK.findall(chunk):
            texts.setdefault(_KEYS[label], text.strip())
        shared = texts.get("facebook") or (cap.group(1).strip() if cap else "")
        for k in ("facebook", "instagram", "youtube"):
            texts.setdefault(k, shared)  # one shared box in older issues
        texts.setdefault("x", "")
        alt = _ALT.search(chunk)
        posts[m.group(1)] = {
            "checked": bool(box and box.group(1).lower() == "x"),
            "title": titles[-1].strip() if titles else m.group(1),
            "texts": texts,
            "caption": texts["facebook"],
            "image_url": pick_image(chunk),
            "alt_text": alt.group(1).strip() if alt else "",
        }
    return posts


def pick_image(chunk: str) -> str:
    """The image to post for one section: an image uploaded into the issue by
    hand wins (that is how a replacement is made), then the download link,
    then any other embedded image."""
    embeds = [a or b for a, b in _EMBED.findall(chunk)]
    for u in embeds:
        if _ATTACHMENT.search(u):
            return u
    link = _IMAGE.search(chunk)
    if link:
        return link.group(1)
    return embeds[0] if embeds else ""


def needs_conversion(url: str) -> bool:
    """True for images Meta should not be handed as they are: GitHub
    attachments and anything that is not a JPEG."""
    path = url.split("?", 1)[0].lower()
    return bool(_ATTACHMENT.search(url)) or not path.endswith((".jpg", ".jpeg"))


def localise_image(post_id: str, url: str, http=None) -> str:
    """Download `url`, write a 1080x1080 JPEG to drafts/images/ and return the
    public raw URL of that file. The name carries a hash of the source so a
    second replacement gets a new URL (raw.githubusercontent caches for minutes)."""
    from PIL import Image, ImageOps
    from .images import make_square
    from .review import image_urls

    http = http or requests
    headers = {"User-Agent": "geoawesome-social-agent"}
    r = http.get(url, headers=headers, timeout=60, allow_redirects=True)
    if r.status_code in (401, 403, 404) and os.environ.get("GITHUB_TOKEN"):
        r = http.get(url, timeout=60, allow_redirects=True,
                     headers={**headers, "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"})
    r.raise_for_status()
    pic = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content)))
    if pic.mode != "RGB":
        bg = Image.new("RGB", pic.size, "white")
        pic = pic.convert("RGBA")
        bg.paste(pic, mask=pic.split()[-1])
        pic = bg
    square, _ = make_square([pic], [""])
    rel = f"drafts/images/{post_id}-r{hashlib.sha1(url.encode()).hexdigest()[:8]}.jpg"
    out = ROOT / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    square.save(out, "JPEG", quality=90, optimize=True, progressive=True)
    return image_urls(rel)[1]


def _image_for(pid: str, src: str, localise) -> str:
    return localise(pid, src) if needs_conversion(src) else src


# ---------------------------------------------------------------- state and slots

def load_cfg(path: Path = ROOT / "config/publish.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load(p: Path, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _save(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                 encoding="utf-8")


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _specs(cfg: dict, day=None) -> list[tuple[str, str | None]]:
    """Slots for one local day as (start, end): "16:00" is a point slot,
    ["16:00", "20:00"] a window. `weekday_slots` replaces the slots on the days
    it names. With slot_order "best_first" the slots are tried in the order they
    are listed (best first); otherwise by start time."""
    slots = cfg["slots"]
    if day is not None:
        slots = (cfg.get("weekday_slots") or {}).get(_DAYS[day.weekday()], slots)
    out = []
    for s in slots:
        if isinstance(s, (list, tuple)):
            out.append((s[0], s[1]))
        else:
            out.append((s, None))
    if cfg.get("slot_order", "time") == "best_first":
        return out
    return sorted(out)  # by start time, whatever order they are listed in


def _at(day, hhmm: str, tz) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(timezone.utc)


def next_slot(now: datetime, taken: set[str], cfg: dict) -> datetime:
    """Start of the first free slot (UTC) that can still be met at least
    min_lead_minutes from now. A window counts while some of it is left.
    Days are filled one at a time, soonest first; within a day the slots are
    tried in time order, or in listed order with slot_order "best_first"."""
    tz = ZoneInfo(cfg["timezone"])
    earliest = now + timedelta(minutes=cfg.get("min_lead_minutes", 30))
    day = now.astimezone(tz).date()
    for _ in range(366):
        for start, end in _specs(cfg, day):
            t = _at(day, start, tz)
            last = _at(day, end, tz) if end else t
            if last >= earliest and t.isoformat() not in taken:
                return t
        day += timedelta(days=1)
    raise RuntimeError("no free slot within a year")


def jittered(base: datetime, now: datetime, cfg: dict, rng=random) -> datetime:
    """Actual posting time for the slot starting at `base`, never sooner than
    min_lead_minutes from now.
    Window slot: a random minute inside the window.
    Point slot: a random minute within +/- jitter_minutes of it."""
    tz = ZoneInfo(cfg["timezone"])
    earliest = now + timedelta(minutes=cfg.get("min_lead_minutes", 30))
    local = base.astimezone(tz)
    end = next((e for s, e in _specs(cfg, local.date()) if s == local.strftime("%H:%M")), None)
    if end:
        lo = max(base, earliest)
        hi = _at(local.date(), end, tz)
        t = lo + timedelta(minutes=rng.randint(0, max(int((hi - lo).total_seconds() // 60), 0)))
    else:
        j = int(cfg.get("jitter_minutes", 0))
        t = base + timedelta(minutes=rng.randint(-j, j)) if j else base
        if t < earliest:
            t = earliest + timedelta(minutes=rng.randint(0, max(j, 1)))
    return t.replace(second=0, microsecond=0)


def local_label(t: datetime | str, cfg: dict) -> str:
    if isinstance(t, str):
        t = datetime.fromisoformat(t)
    lt = t.astimezone(ZoneInfo(cfg["timezone"]))
    return lt.strftime("%a %d %b, %H:%M") + f" ({cfg['timezone'].split('/')[-1]} time)"


def enqueue(issue: int, body: str, now: datetime, cfg: dict,
            queue: list[dict], published: dict, rng=random,
            warn: bool = True, localise=None) -> tuple[list[str], list[str]]:
    """Queue newly ticked posts, drop unticked ones. Mutates queue.
    Returns (queued lines, cancelled lines) for the issue comment.
    `warn` False (the 10-minute sweep) keeps problems out of the comment, so a
    post that cannot be queued is reported once, on the issue edit, not every run."""
    localise = localise or localise_image
    posts = parse_issue(body)
    queued, cancelled = [], []
    in_queue = {q["post_id"]: q for q in queue}
    for pid, p in posts.items():
        tried = pid in published  # published, or failed after all retries: never re-queue
        if p["checked"] and pid in in_queue and not tried:
            q = in_queue[pid]  # image swapped in the issue after queueing
            if p["image_url"] and p["image_url"] != q.get("image_src", p["image_url"]):
                try:
                    q["image_url"] = _image_for(pid, p["image_url"], localise)
                    q["image_src"] = p["image_url"]
                    queued.append(f"- **{p['title']}**: new image picked up, "
                                  f"still {local_label(q['slot'], cfg)}")
                except Exception as exc:
                    if warn:
                        queued.append(f"- ⚠️ **{p['title']}**: new image could not be "
                                      f"used ({str(exc)[:200]}), keeping the old one")
        elif p["checked"] and pid not in in_queue and not tried:
            missing = [w for w, ok in (("image", p["image_url"]), ("caption", p["caption"])) if not ok]
            if missing:
                if warn:
                    queued.append(f"- ⚠️ **{p['title']}**: no {' or '.join(missing)} found, not queued")
                continue
            try:
                image = _image_for(pid, p["image_url"], localise)
            except Exception as exc:
                if warn:
                    queued.append(f"- ⚠️ **{p['title']}**: image could not be downloaded or "
                                  f"converted ({str(exc)[:200]}), not queued")
                continue
            base = next_slot(now, {q.get("base", q["slot"]) for q in queue}, cfg)
            slot = jittered(base, now, cfg, rng)
            entry = {"post_id": pid, "issue": issue, "base": base.isoformat(),
                     "slot": slot.isoformat(), "queued_at": now.isoformat(),
                     "image_src": p["image_url"]}
            if image != p["image_url"]:
                entry["image_url"] = image
            queue.append(entry)
            queued.append(f"- **{p['title']}**: {local_label(slot, cfg)}")
        elif not p["checked"] and pid in in_queue:
            queue[:] = [q for q in queue if q["post_id"] != pid]
            cancelled.append(f"- **{p['title']}**: cancelled")
    return queued, cancelled


# ---------------------------------------------------------------- Meta Graph API

class MetaError(RuntimeError):
    pass


class Meta:
    def __init__(self, token: str, version: str, http=None, sleep=time.sleep):
        self.token, self.base = token, f"https://graph.facebook.com/{version}"
        self.http, self.sleep = http or requests, sleep

    def _call(self, method: str, path: str, params: dict) -> dict:
        params = {**params, "access_token": self.token}
        if method == "GET":
            r = self.http.get(f"{self.base}/{path}", params=params, timeout=60)
        else:
            r = self.http.post(f"{self.base}/{path}", data=params, timeout=60)
        try:
            data = r.json()
        except ValueError:
            raise MetaError(f"HTTP {r.status_code}, not JSON")
        if "error" in data:
            e = data["error"]
            raise MetaError(f"{e.get('type', 'Error')} {e.get('code', '')}: {e.get('message', '')}")
        return data

    def page(self, page_id: str) -> dict:
        return self._call("GET", page_id, {"fields": "name,instagram_business_account{id,username}"})

    def facebook_photo(self, page_id: str, image_url: str, caption: str) -> str:
        """Photo post on the Page; returns its URL."""
        d = self._call("POST", f"{page_id}/photos", {"url": image_url, "caption": caption})
        return f"https://www.facebook.com/{d.get('post_id') or d['id']}"

    def instagram_photo(self, ig_id: str, image_url: str, caption: str, alt: str = "") -> str:
        """Feed post on Instagram; returns its permalink."""
        params = {"image_url": image_url, "caption": caption}
        if alt:
            params["alt_text"] = alt
        try:
            cid = self._call("POST", f"{ig_id}/media", params)["id"]
        except MetaError as exc:
            if "alt_text" not in params or "alt_text" not in str(exc):
                raise
            params.pop("alt_text")  # older API versions reject alt_text
            cid = self._call("POST", f"{ig_id}/media", params)["id"]
        for _ in range(30):  # Instagram fetches and processes the image first (Meta: poll up to 5 min)
            status = self._call("GET", cid, {"fields": "status_code"}).get("status_code")
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise MetaError(f"Instagram could not process the image ({status})")
            self.sleep(10)
        else:
            raise MetaError("Instagram image still processing after 5 minutes")
        mid = self._call("POST", f"{ig_id}/media_publish", {"creation_id": cid})["id"]
        return self._call("GET", mid, {"fields": "permalink"}).get("permalink", f"instagram media {mid}")


# ---------------------------------------------------------------- GitHub

class GitHub:
    def __init__(self, repo: str, token: str, http=None):
        self.base = f"https://api.github.com/repos/{repo}"
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        self.http = http or requests

    def issue_body(self, n: int) -> str:
        r = self.http.get(f"{self.base}/issues/{n}", headers=self.h, timeout=30)
        r.raise_for_status()
        return r.json().get("body") or ""

    def open_issues(self, label: str = "social-drafts") -> list[dict]:
        r = self.http.get(f"{self.base}/issues", headers=self.h, timeout=30,
                          params={"state": "open", "labels": label, "per_page": 30})
        r.raise_for_status()
        return [i for i in r.json() if "pull_request" not in i]

    def comment(self, n: int, text: str) -> None:
        r = self.http.post(f"{self.base}/issues/{n}/comments", headers=self.h,
                           json={"body": text}, timeout=30)
        r.raise_for_status()


# ---------------------------------------------------------------- publishing

def manual_steps(post: dict, cfg: dict, owner: str = "") -> str:
    """The comment part that @mentions the owner (so GitHub emails them) with
    what still has to be posted by hand: YouTube Community and X via Buffer."""
    parts = [f"@{owner} " if owner else "", "**Now post by hand** (Facebook and Instagram are done):\n"]
    nets = cfg.get("manual", ["youtube", "x"])
    if "youtube" in nets and post["texts"].get("youtube"):
        ch = cfg.get("youtube_channel_id", "")
        where = (f"[open the channel's Posts tab](https://www.youtube.com/channel/{ch}/posts), "
                 if ch else "")
        parts += [f"\n**YouTube Community**: {where}choose **Create post**, add the "
                  f"[square image]({post['image_url']}) and paste:\n",
                  "```text\n" + post["texts"]["youtube"] + "\n```\n"]
    if "x" in nets and post["texts"].get("x"):
        parts += ["\n**X via Buffer**: [open Buffer](https://publish.buffer.com), "
                  f"create a post for X, add the [square image]({post['image_url']}) and paste:\n",
                  "```text\n" + post["texts"]["x"] + "\n```\n"]
    return "".join(parts) if len(parts) > 2 else ""


def run_due(now: datetime, cfg: dict, queue: list[dict], published: dict,
            gh: GitHub, meta: Meta, ig_id: str, owner: str = "") -> None:
    """Publish every queued post whose slot has come. Mutates queue/published."""
    page_id = cfg["facebook_page_id"]
    for q in sorted([q for q in queue if datetime.fromisoformat(q["slot"]) <= now],
                    key=lambda q: q["slot"]):
        pid, issue = q["post_id"], q["issue"]
        post = parse_issue(gh.issue_body(issue)).get(pid)
        if not post or not post["checked"]:
            queue.remove(q)
            continue
        # the converted JPEG made at queue time, unless the issue now points elsewhere
        if q.get("image_url") and post["image_url"] == q.get("image_src"):
            post["image_url"] = q["image_url"]
        elif post["image_url"] and needs_conversion(post["image_url"]):
            try:
                post["image_url"] = localise_image(pid, post["image_url"])
            except Exception as exc:
                print(f"[publish] {pid}: image conversion failed: {exc}")
        rec = published.setdefault(pid, {"issue": issue, "title": post["title"], "attempts": {}})
        lines = []
        for net in cfg.get("networks", ["facebook", "instagram"]):
            if net in rec:  # already out
                continue
            tries = rec["attempts"].get(net, 0)
            if tries >= cfg.get("max_attempts", 3):
                continue
            rec["attempts"][net] = tries + 1
            try:
                if net == "facebook":
                    rec[net] = meta.facebook_photo(page_id, post["image_url"],
                                                   post["texts"]["facebook"])
                elif net == "instagram":
                    rec[net] = meta.instagram_photo(ig_id, post["image_url"],
                                                    post["texts"]["instagram"],
                                                    post["alt_text"])
                lines.append(f"- {net.title()}: {rec[net]}")
            except Exception as exc:
                rec.setdefault("errors", {})[net] = str(exc)[:300]
                left = cfg.get("max_attempts", 3) - rec["attempts"][net]
                lines.append(f"- ⚠️ {net.title()} failed: {str(exc)[:300]}"
                             + (f" (will retry in about 10 minutes, {left} left)" if left else " (giving up)"))
        pending = [n for n in cfg.get("networks", []) if n not in rec
                   and rec["attempts"].get(n, 0) < cfg.get("max_attempts", 3)]
        text = f"**{post['title']}**\n\n" + "\n".join(lines) if lines else ""
        if not pending:
            rec["done"] = now.isoformat()
            queue.remove(q)
            if any(n in rec for n in cfg.get("networks", [])):  # at least one went out
                steps = manual_steps(post, cfg, owner)
                if steps:
                    text += "\n\n" + steps
        if text:
            gh.comment(issue, text)


def _ig_id(cfg: dict, meta: Meta) -> str:
    if cfg.get("instagram_user_id"):
        return str(cfg["instagram_user_id"])
    ig = meta.page(cfg["facebook_page_id"]).get("instagram_business_account") or {}
    if not ig.get("id"):
        raise MetaError("the Page has no linked Instagram professional account")
    return ig["id"]


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    cfg = load_cfg()
    now = datetime.now(timezone.utc)
    queue = _load(QUEUE, [])
    published = _load(PUBLISHED, {})
    token = os.environ.get("META_PAGE_TOKEN", "").strip()

    gh = GitHub(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""))

    def sync(issue: int, body: str, warn: bool = True) -> None:
        queued, cancelled = enqueue(issue, body, now, cfg, queue, published, warn=warn)
        if queued or cancelled:
            text = ""
            if queued:
                text += "Queued for Facebook + Instagram:\n" + "\n".join(queued) + "\n\n"
            if cancelled:
                text += "\n".join(cancelled) + "\n\n"
            if not token:
                text += ("⚠️ The secret META_PAGE_TOKEN is not set yet, so nothing can be "
                         "published until it is (see README, Publishing).")
            gh.comment(issue, text.strip())
        print(f"[publish] issue #{issue}: queued {len(queued)}, cancelled {len(cancelled)}")

    if mode == "enqueue":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        sync(event["issue"]["number"], event["issue"].get("body") or "")
        _save(QUEUE, queue)
        return

    if mode == "run":
        # catch ticks whose issue-edit run was skipped (GitHub keeps only one
        # waiting run per concurrency group, so a quick series of edits can drop one)
        for i in gh.open_issues():
            sync(i["number"], i.get("body") or "", warn=False)
        _save(QUEUE, queue)

    if not token:
        due = [q for q in queue if datetime.fromisoformat(q["slot"]) <= now]
        print(f"[publish] META_PAGE_TOKEN not set; {len(due)} due post(s) waiting")
        if mode == "check":
            sys.exit("META_PAGE_TOKEN is empty")
        return
    meta = Meta(token, cfg.get("graph_version", "v26.0"))

    if mode == "check":
        page = meta.page(cfg["facebook_page_id"])
        ig = page.get("instagram_business_account") or {}
        print(f"OK: Facebook Page '{page.get('name')}', "
              f"Instagram @{ig.get('username', '(none linked)')}. Nothing was posted.")
        return

    due = [q for q in queue if datetime.fromisoformat(q["slot"]) <= now]
    if not due:
        print(f"[publish] nothing due ({len(queue)} queued)")
        return
    run_due(now, cfg, queue, published, gh, meta, _ig_id(cfg, meta),
            owner=os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    _save(QUEUE, queue)
    _save(PUBLISHED, published)


if __name__ == "__main__":
    main()
