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
from qagent.research.shadow_price_repair import (
    ExactPriceRequirement,
    repair_exact_daily_prices,
)
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
# Shadow evidence is deliberately harder to promote than to record. These
# thresholds only determine whether a frozen challenger merits manual review;
# they never change paper-trading weights or admission by themselves.
FACTOR_SHADOW_PROMOTION_MIN_MATURED_RUNS = 20
FACTOR_SHADOW_PROMOTION_MIN_OUTCOME_COVERAGE = 0.95
FACTOR_SHADOW_PROMOTION_MIN_SESSION_EDGE_RATE = 0.55
FACTOR_SHADOW_PROMOTION_MIN_SELECTION_LIFT_RATE = 0.55
FACTOR_SHADOW_RUN_SELECTION_RULE = "earliest_created_at_then_scan_job_id_per_signal_date"
FACTOR_SHADOW_EXECUTION_HEAD_POLICY = "factor-shadow-execution-head-top10-cap3-v1"
FACTOR_SHADOW_EXECUTION_HEAD_SIZE = 10
FACTOR_SHADOW_EXECUTION_HEAD_INDUSTRY_CAP = 3
FACTOR_SHADOW_UNKNOWN_INDUSTRY_BUCKET = "unknown"


class FactorShadowOutcomeResolution(BaseModel):
    status: str
    as_of_date: date
    experiment_id: str | None = None
    # Compatibility names: both counts use one canonical run per signal date.
    runs: int = 0
    matured_run_horizons: int = 0
    outcomes_inserted: int = 0
    outcomes_existing: int = 0
    unresolved_prices: int = 0
    next_maturity_date: date | None = None
    data_health: dict[str, str] = Field(default_factory=dict)


class FactorShadowBenchmarkRefresh(BaseModel):
    status: str
    benchmark_id: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    data_health: dict[str, str] = Field(default_factory=dict)


class FactorShadowAttributionGroup(BaseModel):
    key: str
    label: str
    sample_count: int
    average_excess_return_pct: float | None
    average_net_excess_return_pct: float | None
    positive_net_excess_rate: float | None


class FactorShadowExecutionHeadEvaluation(BaseModel):
    """Execution-sized, constraint-matched evidence with no paper-order effect."""

    policy_version: str = FACTOR_SHADOW_EXECUTION_HEAD_POLICY
    requested_size: int = FACTOR_SHADOW_EXECUTION_HEAD_SIZE
    industry_cap: int = FACTOR_SHADOW_EXECUTION_HEAD_INDUSTRY_CAP
    unknown_industry_bucket: str = FACTOR_SHADOW_UNKNOWN_INDUSTRY_BUCKET
    matured_sessions: int = 0
    baseline_selection_slots: int = 0
    challenger_selection_slots: int = 0
    baseline_completed_outcomes: int = 0
    challenger_completed_outcomes: int = 0
    baseline_full_sessions: int = 0
    challenger_full_sessions: int = 0
    paired_outcome_sessions: int = 0
    baseline_all_matured_sessions_filled: bool = False
    challenger_all_matured_sessions_filled: bool = False
    baseline_head_net_excess_return_pct: float | None = None
    challenger_head_net_excess_return_pct: float | None = None
    challenger_lift_win_rate: float | None = None
    challenger_median_lift_pct: float | None = None
    baseline_raw_max_industry_positions: int = 0
    challenger_raw_max_industry_positions: int = 0
    baseline_raw_max_industry_concentration: float | None = None
    challenger_raw_max_industry_concentration: float | None = None
    baseline_max_industry_positions: int = 0
    challenger_max_industry_positions: int = 0
    baseline_max_industry_concentration: float | None = None
    challenger_max_industry_concentration: float | None = None


class FactorShadowHorizonEvaluation(BaseModel):
    horizon_sessions: int
    status: str
    # Retained for API compatibility; semantically this is mature signal dates.
    matured_runs: int
    expected_instruments: int
    completed_instruments: int
    outcome_coverage: float
    signal_date_scored_sessions: int = 0
    signal_date_scored_coverage: float = 0.0
    scored_cohort_instruments: int = 0
    scored_cohort_outcome_coverage: float = 0.0
    eligible_universe_instruments: int | None = None
    universe_coverage: str = "unknown"
    selection_filled_instruments: int = 0
    outcome_filled_instruments: int = 0
    paired_outcome_sessions: int = 0
    mean_baseline_rank_ic: float | None = None
    mean_challenger_rank_ic: float | None = None
    baseline_top_excess_return_pct: float | None = None
    challenger_top_excess_return_pct: float | None = None
    challenger_top_net_excess_return_pct: float | None = None
    baseline_average_turnover_rate: float | None = None
    challenger_average_turnover_rate: float | None = None
    challenger_max_industry_concentration: float | None = None
    challenger_session_count: int = 0
    challenger_session_outperformance_rate: float | None = None
    challenger_rank_ic_win_rate: float | None = None
    challenger_median_session_net_excess_return_pct: float | None = None
    challenger_addition_count: int = 0
    challenger_removal_count: int = 0
    challenger_addition_net_excess_return_pct: float | None = None
    challenger_removal_net_excess_return_pct: float | None = None
    challenger_selection_lift_session_count: int = 0
    challenger_selection_lift_win_rate: float | None = None
    challenger_median_selection_lift_pct: float | None = None
    challenger_rank_buckets: list[FactorShadowAttributionGroup] = Field(default_factory=list)
    challenger_industries: list[FactorShadowAttributionGroup] = Field(default_factory=list)
    execution_head: FactorShadowExecutionHeadEvaluation = Field(
        default_factory=FactorShadowExecutionHeadEvaluation
    )


