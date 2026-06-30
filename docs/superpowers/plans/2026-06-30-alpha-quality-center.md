# Alpha Quality Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a recommendation-quality center that turns current Qagent recommendations into a concrete buyability judgment, strategy-weight policy, and theme confirmation view.

**Architecture:** Compose existing cards, strategy health, rotation radar, and data-health signals into a lightweight backend center. Attach it to opportunity payloads and cached full-market payloads, then render it near the top of Today so users can see whether the current top recommendation is usable and why.

**Tech Stack:** Python/Pydantic/FastAPI, React/TypeScript, existing pytest and frontend static checks.

---

### Task 1: Backend Contract

**Files:**
- Create: `backend/tests/test_alpha_quality_center.py`
- Modify: `backend/tests/test_api_opportunities.py`

- [x] **Step 1: Write failing tests for buyability gate, top recommendation review, strategy tuning, and theme confirmation**
- [x] **Step 2: Run targeted pytest and verify expected missing-module/field failures**

### Task 2: Backend Implementation

**Files:**
- Create: `backend/qagent/research/alpha_quality.py`
- Modify: `backend/qagent/api/routes.py`

- [x] **Step 1: Implement AlphaQualityCenter and pure builder**
- [x] **Step 2: Attach center to opportunities, overview, full-market scan payloads, task payloads, and cached payload hydration**
- [x] **Step 3: Run targeted pytest and fix failures**

### Task 3: Frontend UI

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/components/AlphaQualityCenter.tsx`
- Modify: `frontend/src/pages/Today.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/check-today-ui.mjs`

- [x] **Step 1: Add types and static UI assertions**
- [x] **Step 2: Render recommendation quality center with Chinese-first labels**
- [x] **Step 3: Run frontend checks and build**

### Task 4: Verification

**Files:**
- No new files unless failures require fixes.

- [x] **Step 1: Run backend suite and ruff**
- [x] **Step 2: Run frontend build and UI checks**
- [x] **Step 3: Hit live fixture API and verify top recommendation, gate, strategy tuning, and theme confirmation are present**
- [x] **Step 4: Open desktop/mobile page and verify no obvious layout regression**
