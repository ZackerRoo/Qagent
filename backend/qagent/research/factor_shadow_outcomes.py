from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import math
from typing import Iterable

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.market.calendars import trading_day_offset
from qagent.storage.factor_research import (
    FactorResearchRepository,
    FactorShadowOutcome,
    FactorShadowRunRef,
    FactorShadowScore,
)
from qagent.storage.market_cache import BAR_COLUMNS, MarketDataCacheRepository


FACTOR_SHADOW_OUTCOME_CONTRACT = "factor-shadow-outcome-v1-next-open-adjusted"
FACTOR_SHADOW_HORIZONS = (5, 10, 20)
FACTOR_SHADOW_ENTRY_WAIT_SESSIONS = 1


class FactorShadowOutcomeResolution(BaseModel):
    status: str
    as_of_date: date
    runs: int = 0
    matured_run_horizons: int = 0
    outcomes_inserted: int = 0
    outcomes_existing: int = 0
    unresolved_prices: int = 0
    next_maturity_date: date | None = None
    data_health: dict[str, str] = Field(default_factory=dict)


class FactorShadowHorizonEvaluation(BaseModel):
    horizon_sessions: int
    status: str
    matured_runs: int
    expected_instruments: int
    completed_instruments: int
    outcome_coverage: float
    mean_baseline_rank_ic: float | None = None
    mean_challenger_rank_ic: float | None = None
    baseline_top_excess_return_pct: float | None = None
    challenger_top_excess_return_pct: float | None = None
    challenger_top_net_excess_return_pct: float | None = None
    baseline_average_turnover_rate: float | None = None
    challenger_average_turnover_rate: float | None = None
    challenger_max_industry_concentration: float | None = None


