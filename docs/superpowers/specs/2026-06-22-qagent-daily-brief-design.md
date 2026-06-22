# Qagent Daily Brief Design

## Goal

Turn Qagent's existing scan, catalyst, alert, portfolio, provider, and backtest outputs into one daily research brief that a user can read before drilling into the dashboard.

## Product Shape

The brief is not a buy/sell recommendation. It is a structured research digest:

- Top opportunities ranked by `rank_score`.
- Entry watch items whose current plan has a trigger, stop, and target.
- Risk alerts from existing positions, especially stop breaches and target reaches.
- Catalyst watch items from news-derived hypotheses.
- Strategy validation from the event-level backtest.
- Data caveats from missing provider configuration and card caveats.
- Next-step checklist that tells the user what to verify before action.

The brief should answer the user's practical question: "What should I look at today, why, what are the levels, what could go wrong, and how validated is the setup?"

## Architecture

Add `qagent.briefing.daily` as a small service module. It accepts already-computed domain objects and returns Pydantic models. The service does not fetch network data or talk to SQLite. This keeps it deterministic and testable.

The API route `/api/daily-brief` composes the existing capabilities:

1. Run `run_daily_scan` through the current provider.
2. Generate alert suggestions from an in-memory snapshot view of the cards.
3. Run event-level backtest for strategy validation.
4. Pull stored positions and compute portfolio risk when positions exist.
5. Pull catalyst hypotheses through the current free catalyst provider.
6. Pull provider readiness.
7. Build and return the daily brief.

If catalyst fetching fails or returns nothing, the brief still works and surfaces that in `data_health`.

## API

Add:

```text
GET /api/daily-brief
```

Parameters:

- `provider`: `fixture` or `free`, default `fixture`.
- `symbols`: comma-separated universe, using provider defaults if omitted.
- `limit`: top opportunity limit, default `5`, capped at `20`.
- `include_news`: boolean, default `true`.

Response:

```json
{
  "generated_at": "2026-06-22T...",
  "provider": "fixture",
  "symbols": ["US:TEST", "CN:000001"],
  "headline": "2 setup-ready opportunities; 1 strategy validated by backtest.",
  "top_opportunities": [],
  "entry_watch": [],
  "risk_alerts": [],
  "catalyst_watch": [],
  "strategy_validation": [],
  "data_caveats": [],
  "next_steps": [],
  "data_health": {}
}
```

## Frontend

Add a `Brief` page as the first navigation item. It should show:

- Headline and data health.
- Metric strip for opportunities, entry watch, risk alerts, catalysts, and validated strategies.
- Top opportunities cards/table with trigger, stop, target, rank, strategy, and caveats.
- Strategy validation table.
- Catalyst and risk alert lists.
- Next-step checklist.

The page uses the global data mode and symbols from the top bar. It should have a Refresh Brief button and should not introduce new layout primitives beyond existing panels, tables, metric grid, and status pills.

## Testing

Backend:

- Pure service test builds a brief from fixture scan + backtest + alert suggestions.
- API test checks `/api/daily-brief` returns headline, top opportunities, entry watch, strategy validation, caveats, and data health.

Frontend:

- TypeScript production build must pass.

## Boundaries

No scheduled delivery, email, broker connection, SMS, or push notification in this feature. Those can be layered on once the brief is stable.
