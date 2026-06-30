# Data Quality Closure Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task.

**Goal:** Make Qagent explain whether today’s recommendations are based on trustworthy A-share data and whether recent recommendations have statistically usable follow-through.

**Architecture:** Extend the existing market intelligence and follow-through centers instead of adding new pages. Backend models expose structured data-source checks and closure risk metrics; frontend panels render them in the existing dark quant dashboard.

**Tech Stack:** Python/FastAPI/Pydantic, pandas, pytest, React/TypeScript/Vite.

---

### Task 1: Data-Source Quality Checks

**Files:**
- Modify: `backend/qagent/research/market_intelligence.py`
- Test: `backend/tests/test_market_intelligence.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/MarketIntelligenceCenter.tsx`

- [ ] Add failing tests that expect structured checks for adjusted price, suspension, price limit, industry, liquidity, constituents, fund flow, announcements, and dragon-tiger data.
- [ ] Implement `DataSourceQualityCheck` and populate checks from data health, cards, and bars.
- [ ] Render top checks in the market intelligence panel.

### Task 2: Recommendation Closure Risk Metrics

**Files:**
- Modify: `backend/qagent/monitoring/outcomes.py`
- Modify: `backend/qagent/monitoring/followthrough.py`
- Test: `backend/tests/test_outcomes.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/RecommendationFollowThrough.tsx`

- [ ] Add failing tests for expectancy, payoff ratio, profit factor, max consecutive losses, and risk verdict.
- [ ] Compute metrics per 30/60/90 day closure window.
- [ ] Render risk metrics in recommendation follow-through.

### Task 3: Verification

- [ ] Run targeted backend tests.
- [ ] Run full backend tests and ruff.
- [ ] Run frontend build and UI checks.
