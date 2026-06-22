# Qagent Production Readiness Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add provider readiness, strategy performance, alert suggestions, and SEC ownership confirmation so Qagent feels like a complete research workflow.

**Architecture:** Keep the current scan/replay data model. Add small services for provider status, performance aggregation, and alert suggestions; wire them into APIs and existing dashboard pages.

**Tech Stack:** FastAPI, SQLAlchemy repository records, Pydantic services, pytest, React/Vite/TypeScript.

---

### Task 1: Provider Readiness

**Files:**
- Create: `backend/qagent/providers/status.py`
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_provider_status.py`

- [x] **Step 1: Write failing tests**

Test that provider status reports fixture, free market providers, Alpha Vantage, FMP, Finnhub, SEC EDGAR, CNINFO, and Tushare readiness based on `Settings`.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_provider_status.py -q`
Expected: fail because provider status service does not exist.

- [x] **Step 3: Implement service and route**

Add `ProviderStatus` model, `build_provider_status(settings)`, and `GET /api/provider-status`.

- [x] **Step 4: Run green test**

Run: `backend/.venv/bin/pytest backend/tests/test_provider_status.py -q`
Expected: pass.

### Task 2: Alert Suggestions

**Files:**
- Modify: `backend/qagent/monitoring/alerts.py`
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_alerts.py`
- Test: `backend/tests/test_api_alert_rules.py`

- [x] **Step 1: Write failing tests**

Test that recent opportunity snapshots generate entry, stop, and target suggestions.

- [x] **Step 2: Run red tests**

Run: `backend/.venv/bin/pytest backend/tests/test_alerts.py backend/tests/test_api_alert_rules.py -q`
Expected: fail because suggestions do not exist.

- [x] **Step 3: Implement suggestions and route**

Add `AlertSuggestion`, `suggest_alert_rules`, and `GET /api/alert-suggestions`.

- [x] **Step 4: Run green tests**

Run: `backend/.venv/bin/pytest backend/tests/test_alerts.py backend/tests/test_api_alert_rules.py -q`
Expected: pass.

### Task 3: Strategy Performance

**Files:**
- Modify: `backend/qagent/monitoring/outcomes.py`
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_outcomes.py`
- Test: `backend/tests/test_api_state.py`

- [x] **Step 1: Write failing tests**

Test that replayed opportunity outcomes aggregate by primary strategy with sample counts, hit rate, positive rate, average returns, max drawdown, and pending count.

- [x] **Step 2: Run red tests**

Run: `backend/.venv/bin/pytest backend/tests/test_outcomes.py backend/tests/test_api_state.py -q`
Expected: fail because performance aggregation route does not exist.

- [x] **Step 3: Implement aggregation and route**

Add `StrategyPerformance`, `summarize_strategy_performance`, and `GET /api/strategy-performance`.

- [x] **Step 4: Run green tests**

Run: `backend/.venv/bin/pytest backend/tests/test_outcomes.py backend/tests/test_api_state.py -q`
Expected: pass.

### Task 4: SEC Ownership Confirmation Strategy

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Modify: `backend/qagent/strategies/evaluator.py`
- Test: `backend/tests/test_strategy_evaluator.py`
- Test: `backend/tests/test_jobs.py`

- [x] **Step 1: Write failing tests**

Test that Form 4 or 13F filings make `insider_institutional_confirmation` score watch/passed, and absent filings stay missing-data.

- [x] **Step 2: Run red tests**

Run: `backend/.venv/bin/pytest backend/tests/test_strategy_evaluator.py backend/tests/test_jobs.py -q`
Expected: fail because ownership strategy is still always missing-data.

- [x] **Step 3: Implement strategy branch**

Add available-data flags from filings and evaluator logic for ownership confirmation.

- [x] **Step 4: Run green tests**

Run: `backend/.venv/bin/pytest backend/tests/test_strategy_evaluator.py backend/tests/test_jobs.py -q`
Expected: pass.

### Task 5: Frontend Integration

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/Settings.tsx`
- Modify: `frontend/src/pages/Alerts.tsx`
- Modify: `frontend/src/pages/History.tsx`

- [x] **Step 1: Add types and client calls**

Add provider status, alert suggestions, and strategy performance types plus fetch methods.

- [x] **Step 2: Render tables**

Render provider status in Settings, suggestions in Alerts, and performance in History.

- [x] **Step 3: Build frontend**

Run: `npm --prefix frontend run build`
Expected: pass.

### Task 6: Verification And Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/development.md`

- [x] **Step 1: Update docs**

Document provider status, alert suggestions, strategy performance, and SEC ownership confirmation.

- [x] **Step 2: Run full verification**

Run:
`backend/.venv/bin/pytest -q`
`backend/.venv/bin/ruff check backend/qagent backend/tests`
`npm --prefix frontend run build`
`git diff --check`
Expected: all exit 0.
