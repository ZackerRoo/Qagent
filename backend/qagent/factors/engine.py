from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from qagent.factors.models import FactorExposure, FactorRanking
from qagent.features import FeatureSnapshot, build_feature_snapshot
from qagent.market.indicators import regression_quality_momentum
from qagent.strategy_data.models import FundamentalSnapshot


RESEARCH_FACTOR_WEIGHTS = {
    "profitability": 0.0,
    "growth": 0.0,
    "downside_risk": 0.0,
    "market_adjusted_momentum": 0.0,
}

FACTOR_WEIGHTS = {
    "valuation": 0.08,
    "size": 0.06,
    "quality": 0.08,
    "momentum": 0.22,
    "trend_quality": 0.22,
    "liquidity": 0.12,
    "low_risk": 0.10,
    "risk_filter": 0.06,
    "reversal": 0.06,
    **RESEARCH_FACTOR_WEIGHTS,
}

A_SHARE_FACTOR_WEIGHTS = {
    "valuation": 0.14,
    "size": 0.10,
    "quality": 0.12,
    "momentum": 0.14,
    "trend_quality": 0.20,
    "liquidity": 0.10,
    "low_risk": 0.08,
    "risk_filter": 0.08,
    "reversal": 0.04,
    **RESEARCH_FACTOR_WEIGHTS,
}

ETF_FACTOR_WEIGHTS = {
    "momentum": 0.21875,
    "trend_quality": 0.3125,
    "liquidity": 0.15625,
    "low_risk": 0.125,
    "risk_filter": 0.125,
    "reversal": 0.0625,
    **RESEARCH_FACTOR_WEIGHTS,
}

FACTOR_FEATURE_SET_VERSION = "factor-cross-sectional-v3-research-exposures"

_STOCK_ASSET_TYPES = frozenset({"stock", "equity", "1"})
_ETF_ASSET_TYPES = frozenset({"etf", "fund", "index_fund", "5"})
_ETF_SYMBOL_PREFIXES = ("15", "16", "51", "52", "56", "58")

_AUTHORITATIVE_FACTOR_SCORE_FIELDS = {
    "valuation": "valuation_score",
    "size": "size_score",
    "quality": "quality_score",
    "momentum": "momentum_score",
    "trend_quality": "trend_quality_score",
    "liquidity": "liquidity_score",
    "low_risk": "low_risk_score",
    "risk_filter": "risk_filter_score",
    "reversal": "reversal_score",
}

_RESEARCH_FACTOR_SCORE_FIELDS = {
    "profitability": "profitability_score",
    "growth": "growth_score",
    "downside_risk": "downside_risk_score",
    "market_adjusted_momentum": "market_adjusted_momentum_score",
}

_FACTOR_SCORE_FIELDS = {
    **_AUTHORITATIVE_FACTOR_SCORE_FIELDS,
    **_RESEARCH_FACTOR_SCORE_FIELDS,
}


@dataclass
class _RawFactors:
    instrument_id: str
    asset_type: str
    valuation_raw: float | None
    size_raw: float | None
    quality_raw: float | None
    momentum_raw: float | None
    trend_quality_raw: float | None
    liquidity_raw: float | None
    low_risk_raw: float | None
    risk_filter_raw: float | None
    reversal_raw: float | None
    profitability_raw: float | None
    growth_raw: float | None
    downside_risk_raw: float | None
    market_adjusted_momentum_raw: float | None
    distance_ma20: float | None
    volatility_20d: float | None
    max_drawdown_60d: float | None
    volume_ratio_5_20: float | None
    market_cap: float | None
    data_completeness: float
    missing_data: list[str]
    flags: list[str]


