from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.domain.enums import Direction, Market, OpportunityStatus, SignalType
from qagent.factors.models import FactorExposure
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


class RiskVeto(BaseModel):
    code: str
    severity: str
    title: str
    message: str


class TradingConstraint(BaseModel):
    code: str
    severity: str
    title: str
    message: str


class TradingConstraintProfile(BaseModel):
    board: str
    price_limit_pct: int | None = None
    permission_required: bool = False
    t_plus_one: bool = True
    min_lot: int | None = 100
    constraints: list[TradingConstraint] = Field(default_factory=list)


class MarketContext(BaseModel):
    board: str
    industry: str
    themes: list[str] = Field(default_factory=list)
    index_memberships: list[str] = Field(default_factory=list)
    summary: str


class TradingStatus(BaseModel):
    status: str
    label: str
    severity: str
    latest_close: str | None = None
    previous_close: str | None = None
    change_pct: float | None = None
    limit_up_price: str | None = None
    limit_down_price: str | None = None
    can_buy: bool = True
    can_sell: bool = True
    notes: list[str] = Field(default_factory=list)


class SectorMove(BaseModel):
    instrument_id: str
    instrument_label: str | None = None
    change_pct: float
    latest_close: str | None = None


class SectorStrength(BaseModel):
    industry: str
    themes: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    avg_change_pct: float
    advance_ratio: float
    total_volume: int
    score: float
    leaders: list[SectorMove] = Field(default_factory=list)
    laggards: list[SectorMove] = Field(default_factory=list)
    summary: str


class StrategyCalibration(BaseModel):
    strategy_id: str
    readiness: str
    sample_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    max_loss_10d: float | None = None
    message: str


class TradabilityCheck(BaseModel):
    code: str
    severity: str
    title: str
    message: str


class TradabilityAssessment(BaseModel):
    status: str
    label: str
    score: float = Field(ge=0.0, le=1.0)
    can_open: bool
    can_hold: bool = True
    avg_volume_20d: int | None = None
    avg_amount_20d: str | None = None
    checks: list[TradabilityCheck] = Field(default_factory=list)
    summary: str


class PortfolioAllocation(BaseModel):
    instrument_id: str
    instrument_label: str | None = None
    action: str
    weight_pct: float
    risk_budget_pct: float
    max_position_pct: float
    industry: str | None = None
    rationale: str


class PortfolioPlan(BaseModel):
    profile: str = "balanced"
    max_positions: int
    total_risk_budget_pct: float
    allocated_weight_pct: float
    eligible_count: int
    blocked_count: int
    allocations: list[PortfolioAllocation] = Field(default_factory=list)
    watchlist: list[PortfolioAllocation] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    summary: str


class RecommendationSummary(BaseModel):
    headline: str
    stance: str
    buy_timing: str
    sell_timing: str
    position_note: str
    risk_note: str
    context_note: str
    checklist: list[str] = Field(default_factory=list)


class ConfidenceDriver(BaseModel):
    label: str
    value: str
    impact: str = "neutral"
    weight: float | None = None


class ConfidenceExplanation(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    label: str
    summary: str
    positive_drivers: list[ConfidenceDriver] = Field(default_factory=list)
    risk_drivers: list[ConfidenceDriver] = Field(default_factory=list)
    data_checks: list[ConfidenceDriver] = Field(default_factory=list)


class SignalHubComponent(BaseModel):
    key: str
    label: str
    score: float = Field(ge=0.0, le=1.0)
    status: str
    detail: str


class SimilarSignalValidation(BaseModel):
    readiness: str
    sample_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    max_loss_10d: float | None = None
    verdict: str
    summary: str


class RecommendationTimelineEvent(BaseModel):
    key: str
    label: str
    status: str
    severity: str
    detail: str


class SignalAlertSuggestion(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    operator: str
    threshold: Decimal
    rationale: str


class SignalHub(BaseModel):
    trust_score: float = Field(ge=0.0, le=1.0)
    label: str
    verdict: str
    rotation_context: str | None = None
    next_action: str
    components: list[SignalHubComponent] = Field(default_factory=list)
    similar_validation: SimilarSignalValidation
    timeline: list[RecommendationTimelineEvent] = Field(default_factory=list)
    alert_suggestions: list[SignalAlertSuggestion] = Field(default_factory=list)


class ExecutionPlanSummary(BaseModel):
    action: str
    action_label: str
    buy_zone: str
    sell_plan: str
    risk_plan: str
    position_plan: str
    invalidation: str
    next_checklist: list[str] = Field(default_factory=list)


class OpportunityDecision(BaseModel):
    action: str
    action_label: str
    conviction_score: float = Field(ge=0.0, le=1.0)
    components: DecisionComponents
    risk_status: str = "clear"
    risk_vetoes: list[RiskVeto] = Field(default_factory=list)
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
    instrument_label: str | None = None
    market: Market
    asset_type: str = "stock"
    opportunity_bucket: str = "stock_momentum"
    opportunity_tags: list[str] = Field(default_factory=list)
    rotation_note: str | None = None
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
    factor_score: float = Field(default=0.0, ge=0.0, le=1.0)
    factor_rank: int | None = None
    factor_percentile: float = Field(default=0.0, ge=0.0, le=1.0)
    factor_flags: list[str] = Field(default_factory=list)
    factor_exposures: list[FactorExposure] = Field(default_factory=list)
    data_caveats: list[str] = Field(default_factory=list)
    decision: OpportunityDecision | None = None
    trading_constraints: TradingConstraintProfile | None = None
    market_context: MarketContext | None = None
    trading_status: TradingStatus | None = None
    tradability: TradabilityAssessment | None = None
    strategy_calibration: StrategyCalibration | None = None
    recommendation_summary: RecommendationSummary | None = None
    confidence_explanation: ConfidenceExplanation | None = None
    signal_hub: SignalHub | None = None
    execution_plan: ExecutionPlanSummary | None = None
