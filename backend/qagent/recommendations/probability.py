from __future__ import annotations

from qagent.domain.models import (
    OpportunityCard,
    RecommendationProbabilityForecast,
    StrategyCalibration,
)
from qagent.strategies.models import StrategyHealth


def apply_probability_calibration(
    cards: list[OpportunityCard],
    strategy_health: list[StrategyHealth],
) -> list[OpportunityCard]:
    health_by_id = {item.strategy_id: item for item in strategy_health}
    for card in cards:
        health = health_by_id.get(card.primary_strategy_id or "")
        forecast = build_probability_forecast(card, health)
        card.probability_forecast = forecast
        if forecast.rank_adjustment:
            card.rank_score = round(_clamp(card.rank_score + forecast.rank_adjustment), 4)
            card.dynamic_score = card.rank_score
            if card.recommendation_score is not None:
                card.recommendation_score.final_score = card.rank_score
                card.recommendation_score.summary = (
                    f"推荐分 {card.rank_score:.0%}：质量 {card.recommendation_score.quality_score:.0%}，"
                    f"概率校准 {forecast.rank_adjustment:+.0%}，"
                    f"10日胜率估计 {forecast.win_probability_10d:.0%}。"
                )
        note = (
            f"概率校准：10日胜率估计 {forecast.win_probability_10d:.0%}，"
            f"10日期望收益 {forecast.expected_return_10d:+.1f}% ，"
            f"策略权重 x{forecast.strategy_multiplier:.2f}。"
        )
        if note not in card.rank_reasons:
            card.rank_reasons.append(note)
        if forecast.reason not in card.calibration_notes:
            card.calibration_notes.append(forecast.reason)
    return cards


def build_probability_forecast(
    card: OpportunityCard,
    health: StrategyHealth | None = None,
) -> RecommendationProbabilityForecast:
    calibration = card.strategy_calibration
    strategy = _strategy_inputs(health, calibration)
    sample_count = strategy.sample_count
    confidence = _confidence(strategy.readiness, sample_count)
    multiplier = _strategy_multiplier(strategy)
    composite_score = _composite_score(card)
    base_win_10d = _clamp(0.34 + composite_score * 0.36, 0.2, 0.78)
    if strategy.win_rate_10d is None:
        calibrated_win_10d = base_win_10d
    else:
        sample_weight = _sample_weight(sample_count)
        history_win = _clamp(strategy.win_rate_10d / 100, 0.15, 0.85)
        calibrated_win_10d = base_win_10d * (1 - sample_weight) + history_win * sample_weight
    calibrated_win_10d = _clamp(calibrated_win_10d * _probability_multiplier(multiplier), 0.16, 0.84)

    action_adjustment = _action_probability_adjustment(card)
    win_10d = _clamp(calibrated_win_10d + action_adjustment, 0.14, 0.86)
    win_5d = _clamp(win_10d - 0.04 + min(0.04, max(0.0, card.strategy_score - 0.55) * 0.12), 0.12, 0.84)
    win_20d = _clamp(win_10d + 0.03 + _expected_trend_bonus(strategy.avg_return_20d), 0.16, 0.88)

    expected_10d = _expected_return(card, win_10d, strategy.avg_return_10d, horizon_multiplier=0.75)
    expected_20d = _expected_return(card, win_20d, strategy.avg_return_20d, horizon_multiplier=1.05)
    rank_adjustment = _rank_adjustment(
        win_probability_10d=win_10d,
        expected_return_10d=expected_10d,
        multiplier=multiplier,
        confidence=confidence,
        card=card,
    )
    score_band = _score_band(win_10d, expected_10d, confidence)
    return RecommendationProbabilityForecast(
        win_probability_5d=round(win_5d, 4),
        win_probability_10d=round(win_10d, 4),
        win_probability_20d=round(win_20d, 4),
        expected_return_10d=round(expected_10d, 2),
        expected_return_20d=round(expected_20d, 2),
        confidence=confidence,
        sample_count=sample_count,
        calibration_source=strategy.source,
        score_band=score_band,
        strategy_multiplier=round(multiplier, 4),
        rank_adjustment=round(rank_adjustment, 4),
        reason=_reason(score_band, confidence, sample_count, win_10d, expected_10d, multiplier),
        evidence=_evidence(card, strategy, win_10d, expected_10d),
    )