def build_factor_rankings(
    bars: pd.DataFrame,
    fundamentals: list[FundamentalSnapshot] | dict[str, FundamentalSnapshot] | None = None,
    *,
    asset_types: Mapping[str, str] | None = None,
) -> list[FactorRanking]:
    if bars.empty:
        return []
    fundamental_by_id = _latest_fundamentals(fundamentals)
    grouped_bars = [
        (
            symbol,
            frame,
            _asset_type_from_bars(
                symbol,
                frame,
                override=(asset_types or {}).get(symbol),
            ),
        )
        for symbol, frame in bars.groupby("instrument_id")
    ]
    market_returns = _market_returns_by_asset_type(grouped_bars)
    raw_items = [
        _raw_factors(
            symbol,
            frame,
            fundamental_by_id.get(symbol),
            asset_type=asset_type,
            market_returns=market_returns.get(asset_type),
        )
        for symbol, frame, asset_type in grouped_bars
    ]
    scores = _bucketed_factor_scores(raw_items)
    rankings: list[FactorRanking] = []
    for item in raw_items:
        weights = _factor_weights(item.instrument_id, item.asset_type)
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
                valuation_score=round(scores["valuation"][item.instrument_id], 4),
                size_score=round(scores["size"][item.instrument_id], 4),
                quality_score=round(scores["quality"][item.instrument_id], 4),
                momentum_score=round(scores["momentum"][item.instrument_id], 4),
                trend_quality_score=round(scores["trend_quality"][item.instrument_id], 4),
                liquidity_score=round(scores["liquidity"][item.instrument_id], 4),
                low_risk_score=round(scores["low_risk"][item.instrument_id], 4),
                risk_filter_score=round(scores["risk_filter"][item.instrument_id], 4),
                reversal_score=round(scores["reversal"][item.instrument_id], 4),
                profitability_score=round(scores["profitability"][item.instrument_id], 4),
                growth_score=round(scores["growth"][item.instrument_id], 4),
                downside_risk_score=round(scores["downside_risk"][item.instrument_id], 4),
                market_adjusted_momentum_score=round(
                    scores["market_adjusted_momentum"][item.instrument_id],
                    4,
                ),
                execution_penalty=round(penalty, 4),
                data_completeness=round(item.data_completeness, 4),
                factor_exposures=_exposures(item, scores, weights),
                flags=item.flags,
                missing_data=item.missing_data,
            )
        )
    rankings.sort(key=lambda ranking: (-ranking.factor_score, ranking.instrument_id))
    rankings_by_id = {ranking.instrument_id: ranking for ranking in rankings}
    buckets: dict[str, list[FactorRanking]] = {}
    for item in raw_items:
        buckets.setdefault(item.asset_type, []).append(rankings_by_id[item.instrument_id])
    for bucket in buckets.values():
        bucket.sort(key=lambda ranking: (-ranking.factor_score, ranking.instrument_id))
        total = len(bucket)
        for index, ranking in enumerate(bucket, start=1):
            ranking.factor_rank = index
            ranking.percentile = round(
                1.0 if total == 1 else 1 - ((index - 1) / (total - 1)),
                4,
            )
    return rankings


def rerank_factor_rankings(
    rankings: Iterable[FactorRanking],
    *,
    instrument_ids: Iterable[str] | None = None,
) -> list[FactorRanking]:
    """Rebuild cross-sectional scores over one canonical, complete universe."""

    ranking_by_id = _ranking_by_id(rankings)
    universe = sorted(
        set(instrument_ids) if instrument_ids is not None else set(ranking_by_id)
    )
    raw_scores = {
        instrument_id: {
            factor_id: raw_value
            for factor_id, raw_value in _raw_exposure_scores(
                ranking_by_id.get(instrument_id)
            ).items()
            if factor_id in _AUTHORITATIVE_FACTOR_SCORE_FIELDS
        }
        for instrument_id in universe
    }
    cross_sectional_scores = _cross_sectional_scores(raw_scores)
    rescored = [
        _rescore_ranking(ranking_by_id[instrument_id], cross_sectional_scores[instrument_id])
        for instrument_id in universe
        if instrument_id in ranking_by_id
    ]
    rescored.sort(key=lambda ranking: (-ranking.factor_score, ranking.instrument_id))
    total = len(rescored)
    return [
        ranking.model_copy(
            update={
                "factor_rank": index,
                "percentile": round(
                    1.0 if total == 1 else 1 - ((index - 1) / (total - 1)),
                    4,
                ),
            }
        )
        for index, ranking in enumerate(rescored, start=1)
    ]


def build_factor_feature_snapshot(
    rankings: Iterable[FactorRanking],
    *,
    as_of: date | datetime,
    dataset_revision: int | str,
    instrument_ids: Iterable[str] | None = None,
) -> FeatureSnapshot:
    ranking_by_id = _ranking_by_id(rankings)
    universe = sorted(
        set(instrument_ids) if instrument_ids is not None else set(ranking_by_id)
    )
    raw_scores = {
        instrument_id: {
            factor_id: raw_value
            for factor_id, raw_value in _raw_exposure_scores(
                ranking_by_id.get(instrument_id)
            ).items()
            if factor_id in _AUTHORITATIVE_FACTOR_SCORE_FIELDS
        }
        for instrument_id in universe
    }
    cross_sectional_scores = {
        instrument_id: {
            factor_id: float(getattr(ranking_by_id[instrument_id], score_field))
            for factor_id, score_field in _AUTHORITATIVE_FACTOR_SCORE_FIELDS.items()
        }
        if instrument_id in ranking_by_id
        else {}
        for instrument_id in universe
    }
    input_metadata = {
        instrument_id: {
            "data_completeness": ranking_by_id[instrument_id].data_completeness,
            "execution_penalty": ranking_by_id[instrument_id].execution_penalty,
        }
        if instrument_id in ranking_by_id
        else {
            "data_completeness": None,
            "execution_penalty": None,
        }
        for instrument_id in universe
    }
    return build_feature_snapshot(
        as_of=as_of,
        feature_set_version=FACTOR_FEATURE_SET_VERSION,
        dataset_revision=dataset_revision,
        raw_scores=raw_scores,
        cross_sectional_scores=cross_sectional_scores,
        universe_ids=universe,
        input_metadata=input_metadata,
    )


