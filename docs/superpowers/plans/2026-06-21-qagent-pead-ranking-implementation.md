# Qagent PEAD And Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for each behavior change.

**Goal:** Turn Qagent's next strategy slice into a usable research loop by adding strategy data contracts, a real PEAD evaluator for fixture/free-compatible data, strategy-specific trade plans, and opportunity ranking.

**Architecture:** Add a `strategy_data` package for earnings and later fundamentals/revisions providers. Feed strategy-data context into `StrategyEvaluator`, then let cards choose trade plans based on primary strategy and rank cards with an explainable ranking score.

**Tech Stack:** Python 3.11, Pydantic, pandas, FastAPI, pytest, React/TypeScript.

---

## Tasks

- [x] Add tests for earnings event models and fixture strategy-data provider.
- [x] Add tests for PEAD scoring when earnings actuals, estimates, announcement timestamp, and bars exist.
- [x] Add tests that PEAD stays `missing_data` when estimates or timestamps are absent.
- [x] Add tests for `build_pead_plan` and strategy-specific plan selection in cards.
- [x] Add tests for opportunity ranking fields, rank sorting, and API payload.
- [x] Implement strategy-data models/providers.
- [x] Implement PEAD evaluator and context wiring in daily scan.
- [x] Implement PEAD trade plan and card plan selection.
- [x] Implement opportunity ranking and frontend fields.
- [x] Update docs and run full verification.
