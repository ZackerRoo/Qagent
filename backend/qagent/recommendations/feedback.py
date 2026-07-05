from __future__ import annotations

from datetime import date

from qagent.domain.models import OpportunityCard
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


def recommendation_feedback_data_health(cards: list[OpportunityCard]) -> dict[str, str]:
    adjusted = sum(
        1
        for card in cards
        if any("推荐反馈校准" in reason for reason in card.rank_reasons)
    )
    return {
        "recommendation_feedback_cards": str(len(cards)),
        "recommendation_feedback_adjusted": str(adjusted),
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


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))
