# Qagent Backtest Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add event-level historical backtesting for Qagent opportunity cards and primary strategies.

**Architecture:** Reuse the existing daily scan, opportunity snapshot, outcome replay, and strategy performance modules. Add one focused backtesting engine, one API route, and one History-page UI panel.

**Tech Stack:** FastAPI, Pydantic, pandas, pytest, React/Vite/TypeScript.

---

### Task 1: Make Daily Scan As-Of A Date Range

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Test: `backend/tests/test_jobs.py`

- [x] **Step 1: Write failing test**

Add a test proving `run_daily_scan(..., start=..., end=...)` only uses bars through `end`.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_jobs.py::test_daily_scan_respects_caller_date_window -q`
Expected: fail because `run_daily_scan` does not accept `start` and `end`.

- [x] **Step 3: Implement date parameters**

Add optional `start` and `end` parameters with existing 2026 defaults, and use them for market bars plus strategy data.

- [x] **Step 4: Run green test**

Run: `backend/.venv/bin/pytest backend/tests/test_jobs.py::test_daily_scan_respects_caller_date_window -q`
Expected: pass.

### Task 2: Add Backtest Engine

**Files:**
- Create: `backend/qagent/backtesting/__init__.py`
- Create: `backend/qagent/backtesting/engine.py`
- Test: `backend/tests/test_backtesting.py`

- [x] **Step 1: Write failing tests**

Test fixture backtests produce scan dates, event outcomes, summary metrics, and strategy performance.

- [x] **Step 2: Run red tests**

Run: `backend/.venv/bin/pytest backend/tests/test_backtesting.py -q`
Expected: fail because the module does not exist.

- [x] **Step 3: Implement engine**

Add `BacktestSignal`, `BacktestSummary`, `BacktestResult`, and `run_historical_backtest`.

- [x] **Step 4: Run green tests**

Run: `backend/.venv/bin/pytest backend/tests/test_backtesting.py -q`
Expected: pass.

### Task 3: Add Backtest API

**Files:**
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_api_state.py`

- [x] **Step 1: Write failing API tests**

Test `/api/backtest` returns summary/performance/signals and rejects reversed date ranges.

- [x] **Step 2: Run red tests**

Run: `backend/.venv/bin/pytest backend/tests/test_api_state.py::test_backtest_api_returns_fixture_validation backend/tests/test_api_state.py::test_backtest_api_rejects_reversed_date_range -q`
Expected: fail because `/api/backtest` does not exist.

- [x] **Step 3: Implement route**

Parse query parameters, choose provider/universe defaults, call `run_historical_backtest`, and serialize result.

- [x] **Step 4: Run green tests**

Run: `backend/.venv/bin/pytest backend/tests/test_api_state.py::test_backtest_api_returns_fixture_validation backend/tests/test_api_state.py::test_backtest_api_rejects_reversed_date_range -q`
Expected: pass.

### Task 4: Add Frontend Backtest Panel

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/History.tsx`
- Modify: `frontend/src/styles.css`

- [x] **Step 1: Add types and client call**

Add backtest response types and `fetchBacktest`.

- [x] **Step 2: Render History panel**

Add a Run Backtest panel with summary metrics, strategy performance table, and recent signal table.

- [x] **Step 3: Build frontend**

Run: `npm --prefix frontend run build`
Expected: pass.

### Task 5: Docs And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/development.md`
- Modify: `docs/superpowers/plans/2026-06-22-qagent-backtest-validation.md`

- [x] **Step 1: Update docs**

Document `/api/backtest`, event-level scope, and limitations.

- [x] **Step 2: Run full verification**

Run:
`backend/.venv/bin/pytest -q`
`backend/.venv/bin/ruff check backend/qagent backend/tests`
`npm --prefix frontend run build`
`git diff --check`
Expected: all exit 0.
