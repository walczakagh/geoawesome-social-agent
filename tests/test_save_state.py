import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "save_state", Path(__file__).resolve().parent.parent / "tools/save_state.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def q(*ids):
    return [{"post_id": i, "slot": f"2026-10-2{n}T00:00:00+00:00"} for n, i in enumerate(ids)]


def test_queue_keeps_both_runs_additions():
    # 26 Oct 2026: two boxes ticked seconds apart, both runs started from the same queue
    base = q("a")
    ours = q("a", "glacier")
    theirs = q("a", "peru")
    assert {x["post_id"] for x in ss.merge_queue(base, ours, theirs)} == {"a", "peru", "glacier"}


def test_queue_respects_removal_and_does_not_duplicate():
    base = q("a", "b")
    ours = q("b")                      # we published or cancelled "a"
    theirs = q("a", "b", "c")
    assert [x["post_id"] for x in ss.merge_queue(base, ours, theirs)] == ["b", "c"]
    # both runs queued the same post: keep origin's entry (its time was announced first)
    theirs2 = [{"post_id": "p", "slot": "T1"}]
    ours2 = [{"post_id": "p", "slot": "T2"}]
    assert ss.merge_queue([], ours2, theirs2) == theirs2


def test_published_never_forgets_a_post():
    base = {}
    ours = {"x": {"facebook": "fb/1", "attempts": {"facebook": 1}}}
    theirs = {"x": {"instagram": "ig/1", "attempts": {"instagram": 1}},
              "y": {"facebook": "fb/2", "attempts": {"facebook": 1}}}
    m = ss.merge_published(base, ours, theirs)
    assert m["x"]["facebook"] == "fb/1" and m["x"]["instagram"] == "ig/1"
    assert m["x"]["attempts"] == {"facebook": 1, "instagram": 1}
    assert m["y"]["facebook"] == "fb/2"


def test_seen_is_a_union():
    assert ss.merge_seen({}, {"a": "2026-10-01"}, {"b": "2026-10-02"}) == {
        "a": "2026-10-01", "b": "2026-10-02"}
