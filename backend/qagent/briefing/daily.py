from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.backtesting.engine import BacktestResult
from qagent.catalysts.models import CatalystHypothesis
from qagent.jobs.daily_scan import DailyScanResult
from qagent.monitoring.portfolio import PositionRisk
from qagent.providers.status import ProviderStatus


class BriefOpportunity(BaseModel):
    instrument_id: str
    status: str
    primary_strategy_id: str | None
    rank_score: float
    thesis: str
    trigger_price: Decimal | None
    initial_stop: Decimal | None
    target_1: Decimal | None
    risk_reward: float | None
    scenario_summary: str
    decision_action: str | None = None
    decision_label: str | None = None
    conviction_score: float | None = None
    suggested_risk_pct: float | None = None
    rank_reasons: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    verification_checks: list[str] = Field(default_factory=list)
    data_caveats: list[str] = Field(default_factory=list)


class EntryWatchItem(BaseModel):
    instrument_id: str
    primary_strategy_id: str | None
    trigger_price: Decimal
    initial_stop: Decimal | None
    target_1: Decimal | None
    risk_reward: float | None
    decision_action: str | None = None
    conviction_score: float | None = None
    suggested_risk_pct: float | None = None
    note: str


class BriefRiskAlert(BaseModel):
    instrument_id: str
    status: str
    current_price: Decimal
    stop_distance_pct: float | None
    target_1_distance_pct: float | None
    message: str


class BriefCatalyst(BaseModel):
    instrument_id: str
    catalyst_type: str
    title: str
    investment_hypothesis: str
    verification_path: str
    confidence: float


class BriefStrategyValidation(BaseModel):
    strategy_id: str
    sample_count: int
    completed_count: int
    target_hit_rate: float | None
    positive_rate_10d: float | None
    avg_return_10d: float | None
    max_drawdown_pct: float | None
    max_runup_pct: float | None


class DailyBrief(BaseModel):
    generated_at: datetime
    provider: str
    symbols: list[str]
    headline: str
    top_opportunities: list[BriefOpportunity]
    entry_watch: list[EntryWatchItem]
    risk_alerts: list[BriefRiskAlert]
    catalyst_watch: list[BriefCatalyst]
    strategy_validation: list[BriefStrategyValidation]
    data_caveats: list[str]
    next_steps: list[str]
    data_health: dict[str, str]


def build_daily_brief(
    provider: str,
    symbols: list[str],
    scan_result: DailyScanResult,
    backtest_result: BacktestResult | None = None,
    catalyst_hypotheses: list[CatalystHypothesis] | None = None,
    position_risks: list[PositionRisk] | None = None,
    provider_statuses: list[ProviderStatus] | None = None,
    limit: int = 5,
    data_health: dict[str, str] | None = None,
) -> DailyBrief:
    top_opportunities = _top_opportunities(scan_result, limit)
    entry_watch = _entry_watch(top_opportunities)
    risk_alerts = _risk_alerts(position_risks or [])
    catalyst_watch = _catalyst_watch(catalyst_hypotheses or [], limit)
    strategy_validation = _strategy_validation(backtest_result, limit)
    caveats = _data_caveats(
        scan_result,
        backtest_result,
        catalyst_hypotheses or [],
        provider_statuses or [],
    )
    next_steps = _next_steps(entry_watch, catalyst_watch, risk_alerts, caveats)
    merged_health = {
        **scan_result.data_health,
        **(data_health or {}),
        "brief_opportunities": str(len(top_opportunities)),
        "brief_entry_watch": str(len(entry_watch)),
        "brief_catalysts": str(len(catalyst_watch)),
        "brief_risk_alerts": str(len(risk_alerts)),
        "brief_validated_strategies": str(len(strategy_validation)),
    }
    return DailyBrief(
        generated_at=datetime.now(timezone.utc),
        provider=provider,
        symbols=symbols,
        headline=_headline(top_opportunities, strategy_validation, risk_alerts),
        top_opportunities=top_opportunities,
        entry_watch=entry_watch,
        risk_alerts=risk_alerts,
        catalyst_watch=catalyst_watch,
        strategy_validation=strategy_validation,
        data_caveats=caveats,
        next_steps=next_steps,
        data_health=merged_health,
    )


def _top_opportunities(scan_result: DailyScanResult, limit: int) -> list[BriefOpportunity]:
    cards = sorted(scan_result.cards, key=lambda card: card.rank_score, reverse=True)[:limit]
    return [
        BriefOpportunity(
            instrument_id=card.instrument_id,
            status=card.status.value,
            primary_strategy_id=card.primary_strategy_id,
            rank_score=card.rank_score,
            thesis=card.thesis,
            trigger_price=card.entry_plan.trigger_price,
            initial_stop=card.exit_plan.initial_stop,
            target_1=card.exit_plan.target_1,
            risk_reward=card.risk_reward,
            scenario_summary=card.scenario.summary,
            decision_action=card.decision.action if card.decision else None,
            decision_label=card.decision.action_label if card.decision else None,
            conviction_score=card.decision.conviction_score if card.decision else None,
            suggested_risk_pct=card.decision.suggested_risk_pct if card.decision else None,
            rank_reasons=card.rank_reasons,
            failure_conditions=card.decision.failure_conditions if card.decision else [],
            verification_checks=card.decision.verification_checks if card.decision else [],
            data_caveats=card.data_caveats,
        )
        for card in cards
    ]


