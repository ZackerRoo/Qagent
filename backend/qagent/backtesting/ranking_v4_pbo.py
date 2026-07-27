from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
from itertools import combinations
import json
import math
from numbers import Real
from typing import TypeAlias

from qagent.backtesting.ranking_v4_protocol import build_ranking_v4_protocol


_PROTOCOL = build_ranking_v4_protocol()
_STATISTICS = _PROTOCOL.statistics_definition

RANKING_V4_CSCV_PBO_METHOD = _STATISTICS.pbo_method
RANKING_V4_PBO_SCOPE = _STATISTICS.pbo_scope
RANKING_V4_PBO_SEARCH_PROCESS_COVERAGE = "partial"
RANKING_V4_FROZEN_PBO_MODEL_IDS = _STATISTICS.pbo_model_ids
RANKING_V4_PBO_BLOCK_COUNT = _STATISTICS.pbo_block_count
RANKING_V4_PBO_PURGE_REBALANCE_COHORTS = _STATISTICS.pbo_purge_rebalance_cohorts
RANKING_V4_PBO_MINIMUM_DATE_COUNT = _PROTOCOL.thresholds.minimum_rebalance_dates

_MATRIX_SCHEMA_VERSION = "ranking-v4-real-model-return-matrix-v1"
_EVIDENCE_SCHEMA_VERSION = "ranking-v4-cscv-pbo-evidence-v1"
_MATRIX_RETURN_SEMANTICS = (
    "caller_supplied_common_rebalance_calendar_with_invalid_or_missing_model_dates_"
    "explicitly_filled_as_cash_zero_return_before_evaluation"
)

SerializableEvidence: TypeAlias = dict[str, object]


@dataclass(frozen=True, slots=True)
class RankingV4DatedModelReturn:
    """One realized net model return on a genuine common rebalance date."""

    rebalance_date: date
    net_return: float


def evaluate_ranking_v4_cscv_pbo(
    model_returns: Mapping[str, Sequence[RankingV4DatedModelReturn]],
) -> SerializableEvidence:
    """Evaluate frozen-family Ranking V4 PBO from a complete real-date matrix.

    The evaluator never intersects calendars, invents observations, or fills
    missing model dates. The caller must first place all eight registered
    models on the same genuine rebalance calendar and explicitly represent an
    unavailable model/date result as cash with zero return.
    """

    normalized, rejection_reason = _normalize_matrix(model_returns)
    if normalized is None:
        return _rejected_evidence(
            model_count=_safe_mapping_length(model_returns),
            rejection_reason=rejection_reason or "model-return matrix is invalid",
        )

    model_ids, rebalance_dates, returns_by_model = normalized
    serialized_matrix = _serialize_matrix(model_ids, rebalance_dates, returns_by_model)
    matrix_digest = _matrix_digest(serialized_matrix)
    model_count = len(model_ids)
    date_count = len(rebalance_dates)

    if model_ids != tuple(sorted(RANKING_V4_FROZEN_PBO_MODEL_IDS)):
        return _rejected_evidence(
            model_count=model_count,
            date_count=date_count,
            matrix_digest=matrix_digest,
            model_return_matrix=serialized_matrix,
            rejection_reason=(
                "model family must match the frozen Ranking V4 eight-model family exactly"
            ),
        )
    if date_count < RANKING_V4_PBO_MINIMUM_DATE_COUNT:
        return _rejected_evidence(
            model_count=model_count,
            date_count=date_count,
            matrix_digest=matrix_digest,
            model_return_matrix=serialized_matrix,
            rejection_reason=(
                "date count must meet the Ranking V4 minimum of "
                f"{RANKING_V4_PBO_MINIMUM_DATE_COUNT} genuine rebalance dates"
            ),
        )

    blocks = _contiguous_blocks(date_count, RANKING_V4_PBO_BLOCK_COUNT)
    training_block_count = RANKING_V4_PBO_BLOCK_COUNT // 2
    selected_model_frequencies = dict.fromkeys(model_ids, 0)
    relative_rank_logits: list[float] = []
    purged_observation_counts: list[int] = []
    fold_observation_counts: list[dict[str, int]] = []

    for training_blocks in combinations(
        range(RANKING_V4_PBO_BLOCK_COUNT),
        training_block_count,
    ):
        training_indices, testing_indices, purged_count = _purged_split_indices(
            blocks,
            frozenset(training_blocks),
        )
        if not training_indices or not testing_indices:
            return _rejected_evidence(
                model_count=model_count,
                date_count=date_count,
                matrix_digest=matrix_digest,
                model_return_matrix=serialized_matrix,
                rejection_reason="purge removed every observation from a CSCV half",
            )
        purged_observation_counts.append(purged_count)
        fold_observation_counts.append(
            {
                "training": len(training_indices),
                "testing": len(testing_indices),
                "purged": purged_count,
            }
        )

        training_means = {
            model_id: _mean_at_indices(returns_by_model[model_id], training_indices)
            for model_id in model_ids
        }
        selected_model = min(
            model_ids,
            key=lambda model_id: (-training_means[model_id], model_id),
        )
        selected_model_frequencies[selected_model] += 1

        testing_means = {
            model_id: _mean_at_indices(returns_by_model[model_id], testing_indices)
            for model_id in model_ids
        }
        relative_rank_logits.append(_relative_rank_logit(testing_means, selected_model))

    combination_count = len(relative_rank_logits)
    below_median_count = sum(logit < 0.0 for logit in relative_rank_logits)
    evidence: SerializableEvidence = {
        "evidence_schema_version": _EVIDENCE_SCHEMA_VERSION,
        "probability": below_median_count / combination_count,
        "combination_count": combination_count,
        "fold_count": combination_count,
        "model_count": model_count,
        "date_count": date_count,
        "block_count": RANKING_V4_PBO_BLOCK_COUNT,
        "purge_rebalance_cohorts": RANKING_V4_PBO_PURGE_REBALANCE_COHORTS,
        "matrix_digest": matrix_digest,
        "model_return_matrix": serialized_matrix,
        "matrix_return_semantics": _MATRIX_RETURN_SEMANTICS,
        "method": RANKING_V4_CSCV_PBO_METHOD,
        "scope": RANKING_V4_PBO_SCOPE,
        "search_process_coverage": RANKING_V4_PBO_SEARCH_PROCESS_COVERAGE,
        "registered_model_ids": list(RANKING_V4_FROZEN_PBO_MODEL_IDS),
        "selected_model_frequencies": selected_model_frequencies,
        "relative_rank_logits": relative_rank_logits,
        "purged_observation_counts": purged_observation_counts,
        "fold_observation_counts": fold_observation_counts,
        "rejection_reason": None,
    }
    evidence["evidence_digest"] = _evidence_digest(evidence)
    return evidence


