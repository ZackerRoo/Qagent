from datetime import date

import pandas as pd
from pydantic import BaseModel

from qagent.factors.engine import build_factor_rankings
from qagent.strategy_data.models import FundamentalSnapshot


class FactorBacktestSignal(BaseModel):
    signal_date: date
    instrument_id: str
    factor_rank: int
    factor_score: float
    entry_close: float
    exit_close: float | None = None
    forward_return_pct: float | None = None


class FactorBacktestSummary(BaseModel):
    sample_count: int
    completed_count: int
    positive_rate: float | None
    avg_forward_return_pct: float | None
    best_forward_return_pct: float | None
    worst_forward_return_pct: float | None


class FactorRankBucket(BaseModel):
    factor_rank: int
    sample_count: int
    completed_count: int
    positive_rate: float | None
    avg_forward_return_pct: float | None


class FactorQuantileBucket(BaseModel):
    quantile: int
    label: str
    sample_count: int
    completed_count: int
    positive_rate: float | None
    avg_forward_return_pct: float | None
    avg_factor_score: float | None


class FactorInformationCoefficient(BaseModel):
    sample_count: int
    mean_ic: float | None
    mean_rank_ic: float | None
    positive_ic_rate: float | None
    positive_rank_ic_rate: float | None
    top_bottom_spread_pct: float | None


class FactorExposureInformationCoefficient(BaseModel):
    factor_id: str
    label: str
    sample_count: int
    mean_ic: float | None
    mean_rank_ic: float | None
    positive_ic_rate: float | None
    top_bottom_spread_pct: float | None


class FactorBacktestResult(BaseModel):
    summary: FactorBacktestSummary
    signals: list[FactorBacktestSignal]
    rank_buckets: list[FactorRankBucket]
    quantile_buckets: list[FactorQuantileBucket]
    information_coefficient: FactorInformationCoefficient
    factor_ic: list[FactorExposureInformationCoefficient]
    data_health: dict[str, str]