def _ranking_by_id(rankings: Iterable[FactorRanking]) -> dict[str, FactorRanking]:
    ordered = sorted(rankings, key=_ranking_input_key)
    return {ranking.instrument_id: ranking for ranking in reversed(ordered)}


def _ranking_input_key(ranking: FactorRanking) -> tuple[object, ...]:
    raw_values = tuple(
        sorted(
            (
                exposure.factor_id,
                "" if exposure.raw_value is None else format(exposure.raw_value, ".17g"),
            )
            for exposure in ranking.factor_exposures
            if exposure.factor_id in _AUTHORITATIVE_FACTOR_SCORE_FIELDS
        )
    )
    return (
        ranking.instrument_id,
        raw_values,
        ranking.data_completeness,
        ranking.execution_penalty,
    )


def _raw_exposure_scores(ranking: FactorRanking | None) -> dict[str, float | None]:
    exposure_by_id = (
        {exposure.factor_id: exposure for exposure in ranking.factor_exposures}
        if ranking is not None
        else {}
    )
    return {
        factor_id: (
            _float_or_none(exposure_by_id[factor_id].raw_value)
            if factor_id in exposure_by_id
            else None
        )
        for factor_id in _FACTOR_SCORE_FIELDS
    }


def _cross_sectional_scores(
    raw_scores: Mapping[str, Mapping[str, float | None]],
) -> dict[str, dict[str, float]]:
    factor_ids = [
        factor_id
        for factor_id in _FACTOR_SCORE_FIELDS
        if any(factor_id in scores for scores in raw_scores.values())
    ]
    by_factor = {
        factor_id: _rank_scores(
            {
                instrument_id: scores.get(factor_id)
                for instrument_id, scores in raw_scores.items()
            }
        )
        for factor_id in factor_ids
        if factor_id != "size"
    }
    if "size" in factor_ids:
        by_factor["size"] = _size_scores_from_values(
            {
                instrument_id: scores.get("size")
                for instrument_id, scores in raw_scores.items()
            }
        )
    return {
        instrument_id: {
            factor_id: round(by_factor[factor_id][instrument_id], 4)
            for factor_id in factor_ids
        }
        for instrument_id in raw_scores
    }


def _size_scores_from_values(values: Mapping[str, float | None]) -> dict[str, float]:
    if not any(value is not None for value in values.values()):
        return {instrument_id: 0.5 for instrument_id in values}
    rank_fallback = _rank_scores(dict(values))
    scores = {
        instrument_id: (
            0.35
            if market_cap is None
            else _a_share_size_score(market_cap)
            if _is_a_share(instrument_id)
            else rank_fallback[instrument_id]
        )
        for instrument_id, market_cap in values.items()
    }
    return {instrument_id: round(score, 4) for instrument_id, score in scores.items()}


def _rescore_ranking(
    ranking: FactorRanking,
    scores: Mapping[str, float],
) -> FactorRanking:
    weights = _ranking_factor_weights(ranking)
    component_score = sum(
        scores.get(
            factor_id,
            float(getattr(ranking, _FACTOR_SCORE_FIELDS[factor_id])),
        )
        * weight
        for factor_id, weight in weights.items()
    )
    factor_score = _clamp(component_score * ranking.data_completeness - ranking.execution_penalty)
    exposures = [
        exposure.model_copy(
            update={
                "score": round(scores[exposure.factor_id], 4),
                "weight": weights[exposure.factor_id],
            }
        )
        if exposure.factor_id in scores
        else exposure.model_copy(deep=True)
        for exposure in ranking.factor_exposures
    ]
    return ranking.model_copy(
        update={
            "factor_score": round(factor_score, 4),
            "factor_rank": 0,
            "percentile": 0.0,
            "factor_exposures": exposures,
            **{
                score_field: round(scores[factor_id], 4)
                for factor_id, score_field in _FACTOR_SCORE_FIELDS.items()
                if factor_id in scores
            },
        }
    )