def probability_calibration_data_health(cards: list[OpportunityCard]) -> dict[str, str]:
    forecasts = [card.probability_forecast for card in cards if card.probability_forecast]
    if not forecasts:
        return {
            "probability_calibration_cards": "0",
            "probability_calibration_validated": "0",
            "strategy_auto_weighting_applied": "0",
        }
    adjusted = sum(1 for forecast in forecasts if abs(forecast.rank_adjustment) >= 0.0001)
    validated = sum(1 for forecast in forecasts if forecast.confidence == "validated")
    limited = sum(1 for forecast in forecasts if forecast.confidence == "limited_sample")
    avg_win = sum(forecast.win_probability_10d for forecast in forecasts) / len(forecasts)
    avg_expected = sum(forecast.expected_return_10d for forecast in forecasts) / len(forecasts)
    return {
        "probability_calibration_cards": str(len(forecasts)),
        "probability_calibration_validated": str(validated),
        "probability_calibration_limited_sample": str(limited),
        "probability_calibration_avg_win_10d": f"{avg_win:.4f}",
        "probability_calibration_avg_expected_10d": f"{avg_expected:.2f}",
        "strategy_auto_weighting_applied": str(adjusted),
    }


class _StrategyInputs:
    def __init__(
        self,
        *,
        strategy_id: str,
        readiness: str,
        sample_count: int,
        win_rate_10d: float | None,
        avg_return_10d: float | None,
        avg_return_20d: float | None,
        max_loss_10d: float | None,
        source: str,
    ):
        self.strategy_id = strategy_id
        self.readiness = readiness
        self.sample_count = sample_count
        self.win_rate_10d = win_rate_10d
        self.avg_return_10d = avg_return_10d
        self.avg_return_20d = avg_return_20d
        self.max_loss_10d = max_loss_10d
        self.source = source


def _strategy_inputs(
    health: StrategyHealth | None,
    calibration: StrategyCalibration | None,
) -> _StrategyInputs:
    if health is not None:
        return _StrategyInputs(
            strategy_id=health.strategy_id,
            readiness=health.readiness,
            sample_count=health.sample_count,
            win_rate_10d=health.win_rate_10d,
            avg_return_10d=health.avg_return_10d,
            avg_return_20d=health.avg_return_20d,
            max_loss_10d=health.max_loss_10d,
            source="strategy_health",
        )
    if calibration is not None:
        return _StrategyInputs(
            strategy_id=calibration.strategy_id,
            readiness=calibration.readiness,
            sample_count=calibration.sample_count,
            win_rate_10d=calibration.win_rate_10d,
            avg_return_10d=calibration.avg_return_10d,
            avg_return_20d=calibration.avg_return_20d,
            max_loss_10d=calibration.max_loss_10d,
            source="card_strategy_calibration",
        )
    return _StrategyInputs(
        strategy_id="unclassified",
        readiness="unverified",
        sample_count=0,
        win_rate_10d=None,
        avg_return_10d=None,
        avg_return_20d=None,
        max_loss_10d=None,
        source="score_model",
    )


def _composite_score(card: OpportunityCard) -> float:
    quality = card.recommendation_quality.score if card.recommendation_quality else card.quality_score
    quality_score = card.rank_score if quality is None else quality
    conviction = card.decision.conviction_score if card.decision else card.strategy_score
    risk_penalty = 0.0
    if card.decision and card.decision.risk_status == "blocked":
        risk_penalty += 0.18
    if card.recommendation_quality and card.recommendation_quality.block_count > 0:
        risk_penalty += 0.12
    return _clamp(
        card.rank_score * 0.32
        + card.factor_score * 0.22
        + card.strategy_score * 0.18
        + conviction * 0.14
        + quality_score * 0.14
        - risk_penalty,
        0.0,
        1.0,
    )


def _confidence(readiness: str, sample_count: int) -> str:
    if readiness == "validated" and sample_count >= 20:
        return "validated"
    if sample_count > 0:
        return "limited_sample"
    return "unverified"


def _strategy_multiplier(strategy: _StrategyInputs) -> float:
    if strategy.sample_count < 10:
        return 0.86
    if (strategy.avg_return_10d is not None and strategy.avg_return_10d < 0) or (
        strategy.max_loss_10d is not None and strategy.max_loss_10d <= -8
    ):
        return 0.72
    if strategy.win_rate_10d is not None and strategy.win_rate_10d < 45:
        return 0.78
    if (
        strategy.readiness == "validated"
        and (strategy.win_rate_10d or 0) >= 56
        and (strategy.avg_return_10d or 0) >= 0
    ):
        return 1.18
    if (strategy.win_rate_10d or 0) >= 52 and (strategy.avg_return_10d or 0) >= 0:
        return 1.06
    return 1.0


def _probability_multiplier(multiplier: float) -> float:
    return 1.0 + (multiplier - 1.0) * 0.42