class FactorShadowPromotionAssessment(BaseModel):
    """Non-binding evidence assessment for a frozen factor challenger."""

    status: str
    action: str
    eligible_for_manual_review: bool = False
    required_horizons: tuple[int, ...] = FACTOR_SHADOW_HORIZONS
    reasons: list[str] = Field(default_factory=list)


class FactorShadowEvaluation(BaseModel):
    status: str
    experiment_id: str | None = None
    model_digest: str | None = None
    as_of_date: date
    # Retained for API compatibility; semantically this is unique signal dates.
    run_count: int = 0
    signal_dates: list[date] = Field(default_factory=list)
    next_maturity_date: date | None = None
    horizons: list[FactorShadowHorizonEvaluation] = Field(default_factory=list)
    promotion: FactorShadowPromotionAssessment | None = None
    data_health: dict[str, str] = Field(default_factory=dict)


class FactorShadowCandidate(BaseModel):
    """One frozen challenger evaluated independently from the paper baseline."""

    experiment_id: str
    experiment_name: str
    config_digest: str
    model_digest: str
    status: str
    eligible_for_manual_review: bool = False
    evaluation: FactorShadowEvaluation


class FactorShadowRoster(BaseModel):
    status: str
    as_of_date: date
    candidates: list[FactorShadowCandidate] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def refresh_factor_shadow_benchmark_cache(
    session_factory: sessionmaker[Session],
    *,
    provider_mode: str,
    market_provider: object,
    as_of_date: date,
    horizons: tuple[int, ...] = FACTOR_SHADOW_HORIZONS,
    experiment_id: str | None = None,
) -> FactorShadowBenchmarkRefresh:
    """Refresh only the benchmark bars needed by already-matured shadow runs."""

    store = FactorResearchRepository(session_factory)
    if experiment_id is None:
        bundles = store.model_bundles(provider_mode)
    else:
        selected_bundle = store.model_bundle(experiment_id)
        bundles = [selected_bundle] if selected_bundle is not None else []
    if experiment_id is None and len(bundles) > 1:
        refreshed = [
            refresh_factor_shadow_benchmark_cache(
                session_factory,
                provider_mode=provider_mode,
                market_provider=market_provider,
                as_of_date=as_of_date,
                horizons=horizons,
                experiment_id=bundle.experiment.experiment_id,
            )
            for bundle in bundles
        ]
        status = (
            "refreshed"
            if any(item.status == "refreshed" for item in refreshed)
            else refreshed[0].status
        )
        starts = [item.start_date for item in refreshed if item.start_date is not None]
        ends = [item.end_date for item in refreshed if item.end_date is not None]
        return FactorShadowBenchmarkRefresh(
            status=status,
            benchmark_id=",".join(
                sorted({item.benchmark_id for item in refreshed if item.benchmark_id})
            )
            or None,
            start_date=min(starts, default=None),
            end_date=max(ends, default=None),
            data_health={
                "factor_shadow_benchmark_refresh": status,
                "factor_shadow_benchmark_candidates": str(len(refreshed)),
                "factor_shadow_paper_isolation": "true",
            },
        )
    bundle = bundles[0] if bundles else None
    raw_runs = store.shadow_runs(bundle.experiment.experiment_id) if bundle is not None else []
    runs = _canonical_shadow_runs(raw_runs)
    if bundle is None or not runs:
        return FactorShadowBenchmarkRefresh(
            status="not_started",
            data_health={"factor_shadow_benchmark_refresh": "not_started"},
        )

    windows = [
        factor_shadow_outcome_dates(run.signal_date, horizon)
        for run in runs
        for horizon in sorted(set(horizons))
        if factor_shadow_outcome_dates(run.signal_date, horizon)[1] <= as_of_date
    ]
    if not windows:
        return FactorShadowBenchmarkRefresh(
            status="waiting_for_maturity",
            benchmark_id=bundle.experiment.benchmark_id,
            data_health={"factor_shadow_benchmark_refresh": "waiting_for_maturity"},
        )

    start_date = min(entry_date for entry_date, _ in windows)
    end_date = max(outcome_date for _, outcome_date in windows)
    prefetch = getattr(market_provider, "prefetch_daily_bars", None)
    if not callable(prefetch):
        return FactorShadowBenchmarkRefresh(
            status="unsupported_provider",
            benchmark_id=bundle.experiment.benchmark_id,
            start_date=start_date,
            end_date=end_date,
            data_health={"factor_shadow_benchmark_refresh": "unsupported_provider"},
        )

    try:
        prefetch([bundle.experiment.benchmark_id], start=start_date, end=end_date)
    except Exception as exc:
        return FactorShadowBenchmarkRefresh(
            status="error",
            benchmark_id=bundle.experiment.benchmark_id,
            start_date=start_date,
            end_date=end_date,
            data_health={
                "factor_shadow_benchmark_refresh": "error",
                "factor_shadow_benchmark_refresh_error": str(exc)[:500],
            },
        )

    stats_getter = getattr(market_provider, "prefetch_stats", None)
    stats = stats_getter() if callable(stats_getter) else {}
    return FactorShadowBenchmarkRefresh(
        status="refreshed",
        benchmark_id=bundle.experiment.benchmark_id,
        start_date=start_date,
        end_date=end_date,
        data_health={
            "factor_shadow_benchmark_refresh": "refreshed",
            "factor_shadow_benchmark_id": bundle.experiment.benchmark_id,
            "factor_shadow_benchmark_refresh_start": start_date.isoformat(),
            "factor_shadow_benchmark_refresh_end": end_date.isoformat(),
            "factor_shadow_benchmark_prefetch_refreshed": str(stats.get("refreshed", 0)),
            "factor_shadow_benchmark_prefetch_stale": str(stats.get("stale_after_refresh", 0)),
        },
    )


