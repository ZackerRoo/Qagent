from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import math
from typing import Any, Iterable

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.research.factor_shadow_outcomes import factor_shadow_outcome_dates
from qagent.storage.fuyao_research import FuyaoResearchRepository, FuyaoResearchSnapshot
from qagent.storage.fuyao_shadow import FuyaoShadowOutcome, FuyaoShadowRepository
from qagent.storage.market_cache import BAR_COLUMNS, MarketDataCacheRepository


FUYAO_SHADOW_OUTCOME_CONTRACT = "fuyao-market-shadow-v1-next-open-adjusted"
FUYAO_SHADOW_HORIZONS = (5, 10, 20)
FUYAO_SHADOW_BENCHMARK_ID = "CN:000300.IDX"
FUYAO_SHADOW_ROUND_TRIP_COST_BPS = 20.0


class FuyaoShadowResolution(BaseModel):
    status: str
    as_of_date: date
    snapshots: int = 0
    matured_snapshot_horizons: int = 0
    outcomes_inserted: int = 0
    outcomes_existing: int = 0
    unresolved_prices: int = 0
    next_maturity_date: date | None = None
    data_health: dict[str, str] = Field(default_factory=dict)


class FuyaoShadowHorizonEvaluation(BaseModel):
    horizon_sessions: int
    status: str
    matured_snapshots: int
    expected_signals: int
    completed_signals: int
    outcome_coverage: float
    mean_rank_ic: float | None = None
    average_excess_return_pct: float | None = None
    average_net_excess_return_pct: float | None = None
    top_quintile_net_excess_return_pct: float | None = None
    positive_excess_rate: float | None = None


class FuyaoShadowEvaluation(BaseModel):
    status: str
    as_of_date: date
    contract: str = FUYAO_SHADOW_OUTCOME_CONTRACT
    snapshot_count: int = 0
    signal_dates: list[date] = Field(default_factory=list)
    next_maturity_date: date | None = None
    horizons: list[FuyaoShadowHorizonEvaluation] = Field(default_factory=list)
    classification: str = "research_only"
    decision_weight_applied: bool = False
    paper_order_side_effect: bool = False
    data_health: dict[str, str] = Field(default_factory=dict)


def resolve_fuyao_shadow_outcomes(
    session_factory: sessionmaker[Session],
    *,
    provider_mode: str,
    as_of_date: date,
    horizons: tuple[int, ...] = FUYAO_SHADOW_HORIZONS,
) -> FuyaoShadowResolution:
    snapshots = _daily_market_snapshots(session_factory)
    if not snapshots:
        return FuyaoShadowResolution(
            status="not_started",
            as_of_date=as_of_date,
            data_health=_health("not_started", 0, 0),
        )
    outcome_store = FuyaoShadowRepository(session_factory)
    existing = outcome_store.list_outcomes()
    existing_keys = {
        (item.snapshot_id, item.instrument_id, item.horizon_sessions) for item in existing
    }
    cache = MarketDataCacheRepository(session_factory)
    inserted = 0
    unresolved = 0
    matured = 0
    next_dates: list[date] = []

    for snapshot in snapshots:
        signal_date = _snapshot_signal_date(snapshot)
        signals = _snapshot_signals(snapshot)
        if signal_date is None or not signals:
            continue
        for horizon in sorted(set(horizons)):
            entry_date, outcome_date = factor_shadow_outcome_dates(signal_date, horizon)
            if outcome_date > as_of_date:
                next_dates.append(outcome_date)
                continue
            matured += 1
            unresolved_signals = [
                signal
                for signal in signals
                if (
                    snapshot.snapshot_id,
                    str(signal["instrument_id"]),
                    horizon,
                )
                not in existing_keys
            ]
            if not unresolved_signals:
                continue
            instrument_ids = [str(item["instrument_id"]) for item in unresolved_signals]
            bars = _load_cached_bars(
                cache,
                provider_mode,
                [*instrument_ids, FUYAO_SHADOW_BENCHMARK_ID],
                entry_date,
                outcome_date,
            )
            benchmark_entry = _adjusted_price(
                bars,
                FUYAO_SHADOW_BENCHMARK_ID,
                entry_date,
                "adjusted_open",
            )
            benchmark_exit = _adjusted_price(
                bars,
                FUYAO_SHADOW_BENCHMARK_ID,
                outcome_date,
                "adjusted_close",
            )
            if benchmark_entry is None or benchmark_exit is None:
                unresolved += len(unresolved_signals)
                continue
            benchmark_return = _return_pct(benchmark_entry, benchmark_exit)
            outcomes: list[FuyaoShadowOutcome] = []
            for signal in unresolved_signals:
                instrument_id = str(signal["instrument_id"])
                entry = _adjusted_price(bars, instrument_id, entry_date, "adjusted_open")
                exit_ = _adjusted_price(bars, instrument_id, outcome_date, "adjusted_close")
                if entry is None or exit_ is None:
                    unresolved += 1
                    continue
                instrument_return = _return_pct(entry, exit_)
                excess = instrument_return - benchmark_return
                outcomes.append(
                    FuyaoShadowOutcome(
                        snapshot_id=snapshot.snapshot_id,
                        instrument_id=instrument_id,
                        signal_date=signal_date,
                        horizon_sessions=horizon,
                        entry_date=entry_date,
                        outcome_date=outcome_date,
                        signal_score=float(signal["score"]),
                        entry_adjusted_open=entry,
                        exit_adjusted_close=exit_,
                        benchmark_id=FUYAO_SHADOW_BENCHMARK_ID,
                        benchmark_entry_adjusted_open=benchmark_entry,
                        benchmark_exit_adjusted_close=benchmark_exit,
                        instrument_return_pct=instrument_return,
                        benchmark_return_pct=benchmark_return,
                        excess_return_pct=excess,
                        net_excess_return_pct=(
                            excess - FUYAO_SHADOW_ROUND_TRIP_COST_BPS / 100.0
                        ),
                        round_trip_cost_bps=FUYAO_SHADOW_ROUND_TRIP_COST_BPS,
                        source_digest=_source_digest(
                            snapshot,
                            instrument_id=instrument_id,
                            horizon=horizon,
                            entry_date=entry_date,
                            outcome_date=outcome_date,
                        ),
                    )
                )
            inserted += outcome_store.append_outcomes(outcomes)

    refreshed = outcome_store.list_outcomes()
    next_maturity = min(next_dates, default=None)
    status = "resolved" if matured else "collecting"
    return FuyaoShadowResolution(
        status=status,
        as_of_date=as_of_date,
        snapshots=len(snapshots),
        matured_snapshot_horizons=matured,
        outcomes_inserted=inserted,
        outcomes_existing=max(len(refreshed) - inserted, 0),
        unresolved_prices=unresolved,
        next_maturity_date=next_maturity,
        data_health=_health(status, len(snapshots), len(refreshed)),
    )


