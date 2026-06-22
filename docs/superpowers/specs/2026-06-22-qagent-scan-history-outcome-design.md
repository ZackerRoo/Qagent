# Qagent Scan History And Outcome Replay Design

## Goal

Qagent should remember each opportunity scan, preserve the exact opportunity cards produced at that moment, and replay later price action against those cards. This turns candidate recommendations into auditable records instead of one-off dashboard output.

## Scope

This iteration adds a persistent research memory:

- save scan runs from the `/api/opportunities` workflow;
- save one opportunity snapshot per generated card;
- expose recent scan runs, opportunity history, and replayed outcomes through APIs;
- show scan history and outcome replay in the dashboard.

This iteration does not add external push channels yet. Push notifications should use the same persisted scan snapshots in a later step.

## Data Model

`scan_runs` stores one row per scan with provider mode, requested symbols, counts, data-health metadata, and creation time.

`opportunity_snapshots` stores one row per opportunity card with run id, instrument id, signal date, latest close, primary strategy, strategy/rank scores, trigger, stop, target, and the full card JSON.

The snapshot keeps numeric fields as first-class columns for filtering and keeps full JSON for explainability.

## Outcome Replay

Outcome replay reads persisted opportunity snapshots and fetches historical daily bars from the selected market provider. For each snapshot it computes forward returns, max drawdown, max runup, and a simple outcome status:

- `target_1_hit` when later highs reach target 1;
- `stopped` when later lows reach the initial stop before target classification;
- `working` when forward return is positive but no target is hit;
- `lagging` when forward return is negative;
- `pending` when not enough future bars exist.

The replay is research evidence, not trading execution simulation. It uses daily OHLC bars and therefore cannot prove intraday ordering between stop and target.

## API

- `GET /api/scan-runs?limit=20`
- `GET /api/opportunity-history?instrument_id=US:AAPL&limit=50`
- `GET /api/outcomes?provider=fixture&instrument_id=US:AAPL&limit=50`

`GET /api/opportunities` records a scan run as a side effect because the dashboard scan action is the user-visible scan workflow.

## Dashboard

Add a `History` page with:

- recent scan runs;
- recent opportunity snapshots;
- outcome replay table.

The page uses existing restrained dashboard styling and does not introduce a new charting layer.

## Error Handling

If a provider cannot return future bars, the outcome row is marked `pending` and the response includes provider errors in `data_health`. Missing signal dates also become `pending`.

## Testing

Tests cover repository persistence, API persistence from `/api/opportunities`, outcome replay classification, and API response shape.
