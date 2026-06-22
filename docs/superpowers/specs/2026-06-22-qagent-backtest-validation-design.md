# Qagent Backtest Validation Design

## Goal

Build a historical validation layer that answers whether Qagent's opportunity cards and primary strategies would have worked on prior scan dates. The feature should be usable with fixture data immediately and with free providers when enough historical bars are available.

## Product Shape

The first backtest version is event-level, not broker-level. It does not simulate cash, position sizing, partial fills, slippage, commissions, or portfolio rebalancing. It repeatedly runs the existing scan stack on historical as-of dates, records the cards that would have existed on those dates, then replays forward outcomes with the existing outcome engine.

This matches Qagent's current role: a research radar that suggests setups, entry levels, stops, targets, and strategy evidence. The validation question is therefore:

- On historical scan dates, how often did a strategy generate setup cards?
- After those cards appeared, what were 5/10/20/60-day forward returns?
- Did the stored target or stop get touched in the available future window?
- Which primary strategies look strongest or weakest in the sample?

## Architecture

Add a focused `qagent.backtesting.engine` module. It will:

1. Build actual scan dates from available daily bars.
2. Run `run_daily_scan` as of each scan date, using only bars and strategy data available through that date.
3. Convert each generated card into an in-memory `OpportunitySnapshotRecord`.
4. Reuse `compute_opportunity_outcome` and `summarize_strategy_performance`.
5. Return a serializable `BacktestResult` with summary, signals, performance, and data-health metadata.

`run_daily_scan` should accept optional `start` and `end` arguments so the backtest can enforce an as-of window without duplicating the scan engine.

## API

Add:

```text
GET /api/backtest
```

Query parameters:

- `provider`: `fixture` or `free`, default `fixture`.
- `symbols`: comma-separated instruments, default follows the provider universe.
- `start`: ISO date. Fixture default uses `2026-01-15`.
- `end`: ISO date. Fixture default uses `2026-03-20`.
- `step_days`: positive integer, default `5`, capped to a practical range.
- `limit`: max returned signal rows, default `100`.

Response:

```json
{
  "summary": {
    "provider": "fixture",
    "symbols": ["US:TEST", "CN:000001"],
    "start": "2026-01-15",
    "end": "2026-03-20",
    "scan_count": 12,
    "evaluated_signals": 8,
    "completed_signals": 6,
    "target_hit_rate": 0.5,
    "positive_rate_10d": 0.67,
    "avg_return_10d": 2.41,
    "max_drawdown_pct": -4.2,
    "max_runup_pct": 8.9
  },
  "performance": [],
  "signals": [],
  "data_health": {}
}
```

## Frontend

Use the existing History page because this is validation of historical scans. Add a Backtest panel above Strategy Performance:

- Run button.
- Summary cards for scans, signals, completed, target-hit rate, positive 10D, avg 10D, max drawdown, max runup.
- Strategy table from `performance`.
- Recent signal table with date, ticker, strategy, status, 5D/10D/20D, target/stop outcome.

Keep controls minimal for now: use the current global data mode and symbols from the app. The API still supports `start`, `end`, and `step_days` for direct experiments.

## Error Handling

- Invalid provider should remain a `400` from provider factory handling.
- Invalid or reversed date ranges should return `400`.
- Missing bars should produce an empty result with `data_health` explaining no scan dates.
- Future outcomes without enough bars should be included as `pending`, not omitted.

## Testing

Backend tests:

- `run_daily_scan` respects caller-provided `start` and `end`.
- Backtest generates scan dates from available fixture trading dates.
- Backtest result contains summary, signal outcomes, and strategy performance.
- API route returns the same shape and rejects invalid date ranges.

Frontend verification:

- TypeScript build must pass.
- History page must render backtest responses without adding a separate route.

## Boundaries

This feature intentionally avoids full portfolio accounting. That should come after event-level strategy validity is visible. It also does not fabricate missing fundamentals, options flow, 北向资金, 龙虎榜, or live short-interest data.
