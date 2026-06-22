# Qagent Daily Brief Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily research brief that condenses Qagent opportunities, levels, catalysts, risk, data caveats, and backtest validation into one readable artifact.

**Architecture:** Add a deterministic `qagent.briefing.daily` service, compose it from existing API route dependencies, and render it in a new Brief page. Keep data fetching in the API route and presentation in React.

**Tech Stack:** FastAPI, Pydantic, pytest, React/Vite/TypeScript.

---

### Task 1: Daily Brief Service

**Files:**
- Create: `backend/qagent/briefing/__init__.py`
- Create: `backend/qagent/briefing/daily.py`
- Test: `backend/tests/test_daily_brief.py`

- [x] **Step 1: Write failing service test**

Create a test that runs fixture scan/backtest data and asserts the brief has headline, top opportunities, entry watch, strategy validation, caveats, next steps, and no guaranteed-return wording.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_daily_brief.py -q`
Expected: fail because `qagent.briefing.daily` does not exist.

- [x] **Step 3: Implement service**

Add Pydantic models and `build_daily_brief(...)` with deterministic ranking and caveat aggregation.

- [x] **Step 4: Run green test**

Run: `backend/.venv/bin/pytest backend/tests/test_daily_brief.py -q`
Expected: pass.

### Task 2: Daily Brief API

**Files:**
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_api_opportunities.py`

- [x] **Step 1: Write failing API test**

Add a test for `/api/daily-brief?provider=fixture` that asserts top opportunities, entry watch, strategy validation, next steps, and data health.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_api_opportunities.py::test_daily_brief_endpoint_returns_research_digest -q`
Expected: fail with 404.

- [x] **Step 3: Implement route composition**

Compose scan, in-memory alert suggestions, backtest, provider status, optional catalysts, and portfolio risk into `build_daily_brief`.

- [x] **Step 4: Run green test**

Run: `backend/.venv/bin/pytest backend/tests/test_api_opportunities.py::test_daily_brief_endpoint_returns_research_digest -q`
Expected: pass.

### Task 3: Brief Frontend Page

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/pages/Brief.tsx`
- Modify: `frontend/src/styles.css`

- [x] **Step 1: Add types and API client**

Add daily brief response types and `fetchDailyBrief`.

- [x] **Step 2: Add navigation and page**

Add `brief` to nav, route it in `App`, and render headline, metric strip, top opportunities, validation, catalysts, risks, caveats, and next steps.

- [x] **Step 3: Build frontend**

Run: `npm --prefix frontend run build`
Expected: pass.

### Task 4: Docs And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/development.md`
- Modify: `docs/superpowers/plans/2026-06-22-qagent-daily-brief.md`

- [x] **Step 1: Update docs**

Document `/api/daily-brief`, the Brief page, and the current boundary that scheduling/push is not included yet.

- [x] **Step 2: Run full verification**

Run:
`backend/.venv/bin/pytest -q`
`backend/.venv/bin/ruff check backend/qagent backend/tests`
`npm --prefix frontend run build`
`git diff --check`
Expected: all exit 0.
