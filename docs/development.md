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
./scripts/dev_backend.sh
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

The default application uses deterministic fixture data for both markets:

- `US:TEST`
- `CN:000001`

Fixture data keeps tests stable. Free providers are implemented behind adapter contracts and should not be used in tests without mocks.

## Known Limitations

- No automated trading or broker execution.
- Free data may be delayed or incomplete.
- Analyst revisions, options flow, 北向资金, 龙虎榜, and richer announcement parsing are not production-ready in this slice.
- Opportunity cards are research artifacts, not personalized investment advice.
