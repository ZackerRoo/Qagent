# Operational Readiness Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a user-facing readiness layer that answers whether Qagent's recommendation, reason, score, trade plan, validation, and alert follow-up are usable.

**Architecture:** Build a focused backend research module that composes existing decision-quality, market-intelligence, signal-monitor, strategy-health, data-health, and current recommendation cards into six readiness checks. Expose it through scan results and API hydration, then render a compact Chinese-first panel on Today.

**Tech Stack:** Python/Pydantic/FastAPI, React/TypeScript, existing pytest and frontend static UI checks.

---

### Task 1: Backend Behavior Contract

**Files:**
- Create: `backend/tests/test_operational_readiness_center.py`
- Modify: `backend/tests/test_jobs.py`
- Modify: `backend/tests/test_api_opportunities.py`

- [x] **Step 1: Write failing unit and integration tests**
- [x] **Step 2: Run targeted pytest and verify expected import/field failures**

### Task 2: Backend Implementation

**Files:**
- Create: `backend/qagent/research/operational_readiness.py`
- Modify: `backend/qagent/jobs/daily_scan.py`
- Modify: `backend/qagent/jobs/full_market.py`
- Modify: `backend/qagent/api/routes.py`

- [x] **Step 1: Implement six readiness checks**
- [x] **Step 2: Attach the center to scan results, full-market batch payloads, cached payload hydration, opportunities, and overview**
- [x] **Step 3: Run targeted pytest and fix failures**

### Task 3: Frontend Contract and UI

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/components/OperationalReadinessCenter.tsx`
- Modify: `frontend/src/pages/Today.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/check-today-ui.mjs`

- [x] **Step 1: Add TypeScript types and static UI assertions**
- [x] **Step 2: Render a Chinese-first panel that answers user questions**
- [x] **Step 3: Run frontend checks and build**

### Task 4: User Perspective Verification

**Files:**
- No new files unless failures require fixes.

- [x] **Step 1: Run backend suite and ruff**
- [x] **Step 2: Run frontend build and UI checks**
- [x] **Step 3: Hit live fixture API and verify recommendation, reason, strategy score, buy/sell plan, validation, and alert readiness are present**
- [x] **Step 4: Open/test the local page if needed and fix layout regressions**
