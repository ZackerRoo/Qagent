from __future__ import annotations

from datetime import date

from qagent.domain.models import (
    OpportunityCard,
    PreTradeRiskCheck,
    PreTradeRiskProfile,
    RecommendationQualityCheck,
    RiskVeto,
)
from qagent.monitoring.outcomes import compute_opportunity_outcome
from qagent.monitoring.recommendation_calibration import (
    RecommendationCalibrationCenter,
    build_recommendation_calibration_center,
)
from qagent.providers.base import MarketDataProvider
from qagent.storage.repository import QagentRepository


def build_recent_recommendation_feedback_center(
    *,
    repo: QagentRepository,
    provider: str,
    market_provider: MarketDataProvider,
    limit: int = 150,
) -> RecommendationCalibrationCenter | None:
    snapshots = repo.list_opportunity_snapshots(provider=provider, limit=limit)
    if not snapshots:
        return None
    instrument_ids = list(dict.fromkeys(snapshot.instrument_id for snapshot in snapshots))
    bars = market_provider.get_daily_bars(
        instrument_ids,
        start=date(1900, 1, 1),
        end=date(2100, 1, 1),
    )
    pairs = []
    for snapshot in snapshots:
        if not bars.empty and "instrument_id" in bars.columns:
            instrument_bars = bars.loc[bars["instrument_id"] == snapshot.instrument_id]
        else:
            instrument_bars = bars
        pairs.append((snapshot, compute_opportunity_outcome(snapshot, instrument_bars)))
    return build_recommendation_calibration_center(
        pairs,
        data_health={
            "feedback_provider": provider,
            "feedback_snapshots": str(len(snapshots)),
        },
    )


def apply_recommendation_feedback_calibration(
    cards: list[OpportunityCard],
    center: RecommendationCalibrationCenter | None,
) -> list[OpportunityCard]:
    if center is None or not center.signal_effects:
        return cards
    effects = {
        effect.signal_key: effect
        for effect in center.signal_effects
        if effect.completed_count >= 2 and abs(effect.suggested_weight_delta) > 0
    }
    if not effects:
        return cards
    for card in cards:
        matched = [effects[key] for key in _card_signal_keys(card) if key in effects]
        if not matched:
            continue
        raw_delta = sum(_action_delta(effect) for effect in matched)
        reliability_scale = 0.45 + center.reliability_score * 0.55
        delta = _clamp(raw_delta * reliability_scale, -0.08, 0.08)
        if abs(delta) < 0.001:
            continue
        card.rank_score = round(_clamp(card.rank_score + delta, 0.0, 1.0), 4)
        card.dynamic_score = card.rank_score
        if card.recommendation_score is not None:
            card.recommendation_score.final_score = card.rank_score
            card.recommendation_score.summary = (
                f"推荐分 {card.rank_score:.0%}：已纳入推荐后闭环反馈 {delta:+.1%}。"
            )
        labels = "、".join(effect.label for effect in matched[:3])
        note = f"推荐反馈校准：{labels} 根据历史推荐后表现调整 {delta:+.1%}。"
        if note not in card.rank_reasons:
            card.rank_reasons.append(note)
        if note not in card.calibration_notes:
            card.calibration_notes.append(note)
    return cards


def apply_recommendation_feedback_quality_gate(
    cards: list[OpportunityCard],
    center: RecommendationCalibrationCenter | None,
) -> list[OpportunityCard]:
    if center is None or not center.signal_effects:
        return cards
    weak_effects = {
        effect.signal_key: effect
        for effect in center.signal_effects
        if _is_blocking_effect(effect)
    }
    if not weak_effects:
        return cards
    for card in cards:
        matched = [weak_effects[key] for key in _card_signal_keys(card) if key in weak_effects]
        if not matched:
            continue
        _apply_feedback_block(card, matched)
    return cards


def recommendation_feedback_data_health(cards: list[OpportunityCard]) -> dict[str, str]:
    adjusted = sum(
        1
        for card in cards
        if any("推荐反馈校准" in reason for reason in card.rank_reasons)
    )
    blocked = sum(
        1
        for card in cards
        if any("推荐反馈门禁" in reason for reason in card.rank_reasons)
    )
    return {
        "recommendation_feedback_cards": str(len(cards)),
        "recommendation_feedback_adjusted": str(adjusted),
        "recommendation_feedback_blocked": str(blocked),
    }


def _card_signal_keys(card: OpportunityCard) -> list[str]:
    keys = list(card.factor_flags)
    a_share_enhanced = getattr(card, "a_share_enhanced", None)
    if a_share_enhanced is not None:
        keys.extend(a_share_enhanced.signals)
    if card.recommendation_quality is not None:
        keys.append(f"quality_{card.recommendation_quality.tier}")
    if card.primary_strategy_id:
        keys.append(card.primary_strategy_id)
    return sorted(set(key for key in keys if key))


def _action_delta(effect) -> float:
    action = str(effect.weight_action)
    if action in {"提高", "raise", "increase", "promote"}:
        return abs(effect.suggested_weight_delta)
    if action in {"降低", "lower", "decrease", "demote"}:
        return -abs(effect.suggested_weight_delta)
    return effect.suggested_weight_delta