def _raw_factors(
    instrument_id: str,
    bars: pd.DataFrame,
    fundamental: FundamentalSnapshot | None = None,
    *,
    asset_type: str,
    market_returns: pd.Series | None = None,
) -> _RawFactors:
    ordered = bars.sort_values("trade_date").copy()
    raw_close = pd.to_numeric(ordered["close"], errors="coerce")
    close = raw_close.dropna()
    volume = pd.to_numeric(ordered["volume"], errors="coerce").dropna()
    missing: list[str] = []
    flags: list[str] = []
    valuation_raw = _earnings_yield(fundamental)
    size_raw = _float_or_none(fundamental.market_cap if fundamental is not None else None)
    quality_raw = _fundamental_quality(fundamental)
    profitability_raw = _fundamental_profitability(fundamental)
    growth_raw = _fundamental_growth(fundamental)
    if valuation_raw is None:
        missing.append("valuation_ep")
    if size_raw is None:
        missing.append("market_cap")
    if quality_raw is None:
        missing.append("quality_fundamentals")
    if profitability_raw is None:
        missing.append("profitability_fundamentals")
    if growth_raw is None:
        missing.append("growth_fundamentals")
    if len(close) < 20:
        missing.append("20d_return")
    if len(close) < 60:
        missing.append("60d_return")
    if len(close) < 120:
        missing.append("120d_return")
    regression_momentum = regression_quality_momentum(raw_close, window=29)
    if regression_momentum.status != "available":
        missing.append("29d_trend_regression")
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
    alignment_score = alignment / alignment_inputs if alignment_inputs else None
    regression_score = _squash_signed(regression_momentum.quality_score)
    trend_quality_raw = _weighted_available([(alignment_score, 0.35), (regression_score, 0.65)])

    avg_volume_20 = float(volume.tail(20).mean()) if len(volume) >= 20 else None
    avg_volume_5 = float(volume.tail(5).mean()) if len(volume) >= 5 else None
    volume_ratio_5_20 = avg_volume_5 / avg_volume_20 if avg_volume_5 and avg_volume_20 else None
    if trend_quality_raw is not None:
        if distance_ma20 is not None:
            trend_quality_raw += max(min(distance_ma20, 0.10), -0.10)
        if volume_ratio_5_20 is not None:
            if 1.05 <= volume_ratio_5_20 <= 2.8:
                trend_quality_raw += 0.07
            elif volume_ratio_5_20 > 4.0:
                trend_quality_raw -= 0.05
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
    downside_risk_raw = _downside_risk(returns, window=60)
    market_adjusted_momentum_raw = _market_adjusted_momentum(
        ordered,
        market_returns,
    )
    if downside_risk_raw is None:
        missing.append("60d_downside_risk")
    if market_adjusted_momentum_raw is None:
        missing.append("market_adjusted_momentum")

    ret_5 = _period_return(close, 5)
    reversal_raw = None
    if ret_5 is not None and latest_close is not None and ma20:
        reversal_raw = -ret_5 if latest_close >= ma20 else ret_5

    if distance_ma20 is not None and distance_ma20 > 0.12:
        flags.append("overextended")
    if distance_ma20 is not None and distance_ma20 > 0.24:
        flags.append("fomo_escape_risk")
    if volatility_20d is not None and volatility_20d > 0.045:
        flags.append("high_volatility")
    if max_drawdown_60d is not None and max_drawdown_60d < -0.28:
        flags.append("deep_drawdown_risk")
    if avg_volume_20 is not None and avg_volume_20 < 300_000:
        flags.append("low_liquidity")
    if volume_ratio_5_20 is not None and volume_ratio_5_20 > 4.0:
        flags.append("volume_spike_overheat")
    is_a_share_stock = asset_type == "stock" and _is_a_share(instrument_id)
    if is_a_share_stock and size_raw is not None and size_raw < 2_000_000_000:
        flags.append("shell_size_risk")
    risk_filter_raw = _risk_filter_raw(
        distance_ma20=distance_ma20,
        volatility_20d=volatility_20d,
        max_drawdown_60d=max_drawdown_60d,
        avg_volume_20=avg_volume_20,
        volume_ratio_5_20=volume_ratio_5_20,
        market_cap=size_raw if is_a_share_stock else None,
    )
    completeness = _data_completeness(missing, asset_type=asset_type)
    return _RawFactors(
        instrument_id=instrument_id,
        asset_type=asset_type,
        valuation_raw=valuation_raw,
        size_raw=size_raw,
        quality_raw=quality_raw,
        momentum_raw=momentum_raw,
        trend_quality_raw=trend_quality_raw,
        liquidity_raw=liquidity_raw,
        low_risk_raw=low_risk_raw,
        risk_filter_raw=risk_filter_raw,
        reversal_raw=reversal_raw,
        profitability_raw=profitability_raw,
        growth_raw=growth_raw,
        downside_risk_raw=downside_risk_raw,
        market_adjusted_momentum_raw=market_adjusted_momentum_raw,
        distance_ma20=distance_ma20,
        volatility_20d=volatility_20d,
        max_drawdown_60d=max_drawdown_60d,
        volume_ratio_5_20=volume_ratio_5_20,
        market_cap=size_raw,
        data_completeness=max(0.2, completeness),
        missing_data=sorted(set(missing)),
        flags=sorted(set(flags)),
    )


