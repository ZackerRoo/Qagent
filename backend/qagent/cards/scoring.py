from qagent.domain.models import Signal


SIGNAL_WEIGHTS = {
    "trend_strength": 0.25,
    "pullback": 0.20,
    "breakout": 0.25,
    "volume_anomaly": 0.15,
    "limit_status": 0.15,
    "event_catalyst": 0.15,
}


def aggregate_score(signals: list[Signal]) -> float:
    if not signals:
        return 0.0

    score = 0.0
    weight_sum = 0.0
    for signal in signals:
        weight = SIGNAL_WEIGHTS.get(signal.signal_type.value, 0.10)
        score += signal.score * weight
        weight_sum += weight
    return round(score / weight_sum, 4) if weight_sum else 0.0
