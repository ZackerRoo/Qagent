# Qagent Universe And Alert Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add saved theme universes and a provider-backed alert runner with outbox notifications.

**Architecture:** Implement static starter universes in `qagent.market.universes`, persist custom universes in SQLite through the existing repository, and expose both through FastAPI. Add `qagent.jobs.alert_runner` to compose provider snapshots, stored alert rules, and delivery outbox records.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, pytest, React, TypeScript, Vite.

---

### Task 1: Universe Catalog

**Files:**
- Create: `backend/qagent/market/universes.py`
- Modify: `backend/qagent/storage/tables.py`
- Modify: `backend/qagent/storage/repository.py`
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_universes.py`
- Test: `backend/tests/test_api_universes.py`

- [ ] Write failing tests for built-in and custom universes.
- [ ] Implement Pydantic universe models and starter catalog.
- [ ] Add `universe_rows` table and repository methods.
- [ ] Add list/create/detail API routes.

### Task 2: Alert Runner

**Files:**
- Create: `backend/qagent/jobs/alert_runner.py`
- Modify: `backend/qagent/storage/repository.py`
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_alert_runner.py`
- Test: `backend/tests/test_api_alert_rules.py`

- [ ] Write failing tests for provider-backed alert runs and queued delivery records.
- [ ] Implement latest-price snapshot evaluation from stored alert rules.
- [ ] Reuse delivery outbox for Markdown alert notifications.
- [ ] Add `/api/alerts/run`.

### Task 3: Frontend

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/pages/Alerts.tsx`
- Modify: `frontend/src/styles.css`

- [ ] Add universe and alert runner types/client calls.
- [ ] Add universe selector to scan controls.
- [ ] Add alert run button and queued delivery feedback.
- [ ] Run frontend build.

### Task 4: Verification

- [ ] Run targeted backend tests.
- [ ] Run full backend pytest.
- [ ] Run ruff.
- [ ] Run frontend build.
- [ ] Commit locally, do not push.
