from decimal import Decimal

from fastapi import APIRouter, HTTPException

from qagent.agent.responder import answer_question
from qagent.api.schemas import AgentQueryRequest, AgentQueryResponse, AlertEvaluationRequest
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.daily_scan import run_daily_scan
from qagent.jobs.intraday_check import evaluate_snapshot_alerts
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.monitoring.alerts import AlertRule
from qagent.providers.factory import build_market_data_provider
from qagent.storage.repository import (
    AlertRuleCreate,
    PositionCreate,
    QagentRepository,
    WatchlistCreate,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _parse_symbols(symbols: str | None, default_universe: list[str]) -> list[str]:
    if not symbols:
        return default_universe
    return [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]


def _scan(provider_mode: str = "fixture", symbols: str | None = None):
    mode = provider_mode.strip().lower()
    default_universe = DEFAULT_FREE_UNIVERSE if mode == "free" else DEFAULT_DEV_UNIVERSE
    instrument_ids = _parse_symbols(symbols, default_universe)
    try:
        provider = build_market_data_provider(mode)
        return run_daily_scan(instrument_ids, provider, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _repo() -> QagentRepository:
    initialize_database()
    return QagentRepository(create_session_factory())


@router.get("/opportunities")
def opportunities(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result = _scan(provider, symbols)
    return {
        "cards": [card.model_dump(mode="json") for card in result.cards],
        "items": [item.model_dump(mode="json") for item in result.items],
        "data_health": result.data_health,
    }


@router.get("/overview")
def overview(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result = _scan(provider, symbols)
    return {
        "market_regime": {
            "US": "development_fixture",
            "CN": "development_fixture",
        },
        "top_cards": [card.model_dump(mode="json") for card in result.cards[:5]],
        "data_health": result.data_health,
    }


@router.get("/alerts")
def alerts() -> dict[str, list[object]]:
    return {"alerts": []}


@router.get("/alert-rules")
def alert_rules() -> dict[str, list[object]]:
    return {"rules": [rule.model_dump(mode="json") for rule in _repo().list_alert_rules()]}


@router.post("/alert-rules")
def upsert_alert_rule(rule: AlertRuleCreate) -> dict[str, object]:
    saved = _repo().upsert_alert_rule(rule)
    return saved.model_dump(mode="json")


@router.post("/alerts/evaluate")
def evaluate_alerts(request: AlertEvaluationRequest) -> dict[str, list[object]]:
    prices = {instrument_id: Decimal(price) for instrument_id, price in request.prices.items()}
    rules = [
        AlertRule(
            rule_id=rule.rule_id,
            instrument_id=rule.instrument_id,
            kind=rule.kind,
            operator=rule.operator,
            threshold=rule.threshold,
        )
        for rule in _repo().list_alert_rules()
    ]
    alerts = evaluate_snapshot_alerts(prices, rules)
    return {"alerts": [alert.model_dump(mode="json") for alert in alerts]}


@router.get("/portfolio")
def portfolio() -> dict[str, list[object]]:
    return {"positions": [position.model_dump(mode="json") for position in _repo().list_positions()]}


@router.get("/watchlist")
def watchlist() -> dict[str, list[object]]:
    return {"items": [item.model_dump(mode="json") for item in _repo().list_watchlist_items()]}


@router.post("/watchlist")
def upsert_watchlist_item(item: WatchlistCreate) -> dict[str, object]:
    saved = _repo().upsert_watchlist_item(item)
    return saved.model_dump(mode="json")


@router.get("/positions")
def positions() -> dict[str, list[object]]:
    return {"positions": [position.model_dump(mode="json") for position in _repo().list_positions()]}


@router.post("/positions")
def upsert_position(position: PositionCreate) -> dict[str, object]:
    saved = _repo().upsert_position(position)
    return saved.model_dump(mode="json")


@router.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: AgentQueryRequest) -> AgentQueryResponse:
    result = _scan()
    selected = None
    if request.instrument_id:
        selected = next((card for card in result.cards if card.instrument_id == request.instrument_id), None)
    if selected is None and result.cards:
        selected = result.cards[0]
    if selected is None:
        return AgentQueryResponse(answer="No opportunity context is available yet.")

    answer = answer_question(
        request.question,
        context={
            "instrument_id": selected.instrument_id,
            "status": selected.status.value,
            "score": selected.score,
            "initial_stop": str(selected.exit_plan.initial_stop),
            "trigger_price": str(selected.entry_plan.trigger_price),
            "target_1": str(selected.exit_plan.target_1),
            "downside_pct": selected.scenario.downside_pct,
            "target_1_pct": selected.scenario.target_1_pct,
            "no_chase_above": str(selected.entry_plan.no_chase_above),
        },
    )
    return AgentQueryResponse(answer=answer)
