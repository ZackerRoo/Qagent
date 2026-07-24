from __future__ import annotations

from datetime import date
from enum import Enum

import pandas as pd
from pydantic import BaseModel, Field

from qagent.historical_evidence.providers import REQUIRED_BENCHMARK_IDS


class BenchmarkTrendState(str, Enum):
    RISK_ON = "risk_on"
    MIXED = "mixed"
    RISK_OFF = "risk_off"
    UNKNOWN = "unknown"


class BenchmarkTrendSnapshot(BaseModel):
    as_of: date
    state: BenchmarkTrendState
    lookback_sessions: int
    required_benchmarks: int
    valid_benchmarks: int
    above_average_count: int
    below_average_count: int
    entry_allowed: bool
    observations: dict[str, bool] = Field(default_factory=dict)
    reason: str


def build_benchmark_trend_snapshot(
    bars: pd.DataFrame,
    *,
    as_of: date,
    lookback_sessions: int = 60,
    benchmark_ids: tuple[str, ...] = REQUIRED_BENCHMARK_IDS,
    risk_off_below_count: int = 3,
) -> BenchmarkTrendSnapshot:
    if lookback_sessions <= 1:
        raise ValueError("lookback_sessions must be greater than 1")
    if risk_off_below_count <= 0:
        raise ValueError("risk_off_below_count must be positive")

    observations: dict[str, bool] = {}
    if bars is not None and not bars.empty:
        frame = bars.copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        frame = frame.loc[frame["trade_date"] <= as_of]
        for instrument_id in benchmark_ids:
            instrument = frame.loc[frame["instrument_id"] == instrument_id].sort_values(
                "trade_date"
            )
            closes = _usable_closes(instrument)
            if len(closes) < lookback_sessions:
                continue
            window = closes.iloc[-lookback_sessions:]
            observations[instrument_id] = bool(window.iloc[-1] >= window.mean())

    valid = len(observations)
    above = sum(observations.values())
    below = valid - above
    required = len(benchmark_ids)
    if valid < required:
        state = BenchmarkTrendState.UNKNOWN
        entry_allowed = True
        reason = (
            f"宽基趋势数据不足：{valid}/{required} 个指数具备 "
            f"{lookback_sessions} 个交易日。"
        )
    elif below >= risk_off_below_count:
        state = BenchmarkTrendState.RISK_OFF
        entry_allowed = False
        reason = (
            f"{below}/{valid} 个宽基指数低于 {lookback_sessions} 日均线，"
            "暂停新增仓位。"
        )
    elif above >= risk_off_below_count:
        state = BenchmarkTrendState.RISK_ON
        entry_allowed = True
        reason = (
            f"{above}/{valid} 个宽基指数位于 {lookback_sessions} 日均线上方。"
        )
    else:
        state = BenchmarkTrendState.MIXED
        entry_allowed = True
        reason = (
            f"宽基趋势分化：{above} 个在均线上方，{below} 个在均线下方。"
        )
    return BenchmarkTrendSnapshot(
        as_of=as_of,
        state=state,
        lookback_sessions=lookback_sessions,
        required_benchmarks=required,
        valid_benchmarks=valid,
        above_average_count=above,
        below_average_count=below,
        entry_allowed=entry_allowed,
        observations=observations,
        reason=reason,
    )


def benchmark_trend_data_health(
    snapshot: BenchmarkTrendSnapshot,
) -> dict[str, str]:
    return {
        "benchmark_trend_state": snapshot.state.value,
        "benchmark_trend_entry_allowed": str(snapshot.entry_allowed).lower(),
        "benchmark_trend_lookback_sessions": str(snapshot.lookback_sessions),
        "benchmark_trend_valid_benchmarks": str(snapshot.valid_benchmarks),
        "benchmark_trend_above_average": str(snapshot.above_average_count),
        "benchmark_trend_below_average": str(snapshot.below_average_count),
        "benchmark_trend_reason": snapshot.reason,
    }


def _usable_closes(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="float64")
    adjusted = (
        pd.to_numeric(frame["adjusted_close"], errors="coerce")
        if "adjusted_close" in frame.columns
        else pd.Series(index=frame.index, dtype="float64")
    )
    raw = (
        pd.to_numeric(frame["close"], errors="coerce")
        if "close" in frame.columns
        else pd.Series(index=frame.index, dtype="float64")
    )
    return adjusted.fillna(raw).dropna().astype(float)
