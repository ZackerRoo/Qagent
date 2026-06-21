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

Registered but data-limited strategies:

- `catalyst_financial_transmission`
- `pead_earnings_drift`
- `analyst_revision_momentum`
- `tam_adj_peg_growth`
- `bayesian_intrinsic_growth`
- `sector_rotation_regime`
- `short_squeeze_risk`
- `options_flow_confirmation`
- `insider_institutional_confirmation`

Data-limited strategies must appear as `missing_data` unless their required provider fields are available. This prevents the agent from inventing PEAD, analyst revision, valuation, options-flow, or ownership conclusions from price data alone.

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
- PEAD, analyst revisions, TAM-PEG, Bayesian valuation, options flow, 北向资金, 龙虎榜, and richer announcement parsing are registered but not production-grade without the required provider data.
- Opportunity cards are research artifacts, not personalized investment advice.
