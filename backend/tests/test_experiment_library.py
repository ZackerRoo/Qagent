import json
from datetime import date, datetime, timezone

from qagent.research.experiment_library import build_experiment_library
from qagent.recommendations.strategy_configuration import build_paper_strategy_configuration
from qagent.storage.factor_research import FactorResearchExperiment
from qagent.storage.paper import PaperAccountSettings, PaperResearchBaseline
from qagent.storage.repository import ScanRunRecord, WalkForwardRunRecord


NOW = datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc)


def _account() -> PaperAccountSettings:
    return PaperAccountSettings(
        account_id="paper-account",
        session_id="paper-session",
        label="Research",
        status="active",
        initial_capital=100_000,
        allocation_per_trade_pct=10,
        max_positions=10,
        transaction_cost_bps=5,
        slippage_bps=5,
        take_profit_pct=50,
        started_at=NOW,
    )


def _scan_run() -> ScanRunRecord:
    configuration, digest = build_paper_strategy_configuration(
        provider="free",
        signal_date=date(2026, 8, 25),
        symbols=["CN:000001", "CN:510300"],
        include_etfs=True,
        feature_set_version="ranking-v4",
        recommendation_policy="recommendation-v4",
        calibration_merge_policy="frozen",
        quality_weights={"momentum": 0.5, "quality": 0.5},
        governance_source="test",
        governance_strategies={},
        account=_account(),
    )
    return ScanRunRecord(
        run_id="scan-1",
        provider="free",
        mode="full_market_batch",
        symbols=["CN:000001", "CN:510300"],
        scanned=2,
        cards=2,
        data_health={
            "full_market_scan_complete": "true",
            "feature_set_version": "ranking-v4",
            "recommendation_policy_entrypoint": "recommendation-v4",
            "dynamic_calibration_merge_policy": "frozen",
            "paper_strategy_configuration_json": json.dumps(configuration),
            "paper_strategy_configuration_digest": digest,
        },
        completed_at=NOW,
        created_at=NOW,
    )


def _factor_experiment() -> FactorResearchExperiment:
    return FactorResearchExperiment(
        experiment_id="factor-1",
        experiment_name="Frozen factor comparison",
        status="succeeded",
        provider_mode="free",
        model_family="lightgbm",
        benchmark_id="CN:000300.IDX",
        dataset_revision=9000,
        start_date=date(2023, 1, 1),
        end_date=date(2025, 12, 31),
        code_revision="abc123",
        config_digest="a" * 64,
        metrics={
            "baseline": {"mean_rank_ic": 0.02},
            "lightgbm_challenger": {"mean_rank_ic": 0.03},
            "activation_allowed": False,
        },
        created_at=NOW,
        completed_at=NOW,
    )


def _walk_forward() -> WalkForwardRunRecord:
    return WalkForwardRunRecord(
        run_id="walk-1",
        provider="free",
        status="rejected",
        start_date=date(2021, 1, 1),
        end_date=date(2025, 12, 31),
        dataset_revision=8939,
        rebalance_step_sessions=10,
        lookback_days=400,
        snapshot_count=102,
        top_5_trade_count=0,
        top_10_trade_count=0,
        top_5_return_pct=0,
        top_10_return_pct=0,
        top_5_oos_trades=0,
        top_10_oos_trades=0,
        top_5_oos_gate="failed",
        top_10_oos_gate="failed",
        reproducibility_digest="b" * 64,
        payload={},
        data_health={},
        created_at=NOW,
        updated_at=NOW,
    )


def test_experiment_library_separates_evidence_scopes_and_keeps_execution_unchanged():
    baseline = PaperResearchBaseline(
        baseline_id="baseline-1",
        provider="free",
        paper_session_id="paper-session",
        walk_forward_run_id="walk-1",
        start_date=date(2026, 8, 4),
        definition_digest="c" * 64,
        definition={"schema_version": "paper-research-baseline-v1"},
        created_at=NOW,
    )

    report = build_experiment_library(
        provider="free",
        scan_runs=[_scan_run()],
        factor_experiments=[_factor_experiment()],
        walk_forward_runs=[_walk_forward().model_copy(update={"updated_at": NOW.replace(tzinfo=None)})],
        paper_baseline=baseline,
        now=NOW,
    )

    artifacts = {artifact.artifact_type: artifact for artifact in report.artifacts}
    assert set(artifacts) == {
        "strategy_configuration",
        "paper_model_cohort",
        "factor_research",
        "walk_forward_validation",
        "paper_forward_baseline",
    }
    assert artifacts["strategy_configuration"].status == "frozen"
    assert artifacts["paper_model_cohort"].scope == "current_paper"
    assert artifacts["factor_research"].scope == "research_shadow"
    assert artifacts["walk_forward_validation"].scope == "historical_development"
    assert artifacts["walk_forward_validation"].created_at.tzinfo is not None
    assert report.data_health["experiment_library_changes_paper_execution"] == "false"


def test_experiment_library_ignores_incomplete_scans_and_other_providers():
    scan = _scan_run().model_copy(
        update={"data_health": {"full_market_scan_complete": "false"}}
    )
    other = _factor_experiment().model_copy(update={"provider_mode": "fixture"})

    report = build_experiment_library(
        provider="free",
        scan_runs=[scan],
        factor_experiments=[other],
        walk_forward_runs=[],
        paper_baseline=None,
        now=NOW,
    )

    assert report.artifacts == []
    assert report.data_health["experiment_library_scan_runs"] == "0"


def test_factor_experiment_library_marks_new_cohort_and_preserves_predecessor():
    predecessor = _factor_experiment().model_copy(
        update={
            "experiment_id": "factor-old",
            "config_digest": "b" * 64,
            "config": {"model_recipe": "balanced_v1", "top_fraction": 0.10},
            "created_at": NOW.replace(day=25),
            "completed_at": NOW.replace(day=25),
        }
    )
    current = _factor_experiment().model_copy(
        update={
            "experiment_id": "factor-new",
            "config_digest": "c" * 64,
            "config": {"model_recipe": "regularized_v1", "top_fraction": 0.10},
        }
    )

    report = build_experiment_library(
        provider="free",
        scan_runs=[],
        factor_experiments=[predecessor, current],
        walk_forward_runs=[],
        paper_baseline=None,
        now=NOW,
    )

    artifacts = {artifact.artifact_id: artifact for artifact in report.artifacts}
    assert artifacts["factor-new"].evidence["cohort_state"] == "current"
    assert artifacts["factor-new"].evidence["predecessor_experiment"] == "factor-old"
    assert artifacts["factor-new"].evidence["reset_reason"] == "new_config_cohort:model_recipe"
    assert artifacts["factor-new"].evidence["evidence_policy"] == "experiment_scoped_no_carryover"
    assert artifacts["factor-old"].evidence["cohort_state"] == "predecessor"