def _normalize_matrix(
    model_returns: Mapping[str, Sequence[RankingV4DatedModelReturn]],
) -> tuple[
    tuple[tuple[str, ...], tuple[date, ...], dict[str, tuple[float, ...]]] | None,
    str | None,
]:
    if not isinstance(model_returns, Mapping):
        return None, "model_returns must be a mapping"
    if not model_returns:
        return None, "model-return matrix must not be empty"
    if any(
        not isinstance(model_id, str) or not model_id or model_id != model_id.strip()
        for model_id in model_returns
    ):
        return None, "model identifiers must be non-empty, whitespace-trimmed strings"

    model_ids = tuple(sorted(model_returns))
    reference_dates: tuple[date, ...] | None = None
    returns_by_model: dict[str, tuple[float, ...]] = {}

    for model_id in model_ids:
        observations = model_returns[model_id]
        if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
            return None, f"observations for model {model_id!r} must be a sequence"
        if not observations:
            return None, f"observations for model {model_id!r} must not be empty"

        dates: list[date] = []
        values: list[float] = []
        previous_date: date | None = None
        for observation in observations:
            if not isinstance(observation, RankingV4DatedModelReturn):
                return None, (
                    f"observations for model {model_id!r} must contain "
                    "RankingV4DatedModelReturn values"
                )
            rebalance_date = observation.rebalance_date
            if not isinstance(rebalance_date, date) or isinstance(rebalance_date, datetime):
                return None, f"model {model_id!r} contains an invalid rebalance date"
            if previous_date is not None and rebalance_date <= previous_date:
                relation = "duplicate" if rebalance_date == previous_date else "out-of-order"
                return None, (
                    f"model {model_id!r} contains a {relation} rebalance date "
                    f"{rebalance_date.isoformat()}"
                )

            value = observation.net_return
            if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
                return None, f"model {model_id!r} contains a non-numeric return"
            normalized_value = float(value)
            if not math.isfinite(normalized_value):
                return None, f"model {model_id!r} contains a non-finite return"

            dates.append(rebalance_date)
            values.append(normalized_value)
            previous_date = rebalance_date

        current_dates = tuple(dates)
        if reference_dates is None:
            reference_dates = current_dates
        elif current_dates != reference_dates:
            return None, (
                "all eight models must use exactly the same genuine rebalance dates; "
                "the evaluator does not intersect or fill calendars"
            )
        returns_by_model[model_id] = tuple(values)

    assert reference_dates is not None
    return (model_ids, reference_dates, returns_by_model), None


