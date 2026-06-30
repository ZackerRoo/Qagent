# Manual Action Center

## Goal

Add a manual, user-facing action layer without automatic scheduling:

- Today's concrete action list.
- Alert-loop readiness for entry, stop, target, and weakening checks.
- Data-source upgrade roadmap.
- Strategy effectiveness dashboard.

## Scope

- Backend model and builder under `qagent.research`.
- Attach the center to daily scans, full-market scans, cached payload hydration, and API responses.
- Frontend Today panel with Chinese-first copy and compact visual status.
- Focused backend and frontend verification.

## Non-goals

- No daily automatic run scheduler.
- No broker connection or live trading.

## Verification

- Backend tests cover center construction and API payload presence.
- Frontend script checks Today page integration, component text, and CSS classes.
- Run targeted tests, ruff, and frontend checks before final.
