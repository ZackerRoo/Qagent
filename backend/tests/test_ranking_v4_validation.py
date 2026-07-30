from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json

import pytest

from qagent.backtesting.ranking_v4_pbo import (
    RANKING_V4_FROZEN_PBO_MODEL_IDS,
    RankingV4DatedModelReturn,
    evaluate_ranking_v4_cscv_pbo,
)
from qagent.backtesting.ranking_v4_protocol import (
    RANKING_V4_DEVELOPMENT_END,
    RANKING_V4_DEVELOPMENT_LOOKBACK_DAYS,
    RANKING_V4_DEVELOPMENT_START,
    build_ranking_v4_protocol,
)
from qagent.backtesting.ranking_v4_validation import (
    RankingV4ReturnObservation,
    build_ranking_v4_trial_ledger,
    evaluate_ranking_v4_historical_validation,
)


START = date(2023, 1, 3)
DATE_COUNT = 96


def _dates(count: int = DATE_COUNT) -> list[date]:
    return [START + timedelta(days=index * 10) for index in range(count)]


def _passing_values(count: int = DATE_COUNT) -> dict[str, list[float]]:
    return {
        "constraint_matched_baseline": [0.0] * count,
        "ranking_v45_full": [
            1.1 + [0.0, 0.25, -0.1, 0.15, -0.2, 0.1][index % 6] for index in range(count)
        ],
        "channel_baseline": [0.15 + [0.2, -0.2, 0.1, -0.1][index % 4] for index in range(count)],
        "channel_trend": [0.2 + [0.3, -0.25, 0.15, -0.2, 0.0][index % 5] for index in range(count)],
        "channel_breakout": [
            0.1 + [0.4, -0.3, 0.1, -0.25, 0.05][index % 5] for index in range(count)
        ],
        "channel_quality_value": [
            0.18 + [0.15, -0.18, 0.08, -0.12][index % 4] for index in range(count)
        ],
        "channel_defensive_low_vol": [
            0.12 + [0.08, -0.07, 0.04, -0.05][index % 4] for index in range(count)
        ],
        "channel_etf_industry": [
            0.14 + [0.25, -0.22, 0.11, -0.16][index % 4] for index in range(count)
        ],
    }


def _matrix(
    values: dict[str, list[float]] | None = None,
) -> dict[str, list[RankingV4DatedModelReturn]]:
    selected_values = values or _passing_values()
    dates = _dates(len(next(iter(selected_values.values()))))
    return {
        model_id: [
            RankingV4DatedModelReturn(
                rebalance_date=rebalance_date,
                net_return=net_return,
            )
            for rebalance_date, net_return in zip(
                dates,
                selected_values[model_id],
                strict=True,
            )
        ]
        for model_id in RANKING_V4_FROZEN_PBO_MODEL_IDS
    }


def _observations(
    values: list[float],
    *,
    stress_values: list[float] | None = None,
) -> list[RankingV4ReturnObservation]:
    return [
        RankingV4ReturnObservation(
            rebalance_date=rebalance_date,
            net_return_pct=value,
            stress_net_return_pct=(stress_values[index] if stress_values is not None else None),
        )
        for index, (rebalance_date, value) in enumerate(
            zip(_dates(len(values)), values, strict=True)
        )
    ]


