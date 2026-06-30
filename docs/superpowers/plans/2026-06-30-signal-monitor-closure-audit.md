# Signal Monitor Closure Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested signal monitoring layer that tells users which recommendations have triggered, hit risk lines, approached targets, or weakened after recommendation.

**Architecture:** Build a `qagent.monitoring.signal_monitor` module from existing opportunity cards and latest OHLCV bars. Attach the resulting center to `DailyScanResult`, API scan payloads, cached full-market payloads, and Today UI without creating a second recommendation source.

**Tech Stack:** Python/Pydantic/pytest, FastAPI response serialization, React/TypeScript/Vite.

---

### Task 1: Backend Signal Monitor Engine

**Files:**
- Create: `backend/qagent/monitoring/signal_monitor.py`
- Test: `backend/tests/test_signal_monitor.py`

- [x] Write failing tests for entry triggered, stop breached, near target, target reached, and weakened recommendation states.
- [x] Implement monitor models and classification rules.
- [x] Return headline, counts, action queue, and data health.

### Task 2: Scan/API Integration

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Modify: `backend/qagent/api/routes.py`
- Modify: `backend/qagent/jobs/full_market.py`
- Test: `backend/tests/test_jobs.py`
- Test: `backend/tests/test_api_opportunities.py`

- [x] Write failing tests that scan/API payloads expose `signal_monitor`.
- [x] Attach monitor after quality gate/sorting so it uses final recommendations.
- [x] Preserve monitor in full-market cache and hydrated cached payloads.

### Task 3: Today UI

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/components/SignalMonitorCenter.tsx`
- Modify: `frontend/src/pages/Today.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/check-today-ui.mjs`

- [x] Add failing static UI checks for signal monitor panel/classes.
- [x] Render trigger/risk/target/weakened counts and action queue.
- [x] Keep Chinese labels clear for non-expert users.

### Task 4: Verification

- [x] Run targeted backend monitor/API tests.
- [x] Run full backend tests and ruff.
- [x] Run frontend build and all UI checks.
- [x] Browser smoke-test Today page for the new monitor panel.