def resolve_factor_shadow_outcomes(
    session_factory: sessionmaker[Session],
    *,
    provider_mode: str,
    as_of_date: date,
    horizons: tuple[int, ...] = FACTOR_SHADOW_HORIZONS,
    experiment_id: str | None = None,
    market_provider: object | None = None,
) -> FactorShadowOutcomeResolution:
    store = FactorResearchRepository(session_factory)
    if experiment_id is None:
        bundles = store.model_bundles(provider_mode)
        if len(bundles) > 1:
            resolutions = [
                resolve_factor_shadow_outcomes(
                    session_factory,
                    provider_mode=provider_mode,
                    as_of_date=as_of_date,
                    horizons=horizons,
                    experiment_id=bundle.experiment.experiment_id,
                    market_provider=market_provider,
                )
                for bundle in bundles
            ]
            return _merge_outcome_resolutions(resolutions, as_of_date=as_of_date)
        bundle = bundles[0] if bundles else None
    else:
        bundle = store.model_bundle(experiment_id)
    raw_runs = store.shadow_runs(bundle.experiment.experiment_id) if bundle is not None else []
    runs = _canonical_shadow_runs(raw_runs)
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
    raw_existing = store.shadow_outcomes(bundle.experiment.experiment_id)
    canonical_scan_job_ids = {run.scan_job_id for run in runs}
    existing = [item for item in raw_existing if item.scan_job_id in canonical_scan_job_ids]
    existing_keys = {
        (item.scan_job_id, item.instrument_id, item.horizon_sessions) for item in existing
    }
    cache = MarketDataCacheRepository(session_factory)
    inserted = 0
    matured_run_horizons = 0
    unresolved_prices = 0
    next_maturity_dates: list[date] = []
    work: list[tuple[FactorShadowRunRef, int, date, date, list[FactorShadowScore]]] = []
    requirements: set[ExactPriceRequirement] = set()

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
            work.append((run, horizon, entry_date, outcome_date, unresolved_scores))
            requirements.update(
                ExactPriceRequirement(instrument_id, entry_date, "adjusted_open")
                for instrument_id in [*instrument_ids, bundle.experiment.benchmark_id]
            )
            requirements.update(
                ExactPriceRequirement(instrument_id, outcome_date, "adjusted_close")
                for instrument_id in [*instrument_ids, bundle.experiment.benchmark_id]
            )

    repair = repair_exact_daily_prices(
        cache,
        provider_mode=provider_mode,
        market_provider=market_provider,
        requirements=requirements,
    )
    repair_health = repair.data_health("factor_shadow")
    for run, horizon, entry_date, outcome_date, unresolved_scores in work:
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

    retryable = repair.retryable
    if unresolved_prices:
        status = "partial" if retryable else "incomplete"
    elif inserted:
        status = "recorded"
    elif next_maturity_dates:
        status = "waiting_for_maturity"
    else:
        status = "up_to_date"
    return FactorShadowOutcomeResolution(
        status=status,
        as_of_date=as_of_date,
        experiment_id=bundle.experiment.experiment_id,
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
            "factor_shadow_outcome_raw_runs": str(len(raw_runs)),
            "factor_shadow_outcome_run_selection": FACTOR_SHADOW_RUN_SELECTION_RULE,
            "factor_shadow_outcome_inserted": str(inserted),
            "factor_shadow_outcome_existing": str(len(existing)),
            "factor_shadow_outcome_raw_existing": str(len(raw_existing)),
            "factor_shadow_outcome_unresolved_prices": str(unresolved_prices),
            **repair_health,
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
    experiment_id: str | None = None,
) -> FactorShadowEvaluation:
    store = FactorResearchRepository(session_factory)
    bundle = (
        store.model_bundle(experiment_id)
        if experiment_id is not None
        else store.latest_model_bundle(provider_mode)
    )
    raw_runs = store.shadow_runs(bundle.experiment.experiment_id) if bundle is not None else []
    runs = _canonical_shadow_runs(raw_runs)
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
    raw_outcomes = store.shadow_outcomes(bundle.experiment.experiment_id)
    canonical_scan_job_ids = {run.scan_job_id for run in runs}
    outcomes = [item for item in raw_outcomes if item.scan_job_id in canonical_scan_job_ids]
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
        promotion=_assess_shadow_promotion(evaluations),
        data_health={
            "factor_shadow_evaluation_status": status,
            "factor_shadow_evaluation_runs": str(len(runs)),
            "factor_shadow_evaluation_raw_runs": str(len(raw_runs)),
            "factor_shadow_evaluation_run_selection": FACTOR_SHADOW_RUN_SELECTION_RULE,
            "factor_shadow_evaluation_outcomes": str(len(outcomes)),
            "factor_shadow_evaluation_raw_outcomes": str(len(raw_outcomes)),
            "factor_shadow_outcome_contract": FACTOR_SHADOW_OUTCOME_CONTRACT,
            "factor_shadow_evaluation_paper_isolation": "true",
        },
    )


def build_factor_shadow_roster(
    session_factory: sessionmaker[Session],
    *,
    provider_mode: str,
    as_of_date: date,
    max_challengers: int = 3,
) -> FactorShadowRoster:
    """Summarize frozen challenger lanes without changing the paper strategy."""

    store = FactorResearchRepository(session_factory)
    bundles = store.model_bundles(provider_mode, limit=max_challengers)
    candidates = []
    for bundle in bundles:
        evaluation = build_factor_shadow_evaluation(
            session_factory,
            provider_mode=provider_mode,
            as_of_date=as_of_date,
            experiment_id=bundle.experiment.experiment_id,
        )
        promotion = evaluation.promotion
        candidates.append(
            FactorShadowCandidate(
                experiment_id=bundle.experiment.experiment_id,
                experiment_name=bundle.experiment.experiment_name,
                config_digest=bundle.experiment.config_digest,
                model_digest=bundle.aggregate_model_digest,
                status=(
                    "manual_review_required"
                    if promotion and promotion.eligible_for_manual_review
                    else evaluation.status
                ),
                eligible_for_manual_review=bool(promotion and promotion.eligible_for_manual_review),
                evaluation=evaluation,
            )
        )
    return FactorShadowRoster(
        status="ready" if candidates else "not_started",
        as_of_date=as_of_date,
        candidates=candidates,
        data_health={
            "factor_shadow_roster": "ready" if candidates else "not_started",
            "factor_shadow_roster_candidates": str(len(candidates)),
            "factor_shadow_roster_selection": "latest_per_frozen_configuration",
            "factor_shadow_roster_paper_isolation": "true",
            "factor_shadow_roster_order_effect": "none",
        },
    )