def _contiguous_blocks(
    date_count: int,
    block_count: int,
) -> tuple[tuple[int, ...], ...]:
    block_size, remainder = divmod(date_count, block_count)
    blocks: list[tuple[int, ...]] = []
    cursor = 0
    for block_index in range(block_count):
        current_size = block_size + (1 if block_index < remainder else 0)
        blocks.append(tuple(range(cursor, cursor + current_size)))
        cursor += current_size
    return tuple(blocks)


def _purged_split_indices(
    blocks: Sequence[Sequence[int]],
    training_blocks: frozenset[int],
) -> tuple[tuple[int, ...], tuple[int, ...], int]:
    assignment = {
        index: block_index in training_blocks
        for block_index, block in enumerate(blocks)
        for index in block
    }
    ordered_indices = tuple(sorted(assignment))
    purged: set[int] = set()
    for left, right in zip(ordered_indices, ordered_indices[1:]):
        if assignment[left] == assignment[right]:
            continue
        for distance in range(RANKING_V4_PBO_PURGE_REBALANCE_COHORTS):
            purged.add(left - distance)
            purged.add(right + distance)

    valid_indices = set(ordered_indices)
    purged &= valid_indices
    training = tuple(
        index for index in ordered_indices if index not in purged and assignment[index]
    )
    testing = tuple(
        index for index in ordered_indices if index not in purged and not assignment[index]
    )
    return training, testing, len(purged)


def _mean_at_indices(values: Sequence[float], indices: Sequence[int]) -> float:
    return math.fsum(values[index] for index in indices) / len(indices)


def _relative_rank_logit(
    testing_means: Mapping[str, float],
    selected_model: str,
) -> float:
    selected_value = testing_means[selected_model]
    lower_count = sum(value < selected_value for value in testing_means.values())
    equal_count = sum(value == selected_value for value in testing_means.values())
    average_ascending_rank = lower_count + (equal_count + 1) / 2
    relative_rank = average_ascending_rank / (len(testing_means) + 1)
    logit = math.log(relative_rank / (1.0 - relative_rank))
    return 0.0 if logit == 0.0 else logit


def _serialize_matrix(
    model_ids: Sequence[str],
    rebalance_dates: Sequence[date],
    returns_by_model: Mapping[str, Sequence[float]],
) -> dict[str, list[dict[str, object]]]:
    return {
        model_id: [
            {
                "rebalance_date": rebalance_date.isoformat(),
                "net_return": returns_by_model[model_id][index],
            }
            for index, rebalance_date in enumerate(rebalance_dates)
        ]
        for model_id in model_ids
    }


def _matrix_digest(model_return_matrix: Mapping[str, object]) -> str:
    canonical_payload = {
        "schema_version": _MATRIX_SCHEMA_VERSION,
        "model_return_matrix": {
            model_id: [
                {
                    "rebalance_date": row["rebalance_date"],
                    "net_return_hex": float(row["net_return"]).hex(),
                }
                for row in rows
            ]
            for model_id, rows in sorted(model_return_matrix.items())
        },
    }
    return _sha256(canonical_payload)


def _evidence_digest(evidence: Mapping[str, object]) -> str:
    return _sha256({key: value for key, value in evidence.items() if key != "evidence_digest"})


def _sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rejected_evidence(
    *,
    model_count: int,
    rejection_reason: str,
    date_count: int = 0,
    matrix_digest: str | None = None,
    model_return_matrix: Mapping[str, object] | None = None,
) -> SerializableEvidence:
    evidence: SerializableEvidence = {
        "evidence_schema_version": _EVIDENCE_SCHEMA_VERSION,
        "probability": None,
        "combination_count": 0,
        "fold_count": 0,
        "model_count": model_count,
        "date_count": date_count,
        "block_count": RANKING_V4_PBO_BLOCK_COUNT,
        "purge_rebalance_cohorts": RANKING_V4_PBO_PURGE_REBALANCE_COHORTS,
        "matrix_digest": matrix_digest,
        "model_return_matrix": dict(model_return_matrix or {}),
        "matrix_return_semantics": _MATRIX_RETURN_SEMANTICS,
        "method": RANKING_V4_CSCV_PBO_METHOD,
        "scope": RANKING_V4_PBO_SCOPE,
        "search_process_coverage": RANKING_V4_PBO_SEARCH_PROCESS_COVERAGE,
        "registered_model_ids": list(RANKING_V4_FROZEN_PBO_MODEL_IDS),
        "selected_model_frequencies": {},
        "relative_rank_logits": [],
        "purged_observation_counts": [],
        "fold_observation_counts": [],
        "rejection_reason": rejection_reason,
    }
    evidence["evidence_digest"] = _evidence_digest(evidence)
    return evidence


def _safe_mapping_length(value: object) -> int:
    return len(value) if isinstance(value, Mapping) else 0
