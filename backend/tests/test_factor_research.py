from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from hashlib import sha256

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from qagent.db import create_session_factory, initialize_database
from qagent.factors.models import FactorExposure, FactorRanking
from qagent.factors.research_contract import FACTOR_RESEARCH_VERSION
from qagent.research.factor_experiments import (
    FEATURE_COLUMNS,
    FactorResearchConfig,
    _attach_excess_labels,
    _prepare_instrument_rows,
    compare_baseline_and_lightgbm,
    factor_research_feature_contract_digest,
    neutralize_research_features,
)
from qagent.research.factor_shadow import score_factor_shadow_run, score_factor_shadow_runs
from qagent.research.factor_shadow_outcomes import (
    FactorShadowExecutionHeadEvaluation,
    FactorShadowHorizonEvaluation,
    FactorShadowOutcomeResolution,
    _assess_shadow_promotion,
    _constrained_execution_head,
    _evaluate_execution_head,
    _raw_execution_head,
    _merge_outcome_resolutions,
    build_factor_shadow_roster,
    build_factor_shadow_evaluation,
    factor_shadow_outcome_dates,
    refresh_factor_shadow_benchmark_cache,
    resolve_factor_shadow_outcomes,
)
from qagent.storage.factor_research import (
    FactorResearchRepository,
    FactorShadowOutcome,
    FactorShadowRunRef,
    FactorShadowScore,
)
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.replay_evidence import ReplayFactorBarReadRow


def test_factor_shadow_resolution_keeps_latest_maturity_separate_from_old_price_gaps():
    as_of = date(2026, 8, 28)
    merged = _merge_outcome_resolutions(
        [
            FactorShadowOutcomeResolution(
                status="waiting_for_maturity",
                as_of_date=as_of,
                experiment_id="latest",
                next_maturity_date=date(2026, 9, 1),
            ),
            FactorShadowOutcomeResolution(
                status="partial",
                as_of_date=as_of,
                experiment_id="old",
                unresolved_prices=2,
            ),
        ],
        as_of_date=as_of,
    )

    assert merged.status == "partial"
    assert merged.data_health["factor_shadow_outcome_latest_candidate_status"] == (
        "waiting_for_maturity"
    )
    assert merged.data_health["factor_shadow_outcome_latest_candidate_waiting_maturity"] == "true"
    assert merged.data_health["factor_shadow_outcome_older_candidate_price_gaps"] == "1"
    assert merged.data_health["factor_shadow_outcome_candidate_statuses"] == (
        "latest:waiting_for_maturity,old:partial"
    )
    repair_summary = merged.data_health["factor_shadow_outcome_candidate_repair_summary"]
    assert '"experiment_id":"latest"' in repair_summary
    assert '"experiment_id":"old"' in repair_summary
    assert '"unresolved_prices":2' in repair_summary


def test_factor_research_recorder_persists_terminal_result(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'factor-research.db'}"
    initialize_database(database_url)
    store = FactorResearchRepository(create_session_factory(database_url))
    experiment = store.create(
        experiment_name="fixture experiment",
        provider_mode="fixture",
        model_family="baseline+lightgbm",
        benchmark_id="CN:000300.IDX",
        dataset_revision=7,
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        code_revision="a" * 40,
        config={"horizon_sessions": 20},
    )

    running = store.mark_running(experiment.experiment_id)
    completed = store.complete(
        experiment.experiment_id,
        metrics={"activation_allowed": False},
        data_health={"calendar": "XSHG"},
        artifacts={"paper_model_unchanged": True},
    )

    assert running.status == "running"
    assert completed.status == "succeeded"
    assert completed.metrics == {"activation_allowed": False}
    assert store.list()[0].experiment_id == experiment.experiment_id
    with pytest.raises(ValueError, match="not running"):
        store.complete(
            experiment.experiment_id,
            metrics={},
            data_health={},
            artifacts={},
        )


