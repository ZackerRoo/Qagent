# Qagent Production Readiness Pack Design

## Goal

Complete the practical capabilities needed for a usable research agent after scan history and outcome replay:

- show data provider readiness;
- rank strategies from replayed outcomes;
- suggest actionable alert rules from opportunity cards;
- evaluate insider/institutional confirmation from SEC filing data when available.

## Scope

This pack adds product-grade support around the existing research loop. It does not fabricate unavailable data. Options flow, live short interest, 北向资金, and 龙虎榜 remain missing-data areas until reliable providers are connected.

## Provider Readiness

Expose a `/api/provider-status` endpoint showing each provider, whether it is configured, and which capabilities it supports. The Settings page renders this so users understand why a strategy is passing, pending, or missing-data.

Providers:

- fixture;
- yfinance market data;
- akshare/baostock A-share market data;
- Alpha Vantage;
- FMP;
- Finnhub;
- SEC EDGAR;
- CNINFO;
- Tushare.

## Strategy Performance

Use saved opportunity snapshots plus replayed outcomes to produce a strategy leaderboard. The leaderboard groups by `primary_strategy_id` and reports sample count, target-hit count, positive-return count, pending count, average forward returns, max drawdown, and max runup.

This turns historical scans into a feedback loop for which strategies are working.

## Alert Suggestions

Generate suggested alert rules from recent opportunity snapshots:

- entry trigger: price crosses above trigger;
- stop guard: price crosses below initial stop;
- target reached: price crosses above target 1.

Suggestions are returned separately from saved rules so users can inspect before saving.

## Ownership Confirmation

Use SEC filings already collected by the strategy-data provider. If recent `4`, `13F-HR`, `SC 13G`, or buyback-related filings exist, score `insider_institutional_confirmation` as watch/passed. Without those filings, keep missing-data.

## UI

- Settings page: provider readiness table.
- History page: strategy performance leaderboard.
- Alerts page: suggested alert rules table.

## Testing

Tests cover provider status API, strategy performance aggregation, alert suggestions, SEC ownership strategy scoring, and frontend build.