def build_fuyao_shadow_evaluation(
    session_factory: sessionmaker[Session],
    *,
    as_of_date: date,
    horizons: tuple[int, ...] = FUYAO_SHADOW_HORIZONS,
) -> FuyaoShadowEvaluation:
    snapshots = _daily_market_snapshots(session_factory)
    outcomes = FuyaoShadowRepository(session_factory).list_outcomes()
    outcomes_by_key = {
        (item.snapshot_id, item.instrument_id, item.horizon_sessions): item
        for item in outcomes
    }
    next_dates = [
        outcome_date
        for snapshot in snapshots
        for signal_date in [_snapshot_signal_date(snapshot)]
        if signal_date is not None
        for horizon in horizons
        for _, outcome_date in [factor_shadow_outcome_dates(signal_date, horizon)]
        if outcome_date > as_of_date
    ]
    evaluations = [
        _evaluate_horizon(
            snapshots,
            outcomes_by_key,
            as_of_date=as_of_date,
            horizon=horizon,
        )
        for horizon in sorted(set(horizons))
    ]
    if not snapshots:
        status = "not_started"
    elif evaluations and all(item.status == "ready" for item in evaluations):
        status = "ready"
    else:
        status = "collecting"
    signal_dates = sorted(
        {value for snapshot in snapshots if (value := _snapshot_signal_date(snapshot)) is not None}
    )
    return FuyaoShadowEvaluation(
        status=status,
        as_of_date=as_of_date,
        snapshot_count=len(snapshots),
        signal_dates=signal_dates,
        next_maturity_date=min(next_dates, default=None),
        horizons=evaluations,
        data_health={
            **_health(status, len(snapshots), len(outcomes)),
            "fuyao_shadow_paper_isolation": "true",
            "fuyao_shadow_order_effect": "none",
        },
    )


def _evaluate_horizon(
    snapshots: list[FuyaoResearchSnapshot],
    outcomes_by_key: dict[tuple[str, str, int], FuyaoShadowOutcome],
    *,
    as_of_date: date,
    horizon: int,
) -> FuyaoShadowHorizonEvaluation:
    matured = [
        snapshot
        for snapshot in snapshots
        if (signal_date := _snapshot_signal_date(snapshot)) is not None
        and factor_shadow_outcome_dates(signal_date, horizon)[1] <= as_of_date
    ]
    expected = sum(len(_snapshot_signals(snapshot)) for snapshot in matured)
    completed_outcomes: list[FuyaoShadowOutcome] = []
    rank_ics: list[float] = []
    top_returns: list[float] = []
    for snapshot in matured:
        signals = _snapshot_signals(snapshot)
        pairs = [
            (
                signal,
                outcomes_by_key.get(
                    (snapshot.snapshot_id, str(signal["instrument_id"]), horizon)
                ),
            )
            for signal in signals
        ]
        pairs = [(signal, outcome) for signal, outcome in pairs if outcome is not None]
        completed_outcomes.extend(outcome for _, outcome in pairs)
        if len(pairs) >= 5:
            rank_ic = _spearman(
                [float(signal["score"]) for signal, _ in pairs],
                [outcome.excess_return_pct for _, outcome in pairs],
            )
            if rank_ic is not None:
                rank_ics.append(rank_ic)
        top_count = max(1, math.ceil(len(pairs) * 0.2)) if pairs else 0
        top = sorted(pairs, key=lambda item: float(item[0]["score"]), reverse=True)[:top_count]
        top_returns.extend(outcome.net_excess_return_pct for _, outcome in top)
    completed = len(completed_outcomes)
    coverage = completed / expected if expected else 0.0
    status = "pending" if not matured else "ready" if completed == expected else "partial"
    excess = [item.excess_return_pct for item in completed_outcomes]
    net_excess = [item.net_excess_return_pct for item in completed_outcomes]
    return FuyaoShadowHorizonEvaluation(
        horizon_sessions=horizon,
        status=status,
        matured_snapshots=len(matured),
        expected_signals=expected,
        completed_signals=completed,
        outcome_coverage=round(coverage, 6),
        mean_rank_ic=_mean(rank_ics),
        average_excess_return_pct=_mean(excess),
        average_net_excess_return_pct=_mean(net_excess),
        top_quintile_net_excess_return_pct=_mean(top_returns),
        positive_excess_rate=(
            round(sum(value > 0 for value in excess) / len(excess), 6) if excess else None
        ),
    )


