from __future__ import annotations

from collections.abc import Sequence
from datetime import date
import math
import random

from pydantic import BaseModel, Field

from qagent.market.calendars import trading_sessions_elapsed


class TemporalValidationWindow(BaseModel):
    key: str
    label: str
    start_date: date
    end_date: date
    sample_count: int
    positive_rate: float | None
    avg_return_pct: float | None
    confidence_low_pct: float | None
    confidence_high_pct: float | None
    max_loss_pct: float | None


class TemporalValidationResult(BaseModel):
    method: str
    return_horizon_days: int
    embargo_days: int
    windows: list[TemporalValidationWindow] = Field(default_factory=list)
    out_of_sample: TemporalValidationWindow | None = None
    verdict: str
    summary: str
    warnings: list[str] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def build_temporal_validation(
    signals: Sequence[object],
    *,
    return_horizon_days: int = 10,
    embargo_days: int | None = None,
    bootstrap_samples: int = 1000,
    seed: int = 42,
) -> TemporalValidationResult:
    if return_horizon_days not in {5, 10, 20, 60}:
        raise ValueError("return_horizon_days must be one of 5, 10, 20, 60")
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    embargo = return_horizon_days if embargo_days is None else embargo_days
    if embargo < 0:
        raise ValueError("embargo_days must be non-negative")

    rows = _completed_rows(signals, return_horizon_days)
    unique_dates = sorted({signal_date for signal_date, _ in rows})
    date_groups = _split_dates(unique_dates, embargo)
    windows = [
        _window(
            key=key,
            label=label,
            dates=dates,
            rows=rows,
            bootstrap_samples=bootstrap_samples,
            seed=seed + index,
        )
        for index, (key, label, dates) in enumerate(date_groups)
        if dates
    ]
    out_of_sample = next(
        (window for window in windows if window.key == "out_of_sample"),
        None,
    )
    verdict = _verdict(out_of_sample)
    warnings = _warnings(rows, windows, out_of_sample)
    return TemporalValidationResult(
        method="chronological_50_25_25_with_embargo",
        return_horizon_days=return_horizon_days,
        embargo_days=embargo,
        windows=windows,
        out_of_sample=out_of_sample,
        verdict=verdict,
        summary=_summary(verdict, out_of_sample),
        warnings=warnings,
        data_health={
            "temporal_validation": "ok" if verdict != "insufficient" else "insufficient",
            "temporal_completed_signals": str(len(rows)),
            "temporal_unique_dates": str(len(unique_dates)),
            "temporal_windows": str(len(windows)),
            "temporal_oos_samples": str(out_of_sample.sample_count if out_of_sample else 0),
            "temporal_embargo_days": str(embargo),
            "temporal_bootstrap_samples": str(bootstrap_samples),
            "temporal_lookahead_guard": "chronological_split_with_embargo",
        },
    )


def _completed_rows(
    signals: Sequence[object],
    return_horizon_days: int,
) -> list[tuple[date, float]]:
    field = f"return_{return_horizon_days}d"
    rows: list[tuple[date, float]] = []
    for signal in signals:
        signal_date = getattr(signal, "signal_date", None)
        value = getattr(signal, field, None)
        if not isinstance(signal_date, date) or value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            rows.append((signal_date, numeric))
    return sorted(rows, key=lambda row: row[0])


def _split_dates(
    unique_dates: list[date],
    embargo_days: int,
) -> list[tuple[str, str, list[date]]]:
    if len(unique_dates) < 3:
        return [("train", "训练期", unique_dates)] if unique_dates else []
    total = len(unique_dates)
    train_end = max(1, min(total - 2, int(total * 0.5)))
    validation_end = max(train_end + 1, min(total - 1, int(total * 0.75)))
    train_dates = unique_dates[:train_end]
    raw_validation = unique_dates[train_end:validation_end]
    raw_out_of_sample = unique_dates[validation_end:]
    validation_dates = _after_embargo(raw_validation, train_dates[-1], embargo_days)
    validation_boundary = raw_validation[-1] if raw_validation else train_dates[-1]
    out_of_sample_dates = _after_embargo(
        raw_out_of_sample,
        validation_boundary,
        embargo_days,
    )
    return [
        ("train", "训练期", train_dates),
        ("validation", "验证期", validation_dates),
        ("out_of_sample", "样本外", out_of_sample_dates),
    ]