def run_factor_backtest(
    bars: pd.DataFrame,
    forward_days: int = 20,
    step_days: int = 20,
    top_n: int = 3,
    fundamentals: list[FundamentalSnapshot] | dict[str, FundamentalSnapshot | list[FundamentalSnapshot]] | None = None,
) -> FactorBacktestResult:
    fundamental_history = _fundamental_history(fundamentals)
    if bars.empty:
        return FactorBacktestResult(
            summary=FactorBacktestSummary(
                sample_count=0,
                completed_count=0,
                positive_rate=None,
                avg_forward_return_pct=None,
                best_forward_return_pct=None,
                worst_forward_return_pct=None,
            ),
            signals=[],
            rank_buckets=[],
            quantile_buckets=[],
            information_coefficient=_empty_ic(),
            factor_ic=[],
            data_health={"factor_backtest": "no_bars"},
        )
    ordered = bars.copy()
    ordered["trade_date"] = pd.to_datetime(ordered["trade_date"]).dt.date
    dates = sorted(ordered["trade_date"].unique())
    signals: list[FactorBacktestSignal] = []
    ic_rows: list[dict[str, float]] = []
    min_history_days = _minimum_history_days(forward_days)
    for date_index in range(min_history_days, max(min_history_days, len(dates) - forward_days), step_days):
        signal_date = dates[date_index]
        future_date = dates[date_index + forward_days]
        history = ordered[ordered["trade_date"] <= signal_date]
        all_rankings = build_factor_rankings(
            history,
            fundamentals=_fundamentals_as_of(fundamental_history, signal_date),
        )
        rankings = all_rankings[:top_n]
        ic_rows.extend(_ic_rows_for_date(ordered, all_rankings, signal_date, future_date))
        for ranking in rankings:
            symbol_bars = ordered[ordered["instrument_id"] == ranking.instrument_id]
            entry = _close_on_or_before(symbol_bars, signal_date)
            exit_ = _close_on_or_before(symbol_bars, future_date)
            forward_return = None
            if entry is not None and exit_ is not None and entry != 0:
                forward_return = (exit_ / entry - 1) * 100
            signals.append(
                FactorBacktestSignal(
                    signal_date=signal_date,
                    instrument_id=ranking.instrument_id,
                    factor_rank=ranking.factor_rank,
                    factor_score=ranking.factor_score,
                    entry_close=entry or 0,
                    exit_close=exit_,
                    forward_return_pct=round(forward_return, 4)
                    if forward_return is not None
                    else None,
                )
            )
    completed_returns = [
        signal.forward_return_pct
        for signal in signals
        if signal.forward_return_pct is not None
    ]
    summary = FactorBacktestSummary(
        sample_count=len(signals),
        completed_count=len(completed_returns),
        positive_rate=(
            sum(1 for value in completed_returns if value > 0) / len(completed_returns)
            if completed_returns
            else None
        ),
        avg_forward_return_pct=(
            sum(completed_returns) / len(completed_returns) if completed_returns else None
        ),
        best_forward_return_pct=max(completed_returns) if completed_returns else None,
        worst_forward_return_pct=min(completed_returns) if completed_returns else None,
    )
    quantile_buckets = _summarize_quantile_buckets(ic_rows)
    factor_ic = _summarize_factor_ic(ic_rows)
    return FactorBacktestResult(
        summary=summary,
        signals=signals,
        rank_buckets=_summarize_rank_buckets(signals),
        quantile_buckets=quantile_buckets,
        information_coefficient=_summarize_information_coefficient(ic_rows),
        factor_ic=factor_ic,
        data_health={
            "factor_backtest": "ok",
            "forward_days": str(forward_days),
            "step_days": str(step_days),
            "top_n": str(top_n),
            "min_history_days": str(min_history_days),
            "signals": str(len(signals)),
            "ic_samples": str(len(ic_rows)),
            "quantile_buckets": str(len(quantile_buckets)),
            "factor_ic": str(len(factor_ic)),
            "historical_fundamentals": str(
                sum(len(items) for items in fundamental_history.values())
            ),
            "fundamental_mode": "point_in_time" if fundamental_history else "price_only",
        },
    )


def _summarize_rank_buckets(signals: list[FactorBacktestSignal]) -> list[FactorRankBucket]:
    buckets: list[FactorRankBucket] = []
    for rank in sorted({signal.factor_rank for signal in signals}):
        rank_signals = [signal for signal in signals if signal.factor_rank == rank]
        completed_returns = [
            signal.forward_return_pct
            for signal in rank_signals
            if signal.forward_return_pct is not None
        ]
        buckets.append(
            FactorRankBucket(
                factor_rank=rank,
                sample_count=len(rank_signals),
                completed_count=len(completed_returns),
                positive_rate=(
                    sum(1 for value in completed_returns if value > 0) / len(completed_returns)
                    if completed_returns
                    else None
                ),
                avg_forward_return_pct=(
                    sum(completed_returns) / len(completed_returns)
                    if completed_returns
                    else None
                ),
            )
        )
    return buckets


def _ic_rows_for_date(
    bars: pd.DataFrame,
    rankings: list,
    signal_date: date,
    future_date: date,
) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    ordered_rankings = sorted(rankings, key=lambda item: item.factor_score, reverse=True)
    total = len(ordered_rankings)
    for index, ranking in enumerate(ordered_rankings):
        symbol_bars = bars[bars["instrument_id"] == ranking.instrument_id]
        entry = _close_on_or_before(symbol_bars, signal_date)
        exit_ = _close_on_or_before(symbol_bars, future_date)
        if entry is None or exit_ is None or entry == 0:
            continue
        quantile = _factor_quantile(index, total)
        row = {
            "signal_ordinal": float(signal_date.toordinal()),
            "factor_score": ranking.factor_score,
            "factor_rank": float(ranking.factor_rank),
            "factor_quantile": float(quantile),
            "forward_return_pct": (exit_ / entry - 1) * 100,
        }
        for exposure in ranking.factor_exposures:
            row[f"factor__{exposure.factor_id}"] = exposure.score
        rows.append(row)
    return rows


