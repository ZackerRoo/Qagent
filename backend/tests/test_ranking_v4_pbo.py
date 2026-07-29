from datetime import date, timedelta
import json
import math

import pytest

from qagent.backtesting.ranking_v4_pbo import (
    RANKING_V4_CSCV_PBO_METHOD,
    RANKING_V4_FROZEN_PBO_MODEL_IDS,
    RANKING_V4_PBO_BLOCK_COUNT,
    RANKING_V4_PBO_PURGE_REBALANCE_COHORTS,
    RANKING_V4_PBO_SCOPE,
    RANKING_V44_PBO_MINIMUM_MODEL_DATE_COVERAGE_RATIO,
    RANKING_V44_PBO_REMAINDER_POLICY,
    RankingV4DatedModelReturn,
    evaluate_ranking_v4_cscv_pbo,
)
from qagent.backtesting.ranking_v4_protocol import build_ranking_v4_protocol


START = date(2023, 1, 3)
DATE_COUNT = 96


def _series(values: list[float | None]) -> list[RankingV4DatedModelReturn]:
    return [
        RankingV4DatedModelReturn(
            rebalance_date=START + timedelta(days=index * 10),
            net_return=value,
        )
        for index, value in enumerate(values)
    ]


def _valid_matrix(
    *,
    model_ids: tuple[str, ...] = RANKING_V4_FROZEN_PBO_MODEL_IDS,
) -> dict[str, list[RankingV4DatedModelReturn]]:
    matrix: dict[str, list[RankingV4DatedModelReturn]] = {}
    for model_index, model_id in enumerate(model_ids):
        values = [
            ((model_index + 1) * 0.001 if block_index % 2 == 0 else (8 - model_index) * 0.0005)
            for block_index in range(8)
            for _ in range(DATE_COUNT // 8)
        ]
        matrix[model_id] = _series(values)
    return matrix


def _constant_matrix(
    date_count: int,
    *,
    model_ids: tuple[str, ...] = RANKING_V4_FROZEN_PBO_MODEL_IDS,
) -> dict[str, list[RankingV4DatedModelReturn]]:
    return {model_id: _series([0.0] * date_count) for model_id in model_ids}


PBO_BLOCK_RETURNS_14_OF_70 = (
    (0.49, 0.94, 1.12, 0.73, 0.75, 0.40, 0.28, 0.43),
    (0.37, 0.44, 0.68, 0.63, 0.58, 0.42, 0.49, 0.77),
    (0.25, 0.65, 0.32, 0.89, 0.94, 0.27, 0.57, 0.31),
    (0.34, 0.28, 0.82, 0.82, 0.70, 0.49, 0.49, 0.50),
    (0.64, 0.44, 0.28, 0.64, 0.24, 0.35, 0.30, 0.56),
    (0.57, 0.35, 0.29, 0.34, 0.14, -0.03, -0.07, 0.62),
    (0.11, 0.09, 0.15, 0.09, 0.29, 0.11, 0.37, 0.20),
    (0.02, -0.02, 0.25, -0.60, 0.25, 0.55, -0.14, -0.10),
)


def _block_return_matrix(
    block_returns: tuple[tuple[float, ...], ...],
) -> dict[str, list[RankingV4DatedModelReturn]]:
    return {
        model_id: _series(
            [block_return for block_return in block_returns[model_index] for _ in range(12)]
        )
        for model_index, model_id in enumerate(RANKING_V4_FROZEN_PBO_MODEL_IDS)
    }


def test_v4_pbo_uses_frozen_protocol_family_and_normal_path():
    protocol = build_ranking_v4_protocol()

    evidence = evaluate_ranking_v4_cscv_pbo(_valid_matrix())

    assert evidence["rejection_reason"] is None
    assert evidence["probability"] * 70 == pytest.approx(round(evidence["probability"] * 70))
    assert evidence["combination_count"] == math.comb(8, 4) == 70
    assert evidence["fold_count"] == math.comb(8, 4) == 70
    assert evidence["model_count"] == 8
    assert evidence["date_count"] == DATE_COUNT
    assert evidence["block_count"] == protocol.statistics_definition.pbo_block_count == 8
    assert (
        evidence["purge_rebalance_cohorts"]
        == protocol.statistics_definition.pbo_purge_rebalance_cohorts
        == 2
    )
    assert evidence["method"] == protocol.statistics_definition.pbo_method
    assert evidence["method"] == RANKING_V4_CSCV_PBO_METHOD
    assert evidence["scope"] == protocol.statistics_definition.pbo_scope
    assert evidence["scope"] == RANKING_V4_PBO_SCOPE
    assert evidence["search_process_coverage"] == "partial"
    assert evidence["registered_model_ids"] == list(protocol.statistics_definition.pbo_model_ids)
    assert evidence["minimum_dates_per_half"] == 24
    assert evidence["minimum_model_date_coverage_ratio"] == 0.95
    assert evidence["evaluated_date_count"] == DATE_COUNT
    assert evidence["block_observation_counts"] == [12] * 8
    assert evidence["dropped_rebalance_dates"] == []
    assert all(
        fold["training"] >= 24 and fold["testing"] >= 24
        for fold in evidence["fold_observation_counts"]
    )
    assert set(evidence["model_return_matrix"]) == set(RANKING_V4_FROZEN_PBO_MODEL_IDS)
    assert len(evidence["matrix_digest"]) == 64
    assert len(evidence["evidence_digest"]) == 64
    json.dumps(evidence, allow_nan=False)


def test_date_misalignment_fails_closed_without_intersection_or_fill():
    protocol = build_ranking_v4_protocol(version="4.3")
    matrix = _valid_matrix(model_ids=protocol.statistics_definition.pbo_model_ids)
    model_id = protocol.statistics_definition.pbo_model_ids[3]
    matrix[model_id][7] = RankingV4DatedModelReturn(
        rebalance_date=matrix[model_id][7].rebalance_date + timedelta(days=1),
        net_return=matrix[model_id][7].net_return,
    )

    evidence = evaluate_ranking_v4_cscv_pbo(matrix, protocol_version="4.3")

    assert evidence["probability"] is None
    assert evidence["combination_count"] == 0
    assert evidence["matrix_digest"] is None
    assert evidence["model_return_matrix"] == {}
    assert "exactly the same genuine rebalance dates" in evidence["rejection_reason"]


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_incomplete_or_extra_model_family_fails_closed(mutation):
    matrix = _valid_matrix()
    if mutation == "missing":
        matrix.pop(RANKING_V4_FROZEN_PBO_MODEL_IDS[-1])
    else:
        matrix["unregistered_challenger"] = _series([0.0] * DATE_COUNT)

    evidence = evaluate_ranking_v4_cscv_pbo(matrix)

    assert evidence["probability"] is None
    assert evidence["combination_count"] == 0
    assert "eight-model family exactly" in evidence["rejection_reason"]
    assert evidence["scope"] == RANKING_V4_PBO_SCOPE
    assert evidence["search_process_coverage"] == "partial"
    assert len(evidence["matrix_digest"]) == 64
    assert evidence["model_return_matrix"]


def test_purge_removes_two_adjacent_rebalance_cohorts_at_each_boundary():
    evidence = evaluate_ranking_v4_cscv_pbo(_valid_matrix())

    assert evidence["rejection_reason"] is None
    assert RANKING_V4_PBO_BLOCK_COUNT == 8
    assert RANKING_V4_PBO_PURGE_REBALANCE_COHORTS == 2
    assert len(evidence["purged_observation_counts"]) == 70
    assert min(evidence["purged_observation_counts"]) == 4
    assert max(evidence["purged_observation_counts"]) == 28
    assert all(
        fold["training"] + fold["testing"] + fold["purged"] == DATE_COUNT
        for fold in evidence["fold_observation_counts"]
    )


def test_matrix_and_evidence_digests_are_stable_and_sensitive():
    matrix = _valid_matrix()
    reordered = {
        model_id: matrix[model_id] for model_id in reversed(RANKING_V4_FROZEN_PBO_MODEL_IDS)
    }

    first = evaluate_ranking_v4_cscv_pbo(matrix)
    second = evaluate_ranking_v4_cscv_pbo(reordered)
    repeated = evaluate_ranking_v4_cscv_pbo(matrix)

    assert first == second == repeated
    assert first["matrix_digest"] == second["matrix_digest"]
    assert first["evidence_digest"] == second["evidence_digest"]

    changed = _valid_matrix()
    model_id = RANKING_V4_FROZEN_PBO_MODEL_IDS[0]
    changed[model_id][0] = RankingV4DatedModelReturn(
        rebalance_date=changed[model_id][0].rebalance_date,
        net_return=changed[model_id][0].net_return + 0.0001,
    )
    changed_evidence = evaluate_ranking_v4_cscv_pbo(changed)

    assert changed_evidence["matrix_digest"] != first["matrix_digest"]
    assert changed_evidence["evidence_digest"] != first["evidence_digest"]


def test_serialized_matrix_preserves_caller_supplied_cash_zero_returns():
    matrix = _valid_matrix()
    model_id = RANKING_V4_FROZEN_PBO_MODEL_IDS[-1]
    matrix[model_id][5] = RankingV4DatedModelReturn(
        rebalance_date=matrix[model_id][5].rebalance_date,
        net_return=0.0,
    )

    evidence = evaluate_ranking_v4_cscv_pbo(matrix)

    assert evidence["rejection_reason"] is None
    assert evidence["model_return_matrix"][model_id][5] == {
        "rebalance_date": matrix[model_id][5].rebalance_date.isoformat(),
        "net_return": 0.0,
    }
    coverage = evidence["model_date_coverage"][model_id]
    assert coverage["missing_date_count"] == 0
    assert coverage["observed_cash_zero_date_count"] == 1
    assert "numeric_zero_preserved_as_observed_cash_zero" in evidence["matrix_return_semantics"]


def test_v44_uses_equal_blocks_and_reports_six_dropped_tail_dates_for_102_dates():
    evidence = evaluate_ranking_v4_cscv_pbo(_constant_matrix(102))

    assert evidence["rejection_reason"] is None
    assert evidence["date_count"] == 102
    assert evidence["evaluated_date_count"] == 96
    assert evidence["block_size"] == 12
    assert evidence["block_observation_counts"] == [12] * 8
    assert evidence["block_remainder_policy"] == RANKING_V44_PBO_REMAINDER_POLICY
    assert evidence["dropped_date_count"] == 6
    assert evidence["dropped_rebalance_dates"] == [
        (START + timedelta(days=index * 10)).isoformat() for index in range(96, 102)
    ]
    assert evidence["combination_count"] == 70
    assert all(
        fold["training"] + fold["testing"] + fold["purged"] == 96
        for fold in evidence["fold_observation_counts"]
    )


def test_v43_keeps_historical_102_date_remainder_interpretation():
    protocol = build_ranking_v4_protocol(version="4.3")
    matrix = _constant_matrix(
        102,
        model_ids=protocol.statistics_definition.pbo_model_ids,
    )

    evidence = evaluate_ranking_v4_cscv_pbo(matrix, protocol_version="4.3")

    assert evidence["rejection_reason"] is None
    assert evidence["evidence_schema_version"] == "ranking-v4.3-cscv-pbo-evidence-v1"
    assert evidence["date_count"] == 102
    assert "evaluated_date_count" not in evidence
    assert "block_remainder_policy" not in evidence
    assert "model_date_coverage" not in evidence
    assert "explicitly_filled_as_cash_zero_return" in evidence["matrix_return_semantics"]
    assert all(
        fold["training"] + fold["testing"] + fold["purged"] == 102
        for fold in evidence["fold_observation_counts"]
    )


def test_v44_per_model_date_coverage_accepts_95_percent_and_rejects_below():
    model_id = RANKING_V4_FROZEN_PBO_MODEL_IDS[-1]
    boundary_matrix = _constant_matrix(100)
    boundary_matrix[model_id] = boundary_matrix[model_id][4:]
    boundary_matrix[model_id][0] = RankingV4DatedModelReturn(
        rebalance_date=boundary_matrix[model_id][0].rebalance_date,
        net_return=None,
    )

    boundary = evaluate_ranking_v4_cscv_pbo(boundary_matrix)

    assert boundary["rejection_reason"] is None
    assert boundary["minimum_model_date_coverage_ratio"] == float(
        RANKING_V44_PBO_MINIMUM_MODEL_DATE_COVERAGE_RATIO
    )
    assert boundary["model_date_coverage"][model_id] == {
        "expected_date_count": 100,
        "available_date_count": 95,
        "missing_date_count": 5,
        "observed_cash_zero_date_count": 95,
        "coverage_ratio": 0.95,
        "missing_rebalance_dates": [
            (START + timedelta(days=index * 10)).isoformat() for index in range(5)
        ],
    }
    assert boundary["model_return_matrix"][model_id][0]["net_return"] is None
    assert boundary["model_return_matrix"][model_id][5]["net_return"] == 0.0

    below_matrix = _constant_matrix(100)
    below_matrix[model_id] = below_matrix[model_id][6:]
    below = evaluate_ranking_v4_cscv_pbo(below_matrix)

    assert below["probability"] is None
    assert below["combination_count"] == 0
    assert below["model_date_coverage"][model_id]["coverage_ratio"] == 0.94
    assert "below frozen 95%" in below["rejection_reason"]


def test_v44_pbo_resolution_distinguishes_14_of_70_from_15_of_70():
    fourteen = evaluate_ranking_v4_cscv_pbo(_block_return_matrix(PBO_BLOCK_RETURNS_14_OF_70))
    fifteen_block_returns = [list(values) for values in PBO_BLOCK_RETURNS_14_OF_70]
    fifteen_block_returns[0][0] -= 0.10
    fifteen = evaluate_ranking_v4_cscv_pbo(
        _block_return_matrix(tuple(tuple(values) for values in fifteen_block_returns))
    )

    assert fourteen["rejection_reason"] is None
    assert fifteen["rejection_reason"] is None
    assert sum(logit < 0.0 for logit in fourteen["relative_rank_logits"]) == 14
    assert sum(logit < 0.0 for logit in fifteen["relative_rank_logits"]) == 15
    assert fourteen["probability"] == pytest.approx(14 / 70)
    assert fifteen["probability"] == pytest.approx(15 / 70)


def test_less_than_protocol_minimum_genuine_dates_fails_closed():
    matrix = {model_id: _series([0.0] * 23) for model_id in RANKING_V4_FROZEN_PBO_MODEL_IDS}

    evidence = evaluate_ranking_v4_cscv_pbo(matrix)

    assert evidence["probability"] is None
    assert "minimum of 24 genuine rebalance dates" in evidence["rejection_reason"]


def test_v42_fails_closed_when_purged_half_has_fewer_than_24_dates():
    protocol = build_ranking_v4_protocol(version="4.2")
    matrix = {
        model_id: _series([0.0] * 64) for model_id in protocol.statistics_definition.pbo_model_ids
    }

    evidence = evaluate_ranking_v4_cscv_pbo(matrix, protocol_version="4.2")

    assert evidence["probability"] is None
    assert evidence["fold_count"] == 0
    assert "fewer than 24 genuine rebalance dates" in evidence["rejection_reason"]


def test_v41_four_block_six_fold_resolution_remains_reproducible():
    protocol = build_ranking_v4_protocol(version="4.1")
    model_ids = protocol.statistics_definition.pbo_model_ids
    matrix = {
        model_id: _series(
            [
                ((model_index + 1) * 0.001 if block_index % 2 == 0 else (8 - model_index) * 0.0005)
                for block_index in range(4)
                for _ in range(8)
            ]
        )
        for model_index, model_id in enumerate(model_ids)
    }

    evidence = evaluate_ranking_v4_cscv_pbo(matrix, protocol_version="4.1")

    assert evidence["rejection_reason"] is None
    assert evidence["block_count"] == 4
    assert evidence["fold_count"] == math.comb(4, 2) == 6
    assert "minimum_dates_per_half" not in evidence
