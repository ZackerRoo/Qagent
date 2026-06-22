# Qagent Free Fundamental Strategy Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add free-source fundamental, valuation, and analyst-context data so Qagent can evaluate valuation and revision strategies when real fields are available.

**Architecture:** Extend the existing `strategy_data` interface instead of creating a second data path. Providers normalize upstream payloads into typed records; `run_daily_scan` passes those records into `StrategyEvaluator`, which scores only when required fields are present and otherwise keeps `missing_data`.

**Tech Stack:** Python, FastAPI backend, Pydantic models, httpx provider adapters, pytest TDD, existing React dashboard types.

---

### Task 1: Fundamental And Analyst Models

**Files:**
- Modify: `backend/qagent/strategy_data/models.py`
- Modify: `backend/qagent/strategy_data/providers.py`
- Test: `backend/tests/test_strategy_data_provider.py`

- [x] **Step 1: Write failing model/provider fixture tests**

Add tests proving fixture strategy data returns one `FundamentalSnapshot` and one `AnalystInsight` for `US:TEST`, including revenue growth, margin, valuation multiple, PEG, analyst target, and rating counts.

- [x] **Step 2: Run red tests**

Run: `backend/.venv/bin/pytest backend/tests/test_strategy_data_provider.py -q`
Expected: fail because `get_fundamentals` and `get_analyst_insights` do not exist.

- [x] **Step 3: Implement minimal models and fixture loader**

Create `FundamentalSnapshot` and `AnalystInsight` Pydantic models, extend `StrategyDataProvider`, and load deterministic CSV fixtures.

- [x] **Step 4: Run green tests**

Run: `backend/.venv/bin/pytest backend/tests/test_strategy_data_provider.py -q`
Expected: pass.

### Task 2: Real Free Provider Normalization

**Files:**
- Modify: `backend/qagent/config.py`
- Modify: `backend/qagent/strategy_data/providers.py`
- Modify: `backend/qagent/strategy_data/__init__.py`
- Test: `backend/tests/test_strategy_data_real_providers.py`

- [x] **Step 1: Write failing adapter tests**

Add tests for Alpha Vantage `OVERVIEW`, Alpha Vantage `EARNINGS`, Finnhub `/stock/metric`, and FMP key metrics/ratios normalization using mocked httpx payloads.

- [x] **Step 2: Run red tests**

Run: `backend/.venv/bin/pytest backend/tests/test_strategy_data_real_providers.py -q`
Expected: fail because provider methods/classes are missing.

- [x] **Step 3: Implement provider adapters**

Add `QAGENT_ALPHA_VANTAGE_API_KEY`; implement Alpha Vantage earnings/fundamentals/analyst context, Finnhub basic financials fundamentals, and FMP key metric fundamentals.

- [x] **Step 4: Run green tests**

Run: `backend/.venv/bin/pytest backend/tests/test_strategy_data_real_providers.py -q`
Expected: pass.

### Task 3: Strategy Evaluator Integration

**Files:**
- Modify: `backend/qagent/strategies/evaluator.py`
- Test: `backend/tests/test_strategy_evaluator.py`

- [x] **Step 1: Write failing strategy tests**

Add tests proving analyst revision, TAM-adjusted PEG, and Bayesian intrinsic growth strategies score when their normalized inputs are present, and remain `missing_data` when fields are absent.

- [x] **Step 2: Run red tests**

Run: `backend/.venv/bin/pytest backend/tests/test_strategy_evaluator.py -q`
Expected: fail because evaluator branches do not score these strategies.

- [x] **Step 3: Implement scoring**

Use explainable component scores: growth quality, margin quality, valuation sanity, PEG/TAM room, analyst target upside, analyst rating balance, and prior growth probability.

- [x] **Step 4: Run green tests**

Run: `backend/.venv/bin/pytest backend/tests/test_strategy_evaluator.py -q`
Expected: pass.

### Task 4: Daily Scan Coverage And Docs

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Modify: `backend/tests/test_jobs.py`
- Modify: `README.md`
- Modify: `docs/development.md`

- [x] **Step 1: Write failing scan coverage test**

Add a test proving daily scan counts fundamentals and analyst insights in `data_health` and passes the normalized records to strategies.

- [x] **Step 2: Run red test**

Run: `backend/.venv/bin/pytest backend/tests/test_jobs.py -q`
Expected: fail because scan does not request or expose the new records.

- [x] **Step 3: Implement scan wiring and docs**

Fetch fundamentals/analyst insights per instrument, add available-data flags, expose counts, and document free-source environment variables and limitations.

- [x] **Step 4: Run full verification**

Run:
`backend/.venv/bin/pytest -q`
`backend/.venv/bin/ruff check backend/qagent backend/tests`
`npm --prefix frontend run build`
`git diff --check`
Expected: all commands exit 0.