class FactorShadowEvaluation(BaseModel):
    status: str
    experiment_id: str | None = None
    model_digest: str | None = None
    as_of_date: date
    run_count: int = 0
    signal_dates: list[date] = Field(default_factory=list)
    next_maturity_date: date | None = None
    horizons: list[FactorShadowHorizonEvaluation] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def resolve_factor_shadow_outcomes(
    session_factory: sessionmaker[Session],
    *,
    provider_mode: str,
    as_of_date: date,
    horizons: tuple[int, ...] = FACTOR_SHADOW_HORIZONS,
) -> FactorShadowOutcomeResolution:
    store = FactorResearchRepository(session_factory)
    bundle = store.latest_model_bundle(provider_mode)
    runs = store.latest_model_shadow_runs(provider_mode)
    if bundle is None or not runs:
        return FactorShadowOutcomeResolution(
            status="not_started",
            as_of_date=as_of_date,
            data_health={
                "factor_shadow_outcome_status": "not_started",
                "factor_shadow_outcome_contract": FACTOR_SHADOW_OUTCOME_CONTRACT,
            },
        )

    round_trip_cost_bps = float(bundle.experiment.config.get("round_trip_cost_bps", 20.0))
    existing = store.shadow_outcomes(bundle.experiment.experiment_id)
    existing_keys = {
        (item.scan_job_id, item.instrument_id, item.horizon_sessions) for item in existing
    }
    cache = MarketDataCacheRepository(session_factory)
    inserted = 0
    matured_run_horizons = 0
    unresolved_prices = 0
    next_maturity_dates: list[date] = []

    for run in runs:
        scores = store.shadow_scores(run.experiment_id, run.scan_job_id)
        for horizon in sorted(set(horizons)):
            entry_date, outcome_date = factor_shadow_outcome_dates(
                run.signal_date,
                horizon,
            )
            if outcome_date > as_of_date:
                next_maturity_dates.append(outcome_date)
                continue
            matured_run_horizons += 1
            unresolved_scores = [
                item
                for item in scores
                if (run.scan_job_id, item.instrument_id, horizon) not in existing_keys
            ]
            if not unresolved_scores:
                continue
            instrument_ids = [item.instrument_id for item in unresolved_scores]
            bars = _load_cached_bars(
                cache,
                provider_mode,
                [*instrument_ids, bundle.experiment.benchmark_id],
                entry_date,
                outcome_date,
            )
            benchmark_entry = _adjusted_price(
                bars,
                bundle.experiment.benchmark_id,
                entry_date,
                "adjusted_open",
            )
            benchmark_exit = _adjusted_price(
                bars,
                bundle.experiment.benchmark_id,
                outcome_date,
                "adjusted_close",
            )
            if benchmark_entry is None or benchmark_exit is None:
                unresolved_prices += len(unresolved_scores)
                continue
            benchmark_return = _return_pct(benchmark_entry, benchmark_exit)
            outcomes: list[FactorShadowOutcome] = []
            for score in unresolved_scores:
                entry = _adjusted_price(
                    bars,
                    score.instrument_id,
                    entry_date,
                    "adjusted_open",
                )
                exit_ = _adjusted_price(
                    bars,
                    score.instrument_id,
                    outcome_date,
                    "adjusted_close",
                )
                if entry is None or exit_ is None:
                    unresolved_prices += 1
                    continue
                instrument_return = _return_pct(entry, exit_)
                excess_return = instrument_return - benchmark_return
                net_excess_return = excess_return - round_trip_cost_bps / 100.0
                source_payload = {
                    "contract": FACTOR_SHADOW_OUTCOME_CONTRACT,
                    "experiment_id": run.experiment_id,
                    "scan_job_id": run.scan_job_id,
                    "instrument_id": score.instrument_id,
                    "horizon_sessions": horizon,
                    "signal_date": run.signal_date.isoformat(),
                    "entry_date": entry_date.isoformat(),
                    "outcome_date": outcome_date.isoformat(),
                    "entry_adjusted_open": entry,
                    "exit_adjusted_close": exit_,
                    "benchmark_id": bundle.experiment.benchmark_id,
                    "benchmark_entry_adjusted_open": benchmark_entry,
                    "benchmark_exit_adjusted_close": benchmark_exit,
                    "round_trip_cost_bps": round_trip_cost_bps,
                    "signal_dataset_revision": run.dataset_revision,
                    "model_digest": run.model_digest,
                }
                outcomes.append(
                    FactorShadowOutcome(
                        experiment_id=run.experiment_id,
                        scan_job_id=run.scan_job_id,
                        instrument_id=score.instrument_id,
                        horizon_sessions=horizon,
                        signal_date=run.signal_date,
                        entry_date=entry_date,
                        outcome_date=outcome_date,
                        benchmark_id=bundle.experiment.benchmark_id,
                        instrument_return_pct=round(instrument_return, 10),
                        benchmark_return_pct=round(benchmark_return, 10),
                        excess_return_pct=round(excess_return, 10),
                        net_excess_return_pct=round(net_excess_return, 10),
                        round_trip_cost_bps=round_trip_cost_bps,
                        signal_dataset_revision=run.dataset_revision,
                        model_digest=run.model_digest,
                        source_digest=_digest(source_payload),
                    )
                )
            inserted += store.record_shadow_outcomes(outcomes)

    if inserted:
        status = "recorded"
    elif unresolved_prices:
        status = "incomplete"
    elif next_maturity_dates:
        status = "waiting_for_maturity"
    else:
        status = "up_to_date"
    return FactorShadowOutcomeResolution(
        status=status,
        as_of_date=as_of_date,
        runs=len(runs),
        matured_run_horizons=matured_run_horizons,
        outcomes_inserted=inserted,
        outcomes_existing=len(existing),
        unresolved_prices=unresolved_prices,
        next_maturity_date=min(next_maturity_dates, default=None),
        data_health={
            "factor_shadow_outcome_status": status,
            "factor_shadow_outcome_contract": FACTOR_SHADOW_OUTCOME_CONTRACT,
            "factor_shadow_outcome_runs": str(len(runs)),
            "factor_shadow_outcome_inserted": str(inserted),
            "factor_shadow_outcome_existing": str(len(existing)),
            "factor_shadow_outcome_unresolved_prices": str(unresolved_prices),
            "factor_shadow_outcome_paper_isolation": "true",
            "factor_shadow_outcome_order_effect": "none",
        },
    )