def test_excess_label_prefers_sufficient_point_in_time_industry_group():
    rows = []
    signal_date = date(2025, 1, 2)
    for index in range(6):
        rows.append(
            {
                "signal_date": signal_date,
                "instrument_id": f"CN:{index:06d}",
                "industry": "银行",
                "raw_forward_return_pct": float(index),
                "benchmark_return_pct": 1.0,
            }
        )
    rows.append(
        {
            "signal_date": signal_date,
            "instrument_id": "CN:999999",
            "industry": "稀疏行业",
            "raw_forward_return_pct": 3.0,
            "benchmark_return_pct": 1.0,
        }
    )

    labeled = _attach_excess_labels(pd.DataFrame(rows))

    bank = labeled[labeled["industry"] == "银行"]
    sparse = labeled[labeled["industry"] == "稀疏行业"].iloc[0]
    assert set(bank["label_scope"]) == {"industry_excess"}
    assert bank["target_excess_return_pct"].median() == pytest.approx(0.0)
    assert sparse["label_scope"] == "benchmark_excess"
    assert sparse["target_excess_return_pct"] == pytest.approx(2.0)


def test_neutralization_removes_cross_sectional_size_loading():
    rng = np.random.default_rng(42)
    rows = []
    signal_date = date(2025, 1, 2)
    for index in range(120):
        size = 18 + index / 25
        row = {
            "signal_date": signal_date,
            "instrument_id": f"CN:{index:06d}",
            "industry": "行业A" if index % 2 else "行业B",
            "log_market_cap": size,
        }
        for feature in FEATURE_COLUMNS:
            row[feature] = size * 2 + rng.normal(0, 0.2)
        rows.append(row)

    normalized = neutralize_research_features(pd.DataFrame(rows))

    correlation = normalized["momentum_20"].corr(normalized["log_market_cap"])
    assert abs(correlation) < 1e-8
    assert normalized["momentum_20"].std() == pytest.approx(1.0, rel=0.02)


def test_neutralization_keeps_all_missing_feature_columns_numeric():
    rows = []
    for index in range(10):
        row = {
            "signal_date": date(2025, 1, 2),
            "instrument_id": f"CN:{index:06d}",
            "industry": "行业A",
            "log_market_cap": 18 + index / 10,
        }
        for feature in FEATURE_COLUMNS:
            row[feature] = float(index) if feature == "momentum_20" else None
        rows.append(row)

    normalized = neutralize_research_features(pd.DataFrame(rows))

    assert all(str(dtype) == "float64" for dtype in normalized[list(FEATURE_COLUMNS)].dtypes)
    assert normalized["return_on_equity"].isna().all()


def test_replay_factor_rows_use_volume_price_turnover_proxy():
    rows = [
        ReplayFactorBarReadRow(
            instrument_id="CN:000001",
            trade_date=date(2024, 1, 1) + timedelta(days=index),
            raw_close=Decimal("10"),
            adjusted_open=Decimal("10"),
            adjusted_high=Decimal("10"),
            adjusted_low=Decimal("10"),
            adjusted_close=Decimal("10"),
            volume=Decimal("100"),
            adjustment_mode="forward",
        )
        for index in range(61)
    ]

    prepared = _prepare_instrument_rows(rows)

    assert prepared is not None
    assert prepared[3][-1] == pytest.approx(1_000)


def test_lightgbm_challenger_uses_purged_time_split():
    pytest.importorskip("lightgbm")
    rng = np.random.default_rng(7)
    rows = []
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for date_index in range(30):
        signal_date = (start + timedelta(days=date_index * 10)).date()
        for instrument_index in range(60):
            row = {
                "signal_date": signal_date,
                "instrument_id": f"CN:{instrument_index:06d}",
                "industry": f"industry-{instrument_index % 6}",
                "log_market_cap": rng.normal(22, 1),
            }
            for feature in FEATURE_COLUMNS:
                row[feature] = rng.normal()
            row["target_excess_return_pct"] = (
                1.5 * row["momentum_20"] - 0.8 * row["volatility_20"] ** 2 + rng.normal(0, 0.3)
            )
            rows.append(row)
    frame = pd.DataFrame(rows)
    config = FactorResearchConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        dataset_revision=7,
        seeds=[7],
    )

    metrics, artifacts, model_artifacts = compare_baseline_and_lightgbm(frame, config)

    assert metrics["activation_allowed"] is False
    assert metrics["lightgbm_challenger"]["cross_sections"] >= 2
    assert artifacts["split"]["purge_cross_sections"] == 2
    assert artifacts["paper_model_unchanged"] is True
    assert artifacts["feature_importance"]
    assert artifacts["shadow_model_persisted"] is True
    assert len(model_artifacts) == 1
    assert model_artifacts[0]["model_digest"]
    assert "tree" in model_artifacts[0]["model_text"]