def _is_blocking_effect(effect) -> bool:
    if effect.completed_count < 3:
        return False
    if effect.reliability_score < 0.25:
        return False
    if effect.avg_return_10d is None or effect.avg_return_10d > -1.0:
        return False
    weak_win_rate = effect.win_rate_10d is not None and effect.win_rate_10d <= 0.35
    weak_lift = effect.lift_vs_baseline_10d is not None and effect.lift_vs_baseline_10d <= -1.0
    return weak_win_rate or weak_lift


def _apply_feedback_block(card: OpportunityCard, effects: list[object]) -> None:
    labels = "、".join(str(effect.label) for effect in effects[:3])
    worst_return = min(
        (effect.avg_return_10d for effect in effects if effect.avg_return_10d is not None),
        default=None,
    )
    worst_win_rate = min(
        (effect.win_rate_10d for effect in effects if effect.win_rate_10d is not None),
        default=None,
    )
    detail = _feedback_block_detail(labels, worst_return, worst_win_rate)
    check = RecommendationQualityCheck(
        code="feedback_quality_gate",
        status="block",
        label="推荐反馈转弱",
        detail=detail,
        score_impact=-0.28,
    )
    if card.recommendation_quality is not None:
        checks = [
            item
            for item in card.recommendation_quality.checks
            if item.code != "feedback_quality_gate"
        ]
        checks.append(check)
        card.recommendation_quality.checks = checks
        card.recommendation_quality.block_count = sum(1 for item in checks if item.status == "block")
        card.recommendation_quality.warn_count = sum(1 for item in checks if item.status == "warn")
        card.recommendation_quality.pass_count = sum(1 for item in checks if item.status == "pass")
        card.recommendation_quality.score = round(_clamp(card.recommendation_quality.score - 0.18), 4)
        card.recommendation_quality.tier = "risk_filtered"
        card.recommendation_quality.summary = "风险过滤：推荐后验证转弱，暂不进入买入候选。"
    card.rank_score = round(min(card.rank_score, 0.38), 4)
    card.dynamic_score = card.rank_score
    if card.recommendation_score is not None:
        card.recommendation_score.final_score = card.rank_score
        card.recommendation_score.tier = "risk_filtered"
        card.recommendation_score.summary = (
            f"推荐分 {card.rank_score:.0%}：历史推荐闭环显示该信号近期失效，已降级过滤。"
        )
    _block_pre_trade_risk(card, detail)
    _block_decision(card, detail)
    note = f"推荐反馈门禁：{labels} 最近推荐后表现转弱，暂不进入买入候选。"
    if note not in card.rank_reasons:
        card.rank_reasons.append(note)
    if note not in card.calibration_notes:
        card.calibration_notes.append(note)


def _feedback_block_detail(
    labels: str,
    avg_return_10d: float | None,
    win_rate_10d: float | None,
) -> str:
    pieces = [f"{labels} 的历史推荐后表现明显走弱"]
    if avg_return_10d is not None:
        pieces.append(f"10 日均值 {avg_return_10d:+.2f}%")
    if win_rate_10d is not None:
        pieces.append(f"10 日胜率 {win_rate_10d:.0%}")
    return "，".join(pieces) + "，需要等待重新走强后再考虑。"


def _block_pre_trade_risk(card: OpportunityCard, detail: str) -> None:
    check = PreTradeRiskCheck(
        code="feedback_quality_gate",
        severity="block",
        title="推荐反馈转弱",
        message=detail,
        action="暂不买入；等待后续推荐样本重新验证。",
    )
    if card.pre_trade_risk is None:
        card.pre_trade_risk = PreTradeRiskProfile(
            status="blocked",
            label="不可买",
            can_buy=False,
            can_size_up=False,
            risk_budget_pct=0.0,
            max_position_pct=0.0,
            next_action=check.action,
            summary=detail,
            checks=[check],
        )
        return
    checks = [item for item in card.pre_trade_risk.checks if item.code != check.code]
    checks.append(check)
    card.pre_trade_risk.status = "blocked"
    card.pre_trade_risk.label = "不可买"
    card.pre_trade_risk.can_buy = False
    card.pre_trade_risk.can_size_up = False
    card.pre_trade_risk.risk_budget_pct = 0.0
    card.pre_trade_risk.max_position_pct = 0.0
    card.pre_trade_risk.next_action = check.action
    card.pre_trade_risk.summary = detail
    card.pre_trade_risk.checks = checks


def _block_decision(card: OpportunityCard, detail: str) -> None:
    if card.decision is None:
        return
    card.decision.action = "avoid"
    card.decision.action_label = "暂不买"
    card.decision.risk_status = "blocked"
    card.decision.suggested_risk_pct = 0.0
    card.decision.max_position_pct = 0.0
    veto = RiskVeto(
        code="feedback_quality_gate",
        severity="block",
        title="推荐反馈转弱",
        message=detail,
    )
    card.decision.risk_vetoes = [
        item for item in card.decision.risk_vetoes if item.code != veto.code
    ] + [veto]


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))
