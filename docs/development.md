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
```

`qagent.briefing.export.render_daily_brief_markdown` turns a saved brief into a compact Markdown research note. This is the intended handoff point for later email, Telegram, Feishu, or cron-based delivery.

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

The fixture strategy-data provider reads `backend/tests/fixtures/earnings_events.csv`, `fundamental_snapshots.csv`, and `analyst_insights.csv`. In fixture mode, `US:TEST` has a complete earnings event, growth/valuation snapshot, and analyst revision record; `CN:000001` intentionally lacks estimates and remains missing-data for PEAD.

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
- `rank_reasons`: readable reasons behind the ranking.
- Strategy-specific trade plans for breakout, healthy pullback, and PEAD.

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
curl 'http://127.0.0.1:8000/api/provider-status'
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
- No scheduled delivery or external push channel yet; Daily Brief is generated on demand.
- Free data may be delayed or incomplete.
- Backtests are event-level opportunity validation, not broker-grade portfolio simulations with cash, sizing, commissions, slippage, tax, or intraday fills.
- Outcome replay uses daily bars and cannot prove intraday ordering between a stop and target.
- PEAD is implemented when earnings actuals and estimates are available, but production free-data coverage depends on FMP/Finnhub/Alpha Vantage or another earnings provider.
- Analyst revision, TAM-PEG, and Bayesian valuation are implemented with normalized free-source fields, but results are only as good as the upstream fundamentals and estimates. Alpha Vantage ratings are current snapshots; FMP analyst-estimate history is needed for true revision scoring.
- Options flow, 北向资金, 龙虎榜, live short interest, and richer announcement parsing are registered or modeled as missing-data edges, but they are not production-grade without reliable provider data.
- Opportunity cards are research artifacts, not personalized investment advice.
