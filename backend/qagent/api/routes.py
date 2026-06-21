from decimal import Decimal

from fastapi import APIRouter, HTTPException

from qagent.agent.responder import answer_question
from qagent.api.schemas import AgentQueryRequest, AgentQueryResponse, AlertEvaluationRequest
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.providers import FreeCatalystProvider
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.daily_scan import run_daily_scan
from qagent.jobs.intraday_check import evaluate_snapshot_alerts
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.monitoring.portfolio import PositionInput, analyze_position_risk
from qagent.monitoring.alerts import AlertRule
from qagent.providers.factory import build_market_data_provider
from qagent.storage.repository import (
    AlertRuleCreate,
    PositionCreate,
    QagentRepository,
    WatchlistCreate,
)

router = APIRouter()


def _signal_summary(card) -> str:
    return "; ".join(
        f"{signal.signal_type.value} {signal.direction.value} {signal.score:.2f}"
        for signal in card.signals[:4]
    )


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


@router.get("/catalysts")
def catalysts(symbols: str | None = None, limit: int = 5) -> dict[str, object]:
    instrument_ids = _parse_symbols(symbols, DEFAULT_FREE_UNIVERSE)
    provider = FreeCatalystProvider()
    news = provider.get_news(instrument_ids, limit=limit)
    hypotheses = build_catalyst_hypotheses(news)
    data_health = {
        "provider": "free",
        "scanned": str(len(instrument_ids)),
        "news": str(len(news)),
        "hypotheses": str(len(hypotheses)),
    }
    if provider.last_errors:
        data_health["errors"] = " | ".join(provider.last_errors[:3])
    return {
        "news": [item.model_dump(mode="json") for item in news],
        "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
        "data_health": data_health,
    }


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
def portfolio(provider: str = "fixture") -> dict[str, object]:
    positions = _repo().list_positions()
    risks = []
    data_health = {"provider": provider, "positions": str(len(positions)), "risk": "0"}
    if positions:
        try:
            market_provider = build_market_data_provider(provider)
            instrument_ids = [position.instrument_id for position in positions]
            snapshot = market_provider.get_snapshot(instrument_ids)
            latest_prices = {
                row["instrument_id"]: Decimal(str(row["close"]))
                for _, row in snapshot.iterrows()
            }
            for position in positions:
                latest_price = latest_prices.get(position.instrument_id)
                if latest_price is None:
                    continue
                risks.append(
                    analyze_position_risk(
                        PositionInput(**position.model_dump()),
                        current_price=latest_price,
                    )
                )
            data_health["risk"] = str(len(risks))
            provider_errors = getattr(market_provider, "last_errors", [])
            if provider_errors:
                data_health["errors"] = " | ".join(provider_errors[:3])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "positions": [position.model_dump(mode="json") for position in positions],
        "risk": [risk.model_dump(mode="json") for risk in risks],
        "data_health": data_health,
    }


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
            "signal_summary": _signal_summary(selected),
        },
    )
    return AgentQueryResponse(answer=answer)