def _latest_fundamentals(
    fundamentals: list[FundamentalSnapshot] | dict[str, FundamentalSnapshot] | None,
) -> dict[str, FundamentalSnapshot]:
    if fundamentals is None:
        return {}
    if isinstance(fundamentals, dict):
        return fundamentals
    latest: dict[str, FundamentalSnapshot] = {}
    for item in fundamentals:
        current = latest.get(item.instrument_id)
        if current is None or item.as_of_date > current.as_of_date:
            latest[item.instrument_id] = item
    return latest


def _earnings_yield(fundamental: FundamentalSnapshot | None) -> float | None:
    if fundamental is None:
        return None
    yields = []
    for pe in [fundamental.pe_ratio, fundamental.forward_pe]:
        value = _float_or_none(pe)
        if value is not None and value > 0:
            yields.append(1 / value)
    if not yields:
        return None
    return sum(yields) / len(yields)


def _fundamental_quality(fundamental: FundamentalSnapshot | None) -> float | None:
    if fundamental is None:
        return None
    components = []
    roe = _float_or_none(fundamental.return_on_equity_pct)
    gross_margin = _float_or_none(fundamental.gross_margin_pct)
    net_margin = _float_or_none(fundamental.net_margin_pct)
    operating_margin = _float_or_none(fundamental.operating_margin_pct)
    revenue_growth = _float_or_none(fundamental.revenue_growth_pct)
    earnings_growth = _float_or_none(fundamental.earnings_growth_pct)
    if roe is not None:
        components.append((_bounded_score(roe, -5, 25), 0.30))
    if gross_margin is not None:
        components.append((_bounded_score(gross_margin, 5, 55), 0.20))
    if net_margin is not None:
        components.append((_bounded_score(net_margin, -10, 25), 0.18))
    elif operating_margin is not None:
        components.append((_bounded_score(operating_margin, -10, 25), 0.14))
    if revenue_growth is not None:
        components.append((_bounded_score(revenue_growth, -20, 35), 0.16))
    if earnings_growth is not None:
        components.append((_bounded_score(earnings_growth, -30, 45), 0.16))
    if not components:
        return None
    weight_sum = sum(weight for _, weight in components)
    return sum(score * weight for score, weight in components) / weight_sum


def _fundamental_profitability(fundamental: FundamentalSnapshot | None) -> float | None:
    if fundamental is None:
        return None
    components = []
    for value, low, high, weight in [
        (fundamental.return_on_equity_pct, -5, 25, 0.40),
        (fundamental.gross_margin_pct, 5, 55, 0.25),
        (fundamental.net_margin_pct, -10, 25, 0.25),
        (fundamental.operating_margin_pct, -10, 25, 0.10),
    ]:
        numeric = _float_or_none(value)
        if numeric is not None:
            components.append((_bounded_score(numeric, low, high), weight))
    return _weighted_available(components)


def _fundamental_growth(fundamental: FundamentalSnapshot | None) -> float | None:
    if fundamental is None:
        return None
    components = []
    revenue_growth = _float_or_none(fundamental.revenue_growth_pct)
    earnings_growth = _float_or_none(fundamental.earnings_growth_pct)
    if revenue_growth is not None:
        components.append((_bounded_score(revenue_growth, -20, 35), 0.45))
    if earnings_growth is not None:
        components.append((_bounded_score(earnings_growth, -30, 45), 0.55))
    return _weighted_available(components)


def _market_returns_by_asset_type(
    grouped_bars: list[tuple[str, pd.DataFrame, str]],
) -> dict[str, pd.Series]:
    rows = []
    for instrument_id, frame, asset_type in grouped_bars:
        ordered = frame.sort_values("trade_date")
        closes = pd.to_numeric(ordered["close"], errors="coerce")
        returns = closes.pct_change(fill_method=None)
        rows.append(
            pd.DataFrame(
                {
                    "instrument_id": instrument_id,
                    "asset_type": asset_type,
                    "trade_date": pd.to_datetime(ordered["trade_date"]),
                    "return": returns,
                }
            )
        )
    if not rows:
        return {}
    combined = pd.concat(rows, ignore_index=True)
    combined = combined.replace([float("inf"), float("-inf")], pd.NA).dropna(
        subset=["return"]
    )
    return {
        asset_type: group.groupby("trade_date")["return"].mean().sort_index()
        for asset_type, group in combined.groupby("asset_type")
    }


