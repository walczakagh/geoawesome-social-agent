import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from agent import checks, enrich, images, llm, review, scout

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
RULES = checks.load_rules(ROOT / "config/style_rules.yaml")
LINK = "https://www.esa.int/ESA_Multimedia/Images/2026/10/Earth_from_Space_Namibia_s_biggest_salt_pan"
ESA = {"id": "esa_eo", "name": "ESA Observing the Earth", "type": "eo_imagery",
       "licence": "reuse", "credit_hint": "ESA", "tag": "@esa"}
CREDIT = "Image: contains modified Copernicus Sentinel data (2026), processed by ESA"


def esa_items():
    return scout.parse_feed(ESA, (FIX / "esa_feed.xml").read_text(encoding="utf-8"))


def blank_item(**kw):
    base = dict(id="n", source_id="nasa_eo", source_name="NASA EO", source_type="eo_imagery",
                licence="reuse", title="Floodwaters", link="https://science.nasa.gov/x",
                published="", summary="")
    base.update(kw)
    return scout.Item(**base)


GOOD = {
    "worth_posting": True,
    "what_it_shows": "Sentinel-2 false colour, 5 April 2026, Etosha Pan.",
    "uncertainties": [],
    "image_credit": CREDIT[7:],
    "caption": "A salt pan 130 km wide, and the red is grass.\n\n"
               "This false-colour Sentinel-2 view of Etosha in Namibia puts near-infrared in the red "
               "channel, so dense vegetation glows. Acquired 5 April 2026.\n\n"
               f"Read more: {LINK}\n\n{CREDIT}\n\n#Sentinel2 #Namibia #Copernicus #Geoawesome",
    "x": f"Etosha Pan, 4760 sq km of salt, in Sentinel-2 false colour.\n{LINK}\n"
         "Image: ESA, Copernicus Sentinel data\n#Sentinel2 #Namibia #Geoawesome",
    "alt_text": "A white salt flat surrounded by red vegetation in a false-colour satellite image.",
}


def test_parse_esa_feed():
    items = esa_items()
    assert len(items) == 2
    assert items[0].link == LINK
    assert items[0].published.startswith("2026-10-09")


def test_scout_filters_seen_and_old(monkeypatch):
    parsed = esa_items()
    monkeypatch.setattr(scout, "parse_feed", lambda src: list(parsed))
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    assert [i.link for i in scout.scout([ESA], set(), 7, now)] == [LINK]
    assert scout.scout([ESA], {LINK}, 7, now) == []


def test_scout_reports_empty_feed(monkeypatch):
    def boom(src):
        raise RuntimeError("https://x: HTTP 200 but no feed entries (content-type text/html)")
    monkeypatch.setattr(scout, "parse_feed", boom)
    report = {}
    scout.scout([ESA], set(), report=report)
    assert "no feed entries" in report["ESA Observing the Earth"]


def test_enrich_esa_credit_licence_and_hires():
    it = enrich.extract(esa_items()[0], (FIX / "esa_page.html").read_text(encoding="utf-8"))
    assert it.image_credit == "contains modified Copernicus Sentinel data (2026), processed by ESA"
    assert it.image_licence.startswith("CC BY-SA 3.0 IGO")
    assert it.images == [{"url": "https://www.esa.int/var/esa/storage/images/x/27564542-1-eng-GB/Etosha_hires.jpg",
                          "label": ""}]


def test_enrich_nasa_pair_and_third_party():
    it = enrich.extract(blank_item(), (FIX / "nasa_page.html").read_text(encoding="utf-8"))
    assert it.image_credit.startswith("NASA Earth Observatory images by Lauren Dauphin")
    assert any("Lacy Cotter-Vardeman" in w for w in it.extra_images)
    assert [i["label"] for i in it.images] == ["October 7, 2025", "October 2, 2026"]
    assert it.images[1]["url"].endswith("thailandflooding_oli2_20261002.jpg")  # query stripped


def test_enrich_maps_mania_origin():
    it = blank_item(source_id="maps_mania", source_type="maps", licence="link_only",
                    link="http://googlemapsmania.blogspot.com/2026/10/providing-global-relief.html")
    it = enrich.extract(it, (FIX / "mapsmania_page.html").read_text(encoding="utf-8"))
    assert it.origin_url.startswith("https://relief.bertspaan.nl")
    assert it.images[0]["url"].endswith("relief.png")


def _noise(w, h, seed=0):
    return Image.fromarray(np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8))


