# Qagent Validation Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct paper-validation statistics, persist point-in-time fundamentals, and add genuine temporal out-of-sample evidence to historical backtests.

**Architecture:** Keep paper statistics inside `paper_trading/engine.py`, add a focused fundamental snapshot repository contract to the existing SQLite storage layer, and add a standalone temporal validation module consumed by the event backtest. Existing API and History UI receive additive fields so current clients remain compatible.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy, pandas, pytest, React, TypeScript.

---

### Task 1: Correct paper validation semantics

**Files:**
- Modify: `backend/qagent/paper_trading/engine.py`
- Modify: `backend/tests/test_api_paper_trading.py`
- Modify: `backend/tests/test_paper_trading.py`

- [x] Write a failing test where eight-day-old stopped and missed trades do not become 10/20-day mature samples.
- [x] Run the focused test and confirm it fails on the current maturity count.
- [x] Separate tracked, missed, executed, resolved, and mature sample predicates.
- [x] Exclude missed entries from returns, win rate, drawdown attribution, and credibility.
- [x] Run focused paper-trading tests.

### Task 2: Persist point-in-time fundamental snapshots

**Files:**
- Modify: `backend/qagent/storage/tables.py`
- Modify: `backend/qagent/storage/repository.py`
- Modify: `backend/qagent/api/routes.py`
- Modify: `backend/tests/test_state_repository.py`
- Modify: `backend/tests/test_factor_backtest.py`

- [x] Write failing repository tests for idempotent upsert and date-filtered loading.
- [x] Add `fundamental_snapshots` SQLite table and repository model conversion.
- [x] Merge fetched and stored snapshots in the factor-backtest endpoint.
- [x] Add data-health counters for live and stored fundamentals.
- [x] Run repository and factor-backtest tests.

### Task 3: Add temporal out-of-sample validation

**Files:**
- Create: `backend/qagent/backtesting/temporal_validation.py`
- Create: `backend/tests/test_temporal_validation.py`
- Modify: `backend/qagent/backtesting/engine.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/History.tsx`
- Modify: `frontend/scripts/check-backtest-ui.mjs`

- [x] Write failing tests for chronological split, embargo, deterministic confidence intervals, and insufficient samples.
- [x] Implement focused temporal validation models and calculations.
- [x] Attach temporal validation to `BacktestResult`.
- [x] Add a compact existing-page panel showing the three windows and the out-of-sample verdict.
- [x] Run focused backend and frontend checks.

### Task 4: Full verification

- [x] Run the backend test suite (`305 passed`).
- [x] Run `ruff check .`.
- [x] Run all frontend contract checks used by the dashboard.
- [x] Run `npm run build`.
- [x] Query the live paper validation endpoint and confirm that 10/20-day mature counts no longer exceed actual horizon age.
