# Recommendation Quality V2 Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each Qagent recommendation explain its composite score, pre-trade risk, and account-level buy scenario before the user follows it.

**Architecture:** Extend `OpportunityCard` with three structured payloads produced by the existing recommendation quality gate. Keep scan/card generation as the source of truth, so all API consumers and frontend pages receive the same recommendation-quality-v2 data without a new endpoint.

**Tech Stack:** Python/Pydantic/pytest, React/TypeScript/Vite, existing FastAPI card serialization.

---

### Task 1: Recommendation Quality V2 Payload

**Files:**
- Modify: `backend/qagent/domain/models.py`
- Modify: `backend/qagent/recommendations/quality_gate.py`
- Test: `backend/tests/test_recommendation_quality_gate.py`
- Modify: `frontend/src/types.ts`

- [x] Add failing tests that every quality-gated card has a weighted recommendation score breakdown.
- [x] Include factor, strategy, risk/reward, data, execution, market/theme, and penalty components.
- [x] Keep `rank_score` aligned with the visible final score so sorting is explainable.

### Task 2: Pre-Trade Risk Profile

**Files:**
- Modify: `backend/qagent/domain/models.py`
- Modify: `backend/qagent/recommendations/quality_gate.py`
- Test: `backend/tests/test_recommendation_quality_gate.py`
- Modify: `frontend/src/types.ts`

- [x] Add failing tests for blocked/warning/clear pre-trade risk status.
- [x] Combine trading status, tradability, board permissions, ST/low-liquidity/overextension, and incomplete-plan checks.
- [x] Produce concise user-facing next action and risk checklist.

### Task 3: Buy Scenario Simulation

**Files:**
- Modify: `backend/qagent/domain/models.py`
- Modify: `backend/qagent/recommendations/quality_gate.py`
- Test: `backend/tests/test_recommendation_quality_gate.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/Today.tsx`

- [x] Add failing tests for account-level position scenario fields.
- [x] Compute planned-entry downside, target upside, account drawdown if stopped, account gain at target, and min-lot cash.
- [x] Render the scenario beside the existing payoff chart in the selected opportunity workup.

### Task 4: UI Guardrails

**Files:**
- Modify: `frontend/src/pages/Today.tsx`
- Modify: `frontend/src/components/OpportunityTable.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/check-today-ui.mjs`

- [x] Add failing static checks for score breakdown, pre-trade risk, and position scenario classes.
- [x] Show the top score components and risk status on opportunity cards.
- [x] Show a richer pre-trade panel in the Today selected opportunity section.

### Task 5: Verification

- [x] Run targeted recommendation-quality tests.
- [x] Run full backend tests and ruff.
- [x] Run frontend build and UI checks.
