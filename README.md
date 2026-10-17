# Geoawesome social agent

Every morning this repo checks map and Earth observation feeds plus your Google Alerts, picks the items with the strongest visuals, and prepares ready-to-post reposts for Facebook, Instagram, X and YouTube Community. Each repost is:

- a **1080×1080 JPEG** made from the source image,
- a **short caption** (Facebook, Instagram, YouTube Community): what you are looking at and why it is interesting, a link to the source article, the image credit, and 3 to 5 single-word hashtags including #Geoawesome,
- an **X version** that fits 280 characters, and a one-line alt text.

Everything lands in a GitHub issue for review. Nothing is posted automatically.

## How a run works

1. **Scout** (`agent/scout.py`): reads the feeds in `config/sources.yaml` and the Google Alerts feeds in the `GOOGLE_ALERTS_FEEDS` secret, and keeps items from the last 7 days that have not been seen before (`state/seen.json`). Redirect links (Google Alerts, Bing) are unwrapped to the real article, the same article arriving from two feeds is kept once, sites in `exclude_domains` are dropped, and `max_new` caps busy feeds.
2. **Shortlist** (`agent/llm.py`, Claude Haiku, `prompts/shortlist_system.md`): scores every new item (in batches of 40) on whether the visual would stop a geodata reader scrolling: satellite scenes, interactive maps and portals, story maps, geo data-vis. Each item carries its site's record from your alerts sheet (`config/editor_history.yaml`, e.g. "picked 24 of 49"). Keeps up to 6 scoring 6/10 or more.
3. **Enrich** (`agent/enrich.py`): opens each page and takes the caption text, the credit and licence lines, and the best image: ESA's HI-RES JPG, NASA's before/after pair (with its dates) or lead image, otherwise the page's preview image. For Maps Mania posts it also records where the featured map lives, so its maker can be credited.
4. **Square image** (`agent/images.py`): downloads the image and crops it to 1:1 around the most detailed part of the scene, then saves it at 1080×1080. A before/after pair becomes one square with the earlier date on top and the later below, both cropped to the same ground area and labelled with their dates. Small sources are flagged as low resolution.
5. **Caption** (Claude Opus, `prompts/draft_system.md`): writes the caption, the X version, alt text, a "what it shows" note for you and a list of things to check. It may only use facts from the page.
6. **Check** (`agent/checks.py`, rules in `config/style_rules.yaml`): em dashes, banned words and openers, CTA line with the link, credit line, 3 to 5 hashtags (3 to 4 on X) including #Geoawesome, single-word hashtags, X length (links count as 23). Failures go back to Claude once; anything still broken is marked in the issue.
7. **Review**: images go to `drafts/images/`, the digest to `drafts/YYYY-MM-DD.md` (plus `.json`), and the digest is opened as an issue titled "Geoawesome reposts YYYY-MM-DD". No issue on days with nothing worth posting. The issue shows each square image with a download link, the caption and X text ready to copy, and at the end which sources answered.

## Publishing to Facebook and Instagram

Each post in a daily issue has a box **Publish on Facebook + Instagram**. Nothing is posted unless you tick it.

1. Open the day's issue. To change a caption first: **...** menu on the issue text, **Edit**, change the text inside the caption box, **Update comment**.
2. Tick the box under each post you want. Within a minute a comment confirms the time slot, e.g. "Post title: Sun 18 Oct, 16:00".
3. Every hour `.github/workflows/publish.yml` publishes posts whose slot has come and comments with links to the live posts. It reads the issue again just before posting, so caption edits made up to then are used, and a box unticked in the meantime cancels the post.

Slots are in `config/publish.yaml` (default 16:00 and 20:00 Warsaw time, at most two posts a day; each goes out a few minutes after the hour). A failed network is retried on the next two hourly runs. YouTube Community and X are not covered. Only the repository owner's ticks count. `state/queue.json` holds queued posts, `state/published.json` what went out.

The workflow posts with a Facebook Page access token in the secret `META_PAGE_TOKEN`. Test it without posting: **Actions → Publish → Run workflow → mode: check**. Switch publishing off: delete the `META_PAGE_TOKEN` secret.

