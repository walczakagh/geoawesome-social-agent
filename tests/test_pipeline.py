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


# ---- sourcing (15 Oct 2026) -------------------------------------------------

NASA_BROKEN = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
\t xmlns:content="http://purl.org/rss/1.0/modules/content/"
\t xmlns:apod="https://science.nasa.gov/apod/"xmlns:media="http://search.yahoo.com/mrss/" >
<channel><title>NASA</title>
<item><title>Fires Arrive in the Amazon Rainforest</title>
<link>https://science.nasa.gov/earth/earth-observatory/fires-arrive-in-the-amazon-rainforest/</link>
<pubDate>Thu, 15 Oct 2026 04:01:00 +0000</pubDate></item>
</channel></rss>"""


def test_nasa_feed_with_run_together_namespaces_parses():
    src = {"id": "nasa_eo", "name": "NASA EO", "type": "eo_imagery"}
    items = scout.parse_feed(src, NASA_BROKEN)
    assert [i.title for i in items] == ["Fires Arrive in the Amazon Rainforest"]


def test_unwrap_google_alerts_and_bing_links():
    g = ("https://www.google.com/url?rct=j&sa=t&url=https://www.esri.com/arcgis-blog/products/"
         "x/%3Futm_source%3Dalert&ct=ga&cd=CAE&usg=AOv")
    assert scout.unwrap_link(g) == "https://www.esri.com/arcgis-blog/products/x/"
    b = ("http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&url=https%3a%2f%2fwww.msn.com"
         "%2fen-us%2fweather%2fx%2far-AA2&c=1&mkt=en-us")
    assert scout.unwrap_link(b) == "https://www.msn.com/en-us/weather/x/ar-AA2"
    m = "https://googlemapsmania.blogspot.com/2026/08/fall-foliage.html#google_vignette"
    assert scout.unwrap_link(m) == "https://googlemapsmania.blogspot.com/2026/08/fall-foliage.html"


def test_link_key_matches_http_https_and_trailing_slash():
    assert (scout.link_key("http://www.esa.int/A/B/") ==
            scout.link_key("https://esa.int/A/B?utm_source=x"))


def test_alerts_sources_come_from_env(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text("sources:\n  - {id: ga, name: Google Alerts, urls_env: GA, type: alerts}\n"
                   "  - {id: esa, name: ESA, url: https://esa.int/rss}\n")
    two = scout.load_sources(cfg, {"GA": "https://g.co/a1\nhttps://g.co/a2"})
    assert [s["id"] for s in two] == ["ga_1", "ga_2", "esa"]
    assert two[1]["url"] == "https://g.co/a2"
    none = scout.load_sources(cfg, {})
    report = {}
    scout.scout([none[0]], set(), report=report)
    assert report["Google Alerts"].startswith("not configured")


def test_scout_dedupes_excludes_and_caps(monkeypatch):
    def items_for(src):
        mk = lambda link, day: blank_item(id=link, link=link, source_id=src["id"],
                                          source_name=src["name"],
                                          published=f"2026-10-{day:02d}T08:00:00+00:00")
        if src["id"] == "a":
            return [mk("https://esa.int/x", 9), mk("https://finance.yahoo.com/y", 9),
                    mk("https://esa.int/z", 8), mk("https://esa.int/w", 7)]
        return [mk("http://www.esa.int/x/", 9), mk("https://nasa.gov/q", 9)]
    monkeypatch.setattr(scout, "parse_feed", items_for)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    report = {}
    got = scout.scout([{"id": "a", "name": "A", "max_new": 2}, {"id": "b", "name": "B"}],
                      set(), 7, now, report, exclude_domains=["finance.yahoo.com"])
    assert [i.link for i in got] == ["https://esa.int/x", "https://esa.int/z", "https://nasa.gov/q"]
    assert "1 from excluded sites" in report["A"]
    assert "1 already found via another source" in report["B"]


def test_history_note_uses_parent_domain():
    from agent import history
    sites = {"esri.com": {"picked": 78, "seen": 219}}
    assert history.note("https://www.esri.com/arcgis-blog/x", sites) == \
        "picked 78 of 219 alerts from esri.com"
    assert history.note("https://storymaps.esri.com/x", sites).startswith("picked 78")
    assert history.note("https://example.org/x", sites) == ""


def test_shortlist_batches_and_keeps_best(monkeypatch):
    monkeypatch.setattr(llm, "BATCH", 2)
    its = [blank_item(id=f"i{n}", link=f"https://esa.int/{n}") for n in range(5)]
    calls = []

    def fake_ask(model, system, messages, max_tokens):
        calls.append(messages[0]["content"])
        ids = [l[4:] for l in messages[0]["content"].splitlines() if l.startswith("id: ")]
        return json.dumps({"picks": [{"id": i, "score": int(i[1:]) + 4, "why": ""} for i in ids]
                           + [{"id": "made-up", "score": 10}]})
    picks = llm.shortlist(its, limit=2, ask=fake_ask,
                          history={"esa.int": {"picked": 36, "seen": 95}})
    assert len(calls) == 3
    assert "editor history: picked 36 of 95" in calls[0]
    assert [p["id"] for p in picks] == ["i4", "i3"]   # i0, i1 below 6; made-up dropped


def test_shortlist_survives_a_broken_reply(monkeypatch):
    monkeypatch.setattr(llm, "BATCH", 2)
    its = [blank_item(id=f"i{n}", link=f"https://esa.int/{n}") for n in range(4)]
    replies = iter(['{"picks": [{"id": "i0", "score": 9, "why": "cut off',   # batch 1 try 1
                    '{"picks": [{"id": "i0", "score": 9, "why": "ok"}]}',    # batch 1 retry
                    '{"picks": [{"id": "i2", "score": 7, "wh',              # batch 2 try 1
                    'not json'])                                             # batch 2 retry
    picks = llm.shortlist(its, limit=6, ask=lambda *a, **k: next(replies))
    assert [p["id"] for p in picks] == ["i0"]


def test_shortlist_raises_when_every_batch_fails():
    its = [blank_item(id="i0")]
    with pytest.raises(RuntimeError):
        llm.shortlist(its, limit=6, ask=lambda *a, **k: "{\"picks\": [")


def test_ask_picks_returns_tool_input(monkeypatch):
    class B:  # minimal stand-ins for the SDK objects
        type = "tool_use"
        input = {"picks": [{"id": "a", "score": 8, "why": "x"}]}

    class R:
        content = [B()]
        stop_reason = "tool_use"

    class C:
        class messages:
            @staticmethod
            def create(**kw):
                assert kw["tool_choice"] == {"type": "tool", "name": "submit_picks"}
                return R()
    monkeypatch.setattr(llm, "_client", lambda: C())
    assert json.loads(llm._ask_picks("m", "s", [])) == B.input


# ---- image choice (17 Oct 2026: locator maps, other stories, Reuters) -------

NASA_SPIDER = """<html><head>
<meta property="og:image" content="https://assets.science.nasa.gov/x/og.jpg"></head><body>
<figure><img src="https://assets.science.nasa.gov/content/dam/science/esd/eo/images/iotd/2026/an-impact-structure-with-legs/locatormap.png/jcr:content/renditions/cq5dam.thumbnail.319.319.png"></figure>
<figure><img src="https://assets.science.nasa.gov/content/dam/science/esd/eo/images/iotd/2026/antarctic-sea-ice-grows-to-its-2026-maximum/antarctic_nsidc_20260914_th.jpg/jcr:content/renditions/cq5dam.thumbnail.319.319.png"></figure>
<figure><img src="https://assets.science.nasa.gov/dynamicimage/assets/science/esd/eo/images/iotd/2026/antarctic-sea-ice-grows-to-its-2026-maximum/antarctic_big.jpg"></figure>
<p>NASA Earth Observatory image by Michala Garrison, using Landsat data from the U.S. Geological Survey.</p>
</body></html>"""


def test_nasa_uses_feed_lead_image_not_thumbnails_or_other_stories():
    lead = ("https://assets.science.nasa.gov/dynamicimage/assets/science/esd/eo/images/iotd/2026/"
            "an-impact-structure-with-legs/aus_spidercrater_oli2_20260920.jpg?w=1900&h=1267&fit=clip")
    it = blank_item(link="https://science.nasa.gov/earth/earth-observatory/an-impact-structure-with-legs/",
                    image_url=lead)
    it = enrich.extract(it, NASA_SPIDER)
    assert it.images == [{"url": lead.split("?")[0], "label": ""}]
    urls = [c["url"] for c in it.alt_images]
    assert not any("thumbnail" in u or "antarctic" in u for u in urls)


def test_news_agency_photo_is_never_picked():
    page = ('<html><head><meta property="og:image" content="https://www.bellingcat.com/app/uploads/'
            '2026/10/2026-10-14T192750Z_1002783143_RC263OALNP1F_RTRMADP_3_USA-TRUMP-GOLF-scaled.jpg">'
            '</head><body><p>' + "x" * 50 + '</p></body></html>')
    shot = "https://www.bellingcat.com/app/uploads/2026/10/Screenshot-2026-10-15-at-10.59.57-1200x777.jpg"
    it = blank_item(source_id="bellingcat", licence="link_only",
                    link="https://www.bellingcat.com/news/2026/10/15/golf/", image_url=shot)
    it = enrich.extract(it, page)
    assert it.images[0]["url"] == shot
    assert all("RTRMADP" not in c["url"] for c in it.alt_images)


def test_small_pick_falls_back_to_bigger_candidate(tmp_path):
    it = blank_item(images=[{"url": "small", "label": ""}],
                    alt_images=[{"url": "big", "label": ""}])
    sizes = {"small": (150, 150), "big": (2000, 1300)}
    images.build(it, tmp_path / "s.jpg", fetch=lambda u: _noise(*sizes[u]))
    assert it.images[0]["url"] == "big" and "low resolution" not in it.image_note


def test_private_alert_url_never_reported(monkeypatch):
    secret = "https://www.google.com/alerts/feeds/123/456"
    def boom(url):
        raise RuntimeError(f"HTTP 404 for {url}")
    monkeypatch.setattr(scout, "_fetch", boom)
    src = {"id": "ga_1", "name": "Google Alerts #1", "url": secret, "urls_env": "GA"}
    report = {}
    scout.scout([src], set(), report=report)
    assert secret not in report["Google Alerts #1"] and "private feed URL" in report["Google Alerts #1"]


def test_empty_alert_feed_is_not_a_failure(monkeypatch):
    def empty(url):
        raise scout.FeedEmpty("HTTP 200 but no feed entries")
    monkeypatch.setattr(scout, "_fetch", empty)
    src = {"id": "ga_5", "name": "Google Alerts #5", "url": "https://g/x", "urls_env": "GA",
           "type": "alerts"}
    report = {}
    scout.scout([src], set(), report=report)
    assert report["Google Alerts #5"] == "0 in feed, 0 new"
