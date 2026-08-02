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


class FactorMonotonicityDiagnostic(BaseModel):
    available: bool
    observed_buckets: int
    monotonic_steps: int
    expected_steps: int
    quantile_return_correlation: float | None
    top_bottom_spread_pct: float | None
    verdict: str


class FactorTurnoverDiagnostic(BaseModel):
    rebalance_count: int
    average_turnover_rate: float | None
    round_trip_cost_bps: float
    estimated_cost_drag_pct: float | None
    gross_average_return_pct: float | None
    net_average_return_pct: float | None
    verdict: str


class FactorRegimeDiagnostic(BaseModel):
    regime: str
    sample_count: int
    positive_rate: float | None
    average_return_pct: float | None


class FactorDecayPoint(BaseModel):
    forward_days: int
    sample_count: int
    mean_ic: float | None
    mean_rank_ic: float | None
    top_bottom_spread_pct: float | None


class FactorDecayDiagnostic(BaseModel):
    factor_id: str
    label: str
    points: list[FactorDecayPoint]
    verdict: str


class FactorDiagnosticsResult(BaseModel):
    primary_horizon_days: int
    primary: FactorBacktestResult
    monotonicity: FactorMonotonicityDiagnostic
    decay: list[FactorDecayDiagnostic]
    turnover_cost: FactorTurnoverDiagnostic
    market_regimes: list[FactorRegimeDiagnostic]
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


def run_factor_diagnostics(
    bars: pd.DataFrame,
    *,
    fundamentals: (
        list[FundamentalSnapshot]
        | dict[str, FundamentalSnapshot | list[FundamentalSnapshot]]
        | None
    ) = None,
    horizons: tuple[int, ...] = (5, 10, 20, 40),
    primary_horizon_days: int = 20,
    step_days: int = 10,
    top_n: int = 5,
    round_trip_cost_bps: float = 20.0,
) -> FactorDiagnosticsResult:
    normalized_horizons = tuple(sorted(set(horizons)))
    if primary_horizon_days not in normalized_horizons:
        normalized_horizons = tuple(
            sorted((*normalized_horizons, primary_horizon_days))
        )
    results = {
        horizon: run_factor_backtest(
            bars,
            forward_days=horizon,
            step_days=step_days,
            top_n=top_n,
            fundamentals=fundamentals,
        )
        for horizon in normalized_horizons
    }
    primary = results[primary_horizon_days]
    return FactorDiagnosticsResult(
        primary_horizon_days=primary_horizon_days,
        primary=primary,
        monotonicity=_monotonicity_diagnostic(primary),
        decay=_decay_diagnostics(results),
        turnover_cost=_turnover_diagnostic(
            primary,
            round_trip_cost_bps=round_trip_cost_bps,
        ),
        market_regimes=_regime_diagnostics(bars, primary.signals),
        data_health={
            "factor_diagnostics": (
                "ready" if primary.information_coefficient.sample_count else "insufficient"
            ),
            "factor_diagnostics_horizons": ",".join(
                str(item) for item in normalized_horizons
            ),
            "factor_diagnostics_primary_horizon": str(primary_horizon_days),
            "factor_diagnostics_step_days": str(step_days),
            "factor_diagnostics_top_n": str(top_n),
            "factor_diagnostics_round_trip_cost_bps": f"{round_trip_cost_bps:.4f}",
            "factor_diagnostics_regimes": str(
                len(_regime_diagnostics(bars, primary.signals))
            ),
        },
    )


def _monotonicity_diagnostic(
    result: FactorBacktestResult,
) -> FactorMonotonicityDiagnostic:
    buckets = [
        bucket
        for bucket in sorted(result.quantile_buckets, key=lambda item: item.quantile)
        if bucket.avg_forward_return_pct is not None
    ]
    if len(buckets) < 3:
        return FactorMonotonicityDiagnostic(
            available=False,
            observed_buckets=len(buckets),
            monotonic_steps=0,
            expected_steps=max(len(buckets) - 1, 0),
            quantile_return_correlation=None,
            top_bottom_spread_pct=result.information_coefficient.top_bottom_spread_pct,
            verdict="insufficient",
        )
    returns = pd.Series(
        [bucket.avg_forward_return_pct for bucket in buckets],
        dtype="float64",
    )
    quantiles = pd.Series([bucket.quantile for bucket in buckets], dtype="float64")
    monotonic_steps = sum(
        1 for left, right in zip(returns.tolist(), returns.tolist()[1:]) if left >= right
    )
    expected_steps = len(buckets) - 1
    correlation = quantiles.rank().corr(returns.rank())
    spread = result.information_coefficient.top_bottom_spread_pct
    verdict = (
        "monotonic"
        if monotonic_steps == expected_steps and spread is not None and spread > 0
        else "mixed"
    )
    return FactorMonotonicityDiagnostic(
        available=True,
        observed_buckets=len(buckets),
        monotonic_steps=monotonic_steps,
        expected_steps=expected_steps,
        quantile_return_correlation=(
            round(float(correlation), 4) if pd.notna(correlation) else None
        ),
        top_bottom_spread_pct=spread,
        verdict=verdict,
    )