def _passing_inputs(
    *,
    stress_values: list[float] | None = None,
    values: dict[str, list[float]] | None = None,
):
    selected_values = values or _passing_values()
    model_matrix = _matrix(selected_values)
    pbo = evaluate_ranking_v4_cscv_pbo(model_matrix)
    protocol = build_ranking_v4_protocol()
    predecessor_ids = [
        item.experiment_id for item in protocol.experiment_registry.predecessor_summaries
    ]
    complete_trial_matrix = {
        **model_matrix,
        **{predecessor_id: model_matrix["channel_baseline"] for predecessor_id in predecessor_ids},
    }
    ledger = build_ranking_v4_trial_ledger(
        complete_trial_matrix,
        experiment_registry_digest=protocol.experiment_registry.registry_digest,
    )
    challenger_values = selected_values["ranking_v45_full"]
    return {
        "baseline_returns": _observations(selected_values["constraint_matched_baseline"]),
        "challenger_returns": _observations(
            challenger_values,
            stress_values=(
                stress_values
                if stress_values is not None
                else [value - 0.2 for value in challenger_values]
            ),
        ),
        "completed_trade_count": 80,
        "valid_outcome_count": 98,
        "expected_outcome_count": 100,
        "execution_start_date": RANKING_V4_DEVELOPMENT_START,
        "execution_end_date": RANKING_V4_DEVELOPMENT_END,
        "execution_rebalance_step_sessions": 10,
        "execution_lookback_days": RANKING_V4_DEVELOPMENT_LOOKBACK_DAYS,
        "challenger_max_drawdown_pct": -4.0,
        "pbo_evidence": pbo,
        "trial_ledger": ledger,
        "bootstrap_samples": 1_000,
        "permutation_samples": 3_000,
    }


def _gate(result, key: str):
    return next(item for item in result.gates if item.key == key)


def _rehash_pbo_evidence(evidence: dict[str, object]) -> dict[str, object]:
    evidence["evidence_digest"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in evidence.items() if key != "evidence_digest"},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return evidence


def test_positive_statistics_remain_shadow_only_and_dsr_unavailable_without_audit():
    inputs = _passing_inputs()

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert result.status == "insufficient"
    assert result.historical_gate_status == "insufficient"
    assert result.eligible_for_confirmatory_forward is False
    assert result.deployment_scope == "shadow_only"
    assert result.official_release_allowed is False
    assert result.evidence_window == "development"
    assert result.evidence_class == "exploratory_development_evidence"
    assert result.dependence_block_length == 3
    assert result.holm_family_size == 8
    assert result.pbo_status == "pass"
    assert result.pbo_probability == 0
    assert result.trial_ledger_status == "unavailable"
    assert "no audited complete inventory" in result.trial_ledger_reason
    assert result.deflated_sharpe_status == "unavailable"
    assert result.deflated_sharpe_probability is None
    assert result.positive_subperiod_count == 5
    assert result.subperiod_count == 5
    assert result.validation_schema_version == "ranking-v4.5-historical-validation-v1"
    assert inputs["pbo_evidence"]["evidence_schema_version"] == (
        "ranking-v4.5-cscv-pbo-evidence-v1"
    )
    assert inputs["trial_ledger"].schema_version == ("ranking-v4.5-immutable-trial-ledger-v1")
    assert inputs["trial_ledger"].ledger_id == "QAGENT-RANK-V4.5-ALL-KNOWN-TRIALS"
    assert inputs["trial_ledger"].current_trial_id == "ranking_v45_full"
    assert all(gate.status == "pass" for gate in result.gates if gate.key != "deflated_sharpe")
    assert _gate(result, "deflated_sharpe").status == "unavailable"
    assert not any("eligible" in gate.key for gate in result.gates)


def test_historical_gate_rejects_non_preregistered_execution_plan():
    inputs = _passing_inputs()
    inputs["execution_start_date"] = date(2022, 1, 1)

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert _gate(result, "preregistered_execution_plan").status == "fail"
    assert result.execution_plan_matches_protocol is False
    assert result.status == "fail"
    assert result.eligible_for_confirmatory_forward is False