def _after_embargo(values: list[date], boundary: date, embargo_days: int) -> list[date]:
    return [
        value
        for value in values
        if trading_sessions_elapsed(boundary, value) > embargo_days
    ]


def _window(
    *,
    key: str,
    label: str,
    dates: list[date],
    rows: list[tuple[date, float]],
    bootstrap_samples: int,
    seed: int,
) -> TemporalValidationWindow:
    selected_dates = set(dates)
    returns = [value for signal_date, value in rows if signal_date in selected_dates]
    low, high = _bootstrap_mean_interval(returns, bootstrap_samples, seed)
    return TemporalValidationWindow(
        key=key,
        label=label,
        start_date=dates[0],
        end_date=dates[-1],
        sample_count=len(returns),
        positive_rate=_round(sum(value > 0 for value in returns) / len(returns))
        if returns
        else None,
        avg_return_pct=_round(sum(returns) / len(returns)) if returns else None,
        confidence_low_pct=low,
        confidence_high_pct=high,
        max_loss_pct=_round(min(returns)) if returns else None,
    )


def _bootstrap_mean_interval(
    values: list[float],
    samples: int,
    seed: int,
) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    generator = random.Random(seed)
    means = sorted(
        sum(generator.choice(values) for _ in values) / len(values)
        for _ in range(samples)
    )
    low_index = max(0, math.floor((len(means) - 1) * 0.025))
    high_index = min(len(means) - 1, math.ceil((len(means) - 1) * 0.975))
    return _round(means[low_index]), _round(means[high_index])


def _verdict(out_of_sample: TemporalValidationWindow | None) -> str:
    if out_of_sample is None or out_of_sample.sample_count < 10:
        return "insufficient"
    if out_of_sample.confidence_low_pct is not None and out_of_sample.confidence_low_pct > 0:
        return "positive"
    if out_of_sample.confidence_high_pct is not None and out_of_sample.confidence_high_pct < 0:
        return "negative"
    return "inconclusive"


def _warnings(
    rows: list[tuple[date, float]],
    windows: list[TemporalValidationWindow],
    out_of_sample: TemporalValidationWindow | None,
) -> list[str]:
    warnings: list[str] = []
    if len(rows) < 30:
        warnings.append("完成信号少于30个，样本外结论只能作为观察。")
    if len(windows) < 3:
        warnings.append("隔离期后不足三个有效时间窗口。")
    if out_of_sample is None or out_of_sample.sample_count < 10:
        warnings.append("样本外信号少于10个，暂不判断策略有效。")
    elif (
        out_of_sample.confidence_low_pct is None
        or out_of_sample.confidence_high_pct is None
        or out_of_sample.confidence_low_pct <= 0 <= out_of_sample.confidence_high_pct
    ):
        warnings.append("样本外均值置信区间跨过0，当前优势不稳定。")
    return warnings


def _summary(
    verdict: str,
    out_of_sample: TemporalValidationWindow | None,
) -> str:
    if out_of_sample is None or out_of_sample.sample_count == 0:
        return "隔离期后没有足够样本外信号。"
    interval = (
        f"95%区间 {out_of_sample.confidence_low_pct:+.2f}% 至 "
        f"{out_of_sample.confidence_high_pct:+.2f}%"
        if out_of_sample.confidence_low_pct is not None
        and out_of_sample.confidence_high_pct is not None
        else "置信区间待补样本"
    )
    labels = {
        "positive": "样本外表现为正",
        "negative": "样本外表现为负",
        "inconclusive": "样本外优势未确认",
        "insufficient": "样本外样本不足",
    }
    return (
        f"{labels[verdict]}：{out_of_sample.sample_count}个信号，"
        f"均值 {out_of_sample.avg_return_pct:+.2f}%，{interval}。"
    )


def _round(value: float) -> float:
    return round(value, 4)
