from dataclasses import dataclass

import pandas as pd

from qagent.factors.models import FactorExposure, FactorRanking


FACTOR_WEIGHTS = {
    "momentum": 0.30,
    "trend_quality": 0.25,
    "liquidity": 0.15,
    "low_risk": 0.20,
    "reversal": 0.10,
}

A_SHARE_FACTOR_WEIGHTS = {
    "momentum": 0.16,
    "trend_quality": 0.24,
    "liquidity": 0.16,
    "low_risk": 0.32,
    "reversal": 0.12,
}


@dataclass
class _RawFactors:
    instrument_id: str
    momentum_raw: float | None
    trend_quality_raw: float | None
    liquidity_raw: float | None
    low_risk_raw: float | None
    reversal_raw: float | None
    distance_ma20: float | None
    volatility_20d: float | None
    max_drawdown_60d: float | None
    volume_ratio_5_20: float | None
    data_completeness: float
    missing_data: list[str]
    flags: list[str]


def build_factor_rankings(bars: pd.DataFrame) -> list[FactorRanking]:
    if bars.empty:
        return []
    raw_items = [_raw_factors(symbol, frame) for symbol, frame in bars.groupby("instrument_id")]
    scores = {
        "momentum": _rank_scores({item.instrument_id: item.momentum_raw for item in raw_items}),
        "trend_quality": _rank_scores(
            {item.instrument_id: item.trend_quality_raw for item in raw_items}
        ),
        "liquidity": _rank_scores({item.instrument_id: item.liquidity_raw for item in raw_items}),
        "low_risk": _rank_scores({item.instrument_id: item.low_risk_raw for item in raw_items}),
        "reversal": _rank_scores({item.instrument_id: item.reversal_raw for item in raw_items}),
    }
    rankings: list[FactorRanking] = []
    for item in raw_items:
        weights = _factor_weights(item.instrument_id)
        component_score = sum(
            scores[factor][item.instrument_id] * weight for factor, weight in weights.items()
        )
        penalty = _execution_penalty(item)
        factor_score = _clamp(component_score * item.data_completeness - penalty)
        rankings.append(
            FactorRanking(
                instrument_id=item.instrument_id,
                factor_score=round(factor_score, 4),
                factor_rank=0,
                percentile=0.0,
                momentum_score=round(scores["momentum"][item.instrument_id], 4),
                trend_quality_score=round(scores["trend_quality"][item.instrument_id], 4),
                liquidity_score=round(scores["liquidity"][item.instrument_id], 4),
                low_risk_score=round(scores["low_risk"][item.instrument_id], 4),
                reversal_score=round(scores["reversal"][item.instrument_id], 4),
                execution_penalty=round(penalty, 4),
                data_completeness=round(item.data_completeness, 4),
                factor_exposures=_exposures(item, scores, weights),
                flags=item.flags,
                missing_data=item.missing_data,
            )
        )
    rankings.sort(key=lambda ranking: ranking.factor_score, reverse=True)
    total = len(rankings)
    for index, ranking in enumerate(rankings, start=1):
        ranking.factor_rank = index
        ranking.percentile = round(1.0 if total == 1 else 1 - ((index - 1) / (total - 1)), 4)
    return rankings


def _raw_factors(instrument_id: str, bars: pd.DataFrame) -> _RawFactors:
    ordered = bars.sort_values("trade_date").copy()
    close = pd.to_numeric(ordered["close"], errors="coerce").dropna()
    volume = pd.to_numeric(ordered["volume"], errors="coerce").dropna()
    missing: list[str] = []
    flags: list[str] = []
    if len(close) < 20:
        missing.append("20d_return")
    if len(close) < 60:
        missing.append("60d_return")
    if len(close) < 120:
        missing.append("120d_return")
    if len(close) < 120:
        flags.append("insufficient_history")

    ret_20 = _period_return(close, 20)
    ret_60 = _period_return(close, 60)
    ret_120 = _period_return(close, 120)
    momentum_values = [(ret_20, 0.30), (ret_60, 0.40), (ret_120, 0.30)]
    momentum_raw = _weighted_available(momentum_values)

    latest_close = float(close.iloc[-1]) if not close.empty else None
    ma20 = _moving_average(close, 20)
    ma50 = _moving_average(close, 50)
    ma100 = _moving_average(close, 100)
    distance_ma20 = (latest_close / ma20 - 1) if latest_close is not None and ma20 else None
    alignment = 0.0
    alignment_inputs = 0
    if latest_close is not None and ma20:
        alignment += 1.0 if latest_close >= ma20 else 0.0
        alignment_inputs += 1
    if ma20 and ma50:
        alignment += 1.0 if ma20 >= ma50 else 0.0
        alignment_inputs += 1
    if ma50 and ma100:
        alignment += 1.0 if ma50 >= ma100 else 0.0
        alignment_inputs += 1
    trend_quality_raw = alignment / alignment_inputs if alignment_inputs else None
    if trend_quality_raw is not None and distance_ma20 is not None:
        trend_quality_raw += max(min(distance_ma20, 0.12), -0.12)

    avg_volume_20 = float(volume.tail(20).mean()) if len(volume) >= 20 else None
    avg_volume_5 = float(volume.tail(5).mean()) if len(volume) >= 5 else None
    volume_ratio_5_20 = avg_volume_5 / avg_volume_20 if avg_volume_5 and avg_volume_20 else None
    liquidity_raw = avg_volume_20

    returns = close.pct_change().dropna()
    volatility_20d = float(returns.tail(20).std()) if len(returns) >= 20 else None
    max_drawdown_60d = _max_drawdown(close.tail(60)) if len(close) >= 20 else None
    low_risk_parts = []
    if volatility_20d is not None:
        low_risk_parts.append(-volatility_20d)
    if max_drawdown_60d is not None:
        low_risk_parts.append(max_drawdown_60d)
    low_risk_raw = sum(low_risk_parts) / len(low_risk_parts) if low_risk_parts else None

    ret_5 = _period_return(close, 5)
    reversal_raw = None
    if ret_5 is not None and latest_close is not None and ma20:
        reversal_raw = -ret_5 if latest_close >= ma20 else ret_5

    if distance_ma20 is not None and distance_ma20 > 0.12:
        flags.append("overextended")
    if volatility_20d is not None and volatility_20d > 0.045:
        flags.append("high_volatility")
    if avg_volume_20 is not None and avg_volume_20 < 300_000:
        flags.append("low_liquidity")
    completeness = (5 - len(set(missing))) / 5
    return _RawFactors(
        instrument_id=instrument_id,
        momentum_raw=momentum_raw,
        trend_quality_raw=trend_quality_raw,
        liquidity_raw=liquidity_raw,
        low_risk_raw=low_risk_raw,
        reversal_raw=reversal_raw,
        distance_ma20=distance_ma20,
        volatility_20d=volatility_20d,
        max_drawdown_60d=max_drawdown_60d,
        volume_ratio_5_20=volume_ratio_5_20,
        data_completeness=max(0.2, completeness),
        missing_data=sorted(set(missing)),
        flags=sorted(set(flags)),
    )


