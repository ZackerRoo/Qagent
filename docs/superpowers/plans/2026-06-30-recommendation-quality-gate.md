# Recommendation Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve Qagent recommendation quality before adding more alert automation.

**Architecture:** Add a backend quality profile on each opportunity card, apply A-share-specific scoring and hard gates after factor/strategy/market enrichment, then expose the result in Today opportunity cards. The frontend only renders backend explanations; it does not reimplement ranking logic.

**Tech Stack:** Python/Pydantic/FastAPI, pandas factor engine, React/TypeScript, existing pytest and frontend check scripts.

---

### Task 1: Backend Quality Profile

**Files:**
- Create: `backend/qagent/recommendations/quality_gate.py`
- Modify: `backend/qagent/domain/models.py`
- Test: `backend/tests/test_recommendation_quality_gate.py`

- [ ] Write failing tests for low-liquidity blocking, overextension warning, and A-share factor balance.
- [ ] Implement `RecommendationQualityProfile` and `RecommendationQualityCheck`.
- [ ] Apply score adjustments and quality tier labels.

### Task 2: Scan Integration

**Files:**
- Modify: `backend/qagent/jobs/daily_scan.py`
- Modify: `backend/qagent/jobs/full_market.py`
- Modify: `backend/qagent/api/routes.py`
- Test: `backend/tests/test_market_decision_layers.py`, `backend/tests/test_api_opportunities.py`

- [ ] Ensure every returned card has `recommendation_quality`.
- [ ] Re-sort after quality gate adjustments.
- [ ] Hydrate legacy cached payloads with the new profile.

### Task 3: Frontend Display

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/OpportunityTable.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/check-today-ui.mjs`

- [ ] Show quality tier, score, pass/warn/block counts, and top explanations on opportunity cards.
- [ ] Keep cards compact and Chinese-first.
- [ ] Add static UI checks.

### Task 4: Verification

**Commands:**
- `backend/.venv/bin/python -m pytest backend/tests -q`
- `backend/.venv/bin/python -m ruff check backend`
- `npm run build`
- `npm run check:today-ui`
- Chrome page check for no stuck scanning, no console errors, and visible quality explanations.