def _merge_outcome_resolutions(
    resolutions: list[FactorShadowOutcomeResolution],
    *,
    as_of_date: date,
) -> FactorShadowOutcomeResolution:
    if not resolutions:
        return FactorShadowOutcomeResolution(
            status="not_started",
            as_of_date=as_of_date,
            data_health={"factor_shadow_outcome_status": "not_started"},
        )
    statuses = {item.status for item in resolutions}
    status = (
        "partial"
        if "partial" in statuses
        else "recorded"
        if "recorded" in statuses
        else "incomplete"
        if "incomplete" in statuses
        else "waiting_for_maturity"
        if "waiting_for_maturity" in statuses
        else "up_to_date"
    )
    next_dates = [item.next_maturity_date for item in resolutions if item.next_maturity_date]
    merged_repair_health = _merge_candidate_repair_health(resolutions)
    return FactorShadowOutcomeResolution(
        status=status,
        as_of_date=as_of_date,
        runs=sum(item.runs for item in resolutions),
        matured_run_horizons=sum(item.matured_run_horizons for item in resolutions),
        outcomes_inserted=sum(item.outcomes_inserted for item in resolutions),
        outcomes_existing=sum(item.outcomes_existing for item in resolutions),
        unresolved_prices=sum(item.unresolved_prices for item in resolutions),
        next_maturity_date=min(next_dates, default=None),
        data_health={
            "factor_shadow_outcome_status": status,
            "factor_shadow_outcome_contract": FACTOR_SHADOW_OUTCOME_CONTRACT,
            "factor_shadow_outcome_candidates": str(len(resolutions)),
            **merged_repair_health,
            "factor_shadow_outcome_candidate_recorded": str(
                sum(item.status == "recorded" for item in resolutions)
            ),
            "factor_shadow_outcome_candidate_waiting_maturity": str(
                sum(item.status == "waiting_for_maturity" for item in resolutions)
            ),
            "factor_shadow_outcome_candidate_price_gaps": str(
                sum(item.status in {"partial", "incomplete"} for item in resolutions)
            ),
            "factor_shadow_outcome_candidate_statuses": ",".join(
                f"{item.experiment_id or index}:{item.status}"
                for index, item in enumerate(resolutions)
            ),
            "factor_shadow_outcome_candidate_repair_summary": json.dumps(
                [
                    {
                        "experiment_id": item.experiment_id or str(index),
                        "status": item.status,
                        "unresolved_prices": item.unresolved_prices,
                        "exact_unresolved": sum(
                            _nonnegative_health_int(
                                item.data_health.get(
                                    f"factor_shadow_exact_price_{suffix}", "0"
                                )
                            )
                            for suffix in ("suspended", "not_listed", "missing", "errors")
                        ),
                        "requested": item.data_health.get(
                            "factor_shadow_exact_price_requested", "0"
                        ),
                        "cache_hits": item.data_health.get(
                            "factor_shadow_exact_price_cache_hits", "0"
                        ),
                        "skipped_after_recheck": item.data_health.get(
                            "factor_shadow_exact_price_skipped_after_recheck", "0"
                        ),
                        "provider_requested": item.data_health.get(
                            "factor_shadow_exact_price_provider_requested", "0"
                        ),
                        "provider_batches": item.data_health.get(
                            "factor_shadow_exact_price_provider_batches", "0"
                        ),
                        "repaired": item.data_health.get("factor_shadow_exact_price_repaired", "0"),
                        "retryable": item.data_health.get(
                            "factor_shadow_exact_price_retryable", "0"
                        ),
                        "reason_mix": item.data_health.get(
                            "factor_shadow_exact_price_reason_mix", ""
                        ),
                    }
                    for index, item in enumerate(resolutions[:3])
                ],
                separators=(",", ":"),
                sort_keys=True,
            ),
            "factor_shadow_outcome_latest_candidate_status": resolutions[0].status,
            "factor_shadow_outcome_latest_candidate_waiting_maturity": str(
                resolutions[0].status == "waiting_for_maturity"
            ).lower(),
            "factor_shadow_outcome_older_candidate_price_gaps": str(
                sum(item.status in {"partial", "incomplete"} for item in resolutions[1:])
            ),
            "factor_shadow_outcome_paper_isolation": "true",
            "factor_shadow_outcome_order_effect": "none",
        },
    )