def _factor_quantile(index: int, total: int) -> int:
    if total <= 0:
        return 5
    return min(5, int(index * 5 / total) + 1)


def _fundamental_history(
    fundamentals: list[FundamentalSnapshot] | dict[str, FundamentalSnapshot | list[FundamentalSnapshot]] | None,
) -> dict[str, list[FundamentalSnapshot]]:
    if fundamentals is None:
        return {}
    grouped: dict[str, list[FundamentalSnapshot]] = {}
    if isinstance(fundamentals, dict):
        iterable: list[FundamentalSnapshot] = []
        for key, value in fundamentals.items():
            if isinstance(value, list):
                iterable.extend(value)
            else:
                iterable.append(value)
    else:
        iterable = list(fundamentals)
    for item in iterable:
        grouped.setdefault(item.instrument_id, []).append(item)
    return {
        instrument_id: sorted(items, key=lambda item: item.as_of_date)
        for instrument_id, items in grouped.items()
    }


def _fundamentals_as_of(
    history: dict[str, list[FundamentalSnapshot]],
    signal_date: date,
) -> dict[str, FundamentalSnapshot]:
    selected: dict[str, FundamentalSnapshot] = {}
    for instrument_id, items in history.items():
        eligible = [item for item in items if item.as_of_date <= signal_date]
        if eligible:
            selected[instrument_id] = eligible[-1]
    return selected


def _summarize_information_coefficient(
    rows: list[dict[str, float]],
) -> FactorInformationCoefficient:
    if not rows:
        return _empty_ic()
    frame = pd.DataFrame(rows)
    ic_values: list[float] = []
    rank_ic_values: list[float] = []
    spreads: list[float] = []
    for _, group in frame.groupby("signal_ordinal"):
        if len(group) < 2:
            continue
        ic = group["factor_score"].corr(group["forward_return_pct"], method="pearson")
        rank_ic = group["factor_score"].rank().corr(group["forward_return_pct"].rank())
        if pd.notna(ic):
            ic_values.append(float(ic))
        if pd.notna(rank_ic):
            rank_ic_values.append(float(rank_ic))
        spread = _top_bottom_spread(group)
        if spread is not None:
            spreads.append(spread)
    return FactorInformationCoefficient(
        sample_count=len(ic_values),
        mean_ic=_average(ic_values),
        mean_rank_ic=_average(rank_ic_values),
        positive_ic_rate=_positive_rate(ic_values),
        positive_rank_ic_rate=_positive_rate(rank_ic_values),
        top_bottom_spread_pct=_average(spreads),
    )


def _summarize_quantile_buckets(rows: list[dict[str, float]]) -> list[FactorQuantileBucket]:
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    buckets: list[FactorQuantileBucket] = []
    for quantile in range(1, 6):
        group = frame[frame["factor_quantile"] == float(quantile)]
        returns = [
            float(value)
            for value in group["forward_return_pct"].dropna().tolist()
        ]
        scores = [
            float(value)
            for value in group["factor_score"].dropna().tolist()
        ]
        buckets.append(
            FactorQuantileBucket(
                quantile=quantile,
                label=_quantile_label(quantile),
                sample_count=len(group),
                completed_count=len(returns),
                positive_rate=_positive_rate(returns),
                avg_forward_return_pct=_average(returns),
                avg_factor_score=_average(scores),
            )
        )
    return buckets


