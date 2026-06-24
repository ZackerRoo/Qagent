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


class FactorBacktestResult(BaseModel):
    summary: FactorBacktestSummary
    signals: list[FactorBacktestSignal]
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
            data_health={"factor_backtest": "no_bars"},
        )
    ordered = bars.copy()
    ordered["trade_date"] = pd.to_datetime(ordered["trade_date"]).dt.date
    dates = sorted(ordered["trade_date"].unique())
    signals: list[FactorBacktestSignal] = []
    min_history_days = _minimum_history_days(forward_days)
    for date_index in range(min_history_days, max(min_history_days, len(dates) - forward_days), step_days):
        signal_date = dates[date_index]
        future_date = dates[date_index + forward_days]
        history = ordered[ordered["trade_date"] <= signal_date]
        rankings = build_factor_rankings(history)[:top_n]
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
        data_health={
            "factor_backtest": "ok",
            "forward_days": str(forward_days),
            "step_days": str(step_days),
            "top_n": str(top_n),
            "min_history_days": str(min_history_days),
            "signals": str(len(signals)),
        },
    )


def _minimum_history_days(forward_days: int) -> int:
    return min(120, max(20, forward_days * 2))


def _close_on_or_before(bars: pd.DataFrame, trade_date: date) -> float | None:
    eligible = bars[bars["trade_date"] <= trade_date].sort_values("trade_date")
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]["close"])