def _market_adjusted_momentum(
    ordered_bars: pd.DataFrame,
    market_returns: pd.Series | None,
) -> float | None:
    if market_returns is None or market_returns.empty:
        return None
    stock = pd.Series(
        pd.to_numeric(ordered_bars["close"], errors="coerce").to_numpy(),
        index=pd.to_datetime(ordered_bars["trade_date"]),
        dtype="float64",
    ).pct_change(fill_method=None)
    market = market_returns.copy()
    market.index = pd.to_datetime(market.index)
    aligned = (
        pd.concat(
            [stock.rename("stock"), market.rename("market")],
            axis=1,
        )
        .replace([float("inf"), float("-inf")], pd.NA)
        .dropna()
        .tail(120)
    )
    if len(aligned) < 20:
        return None
    market_variance = float(aligned["market"].var())
    if market_variance <= 1e-12:
        return None
    beta = float(aligned["stock"].cov(aligned["market"]) / market_variance)
    residual = aligned["stock"] - beta * aligned["market"]
    windows = []
    for window, weight in ((20, 0.30), (60, 0.40), (120, 0.30)):
        if len(residual) >= window:
            compounded = float((1 + residual.tail(window).clip(lower=-0.99)).prod() - 1)
            windows.append((compounded, weight))
    return _weighted_available(windows)


def _downside_risk(returns: pd.Series, *, window: int) -> float | None:
    if len(returns) < window:
        return None
    downside = returns.tail(window).clip(upper=0)
    semideviation = float((downside.pow(2).mean()) ** 0.5)
    return -semideviation


def _risk_filter_raw(
    *,
    distance_ma20: float | None,
    volatility_20d: float | None,
    max_drawdown_60d: float | None,
    avg_volume_20: float | None,
    volume_ratio_5_20: float | None,
    market_cap: float | None,
) -> float:
    score = 1.0
    if distance_ma20 is not None:
        if distance_ma20 > 0.12:
            score -= min(0.28, (distance_ma20 - 0.12) * 1.25)
        if distance_ma20 < -0.18:
            score -= min(0.18, abs(distance_ma20 + 0.18))
    if volatility_20d is not None:
        score -= min(0.26, max(0.0, volatility_20d - 0.03) * 5.0)
    if max_drawdown_60d is not None:
        score -= min(0.20, max(0.0, abs(max_drawdown_60d) - 0.18) * 0.9)
    if avg_volume_20 is not None and avg_volume_20 < 300_000:
        score -= 0.16 if avg_volume_20 < 120_000 else 0.10
    if volume_ratio_5_20 is not None and volume_ratio_5_20 > 4.0:
        score -= min(0.12, (volume_ratio_5_20 - 4.0) * 0.025)
    if market_cap is not None and market_cap < 2_000_000_000:
        score -= 0.18
    return _clamp(score)


def _data_completeness(missing: list[str], *, asset_type: str) -> float:
    technical_missing = sum(
        1
        for key in {"20d_return", "60d_return", "120d_return", "29d_trend_regression"}
        if key in missing
    )
    fundamental_missing = (
        0
        if asset_type == "etf"
        else sum(
            1 for key in {"valuation_ep", "market_cap", "quality_fundamentals"} if key in missing
        )
    )
    return _clamp(1.0 - technical_missing * 0.10 - fundamental_missing * 0.06)


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
    valid = {
        key: normalized
        for key, value in values.items()
        if (normalized := _float_or_none(value)) is not None
    }
    if not valid:
        return {key: 0.5 for key in values}
    sorted_items = sorted(valid.items(), key=lambda item: (item[1], item[0]))
    if len(sorted_items) == 1:
        ranked = {sorted_items[0][0]: 0.5}
    else:
        ranked: dict[str, float] = {}
        start = 0
        while start < len(sorted_items):
            end = start + 1
            while end < len(sorted_items) and sorted_items[end][1] == sorted_items[start][1]:
                end += 1
            average_position = (start + end - 1) / 2
            score = average_position / (len(sorted_items) - 1)
            for index in range(start, end):
                ranked[sorted_items[index][0]] = score
            start = end
    return {key: round(ranked.get(key, 0.35), 4) for key in values}


