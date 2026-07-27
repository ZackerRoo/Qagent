from __future__ import annotations

import ast
import hashlib
import inspect
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from qagent.backtesting import experiment, ranking_v3
from qagent.backtesting.ranking_v3_experiment_registry import (
    RankingV3ExperimentAttempt,
    RankingV3ExperimentRegistryError,
    build_ranking_v3_experiment_registry,
)
from qagent.backtesting.ranking_v3_pbo import (
    CSCV_PBO_METHOD,
    PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY,
    RANKING_V3_FROZEN_PBO_MODEL_IDS,
)
from qagent.backtesting.ranking_v3_protocol import (
    RankingV3BenchmarkDefinition,
    RankingV3CostDefinition,
    RankingV3OutcomeSemanticsDefinition,
    RankingV3SortingDefinition,
    RankingV3StatisticalDefinition,
    RankingV3TemporalIsolationDefinition,
    build_ranking_v3_protocol,
    ranking_v3_protocol_digest_is_valid,
)
from qagent.backtesting.ranking_v3_validation import (
    DEPENDENCE_BLOCK_LENGTH,
    evaluate_ranking_v3_validation,
)
from qagent.market.calendars import trading_sessions_in_range


LEGACY_INCOMPLETE_PROTOCOL_DIGEST = (
    "de827b663095b03ded67075314faac0c1c63d17be61528afaf34912d270c68f3"
)
FROZEN_PROTOCOL_DIGEST = "43ff63ce2dfa8d96ac1f282786f98bbc5f8117afa3d83e8f6332a2a996295cd9"
FROZEN_EXPERIMENT_REGISTRY_DIGEST = (
    "e4fcf09098ed3f40dfa04a1714a4a4406e828e2018ae1f84f3039dfbfd8b6894"
)


def test_experiment_registry_is_traceable_deterministic_and_has_no_fake_p_values():
    registry = build_ranking_v3_experiment_registry()
    reversed_registry = build_ranking_v3_experiment_registry(
        attempts=tuple(reversed(registry.attempts)),
        expected_prior_attempt_count=registry.expected_prior_attempt_count,
    )

    assert registry.prior_attempt_count == 15
    assert registry.prior_attempt_count == registry.expected_prior_attempt_count
    assert registry.registry_digest == FROZEN_EXPERIMENT_REGISTRY_DIGEST
    assert registry.registry_digest == reversed_registry.registry_digest
    assert registry.attempts == reversed_registry.attempts
    assert registry.holm_prior_hypothesis_count == 15
    assert registry.unobserved_holm_p_value_count == 15
    assert registry.confirmatory_holm_p_values() == ()
    assert all(item.confirmatory_p_value is None for item in registry.attempts)
    assert all(item.counts_for_holm_family for item in registry.attempts)
    assert registry.observed_deflated_sharpe_result_count == 0
    assert registry.unobserved_deflated_sharpe_result_count == 15
    assert registry.deflated_sharpe_oos_results() == ()
    assert registry.complete_deflated_sharpe_oos_results() is None
    assert all(item.evidence_uri == f"git:{item.source_revision}" for item in registry.attempts)
    assert all(len(item.source_revision) == 40 for item in registry.attempts)


def test_experiment_registry_fails_closed_for_missing_duplicate_or_tampered_evidence():
    registry = build_ranking_v3_experiment_registry()

    with pytest.raises(
        RankingV3ExperimentRegistryError,
        match="count does not match",
    ):
        build_ranking_v3_experiment_registry(attempts=registry.attempts[:-1])
    with pytest.raises(
        RankingV3ExperimentRegistryError,
        match="count does not match",
    ):
        build_ranking_v3_experiment_registry(attempts=())

    duplicated = (*registry.attempts[:-1], registry.attempts[0])
    with pytest.raises(
        RankingV3ExperimentRegistryError,
        match="duplicate experiment",
    ):
        build_ranking_v3_experiment_registry(attempts=duplicated)

    tampered = registry.model_copy(update={"registry_digest": "0" * 64})
    with pytest.raises(
        RankingV3ExperimentRegistryError,
        match="digest mismatch",
    ):
        build_ranking_v3_protocol(experiment_registry=tampered)