def test_square_single_and_pair(tmp_path):
    it = blank_item(images=[{"url": "a", "label": ""}])
    images.build(it, tmp_path / "one.jpg", fetch=lambda u: _noise(2000, 1200))
    assert Image.open(it.square_path).size == (1080, 1080)
    assert "square crop" in it.image_note and "low resolution" not in it.image_note

    it = blank_item(images=[{"url": "a", "label": "2025"}, {"url": "b", "label": "2026"}])
    images.build(it, tmp_path / "pair.jpg", fetch=lambda u: _noise(1440, 1000))
    assert Image.open(it.square_path).size == (1080, 1080)
    assert "before/after" in it.image_note


def test_square_flags_low_res_and_failures(tmp_path):
    it = blank_item(images=[{"url": "a", "label": ""}])
    images.build(it, tmp_path / "s.jpg", fetch=lambda u: _noise(500, 400))
    assert "low resolution" in it.image_note

    def fail(u):
        raise IOError("403")
    it = blank_item(images=[{"url": "a", "label": ""}])
    images.build(it, tmp_path / "f.jpg", fetch=fail)
    assert it.square_path == "" and "download failed" in it.image_note


def test_pair_halves_share_one_window():
    a = np.full((1000, 2400, 3), 120, np.uint8)
    a[:, 1500:2300] = np.random.default_rng(1).integers(0, 255, (1000, 800, 3), dtype=np.uint8)
    x0, _, x1, _ = images.crop_box(Image.fromarray(a), 2.0)
    assert x0 * 2400 <= 1500 and x1 * 2400 >= 2300


def test_good_draft_passes():
    assert checks.check_draft(GOOD, RULES, LINK) == {}


@pytest.mark.parametrize("bad, expected", [
    ("Did you know salt pans glow?", "opens with"),
    ("Satellites unlock new views of Etosha", 'banned phrase "unlock"'),
    ("Etosha — a salt pan", "em dash"),
    ("We explore the Etosha Pan", 'banned phrase "explore"'),
])
def test_rule_violations(bad, expected):
    d = dict(GOOD, caption=bad + "\n" + GOOD["caption"])
    assert any(expected in p for p in checks.check_draft(d, RULES, LINK).get("caption", []))


def test_hashtag_credit_and_link_rules():
    d = dict(GOOD)
    d["caption"] = GOOD["caption"].replace("#Copernicus", "#RemoteSensing").replace(CREDIT + "\n\n", "")
    d["x"] = GOOD["x"].replace(" #Namibia", "")
    p = checks.check_draft(d, RULES, LINK)
    assert any("#RemoteSensing is not a single word" in x for x in p["caption"])
    assert any("no credit line" in x for x in p["caption"])
    assert any("needs 3-4" in x for x in p["x"])
    d = dict(GOOD, x=GOOD["x"].replace(f"\n{LINK}", f" {LINK}"))
    assert any("own line" in x for x in checks.check_draft(d, RULES, LINK)["x"])


def test_x_length_counts_links_as_23():
    assert checks.x_length("a " + LINK) == 2 + 23


def test_draft_repair_loop_and_digest(monkeypatch):
    calls = []
    bad = dict(GOOD, caption=GOOD["caption"].replace("glows.", "glows — brilliantly."))

    def fake_ask(model, system, messages, max_tokens=4000):
        calls.append(messages)
        return json.dumps(bad if len(calls) == 1 else GOOD)

    it = enrich.extract(esa_items()[0], (FIX / "esa_page.html").read_text(encoding="utf-8"))
    it.square_path = "drafts/images/2026-10-10-1-etosha.jpg"
    it.image_note = "square crop, from 4000px of source width"
    d, probs = llm.draft(it, RULES, checks.check_draft, ask=fake_ask)
    assert len(calls) == 2 and "em dash" in calls[1][-1]["content"]
    assert "IMAGE NOTE: square crop" in calls[0][0]["content"]
    assert probs == {}

    monkeypatch.setenv("GITHUB_REPOSITORY", "walczakagh/geoawesome-social-agent")
    md = review.render([{"item": it.to_dict(), "score": 8, "why": "strong image",
                         "draft": d, "problems": probs}], "2026-10-10", {"ESA": "15 in feed, 4 new"})
    assert "## 1. Earth from Space" in md
    assert "blob/main/drafts/images/2026-10-10-1-etosha.jpg?raw=true" in md
    assert "**X**" in md and "Sources checked" in md


def test_shortlist_ignores_unknown_ids():
    items = esa_items()
    reply = json.dumps({"picks": [{"id": items[0].id, "score": 8, "why": "x"},
                                  {"id": "made-up", "score": 9, "why": "y"}]})
    assert [p["id"] for p in llm.shortlist(items, 4, ask=lambda *a, **k: reply)] == [items[0].id]


def test_parse_json_tolerates_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