def build_factor_shadow_evaluation(
    session_factory: sessionmaker[Session],
    *,
    provider_mode: str,
    as_of_date: date,
    horizons: tuple[int, ...] = FACTOR_SHADOW_HORIZONS,
) -> FactorShadowEvaluation:
    store = FactorResearchRepository(session_factory)
    bundle = store.latest_model_bundle(provider_mode)
    runs = store.latest_model_shadow_runs(provider_mode)
    if bundle is None or not runs:
        return FactorShadowEvaluation(
            status="not_started",
            as_of_date=as_of_date,
            data_health={
                "factor_shadow_evaluation_status": "not_started",
                "factor_shadow_outcome_contract": FACTOR_SHADOW_OUTCOME_CONTRACT,
            },
        )

    scores_by_run = {
        run.scan_job_id: store.shadow_scores(run.experiment_id, run.scan_job_id) for run in runs
    }
    outcomes = store.shadow_outcomes(bundle.experiment.experiment_id)
    outcomes_by_key = {
        (item.scan_job_id, item.instrument_id, item.horizon_sessions): item for item in outcomes
    }
    top_fraction = float(bundle.experiment.config.get("top_fraction", 0.1))
    evaluations = [
        _evaluate_horizon(
            runs,
            scores_by_run,
            outcomes_by_key,
            as_of_date=as_of_date,
            horizon_sessions=horizon,
            top_fraction=top_fraction,
        )
        for horizon in sorted(set(horizons))
    ]
    next_dates = [
        outcome_date
        for run in runs
        for horizon in horizons
        for _, outcome_date in [factor_shadow_outcome_dates(run.signal_date, horizon)]
        if outcome_date > as_of_date
    ]
    status = (
        "ready"
        if evaluations and all(item.status == "ready" for item in evaluations)
        else "collecting"
    )
    return FactorShadowEvaluation(
        status=status,
        experiment_id=bundle.experiment.experiment_id,
        model_digest=bundle.aggregate_model_digest,
        as_of_date=as_of_date,
        run_count=len(runs),
        signal_dates=sorted({run.signal_date for run in runs}),
        next_maturity_date=min(next_dates, default=None),
        horizons=evaluations,
        data_health={
            "factor_shadow_evaluation_status": status,
            "factor_shadow_evaluation_runs": str(len(runs)),
            "factor_shadow_evaluation_outcomes": str(len(outcomes)),
            "factor_shadow_outcome_contract": FACTOR_SHADOW_OUTCOME_CONTRACT,
            "factor_shadow_evaluation_paper_isolation": "true",
        },
    )


def factor_shadow_outcome_dates(
    signal_date: date,
    horizon_sessions: int,
) -> tuple[date, date]:
    if horizon_sessions <= 0:
        raise ValueError("factor shadow horizon must be positive")
    entry_date = trading_day_offset(signal_date, FACTOR_SHADOW_ENTRY_WAIT_SESSIONS)
    outcome_date = trading_day_offset(entry_date, horizon_sessions - 1)
    return entry_date, outcome_date