def test_rows_are_aggregated_by_rebalance_date_before_pairing():
    inputs = _passing_inputs()
    baseline = inputs["baseline_returns"]
    challenger = inputs["challenger_returns"]
    inputs["baseline_returns"] = [
        RankingV4ReturnObservation(
            rebalance_date=item.rebalance_date,
            net_return_pct=item.net_return_pct,
        )
        for item in baseline
        for _ in range(2)
    ]
    inputs["challenger_returns"] = [
        RankingV4ReturnObservation(
            rebalance_date=item.rebalance_date,
            net_return_pct=item.net_return_pct,
            stress_net_return_pct=item.stress_net_return_pct,
        )
        for item in challenger
        for _ in range(2)
    ]

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert result.status == "insufficient"
    assert result.baseline_row_count == DATE_COUNT * 2
    assert result.challenger_row_count == DATE_COUNT * 2
    assert result.common_rebalance_date_count == DATE_COUNT


def test_misaligned_rebalance_dates_fail_without_intersection():
    inputs = _passing_inputs()
    challenger = list(inputs["challenger_returns"])
    last = challenger[-1]
    challenger[-1] = last.model_copy(
        update={"rebalance_date": last.rebalance_date + timedelta(days=1)}
    )
    inputs["challenger_returns"] = challenger

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert result.status == "fail"
    assert result.dates_are_common is False
    assert len(result.baseline_only_dates) == 1
    assert len(result.challenger_only_dates) == 1
    assert _gate(result, "common_rebalance_calendar").status == "fail"
    assert result.paired_mean_net_excess_pct is None


def test_missing_pbo_is_unavailable_and_cannot_pass():
    inputs = _passing_inputs()
    inputs["pbo_evidence"] = None

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert result.status == "insufficient"
    assert result.pbo_status == "unavailable"
    assert _gate(result, "pbo").status == "unavailable"
    assert result.deflated_sharpe_status == "unavailable"
    assert result.official_release_allowed is False


def test_probability_above_pbo_limit_fails():
    values: dict[str, list[float]] = {}
    block_sizes = (12,) * 8
    for model_index, model_id in enumerate(RANKING_V4_FROZEN_PBO_MODEL_IDS):
        values[model_id] = [
            ((model_index + 1) * 0.1 if block_index % 2 == 0 else (8 - model_index) * 0.05)
            for block_index, block_size in enumerate(block_sizes)
            for _ in range(block_size)
        ]
    inputs = _passing_inputs(values=values)

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert result.pbo_probability is not None
    assert result.pbo_probability > 0.20
    assert result.pbo_status == "fail"
    assert _gate(result, "pbo").status == "fail"
    assert result.status == "fail"


def test_tampered_pbo_digest_is_unavailable():
    inputs = _passing_inputs()
    tampered = dict(inputs["pbo_evidence"])
    tampered["probability"] = 0.19
    inputs["pbo_evidence"] = tampered

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert result.pbo_status == "unavailable"
    assert "digest" in result.pbo_reason
    assert result.status == "insufficient"


def test_rehashed_tampered_pbo_statistics_fail_independent_recomputation():
    inputs = _passing_inputs()
    tampered = dict(inputs["pbo_evidence"])
    tampered["probability"] = 0.0
    tampered["fold_count"] = 1
    inputs["pbo_evidence"] = _rehash_pbo_evidence(tampered)

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert result.pbo_status == "unavailable"
    assert "independent CSCV recomputation" in result.pbo_reason
    assert result.status == "insufficient"


def test_v44_sparse_pbo_matrix_passes_only_after_frozen_coverage_is_met():
    inputs = _passing_inputs()
    matrix = _matrix()
    sparse_model_id = "channel_etf_industry"
    matrix[sparse_model_id] = matrix[sparse_model_id][4:]
    pbo = evaluate_ranking_v4_cscv_pbo(matrix)
    inputs["pbo_evidence"] = pbo

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert pbo["rejection_reason"] is None
    assert pbo["model_return_matrix"][sparse_model_id][0]["net_return"] is None
    assert pbo["model_date_coverage"][sparse_model_id]["available_date_count"] == 92
    assert pbo["model_date_coverage"][sparse_model_id]["coverage_ratio"] == pytest.approx(92 / 96)
    assert result.pbo_status == "pass"
    assert result.pbo_probability == pbo["probability"]
    assert result.trial_ledger_status == "unavailable"
    assert result.deflated_sharpe_status == "unavailable"


