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


class PaperSessionStartRequest(BaseModel):
    label: str = "A股正式模拟盘"
    reset_existing: bool = True
    initial_capital: str = "100000"
    allocation_per_trade_pct: str = "10"
    max_positions: int = 5
    transaction_cost_bps: str = "5"
    slippage_bps: str = "5"
    take_profit_pct: str = "50"
