# Development Workflow

## Backend

```bash
cd backend
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -v
```

Run the backend:

```bash
../scripts/dev_backend.sh
```

The API runs at `http://127.0.0.1:8000/api`.

## Frontend

The local npm registry may need to be overridden if a corporate registry is configured:

```bash
cd frontend
npm install --registry=https://registry.npmjs.org
npm run build
npm run dev
```

The dashboard runs at `http://127.0.0.1:5173`.

## Current Data Mode

The system supports two market-data modes:

- `fixture`: deterministic local bars for `US:TEST` and `CN:000001`.
- `free`: `yfinance` for US stocks and `akshare` with `baostock` fallback for China A-shares.

Fixture data keeps tests stable. Free providers are implemented behind adapter contracts and are mocked in unit tests.

Market-data providers are wrapped by `CachedMarketDataProvider` in `qagent.providers.factory`. The cache persists normalized daily OHLCV bars in SQLite through `MarketDataCacheRepository`, keyed by provider mode, symbol, and trade date. The wrapper records coverage spans for requested ranges, so repeated scans and backtests can reuse rows without hitting free upstream providers every time. It still filters returned bars by the caller's requested date window, which preserves the existing no-lookahead behavior.

Useful cache routes:

```bash
curl 'http://127.0.0.1:8000/api/data-cache'
curl 'http://127.0.0.1:8000/api/data-cache?provider=free'
curl -X DELETE 'http://127.0.0.1:8000/api/data-cache?provider=free'
```

Scan `data_health` includes `market_cache`, `market_cache_hits`, `market_cache_misses`, and `market_cache_rows` when the provider is cache-backed. The Settings page also shows cache summaries for the selected data mode.

## Daily Brief

The Brief page and `/api/daily-brief` provide the main daily research readout:

```bash
curl 'http://127.0.0.1:8000/api/daily-brief?provider=fixture&include_news=false'
curl 'http://127.0.0.1:8000/api/daily-brief?provider=free&symbols=US:AAPL,US:NVDA,CN:000001'
curl -X POST 'http://127.0.0.1:8000/api/daily-brief/runs?provider=fixture&include_news=false'
curl 'http://127.0.0.1:8000/api/daily-brief/runs'
```

The API composes existing Qagent capabilities instead of inventing a new ranking stack:

- `run_daily_scan` for current opportunity cards and entry/exit levels.
- `run_historical_backtest` for strategy validation.
- `FreeCatalystProvider` plus catalyst hypotheses when `include_news=true`.
- Stored positions plus latest prices for risk alerts.
- Provider readiness for missing optional data caveats.

The response contains `headline`, `top_opportunities`, `entry_watch`, `risk_alerts`, `catalyst_watch`, `strategy_validation`, `data_caveats`, `next_steps`, and `data_health`.

`qagent.briefing.daily` is intentionally a pure service module: it accepts precomputed scan/backtest/catalyst/risk/provider objects and returns Pydantic models. Network access, SQLite reads, and provider construction stay in the API layer.

Saved brief runs are stored in SQLite as `brief_runs` with summary columns and full brief JSON. Useful routes:

```bash
curl -X POST 'http://127.0.0.1:8000/api/daily-brief/runs?provider=fixture&include_news=false'
curl 'http://127.0.0.1:8000/api/daily-brief/runs'
curl 'http://127.0.0.1:8000/api/daily-brief/runs/<brief_id>'
curl 'http://127.0.0.1:8000/api/daily-brief/runs/<brief_id>/markdown'
curl -X POST 'http://127.0.0.1:8000/api/daily-brief/runs/<brief_id>/deliveries?channel=markdown&recipient=local'
curl 'http://127.0.0.1:8000/api/deliveries?status=queued'
curl -X POST 'http://127.0.0.1:8000/api/deliveries/<delivery_id>/mark-sent'
curl -X POST 'http://127.0.0.1:8000/api/automation/run?provider=fixture&symbols=US:TEST&include_news=false&queue_brief=true&run_backtest=true'
```

`qagent.briefing.export.render_daily_brief_markdown` turns a saved brief into a compact Markdown research note. `delivery_outbox` stores queued/sent delivery records with channel, recipient, subject, Markdown, payload metadata, and timestamps. `qagent.delivery.senders.send_pending_deliveries` currently supports local Markdown-file delivery and webhook JSON POST delivery.

The CLI can generate, save, queue, and print a brief for local schedulers:

```bash
cd backend
.venv/bin/python -m qagent.cli daily-brief --provider fixture --no-news --save --queue --print-markdown
.venv/bin/python -m qagent.cli run-all --provider fixture --symbols US:TEST --no-news --queue-brief --run-backtest
.venv/bin/python -m qagent.cli send-outbox --channel markdown --output-dir ../data/outbox
```

