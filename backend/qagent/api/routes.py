from fastapi import APIRouter

from qagent.agent.responder import answer_question
from qagent.api.schemas import AgentQueryRequest, AgentQueryResponse
from qagent.db import Base, create_db_engine, create_session_factory
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.universe import DEFAULT_DEV_UNIVERSE
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.storage.repository import (
    PositionCreate,
    QagentRepository,
    WatchlistCreate,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _scan():
    return run_daily_scan(DEFAULT_DEV_UNIVERSE, FixtureMarketDataProvider())


def _repo() -> QagentRepository:
    engine = create_db_engine()
    Base.metadata.create_all(engine)
    return QagentRepository(create_session_factory())


@router.get("/opportunities")
def opportunities() -> dict[str, object]:
    result = _scan()
    return {
        "cards": [card.model_dump(mode="json") for card in result.cards],
        "data_health": result.data_health,
    }


@router.get("/overview")
def overview() -> dict[str, object]:
    result = _scan()
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
        },
    )
    return AgentQueryResponse(answer=answer)
