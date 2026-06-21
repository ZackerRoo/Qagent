# Qagent

Qagent is a research-first US + China A-share opportunity radar. It scans symbols, builds opportunity cards, explains signal evidence, tracks entry/exit scenarios, monitors positions, evaluates alerts, and turns news into catalyst hypotheses.

It is not an auto-trading or direct stock-picking system. The product is designed to make market research testable: every card should show data source, signal evidence, trigger, stop, target, risk scenario, and the verification path behind any news catalyst.

## What Works Now

- US + A-share scanning with fixture data and free providers.
- US free market data via `yfinance`.
- A-share free market data via `akshare`, with `baostock` fallback.
- Opportunity cards with trigger, no-chase level, stop, targets, risk/reward, scenario percentages, and signal stack evidence.
- Scan coverage table showing `setup_ready`, `no_setup`, or `no_data` per symbol.
- Watchlist, positions, alert rules, alert evaluation, and portfolio risk view backed by SQLite.
- Catalyst Review using free news sources plus deterministic catalyst hypotheses and verification paths.
- Constrained Agent answers that refuse guaranteed returns and answer from structured card context.

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
curl 'http://127.0.0.1:8000/api/catalysts?symbols=US:AAPL&limit=5'
curl 'http://127.0.0.1:8000/api/portfolio?provider=fixture'
```

## Research Docs

See `docs/research/` for product, market, strategy, data-source, GitHub, and compliance research.