def _merge_candidate_repair_health(
    resolutions: list[FactorShadowOutcomeResolution],
) -> dict[str, str]:
    numeric_suffixes = (
        "requested",
        "cache_hits",
        "skipped_after_recheck",
        "provider_requested",
        "provider_batches",
        "repaired",
        "suspended",
        "not_listed",
        "missing",
        "errors",
        "retryable",
    )
    merged = {
        f"factor_shadow_exact_price_{suffix}": str(
            sum(
                _nonnegative_health_int(
                    item.data_health.get(f"factor_shadow_exact_price_{suffix}", "0")
                )
                for item in resolutions
            )
        )
        for suffix in numeric_suffixes
    }
    reasons: dict[str, int] = {}
    for item in resolutions:
        mix = item.data_health.get("factor_shadow_exact_price_reason_mix", "")
        for token in mix.split(","):
            reason, separator, raw_count = token.partition("=")
            if not separator or not reason:
                continue
            reasons[reason] = reasons.get(reason, 0) + _nonnegative_health_int(raw_count)
    merged["factor_shadow_exact_price_reason_mix"] = ",".join(
        f"{reason}={count}" for reason, count in sorted(reasons.items())
    )
    merged["factor_shadow_exact_price_aggregation"] = "sum_per_candidate_resolution"
    merged["factor_shadow_exact_price_unresolved"] = str(
        sum(
            _nonnegative_health_int(merged[f"factor_shadow_exact_price_{suffix}"])
            for suffix in ("suspended", "not_listed", "missing", "errors")
        )
    )
    merged["factor_shadow_outcome_unresolved_prices"] = str(
        sum(item.unresolved_prices for item in resolutions)
    )
    return merged


def _nonnegative_health_int(value: object) -> int:
    try:
        return max(int(str(value)), 0)
    except (TypeError, ValueError):
        return 0


def factor_shadow_outcome_dates(
    signal_date: date,
    horizon_sessions: int,
) -> tuple[date, date]:
    if horizon_sessions <= 0:
        raise ValueError("factor shadow horizon must be positive")
    entry_date = trading_day_offset(signal_date, FACTOR_SHADOW_ENTRY_WAIT_SESSIONS)
    outcome_date = trading_day_offset(entry_date, horizon_sessions - 1)
    return entry_date, outcome_date


def _canonical_shadow_runs(
    runs: Iterable[FactorShadowRunRef],
) -> list[FactorShadowRunRef]:
    """Select one preregistered run per independent signal date.

    The first successfully persisted run is evidence for that date. Later
    same-day scans cannot replace it, which prevents retries or repaired data
    from increasing the sample size or retrospectively selecting a better run.
    ``scan_job_id`` is the deterministic tie-breaker for equal timestamps.
    """

    selected: dict[date, FactorShadowRunRef] = {}
    for run in sorted(
        runs,
        key=lambda item: (item.signal_date, item.created_at, item.scan_job_id),
    ):
        selected.setdefault(run.signal_date, run)
    return [selected[signal_date] for signal_date in sorted(selected)]


def _evaluate_execution_head(
    matured: list[FactorShadowRunRef],
    scores_by_run: dict[str, list[FactorShadowScore]],
    outcomes_by_key: dict[tuple[str, str, int], FactorShadowOutcome],
    *,
    horizon_sessions: int,
) -> FactorShadowExecutionHeadEvaluation:
    baseline_session_returns: list[float] = []
    challenger_session_returns: list[float] = []
    challenger_lifts: list[float] = []
    challenger_lift_wins: list[bool] = []
    baseline_selected = 0
    challenger_selected = 0
    baseline_completed = 0
    challenger_completed = 0
    baseline_full = 0
    challenger_full = 0
    baseline_raw_positions: list[int] = []
    challenger_raw_positions: list[int] = []
    baseline_raw_concentrations: list[float] = []
    challenger_raw_concentrations: list[float] = []
    baseline_positions: list[int] = []
    challenger_positions: list[int] = []
    baseline_concentrations: list[float] = []
    challenger_concentrations: list[float] = []

    for run in matured:
        scores = scores_by_run.get(run.scan_job_id, [])
        baseline_ordered = _ordered_execution_scores(scores, rank_field="baseline_rank")
        challenger_ordered = _ordered_execution_scores(
            scores,
            rank_field="challenger_rank",
        )
        baseline_raw = baseline_ordered[:FACTOR_SHADOW_EXECUTION_HEAD_SIZE]
        challenger_raw = challenger_ordered[:FACTOR_SHADOW_EXECUTION_HEAD_SIZE]
        baseline_head = _constrained_execution_head_from_ordered(baseline_ordered)
        challenger_head = _constrained_execution_head_from_ordered(challenger_ordered)
        _append_industry_concentration(
            baseline_raw,
            positions=baseline_raw_positions,
            concentrations=baseline_raw_concentrations,
        )
        _append_industry_concentration(
            challenger_raw,
            positions=challenger_raw_positions,
            concentrations=challenger_raw_concentrations,
        )
        _append_industry_concentration(
            baseline_head,
            positions=baseline_positions,
            concentrations=baseline_concentrations,
        )
        _append_industry_concentration(
            challenger_head,
            positions=challenger_positions,
            concentrations=challenger_concentrations,
        )
        baseline_selected += len(baseline_head)
        challenger_selected += len(challenger_head)
        baseline_is_full = len(baseline_head) == FACTOR_SHADOW_EXECUTION_HEAD_SIZE
        challenger_is_full = len(challenger_head) == FACTOR_SHADOW_EXECUTION_HEAD_SIZE
        baseline_full += int(baseline_is_full)
        challenger_full += int(challenger_is_full)
        baseline_outcomes = [
            outcome
            for score in baseline_head
            if (
                outcome := outcomes_by_key.get(
                    (run.scan_job_id, score.instrument_id, horizon_sessions)
                )
            )
            is not None
        ]
        challenger_outcomes = [
            outcome
            for score in challenger_head
            if (
                outcome := outcomes_by_key.get(
                    (run.scan_job_id, score.instrument_id, horizon_sessions)
                )
            )
            is not None
        ]
        baseline_completed += len(baseline_outcomes)
        challenger_completed += len(challenger_outcomes)
        if not (
            baseline_is_full
            and challenger_is_full
            and len(baseline_outcomes) == FACTOR_SHADOW_EXECUTION_HEAD_SIZE
            and len(challenger_outcomes) == FACTOR_SHADOW_EXECUTION_HEAD_SIZE
        ):
            continue
        baseline_return = float(np.mean([item.net_excess_return_pct for item in baseline_outcomes]))
        challenger_return = float(
            np.mean([item.net_excess_return_pct for item in challenger_outcomes])
        )
        lift = challenger_return - baseline_return
        baseline_session_returns.append(baseline_return)
        challenger_session_returns.append(challenger_return)
        challenger_lifts.append(lift)
        challenger_lift_wins.append(lift > 0)

    paired_sessions = len(challenger_lifts)
    matured_sessions = len(matured)
    return FactorShadowExecutionHeadEvaluation(
        matured_sessions=matured_sessions,
        baseline_selection_slots=baseline_selected,
        challenger_selection_slots=challenger_selected,
        baseline_completed_outcomes=baseline_completed,
        challenger_completed_outcomes=challenger_completed,
        baseline_full_sessions=baseline_full,
        challenger_full_sessions=challenger_full,
        paired_outcome_sessions=paired_sessions,
        baseline_all_matured_sessions_filled=bool(
            matured_sessions and baseline_full == matured_sessions
        ),
        challenger_all_matured_sessions_filled=bool(
            matured_sessions and challenger_full == matured_sessions
        ),
        baseline_head_net_excess_return_pct=_rounded_mean(baseline_session_returns),
        challenger_head_net_excess_return_pct=_rounded_mean(challenger_session_returns),
        challenger_lift_win_rate=(
            round(sum(challenger_lift_wins) / paired_sessions, 6) if paired_sessions else None
        ),
        challenger_median_lift_pct=(
            round(float(np.median(challenger_lifts)), 10) if challenger_lifts else None
        ),
        baseline_raw_max_industry_positions=max(baseline_raw_positions, default=0),
        challenger_raw_max_industry_positions=max(challenger_raw_positions, default=0),
        baseline_raw_max_industry_concentration=(
            round(max(baseline_raw_concentrations), 6) if baseline_raw_concentrations else None
        ),
        challenger_raw_max_industry_concentration=(
            round(max(challenger_raw_concentrations), 6) if challenger_raw_concentrations else None
        ),
        baseline_max_industry_positions=max(baseline_positions, default=0),
        challenger_max_industry_positions=max(challenger_positions, default=0),
        baseline_max_industry_concentration=(
            round(max(baseline_concentrations), 6) if baseline_concentrations else None
        ),
        challenger_max_industry_concentration=(
            round(max(challenger_concentrations), 6) if challenger_concentrations else None
        ),
    )


