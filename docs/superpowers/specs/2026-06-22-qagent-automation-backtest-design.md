# Qagent Automation And Portfolio Backtest Design

## Goal

Complete the next product loop for Qagent by turning generated research briefs into a trackable delivery queue and by adding portfolio-level backtest validation on top of existing signal-level backtests.

## Scope

- Add a durable delivery outbox for saved daily briefs. The outbox stores channel, recipient, subject, rendered Markdown, status, timestamps, and payload metadata so local cron or future email/chat adapters can send from a controlled queue.
- Add API endpoints to enqueue a saved brief, list queued/sent deliveries, and mark a delivery as sent.
- Add a CLI path that can generate a daily brief, save it, enqueue it, and optionally print the Markdown for a scheduler.
- Add a portfolio-level backtest engine that converts historical opportunity signals into position-sized trades, applies stop/target/time exits, transaction cost and slippage, and reports equity curve plus account-level metrics.
- Add API and dashboard access for the portfolio backtest and delivery queue.

## Constraints

- Development remains on free/local data providers. Paid broker execution, real account linkage, and live external messaging are out of scope.
- The system must keep investment-safety language: outputs are research context, not guaranteed recommendations or personalized investment advice.
- Backtests must keep the existing no-lookahead guard: signals are generated from bars available up to each scan date, then evaluated only on later bars.

## Data Flow

Daily brief generation produces a saved `BriefRunRecord`. A delivery request renders the saved payload to Markdown and stores a `DeliveryOutboxRecord` with `queued` status. Later a sender can read queued items and mark them `sent`.

Portfolio backtest uses `run_historical_backtest` for research signals, then fetches forward bars per instrument and simulates trade exits. Summary metrics are derived from closed trades and an equity curve.

## Testing

Backend tests cover repository persistence, API behavior, CLI behavior, and portfolio backtest metrics. Frontend verification is by TypeScript build.
