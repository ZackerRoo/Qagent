# Qagent Automation And Portfolio Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable brief delivery queue and account-level portfolio backtest so Qagent can validate and operationalize its research loop.

**Architecture:** Extend the existing SQLAlchemy repository with a delivery outbox table and focused methods. Add a new `qagent.backtesting.portfolio` module that composes the existing event backtest and market provider bars into trades, an equity curve, and summary metrics. Expose both through FastAPI and the React dashboard.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, pandas, pytest, React, TypeScript, Vite.

---

### Task 1: Delivery Outbox

**Files:**
- Modify: `backend/qagent/storage/tables.py`
- Modify: `backend/qagent/storage/repository.py`
- Modify: `backend/qagent/api/routes.py`
- Modify: `backend/qagent/cli.py`
- Test: `backend/tests/test_state_repository.py`
- Test: `backend/tests/test_api_opportunities.py`
- Test: `backend/tests/test_cli.py`

- [ ] Write failing repository and API tests for enqueue/list/mark-sent.
- [ ] Implement `DeliveryOutboxRow`, record models, repository methods, and routes.
- [ ] Add CLI brief generation and outbox enqueue behavior.
- [ ] Run targeted tests until green.

### Task 2: Portfolio Backtest

**Files:**
- Create: `backend/qagent/backtesting/portfolio.py`
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_portfolio_backtest.py`
- Test: `backend/tests/test_api_state.py`

- [ ] Write failing tests for summary metrics, trades, equity curve, and API response.
- [ ] Implement position sizing, stop/target/time exits, costs, slippage, and drawdown metrics.
- [ ] Run targeted tests until green.

### Task 3: Frontend Integration

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/Brief.tsx`
- Modify: `frontend/src/pages/History.tsx`
- Modify: `frontend/src/styles.css`

- [ ] Add delivery and portfolio backtest types/client calls.
- [ ] Add outbox queue actions and status table to Brief.
- [ ] Add portfolio backtest panel, metrics, equity curve table, and trade table to History.
- [ ] Run frontend build.

### Task 4: Verification

- [ ] Run backend targeted tests.
- [ ] Run backend full pytest.
- [ ] Run ruff.
- [ ] Run frontend build.
- [ ] Commit and push the feature branch.