def _raw_execution_head(
    scores: Iterable[FactorShadowScore],
    *,
    rank_field: str,
) -> list[FactorShadowScore]:
    return _ordered_execution_scores(scores, rank_field=rank_field)[
        :FACTOR_SHADOW_EXECUTION_HEAD_SIZE
    ]


def _constrained_execution_head(
    scores: Iterable[FactorShadowScore],
    *,
    rank_field: str,
) -> list[FactorShadowScore]:
    return _constrained_execution_head_from_ordered(
        _ordered_execution_scores(scores, rank_field=rank_field)
    )


def _ordered_execution_scores(
    scores: Iterable[FactorShadowScore],
    *,
    rank_field: str,
) -> list[FactorShadowScore]:
    return sorted(
        scores,
        key=lambda item: (getattr(item, rank_field), item.instrument_id),
    )


def _constrained_execution_head_from_ordered(
    scores: Iterable[FactorShadowScore],
) -> list[FactorShadowScore]:
    selected: list[FactorShadowScore] = []
    industry_counts: dict[str, int] = {}
    for score in scores:
        industry = _shadow_industry_bucket(score)
        if industry_counts.get(industry, 0) >= FACTOR_SHADOW_EXECUTION_HEAD_INDUSTRY_CAP:
            continue
        selected.append(score)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        if len(selected) == FACTOR_SHADOW_EXECUTION_HEAD_SIZE:
            break
    return selected


def _append_industry_concentration(
    scores: list[FactorShadowScore],
    *,
    positions: list[int],
    concentrations: list[float],
) -> None:
    if not scores:
        return
    counts: dict[str, int] = {}
    for score in scores:
        industry = _shadow_industry_bucket(score)
        counts[industry] = counts.get(industry, 0) + 1
    maximum = max(counts.values())
    positions.append(maximum)
    concentrations.append(maximum / len(scores))