def test_v44_sparse_pbo_matrix_below_coverage_stays_unavailable():
    inputs = _passing_inputs()
    matrix = _matrix()
    sparse_model_id = "channel_etf_industry"
    matrix[sparse_model_id] = matrix[sparse_model_id][5:]
    pbo = evaluate_ranking_v4_cscv_pbo(matrix)
    inputs["pbo_evidence"] = pbo

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert pbo["model_return_matrix"][sparse_model_id][0]["net_return"] is None
    assert pbo["model_date_coverage"][sparse_model_id]["available_date_count"] == 91
    assert "below frozen 95%" in pbo["rejection_reason"]
    assert result.pbo_status == "unavailable"
    assert result.pbo_probability is None
    assert "below frozen 95%" in result.pbo_reason


def test_v44_equal_block_remainder_evidence_validates_for_102_dates():
    inputs = _passing_inputs(values=_passing_values(102))
    pbo = inputs["pbo_evidence"]

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert pbo["date_count"] == 102
    assert pbo["evaluated_date_count"] == 96
    assert pbo["block_observation_counts"] == [12] * 8
    assert pbo["dropped_date_count"] == 6
    assert pbo["dropped_rebalance_dates"] == [item.isoformat() for item in _dates(102)[96:]]
    assert result.pbo_status == "pass"


@pytest.mark.parametrize("mutation", ["coverage", "equal_blocks"])
def test_v44_rehashed_coverage_or_equal_block_evidence_fails_recomputation(mutation):
    inputs = _passing_inputs()
    matrix = _matrix()
    sparse_model_id = "channel_etf_industry"
    matrix[sparse_model_id] = matrix[sparse_model_id][4:]
    tampered = deepcopy(evaluate_ranking_v4_cscv_pbo(matrix))
    if mutation == "coverage":
        tampered["model_date_coverage"][sparse_model_id]["available_date_count"] = 91
    else:
        tampered["block_observation_counts"][0] = 11
    inputs["pbo_evidence"] = _rehash_pbo_evidence(tampered)

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert result.pbo_status == "unavailable"
    assert "independent CSCV recomputation" in result.pbo_reason


def test_unknown_trial_ledger_makes_dsr_unavailable():
    inputs = _passing_inputs()
    inputs["trial_ledger"] = None

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert result.pbo_status == "pass"
    assert result.trial_ledger_status == "unavailable"
    assert result.deflated_sharpe_status == "unavailable"
    assert _gate(result, "deflated_sharpe").status == "unavailable"
    assert result.status == "insufficient"


def test_trial_ledger_cannot_omit_registered_rejected_predecessor():
    inputs = _passing_inputs()
    protocol = build_ranking_v4_protocol()
    incomplete = build_ranking_v4_trial_ledger(
        _matrix(),
        experiment_registry_digest=protocol.experiment_registry.registry_digest,
    )
    inputs["trial_ledger"] = incomplete

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert incomplete.covers_all_known_attempts is False
    assert result.trial_ledger_status == "unavailable"
    assert result.deflated_sharpe_status == "unavailable"
    assert result.status == "insufficient"


def test_supplying_synthetic_predecessor_rows_cannot_manufacture_trial_history():
    inputs = _passing_inputs()

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert inputs["trial_ledger"].covers_all_known_attempts is False
    assert result.trial_ledger_status == "unavailable"
    assert "cannot manufacture that evidence" in result.trial_ledger_reason
    assert result.deflated_sharpe_status == "unavailable"


