# Market Intelligence Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the remaining researched Qagent capabilities as one usable A-share market-intelligence layer: data quality, market regime, strategy scheduling, dynamic recommendation calibration, and event-hypothesis enrichment.

**Architecture:** Add a focused backend research module that consumes existing scan cards, scan items, bars, strategy health, and data-health metadata, then returns a `MarketIntelligenceCenter`. The daily scan pipeline applies the center to cards before final sorting so recommendations are actually calibrated, and the frontend renders the result on Today.

**Tech Stack:** Python/Pydantic/FastAPI backend, existing Qagent scan pipeline, React/TypeScript frontend, CSS terminal theme, pytest/ruff/npm checks.

---

### Task 1: Backend Intelligence Model And Tests

**Files:**
- Create: `backend/qagent/research/market_intelligence.py`
- Modify: `backend/tests/test_market_intelligence.py`

- [ ] Write failing tests for data-quality report, market environment, scheduler weights, calibration notes, and event-hypothesis summary.
- [ ] Run: `backend/.venv/bin/python -m pytest backend/tests/test_market_intelligence.py -q`
- [ ] Implement Pydantic models and builder functions.
- [ ] Re-run the test.

### Task 2: Scan Pipeline Integration

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Modify: `backend/qagent/domain/models.py`
- Modify: `backend/tests/test_market_decision_layers.py`

- [ ] Add `market_intelligence` to `DailyScanResult`.
- [ ] Add optional card fields for `quality_score`, `market_fit_score`, `dynamic_score`, and `calibration_notes`.
- [ ] Apply dynamic calibration before final card sorting.
- [ ] Test that daily scan returns intelligence and card scores have calibration notes.

### Task 3: API Serialization

**Files:**
- Modify: `backend/qagent/api/routes.py`
- Modify: `backend/tests/test_api_state.py`

- [ ] Ensure opportunities/full-market responses include `market_intelligence`.
- [ ] Test API payload contains `data_quality`, `market_environment`, `strategy_scheduler`, `event_hypotheses`, and calibrated cards.

### Task 4: Frontend Types, Client, And Today Panel

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/components/MarketIntelligenceCenter.tsx`
- Modify: `frontend/src/pages/Today.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/check-today-ui.mjs`

- [ ] Add TypeScript types for the intelligence center.
- [ ] Render a Today panel showing market regime, data readiness, strategy weights, calibration state, event hypotheses, and warnings.
- [ ] Ensure Chinese display and no code-only labels in the panel.
- [ ] Update Today UI checker.

### Task 5: Verification

**Commands:**
- `backend/.venv/bin/python -m pytest -q`
- `backend/.venv/bin/python -m ruff check .`
- `cd frontend && npm run build`
- `cd frontend && npm run check:today-ui && npm run check:new-user-flow && npm run check:terminal-ui && npm run check:scan-lifecycle && npm run check:instruments`

