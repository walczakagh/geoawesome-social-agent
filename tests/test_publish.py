from datetime import datetime, timezone

import pytest

from agent import publish, review

CFG = {"timezone": "Europe/Warsaw", "slots": ["16:00", "20:00"], "min_lead_minutes": 30,
       "facebook_page_id": "PAGE", "networks": ["facebook", "instagram"], "max_attempts": 3}


def issue_body(checked=(False, False), caption2="Second caption.\n\nRead more: https://x/2"):
    results = []
    for n, cap in ((1, "First caption.\n\nRead more: https://x/1"), (2, caption2)):
        results.append({
            "item": {"title": f"Post {n}", "link": f"https://x/{n}", "source_name": "NASA",
                     "licence": "reuse", "square_path": f"drafts/images/2026-10-18-{n}-post.jpg",
                     "image_note": "square crop"},
            "score": 8, "why": "", "problems": {},
            "draft": {"worth_posting": True, "caption": cap, "x": "x", "alt_text": f"Alt {n}."}})
    body = review.render(results, "2026-10-18")
    for n, c in enumerate(checked, 1):
        if c:
            marker = f"<!-- post:2026-10-18-{n}-post -->\n- [ ]"
            body = body.replace(marker, marker[:-3] + "[x]")
    return body


def test_render_and_parse_round_trip(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    posts = publish.parse_issue(issue_body((True, False)))
    assert list(posts) == ["2026-10-18-1-post", "2026-10-18-2-post"]
    p1 = posts["2026-10-18-1-post"]
    assert p1["checked"] and p1["title"] == "Post 1"
    assert p1["caption"] == "First caption.\n\nRead more: https://x/1"
    assert p1["image_url"].endswith("drafts/images/2026-10-18-1-post.jpg")
    assert p1["alt_text"] == "Alt 1."
    assert not posts["2026-10-18-2-post"]["checked"]


def test_next_slot_skips_taken_and_too_soon():
    now = datetime(2026, 10, 18, 13, 45, tzinfo=timezone.utc)   # 15:45 Warsaw (CEST)
    s1 = publish.next_slot(now, set(), CFG)                     # 16:00 is < 30 min away
    assert s1 == datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc)   # 20:00 Warsaw
    s2 = publish.next_slot(now, {s1.isoformat()}, CFG)
    assert s2 == datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)   # next day 16:00


def test_slots_follow_winter_time():
    now = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)     # after the switch to CET
    assert publish.next_slot(now, set(), CFG).hour == 15        # 16:00 Warsaw = 15:00 UTC


def test_enqueue_and_cancel():
    now = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)
    queue, pub = [], {}
    queued, cancelled = publish.enqueue(9, issue_body((True, True)), now, CFG, queue, pub)
    assert len(queue) == 2 and len(queued) == 2 and not cancelled
    assert queue[0]["slot"] != queue[1]["slot"]
    again, _ = publish.enqueue(9, issue_body((True, True)), now, CFG, queue, pub)
    assert again == [] and len(queue) == 2                       # no double queueing
    _, cancelled = publish.enqueue(9, issue_body((True, False)), now, CFG, queue, pub)
    assert [q["post_id"] for q in queue] == ["2026-10-18-1-post"] and len(cancelled) == 1


class FakeGH:
    def __init__(self, body):
        self.body, self.comments = body, []

    def issue_body(self, n):
        return self.body

    def comment(self, n, text):
        self.comments.append(text)


class FakeMeta:
    def __init__(self, fail_ig=0):
        self.calls, self.fail_ig = [], fail_ig

    def facebook_photo(self, page, url, cap):
        self.calls.append(("fb", cap))
        return "https://www.facebook.com/1_2"

    def instagram_photo(self, ig, url, cap, alt):
        self.calls.append(("ig", cap, alt))
        if self.fail_ig:
            self.fail_ig -= 1
            raise publish.MetaError("OAuthException 190: token expired")
        return "https://www.instagram.com/p/abc/"


def test_run_due_uses_edited_caption_and_retries_failed_network():
    now = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)
    queue, pub = [], {}
    publish.enqueue(9, issue_body((True, False)), now, CFG, queue, pub)
    later = datetime.fromisoformat(queue[0]["slot"])
    edited = issue_body((True, False)).replace("First caption.", "Edited caption.")
    gh, meta = FakeGH(edited), FakeMeta(fail_ig=1)
    publish.run_due(later, CFG, queue, pub, gh, meta, "IG")
    assert meta.calls[0] == ("fb", "Edited caption.\n\nRead more: https://x/1")
    assert "facebook" in pub["2026-10-18-1-post"] and "instagram" not in pub["2026-10-18-1-post"]
    assert len(queue) == 1 and "will retry" in gh.comments[0]
    publish.run_due(later, CFG, queue, pub, gh, meta, "IG")     # next hour
    assert [c[0] for c in meta.calls] == ["fb", "ig", "ig"]      # Facebook not posted twice
    assert pub["2026-10-18-1-post"]["done"] and queue == []


def test_unticked_before_slot_is_not_published():
    now = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)
    queue, pub = [], {}
    publish.enqueue(9, issue_body((True, False)), now, CFG, queue, pub)
    later = datetime.fromisoformat(queue[0]["slot"])
    meta = FakeMeta()
    publish.run_due(later, CFG, queue, pub, FakeGH(issue_body((False, False))), meta, "IG")
    assert meta.calls == [] and queue == []


class Resp:
    def __init__(self, data):
        self.data, self.status_code = data, 200

    def json(self):
        return self.data


class FakeHTTP:
    """Replays Graph API answers in order and records requests."""
    def __init__(self, answers):
        self.answers, self.seen = list(answers), []

    def get(self, url, params=None, timeout=None):
        self.seen.append(("GET", url, params))
        return Resp(self.answers.pop(0))

    def post(self, url, data=None, timeout=None):
        self.seen.append(("POST", url, data))
        return Resp(self.answers.pop(0))


def test_meta_instagram_flow_and_alt_text_fallback():
    http = FakeHTTP([
        {"error": {"type": "OAuthException", "code": 100, "message": "Invalid parameter alt_text"}},
        {"id": "C1"}, {"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"},
        {"id": "M1"}, {"permalink": "https://www.instagram.com/p/xyz/"}])
    m = publish.Meta("TOKEN", "v26.0", http=http, sleep=lambda s: None)
    assert m.instagram_photo("IG", "https://img", "cap", "alt") == "https://www.instagram.com/p/xyz/"
    assert "alt_text" in http.seen[0][2] and "alt_text" not in http.seen[1][2]
    assert http.seen[4][1].endswith("/IG/media_publish") and http.seen[4][2]["creation_id"] == "C1"


def test_meta_error_is_raised():
    http = FakeHTTP([{"error": {"type": "OAuthException", "code": 190, "message": "expired"}}])
    with pytest.raises(publish.MetaError, match="190"):
        publish.Meta("T", "v26.0", http=http).facebook_photo("P", "u", "c")


def test_failed_post_is_not_requeued_every_hour():
    now = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)
    queue, pub = [], {"2026-10-18-1-post": {"attempts": {"instagram": 3}, "facebook": "u"}}
    queued, _ = publish.enqueue(9, issue_body((True, False)), now, CFG, queue, pub)
    assert queued == [] and queue == []