def test_regularized_recipe_is_frozen_in_research_artifacts():
    pytest.importorskip("lightgbm")
    rng = np.random.default_rng(19)
    rows = []
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for date_index in range(30):
        signal_date = (start + timedelta(days=date_index * 10)).date()
        for instrument_index in range(60):
            row = {
                "signal_date": signal_date,
                "instrument_id": f"CN:{instrument_index:06d}",
                "industry": f"industry-{instrument_index % 6}",
                "log_market_cap": rng.normal(22, 1),
            }
            for feature in FEATURE_COLUMNS:
                row[feature] = rng.normal()
            row["target_excess_return_pct"] = (
                row["momentum_20"] - 0.5 * row["volatility_20"] ** 2 + rng.normal(0, 0.3)
            )
            rows.append(row)

    _, artifacts, model_artifacts = compare_baseline_and_lightgbm(
        pd.DataFrame(rows),
        FactorResearchConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2025, 1, 1),
            dataset_revision=7,
            seeds=[7],
            model_recipe="regularized_v1",
        ),
    )

    assert artifacts["model_recipe"] == "regularized_v1"
    assert artifacts["recipe_label"] == "Regularized LightGBM"
    assert artifacts["recipe_parameters"]["num_leaves"] == 15
    assert artifacts["paper_model_unchanged"] is True
    assert model_artifacts[0]["model_digest"]


def test_lightgbm_shadow_scores_are_persisted_without_paper_activation(tmp_path):
    lightgbm = pytest.importorskip("lightgbm")
    database_url = f"sqlite:///{tmp_path / 'factor-shadow.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    store = FactorResearchRepository(session_factory)
    rng = np.random.default_rng(17)
    training = pd.DataFrame(
        rng.normal(size=(120, len(FEATURE_COLUMNS))),
        columns=list(FEATURE_COLUMNS),
    )
    label = 1.2 * training["momentum_20"] - 0.7 * training["volatility_20"]
    model = lightgbm.train(
        {
            "objective": "regression_l1",
            "verbosity": -1,
            "num_threads": 1,
            "seed": 17,
        },
        lightgbm.Dataset(training, label=label),
        num_boost_round=12,
    )
    model_text = model.model_to_string()
    experiment = store.create(
        experiment_name="shadow fixture",
        provider_mode="fixture",
        model_family="baseline+lightgbm",
        benchmark_id="CN:000300.IDX",
        dataset_revision=0,
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        code_revision="b" * 40,
        config={"paper_model_unchanged": True},
    )
    store.mark_running(experiment.experiment_id)
    store.complete(
        experiment.experiment_id,
        metrics={"activation_allowed": False},
        data_health={},
        artifacts={"paper_model_unchanged": True},
        model_artifacts=[
            {
                "seed": 17,
                "feature_set_version": FACTOR_RESEARCH_VERSION,
                "feature_contract_digest": factor_research_feature_contract_digest(),
                "model_digest": sha256(model_text.encode("utf-8")).hexdigest(),
                "model_text": model_text,
            }
        ],
    )
    rankings = [
        FactorRanking(
            instrument_id=f"CN:{index:06d}",
            factor_score=0.5,
            factor_rank=index + 1,
            percentile=0.5,
            momentum_score=0.5,
            trend_quality_score=0.5,
            liquidity_score=0.5,
            low_risk_score=0.5,
            reversal_score=0.5,
            execution_penalty=0.0,
            data_completeness=1.0,
            factor_exposures=[
                FactorExposure(
                    factor_id="size",
                    label="size",
                    raw_value=float(10_000_000_000 + index * 1_000_000),
                    score=0.5,
                    weight=0.0,
                    explanation="fixture",
                )
            ],
            research_features={feature: float(rng.normal()) for feature in FEATURE_COLUMNS},
        )
        for index in range(25)
    ]

    result = score_factor_shadow_run(
        session_factory,
        provider_mode="fixture",
        scan_job_id="scan-fixture-1",
        signal_date=date(2026, 8, 10),
        rankings=rankings,
        stock_ids={item.instrument_id for item in rankings},
    )
    retried = score_factor_shadow_run(
        session_factory,
        provider_mode="fixture",
        scan_job_id="scan-fixture-1",
        signal_date=date(2026, 8, 10),
        rankings=rankings,
        stock_ids={item.instrument_id for item in rankings},
    )

    assert result.status == "recorded"
    assert result.run is not None
    assert result.run.scored_instruments == 25
    assert len(result.run.top_scores) == 20
    assert result.data_health["factor_shadow_paper_isolation"] == "true"
    assert retried.run == result.run
    assert store.latest_shadow_run("fixture") == result.run
    with pytest.raises(ValueError, match="retry identity"):
        store.record_shadow_scores(
            experiment_id=experiment.experiment_id,
            scan_job_id="scan-fixture-1",
            signal_date=date(2026, 8, 11),
            dataset_revision=0,
            model_digest=result.run.model_digest,
            scores=result.run.top_scores,
        )

    second = store.create(
        experiment_name="alternate shadow fixture",
        provider_mode="fixture",
        model_family="baseline+lightgbm",
        benchmark_id="CN:000300.IDX",
        dataset_revision=0,
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        code_revision="d" * 40,
        config={"paper_model_unchanged": True, "top_fraction": 0.2},
    )
    store.mark_running(second.experiment_id)
    store.complete(
        second.experiment_id,
        metrics={"activation_allowed": False},
        data_health={},
        artifacts={"paper_model_unchanged": True},
        model_artifacts=[
            {
                "seed": 17,
                "feature_set_version": FACTOR_RESEARCH_VERSION,
                "feature_contract_digest": factor_research_feature_contract_digest(),
                "model_digest": sha256(model_text.encode("utf-8")).hexdigest(),
                "model_text": model_text,
            }
        ],
    )
    multi = score_factor_shadow_runs(
        session_factory,
        provider_mode="fixture",
        scan_job_id="scan-fixture-2",
        signal_date=date(2026, 8, 11),
        rankings=rankings,
        stock_ids={item.instrument_id for item in rankings},
    )
    roster = build_factor_shadow_roster(
        session_factory,
        provider_mode="fixture",
        as_of_date=date(2026, 8, 11),
    )
    assert len(store.model_bundles("fixture")) == 2
    assert multi.status == "recorded"
    assert len(multi.runs) == 2
    assert multi.data_health["factor_shadow_candidate_count"] == "2"
    assert len(roster.candidates) == 2
    assert roster.data_health["factor_shadow_roster_paper_isolation"] == "true"