def _bucketed_factor_scores(
    items: list[_RawFactors],
) -> dict[str, dict[str, float]]:
    scores = {factor_id: {} for factor_id in _FACTOR_SCORE_FIELDS}
    buckets: dict[str, list[_RawFactors]] = {}
    for item in items:
        buckets.setdefault(item.asset_type, []).append(item)
    for asset_type in sorted(buckets):
        bucket_scores = _factor_scores(buckets[asset_type])
        for factor_id, values in bucket_scores.items():
            scores[factor_id].update(values)
    return scores


def _factor_scores(items: list[_RawFactors]) -> dict[str, dict[str, float]]:
    return {
        "valuation": _rank_scores({item.instrument_id: item.valuation_raw for item in items}),
        "size": _size_scores(items),
        "quality": _rank_scores({item.instrument_id: item.quality_raw for item in items}),
        "momentum": _rank_scores({item.instrument_id: item.momentum_raw for item in items}),
        "trend_quality": _rank_scores(
            {item.instrument_id: item.trend_quality_raw for item in items}
        ),
        "liquidity": _rank_scores({item.instrument_id: item.liquidity_raw for item in items}),
        "low_risk": _rank_scores({item.instrument_id: item.low_risk_raw for item in items}),
        "risk_filter": _rank_scores({item.instrument_id: item.risk_filter_raw for item in items}),
        "reversal": _rank_scores({item.instrument_id: item.reversal_raw for item in items}),
        "profitability": _rank_scores(
            {item.instrument_id: item.profitability_raw for item in items}
        ),
        "growth": _rank_scores({item.instrument_id: item.growth_raw for item in items}),
        "downside_risk": _rank_scores(
            {item.instrument_id: item.downside_risk_raw for item in items}
        ),
        "market_adjusted_momentum": _rank_scores(
            {item.instrument_id: item.market_adjusted_momentum_raw for item in items}
        ),
    }


def _size_scores(items: list[_RawFactors]) -> dict[str, float]:
    if not any(item.size_raw is not None for item in items):
        return {item.instrument_id: 0.5 for item in items}
    scores: dict[str, float] = {}
    rank_fallback = _rank_scores({item.instrument_id: item.size_raw for item in items})
    for item in items:
        market_cap = item.size_raw
        if market_cap is None:
            scores[item.instrument_id] = 0.35
            continue
        if _is_a_share(item.instrument_id):
            scores[item.instrument_id] = _a_share_size_score(market_cap)
        else:
            scores[item.instrument_id] = rank_fallback[item.instrument_id]
    return {key: round(value, 4) for key, value in scores.items()}


def _a_share_size_score(market_cap: float) -> float:
    if market_cap < 2_000_000_000:
        return 0.05
    if market_cap < 5_000_000_000:
        return 0.35
    if market_cap < 20_000_000_000:
        return 0.78
    if market_cap < 150_000_000_000:
        return 0.95
    if market_cap < 500_000_000_000:
        return 0.72
    return 0.55


def _execution_penalty(item: _RawFactors) -> float:
    penalty = 0.0
    if item.distance_ma20 is not None and item.distance_ma20 > 0.12:
        penalty += min(0.20, (item.distance_ma20 - 0.12) * 1.5)
    if item.distance_ma20 is not None and item.distance_ma20 > 0.24:
        penalty += min(0.12, (item.distance_ma20 - 0.24) * 1.2)
    if "low_liquidity" in item.flags:
        penalty += 0.10
    if "high_volatility" in item.flags:
        penalty += 0.08
    if "deep_drawdown_risk" in item.flags:
        penalty += 0.08
    if "volume_spike_overheat" in item.flags:
        penalty += 0.06
    if "shell_size_risk" in item.flags:
        penalty += 0.12
    return _clamp(penalty)


def _factor_weights(instrument_id: str, asset_type: str) -> dict[str, float]:
    if asset_type == "etf":
        return ETF_FACTOR_WEIGHTS
    if asset_type == "stock" and instrument_id.startswith("CN:"):
        return A_SHARE_FACTOR_WEIGHTS
    return FACTOR_WEIGHTS


def _ranking_factor_weights(ranking: FactorRanking) -> dict[str, float]:
    exposure_weights = {
        exposure.factor_id: exposure.weight
        for exposure in ranking.factor_exposures
        if exposure.factor_id in _FACTOR_SCORE_FIELDS
    }
    if set(exposure_weights) == set(_FACTOR_SCORE_FIELDS):
        return exposure_weights
    legacy_asset_type = "stock" if _is_a_share(ranking.instrument_id) else "unknown"
    return _factor_weights(ranking.instrument_id, legacy_asset_type)