def _summarize_factor_ic(rows: list[dict[str, float]]) -> list[FactorExposureInformationCoefficient]:
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    factor_columns = sorted(column for column in frame.columns if column.startswith("factor__"))
    results: list[FactorExposureInformationCoefficient] = []
    for column in factor_columns:
        values = _ic_values_for_column(frame, column)
        rank_values = _rank_ic_values_for_column(frame, column)
        spreads = _spreads_for_column(frame, column)
        factor_id = column.removeprefix("factor__")
        results.append(
            FactorExposureInformationCoefficient(
                factor_id=factor_id,
                label=_factor_label(factor_id),
                sample_count=len(values),
                mean_ic=_average(values),
                mean_rank_ic=_average(rank_values),
                positive_ic_rate=_positive_rate(values),
                top_bottom_spread_pct=_average(spreads),
            )
        )
    results.sort(
        key=lambda item: (
            item.mean_rank_ic is not None,
            item.mean_rank_ic if item.mean_rank_ic is not None else -999,
        ),
        reverse=True,
    )
    return results


def _ic_values_for_column(frame: pd.DataFrame, column: str) -> list[float]:
    values: list[float] = []
    for _, group in frame.groupby("signal_ordinal"):
        if len(group) < 2 or group[column].nunique(dropna=True) < 2:
            continue
        ic = group[column].corr(group["forward_return_pct"], method="pearson")
        if pd.notna(ic):
            values.append(float(ic))
    return values


def _rank_ic_values_for_column(frame: pd.DataFrame, column: str) -> list[float]:
    values: list[float] = []
    for _, group in frame.groupby("signal_ordinal"):
        if len(group) < 2 or group[column].nunique(dropna=True) < 2:
            continue
        ic = group[column].rank().corr(group["forward_return_pct"].rank())
        if pd.notna(ic):
            values.append(float(ic))
    return values


def _spreads_for_column(frame: pd.DataFrame, column: str) -> list[float]:
    spreads: list[float] = []
    for _, group in frame.groupby("signal_ordinal"):
        if len(group) < 2 or group[column].nunique(dropna=True) < 2:
            continue
        ordered = group.sort_values(column, ascending=False)
        bucket_size = max(1, len(ordered) // 5)
        top = ordered.head(bucket_size)["forward_return_pct"]
        bottom = ordered.tail(bucket_size)["forward_return_pct"]
        if not top.empty and not bottom.empty:
            spreads.append(round(float(top.mean() - bottom.mean()), 4))
    return spreads


def _top_bottom_spread(group: pd.DataFrame) -> float | None:
    ordered = group.sort_values("factor_score", ascending=False)
    bucket_size = max(1, len(ordered) // 5)
    top = ordered.head(bucket_size)["forward_return_pct"]
    bottom = ordered.tail(bucket_size)["forward_return_pct"]
    if top.empty or bottom.empty:
        return None
    return round(float(top.mean() - bottom.mean()), 4)


def _quantile_label(quantile: int) -> str:
    labels = {
        1: "Top 20%",
        2: "60-80%",
        3: "40-60%",
        4: "20-40%",
        5: "Bottom 20%",
    }
    return labels.get(quantile, str(quantile))


def _factor_label(factor_id: str) -> str:
    labels = {
        "valuation": "A-share EP valuation",
        "size": "A-share size filter",
        "quality": "Quality",
        "momentum": "Momentum",
        "trend_quality": "Trend quality",
        "liquidity": "Liquidity",
        "low_risk": "Low risk",
        "risk_filter": "Risk filter",
        "reversal": "Reversal setup",
    }
    return labels.get(factor_id, factor_id)


def _empty_ic() -> FactorInformationCoefficient:
    return FactorInformationCoefficient(
        sample_count=0,
        mean_ic=None,
        mean_rank_ic=None,
        positive_ic_rate=None,
        positive_rank_ic_rate=None,
        top_bottom_spread_pct=None,
    )


def _positive_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(1 for value in values if value > 0) / len(values)


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _minimum_history_days(forward_days: int) -> int:
    return min(120, max(20, forward_days * 2))


def _close_on_or_before(bars: pd.DataFrame, trade_date: date) -> float | None:
    eligible = bars[bars["trade_date"] <= trade_date].sort_values("trade_date")
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]["close"])
