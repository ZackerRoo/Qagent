from datetime import date, timedelta
from decimal import Decimal
import json
import math

import pytest

from qagent.backtesting.ranking_v3_pbo import (
    CSCV_PBO_METHOD,
    PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY,
    PBO_SCOPE_PROVIDED_MODEL_FAMILY,
    PBO_SEARCH_PROCESS_COVERAGE,
    RANKING_V3_FROZEN_PBO_MODEL_IDS,
    RankingV3DatedModelReturn,
    evaluate_ranking_v3_cscv_pbo,
)


START = date(2025, 1, 2)


def _series(values: list[float]) -> list[RankingV3DatedModelReturn]:
    return [
        RankingV3DatedModelReturn(
            rebalance_date=START + timedelta(days=index),
            net_return=value,
        )
        for index, value in enumerate(values)
    ]


def _valid_matrix():
    return {
        "regime": _series([4.0, 4.0, 4.0, 4.0, -4.0, -4.0, -4.0, -4.0]),
        "steady": _series([1.0] * 8),
        "flat": _series([0.0] * 8),
    }


def test_cscv_pbo_uses_contiguous_symmetric_splits_and_serializes_evidence():
    evidence = evaluate_ranking_v3_cscv_pbo(_valid_matrix(), block_count=4)

    assert evidence["rejection_reason"] is None
    assert evidence["probability"] == pytest.approx(2 / 6)
    assert evidence["combination_count"] == math.comb(4, 2)
    assert evidence["fold_count"] == math.comb(4, 2)
    assert evidence["model_count"] == 3
    assert evidence["date_count"] == 8
    assert evidence["block_count"] == 4
    assert evidence["method"] == CSCV_PBO_METHOD
    assert evidence["scope"] == PBO_SCOPE_PROVIDED_MODEL_FAMILY
    assert evidence["search_process_coverage"] == PBO_SEARCH_PROCESS_COVERAGE
    assert evidence["selected_model_frequencies"] == {
        "flat": 0,
        "regime": 2,
        "steady": 4,
    }
    assert len(evidence["relative_rank_logits"]) == 6
    assert sum(value < 0 for value in evidence["relative_rank_logits"]) == 2
    assert evidence["purged_observation_counts"] == [2, 6, 4, 4, 6, 2]
    assert len(evidence["matrix_digest"]) == 64
    json.dumps(evidence, allow_nan=False)


def test_frozen_six_model_matrix_discloses_partial_family_scope_not_full_search():
    matrix = {
        model_id: _series([float(index + 1)] * 8)
        for index, model_id in enumerate(RANKING_V3_FROZEN_PBO_MODEL_IDS)
    }

    evidence = evaluate_ranking_v3_cscv_pbo(matrix, block_count=4)

    assert evidence["rejection_reason"] is None
    assert evidence["model_count"] == 6
    assert evidence["scope"] == PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY
    assert evidence["search_process_coverage"] == "partial"


@pytest.mark.parametrize(
    ("matrix", "block_count", "reason"),
    [
        (
            {
                "model-a": _series([1.0] * 8),
                "model-b": _series([0.0] * 8),
            },
            4,
            "at least 3 models",
        ),
        (_valid_matrix(), 3, "at least 4 contiguous time blocks"),
        (_valid_matrix(), 5, "block_count must be even"),
        (
            {
                "model-a": _series([1.0] * 7),
                "model-b": _series([0.0] * 7),
                "model-c": _series([-1.0] * 7),
            },
            4,
            "at least 2 observations per contiguous block",
        ),
    ],
)
def test_insufficient_or_invalid_dimensions_fail_closed(matrix, block_count, reason):
    evidence = evaluate_ranking_v3_cscv_pbo(matrix, block_count=block_count)

    assert evidence["probability"] is None
    assert evidence["combination_count"] == 0
    assert reason in evidence["rejection_reason"]
    assert evidence["selected_model_frequencies"] == {}
    assert evidence["relative_rank_logits"] == []


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, -math.inf, Decimal("NaN"), Decimal("Infinity")],
)
def test_non_finite_returns_fail_closed(value):
    matrix = _valid_matrix()
    matrix["steady"][3] = RankingV3DatedModelReturn(
        rebalance_date=START + timedelta(days=3),
        net_return=value,
    )

    evidence = evaluate_ranking_v3_cscv_pbo(matrix)

    assert evidence["probability"] is None
    assert evidence["matrix_digest"] is None
    assert "non-finite return" in evidence["rejection_reason"]


def test_invalid_block_count_type_returns_serializable_rejection():
    evidence = evaluate_ranking_v3_cscv_pbo(_valid_matrix(), block_count=True)

    assert evidence["probability"] is None
    assert evidence["block_count"] is None
    assert "must be an integer" in evidence["rejection_reason"]
    json.dumps(evidence, allow_nan=False)


def test_out_of_order_dates_fail_closed():
    matrix = _valid_matrix()
    matrix["steady"][3], matrix["steady"][4] = matrix["steady"][4], matrix["steady"][3]

    evidence = evaluate_ranking_v3_cscv_pbo(matrix)

    assert evidence["probability"] is None
    assert "out-of-order rebalance date" in evidence["rejection_reason"]


def test_duplicate_dates_fail_closed():
    matrix = _valid_matrix()
    matrix["steady"][4] = RankingV3DatedModelReturn(
        rebalance_date=matrix["steady"][3].rebalance_date,
        net_return=matrix["steady"][4].net_return,
    )

    evidence = evaluate_ranking_v3_cscv_pbo(matrix)

    assert evidence["probability"] is None
    assert "duplicate rebalance date" in evidence["rejection_reason"]


def test_non_common_model_calendars_fail_closed_without_intersection():
    matrix = _valid_matrix()
    matrix["steady"] = [
        RankingV3DatedModelReturn(
            rebalance_date=item.rebalance_date + timedelta(days=1),
            net_return=item.net_return,
        )
        for item in matrix["steady"]
    ]

    evidence = evaluate_ranking_v3_cscv_pbo(matrix)

    assert evidence["probability"] is None
    assert evidence["date_count"] == 0
    assert "exactly the same rebalance dates" in evidence["rejection_reason"]


def test_matrix_digest_and_evidence_are_deterministic_across_mapping_order():
    matrix = _valid_matrix()
    reordered = {
        "flat": matrix["flat"],
        "steady": matrix["steady"],
        "regime": matrix["regime"],
    }

    first = evaluate_ranking_v3_cscv_pbo(matrix, block_count=4)
    second = evaluate_ranking_v3_cscv_pbo(reordered, block_count=4)
    repeated = evaluate_ranking_v3_cscv_pbo(matrix, block_count=4)

    assert first == second == repeated
    assert first["matrix_digest"] == second["matrix_digest"]

    changed = _valid_matrix()
    changed["flat"][0] = RankingV3DatedModelReturn(
        rebalance_date=changed["flat"][0].rebalance_date,
        net_return=0.01,
    )
    changed_evidence = evaluate_ranking_v3_cscv_pbo(changed, block_count=4)
    assert changed_evidence["matrix_digest"] != first["matrix_digest"]


def test_uneven_contiguous_blocks_use_every_date_without_truncation():
    matrix = {
        "model-a": _series([1.0] * 10),
        "model-b": _series([0.0] * 10),
        "model-c": _series([-1.0] * 10),
    }

    evidence = evaluate_ranking_v3_cscv_pbo(matrix, block_count=4)

    assert evidence["rejection_reason"] is None
    assert evidence["date_count"] == 10
    assert evidence["fold_count"] == math.comb(4, 2)