def _shadow_industry_bucket(score: FactorShadowScore) -> str:
    industry = (score.industry or FACTOR_SHADOW_UNKNOWN_INDUSTRY_BUCKET).strip()
    return industry or FACTOR_SHADOW_UNKNOWN_INDUSTRY_BUCKET


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
    challenger_session_net_excess: list[float] = []
    challenger_session_outperformed: list[bool] = []
    challenger_rank_ic_wins: list[bool] = []
    challenger_addition_net_excess: list[float] = []
    challenger_removal_net_excess: list[float] = []
    challenger_selection_lifts: list[float] = []
    challenger_selection_lift_wins: list[bool] = []
    baseline_sets: list[set[str]] = []
    challenger_sets: list[set[str]] = []
    challenger_rank_outcomes: dict[str, list[FactorShadowOutcome]] = {
        f"q{bucket}": [] for bucket in range(1, 6)
    }
    challenger_industry_outcomes: dict[str, list[FactorShadowOutcome]] = {}

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
            if baseline_ic is not None and challenger_ic is not None:
                challenger_rank_ic_wins.append(challenger_ic > baseline_ic)
        for score, outcome in completed_pairs:
            bucket = min(5, int((score.challenger_rank - 1) * 5 / len(scores)) + 1)
            challenger_rank_outcomes[f"q{bucket}"].append(outcome)
            industry = (score.industry or "unknown").strip() or "unknown"
            challenger_industry_outcomes.setdefault(industry, []).append(outcome)

        top_count = max(1, math.ceil(len(scores) * max(min(top_fraction, 1.0), 0.0)))
        baseline_top = sorted(scores, key=lambda item: item.baseline_rank)[:top_count]
        challenger_top = sorted(scores, key=lambda item: item.challenger_rank)[:top_count]
        baseline_ids = {item.instrument_id for item in baseline_top}
        challenger_ids = {item.instrument_id for item in challenger_top}
        baseline_sets.append(baseline_ids)
        challenger_sets.append(challenger_ids)
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
        if baseline_top_outcomes and challenger_top_outcomes:
            baseline_session_net = float(
                np.mean([item.net_excess_return_pct for item in baseline_top_outcomes])
            )
            challenger_session_net = float(
                np.mean([item.net_excess_return_pct for item in challenger_top_outcomes])
            )
            challenger_session_net_excess.append(challenger_session_net)
            challenger_session_outperformed.append(challenger_session_net > baseline_session_net)
        addition_outcomes = [
            outcome
            for score in challenger_top
            if score.instrument_id not in baseline_ids
            if (
                outcome := outcomes_by_key.get(
                    (run.scan_job_id, score.instrument_id, horizon_sessions)
                )
            )
            is not None
        ]
        removal_outcomes = [
            outcome
            for score in baseline_top
            if score.instrument_id not in challenger_ids
            if (
                outcome := outcomes_by_key.get(
                    (run.scan_job_id, score.instrument_id, horizon_sessions)
                )
            )
            is not None
        ]
        challenger_addition_net_excess.extend(
            outcome.net_excess_return_pct for outcome in addition_outcomes
        )
        challenger_removal_net_excess.extend(
            outcome.net_excess_return_pct for outcome in removal_outcomes
        )
        if addition_outcomes and removal_outcomes:
            selection_lift = float(
                np.mean([outcome.net_excess_return_pct for outcome in addition_outcomes])
                - np.mean([outcome.net_excess_return_pct for outcome in removal_outcomes])
            )
            challenger_selection_lifts.append(selection_lift)
            challenger_selection_lift_wins.append(selection_lift > 0)
        industries = [item.industry for item in challenger_top if item.industry]
        if industries:
            counts = pd.Series(industries).value_counts()
            challenger_industry_concentrations.append(float(counts.max() / len(industries)))

    coverage = completed / expected if expected else 0.0
    status = "pending" if not matured else "ready" if completed == expected else "partial"
    execution_head = _evaluate_execution_head(
        matured,
        scores_by_run,
        outcomes_by_key,
        horizon_sessions=horizon_sessions,
    )
    return FactorShadowHorizonEvaluation(
        horizon_sessions=horizon_sessions,
        status=status,
        matured_runs=len(matured),
        expected_instruments=expected,
        completed_instruments=completed,
        outcome_coverage=round(coverage, 6),
        signal_date_scored_sessions=sum(
            bool(scores_by_run.get(run.scan_job_id)) for run in matured
        ),
        signal_date_scored_coverage=round(
            sum(bool(scores_by_run.get(run.scan_job_id)) for run in matured) / len(matured),
            6,
        )
        if matured
        else 0.0,
        scored_cohort_instruments=expected,
        scored_cohort_outcome_coverage=round(coverage, 6),
        eligible_universe_instruments=None,
        universe_coverage="unknown",
        selection_filled_instruments=sum(
            len(scores_by_run.get(run.scan_job_id, [])) for run in matured
        ),
        outcome_filled_instruments=completed,
        paired_outcome_sessions=execution_head.paired_outcome_sessions,
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
        challenger_session_count=len(challenger_session_net_excess),
        challenger_session_outperformance_rate=(
            round(
                sum(challenger_session_outperformed) / len(challenger_session_outperformed),
                6,
            )
            if challenger_session_outperformed
            else None
        ),
        challenger_rank_ic_win_rate=(
            round(sum(challenger_rank_ic_wins) / len(challenger_rank_ic_wins), 6)
            if challenger_rank_ic_wins
            else None
        ),
        challenger_median_session_net_excess_return_pct=(
            round(float(np.median(challenger_session_net_excess)), 10)
            if challenger_session_net_excess
            else None
        ),
        challenger_addition_count=len(challenger_addition_net_excess),
        challenger_removal_count=len(challenger_removal_net_excess),
        challenger_addition_net_excess_return_pct=_rounded_mean(challenger_addition_net_excess),
        challenger_removal_net_excess_return_pct=_rounded_mean(challenger_removal_net_excess),
        challenger_selection_lift_session_count=len(challenger_selection_lifts),
        challenger_selection_lift_win_rate=(
            round(
                sum(challenger_selection_lift_wins) / len(challenger_selection_lift_wins),
                6,
            )
            if challenger_selection_lift_wins
            else None
        ),
        challenger_median_selection_lift_pct=(
            round(float(np.median(challenger_selection_lifts)), 10)
            if challenger_selection_lifts
            else None
        ),
        challenger_rank_buckets=_shadow_attribution_groups(
            challenger_rank_outcomes,
            labels={
                "q1": "Q1（最高分）",
                "q2": "Q2",
                "q3": "Q3",
                "q4": "Q4",
                "q5": "Q5（最低分）",
            },
            order=[f"q{bucket}" for bucket in range(1, 6)],
        ),
        challenger_industries=_shadow_attribution_groups(
            challenger_industry_outcomes,
            labels={},
            limit=8,
        ),
        execution_head=execution_head,
    )


