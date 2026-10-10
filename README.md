# Geoawesome social agent

Every morning this repo checks a set of map and Earth observation feeds, picks the items worth a post, reads each item's own page, and drafts Facebook, Instagram, X, YouTube Community and LinkedIn versions in the Geoawesome template. The drafts land in a GitHub issue for review. Nothing is posted or scheduled automatically.

## How a run works

1. **Scout** (`agent/scout.py`): reads the feeds in `config/sources.yaml` and keeps items from the last 7 days that have not been seen before (`state/seen.json`).
2. **Shortlist** (`agent/llm.py`, Claude Haiku): scores the new items for visual strength and geo substance, keeps up to 4 scoring 6/10 or more.
3. **Enrich** (`agent/enrich.py`): opens each shortlisted page and pulls the caption text, lead image, and the real credit and licence lines. NASA pages: the `hds-credits` caption and the "NASA Earth Observatory images by..." line. ESA pages: the CREDIT / LICENCE block. Third-party photos found on a page are flagged as not reusable.
4. **Draft** (Claude Opus, `prompts/draft_system.md`): writes all five platform versions plus a short "what it shows" note for you and a list of things to check. It may only use facts from the page; anything missing (sensor, date, band meaning) goes into the check list instead of the post.
5. **Check** (`agent/checks.py`, rules in `config/style_rules.yaml`): em dashes, the banned word list, banned openers, CTA line with the link, hashtag count per platform, #Geoawesome present, single-word hashtags, one hashtag inside the body, image credit line, X length (links count as 23). Failures go back to Claude once with the exact list; anything still broken is marked ⚠️ in the issue.
6. **Review**: `drafts/YYYY-MM-DD.md` and `.json` are committed and the Markdown is opened as an issue labelled `social-drafts`. No issue is opened on days with nothing worth posting.

## Image rights

Each source has a reuse policy in `config/sources.yaml`:

- `reuse` (NASA Earth Observatory, ESA): the post can attach the image, with the `Image:` credit line copied from the page. ESA images are usually CC BY-SA 3.0 IGO, so the credit line is required.
- `link_only` (Maps Mania): the map belongs to someone else. The draft is a link post that names the creator, and the issue says to ask before attaching their image.

## Setup

1. Create an empty private repo (for example `walczakagh/geoawesome-social-agent`) and push this folder to it.
2. Add the repository secret `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions).
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

Metricool's REST API is only on its Advanced and Custom plans, so the repo does not post anywhere. In a Claude chat with the Metricool connector, say which item you want, for example "send item 2 from today's social drafts to Metricool for review", and it goes into Metricool's review queue, where you approve or edit it.

## Changing things

- **Add a source**: append to `config/sources.yaml` with `licence: reuse` only if the publisher's terms allow reuse with credit; otherwise `link_only`.
- **Change the voice or template**: `prompts/draft_system.md`. Keep `config/style_rules.yaml` in step, since the checker enforces it.
- **Models**: set `SHORTLIST_MODEL` / `DRAFT_MODEL` environment variables (defaults `claude-haiku-5-5`, `claude-opus-5-5`).
- **Volume**: workflow input `max_items` (default 4).

## Known limits

- The feed URLs and page structures were checked by hand on 10 October 2026 and the parsing is covered by tests built from them, but the first real run on GitHub is the first time the full chain runs against the live sites. If a source changes its HTML, the credit line falls back to the source's `credit_hint` and the issue says "not found, check the page".
- Copernicus' own RSS feed has not been updated since May 2026, so it is not enabled.
- `@` handles in drafts come from Claude's judgement plus the `tag` field in sources; check any handle you have not used before.