def _asset_type_from_bars(
    instrument_id: str,
    bars: pd.DataFrame,
    *,
    override: object = None,
) -> str:
    override_type = _normalize_asset_type(override)
    if override_type != "unknown":
        return override_type
    if "asset_type" not in bars.columns:
        symbol = instrument_id.removeprefix("CN:")
        if len(symbol) == 6 and symbol.startswith(_ETF_SYMBOL_PREFIXES):
            return "etf"
        return "stock" if _is_a_share(instrument_id) else "unknown"
    asset_types = {_normalize_asset_type(value) for value in bars["asset_type"]}
    if len(asset_types) == 1:
        return next(iter(asset_types))
    return "unknown"


def _normalize_asset_type(value: object) -> str:
    if value is None or pd.isna(value):
        return "unknown"
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized in _STOCK_ASSET_TYPES:
        return "stock"
    if normalized in _ETF_ASSET_TYPES:
        return "etf"
    return "unknown"


def _exposures(
    item: _RawFactors,
    scores: dict[str, dict[str, float]],
    weights: dict[str, float],
) -> list[FactorExposure]:
    return [
        FactorExposure(
            factor_id="valuation",
            label="A-share EP valuation",
            raw_value=item.valuation_raw,
            score=scores["valuation"][item.instrument_id],
            weight=weights.get("valuation", 0.0),
            explanation="Earnings yield from PE/forward PE. For A-shares this uses EP rather than PB when fundamental data is available.",
        ),
        FactorExposure(
            factor_id="size",
            label="A-share size filter",
            raw_value=item.market_cap,
            score=scores["size"][item.instrument_id],
            weight=weights.get("size", 0.0),
            explanation="Market-cap suitability. A-share scoring avoids shell-like micro caps while still allowing mid-cap elasticity.",
        ),
        FactorExposure(
            factor_id="quality",
            label="Quality",
            raw_value=item.quality_raw,
            score=scores["quality"][item.instrument_id],
            weight=weights.get("quality", 0.0),
            explanation="ROE, margins, and growth quality from fundamental snapshots when available.",
        ),
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
            label="Regression trend quality",
            raw_value=item.trend_quality_raw,
            score=scores["trend_quality"][item.instrument_id],
            weight=weights["trend_quality"],
            explanation="29-day log-price regression momentum weighted by R-squared, combined with moving-average alignment and distance from 20DMA.",
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
            factor_id="risk_filter",
            label="Risk filter",
            raw_value=item.risk_filter_raw,
            score=scores["risk_filter"][item.instrument_id],
            weight=weights["risk_filter"],
            explanation="Penalty-aware filter for overextension, high volatility, deep drawdown, low liquidity, volume spikes, and shell-size risk.",
        ),
        FactorExposure(
            factor_id="reversal",
            label="Reversal setup",
            raw_value=item.reversal_raw,
            score=scores["reversal"][item.instrument_id],
            weight=weights["reversal"],
            explanation="Short-term pullback pressure inside an intact trend.",
        ),
        FactorExposure(
            factor_id="profitability",
            label="Profitability research",
            raw_value=item.profitability_raw,
            score=scores["profitability"][item.instrument_id],
            weight=weights["profitability"],
            explanation="Research-only profitability exposure from point-in-time ROE and margins; zero ranking weight until forward evidence supports it.",
        ),
        FactorExposure(
            factor_id="growth",
            label="Growth research",
            raw_value=item.growth_raw,
            score=scores["growth"][item.instrument_id],
            weight=weights["growth"],
            explanation="Research-only revenue and earnings growth exposure; zero ranking weight until point-in-time coverage and forward evidence are sufficient.",
        ),
        FactorExposure(
            factor_id="downside_risk",
            label="Downside risk research",
            raw_value=item.downside_risk_raw,
            score=scores["downside_risk"][item.instrument_id],
            weight=weights["downside_risk"],
            explanation="Research-only 60-session downside semideviation; lower downside volatility scores better and does not change ranking weights.",
        ),
        FactorExposure(
            factor_id="market_adjusted_momentum",
            label="Market-adjusted momentum research",
            raw_value=item.market_adjusted_momentum_raw,
            score=scores["market_adjusted_momentum"][item.instrument_id],
            weight=weights["market_adjusted_momentum"],
            explanation="Research-only beta-adjusted 20/60/120-session performance relative to the same asset pool; zero ranking weight while A-share efficacy is tested.",
        ),
    ]


def _bounded_score(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5
    return _clamp((value - low) / (high - low))


def _float_or_none(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if pd.isna(result):
        return None
    return result


def _squash_signed(value: float | None) -> float | None:
    if value is None:
        return None
    return 0.5 + 0.5 * value / (1 + abs(value))


def _is_a_share(instrument_id: str) -> bool:
    return instrument_id.startswith("CN:")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
