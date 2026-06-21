from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.domain.enums import Direction, Market, OpportunityStatus, SignalType


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
    data_caveats: list[str] = Field(default_factory=list)
