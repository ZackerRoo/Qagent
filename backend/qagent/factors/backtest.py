from datetime import date

import pandas as pd
from pydantic import BaseModel

from qagent.factors.engine import build_factor_rankings


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


class FactorInformationCoefficient(BaseModel):
    sample_count: int
    mean_ic: float | None
    mean_rank_ic: float | None
    positive_ic_rate: float | None
    positive_rank_ic_rate: float | None
    top_bottom_spread_pct: float | None


class FactorBacktestResult(BaseModel):
    summary: FactorBacktestSummary
    signals: list[FactorBacktestSignal]
    rank_buckets: list[FactorRankBucket]
    information_coefficient: FactorInformationCoefficient
    data_health: dict[str, str]


def run_factor_backtest(
    bars: pd.DataFrame,
    forward_days: int = 20,
    step_days: int = 20,
    top_n: int = 3,
) -> FactorBacktestResult:
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
            information_coefficient=_empty_ic(),
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
        all_rankings = build_factor_rankings(history)
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
    return FactorBacktestResult(
        summary=summary,
        signals=signals,
        rank_buckets=_summarize_rank_buckets(signals),
        information_coefficient=_summarize_information_coefficient(ic_rows),
        data_health={
            "factor_backtest": "ok",
            "forward_days": str(forward_days),
            "step_days": str(step_days),
            "top_n": str(top_n),
            "min_history_days": str(min_history_days),
            "signals": str(len(signals)),
            "ic_samples": str(len(ic_rows)),
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
    for ranking in rankings:
        symbol_bars = bars[bars["instrument_id"] == ranking.instrument_id]
        entry = _close_on_or_before(symbol_bars, signal_date)
        exit_ = _close_on_or_before(symbol_bars, future_date)
        if entry is None or exit_ is None or entry == 0:
            continue
        rows.append(
            {
                "signal_ordinal": float(signal_date.toordinal()),
                "factor_score": ranking.factor_score,
                "factor_rank": float(ranking.factor_rank),
                "forward_return_pct": (exit_ / entry - 1) * 100,
            }
        )
    return rows


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


def _top_bottom_spread(group: pd.DataFrame) -> float | None:
    ordered = group.sort_values("factor_score", ascending=False)
    bucket_size = max(1, len(ordered) // 5)
    top = ordered.head(bucket_size)["forward_return_pct"]
    bottom = ordered.tail(bucket_size)["forward_return_pct"]
    if top.empty or bottom.empty:
        return None
    return round(float(top.mean() - bottom.mean()), 4)


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