def test_holm_family_tracks_missing_p_values_and_requires_result_provenance():
    registry = build_ranking_v3_experiment_registry()

    with pytest.raises(ValueError, match="result artifact digest"):
        RankingV3ExperimentAttempt(
            attempt_id="measured-attempt",
            hypothesis_key="measured_hypothesis",
            registered_on=date(2026, 7, 26),
            source_revision="a" * 40,
            evidence_uri=f"git:{'a' * 40}",
            disposition="rejected",
            counts_for_holm_family=True,
            confirmatory_p_value=0.03,
            p_value_method="one-sided-block-sign-flip",
        )

    measured = registry.attempts[0].model_copy(
        update={
            "counts_for_holm_family": True,
            "confirmatory_p_value": 0.03,
            "p_value_method": "one-sided-block-sign-flip",
            "result_artifact_digest": "b" * 64,
        }
    )
    measured_registry = build_ranking_v3_experiment_registry(
        attempts=(measured, *registry.attempts[1:]),
    )

    assert measured_registry.confirmatory_holm_p_values() == (0.03,)
    assert measured_registry.holm_prior_hypothesis_count == 15
    assert measured_registry.unobserved_holm_p_value_count == 14
    assert measured_registry.registry_digest != registry.registry_digest


def test_registered_exploration_cannot_be_silently_excluded_from_holm_family():
    registry = build_ranking_v3_experiment_registry()
    excluded = registry.attempts[0].model_copy(update={"counts_for_holm_family": False})

    with pytest.raises(
        RankingV3ExperimentRegistryError,
        match="must occupy a Holm-family hypothesis slot",
    ):
        build_ranking_v3_experiment_registry(
            attempts=(excluded, *registry.attempts[1:]),
        )


def test_dsr_registry_requires_immutable_oos_sharpe_provenance_and_complete_family():
    registry = build_ranking_v3_experiment_registry()

    with pytest.raises(ValueError, match="result artifact digest"):
        RankingV3ExperimentAttempt.model_validate(
            {
                **registry.attempts[0].model_dump(mode="python"),
                "oos_sharpe": 0.42,
                "oos_sharpe_method": "daily-oos-net-excess-sharpe",
            }
        )
    with pytest.raises(ValueError, match="named estimation method"):
        RankingV3ExperimentAttempt.model_validate(
            {
                **registry.attempts[0].model_dump(mode="python"),
                "oos_sharpe": 0.42,
                "result_artifact_digest": "d" * 64,
            }
        )

    measured = RankingV3ExperimentAttempt.model_validate(
        {
            **registry.attempts[0].model_dump(mode="python"),
            "oos_sharpe": 0.42,
            "oos_sharpe_method": "daily-oos-net-excess-sharpe",
            "result_artifact_digest": "d" * 64,
        }
    )
    partial_registry = build_ranking_v3_experiment_registry(
        attempts=(measured, *registry.attempts[1:]),
    )

    assert partial_registry.deflated_sharpe_oos_results() == (0.42,)
    assert partial_registry.observed_deflated_sharpe_result_count == 1
    assert partial_registry.unobserved_deflated_sharpe_result_count == 14
    assert partial_registry.complete_deflated_sharpe_oos_results() is None