def _decay_diagnostics(
    results: dict[int, FactorBacktestResult],
) -> list[FactorDecayDiagnostic]:
    factor_ids = sorted(
        {
            factor.factor_id
            for result in results.values()
            for factor in result.factor_ic
        }
    )
    diagnostics: list[FactorDecayDiagnostic] = []
    for factor_id in factor_ids:
        points: list[FactorDecayPoint] = []
        label = factor_id
        for horizon, result in sorted(results.items()):
            factor = next(
                (item for item in result.factor_ic if item.factor_id == factor_id),
                None,
            )
            if factor is None:
                continue
            label = factor.label
            points.append(
                FactorDecayPoint(
                    forward_days=horizon,
                    sample_count=factor.sample_count,
                    mean_ic=factor.mean_ic,
                    mean_rank_ic=factor.mean_rank_ic,
                    top_bottom_spread_pct=factor.top_bottom_spread_pct,
                )
            )
        diagnostics.append(
            FactorDecayDiagnostic(
                factor_id=factor_id,
                label=label,
                points=points,
                verdict=_decay_verdict(points),
            )
        )
    diagnostics.sort(
        key=lambda item: (
            next(
                (
                    point.mean_rank_ic
                    for point in item.points
                    if point.forward_days == 20 and point.mean_rank_ic is not None
                ),
                -999,
            )
        ),
        reverse=True,
    )
    return diagnostics


def _decay_verdict(points: list[FactorDecayPoint]) -> str:
    values = [point.mean_rank_ic for point in points if point.mean_rank_ic is not None]
    if len(values) < 2:
        return "insufficient"
    nonzero_signs = {1 if value > 0 else -1 for value in values if value != 0}
    if len(nonzero_signs) > 1:
        return "reverses"
    if abs(values[-1]) >= abs(values[0]) * 0.5:
        return "stable"
    return "decays"


def _turnover_diagnostic(
    result: FactorBacktestResult,
    *,
    round_trip_cost_bps: float,
) -> FactorTurnoverDiagnostic:
    holdings: list[set[str]] = []
    for signal_date in sorted({signal.signal_date for signal in result.signals}):
        holdings.append(
            {
                signal.instrument_id
                for signal in result.signals
                if signal.signal_date == signal_date
            }
        )
    turnovers: list[float] = []
    for previous, current in zip(holdings, holdings[1:]):
        denominator = max(len(previous), len(current), 1)
        turnovers.append(1 - len(previous & current) / denominator)
    average_turnover = _average(turnovers)
    cost_drag = (
        round(average_turnover * round_trip_cost_bps / 100, 4)
        if average_turnover is not None
        else None
    )
    gross = result.summary.avg_forward_return_pct
    net = (
        round(gross - cost_drag, 4)
        if gross is not None and cost_drag is not None
        else None
    )
    if average_turnover is None:
        verdict = "insufficient"
    elif net is not None and net > 0:
        verdict = "cost_resilient"
    else:
        verdict = "cost_fragile"
    return FactorTurnoverDiagnostic(
        rebalance_count=len(turnovers),
        average_turnover_rate=average_turnover,
        round_trip_cost_bps=round(round_trip_cost_bps, 4),
        estimated_cost_drag_pct=cost_drag,
        gross_average_return_pct=gross,
        net_average_return_pct=net,
        verdict=verdict,
    )


def _regime_diagnostics(
    bars: pd.DataFrame,
    signals: list[FactorBacktestSignal],
) -> list[FactorRegimeDiagnostic]:
    if bars.empty or not signals:
        return []
    ordered = bars.copy()
    ordered["trade_date"] = pd.to_datetime(ordered["trade_date"]).dt.date
    ordered = ordered.sort_values(["instrument_id", "trade_date"])
    ordered["market_trailing_return"] = ordered.groupby("instrument_id")["close"].pct_change(20)
    market_returns = (
        ordered.groupby("trade_date")["market_trailing_return"].mean().dropna().to_dict()
    )
    grouped: dict[str, list[float]] = {}
    for signal in signals:
        if signal.forward_return_pct is None:
            continue
        trailing_return = market_returns.get(signal.signal_date)
        if trailing_return is None:
            regime = "unknown"
        elif trailing_return > 0.02:
            regime = "risk_on"
        elif trailing_return < -0.02:
            regime = "risk_off"
        else:
            regime = "neutral"
        grouped.setdefault(regime, []).append(signal.forward_return_pct)
    order = {"risk_on": 0, "neutral": 1, "risk_off": 2, "unknown": 3}
    return sorted(
        [
            FactorRegimeDiagnostic(
                regime=regime,
                sample_count=len(values),
                positive_rate=_positive_rate(values),
                average_return_pct=_average(values),
            )
            for regime, values in grouped.items()
        ],
        key=lambda item: order.get(item.regime, 99),
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
        "profitability": "Profitability research",
        "growth": "Growth research",
        "downside_risk": "Downside risk research",
        "market_adjusted_momentum": "Market-adjusted momentum research",
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
