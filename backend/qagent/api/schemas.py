from pydantic import BaseModel


class AgentQueryRequest(BaseModel):
    question: str
    instrument_id: str | None = None
    provider: str = "fixture"
    symbols: str | None = None


class AgentQueryResponse(BaseModel):
    answer: str


class AlertEvaluationRequest(BaseModel):
    prices: dict[str, str]


class PaperTradeFromOpportunityRequest(BaseModel):
    card_id: str
    provider: str = "fixture"
    instrument_id: str
    strategy_id: str | None = None
    trigger_price: str | None = None
    initial_stop: str | None = None
    target_1: str | None = None
    rank_score: float | None = None
    action: str = "watch_trigger"
    risk_status: str = "clear"
