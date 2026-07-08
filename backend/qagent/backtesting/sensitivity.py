from __future__ import annotations

from pydantic import BaseModel

from qagent.backtesting.engine import BacktestSignal


class ParameterSensitivityScenario(BaseModel):
    stop_loss_pct: float
    target_pct: float
    hold_days: int
    sample_count: int
    win_rate: float | None
    avg_return_pct: float | None
    median_return_pct: float | None
    max_drawdown_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None
    is_recommended: bool = False
    verdict: str
    summary: str


class ParameterSensitivitySummary(BaseModel):
    sample_count: int
    scenario_count: int
    recommended_stop_loss_pct: float | None
    recommended_target_pct: float | None
    recommended_hold_days: int | None
    data_basis: str


class ParameterSensitivityResult(BaseModel):
    summary: ParameterSensitivitySummary
    recommended: ParameterSensitivityScenario | None
    grid: list[ParameterSensitivityScenario]
    data_health: dict[str, str]


def build_parameter_sensitivity(
    signals: list[BacktestSignal],
    *,
    stop_loss_pcts: list[float] | None = None,
    target_pcts: list[float] | None = None,
    hold_days: list[int] | None = None,
) -> ParameterSensitivityResult:
    stops = stop_loss_pcts or [3.0, 5.0, 8.0]
    targets = target_pcts or [5.0, 8.0, 12.0]
    holds = hold_days or [5, 10, 20]
    completed = [
        signal
        for signal in signals
        if any(_horizon_return(signal, hold) is not None for hold in holds)
    ]
    grid: list[ParameterSensitivityScenario] = []
    for stop in stops:
        for target in targets:
            for hold in holds:
                returns = [
                    value
                    for signal in completed
                    if (value := _scenario_return(signal, stop, target, hold)) is not None
                ]
                grid.append(_scenario(stop, target, hold, returns))

    recommended = _choose_recommended(grid)
    if recommended is not None:
        for scenario in grid:
            scenario.is_recommended = scenario == recommended
        grid = sorted(
            grid,
            key=lambda item: (
                item.is_recommended,
                item.avg_return_pct if item.avg_return_pct is not None else -999.0,
                item.win_rate if item.win_rate is not None else -1.0,
                item.max_drawdown_pct if item.max_drawdown_pct is not None else -999.0,
            ),
            reverse=True,
        )

    return ParameterSensitivityResult(
        summary=ParameterSensitivitySummary(
            sample_count=len(completed),
            scenario_count=len(grid),
            recommended_stop_loss_pct=recommended.stop_loss_pct if recommended else None,
            recommended_target_pct=recommended.target_pct if recommended else None,
            recommended_hold_days=recommended.hold_days if recommended else None,
            data_basis="historical_recommendation_signals",
        ),
        recommended=recommended,
        grid=grid,
        data_health={
            "sensitivity_signals": str(len(signals)),
            "sensitivity_completed_signals": str(len(completed)),
            "sensitivity_scenarios": str(len(grid)),
            "sensitivity_model": "stop_target_hold_grid_from_backtest_signals",
        },
    )


def _scenario_return(
    signal: BacktestSignal,
    stop_loss_pct: float,
    target_pct: float,
    hold_days: int,
) -> float | None:
    period_return = _horizon_return(signal, hold_days)
    if period_return is None:
        return None
    drawdown = signal.max_drawdown_pct
    if drawdown is not None and float(drawdown) <= -abs(stop_loss_pct):
        return -abs(stop_loss_pct)
    if period_return >= abs(target_pct):
        return abs(target_pct)
    return period_return


def _horizon_return(signal: BacktestSignal, hold_days: int) -> float | None:
    if hold_days <= 5:
        return signal.return_5d
    if hold_days <= 10:
        return signal.return_10d
    if hold_days <= 20:
        return signal.return_20d
    return signal.return_60d


def _scenario(
    stop_loss_pct: float,
    target_pct: float,
    hold_days: int,
    returns: list[float],
) -> ParameterSensitivityScenario:
    avg_return = _average(returns)
    win_rate = _ratio(sum(1 for value in returns if value > 0), len(returns))
    max_drawdown = min(min(returns), 0.0) if returns else None
    best = max(returns) if returns else None
    worst = min(returns) if returns else None
    return ParameterSensitivityScenario(
        stop_loss_pct=stop_loss_pct,
        target_pct=target_pct,
        hold_days=hold_days,
        sample_count=len(returns),
        win_rate=win_rate,
        avg_return_pct=avg_return,
        median_return_pct=_median(returns),
        max_drawdown_pct=max_drawdown,
        best_return_pct=best,
        worst_return_pct=worst,
        verdict=_scenario_verdict(avg_return, win_rate, max_drawdown, len(returns)),
        summary=_scenario_summary(stop_loss_pct, target_pct, hold_days, avg_return, win_rate),
    )


def _choose_recommended(
    grid: list[ParameterSensitivityScenario],
) -> ParameterSensitivityScenario | None:
    eligible = [scenario for scenario in grid if scenario.sample_count > 0]
    if not eligible:
        return None
    return sorted(
        eligible,
        key=lambda item: (
            item.avg_return_pct if item.avg_return_pct is not None else -999.0,
            item.win_rate if item.win_rate is not None else -1.0,
            item.max_drawdown_pct if item.max_drawdown_pct is not None else -999.0,
            item.hold_days,
            -item.target_pct,
            -item.stop_loss_pct,
        ),
        reverse=True,
    )[0]


def _scenario_verdict(
    avg_return: float | None,
    win_rate: float | None,
    max_drawdown: float | None,
    sample_count: int,
) -> str:
    if sample_count < 5:
        return "观察"
    if avg_return is not None and avg_return > 1 and (win_rate or 0) >= 0.55:
        return "较优"
    if max_drawdown is not None and max_drawdown <= -8:
        return "偏激进"
    return "中性"


def _scenario_summary(
    stop_loss_pct: float,
    target_pct: float,
    hold_days: int,
    avg_return: float | None,
    win_rate: float | None,
) -> str:
    result = "样本不足" if avg_return is None else f"均值 {avg_return:+.2f}%"
    win = "-" if win_rate is None else f"{win_rate:.0%}"
    return f"止损 {stop_loss_pct:g}% / 目标 {target_pct:g}% / 持有 {hold_days} 日，{result}，胜率 {win}。"


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return round(ordered[middle], 4)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)