def _evaluate_horizon(
    runs: list[FactorShadowRunRef],
    scores_by_run: dict[str, list[FactorShadowScore]],
    outcomes_by_key: dict[tuple[str, str, int], FactorShadowOutcome],
    *,
    as_of_date: date,
    horizon_sessions: int,
    top_fraction: float,
) -> FactorShadowHorizonEvaluation:
    matured = [
        run
        for run in runs
        if factor_shadow_outcome_dates(run.signal_date, horizon_sessions)[1] <= as_of_date
    ]
    expected = sum(run.scored_instruments for run in matured)
    completed = sum(
        (run.scan_job_id, score.instrument_id, horizon_sessions) in outcomes_by_key
        for run in matured
        for score in scores_by_run.get(run.scan_job_id, [])
    )
    baseline_ics: list[float] = []
    challenger_ics: list[float] = []
    baseline_top_excess: list[float] = []
    challenger_top_excess: list[float] = []
    challenger_top_net_excess: list[float] = []
    challenger_industry_concentrations: list[float] = []
    baseline_sets: list[set[str]] = []
    challenger_sets: list[set[str]] = []

    for run in matured:
        scores = scores_by_run.get(run.scan_job_id, [])
        completed_pairs = [
            (
                score,
                outcomes_by_key.get((run.scan_job_id, score.instrument_id, horizon_sessions)),
            )
            for score in scores
        ]
        completed_pairs = [
            (score, outcome) for score, outcome in completed_pairs if outcome is not None
        ]
        if len(completed_pairs) >= 5:
            returns = [item.instrument_return_pct for _, item in completed_pairs]
            baseline_ic = _spearman(
                [score.baseline_score for score, _ in completed_pairs],
                returns,
            )
            challenger_ic = _spearman(
                [score.challenger_score for score, _ in completed_pairs],
                returns,
            )
            if baseline_ic is not None:
                baseline_ics.append(baseline_ic)
            if challenger_ic is not None:
                challenger_ics.append(challenger_ic)

        top_count = max(1, math.ceil(len(scores) * max(min(top_fraction, 1.0), 0.0)))
        baseline_top = sorted(scores, key=lambda item: item.baseline_rank)[:top_count]
        challenger_top = sorted(scores, key=lambda item: item.challenger_rank)[:top_count]
        baseline_sets.append({item.instrument_id for item in baseline_top})
        challenger_sets.append({item.instrument_id for item in challenger_top})
        baseline_top_outcomes = [
            outcome
            for score in baseline_top
            if (
                outcome := outcomes_by_key.get(
                    (run.scan_job_id, score.instrument_id, horizon_sessions)
                )
            )
            is not None
        ]
        challenger_top_outcomes = [
            outcome
            for score in challenger_top
            if (
                outcome := outcomes_by_key.get(
                    (run.scan_job_id, score.instrument_id, horizon_sessions)
                )
            )
            is not None
        ]
        baseline_top_excess.extend(item.excess_return_pct for item in baseline_top_outcomes)
        challenger_top_excess.extend(item.excess_return_pct for item in challenger_top_outcomes)
        challenger_top_net_excess.extend(
            item.net_excess_return_pct for item in challenger_top_outcomes
        )
        industries = [item.industry for item in challenger_top if item.industry]
        if industries:
            counts = pd.Series(industries).value_counts()
            challenger_industry_concentrations.append(float(counts.max() / len(industries)))

    coverage = completed / expected if expected else 0.0
    status = "pending" if not matured else "ready" if completed == expected else "partial"
    return FactorShadowHorizonEvaluation(
        horizon_sessions=horizon_sessions,
        status=status,
        matured_runs=len(matured),
        expected_instruments=expected,
        completed_instruments=completed,
        outcome_coverage=round(coverage, 6),
        mean_baseline_rank_ic=_rounded_mean(baseline_ics),
        mean_challenger_rank_ic=_rounded_mean(challenger_ics),
        baseline_top_excess_return_pct=_rounded_mean(baseline_top_excess),
        challenger_top_excess_return_pct=_rounded_mean(challenger_top_excess),
        challenger_top_net_excess_return_pct=_rounded_mean(challenger_top_net_excess),
        baseline_average_turnover_rate=_average_turnover(baseline_sets),
        challenger_average_turnover_rate=_average_turnover(challenger_sets),
        challenger_max_industry_concentration=(
            round(max(challenger_industry_concentrations), 6)
            if challenger_industry_concentrations
            else None
        ),
    )


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
    value = pd.to_numeric(rows.iloc[0], errors="coerce")
    return float(value) if pd.notna(value) and float(value) > 0 else None


def _return_pct(entry: float, exit_: float) -> float:
    return (exit_ / entry - 1.0) * 100.0


def _spearman(scores: list[float], returns: list[float]) -> float | None:
    value = pd.Series(scores, dtype="float64").corr(
        pd.Series(returns, dtype="float64"),
        method="spearman",
    )
    return round(float(value), 10) if pd.notna(value) and np.isfinite(value) else None


def _rounded_mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 10) if values else None


def _average_turnover(sets: list[set[str]]) -> float | None:
    if len(sets) < 2:
        return None
    values = [
        1.0 - len(previous & current) / max(len(previous), len(current), 1)
        for previous, current in zip(sets, sets[1:])
    ]
    return round(float(np.mean(values)), 6)


def _digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