def _assess_shadow_promotion(
    evaluations: list[FactorShadowHorizonEvaluation],
) -> FactorShadowPromotionAssessment:
    """Require broad, repeated evidence before a challenger can be reviewed.

    The explicit result closes a gap between raw shadow aggregates and a
    decision-ready research record. It is intentionally non-binding: a later
    human-reviewed experiment remains necessary before any model activation.
    """

    by_horizon = {item.horizon_sessions: item for item in evaluations}
    reasons: list[str] = []
    for horizon in FACTOR_SHADOW_HORIZONS:
        evaluation = by_horizon.get(horizon)
        prefix = f"{horizon}d"
        if evaluation is None or evaluation.status == "pending":
            reasons.append(f"{prefix}_outcomes_not_matured")
            reasons.append(f"{prefix}_execution_head_outcomes_not_matured")
            continue
        if evaluation.matured_runs < FACTOR_SHADOW_PROMOTION_MIN_MATURED_RUNS:
            reasons.append(f"{prefix}_matured_runs_below_minimum")
        if evaluation.outcome_coverage < FACTOR_SHADOW_PROMOTION_MIN_OUTCOME_COVERAGE:
            reasons.append(f"{prefix}_outcome_coverage_below_minimum")
        if evaluation.challenger_session_outperformance_rate is None:
            reasons.append(f"{prefix}_session_comparison_missing")
        elif (
            evaluation.challenger_session_outperformance_rate
            < FACTOR_SHADOW_PROMOTION_MIN_SESSION_EDGE_RATE
        ):
            reasons.append(f"{prefix}_session_edge_not_stable")
        if evaluation.challenger_median_session_net_excess_return_pct is None:
            reasons.append(f"{prefix}_session_return_missing")
        elif evaluation.challenger_median_session_net_excess_return_pct <= 0:
            reasons.append(f"{prefix}_median_session_net_excess_not_positive")
        if (
            evaluation.mean_challenger_rank_ic is None
            or evaluation.mean_baseline_rank_ic is None
            or evaluation.mean_challenger_rank_ic <= evaluation.mean_baseline_rank_ic
        ):
            reasons.append(f"{prefix}_rank_ic_not_above_baseline")
        if (
            evaluation.challenger_selection_lift_win_rate is None
            or evaluation.challenger_median_selection_lift_pct is None
        ):
            reasons.append(f"{prefix}_selection_lift_missing")
        else:
            if (
                evaluation.challenger_selection_lift_win_rate
                < FACTOR_SHADOW_PROMOTION_MIN_SELECTION_LIFT_RATE
            ):
                reasons.append(f"{prefix}_selection_lift_not_stable")
            if evaluation.challenger_median_selection_lift_pct <= 0:
                reasons.append(f"{prefix}_selection_lift_not_positive")

        head = evaluation.execution_head
        if head.paired_outcome_sessions < FACTOR_SHADOW_PROMOTION_MIN_MATURED_RUNS:
            reasons.append(f"{prefix}_execution_head_samples_below_minimum")
        if not (
            head.baseline_all_matured_sessions_filled
            and head.challenger_all_matured_sessions_filled
        ):
            reasons.append(f"{prefix}_execution_head_not_filled")
        if (
            head.baseline_max_industry_positions > FACTOR_SHADOW_EXECUTION_HEAD_INDUSTRY_CAP
            or head.challenger_max_industry_positions > FACTOR_SHADOW_EXECUTION_HEAD_INDUSTRY_CAP
        ):
            reasons.append(f"{prefix}_execution_head_industry_cap_violated")
        if head.challenger_lift_win_rate is None or head.challenger_median_lift_pct is None:
            reasons.append(f"{prefix}_execution_head_lift_missing")
        else:
            if head.challenger_lift_win_rate < FACTOR_SHADOW_PROMOTION_MIN_SELECTION_LIFT_RATE:
                reasons.append(f"{prefix}_execution_head_lift_not_stable")
            if head.challenger_median_lift_pct <= 0:
                reasons.append(f"{prefix}_execution_head_lift_not_positive")

    if reasons:
        return FactorShadowPromotionAssessment(
            status="collecting",
            action="keep_shadow_only",
            reasons=reasons,
        )
    return FactorShadowPromotionAssessment(
        status="eligible_for_manual_review",
        action="manual_review_required",
        eligible_for_manual_review=True,
        reasons=["all_preregistered_shadow_evidence_checks_passed"],
    )


def _shadow_attribution_groups(
    outcomes_by_group: dict[str, list[FactorShadowOutcome]],
    *,
    labels: dict[str, str],
    order: list[str] | None = None,
    limit: int | None = None,
) -> list[FactorShadowAttributionGroup]:
    keys = order or sorted(
        outcomes_by_group,
        key=lambda key: (-len(outcomes_by_group[key]), key),
    )
    if limit is not None:
        keys = keys[:limit]
    groups: list[FactorShadowAttributionGroup] = []
    for key in keys:
        outcomes = outcomes_by_group.get(key, [])
        excess = [item.excess_return_pct for item in outcomes]
        net_excess = [item.net_excess_return_pct for item in outcomes]
        groups.append(
            FactorShadowAttributionGroup(
                key=key,
                label=labels.get(key, key),
                sample_count=len(outcomes),
                average_excess_return_pct=_rounded_mean(excess),
                average_net_excess_return_pct=_rounded_mean(net_excess),
                positive_net_excess_rate=(
                    round(sum(value > 0 for value in net_excess) / len(net_excess), 6)
                    if net_excess
                    else None
                ),
            )
        )
    return groups


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
    if len(scores) != len(returns) or len(scores) < 2:
        return None
    # Pandas delegates method="spearman" to SciPy. Ranking first preserves
    # Spearman's definition while keeping this research path runnable with the
    # project's declared NumPy/Pandas dependencies only.
    value = (
        pd.Series(scores, dtype="float64")
        .rank(method="average")
        .corr(
            pd.Series(returns, dtype="float64").rank(method="average"),
            method="pearson",
        )
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
