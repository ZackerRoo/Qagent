# Decision Quality Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one user-facing decision center that connects recommendation calibration, market regime, portfolio sizing, plain-language explanations, validation/backtest linkage, and alert readiness.

**Architecture:** Build a focused backend module from existing opportunity cards, market intelligence, portfolio plan, signal monitor, and strategy health. Attach it to daily scans, full-market cache payloads, hydrated legacy cache, and the Today UI without replacing the existing research/manual/signal centers.

**Tech Stack:** Python/Pydantic/pytest, FastAPI JSON payload hydration, React/TypeScript/Vite static UI checks.

---

### Task 1: Backend Decision Center

**Files:**
- Create: `backend/qagent/research/decision_quality.py`
- Test: `backend/tests/test_decision_quality_center.py`

- [x] Write failing tests for the six sections: calibration, market policy, portfolio, explanations, validation linkage, alerts.
- [x] Implement Pydantic models and builder using existing cards/market intelligence/portfolio/signal monitor/strategy health.
- [x] Return data health keys so API and UI can confirm the center is populated.

### Task 2: Scan/API Integration

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Modify: `backend/qagent/jobs/full_market.py`
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_jobs.py`
- Test: `backend/tests/test_api_opportunities.py`

- [x] Add `decision_quality_center` to scan results and payloads.
- [x] Preserve it in batch full-market cache.
- [x] Hydrate legacy cached payloads that do not have the new field.

### Task 3: Today UI

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/components/DecisionQualityCenter.tsx`
- Modify: `frontend/src/pages/Today.tsx`
- Modify: `frontend/scripts/check-today-ui.mjs`
- Modify: `frontend/src/styles.css`

- [x] Add TypeScript types for the new center.
- [x] Render a Chinese-first panel with the six decision sections.
- [x] Extend static UI checks so this panel cannot disappear silently.

### Task 4: Verification

- [x] Run targeted backend tests for the new center and API/cache integration.
- [x] Run full backend tests and ruff.
- [x] Run frontend build and all frontend checks.
- [x] Verify live API returns populated decision center data.
