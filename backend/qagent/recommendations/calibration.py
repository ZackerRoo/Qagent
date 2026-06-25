from qagent.domain.models import OpportunityCard, StrategyCalibration
from qagent.strategies.models import StrategyHealth


def apply_strategy_calibration(
    cards: list[OpportunityCard],
    strategy_health: list[StrategyHealth],
) -> None:
    health_by_id = {item.strategy_id: item for item in strategy_health}
    for card in cards:
        if card.primary_strategy_id is None:
            continue
        health = health_by_id.get(card.primary_strategy_id)
        if health is None:
            continue
        card.strategy_calibration = _calibration_from_health(health)
        adjustment = _rank_adjustment(health)
        if adjustment:
            card.rank_score = round(max(0.0, min(1.0, card.rank_score + adjustment)), 4)
        card.rank_reasons.append(_rank_reason(card.strategy_calibration))


def _calibration_from_health(health: StrategyHealth) -> StrategyCalibration:
    return StrategyCalibration(
        strategy_id=health.strategy_id,
        readiness=health.readiness,
        sample_count=health.sample_count,
        win_rate_10d=health.win_rate_10d,
        avg_return_10d=health.avg_return_10d,
        avg_return_20d=health.avg_return_20d,
        max_loss_10d=health.max_loss_10d,
        message=_message(health),
    )


def _rank_adjustment(health: StrategyHealth) -> float:
    if health.readiness == "validated" and (health.win_rate_10d or 0) >= 55:
        return 0.04
    if health.readiness in {"limited_sample", "insufficient_history"}:
        return -0.02
    if health.win_rate_10d is not None and health.win_rate_10d < 45:
        return -0.04
    return 0.0


def _rank_reason(calibration: StrategyCalibration) -> str:
    win_rate = "-" if calibration.win_rate_10d is None else f"{calibration.win_rate_10d:.2f}%"
    return (
        f"策略校准：{calibration.strategy_id} 最近样本 {calibration.sample_count} 个，"
        f"10日胜率 {win_rate}，状态 {calibration.readiness}。"
    )


def _message(health: StrategyHealth) -> str:
    if health.sample_count <= 0:
        return "策略校准：暂无可用历史样本，推荐只能按当前信号处理。"
    win_rate = "-" if health.win_rate_10d is None else f"{health.win_rate_10d:.2f}%"
    avg_10d = "-" if health.avg_return_10d is None else f"{health.avg_return_10d:+.2f}%"
    return (
        f"策略校准：样本{health.sample_count}个，10日胜率{win_rate}，"
        f"10日均值{avg_10d}，状态{health.readiness}。"
    )
