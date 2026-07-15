from dataclasses import dataclass

from qagent.domain.enums import Direction
from qagent.domain.models import Signal


SIGNAL_WEIGHTS = {
    "trend_strength": 0.25,
    "pullback": 0.20,
    "breakout": 0.25,
    "volume_anomaly": 0.15,
    "limit_status": 0.15,
    "event_catalyst": 0.15,
}


@dataclass(frozen=True)
class SignalConsensusResult:
    bullish_support: float
    bearish_pressure: float
    neutral_weight: float
    agreement: float
    conflict: float
    net_score: float
    dominant_direction: Direction
    signal_count: int


def aggregate_score(signals: list[Signal]) -> float:
    return calculate_signal_consensus(signals).net_score


def calculate_signal_consensus(signals: list[Signal]) -> SignalConsensusResult:
    if not signals:
        return SignalConsensusResult(
            bullish_support=0.0,
            bearish_pressure=0.0,
            neutral_weight=0.0,
            agreement=0.0,
            conflict=0.0,
            net_score=0.0,
            dominant_direction=Direction.NEUTRAL,
            signal_count=0,
        )

    weight_sum = 0.0
    directional_support = {
        Direction.BULLISH: 0.0,
        Direction.BEARISH: 0.0,
        Direction.NEUTRAL: 0.0,
    }
    for signal in signals:
        weight = SIGNAL_WEIGHTS.get(signal.signal_type.value, 0.10)
        directional_support[signal.direction] += signal.score * weight
        weight_sum += weight
    if weight_sum <= 0:
        return calculate_signal_consensus([])

    bullish = directional_support[Direction.BULLISH] / weight_sum
    bearish = directional_support[Direction.BEARISH] / weight_sum
    neutral = directional_support[Direction.NEUTRAL] / weight_sum
    active_support = bullish + bearish + neutral
    agreement = max(bullish, bearish, neutral) / active_support if active_support else 0.0
    directional_total = bullish + bearish
    conflict = 2 * min(bullish, bearish) / directional_total if bullish > 0 and bearish > 0 else 0.0
    net_score = _clamp(bullish + neutral * 0.5 - bearish)
    dominant_direction = max(
        (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL),
        key=lambda direction: (
            directional_support[direction],
            1 if direction == Direction.NEUTRAL else 0,
        ),
    )
    if bullish == bearish and bullish >= neutral:
        dominant_direction = Direction.NEUTRAL

    return SignalConsensusResult(
        bullish_support=round(_clamp(bullish), 4),
        bearish_pressure=round(_clamp(bearish), 4),
        neutral_weight=round(_clamp(neutral), 4),
        agreement=round(_clamp(agreement), 4),
        conflict=round(_clamp(conflict), 4),
        net_score=round(net_score, 4),
        dominant_direction=dominant_direction,
        signal_count=len(signals),
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