def test_protocol_digest_covers_ranking_cost_statistics_and_registry():
    protocol = build_ranking_v3_protocol()

    assert ranking_v3_protocol_digest_is_valid(protocol)
    assert protocol.protocol_digest == FROZEN_PROTOCOL_DIGEST
    assert protocol.prior_experiment_count == protocol.experiment_registry.prior_attempt_count
    assert protocol.registered_holm_p_values == ()
    assert protocol.statistics_definition.pbo_method == CSCV_PBO_METHOD
    assert protocol.statistics_definition.pbo_scope == PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY
    assert protocol.statistics_definition.pbo_is_full_search_process_estimate is False
    assert tuple(
        item.model_id for item in protocol.statistics_definition.pbo_model_family
    ) == RANKING_V3_FROZEN_PBO_MODEL_IDS
    assert (
        protocol.statistics_definition.deflated_sharpe_evidence_policy
        == "frozen_common_date_model_matrix_with_full_registered_trial_penalty"
    )

    mutations = (
        {
            "ranking_definition": protocol.ranking_definition.model_copy(
                update={"recency_half_life_days": 180.0}
            )
        },
        {
            "cost_definition": protocol.cost_definition.model_copy(
                update={
                    "audit_stress": protocol.cost_definition.audit_stress.model_copy(
                        update={"slippage_bps": "16"}
                    )
                }
            )
        },
        {
            "statistics_definition": protocol.statistics_definition.model_copy(
                update={"dependence_block_length": 2}
            )
        },
        {
            "experiment_registry": protocol.experiment_registry.model_copy(
                update={"registry_digest": "f" * 64}
            )
        },
        {
            "temporal_isolation_definition": (
                protocol.temporal_isolation_definition.model_copy(update={"embargo_sessions": 24})
            )
        },
        {
            "outcome_semantics_definition": (
                protocol.outcome_semantics_definition.model_copy(
                    update={"not_triggered_return_pct": -0.01}
                )
            )
        },
        {
            "benchmark_definition": protocol.benchmark_definition.model_copy(
                update={"forward_release_benchmark_id": "CN:000905.IDX"}
            )
        },
        {
            "thresholds": protocol.thresholds.model_copy(
                update={"minimum_valid_outcome_coverage_ratio": 0.90}
            )
        },
    )
    for update in mutations:
        assert not ranking_v3_protocol_digest_is_valid(protocol.model_copy(update=update))


def test_protocol_freezes_window_isolation_coverage_and_outcome_semantics():
    protocol = build_ranking_v3_protocol()
    temporal = RankingV3TemporalIsolationDefinition()
    outcomes = RankingV3OutcomeSemanticsDefinition()

    assert temporal.session_unit == "a_share_trading_session"
    assert temporal.purge_sessions == 25
    assert temporal.embargo_sessions == 25
    assert temporal.validation_window_start == date(2023, 8, 7)
    assert temporal.validation_window_end == date(2024, 6, 28)
    assert temporal.historical_audit_window_start == date(2024, 8, 5)
    assert temporal.historical_audit_window_end == date(2025, 12, 31)
    assert protocol.temporal_isolation_definition == temporal

    assert protocol.thresholds.minimum_valid_outcome_coverage_ratio == 0.95
    assert protocol.thresholds.maximum_invalid_outcome_ratio == 0.05
    assert outcomes.not_triggered_status == "not_triggered"
    assert outcomes.not_triggered_return_pct == 0.0
    assert outcomes.invalid_or_censored_return_policy == "never_impute_zero_return"
    assert protocol.outcome_semantics_definition == outcomes

    windows = {item.key: item for item in protocol.windows}
    assert windows["validation"].start_date == temporal.validation_window_start
    assert windows["validation"].end_date == temporal.validation_window_end
    assert windows["historical_reused_oos"].start_date == temporal.historical_audit_window_start
    assert windows["historical_reused_oos"].end_date == temporal.historical_audit_window_end
    assert (
        len(
            trading_sessions_in_range(
                temporal.training_window_end + timedelta(days=1),
                temporal.validation_window_start - timedelta(days=1),
            )
        )
        == 25
    )
    assert (
        len(
            trading_sessions_in_range(
                temporal.validation_window_end + timedelta(days=1),
                temporal.historical_audit_window_start - timedelta(days=1),
            )
        )
        == 25
    )


