# Qagent Brief Runs And Export Design

## Goal

Make Daily Brief outputs durable so users can review prior briefs, compare day-to-day changes, and export a readable Markdown brief for future scheduled delivery.

## Product Shape

Daily Brief currently returns an on-demand research digest. This feature adds:

- Persist a generated brief as a `brief_run`.
- List recent brief runs.
- Retrieve one saved brief by id.
- Export a saved or freshly generated brief to Markdown.
- Show saved brief history in the Brief page.

This is the push-ready foundation. External delivery channels such as email, Telegram, Feishu, or cron scheduling remain separate because they require credentials and deployment choices.

## Architecture

Add a `brief_runs` table and repository methods. Store the full brief JSON rather than duplicating every section into relational rows. The brief schema is already typed by Pydantic; persisting JSON preserves compatibility with future sections while supporting list/detail/export.

Add `qagent.briefing.export` for deterministic Markdown rendering. The exporter accepts a `DailyBrief` model and produces a compact research note with headline, opportunities, entry watch, strategy validation, catalysts, risk alerts, caveats, and next steps.

API changes:

- `GET /api/daily-brief`: keep returning an on-demand brief.
- `POST /api/daily-brief/runs`: generate and save a brief.
- `GET /api/daily-brief/runs`: list saved brief runs.
- `GET /api/daily-brief/runs/{brief_id}`: retrieve saved brief.
- `GET /api/daily-brief/runs/{brief_id}/markdown`: export saved brief as Markdown text.

Frontend changes:

- Brief page adds a `Save Brief` button.
- Brief page shows recent saved brief runs.
- User can load a saved brief into the page.
- User can open/copy the Markdown export text in a panel.

## Testing

Backend:

- Repository saves/lists/loads brief runs.
- Markdown exporter includes key sections and does not contain guaranteed-return language.
- API can save a fixture brief, list it, retrieve it, and export Markdown.

Frontend:

- TypeScript build must pass.

## Boundaries

No actual external push channel, no background scheduler, no cron installation, and no notification credentials. The output is ready for those channels later.
