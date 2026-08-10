from datetime import date

from pydantic import BaseModel, Field


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
    label: str = "A股研究模拟盘"
    reset_existing: bool = True
    initial_capital: str = "100000"
    allocation_per_trade_pct: str = "10"
    max_positions: int = 5
    transaction_cost_bps: str = "5"
    slippage_bps: str = "5"
    take_profit_pct: str = "50"


class StrategyGovernanceResponse(BaseModel):
    strategies: list[dict[str, object]] = Field(default_factory=list)
    policies: list[dict[str, object]] = Field(default_factory=list)
    recent_events: list[dict[str, object]] = Field(default_factory=list)
    gate_reasons: list[dict[str, object]] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


class FactorResearchExperimentRequest(BaseModel):
    provider_mode: str = "free"
    start_date: date = date(2021, 11, 1)
    end_date: date = date(2025, 12, 31)
    dataset_revision: int | None = None
    benchmark_id: str = "CN:000300.IDX"
    rebalance_step_sessions: int = Field(default=10, ge=5, le=60)
    horizon_sessions: int = Field(default=20, ge=5, le=60)
    minimum_history_sessions: int = Field(default=120, ge=60, le=260)
    top_fraction: float = Field(default=0.10, gt=0, le=0.30)
    round_trip_cost_bps: float = Field(default=10.0, ge=0, le=100)
    max_instruments: int | None = Field(default=None, ge=50)
    seeds: list[int] = Field(default_factory=lambda: [7, 19, 42], min_length=1, max_length=10)