def _sample_weight(sample_count: int) -> float:
    if sample_count <= 0:
        return 0.0
    if sample_count < 10:
        return min(0.18, sample_count / 10 * 0.18)
    return min(0.58, 0.22 + sample_count / 80 * 0.36)


def _action_probability_adjustment(card: OpportunityCard) -> float:
    action = card.decision.action if card.decision else ""
    if action == "candidate_entry":
        return 0.025
    if action == "watch_trigger":
        return -0.01
    if action == "wait_pullback":
        return -0.025
    if action == "avoid":
        return -0.08
    return 0.0


def _expected_trend_bonus(avg_return_20d: float | None) -> float:
    if avg_return_20d is None:
        return 0.0
    return _clamp(avg_return_20d / 100 * 0.35, -0.025, 0.035)


def _expected_return(
    card: OpportunityCard,
    win_probability: float,
    strategy_average: float | None,
    *,
    horizon_multiplier: float,
) -> float:
    target = card.position_scenario.target_1_gain_pct if card.position_scenario else None
    downside = card.position_scenario.planned_loss_pct if card.position_scenario else None
    if target is None:
        target = card.scenario.target_1_pct
    if downside is None:
        downside = card.scenario.downside_pct
    path_expected = (win_probability * target + (1 - win_probability) * downside) * horizon_multiplier
    if strategy_average is None:
        return path_expected
    return strategy_average * 0.55 + path_expected * 0.45


def _rank_adjustment(
    *,
    win_probability_10d: float,
    expected_return_10d: float,
    multiplier: float,
    confidence: str,
    card: OpportunityCard,
) -> float:
    adjustment = (multiplier - 1.0) * 0.16
    if win_probability_10d >= 0.58 and expected_return_10d >= 1.0:
        adjustment += 0.025
    elif win_probability_10d < 0.45 or expected_return_10d < 0:
        adjustment -= 0.04
    if confidence == "limited_sample":
        adjustment = min(adjustment, 0.0) - 0.008
    if confidence == "unverified":
        adjustment = min(adjustment, 0.0) - 0.018
    if card.decision and card.decision.risk_status == "blocked":
        adjustment -= 0.035
    return _clamp(adjustment, -0.09, 0.07)


def _score_band(win_probability_10d: float, expected_return_10d: float, confidence: str) -> str:
    if confidence == "validated" and win_probability_10d >= 0.58 and expected_return_10d >= 1.0:
        return "高胜率候选"
    if win_probability_10d >= 0.52 and expected_return_10d >= 0:
        return "可验证候选"
    if win_probability_10d < 0.45 or expected_return_10d < 0:
        return "风险偏弱"
    return "观察候选"


def _reason(
    score_band: str,
    confidence: str,
    sample_count: int,
    win_probability_10d: float,
    expected_return_10d: float,
    multiplier: float,
) -> str:
    confidence_label = {
        "validated": "历史样本已验证",
        "limited_sample": "样本仍偏少",
        "unverified": "缺少历史样本",
    }.get(confidence, confidence)
    return (
        f"{score_band}：{confidence_label}，样本 {sample_count}，"
        f"10日胜率估计 {win_probability_10d:.0%}，"
        f"10日期望收益 {expected_return_10d:+.1f}%，策略权重 x{multiplier:.2f}。"
    )


def _evidence(
    card: OpportunityCard,
    strategy: _StrategyInputs,
    win_probability_10d: float,
    expected_return_10d: float,
) -> list[str]:
    items = [
        f"综合分 {card.rank_score:.0%}，因子分 {card.factor_score:.0%}，策略分 {card.strategy_score:.0%}",
        f"策略历史：样本 {strategy.sample_count}，10日胜率 {_fmt_pct(strategy.win_rate_10d)}，10日均值 {_fmt_signed_pct(strategy.avg_return_10d)}",
        f"交易计划：触发 {card.entry_plan.trigger_price}，止损 {card.exit_plan.initial_stop}，目标 {card.exit_plan.target_1}",
        f"模型输出：10日胜率 {win_probability_10d:.0%}，期望收益 {expected_return_10d:+.1f}%",
    ]
    if card.recommendation_quality is not None:
        items.append(
            f"质量门槛：{card.recommendation_quality.tier}，通过 {card.recommendation_quality.pass_count}，"
            f"警告 {card.recommendation_quality.warn_count}，阻断 {card.recommendation_quality.block_count}"
        )
    return items


def _fmt_pct(value: float | None) -> str:
    return "待验证" if value is None else f"{value:.1f}%"


def _fmt_signed_pct(value: float | None) -> str:
    return "待验证" if value is None else f"{value:+.1f}%"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
