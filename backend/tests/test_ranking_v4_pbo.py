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
    RankingV4DatedModelReturn,
    evaluate_ranking_v4_cscv_pbo,
)
from qagent.backtesting.ranking_v4_protocol import build_ranking_v4_protocol


START = date(2023, 1, 3)
DATE_COUNT = 32


def _series(values: list[float]) -> list[RankingV4DatedModelReturn]:
    return [
        RankingV4DatedModelReturn(
            rebalance_date=START + timedelta(days=index * 10),
            net_return=value,
        )
        for index, value in enumerate(values)
    ]


def _valid_matrix() -> dict[str, list[RankingV4DatedModelReturn]]:
    matrix: dict[str, list[RankingV4DatedModelReturn]] = {}
    for model_index, model_id in enumerate(RANKING_V4_FROZEN_PBO_MODEL_IDS):
        values = [
            ((model_index + 1) * 0.001 if block_index % 2 == 0 else (8 - model_index) * 0.0005)
            for block_index in range(4)
            for _ in range(DATE_COUNT // 4)
        ]
        matrix[model_id] = _series(values)
    return matrix


def test_v4_pbo_uses_frozen_protocol_family_and_normal_path():
    protocol = build_ranking_v4_protocol()

    evidence = evaluate_ranking_v4_cscv_pbo(_valid_matrix())

    assert evidence["rejection_reason"] is None
    assert evidence["probability"] == pytest.approx(2 / 6)
    assert evidence["combination_count"] == math.comb(4, 2)
    assert evidence["fold_count"] == math.comb(4, 2)
    assert evidence["model_count"] == 8
    assert evidence["date_count"] == DATE_COUNT
    assert evidence["block_count"] == protocol.statistics_definition.pbo_block_count == 4
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
    assert set(evidence["model_return_matrix"]) == set(RANKING_V4_FROZEN_PBO_MODEL_IDS)
    assert len(evidence["matrix_digest"]) == 64
    assert len(evidence["evidence_digest"]) == 64
    json.dumps(evidence, allow_nan=False)


def test_date_misalignment_fails_closed_without_intersection_or_fill():
    matrix = _valid_matrix()
    model_id = RANKING_V4_FROZEN_PBO_MODEL_IDS[3]
    matrix[model_id][7] = RankingV4DatedModelReturn(
        rebalance_date=matrix[model_id][7].rebalance_date + timedelta(days=1),
        net_return=matrix[model_id][7].net_return,
    )

    evidence = evaluate_ranking_v4_cscv_pbo(matrix)

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
    assert RANKING_V4_PBO_BLOCK_COUNT == 4
    assert RANKING_V4_PBO_PURGE_REBALANCE_COHORTS == 2
    assert evidence["purged_observation_counts"] == [4, 12, 8, 8, 12, 4]
    assert evidence["fold_observation_counts"] == [
        {"training": 14, "testing": 14, "purged": 4},
        {"training": 10, "testing": 10, "purged": 12},
        {"training": 12, "testing": 12, "purged": 8},
        {"training": 12, "testing": 12, "purged": 8},
        {"training": 10, "testing": 10, "purged": 12},
        {"training": 14, "testing": 14, "purged": 4},
    ]


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
    assert "explicitly_filled_as_cash_zero_return" in evidence["matrix_return_semantics"]


def test_less_than_protocol_minimum_genuine_dates_fails_closed():
    matrix = {model_id: _series([0.0] * 23) for model_id in RANKING_V4_FROZEN_PBO_MODEL_IDS}

    evidence = evaluate_ranking_v4_cscv_pbo(matrix)

    assert evidence["probability"] is None
    assert "minimum of 24 genuine rebalance dates" in evidence["rejection_reason"]