`run-all` saves a scan run, saves a daily brief, can queue the brief, can evaluate alert rules, and can run event-level backtest validation in one command. `send-outbox` processes queued local deliveries. `markdown` and `file` deliveries are written to Markdown files; `webhook` deliveries require `--webhook-url`. The dashboard Settings page can trigger the same automation through `/api/automation/run`.

## Strategy Engine

The daily scan now evaluates a registered strategy stack before building opportunity cards.

Free-data-ready strategies:

- `trend_momentum_stage2`
- `breakout_volume_confirmation`
- `healthy_pullback`
- `gf_dma_health`
- `pead_earnings_drift` when earnings actuals, estimates, announcement timing, and daily bars are available
- `analyst_revision_momentum` when current/prior EPS, revenue, or target-price estimates and revision dates are available
- `tam_adj_peg_growth` when fundamentals, valuation multiples, and the free-data TAM proxy are available
- `bayesian_intrinsic_growth` when fundamentals and valuation-derived growth priors are available
- `insider_institutional_confirmation` when SEC filing metadata includes Form 3/4/5, 13F, 13D, or 13G confirmation

Registered but data-limited strategies:

- `catalyst_financial_transmission`
- `sector_rotation_regime`
- `short_squeeze_risk`
- `options_flow_confirmation`

Data-limited strategies must appear as `missing_data` unless their required provider fields are available. This prevents the agent from inventing PEAD, analyst revision, valuation, options-flow, or ownership conclusions from price data alone.

The fixture strategy-data provider reads `backend/qagent/providers/fixture_data/earnings_events.csv`, `fundamental_snapshots.csv`, and `analyst_insights.csv`; backend tests keep their own fixtures under `backend/tests/fixtures`. In fixture mode, `US:TEST` has a complete earnings event, growth/valuation snapshot, and analyst revision record; `CN:000001` intentionally lacks estimates and remains missing-data for PEAD.

Free mode composes real-data adapters:

- SEC EDGAR filings through `data.sec.gov` using `QAGENT_SEC_USER_AGENT`; the adapter resolves ticker to CIK through SEC's company ticker list.
- CNINFO announcement search for A-share announcements.
- Alpha Vantage company overview and earnings history when `QAGENT_ALPHA_VANTAGE_API_KEY` is set.
- FMP earnings calendar when `QAGENT_FMP_API_KEY` is set.
- FMP key metrics, ratios, and analyst estimates when `QAGENT_FMP_API_KEY` is set.
- Finnhub earnings calendar, basic financials, and recommendation trends when `QAGENT_FINNHUB_API_KEY` is set.
- Tushare placeholder/config entry when `QAGENT_TUSHARE_TOKEN` is set.

Optional environment variables:

```bash
export QAGENT_FMP_API_KEY="..."
export QAGENT_FINNHUB_API_KEY="..."
export QAGENT_ALPHA_VANTAGE_API_KEY="..."
export QAGENT_TUSHARE_TOKEN="..."
export QAGENT_SEC_USER_AGENT="Qagent research app you@example.com"
```

The scan response surfaces `strategy_data_provider`, `strategy_filings`, `strategy_announcements`, `strategy_fundamentals`, `strategy_analyst_insights`, and `strategy_data_errors` in `data_health`.

Opportunity cards now include:

- `strategy_score`: max score from the strategy stack and signal stack.
- `rank_score`: ranking score combining strategy strength, data completeness, risk/reward, and active strategy count.
- `factor_score`, `factor_rank`, `factor_flags`, and `factor_exposures`: cross-sectional factor quality, ranking, caveats, and attribution.
- `rank_reasons`: readable reasons behind the ranking.
- `decision`: research action, conviction score, component scores, suggested risk budget, failure conditions, and verification checks.
- Strategy-specific trade plans for breakout, healthy pullback, and PEAD.

## Multifactor Ranking

The factor layer lives in `qagent.factors`. It ranks the scanned universe using only fields available from daily OHLCV bars:

- Momentum: 20/60/120-day price strength.
- Trend quality: moving-average alignment and distance control.
- Liquidity: recent traded value and volume stability.
- Low risk: lower realized volatility and drawdown pressure.
- Reversal: avoids chasing names with very stretched short-term moves.

`run_daily_scan` computes factor rankings once across the scan universe and attaches the matching factor record to each card and scan item. Final opportunity ordering uses a combined score: strategy/card ranking plus factor ranking. This is intentionally different from a black-box “AI prediction”: the UI can show each factor score, weight, raw value, warning flags, and missing-data notes.

Useful routes:

```bash
curl 'http://127.0.0.1:8000/api/factors?provider=fixture'
curl 'http://127.0.0.1:8000/api/factors?provider=free&symbols=CN:000001,CN:600519'
curl 'http://127.0.0.1:8000/api/factors/backtest?provider=fixture&forward_days=20&step_days=20&top_n=3'
```

