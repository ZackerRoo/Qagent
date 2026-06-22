# Qagent Brief Runs And Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist Daily Brief outputs, expose brief history/detail APIs, and render saved briefs as Markdown.

**Architecture:** Add a `brief_runs` table with JSON payload storage, repository records, API endpoints, a deterministic Markdown exporter, and Brief page controls for save/load/export.

**Tech Stack:** SQLAlchemy, SQLite, Pydantic, FastAPI, pytest, React/Vite/TypeScript.

---

### Task 1: Brief Run Storage

**Files:**
- Modify: `backend/qagent/storage/tables.py`
- Modify: `backend/qagent/storage/repository.py`
- Test: `backend/tests/test_state_repository.py`

- [x] **Step 1: Write failing repository test**

Test that a `DailyBrief` can be saved, listed, and loaded by id with provider, symbols, headline, counts, and JSON payload intact.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_state_repository.py::test_repository_saves_and_loads_brief_runs -q`
Expected: fail because repository methods do not exist.

- [x] **Step 3: Implement table and repository methods**

Add `BriefRunRow`, `BriefRunRecord`, `save_brief_run`, `list_brief_runs`, and `get_brief_run`.

- [x] **Step 4: Run green test**

Run: `backend/.venv/bin/pytest backend/tests/test_state_repository.py::test_repository_saves_and_loads_brief_runs -q`
Expected: pass.

### Task 2: Markdown Export

**Files:**
- Create: `backend/qagent/briefing/export.py`
- Test: `backend/tests/test_daily_brief.py`

- [x] **Step 1: Write failing exporter test**

Test `render_daily_brief_markdown` includes headline, top opportunities, entry watch, strategy validation, caveats, and next steps.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_daily_brief.py::test_render_daily_brief_markdown_contains_key_sections -q`
Expected: fail because exporter does not exist.

- [x] **Step 3: Implement exporter**

Add deterministic Markdown rendering from a `DailyBrief` model.

- [x] **Step 4: Run green test**

Run: `backend/.venv/bin/pytest backend/tests/test_daily_brief.py::test_render_daily_brief_markdown_contains_key_sections -q`
Expected: pass.

### Task 3: Brief Run API

**Files:**
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_api_opportunities.py`

- [x] **Step 1: Write failing API tests**

Test saving a brief run, listing runs, loading a run, and exporting Markdown.

- [x] **Step 2: Run red tests**

Run: `backend/.venv/bin/pytest backend/tests/test_api_opportunities.py::test_daily_brief_run_api_saves_lists_loads_and_exports_markdown -q`
Expected: fail because routes do not exist.

- [x] **Step 3: Implement API routes**

Refactor daily brief composition into a helper and add the run/list/detail/export endpoints.

- [x] **Step 4: Run green tests**

Run: `backend/.venv/bin/pytest backend/tests/test_api_opportunities.py::test_daily_brief_run_api_saves_lists_loads_and_exports_markdown -q`
Expected: pass.

### Task 4: Brief Page Save/History/Export

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/Brief.tsx`
- Modify: `frontend/src/styles.css`

- [x] **Step 1: Add types and client calls**

Add brief run list/detail/markdown types and API calls.

- [x] **Step 2: Render save/history/export controls**

Add Save Brief, recent run list, Load, and Markdown export panel.

- [x] **Step 3: Build frontend**

Run: `npm --prefix frontend run build`
Expected: pass.

### Task 5: Docs And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/development.md`
- Modify: `docs/superpowers/plans/2026-06-22-qagent-brief-runs.md`

- [x] **Step 1: Update docs**

Document saved brief runs and Markdown export APIs.

- [x] **Step 2: Run full verification**

Run:
`backend/.venv/bin/pytest -q`
`backend/.venv/bin/ruff check backend/qagent backend/tests`
`npm --prefix frontend run build`
`git diff --check`
Expected: all exit 0.