def test_factor_shadow_outcomes_use_one_run_per_signal_date_and_are_immutable(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'factor-shadow-outcomes.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    store = FactorResearchRepository(session_factory)
    model_text = "fixture frozen factor model"
    model_digest = sha256(model_text.encode("utf-8")).hexdigest()
    experiment = store.create(
        experiment_name="outcome fixture",
        provider_mode="fixture",
        model_family="baseline+lightgbm",
        benchmark_id="CN:000300.IDX",
        dataset_revision=7,
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        code_revision="c" * 40,
        config={"top_fraction": 0.2, "round_trip_cost_bps": 10},
    )
    store.mark_running(experiment.experiment_id)
    store.complete(
        experiment.experiment_id,
        metrics={"activation_allowed": False},
        data_health={},
        artifacts={"paper_model_unchanged": True},
        model_artifacts=[
            {
                "seed": 7,
                "feature_set_version": FACTOR_RESEARCH_VERSION,
                "feature_contract_digest": factor_research_feature_contract_digest(),
                "model_digest": model_digest,
                "model_text": model_text,
            }
        ],
    )
    bundle = store.latest_model_bundle("fixture")
    assert bundle is not None
    signal_date = date(2026, 7, 1)
    scores = [
        FactorShadowScore(
            instrument_id=f"CN:{index:06d}",
            baseline_score=float(10 - index),
            challenger_score=float(index),
            baseline_rank=index + 1,
            challenger_rank=10 - index,
            feature_coverage=1.0,
            industry=f"industry-{index % 2}",
        )
        for index in range(10)
    ]
    store.record_shadow_scores(
        experiment_id=experiment.experiment_id,
        scan_job_id="scan-outcome-1",
        signal_date=signal_date,
        dataset_revision=7,
        model_digest=bundle.aggregate_model_digest,
        scores=scores,
    )
    duplicate_scores = [
        score.model_copy(
            update={
                "challenger_score": score.baseline_score,
                "challenger_rank": score.baseline_rank,
            }
        )
        for score in scores
    ]
    store.record_shadow_scores(
        experiment_id=experiment.experiment_id,
        scan_job_id="scan-outcome-2",
        signal_date=signal_date,
        dataset_revision=8,
        model_digest=bundle.aggregate_model_digest,
        scores=duplicate_scores,
    )
    entry_date, outcome_date = factor_shadow_outcome_dates(signal_date, 5)
    bars = []
    for index, score in enumerate(scores):
        bars.extend(
            _factor_shadow_price_rows(
                score.instrument_id,
                entry_date,
                outcome_date,
                outcome_close=Decimal(101 + index),
            )
        )
    benchmark_bars = _factor_shadow_price_rows(
        "CN:000300.IDX",
        entry_date,
        outcome_date,
        outcome_close=Decimal("101"),
    )
    missing_instrument = scores[-1].instrument_id
    missing_outcome_rows = [
        row
        for row in bars
        if row["instrument_id"] == missing_instrument and row["trade_date"] == outcome_date
    ]
    cached_bars = [row for row in bars if row not in missing_outcome_rows]
    cache = MarketDataCacheRepository(session_factory)
    cache.save_daily_bars(
        "fixture",
        pd.DataFrame(cached_bars),
    )

    class BenchmarkPrefetchProvider:
        def __init__(self):
            self.calls: list[tuple[list[str], date, date]] = []

        def prefetch_daily_bars(self, instrument_ids, start, end):
            self.calls.append((instrument_ids, start, end))
            cache.save_daily_bars("fixture", pd.DataFrame(benchmark_bars))

        def prefetch_stats(self):
            return {"refreshed": 1, "stale_after_refresh": 0}

    benchmark_provider = BenchmarkPrefetchProvider()

    class ExactRepairProvider:
        def __init__(self):
            self.calls = []
            self.last_errors = []

        def get_historical_daily_bars(self, instrument_ids, start, end):
            self.calls.append((instrument_ids, start, end))
            return pd.DataFrame(
                row
                for row in missing_outcome_rows
                if row["instrument_id"] in instrument_ids and row["trade_date"] == start
            )

    exact_repair_provider = ExactRepairProvider()

    pending = resolve_factor_shadow_outcomes(
        session_factory,
        provider_mode="fixture",
        as_of_date=entry_date,
        horizons=(5,),
    )
    refresh = refresh_factor_shadow_benchmark_cache(
        session_factory,
        provider_mode="fixture",
        market_provider=benchmark_provider,
        as_of_date=outcome_date,
        horizons=(5,),
    )
    resolved = resolve_factor_shadow_outcomes(
        session_factory,
        provider_mode="fixture",
        as_of_date=outcome_date,
        horizons=(5,),
        market_provider=exact_repair_provider,
    )
    single_run_evaluation = build_factor_shadow_evaluation(
        session_factory,
        provider_mode="fixture",
        as_of_date=outcome_date,
        horizons=(5,),
    )
    canonical_outcomes = store.shadow_outcomes(
        experiment.experiment_id,
        scan_job_id="scan-outcome-1",
    )
    assert len(canonical_outcomes) == 10
    assert (
        store.shadow_outcomes(
            experiment.experiment_id,
            scan_job_id="scan-outcome-2",
        )
        == []
    )
    store.record_shadow_outcomes(
        [
            outcome.model_copy(
                update={
                    "scan_job_id": "scan-outcome-2",
                    "signal_dataset_revision": 8,
                    "source_digest": sha256(
                        f"duplicate|{outcome.source_digest}".encode("utf-8")
                    ).hexdigest(),
                    "created_at": None,
                }
            )
            for outcome in canonical_outcomes
        ]
    )
    evaluation = build_factor_shadow_evaluation(
        session_factory,
        provider_mode="fixture",
        as_of_date=outcome_date,
        horizons=(5,),
    )
    retried = resolve_factor_shadow_outcomes(
        session_factory,
        provider_mode="fixture",
        as_of_date=outcome_date,
        horizons=(5,),
    )

    assert pending.status == "waiting_for_maturity"
    assert pending.runs == 1
    assert pending.outcomes_inserted == 0
    assert pending.next_maturity_date == outcome_date
    assert refresh.status == "refreshed"
    assert benchmark_provider.calls == [(["CN:000300.IDX"], entry_date, outcome_date)]
    assert refresh.data_health["factor_shadow_benchmark_prefetch_refreshed"] == "1"
    assert resolved.status == "recorded"
    assert resolved.runs == 1
    assert resolved.outcomes_inserted == 10
    assert resolved.unresolved_prices == 0
    assert exact_repair_provider.calls == [([missing_instrument], outcome_date, outcome_date)]
    assert resolved.data_health["factor_shadow_outcome_raw_runs"] == "2"
    assert (
        resolved.data_health["factor_shadow_outcome_run_selection"]
        == "earliest_created_at_then_scan_job_id_per_signal_date"
    )
    assert retried.status == "up_to_date"
    assert retried.outcomes_inserted == 0
    assert retried.outcomes_existing == 10
    assert retried.data_health["factor_shadow_outcome_raw_existing"] == "20"
    assert evaluation.status == "ready"
    assert evaluation.run_count == 1
    assert evaluation.signal_dates == [signal_date]
    assert evaluation.data_health["factor_shadow_evaluation_raw_runs"] == "2"
    assert evaluation.data_health["factor_shadow_evaluation_outcomes"] == "10"
    assert evaluation.data_health["factor_shadow_evaluation_raw_outcomes"] == "20"
    assert evaluation.horizons == single_run_evaluation.horizons
    assert evaluation.promotion == single_run_evaluation.promotion
    assert evaluation.promotion is not None
    assert evaluation.promotion.status == "collecting"
    assert evaluation.promotion.action == "keep_shadow_only"
    assert "5d_matured_runs_below_minimum" in evaluation.promotion.reasons
    horizon = evaluation.horizons[0]
    assert horizon.status == "ready"
    assert horizon.outcome_coverage == 1.0
    assert horizon.signal_date_scored_sessions == 1
    assert horizon.signal_date_scored_coverage == 1.0
    assert horizon.scored_cohort_instruments == 10
    assert horizon.scored_cohort_outcome_coverage == 1.0
    assert horizon.eligible_universe_instruments is None
    assert horizon.universe_coverage == "unknown"
    assert horizon.selection_filled_instruments == 10
    assert horizon.outcome_filled_instruments == 10
    assert horizon.paired_outcome_sessions == 0
    assert horizon.mean_baseline_rank_ic == pytest.approx(-1.0)
    assert horizon.mean_challenger_rank_ic == pytest.approx(1.0)
    assert horizon.challenger_top_net_excess_return_pct == pytest.approx(8.4)
    assert horizon.challenger_session_count == 1
    assert horizon.challenger_session_outperformance_rate == 1.0
    assert horizon.challenger_rank_ic_win_rate == 1.0
    assert horizon.challenger_median_session_net_excess_return_pct == pytest.approx(8.4)
    assert horizon.challenger_addition_count == 2
    assert horizon.challenger_removal_count == 2
    assert horizon.challenger_addition_net_excess_return_pct == pytest.approx(8.4)
    assert horizon.challenger_removal_net_excess_return_pct == pytest.approx(0.4)
    assert horizon.challenger_selection_lift_session_count == 1
    assert horizon.challenger_selection_lift_win_rate == 1.0
    assert horizon.challenger_median_selection_lift_pct == pytest.approx(8.0)
    assert [item.key for item in horizon.challenger_rank_buckets] == [
        "q1",
        "q2",
        "q3",
        "q4",
        "q5",
    ]
    assert sum(item.sample_count for item in horizon.challenger_rank_buckets) == 10
    assert horizon.challenger_industries
    assert horizon.execution_head.requested_size == 10
    assert horizon.execution_head.industry_cap == 3
    assert horizon.execution_head.baseline_selection_slots == 6
    assert horizon.execution_head.challenger_selection_slots == 6
    assert horizon.execution_head.paired_outcome_sessions == 0
    assert not horizon.execution_head.baseline_all_matured_sessions_filled
    assert not horizon.execution_head.challenger_all_matured_sessions_filled

    stored = store.shadow_outcomes(experiment.experiment_id)
    concurrent_outcome = stored[0].model_copy(
        update={
            "instrument_id": "CN:999999",
            "horizon_sessions": 99,
            "source_digest": sha256(b"concurrent-factor-shadow-outcome").hexdigest(),
            "created_at": None,
        }
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_results = list(
            executor.map(
                lambda _: store.record_shadow_outcomes([concurrent_outcome]),
                range(2),
            )
        )
    assert sum(concurrent_results) == 1
    assert (
        len(
            store.shadow_outcomes(
                experiment.experiment_id,
                scan_job_id=concurrent_outcome.scan_job_id,
                horizon_sessions=99,
            )
        )
        == 1
    )
    with pytest.raises(ValueError, match="immutable row"):
        store.record_shadow_outcomes(
            [stored[0].model_copy(update={"instrument_return_pct": 999.0})]
        )
    with session_factory() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(
            text(
                "UPDATE factor_shadow_outcomes "
                "SET instrument_return_pct = 999 "
                "WHERE experiment_id = :experiment_id"
            ),
            {"experiment_id": experiment.experiment_id},
        )
        session.commit()


def test_execution_sized_shadow_head_is_constraint_matched_and_deterministic():
    scores = [
        FactorShadowScore(
            instrument_id=f"CN:{index:06d}",
            baseline_score=float(index),
            challenger_score=float(15 - index),
            baseline_rank=(15 - index if index < 8 else index - 7),
            challenger_rank=index + 1,
            feature_coverage=1.0,
            industry=("C39" if index < 8 else f"industry-{index}"),
        )
        for index in range(15)
    ]
    raw_challenger = _raw_execution_head(scores, rank_field="challenger_rank")
    baseline = _constrained_execution_head(scores, rank_field="baseline_rank")
    challenger = _constrained_execution_head(scores, rank_field="challenger_rank")
    reversed_challenger = _constrained_execution_head(
        list(reversed(scores)),
        rank_field="challenger_rank",
    )

    assert sum(item.industry == "C39" for item in raw_challenger) == 8
    assert len(baseline) == 10
    assert len(challenger) == 10
    assert sum(item.industry == "C39" for item in baseline) == 3
    assert sum(item.industry == "C39" for item in challenger) == 3
    assert [item.instrument_id for item in reversed_challenger] == [
        item.instrument_id for item in challenger
    ]

    signal_date = date(2026, 7, 1)
    run = FactorShadowRunRef(
        experiment_id="experiment-head",
        scan_job_id="scan-head",
        signal_date=signal_date,
        dataset_revision=7,
        model_digest="a" * 64,
        scored_instruments=len(scores),
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    outcomes = {
        (run.scan_job_id, score.instrument_id, 5): FactorShadowOutcome(
            experiment_id=run.experiment_id,
            scan_job_id=run.scan_job_id,
            instrument_id=score.instrument_id,
            horizon_sessions=5,
            signal_date=signal_date,
            entry_date=date(2026, 7, 2),
            outcome_date=date(2026, 7, 8),
            benchmark_id="CN:000300.IDX",
            instrument_return_pct=(3.0 if index < 3 else -3.0 if 5 <= index < 8 else 0.0),
            benchmark_return_pct=0.0,
            excess_return_pct=(3.0 if index < 3 else -3.0 if 5 <= index < 8 else 0.0),
            net_excess_return_pct=(3.0 if index < 3 else -3.0 if 5 <= index < 8 else 0.0),
            round_trip_cost_bps=0.0,
            signal_dataset_revision=7,
            model_digest=run.model_digest,
            source_digest=sha256(f"head|{score.instrument_id}".encode()).hexdigest(),
        )
        for index, score in enumerate(scores)
    }
    evaluation = _evaluate_execution_head(
        [run],
        {run.scan_job_id: scores},
        outcomes,
        horizon_sessions=5,
    )

    assert evaluation.policy_version == "factor-shadow-execution-head-top10-cap3-v1"
    assert evaluation.baseline_raw_max_industry_positions == 3
    assert evaluation.challenger_raw_max_industry_positions == 8
    assert evaluation.challenger_raw_max_industry_concentration == 0.8
    assert evaluation.baseline_max_industry_positions == 3
    assert evaluation.challenger_max_industry_positions == 3
    assert evaluation.baseline_max_industry_concentration == 0.3
    assert evaluation.challenger_max_industry_concentration == 0.3
    assert evaluation.baseline_full_sessions == 1
    assert evaluation.challenger_full_sessions == 1
    assert evaluation.paired_outcome_sessions == 1
    assert evaluation.baseline_head_net_excess_return_pct == pytest.approx(-0.9)
    assert evaluation.challenger_head_net_excess_return_pct == pytest.approx(0.9)
    assert evaluation.challenger_lift_win_rate == 1.0
    assert evaluation.challenger_median_lift_pct == pytest.approx(1.8)


def test_execution_sized_shadow_head_caps_unknown_and_reports_unfilled():
    scores = [
        FactorShadowScore(
            instrument_id=f"CN:{index:06d}",
            baseline_score=float(7 - index),
            challenger_score=float(7 - index),
            baseline_rank=1 if index < 2 else index,
            challenger_rank=1 if index < 2 else index,
            feature_coverage=1.0,
            industry=None if index < 4 else f"industry-{index}",
        )
        for index in range(7)
    ]
    selected = _constrained_execution_head(scores, rank_field="challenger_rank")

    assert [item.instrument_id for item in selected[:2]] == ["CN:000000", "CN:000001"]
    assert len(selected) == 6
    assert sum(item.industry is None for item in selected) == 3


def test_execution_head_promotion_gate_fails_closed_on_sample_and_negative_lift():
    head = FactorShadowExecutionHeadEvaluation(
        matured_sessions=20,
        baseline_selection_slots=200,
        challenger_selection_slots=200,
        baseline_completed_outcomes=199,
        challenger_completed_outcomes=199,
        baseline_full_sessions=20,
        challenger_full_sessions=20,
        paired_outcome_sessions=19,
        baseline_all_matured_sessions_filled=True,
        challenger_all_matured_sessions_filled=True,
        baseline_head_net_excess_return_pct=0.2,
        challenger_head_net_excess_return_pct=0.1,
        challenger_lift_win_rate=0.4,
        challenger_median_lift_pct=-0.1,
        baseline_max_industry_positions=3,
        challenger_max_industry_positions=3,
        baseline_max_industry_concentration=0.3,
        challenger_max_industry_concentration=0.3,
    )
    horizons = [
        FactorShadowHorizonEvaluation(
            horizon_sessions=horizon,
            status="ready",
            matured_runs=20,
            expected_instruments=200,
            completed_instruments=200,
            outcome_coverage=1.0,
            mean_baseline_rank_ic=0.1,
            mean_challenger_rank_ic=0.2,
            challenger_session_outperformance_rate=0.75,
            challenger_median_session_net_excess_return_pct=0.2,
            challenger_selection_lift_win_rate=0.75,
            challenger_median_selection_lift_pct=0.2,
            execution_head=head,
        )
        for horizon in (5, 10, 20)
    ]

    promotion = _assess_shadow_promotion(horizons)

    assert promotion.action == "keep_shadow_only"
    assert not promotion.eligible_for_manual_review
    assert "5d_execution_head_samples_below_minimum" in promotion.reasons
    assert "5d_execution_head_lift_not_stable" in promotion.reasons
    assert "5d_execution_head_lift_not_positive" in promotion.reasons


def _factor_shadow_price_rows(
    instrument_id: str,
    entry_date: date,
    outcome_date: date,
    *,
    outcome_close: Decimal,
) -> list[dict[str, object]]:
    return [
        {
            "instrument_id": instrument_id,
            "trade_date": entry_date,
            "open": Decimal("100"),
            "high": Decimal("101"),
            "low": Decimal("99"),
            "close": Decimal("100"),
            "volume": Decimal("1000000"),
            "turnover": Decimal("100000000"),
            "provider": "fixture",
            "adjusted_open": Decimal("100"),
            "adjusted_high": Decimal("101"),
            "adjusted_low": Decimal("99"),
            "adjusted_close": Decimal("100"),
            "adjustment_factor": Decimal("1"),
            "adjustment_type": "forward",
        },
        {
            "instrument_id": instrument_id,
            "trade_date": outcome_date,
            "open": outcome_close,
            "high": outcome_close + Decimal("1"),
            "low": outcome_close - Decimal("1"),
            "close": outcome_close,
            "volume": Decimal("1000000"),
            "turnover": Decimal("100000000"),
            "provider": "fixture",
            "adjusted_open": outcome_close,
            "adjusted_high": outcome_close + Decimal("1"),
            "adjusted_low": outcome_close - Decimal("1"),
            "adjusted_close": outcome_close,
            "adjustment_factor": Decimal("1"),
            "adjustment_type": "forward",
        },
    ]