def _daily_market_snapshots(
    session_factory: sessionmaker[Session],
) -> list[FuyaoResearchSnapshot]:
    snapshots = FuyaoResearchRepository(session_factory).list_for_type("market", limit=2_000)
    by_signal_date: dict[date, FuyaoResearchSnapshot] = {}
    for snapshot in snapshots:
        signal_date = _snapshot_signal_date(snapshot)
        if signal_date is not None and signal_date not in by_signal_date:
            by_signal_date[signal_date] = snapshot
    return [by_signal_date[key] for key in sorted(by_signal_date)]


def _snapshot_signal_date(snapshot: FuyaoResearchSnapshot) -> date | None:
    value = snapshot.identity.get("trade_date")
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _snapshot_signals(snapshot: FuyaoResearchSnapshot) -> list[dict[str, Any]]:
    sections = snapshot.payload.get("sections")
    if not isinstance(sections, dict):
        return []
    sentiment = sections.get("derived_sentiment")
    if not isinstance(sentiment, dict):
        return []
    raw = sentiment.get("signals")
    if not isinstance(raw, list):
        return []
    return [
        item
        for item in raw
        if isinstance(item, dict)
        and isinstance(item.get("instrument_id"), str)
        and _finite(item.get("score")) is not None
    ]


def _load_cached_bars(
    cache: MarketDataCacheRepository,
    provider_mode: str,
    instrument_ids: Iterable[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    unique_ids = sorted(set(instrument_ids))
    frames = [
        cache.load_daily_bars(provider_mode, unique_ids[offset : offset + 500], start, end)
        for offset in range(0, len(unique_ids), 500)
    ]
    nonempty = [frame for frame in frames if not frame.empty]
    return pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame(columns=BAR_COLUMNS)


def _adjusted_price(
    bars: pd.DataFrame,
    instrument_id: str,
    trade_date: date,
    column: str,
) -> float | None:
    if bars.empty or column not in bars.columns:
        return None
    rows = bars.loc[
        (bars["instrument_id"] == instrument_id) & (bars["trade_date"] == trade_date),
        column,
    ]
    if len(rows) != 1:
        return None
    value = _finite(rows.iloc[0])
    return value if value is not None and value > 0 else None


def _return_pct(entry: float, exit_: float) -> float:
    return (exit_ / entry - 1.0) * 100.0


def _spearman(scores: list[float], returns: list[float]) -> float | None:
    if len(scores) != len(returns) or len(scores) < 2:
        return None
    value = pd.Series(scores, dtype="float64").rank(method="average").corr(
        pd.Series(returns, dtype="float64").rank(method="average"),
        method="pearson",
    )
    return round(float(value), 10) if pd.notna(value) and np.isfinite(value) else None


def _mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 10) if values else None


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _source_digest(
    snapshot: FuyaoResearchSnapshot,
    *,
    instrument_id: str,
    horizon: int,
    entry_date: date,
    outcome_date: date,
) -> str:
    payload = {
        "contract": FUYAO_SHADOW_OUTCOME_CONTRACT,
        "snapshot_id": snapshot.snapshot_id,
        "payload_digest": snapshot.payload_digest,
        "instrument_id": instrument_id,
        "horizon_sessions": horizon,
        "entry_date": entry_date.isoformat(),
        "outcome_date": outcome_date.isoformat(),
        "benchmark_id": FUYAO_SHADOW_BENCHMARK_ID,
        "round_trip_cost_bps": FUYAO_SHADOW_ROUND_TRIP_COST_BPS,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _health(status: str, snapshots: int, outcomes: int) -> dict[str, str]:
    return {
        "fuyao_shadow_status": status,
        "fuyao_shadow_contract": FUYAO_SHADOW_OUTCOME_CONTRACT,
        "fuyao_shadow_snapshots": str(snapshots),
        "fuyao_shadow_outcomes": str(outcomes),
        "fuyao_shadow_classification": "research_only",
        "fuyao_shadow_decision_weight_applied": "false",
    }
