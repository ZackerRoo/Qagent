# Recommendation Usability Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the next four Qagent usability capabilities together: stronger A-share data-source readiness, visible non-recommendation explanations, richer backtest credibility, and a direct “how to use today” guide.

**Architecture:** Keep the existing scan/result payload as the source of truth. Add structured fields to scan items and backtest results, keep legacy payloads tolerant in the UI, and render the new user guidance in Today/Opportunities/History without introducing a new page.

**Tech Stack:** Python/FastAPI/Pydantic/pytest, React/TypeScript/Vite, existing SQLite scan cache.

---

### Task 1: Scan Data Readiness

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Test: `backend/tests/test_jobs.py`
- Modify: `frontend/src/types.ts`

- [x] Add failing tests for A-share data readiness keys in `data_health`.
- [x] Derive readiness from bars, trading status, tradability, market context, strategy data, and cache stats.
- [x] Surface readiness through existing market intelligence and action center.

### Task 2: Non-Recommendation Explanations

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Modify: `backend/qagent/jobs/full_market.py`
- Test: `backend/tests/test_jobs.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/Opportunities.tsx`

- [x] Add failing tests that batch scan cache includes rejected/no-data scan items.
- [x] Add `rejection_category`, `rejection_score`, and `remediation` to `ScanItem`.
- [x] Render category and remediation in the existing “未推荐原因” table.

### Task 3: Backtest Credibility

**Files:**
- Modify: `backend/qagent/backtesting/engine.py`
- Test: `backend/tests/test_backtesting.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/History.tsx`

- [x] Add failing tests for benchmark comparison and environment breakdown.
- [x] Compute equal-weight universe benchmark, excess return, and risk-adjusted verdict.
- [x] Split results into up/range/down environments from forward universe returns.
- [x] Render the metrics in the backtest summary.

### Task 4: “Today How To Use” Guide

**Files:**
- Modify: `frontend/src/pages/Today.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/check-today-ui.mjs`

- [x] Add failing UI static checks for the guide.
- [x] Render a compact step-by-step guide: load scan, check quality, inspect plan, add to paper ledger, review follow-through.
- [x] Keep it concise and operational, not marketing copy.

### Task 5: Verification

- [x] Run targeted backend tests for jobs and backtesting.
- [x] Run full backend tests and ruff.
- [x] Run frontend build and all UI checks.
