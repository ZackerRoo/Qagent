from datetime import date
from decimal import Decimal

import pandas as pd
from pydantic import BaseModel

from qagent.storage.repository import OpportunitySnapshotRecord


def compute_forward_returns(
    bars: pd.DataFrame,
    signal_date: date,
    horizons: tuple[int, ...] = (1, 5, 10, 20, 60),
) -> dict[str, float | None]:
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    matches = ordered.index[ordered["trade_date"] == signal_date].tolist()
    if not matches:
        raise ValueError("signal_date not found in bars")

    base_index = matches[0]
    base_close = float(ordered.loc[base_index, "close"])
    result: dict[str, float | None] = {}

    for horizon in horizons:
        target_index = base_index + horizon
        key = f"return_{horizon}d"
        if target_index >= len(ordered):
            result[key] = None
        else:
            future_close = float(ordered.loc[target_index, "close"])
            result[key] = round((future_close / base_close - 1) * 100, 4)

    return result


class OpportunityOutcome(BaseModel):
    snapshot_id: str
    run_id: str
    instrument_id: str
    primary_strategy_id: str | None
    signal_date: date | None
    outcome_status: str
    return_5d: float | None = None
    return_10d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    max_drawdown_pct: float | None = None
    max_runup_pct: float | None = None
    trigger_price: Decimal | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None


class StrategyPerformance(BaseModel):
    strategy_id: str
    sample_count: int
    completed_count: int
    pending_count: int
    target_hit_count: int
    stopped_count: int
    target_hit_rate: float | None
    positive_rate_10d: float | None
    avg_return_5d: float | None
    avg_return_10d: float | None
    avg_return_20d: float | None
    max_drawdown_pct: float | None
    max_runup_pct: float | None


def compute_opportunity_outcome(
    snapshot: OpportunitySnapshotRecord,
    bars: pd.DataFrame,
    horizons: tuple[int, ...] = (5, 10, 20, 60),
) -> OpportunityOutcome:
    if snapshot.signal_date is None or bars.empty:
        return _pending_outcome(snapshot)

    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    matches = ordered.index[ordered["trade_date"] == snapshot.signal_date].tolist()
    if not matches:
        return _pending_outcome(snapshot)

    base_index = matches[0]
    max_horizon = max(horizons)
    future = ordered.iloc[base_index + 1 : min(base_index + max_horizon + 1, len(ordered))]
    if future.empty:
        return _pending_outcome(snapshot)

    returns = compute_forward_returns(ordered, snapshot.signal_date, horizons)
    base_close = float(ordered.loc[base_index, "close"])
    max_drawdown_pct = round((float(future["low"].min()) / base_close - 1) * 100, 4)
    max_runup_pct = round((float(future["high"].max()) / base_close - 1) * 100, 4)
    target_hit = snapshot.target_1 is not None and bool(
        (future["high"] >= float(snapshot.target_1)).any()
    )
    stopped = snapshot.initial_stop is not None and bool(
        (future["low"] <= float(snapshot.initial_stop)).any()
    )
    status = _outcome_status(returns, target_hit, stopped)

    return OpportunityOutcome(
        snapshot_id=snapshot.snapshot_id,
        run_id=snapshot.run_id,
        instrument_id=snapshot.instrument_id,
        primary_strategy_id=snapshot.primary_strategy_id,
        signal_date=snapshot.signal_date,
        outcome_status=status,
        return_5d=returns.get("return_5d"),
        return_10d=returns.get("return_10d"),
        return_20d=returns.get("return_20d"),
        return_60d=returns.get("return_60d"),
        max_drawdown_pct=max_drawdown_pct,
        max_runup_pct=max_runup_pct,
        trigger_price=snapshot.trigger_price,
        initial_stop=snapshot.initial_stop,
        target_1=snapshot.target_1,
    )


def _pending_outcome(snapshot: OpportunitySnapshotRecord) -> OpportunityOutcome:
    return OpportunityOutcome(
        snapshot_id=snapshot.snapshot_id,
        run_id=snapshot.run_id,
        instrument_id=snapshot.instrument_id,
        primary_strategy_id=snapshot.primary_strategy_id,
        signal_date=snapshot.signal_date,
        outcome_status="pending",
        trigger_price=snapshot.trigger_price,
        initial_stop=snapshot.initial_stop,
        target_1=snapshot.target_1,
    )


def _outcome_status(
    returns: dict[str, float | None],
    target_hit: bool,
    stopped: bool,
) -> str:
    if target_hit:
        return "target_1_hit"
    if stopped:
        return "stopped"
    available_returns = [value for value in returns.values() if value is not None]
    if not available_returns:
        return "pending"
    return "working" if available_returns[-1] >= 0 else "lagging"


def summarize_strategy_performance(
    outcomes: list[OpportunityOutcome],
) -> list[StrategyPerformance]:
    grouped: dict[str, list[OpportunityOutcome]] = {}
    for outcome in outcomes:
        strategy_id = outcome.primary_strategy_id or "unclassified"
        grouped.setdefault(strategy_id, []).append(outcome)

    rows = []
    for strategy_id, items in grouped.items():
        completed = [item for item in items if item.outcome_status != "pending"]
        return_5d = [item.return_5d for item in completed if item.return_5d is not None]
        return_10d = [item.return_10d for item in completed if item.return_10d is not None]
        return_20d = [item.return_20d for item in completed if item.return_20d is not None]
        drawdowns = [
            item.max_drawdown_pct for item in completed if item.max_drawdown_pct is not None
        ]
        runups = [item.max_runup_pct for item in completed if item.max_runup_pct is not None]
        target_hit_count = sum(1 for item in completed if item.outcome_status == "target_1_hit")
        rows.append(
            StrategyPerformance(
                strategy_id=strategy_id,
                sample_count=len(items),
                completed_count=len(completed),
                pending_count=len(items) - len(completed),
                target_hit_count=target_hit_count,
                stopped_count=sum(1 for item in completed if item.outcome_status == "stopped"),
                target_hit_rate=_ratio(target_hit_count, len(completed)),
                positive_rate_10d=_ratio(sum(1 for value in return_10d if value > 0), len(return_10d)),
                avg_return_5d=_average(return_5d),
                avg_return_10d=_average(return_10d),
                avg_return_20d=_average(return_20d),
                max_drawdown_pct=min(drawdowns) if drawdowns else None,
                max_runup_pct=max(runups) if runups else None,
            )
        )
    return sorted(rows, key=lambda item: (item.target_hit_rate or 0, item.sample_count), reverse=True)


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)