def test_trial_ledger_must_be_immutable_complete_and_digest_valid():
    inputs = _passing_inputs()
    ledger = inputs["trial_ledger"]
    inputs["trial_ledger"] = ledger.model_copy(update={"covers_all_known_attempts": False})

    incomplete = evaluate_ranking_v4_historical_validation(**inputs)

    assert incomplete.trial_ledger_status == "unavailable"
    assert incomplete.deflated_sharpe_status == "unavailable"

    inputs = _passing_inputs()
    ledger = inputs["trial_ledger"]
    inputs["trial_ledger"] = ledger.model_copy(update={"ledger_digest": "0" * 64})
    tampered = evaluate_ranking_v4_historical_validation(**inputs)

    assert tampered.trial_ledger_status == "unavailable"
    assert "digest" in tampered.trial_ledger_reason


def test_persisted_research_attempt_inventory_cannot_be_omitted():
    inputs = _passing_inputs()
    attempt_id = "walk-forward-v4-extra-attempt"
    protocol = build_ranking_v4_protocol()
    inputs["known_research_attempt_ids"] = [attempt_id]
    inputs["trial_ledger"] = build_ranking_v4_trial_ledger(
        _matrix(),
        experiment_registry_digest=protocol.experiment_registry.registry_digest,
        known_research_attempt_ids=[attempt_id],
    )

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert inputs["trial_ledger"].covers_all_known_attempts is False
    assert attempt_id in inputs["trial_ledger"].known_trial_ids
    assert result.trial_ledger_status == "unavailable"
    assert result.deflated_sharpe_status == "unavailable"
    assert result.status == "insufficient"


@pytest.mark.parametrize(
    ("mutation", "gate_key"),
    [
        ("coverage", "valid_outcome_coverage"),
        ("stress_cost", "positive_stress_cost_return"),
        ("drawdown", "maximum_drawdown"),
    ],
)
def test_coverage_stress_cost_and_drawdown_fail_closed(mutation, gate_key):
    if mutation == "drawdown":
        inputs = _passing_inputs()
        inputs["challenger_max_drawdown_pct"] = -20.0
    elif mutation == "stress_cost":
        inputs = _passing_inputs(stress_values=[-0.5] * DATE_COUNT)
    else:
        inputs = _passing_inputs()
        inputs["valid_outcome_count"] = 90

    result = evaluate_ranking_v4_historical_validation(**inputs)

    assert _gate(result, gate_key).status == "fail"
    assert result.status == "fail"
    assert result.official_release_allowed is False


def test_evaluation_is_deterministic_for_fixed_seed():
    inputs = _passing_inputs()

    first = evaluate_ranking_v4_historical_validation(**inputs)
    second = evaluate_ranking_v4_historical_validation(**inputs)

    assert first == second
    assert first.evaluation_digest == second.evaluation_digest
    assert len(first.evaluation_digest) == 64
    json.dumps(first.model_dump(mode="json"), allow_nan=False, sort_keys=True)


