from __future__ import annotations

from decimal import Decimal

from qagent.domain.models import OpportunityCard, RecommendationBrief


def apply_recommendation_briefs(cards: list[OpportunityCard]) -> dict[str, str]:
    applied = 0
    for card in cards:
        card.recommendation_brief = build_recommendation_brief(card)
        applied += 1
    return {"recommendation_brief_cards": str(applied)}


def build_recommendation_brief(card: OpportunityCard) -> RecommendationBrief:
    return RecommendationBrief(
        why=_why(card),
        buy_point=_buy_point(card),
        stop_loss=_price_text(card.exit_plan.initial_stop, "未设置硬止损"),
        target=_price_text(card.exit_plan.target_1, "未设置第一目标"),
        risk=_risk(card),
        history_odds=_history_odds(card),
        current_verdict=_current_verdict(card),
    )


def _why(card: OpportunityCard) -> str:
    if card.signal_hub is not None:
        return card.signal_hub.verdict
    if card.recommendation_score is not None:
        return card.recommendation_score.summary
    if card.rank_reasons:
        return "；".join(card.rank_reasons[:2])
    return card.thesis


def _buy_point(card: OpportunityCard) -> str:
    trigger = card.entry_plan.trigger_price
    if trigger is None and card.decision is not None:
        trigger = card.decision.trigger_price
    no_chase = card.entry_plan.no_chase_above
    if no_chase is None and card.decision is not None:
        no_chase = card.decision.no_chase_above
    if trigger is not None and no_chase is not None:
        return f"触发价 {trigger}，不追高于 {no_chase}。"
    if trigger is not None:
        return f"等待触发价 {trigger}，未触发不追。"
    if card.entry_plan.entry_zone_low is not None and card.entry_plan.entry_zone_high is not None:
        return f"观察区间 {card.entry_plan.entry_zone_low}-{card.entry_plan.entry_zone_high}。"
    return card.entry_plan.confirmation


def _risk(card: OpportunityCard) -> str:
    if card.pre_trade_risk is not None:
        return card.pre_trade_risk.summary
    if card.decision is not None and card.decision.risk_vetoes:
        return card.decision.risk_vetoes[0].message
    if card.data_quality_audit is not None and not card.data_quality_audit.can_recommend:
        return card.data_quality_audit.summary
    if card.factor_flags:
        return f"关注 {'、'.join(card.factor_flags[:3])}。"
    return card.exit_plan.invalidation


def _history_odds(card: OpportunityCard) -> str:
    if card.probability_forecast is not None:
        forecast = card.probability_forecast
        return (
            f"10日胜率估计 {forecast.win_probability_10d:.0%}，"
            f"20日期望 {forecast.expected_return_20d:+.2f}%，样本 {forecast.sample_count}。"
        )
    if card.strategy_calibration is not None:
        calibration = card.strategy_calibration
        if calibration.win_rate_10d is not None:
            return (
                f"10日历史胜率 {calibration.win_rate_10d:.0%}，"
                f"样本 {calibration.sample_count}。"
            )
        return f"历史样本 {calibration.sample_count}，仍需积累。"
    return "历史样本不足，先按观察信号处理。"


def _current_verdict(card: OpportunityCard) -> str:
    if card.decision is not None and card.decision.risk_status == "blocked":
        return "不适合"
    if card.pre_trade_risk is not None and not card.pre_trade_risk.can_buy:
        return "不适合"
    action = card.decision.action if card.decision is not None else ""
    if action in {"buy", "buy_now", "open_position"}:
        return "适合买"
    return "等待买点"


def _price_text(value: Decimal | None, fallback: str) -> str:
    if value is None:
        return fallback
    return str(value)