def test_protocol_semantics_fail_closed_even_if_tampered_payload_is_rehashed():
    protocol = build_ranking_v3_protocol()
    inconsistent = protocol.model_copy(
        update={
            "thresholds": protocol.thresholds.model_copy(
                update={"minimum_valid_outcome_coverage_ratio": 0.90}
            )
        }
    )
    payload = inconsistent.model_dump(mode="json", exclude={"protocol_digest"})
    forged_digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    inconsistent = inconsistent.model_copy(update={"protocol_digest": forged_digest})

    assert not ranking_v3_protocol_digest_is_valid(inconsistent)


@pytest.mark.parametrize(
    ("temporal_field", "window_key", "early_start"),
    (
        ("validation_window_start", "validation", date(2023, 8, 4)),
        (
            "historical_audit_window_start",
            "historical_reused_oos",
            date(2024, 8, 2),
        ),
    ),
)
def test_protocol_fails_closed_when_only_24_complete_gap_sessions_are_declared(
    temporal_field,
    window_key,
    early_start,
):
    protocol = build_ranking_v3_protocol()
    temporal = protocol.temporal_isolation_definition.model_copy(
        update={temporal_field: early_start}
    )
    windows = [
        item.model_copy(update={"start_date": early_start}) if item.key == window_key else item
        for item in protocol.windows
    ]
    inconsistent = protocol.model_copy(
        update={
            "temporal_isolation_definition": temporal,
            "windows": windows,
        }
    )
    payload = inconsistent.model_dump(mode="json", exclude={"protocol_digest"})
    forged_digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    inconsistent = inconsistent.model_copy(update={"protocol_digest": forged_digest})

    assert not ranking_v3_protocol_digest_is_valid(inconsistent)


def test_protocol_sorting_definition_matches_runtime_ranking_constants():
    definition = RankingV3SortingDefinition()

    assert definition.minimum_training_observations == ranking_v3.MIN_V3_TRAINING_OBSERVATIONS
    assert definition.minimum_training_dates == ranking_v3.MIN_V3_TRAINING_DATES
    assert definition.prior_date_strength == ranking_v3.V3_PRIOR_DATE_STRENGTH
    assert definition.recency_half_life_days == ranking_v3.V3_RECENCY_HALF_LIFE_DAYS
    assert definition.maximum_calibration_delta == ranking_v3.V3_MAX_CALIBRATION_DELTA
    assert definition.incumbent_turnover_bonus == ranking_v3.V3_INCUMBENT_TURNOVER_BONUS

    for asset_type, weights in (
        ("etf", definition.etf_feature_weights),
        ("stock", definition.stock_feature_weights),
    ):
        for feature_name, expected_weight in weights.items():
            values = {
                field_name: 0.0 for field_name in ranking_v3.RankingV3FeatureVector.model_fields
            }
            values["data_completeness"] = 1.0
            values[feature_name] = 1.0
            features = ranking_v3.RankingV3FeatureVector(**values)

            assert ranking_v3.frozen_feature_score(
                features,
                asset_type=asset_type,
            ) == pytest.approx(expected_weight)


def test_protocol_statistical_defaults_match_runtime_validation():
    definition = RankingV3StatisticalDefinition()
    signature = inspect.signature(evaluate_ranking_v3_validation)

    assert definition.dependence_block_length == DEPENDENCE_BLOCK_LENGTH
    assert definition.bootstrap_samples == signature.parameters["bootstrap_samples"].default
    assert definition.permutation_samples == signature.parameters["permutation_samples"].default
    assert definition.random_seed == signature.parameters["seed"].default
    assert definition.holding_sessions == 20
    assert definition.rebalance_step_sessions == 10
    assert definition.dependence_block_length == 3
    assert definition.pbo_purge_rebalance_cohorts == 2
    assert "registered_prior_attempts" in definition.holm_family_source
    assert "fail_closed" in definition.holm_family_source