@pytest.mark.parametrize(
    (
        "version",
        "current_trial_id",
        "matrix_digest",
        "evidence_digest",
        "ledger_digest",
        "evaluation_digest",
        "ledger_status",
        "dsr_status",
        "status",
    ),
    [
        (
            "4.1",
            "ranking_v41_full",
            "879f0a5f4e8115596e8b9bd20dca225ca2cea3cb188f8ad01e4638c9f5cffd96",
            "f1db864392e11ccc454984a9b78008cfbf130c0fc9cc56f6cc324f09109415bf",
            "b9bb300b9044f62ae723f5a9435af8e6de56b8ce66353d5b6a23e465133d8a9a",
            "cb63dba9a44a2207a6de6889ab62e1468ac4a9144e52e6125571b03ff54e4418",
            "pass",
            "pass",
            "pass",
        ),
        (
            "4.2",
            "ranking_v42_full",
            "177f009e6fec84d49d186201d706859fc2e785c138194a1c19d902e392847b78",
            "b03b145cb1120c1a8d5c5c3007f0a414a2d5fc88a90bd7b4d28d39a2712bf2ed",
            "6d26bf77b25a2a26f819bcebdccb68e2483a8dc24b38ab0e5dd21e712c5c83ab",
            "afae28d4a5cd3afacb8673ee9b379a5c56cf6f9fd235ca671e27ade2e283b930",
            "unavailable",
            "unavailable",
            "insufficient",
        ),
        (
            "4.3",
            "ranking_v43_full",
            "0f055adf45c4f661b2d176c707cc5b66778b755aca925bb848c2baed20d19b08",
            "c2108f3fa47b4d4d02ff29df9d5251df36caf611d411151c9a0e0f4a1806228e",
            "f3043dee61b81abb2c8e1a3d85188b7a4fdaf5e36438074704eb813de656ad08",
            "19f4dbcc6d48fc6bf4c2bbb4080fae21f763892b0746a0e73b9c4fc6870664be",
            "unavailable",
            "unavailable",
            "insufficient",
        ),
    ],
)
def test_prior_historical_validation_paths_keep_exact_schemas_digests_and_behavior(
    version,
    current_trial_id,
    matrix_digest,
    evidence_digest,
    ledger_digest,
    evaluation_digest,
    ledger_status,
    dsr_status,
    status,
):
    protocol = build_ranking_v4_protocol(version=version)
    dates = _dates()
    values = {key: series for key, series in _passing_values().items() if key != "ranking_v45_full"}
    values[current_trial_id] = _passing_values()["ranking_v45_full"]
    matrix = {
        model_id: [
            RankingV4DatedModelReturn(rebalance_date=rebalance_date, net_return=value)
            for rebalance_date, value in zip(dates, values[model_id], strict=True)
        ]
        for model_id in protocol.statistics_definition.pbo_model_ids
    }
    pbo = evaluate_ranking_v4_cscv_pbo(matrix, protocol_version=version)
    predecessor_ids = [
        item.experiment_id for item in protocol.experiment_registry.predecessor_summaries
    ]
    ledger = build_ranking_v4_trial_ledger(
        {
            **matrix,
            **{predecessor_id: matrix["channel_baseline"] for predecessor_id in predecessor_ids},
        },
        experiment_registry_digest=protocol.experiment_registry.registry_digest,
        protocol_version=version,
    )

    result = evaluate_ranking_v4_historical_validation(
        [(rebalance_date, 0.0) for rebalance_date in dates],
        [
            (rebalance_date, value, value - 0.2)
            for rebalance_date, value in zip(
                dates,
                values[current_trial_id],
                strict=True,
            )
        ],
        completed_trade_count=80,
        valid_outcome_count=98,
        expected_outcome_count=100,
        execution_start_date=RANKING_V4_DEVELOPMENT_START,
        execution_end_date=RANKING_V4_DEVELOPMENT_END,
        execution_rebalance_step_sessions=10,
        execution_lookback_days=RANKING_V4_DEVELOPMENT_LOOKBACK_DAYS,
        challenger_max_drawdown_pct=-4,
        pbo_evidence=pbo,
        trial_ledger=ledger,
        bootstrap_samples=500,
        permutation_samples=1_000,
        protocol_version=version,
    )

    assert pbo["evidence_schema_version"] == f"ranking-v{version}-cscv-pbo-evidence-v1"
    assert pbo["matrix_digest"] == matrix_digest
    assert pbo["evidence_digest"] == evidence_digest
    assert ledger.schema_version == f"ranking-v{version}-immutable-trial-ledger-v1"
    assert ledger.ledger_id == f"QAGENT-RANK-V{version}-ALL-KNOWN-TRIALS"
    assert ledger.current_trial_id == current_trial_id
    assert ledger.ledger_digest == ledger_digest
    assert result.validation_schema_version == f"ranking-v{version}-historical-validation-v1"
    assert result.protocol_digest == protocol.protocol_digest
    assert result.evaluation_digest == evaluation_digest
    assert result.pbo_status == "pass"
    assert result.trial_ledger_status == ledger_status
    assert result.deflated_sharpe_status == dsr_status
    assert result.status == status
