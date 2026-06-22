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