def _period_return(close: pd.Series, window: int) -> float | None:
    if len(close) <= window:
        return None
    previous = float(close.iloc[-window - 1])
    if previous == 0:
        return None
    return float(close.iloc[-1]) / previous - 1


def _moving_average(close: pd.Series, window: int) -> float | None:
    if len(close) < window:
        return None
    return float(close.tail(window).mean())


def _max_drawdown(close: pd.Series) -> float | None:
    if close.empty:
        return None
    running_max = close.cummax()
    drawdown = close / running_max - 1
    return float(drawdown.min())


def _weighted_available(values: list[tuple[float | None, float]]) -> float | None:
    available = [(value, weight) for value, weight in values if value is not None]
    if not available:
        return None
    weight_sum = sum(weight for _, weight in available)
    return sum(value * weight for value, weight in available) / weight_sum


def _rank_scores(values: dict[str, float | None]) -> dict[str, float]:
    valid = {key: value for key, value in values.items() if value is not None}
    if not valid:
        return {key: 0.5 for key in values}
    sorted_items = sorted(valid.items(), key=lambda item: item[1])
    if len(sorted_items) == 1:
        ranked = {sorted_items[0][0]: 0.5}
    else:
        ranked = {
            key: index / (len(sorted_items) - 1)
            for index, (key, _) in enumerate(sorted_items)
        }
    return {key: round(ranked.get(key, 0.35), 4) for key in values}


def _execution_penalty(item: _RawFactors) -> float:
    penalty = 0.0
    if item.distance_ma20 is not None and item.distance_ma20 > 0.12:
        penalty += min(0.20, (item.distance_ma20 - 0.12) * 1.5)
    if "low_liquidity" in item.flags:
        penalty += 0.10
    if "high_volatility" in item.flags:
        penalty += 0.08
    return _clamp(penalty)


def _factor_weights(instrument_id: str) -> dict[str, float]:
    if instrument_id.startswith("CN:"):
        return A_SHARE_FACTOR_WEIGHTS
    return FACTOR_WEIGHTS


def _exposures(
    item: _RawFactors,
    scores: dict[str, dict[str, float]],
    weights: dict[str, float],
) -> list[FactorExposure]:
    return [
        FactorExposure(
            factor_id="momentum",
            label="Momentum",
            raw_value=item.momentum_raw,
            score=scores["momentum"][item.instrument_id],
            weight=weights["momentum"],
            explanation="20/60/120 day price momentum ranked within the scan universe; A-share ranking caps pure momentum influence.",
        ),
        FactorExposure(
            factor_id="trend_quality",
            label="Trend quality",
            raw_value=item.trend_quality_raw,
            score=scores["trend_quality"][item.instrument_id],
            weight=weights["trend_quality"],
            explanation="Moving-average alignment and distance from 20DMA.",
        ),
        FactorExposure(
            factor_id="liquidity",
            label="Liquidity",
            raw_value=item.liquidity_raw,
            score=scores["liquidity"][item.instrument_id],
            weight=weights["liquidity"],
            explanation="20 day average volume ranked within the scan universe.",
        ),
        FactorExposure(
            factor_id="low_risk",
            label="Low risk",
            raw_value=item.low_risk_raw,
            score=scores["low_risk"][item.instrument_id],
            weight=weights["low_risk"],
            explanation="Lower 20 day volatility and shallower 60 day drawdown score better; this carries higher weight for A-shares.",
        ),
        FactorExposure(
            factor_id="reversal",
            label="Reversal setup",
            raw_value=item.reversal_raw,
            score=scores["reversal"][item.instrument_id],
            weight=weights["reversal"],
            explanation="Short-term pullback pressure inside an intact trend.",
        ),
    ]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
