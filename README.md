# Qagent

Qagent is a research-first US + China A-share opportunity radar. It scans symbols, evaluates strategy stacks, builds opportunity cards, explains signal evidence, tracks entry/exit scenarios, monitors positions, evaluates alerts, and turns news into catalyst hypotheses.

It is not an auto-trading or direct stock-picking system. The product is designed to make market research testable: every card should show data source, primary strategy, missing-data strategies, signal evidence, trigger, stop, target, risk scenario, and the verification path behind any news catalyst.

## What Works Now

- US + A-share scanning with fixture data and free providers.
- Persistent market-data cache for fixture/free providers, with cache hit/miss data-health fields and Settings-page cache inspection.
- Daily Brief page and `/api/daily-brief` research digest combining opportunities, entry levels, catalysts, portfolio risk, data caveats, and strategy validation.
- Saved brief runs with history, detail retrieval, and Markdown export for push-ready workflows.
- Delivery outbox for saved briefs and alert runs, with queued/sent status plus local Markdown-file and webhook sender adapters.
- One-command automation runner for scan history, daily brief save/queue, optional alerts, optional backtest validation, and optional outbox sending.
- US free market data via `yfinance`.
- A-share free market data via `akshare`, with `baostock` fallback.
- Strategy registry covering trend momentum, breakout + volume, healthy pullback, GF-DMA health, catalyst transmission, PEAD, analyst revisions, TAM-adjusted PEG, Bayesian growth valuation, sector regime, short squeeze risk, options flow, and insider/institutional confirmation.
- Strategy-data provider contract for earnings events, SEC filings, A-share announcements, fundamentals, valuation multiples, and analyst context.
- Real-data strategy adapters for Alpha Vantage fundamentals/earnings/ratings, FMP earnings/fundamentals/analyst estimates, Finnhub earnings/fundamentals/recommendations, SEC EDGAR filings, CNINFO announcements, and Tushare configuration.
- Free-data strategy evaluator for trend momentum, breakout + volume, healthy pullback, GF-DMA health, PEAD when earnings actuals/estimates exist, analyst revision when estimate revisions exist, TAM-adjusted PEG, Bayesian growth valuation, and A-share limit-status confirmation.
- Missing-data handling for strategies that need unavailable estimates, options flow, insider transactions, institutional filings, sector breadth, or short-interest data.
- Strategy-specific trade plans for breakout, healthy pullback, and PEAD earnings drift.
- Opportunity cards with primary strategy, strategy score, rank score, ranking reasons, strategy stack, trigger, no-chase level, stop, targets, risk/reward, scenario percentages, and signal stack evidence.
- Research decision layer on each card with action, conviction score, component scores, suggested risk budget, failure conditions, and verification checks.
- Built-in and custom stock universes for starter theme pools and editable user pools.
- Scan coverage table showing `setup_ready`, `no_setup`, or `no_data` per symbol plus passed/watch/missing strategy counts.
- Strategy health summary with sample count, 10-day win rate, average 10/20-day forward return, max 10-day loss, and readiness labels.
- Persistent scan history and opportunity snapshots saved from dashboard scans.
- Outcome replay that computes forward returns, max drawdown, max runup, and target/stop/pending status from saved opportunity snapshots.
- Strategy performance leaderboard summarizing replayed outcomes by primary strategy.
- Event-level historical backtesting that reruns scans on prior dates and validates generated opportunity cards with forward outcomes.
- Portfolio-level historical backtesting that converts validated signals into position-sized trades, stop/target/time exits, costs, slippage, an equity curve, and account-level metrics.
- Provider readiness dashboard and API status for fixture, free market-data, SEC, CNINFO, and optional vendor feeds.
- Alert suggestions generated from saved opportunity trigger, stop, and target levels.
- Provider-backed alert runner that evaluates saved alert rules against latest snapshots and can queue Markdown notifications in the delivery outbox.
- Watchlist, positions, alert rules, alert evaluation, and portfolio risk view backed by SQLite.
- Catalyst Review using free news sources plus deterministic catalyst hypotheses and verification paths.
- SEC ownership confirmation strategy using available Form 3/4/5, 13F, 13D, and 13G filing metadata.
- Constrained Agent answers that refuse guaranteed returns and answer from structured strategy/card context.

## Run Locally

Backend:

```bash
cd backend
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
../scripts/dev_backend.sh
```

Frontend:

```bash
cd frontend
npm install --registry=https://registry.npmjs.org
../scripts/dev_frontend.sh
```

Open:

- Dashboard: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000/api`

Optional strategy-data keys:

```bash
export QAGENT_FMP_API_KEY="..."
export QAGENT_FINNHUB_API_KEY="..."
export QAGENT_ALPHA_VANTAGE_API_KEY="..."
export QAGENT_TUSHARE_TOKEN="..."
export QAGENT_SEC_USER_AGENT="Qagent research app you@example.com"
```

Without these keys, Qagent still runs. SEC EDGAR and CNINFO adapters remain available where possible, and unavailable vendor data is surfaced as missing data instead of inferred.

## Verify

```bash
cd backend
.venv/bin/python -m pytest -v
.venv/bin/python -m ruff check .

cd ../frontend
npm run build
```

## Key API Examples

```bash
curl 'http://127.0.0.1:8000/api/opportunities?provider=free&symbols=US:AAPL,CN:000001'
curl 'http://127.0.0.1:8000/api/daily-brief?provider=fixture&include_news=false'
curl -X POST 'http://127.0.0.1:8000/api/daily-brief/runs?provider=fixture&include_news=false'
curl 'http://127.0.0.1:8000/api/daily-brief/runs'
curl -X POST 'http://127.0.0.1:8000/api/daily-brief/runs/<brief_id>/deliveries?channel=markdown&recipient=local'
curl 'http://127.0.0.1:8000/api/deliveries?status=queued'
curl -X POST 'http://127.0.0.1:8000/api/automation/run?provider=fixture&include_news=false&queue_brief=true&run_backtest=true'
curl 'http://127.0.0.1:8000/api/scan-runs'
curl 'http://127.0.0.1:8000/api/outcomes?provider=fixture'
curl 'http://127.0.0.1:8000/api/strategy-performance?provider=fixture'
curl 'http://127.0.0.1:8000/api/backtest?provider=fixture&start=2026-01-30&end=2026-03-20&step_days=5'
curl 'http://127.0.0.1:8000/api/portfolio-backtest?provider=fixture&start=2026-01-30&end=2026-03-20&step_days=5'
curl 'http://127.0.0.1:8000/api/alert-suggestions'
curl 'http://127.0.0.1:8000/api/universes'
curl -X POST 'http://127.0.0.1:8000/api/alerts/run?provider=fixture&queue=true&recipient=local'
curl 'http://127.0.0.1:8000/api/provider-status'
curl 'http://127.0.0.1:8000/api/data-cache?provider=free'
curl -X DELETE 'http://127.0.0.1:8000/api/data-cache?provider=free'
curl 'http://127.0.0.1:8000/api/catalysts?symbols=US:AAPL&limit=5'
curl 'http://127.0.0.1:8000/api/portfolio?provider=fixture'
```

`/api/opportunities` returns `cards`, `items`, `strategy_health`, and `data_health`. Cards include `rank_score`, `rank_reasons`, and `decision`. The decision object is a research workflow: action, conviction score, component scores, suggested risk budget, trigger/stop/target references, failure conditions, and verification checks. Strategies that cannot be evaluated with the current free-data scan are returned with `status: "missing_data"` instead of fabricated scores.

Market-data provider calls are cached in SQLite by provider mode, symbol, and date. Scan `data_health` includes `market_cache`, hit/miss counts, and returned cache rows. `/api/data-cache` lists cached date ranges and source providers; `DELETE /api/data-cache` clears all or filtered cache rows.

`/api/daily-brief` is the daily readout. It composes the current scan, entry watch levels, optional news catalysts, position risk, provider caveats, and backtest validation. `/api/daily-brief/runs` saves and lists generated briefs; `/api/daily-brief/runs/{brief_id}/markdown` exports a saved brief as Markdown; `/api/daily-brief/runs/{brief_id}/deliveries` queues a saved brief in the local delivery outbox. `/api/opportunities` also records a scan run. `/api/scan-runs`, `/api/opportunity-history`, `/api/outcomes`, and `/api/strategy-performance` expose the saved research trail, daily-bar outcome replay, and strategy-level replay summary. `/api/backtest` runs event-level historical validation without saving records. `/api/portfolio-backtest` simulates account-level trades from historical signals. `/api/alert-suggestions` turns saved opportunity trigger/stop/target levels into draft alert rules.

CLI daily brief handoff:

```bash
cd backend
.venv/bin/python -m qagent.cli daily-brief --provider fixture --no-news --save --queue --print-markdown
.venv/bin/python -m qagent.cli run-all --provider fixture --symbols US:TEST --no-news --queue-brief --run-backtest
.venv/bin/python -m qagent.cli send-outbox --channel markdown --output-dir ../data/outbox
```

## Research Docs

See `docs/research/` for product, market, strategy, data-source, GitHub, and compliance research.
