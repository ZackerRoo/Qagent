# Qagent Strategy Engine Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Upgrade Qagent from signal-based opportunity cards into a strategy-driven opportunity system that can explain which strategy fired, what data is missing, how the setup should be traded, and how similar signals performed historically.

**Architecture:** Add a backend strategy layer between signals and opportunity cards. Free-data strategies produce scored evaluations; commercial-data strategies are registered and reported as missing-data instead of being fabricated. Daily scans return strategy evaluations and strategy health, and the frontend renders a Strategy Stack and health summary.

**Tech Stack:** Python 3.11, Pydantic, pandas, FastAPI, pytest, React, TypeScript, Vite.

---

## File Structure

Create:

```text
backend/qagent/strategies/
  __init__.py
  models.py
  registry.py
  evaluator.py
  health.py
backend/tests/test_strategy_registry.py
backend/tests/test_strategy_evaluator.py
backend/tests/test_strategy_health.py
```

Modify:

```text
backend/qagent/domain/models.py
backend/qagent/cards/generator.py
backend/qagent/jobs/daily_scan.py
backend/qagent/api/routes.py
backend/tests/test_card_generation.py
backend/tests/test_jobs.py
backend/tests/test_api_opportunities.py
frontend/src/types.ts
frontend/src/components/OpportunityTable.tsx
frontend/src/components/OpportunityDetail.tsx
frontend/src/pages/Opportunities.tsx
frontend/src/styles.css
README.md
```

## Task 1: Strategy Registry

- [x] Write failing tests proving the default registry includes trend momentum, breakout volume, healthy pullback, GF-DMA, catalyst transmission, PEAD, analyst revisions, TAM-adjusted PEG, Bayesian growth valuation, sector rotation, and risk squeeze strategies.
- [x] Verify those tests fail because `qagent.strategies` does not exist.
- [x] Implement strategy definition models and the default registry.
- [x] Re-run the registry tests.

## Task 2: Strategy Evaluation

- [x] Write failing tests proving existing signal stacks produce scored evaluations for trend momentum, breakout volume, healthy pullback, and GF-DMA.
- [x] Write failing tests proving PEAD, analyst revisions, TAM-PEG, Bayesian valuation, options flow, and insider/institutional confirmation are marked `missing_data` when the free-data scan lacks required inputs.
- [x] Verify those tests fail for missing evaluator behavior.
- [x] Implement the evaluator with explainable preconditions, triggers, confirmations, invalidations, evidence, missing data, and score components.
- [x] Re-run evaluator tests.

## Task 3: Opportunity Cards And Scan API

- [x] Write failing tests proving opportunity cards include `strategy_evaluations`, `primary_strategy_id`, and `strategy_score`.
- [x] Write failing tests proving `/api/opportunities` returns strategy health and scan items include the number of strategies that passed, watched, and missed data.
- [x] Verify the tests fail against the current card/API payload.
- [x] Connect strategy evaluations into daily scan and card generation.
- [x] Re-run card, job, and API tests.

## Task 4: Strategy Health And Historical Outcomes

- [x] Write failing tests proving strategy health computes sample count, win rate, average forward return, max drawdown proxy, and readiness labels from forward returns.
- [x] Verify those tests fail because strategy health is not implemented.
- [x] Implement deterministic health aggregation from current fixture scans and forward-return windows.
- [x] Expose health in the opportunities payload.
- [x] Re-run health and API tests.

## Task 5: Frontend Strategy Stack

- [x] Extend TypeScript types for strategy evaluations and health.
- [x] Show primary strategy and strategy score in the opportunity table.
- [x] Show detailed Strategy Stack and missing-data strategies in the opportunity detail panel.
- [x] Show strategy health summary beside scan coverage.
- [x] Run the frontend build.

## Task 6: Documentation And Verification

- [x] Update README with the strategy engine contract, free-data limits, and how to run scans.
- [x] Run backend tests, ruff, frontend build, and `git diff --check`.
- [x] Commit and push the feature branch.
