import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent import checks, enrich, llm, review, scout

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
RULES = checks.load_rules(ROOT / "config/style_rules.yaml")
LINK = "https://www.esa.int/ESA_Multimedia/Images/2026/10/Earth_from_Space_Namibia_s_biggest_salt_pan"
ESA = {"id": "esa_eo", "name": "ESA Observing the Earth", "type": "eo_imagery",
       "licence": "reuse", "credit_hint": "ESA", "tag": "@esa"}


def esa_items():
    return scout.parse_feed(ESA, (FIX / "esa_feed.xml").read_text(encoding="utf-8"))


GOOD = {
    "worth_posting": True,
    "what_it_shows": "Sentinel-2 false colour, 5 April 2026, Etosha Pan.",
    "uncertainties": [],
    "image_ok": True,
    "image_credit": "contains modified Copernicus Sentinel data (2026), processed by ESA",
    "posts": {
        "facebook": "A salt pan 130 km wide, and the red is grass.\n"
                    "This #Sentinel2 false-colour image of Etosha in Namibia puts near-infrared in the red channel, so dense vegetation glows. Acquired 5 April 2026 by @esa.\n"
                    f"Read more: {LINK}\nImage: contains modified Copernicus Sentinel data (2026), processed by ESA\n"
                    "#Geoawesome #Namibia #Copernicus #Salt",
        "instagram": "Etosha Pan covers 4760 sq km of salt.\nRed means vegetation in this #Sentinel2 false-colour view.\n"
                     f"Read more: {LINK}\nImage: contains modified Copernicus Sentinel data (2026), processed by ESA\n"
                     "#Geoawesome #Namibia #Copernicus #Salt",
        "x": f"Etosha Pan, 4760 sq km of salt, seen by #Sentinel2 in false colour.\n{LINK}\n"
             "Image: contains modified Copernicus Sentinel data (2026), processed by ESA\n#Geoawesome #Namibia",
        "youtube": "The red in this image is near-infrared reflectance.\n#Sentinel2 records 13 spectral bands.\n"
                   f"Read more: {LINK}\nImage: contains modified Copernicus Sentinel data (2026), processed by ESA\n"
                   "#Geoawesome #Namibia #Copernicus #Salt",
        "linkedin": "130 km of salt flat, mapped in near-infrared.\nThe #Sentinel2 scene from 5 April 2026 separates water turbidity and vegetation density.\n"
                    f"Full story: {LINK}\nImage: contains modified Copernicus Sentinel data (2026), processed by ESA\n"
                    "#Geoawesome #Namibia #Copernicus #Salt",
    },
}


def test_parse_esa_feed():
    items = esa_items()
    assert len(items) == 2
    assert items[0].link == LINK
    assert items[0].image_url.endswith("_card_full.jpg")
    assert items[0].published.startswith("2026-10-09")


def test_scout_filters_seen_and_old(monkeypatch):
    parsed = esa_items()
    monkeypatch.setattr(scout, "parse_feed", lambda src: list(parsed))
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    fresh = scout.scout([ESA], seen=set(), max_age_days=7, now=now)
    assert [i.link for i in fresh] == [LINK]  # September briefing is too old
    assert scout.scout([ESA], seen={LINK}, max_age_days=7, now=now) == []


def test_enrich_esa_credit_and_licence():
    it = enrich.extract(esa_items()[0], (FIX / "esa_page.html").read_text(encoding="utf-8"))
    assert it.image_credit == "contains modified Copernicus Sentinel data (2026), processed by ESA"
    assert it.image_licence.startswith("CC BY-SA 3.0 IGO")
    assert "4760 sq km" in it.page_text


def test_enrich_nasa_separates_third_party_photos():
    it = scout.Item(id="n", source_id="nasa_eo", source_name="NASA EO", source_type="eo_imagery",
                    licence="reuse", title="Floodwaters", link="https://science.nasa.gov/x",
                    published="", summary="")
    it = enrich.extract(it, (FIX / "nasa_page.html").read_text(encoding="utf-8"))
    assert it.image_credit.startswith("NASA Earth Observatory images by Lauren Dauphin")
    assert any("Lacy Cotter-Vardeman" in w for w in it.extra_images)


def test_good_draft_passes():
    assert checks.check_draft(GOOD, RULES, LINK) == {}


@pytest.mark.parametrize("bad, expected", [
    ("Did you know salt pans glow? Read more", "opens with"),
    ("Satellites unlock new views of Etosha", 'banned phrase "unlock"'),
    ("Etosha — a salt pan", "em dash"),
    ("We explore the Etosha Pan", 'banned phrase "explore"'),
])
def test_rule_violations(bad, expected):
    d = json.loads(json.dumps(GOOD))
    d["posts"]["facebook"] = bad + "\n" + d["posts"]["facebook"]
    probs = checks.check_draft(d, RULES, LINK)
    assert any(expected in p for p in probs.get("facebook", [])), probs


def test_hashtag_rules():
    d = json.loads(json.dumps(GOOD))
    d["posts"]["instagram"] = d["posts"]["instagram"].replace("#Salt", "#RemoteSensing")
    d["posts"]["x"] = d["posts"]["x"].replace("#Geoawesome ", "")
    probs = checks.check_draft(d, RULES, LINK)
    assert any("#RemoteSensing is not a single word" in p for p in probs["instagram"])
    assert any("missing #Geoawesome" in p for p in probs["x"])


def test_link_only_must_not_carry_image_credit():
    d = json.loads(json.dumps(GOOD))
    d["image_ok"] = False
    probs = checks.check_draft(d, RULES, LINK)
    assert all(any("may not be reused" in p for p in v) for v in probs.values())


def test_x_length_counts_links_as_23():
    assert checks.x_length("a " + LINK) == 2 + 23


def test_draft_repair_loop_and_digest():
    calls = []
    bad = json.loads(json.dumps(GOOD))
    bad["posts"]["facebook"] = bad["posts"]["facebook"].replace("glows", "glows — brilliantly")

    def fake_ask(model, system, messages, max_tokens=4000):
        calls.append(messages)
        return json.dumps(bad if len(calls) == 1 else GOOD)

    it = enrich.extract(esa_items()[0], (FIX / "esa_page.html").read_text(encoding="utf-8"))
    d, probs = llm.draft(it, RULES, checks.check_draft, ask=fake_ask)
    assert len(calls) == 2 and "em dash" in calls[1][-1]["content"]
    assert probs == {}

    md = review.render([{"item": it.to_dict(), "score": 8, "why": "strong image",
                         "draft": d, "problems": probs}], "2026-10-10")
    assert "## 1. Earth from Space" in md and "CC BY-SA 3.0 IGO" in md


def test_shortlist_ignores_unknown_ids():
    items = esa_items()
    reply = json.dumps({"picks": [{"id": items[0].id, "score": 8, "why": "x"},
                                  {"id": "made-up", "score": 9, "why": "y"}]})
    picks = llm.shortlist(items, 4, ask=lambda *a, **k: reply)
    assert [p["id"] for p in picks] == [items[0].id]


def test_parse_json_tolerates_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
