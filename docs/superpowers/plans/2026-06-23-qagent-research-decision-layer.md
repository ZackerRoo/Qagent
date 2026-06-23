# Qagent Research Decision Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic research actions and conviction scoring to each opportunity card and brief.

**Architecture:** Define decision models in `qagent.domain.models`, implement scoring in a focused `qagent.recommendations.decision` service, and call it from the existing card generator after the card is assembled. Brief and frontend components consume the embedded card decision rather than recalculating it.

**Tech Stack:** Pydantic, FastAPI JSON serialization, pytest, React, TypeScript, Vite.

---

### Task 1: Backend Decision Model

**Files:**
- Modify: `backend/qagent/domain/models.py`
- Create: `backend/qagent/recommendations/decision.py`
- Modify: `backend/qagent/cards/generator.py`
- Test: `backend/tests/test_recommendation_decision.py`
- Test: `backend/tests/test_card_generation.py`

- [ ] Write failing tests for `decision.action`, `conviction_score`, components, position sizing, failure conditions, and verification checks.
- [ ] Implement Pydantic models and deterministic decision service.
- [ ] Inject decisions into generated opportunity cards.
- [ ] Run targeted backend tests.

### Task 2: Brief Integration

**Files:**
- Modify: `backend/qagent/briefing/daily.py`
- Modify: `backend/qagent/briefing/export.py`
- Test: `backend/tests/test_daily_brief.py`

- [ ] Write failing tests for brief opportunity decision fields and Markdown decision text.
- [ ] Copy card decisions into brief opportunities and entry watch items.
- [ ] Include action and conviction in Markdown export.

### Task 3: API And Frontend

**Files:**
- Modify: `backend/tests/test_api_opportunities.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/OpportunityTable.tsx`
- Modify: `frontend/src/components/OpportunityDetail.tsx`
- Modify: `frontend/src/pages/Brief.tsx`
- Modify: `frontend/src/styles.css`

- [ ] Add API tests asserting opportunity card decisions are serialized.
- [ ] Add TypeScript decision types.
- [ ] Render action and conviction in opportunity table, detail panel, and brief top opportunities.
- [ ] Run frontend build.

### Task 4: Verification

- [ ] Run targeted backend tests.
- [ ] Run full backend pytest.
- [ ] Run ruff.
- [ ] Run frontend build.
- [ ] Leave changes local; do not push.
