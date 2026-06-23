from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.domain.enums import Direction, Market, OpportunityStatus, SignalType
from qagent.strategies.models import StrategyEvaluation


class Instrument(BaseModel):
    market: Market
    symbol: str
    exchange: str
    name: str
    currency: str
    timezone: str
    trading_calendar: str
    asset_type: str = "stock"

    @property
    def instrument_id(self) -> str:
        return f"{self.market.value}:{self.symbol}"


class DailyBar(BaseModel):
    instrument_id: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    provider: str


class Signal(BaseModel):
    instrument_id: str
    signal_type: SignalType
    direction: Direction
    observed_at: datetime
    horizon: str
    score: float = Field(ge=0.0, le=1.0)
    evidence: dict[str, object] = Field(default_factory=dict)
    provider: str = "computed"


class SignalSnapshot(BaseModel):
    signal_type: SignalType
    direction: Direction
    horizon: str
    score: float = Field(ge=0.0, le=1.0)
    evidence: dict[str, object] = Field(default_factory=dict)


class EntryPlan(BaseModel):
    entry_type: str
    confirmation: str
    trigger_price: Decimal | None = None
    entry_zone_low: Decimal | None = None
    entry_zone_high: Decimal | None = None
    no_chase_above: Decimal | None = None


class ExitPlan(BaseModel):
    invalidation: str
    trailing_rule: str
    time_stop: str
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    target_2: Decimal | None = None


class TradeScenario(BaseModel):
    downside_pct: float
    target_1_pct: float
    no_chase_pct: float
    summary: str


class DecisionComponents(BaseModel):
    strategy_quality: float = Field(ge=0.0, le=1.0)
    risk_reward: float = Field(ge=0.0, le=1.0)
    data_quality: float = Field(ge=0.0, le=1.0)
    execution_quality: float = Field(ge=0.0, le=1.0)
    catalyst_support: float = Field(ge=0.0, le=1.0)


class OpportunityDecision(BaseModel):
    action: str
    action_label: str
    conviction_score: float = Field(ge=0.0, le=1.0)
    components: DecisionComponents
    suggested_risk_pct: float = Field(ge=0.0)
    max_position_pct: float = Field(ge=0.0)
    trigger_price: Decimal | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    no_chase_above: Decimal | None = None
    horizon: str = "swing"
    rationale: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    verification_checks: list[str] = Field(default_factory=list)
    safety_note: str = "Research workflow only; not personalized investment advice."


class OpportunityCard(BaseModel):
    card_id: str
    instrument_id: str
    market: Market
    status: OpportunityStatus
    thesis: str
    score: float = Field(ge=0.0, le=1.0)
    entry_plan: EntryPlan
    exit_plan: ExitPlan
    risk_reward: float | None = None
    scenario: TradeScenario
    signals: list[SignalSnapshot] = Field(default_factory=list)
    strategy_evaluations: list[StrategyEvaluation] = Field(default_factory=list)
    primary_strategy_id: str | None = None
    strategy_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rank_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rank_reasons: list[str] = Field(default_factory=list)
    data_caveats: list[str] = Field(default_factory=list)
    decision: OpportunityDecision | None = None