def test_protocol_freezes_distinct_candidate_portfolio_and_forward_benchmarks():
    definition = RankingV3BenchmarkDefinition()

    assert definition.candidate_outcome_benchmark_ids == (
        "CN:000300.IDX",
        "CN:000905.IDX",
        "CN:399006.IDX",
        "CN:000688.IDX",
    )
    assert definition.candidate_outcome_aggregation == (
        "median_of_all_four_required_benchmarks"
    )
    assert definition.candidate_outcome_missing_policy == (
        "fail_closed_if_any_benchmark_is_missing"
    )
    assert definition.historical_portfolio_benchmark_id == "CN:EQUAL_WEIGHT_ELIGIBLE"
    assert definition.forward_release_benchmark_id == "CN:000300.IDX"


def test_protocol_cost_definition_matches_walk_forward_runtime_constants():
    definition = RankingV3CostDefinition()
    source_path = Path(__file__).parents[1] / "qagent" / "backtesting" / "walk_forward.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))

    sensitivity = _cost_sensitivity_scenarios(module)
    audit_stress = _ranking_v3_audit_stress(module)

    assert sensitivity == [
        (item.key, item.slippage_bps, item.fee_multiplier)
        for item in definition.sensitivity_scenarios
    ]
    assert audit_stress == (
        definition.audit_stress.slippage_bps,
        definition.audit_stress.fee_multiplier,
    )


def test_new_protocol_digest_prevents_legacy_checkpoint_reuse(monkeypatch):
    monkeypatch.setattr(experiment, "_git_revision", lambda: ("revision-a", False))
    current = experiment.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    legacy = current.model_copy(
        update={"ranking_v3_protocol_digest": LEGACY_INCOMPLETE_PROTOCOL_DIGEST}
    )
    legacy = legacy.model_copy(
        update={
            "experiment_digest": experiment._digest(
                experiment._semantic_manifest_digest_payload(legacy)
            )
        }
    )
    legacy = experiment._with_execution_digest(legacy)

    assert current.ranking_v3_protocol_digest != LEGACY_INCOMPLETE_PROTOCOL_DIGEST
    assert experiment.walk_forward_manifest_digest_is_valid(legacy)
    assert not experiment.walk_forward_manifests_semantically_compatible(
        legacy,
        current,
    )
    assert not experiment.walk_forward_selection_manifests_semantically_compatible(
        legacy,
        current,
    )


def _cost_sensitivity_scenarios(
    module: ast.Module,
) -> list[tuple[str, str, str]]:
    for node in module.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "_cost_sensitivity":
            continue
        for child in node.body:
            if not isinstance(child, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "scenarios"
                for target in child.targets
            ):
                continue
            assert isinstance(child.value, ast.List)
            return [
                (
                    _string_value(item.elts[0]),
                    _decimal_argument(item.elts[2]),
                    _decimal_argument(item.elts[3]),
                )
                for item in child.value.elts
                if isinstance(item, ast.Tuple)
            ]
    raise AssertionError("walk-forward cost sensitivity scenarios were not found")


def _ranking_v3_audit_stress(module: ast.Module) -> tuple[str, str]:
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "audit_ranking_v3_stress_portfolio"
            for target in node.targets
        ):
            continue
        assert isinstance(node.value, ast.Call)
        keywords = {item.arg: item.value for item in node.value.keywords}
        return (
            _decimal_argument(keywords["slippage_bps"]),
            _decimal_argument(keywords["fee_multiplier"]),
        )
    raise AssertionError("Ranking V3 audit stress call was not found")


def _decimal_argument(node: ast.expr) -> str:
    assert isinstance(node, ast.Call)
    assert isinstance(node.func, ast.Name)
    assert node.func.id == "Decimal"
    return _string_value(node.args[0])


def _string_value(node: ast.expr) -> str:
    assert isinstance(node, ast.Constant)
    assert isinstance(node.value, str)
    return node.value