The factor backtest freezes historical factor rankings at each sampled scan date, selects the top-ranked names, and measures forward returns after the configured horizon. It is a factor validation study, not a portfolio simulator.

## Research Decision Layer

Every generated opportunity card includes a deterministic decision object:

- `action`: `candidate_entry`, `watch_trigger`, `wait_pullback`, or `avoid`.
- `conviction_score`: weighted score from strategy quality, risk/reward, data quality, execution quality, and catalyst support.
- `suggested_risk_pct`: research risk budget percentage derived from conviction and data quality.
- `failure_conditions`: stop/no-chase/time-stop conditions that invalidate the setup.
- `verification_checks`: trigger, confirmation, missing-data, caveat, and sizing checks.

The decision layer lives in `qagent.recommendations.decision`. It only uses structured Qagent evidence already present on the card. Missing optional data lowers data quality and sizing, but it does not automatically discard a setup when a core strategy, trigger, stop, and target are present.

## Scan History And Outcome Replay

Dashboard scans through `/api/opportunities` are persisted to SQLite:

- `scan_runs` records provider, mode, requested symbols, counts, data-health metadata, and created time.
- `opportunity_snapshots` records one saved opportunity card per scan, including signal date, latest close, primary strategy, scores, trigger, stop, target, and full card JSON.

Useful routes:

```bash
curl 'http://127.0.0.1:8000/api/scan-runs'
curl 'http://127.0.0.1:8000/api/opportunity-history?instrument_id=US:TEST'
curl 'http://127.0.0.1:8000/api/outcomes?provider=fixture'
curl 'http://127.0.0.1:8000/api/strategy-performance?provider=fixture'
curl 'http://127.0.0.1:8000/api/alert-suggestions'
```

Outcome replay uses daily OHLCV bars to calculate 5/10/20/60-day forward returns, max drawdown, max runup, and a status of `target_1_hit`, `stopped`, `working`, `lagging`, or `pending`.

Strategy performance groups replayed outcomes by `primary_strategy_id` and reports sample count, pending/completed counts, target-hit rate, positive 10-day rate, average forward returns, max drawdown, and max runup. Alert suggestions derive draft entry, stop, and target rules from persisted opportunity snapshots.

## Paper Forward Testing

Paper trades are research-only simulated trades stored separately from manual portfolio positions:

```bash
curl -X POST 'http://127.0.0.1:8000/api/paper-trades/seed?provider=fixture&limit=50'
curl -X POST 'http://127.0.0.1:8000/api/paper-trades/update?provider=fixture'
curl 'http://127.0.0.1:8000/api/paper-trades'
```

`seed` creates one paper trade per saved opportunity snapshot that has a signal date and trigger price. It de-duplicates by `source_snapshot_id`. `update` pulls provider OHLCV bars from the signal date forward, moves trades from `pending` to `open` when the trigger is crossed, and closes them as `target_1_hit`, `stopped`, or `time_exit`. Same-bar stop/target conflicts use conservative stop-first ordering. The Portfolio page shows the paper-forward table and summary metrics.

`run-all` and `/api/automation/run` seed and update paper trades by default, so daily automation keeps the forward test current without manual clicks.

## Universes And Alert Runner

Universe APIs merge built-in starter pools with user-saved custom pools:

```bash
curl 'http://127.0.0.1:8000/api/universes'
curl 'http://127.0.0.1:8000/api/universes/free_default'
curl -X POST 'http://127.0.0.1:8000/api/universes' \
  -H 'content-type: application/json' \
  -d '{"universe_id":"custom_ai_pool","name":"Custom AI Pool","description":"Editable pool","market_scope":"mixed","tags":["custom"],"symbols":["US:NVDA","US:MSFT"]}'
```

Built-in universes are starter pools, not live index or ETF constituents. Users can edit and save their own pools from Settings. The top scan bar can load a selected universe into the symbol input.

The alert runner evaluates stored alert rules against provider snapshots and can queue a Markdown outbox item:

```bash
curl -X POST 'http://127.0.0.1:8000/api/alerts/run?provider=fixture&queue=true&recipient=local'
curl 'http://127.0.0.1:8000/api/deliveries?status=queued'
```

This uses the same alert rules as `/api/alerts/evaluate`, but it fetches latest prices itself instead of requiring manual prices.

## Event-Level Backtesting

The backtest engine reruns Qagent scans on historical trading dates and validates the generated opportunity cards with forward bars:

```bash
curl 'http://127.0.0.1:8000/api/backtest?provider=fixture&start=2026-01-30&end=2026-03-20&step_days=5'
curl 'http://127.0.0.1:8000/api/backtest?provider=free&symbols=US:AAPL,US:NVDA,CN:000001&step_days=5'
```

