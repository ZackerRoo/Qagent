# Qagent Real Data Provider Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for each behavior change.

**Goal:** Add real-data provider adapters so Qagent can move from fixture-only strategy data toward production US and A-share research data without hard-coding API keys or fabricating missing conclusions.

**Architecture:** Keep market OHLCV providers separate from strategy-data providers. Add normalized records for earnings, SEC filings, and A-share announcements; implement FMP, Finnhub, SEC EDGAR, CNINFO, and Tushare/empty provider adapters; compose them through a factory that degrades gracefully when keys are absent.

**Tech Stack:** Python 3.11, Pydantic, httpx, pandas, pytest.

---

## Tasks

- [x] Add provider-contract tests for FMP earnings normalization.
- [x] Add provider-contract tests for Finnhub earnings normalization.
- [x] Add provider-contract tests for SEC EDGAR filings normalization.
- [x] Add provider-contract tests for CNINFO announcement normalization.
- [x] Add provider-factory tests for keyless graceful fallback and configured providers.
- [x] Implement normalized models for filings and announcements.
- [x] Implement HTTP provider adapters and composite strategy-data provider.
- [x] Expose provider health/errors through daily scan `data_health`.
- [x] Update docs with environment variables and source mapping.
- [x] Run backend tests, ruff, frontend build, and diff check.
