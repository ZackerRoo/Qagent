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

Registered but data-limited strategies:

- `catalyst_financial_transmission`
- `sector_rotation_regime`
- `short_squeeze_risk`
- `options_flow_confirmation`
- `insider_institutional_confirmation`

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

## Useful API Checks

```bash
curl 'http://127.0.0.1:8000/api/opportunities?provider=fixture'
curl 'http://127.0.0.1:8000/api/opportunities?provider=free&symbols=US:AAPL,CN:000001'
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
- Free data may be delayed or incomplete.
- PEAD is implemented when earnings actuals and estimates are available, but production free-data coverage depends on FMP/Finnhub/Alpha Vantage or another earnings provider.
- Analyst revision, TAM-PEG, and Bayesian valuation are implemented with normalized free-source fields, but results are only as good as the upstream fundamentals and estimates. Alpha Vantage ratings are current snapshots; FMP analyst-estimate history is needed for true revision scoring.
- Options flow, 北向资金, 龙虎榜, short interest, and richer announcement parsing are registered but not production-grade without the required provider data.
- Opportunity cards are research artifacts, not personalized investment advice.