Backtesting is event-level. It measures opportunity-card outcomes, not portfolio returns. Each scan date calls `run_daily_scan` with bars and strategy data capped at that date, then outcome replay uses future daily bars to calculate 5/10/20/60-day returns, target/stop status, max drawdown, and max runup.

The response includes:

- `summary`: scan count, evaluated signals, completed signals, target-hit rate, positive 10-day rate, average forward returns, max drawdown, and max runup.
- `performance`: strategy-level replay metrics grouped by primary strategy.
- `signals`: recent historical opportunity events and their forward outcomes.
- `data_health`: provider, signal count, scan dates, and the `lookahead_guard` flag.

The History page exposes this through the Backtest Validation panel. Fixture mode uses the deterministic fixture universe; free mode uses the current dashboard symbol input.

## Portfolio-Level Backtesting

Portfolio backtesting converts historical Qagent signals into account-level trades:

```bash
curl 'http://127.0.0.1:8000/api/portfolio-backtest?provider=fixture&start=2026-01-30&end=2026-03-20&step_days=5'
curl 'http://127.0.0.1:8000/api/portfolio-backtest?provider=free&symbols=US:AAPL,US:NVDA,CN:000001&step_days=5&initial_capital=100000&risk_per_trade_pct=1&max_positions=5'
```

The engine first calls `run_historical_backtest`, so signal generation still uses bars capped at each scan date. It then waits for future daily bars to trigger entry, applies fixed-risk sizing, `max_positions`, transaction cost, slippage, stop/target/time exits, and reports:

- `summary`: initial/final equity, total return, max drawdown, trade count, win rate, profit factor, average trade return, and exposure.
- `trades`: entry/exit dates, stop/target/time exit reason, shares, costs, net P/L, and return.
- `equity_curve`: realized-equity points and drawdown.
- `data_health`: provider, source signal counts, portfolio model, and lookahead guard.

This is a research-grade portfolio simulator, not broker-grade execution. It uses daily OHLCV bars and conservative same-day stop/target ordering.

## Provider Readiness

Provider status is available through the Settings page and `/api/provider-status`:

```bash
curl 'http://127.0.0.1:8000/api/provider-status'
```

The response distinguishes built-in free providers from optional API-key-backed providers. Missing optional keys are reported as `missing_config`; they do not stop fixture/free scans, but strategies that require those fields remain `missing_data`.

## Useful API Checks

```bash
curl 'http://127.0.0.1:8000/api/opportunities?provider=fixture'
curl 'http://127.0.0.1:8000/api/opportunities?provider=free&symbols=US:AAPL,CN:000001'
curl 'http://127.0.0.1:8000/api/daily-brief?provider=fixture&include_news=false'
curl -X POST 'http://127.0.0.1:8000/api/daily-brief/runs?provider=fixture&include_news=false'
curl 'http://127.0.0.1:8000/api/backtest?provider=fixture'
curl 'http://127.0.0.1:8000/api/portfolio-backtest?provider=fixture'
curl 'http://127.0.0.1:8000/api/provider-status'
curl 'http://127.0.0.1:8000/api/data-cache?provider=fixture'
curl 'http://127.0.0.1:8000/api/strategy-performance?provider=fixture'
curl 'http://127.0.0.1:8000/api/alert-suggestions'
curl 'http://127.0.0.1:8000/api/catalysts?symbols=US:AAPL&limit=5'
curl 'http://127.0.0.1:8000/api/portfolio?provider=fixture'
```

## Verification

Run these before pushing:

```bash
cd backend
.venv/bin/python -m pytest -v
.venv/bin/python -m ruff check .

cd ../frontend
npm run build
```

## Known Limitations

- No automated trading or broker execution.
- No external push sender yet; the local delivery outbox is implemented for scheduled jobs and future senders.
- Free data may be delayed or incomplete.
- The market-data cache improves repeatability and reduces free-provider calls, but it is not a live market data entitlement or real-time quote store.
- Portfolio backtests include sizing, costs, slippage, stops, targets, time exits, and realized equity curves, but they are not broker-grade simulations with intraday fills, taxes, borrow fees, or live execution constraints.
- Outcome replay uses daily bars and cannot prove intraday ordering between a stop and target.
- PEAD is implemented when earnings actuals and estimates are available, but production free-data coverage depends on FMP/Finnhub/Alpha Vantage or another earnings provider.
- Analyst revision, TAM-PEG, and Bayesian valuation are implemented with normalized free-source fields, but results are only as good as the upstream fundamentals and estimates. Alpha Vantage ratings are current snapshots; FMP analyst-estimate history is needed for true revision scoring.
- Options flow, 北向资金, 龙虎榜, live short interest, and richer announcement parsing are registered or modeled as missing-data edges, but they are not production-grade without reliable provider data.
- Opportunity cards are research artifacts, not personalized investment advice.