## Google Alerts as a source

Your alerts are the widest net (about 200 links a month, of which you highlighted about 45). To feed them in:

1. In Google Alerts, for each alert: pencil icon, **Show options**, **Deliver to: RSS feed**, **Update alert**. An RSS icon appears next to the alert; copy its link.
2. Add a repository secret named `GOOGLE_ALERTS_FEEDS` with all those links, one per line.

The links stay out of the public repo. Until the secret is set, the issue's "Sources checked" says "Google Alerts: not configured". Note that switching an alert to RSS stops its emails, so if you still use the emails to fill the alerts sheet, create a second copy of each alert for RSS.

`config/editor_history.yaml` was built from the alerts sheet on 15 Oct 2026. Regenerate it after a few months of new highlights so the shortlist keeps learning from what you pick.

## Checking sources without spending API credits

```bash
python -m agent.main --scout-only
```

lists what today's run would hand to the shortlist and how each feed answered. The sandbox Claude works in cannot reach most feeds, so `.github/workflows/probe-sources.yml` runs the same check on GitHub: push any branch named `probe/...`, and the results (`tools/probe_results/`) are committed back to that branch.

## Image rights

Each source has a reuse policy in `config/sources.yaml`:

- `reuse` (NASA Earth Observatory, ESA): the publisher allows reuse with credit. ESA images are usually CC BY-SA 3.0 IGO, so keep the credit line. Check the licence shown in the issue: a few ESA images are under the ESA Standard Licence only.
- `link_only` (Maps Mania, Google Alerts, EUMETSAT, Planet, Visual Capitalist, FlowingData and the other newer sources): the image belongs to someone else. The caption credits the map's maker or site and Maps Mania; ask the maker first if you are unsure they are happy with a repost.

## Setup

1. Create an empty private repo (for example `walczakagh/geoawesome-social-agent`) and push this folder to it.
2. Add the repository secret `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions). If the key is an organisation-level key rather than one scoped to a workspace, also add `ANTHROPIC_WORKSPACE_ID` (the `wrkspc_...` ID from the Workspaces page in the Claude Console).
3. Settings → Actions → General → Workflow permissions: "Read and write permissions" (the run commits `state/` and `drafts/` and opens issues).
4. Actions → Social drafts → Run workflow, with `dry_run` ticked for the first run. Check the issue, then run once without `dry_run` or wait for the 06:47 UTC schedule.

Run locally:

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python -m agent.main --max-items 2 --dry-run
pytest -q
```

## Sending a draft to Metricool

Metricool's REST API is only on its Advanced and Custom plans, so the repo does not post anywhere. In a Claude chat with the Metricool connector, say which item you want, for example "send item 2 from today's reposts to Metricool", and it goes into Metricool's review queue with the square image attached, where you approve or edit it. The image is attached from its raw GitHub link, which only works while the repo is public; with a private repo, download the square image from the issue and add it in Metricool yourself.

## Changing things

- **Add a source**: append to `config/sources.yaml` with `licence: reuse` only if the publisher's terms allow reuse with credit; otherwise `link_only`.
- **Change the caption format**: `prompts/draft_system.md`. Keep `config/style_rules.yaml` in step, since the checker enforces it.
- **Models**: set `SHORTLIST_MODEL` / `DRAFT_MODEL` environment variables (defaults `claude-haiku-5-5`, `claude-opus-5-5`).
- **Volume**: workflow input `max_items` (default 6).

## Known limits

- The feed URLs and page structures were checked by hand on 10 October 2026 and the parsing is covered by tests built from them, but the first real run on GitHub is the first time the full chain runs against the live sites. If a source changes its HTML, the credit line falls back to the source's `credit_hint` and the issue says "not found, check the page".
- Esri's blog and newsroom feeds return empty channels, and NASA SVS, Earthdata, NOAA NESDIS and USGS block or time out from GitHub runners. These sites only come in through Google Alerts. The full list of checked and rejected feeds is at the bottom of `config/sources.yaml`.
- `@` handles in drafts come from Claude's judgement plus the `tag` field in sources; check any handle you have not used before.
