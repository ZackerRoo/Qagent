from collections import defaultdict
from collections.abc import Iterable

import pandas as pd

from qagent.monitoring.outcomes import compute_forward_returns
from qagent.signals.engine import SignalEngine
from qagent.strategies.evaluator import StrategyEvaluator
from qagent.strategies.models import StrategyHealth
from qagent.strategies.registry import StrategyRegistry


def summarize_strategy_health(
    registry: StrategyRegistry,
    outcome_rows: Iterable[dict[str, float | str | None]],
) -> list[StrategyHealth]:
    grouped: dict[str, list[dict[str, float | str | None]]] = defaultdict(list)
    for row in outcome_rows:
        strategy_id = row.get("strategy_id")
        if isinstance(strategy_id, str):
            grouped[strategy_id].append(row)

    health: list[StrategyHealth] = []
    for definition in registry.all():
        rows = grouped.get(definition.strategy_id, [])
        return_10d = _numeric_values(rows, "return_10d")
        return_20d = _numeric_values(rows, "return_20d")
        sample_count = len(return_10d)
        health.append(
            StrategyHealth(
                strategy_id=definition.strategy_id,
                name=definition.name,
                family=definition.family,
                readiness=_readiness(definition.free_data_ready, sample_count, return_10d),
                sample_count=sample_count,
                win_rate_10d=_win_rate(return_10d),
                avg_return_10d=_average(return_10d),
                avg_return_20d=_average(return_20d),
                max_loss_10d=min(return_10d) if return_10d else None,
                missing_data=[] if definition.free_data_ready else list(definition.required_data),
            )
        )
    return health


def build_strategy_health_from_bars(
    bars_by_instrument: dict[str, pd.DataFrame],
    registry: StrategyRegistry,
    horizons: tuple[int, ...] = (10, 20),
    min_bars: int = 50,
    sample_step: int = 5,
) -> list[StrategyHealth]:
    signal_engine = SignalEngine()
    evaluator = StrategyEvaluator(registry)
    outcome_rows: list[dict[str, float | str | None]] = []

    min_horizon = min(horizons)
    for instrument_id, bars in bars_by_instrument.items():
        if bars.empty or len(bars) < min_bars + min_horizon:
            continue
        ordered = bars.sort_values("trade_date").reset_index(drop=True)
        last_index_with_forward_return = len(ordered) - min_horizon - 1
        for index in range(min_bars - 1, last_index_with_forward_return + 1, sample_step):
            sliced = ordered.iloc[: index + 1].copy()
            signals = signal_engine.generate(instrument_id, sliced)
            evaluations = evaluator.evaluate(instrument_id, signals, sliced)
            signal_date = ordered.loc[index, "trade_date"]
            returns = compute_forward_returns(ordered, signal_date=signal_date, horizons=horizons)
            for evaluation in evaluations:
                if evaluation.status not in {"passed", "watch"} or evaluation.score <= 0:
                    continue
                outcome_rows.append({"strategy_id": evaluation.strategy_id, **returns})

    return summarize_strategy_health(registry, outcome_rows)


def _numeric_values(rows: list[dict[str, float | str | None]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def _win_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(1 for value in values if value > 0) / len(values) * 100, 2)


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _readiness(free_data_ready: bool, sample_count: int, return_10d: list[float]) -> str:
    if sample_count == 0:
        return "insufficient_history" if free_data_ready else "missing_data"
    if sample_count < 20:
        return "limited_sample"
    if (_win_rate(return_10d) or 0) >= 55 and (_average(return_10d) or 0) > 0:
        return "validated"
    return "watch"