def _entry_watch(opportunities: list[BriefOpportunity]) -> list[EntryWatchItem]:
    items = []
    for opportunity in opportunities:
        if opportunity.trigger_price is None:
            continue
        items.append(
            EntryWatchItem(
                instrument_id=opportunity.instrument_id,
                primary_strategy_id=opportunity.primary_strategy_id,
                trigger_price=opportunity.trigger_price,
                initial_stop=opportunity.initial_stop,
                target_1=opportunity.target_1,
                risk_reward=opportunity.risk_reward,
                decision_action=opportunity.decision_action,
                conviction_score=opportunity.conviction_score,
                suggested_risk_pct=opportunity.suggested_risk_pct,
                note="Watch trigger, invalidation, target, and data caveats before action.",
            )
        )
    return items


def _risk_alerts(position_risks: list[PositionRisk]) -> list[BriefRiskAlert]:
    alerts = []
    for risk in position_risks:
        urgent = risk.status in {"stop_breached", "target_reached"}
        near_stop = risk.stop_distance_pct is not None and risk.stop_distance_pct <= 3
        near_target = risk.target_1_distance_pct is not None and risk.target_1_distance_pct <= 3
        if not (urgent or near_stop or near_target):
            continue
        alerts.append(
            BriefRiskAlert(
                instrument_id=risk.instrument_id,
                status=risk.status,
                current_price=risk.current_price,
                stop_distance_pct=risk.stop_distance_pct,
                target_1_distance_pct=risk.target_1_distance_pct,
                message=_risk_message(risk),
            )
        )
    return alerts


def _risk_message(risk: PositionRisk) -> str:
    if risk.status == "stop_breached":
        return "Position is at or below the stored invalidation level."
    if risk.status == "target_reached":
        return "Position has reached the first stored target."
    if risk.stop_distance_pct is not None and risk.stop_distance_pct <= 3:
        return "Position is close to the stored stop."
    return "Position is close to the first stored target."


def _catalyst_watch(
    hypotheses: list[CatalystHypothesis],
    limit: int,
) -> list[BriefCatalyst]:
    sorted_items = sorted(hypotheses, key=lambda item: item.confidence, reverse=True)[:limit]
    return [
        BriefCatalyst(
            instrument_id=item.instrument_id,
            catalyst_type=item.catalyst_type,
            title=item.title,
            investment_hypothesis=item.investment_hypothesis,
            verification_path=item.verification_path,
            confidence=item.confidence,
        )
        for item in sorted_items
    ]


def _strategy_validation(
    backtest_result: BacktestResult | None,
    limit: int,
) -> list[BriefStrategyValidation]:
    if backtest_result is None:
        return []
    performance = sorted(
        backtest_result.performance,
        key=lambda item: (item.target_hit_rate or 0, item.sample_count),
        reverse=True,
    )[:limit]
    return [
        BriefStrategyValidation(
            strategy_id=item.strategy_id,
            sample_count=item.sample_count,
            completed_count=item.completed_count,
            target_hit_rate=item.target_hit_rate,
            positive_rate_10d=item.positive_rate_10d,
            avg_return_10d=item.avg_return_10d,
            max_drawdown_pct=item.max_drawdown_pct,
            max_runup_pct=item.max_runup_pct,
        )
        for item in performance
    ]


def _data_caveats(
    scan_result: DailyScanResult,
    backtest_result: BacktestResult | None,
    hypotheses: list[CatalystHypothesis],
    provider_statuses: list[ProviderStatus],
) -> list[str]:
    caveats: list[str] = []
    for card in scan_result.cards:
        for caveat in card.data_caveats:
            caveats.append(f"{card.instrument_id}: {caveat}")
    for key in ("errors", "strategy_data_errors"):
        if key in scan_result.data_health:
            caveats.append(scan_result.data_health[key])
    missing = [status.name for status in provider_statuses if status.status == "missing_config"]
    if missing:
        caveats.append(f"Optional data providers missing config: {', '.join(missing)}.")
    if backtest_result is None or not backtest_result.performance:
        caveats.append("No strategy backtest validation is available for this brief.")
    if not hypotheses:
        caveats.append("No catalyst hypotheses returned for this brief.")
    return _dedupe(caveats)


def _next_steps(
    entry_watch: list[EntryWatchItem],
    catalysts: list[BriefCatalyst],
    risk_alerts: list[BriefRiskAlert],
    caveats: list[str],
) -> list[str]:
    steps = []
    if entry_watch:
        steps.append("Check each trigger against volume, invalidation, and no-chase levels.")
    if catalysts:
        steps.append("Validate catalyst transmission through orders, guidance, estimates, or margins.")
    if risk_alerts:
        steps.append("Review positions near stop or target levels before adding exposure.")
    if caveats:
        steps.append("Resolve material data caveats before treating any setup as actionable.")
    steps.append("Treat the brief as research context, not personalized investment advice.")
    return steps


def _headline(
    opportunities: list[BriefOpportunity],
    validation: list[BriefStrategyValidation],
    risk_alerts: list[BriefRiskAlert],
) -> str:
    setup_count = sum(1 for item in opportunities if item.status == "setup_ready")
    strategy_word = "strategy" if len(validation) == 1 else "strategies"
    return (
        f"{setup_count} setup-ready opportunities; "
        f"{len(validation)} {strategy_word} with validation samples; "
        f"{len(risk_alerts)} position risk alerts."
    )


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
