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


CSCV_PBO_METHOD = (
    "cscv_contiguous_blocks_symmetric_half_split_purged_overlap_mean_return_rank_logit_v3"
)
PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY = (
    "frozen_six_model_family_only_not_full_search_process"
)
PBO_SCOPE_PROVIDED_MODEL_FAMILY = (
    "provided_model_family_only_not_full_search_process"
)
PBO_SEARCH_PROCESS_COVERAGE = "partial"
RANKING_V3_FROZEN_PBO_MODEL_IDS = (
    "constraint_matched_baseline",
    "ranking_v3_full",
    "static_balanced",
    "trend_momentum",
    "quality_value",
    "defensive_liquidity",
)

SerializableEvidence: TypeAlias = dict[str, object]


@dataclass(frozen=True, slots=True)
class RankingV3DatedModelReturn:
    """One realized model return at a genuine rebalance date."""

    rebalance_date: date
    net_return: float


def evaluate_ranking_v3_cscv_pbo(
    model_returns: Mapping[str, Sequence[RankingV3DatedModelReturn]],
    *,
    block_count: int = 4,
    purge_rebalance_cohorts: int = 1,
) -> SerializableEvidence:
    """Estimate PBO from a real, date-aligned model-return matrix.

    The function does not construct, fill, intersect, or simulate observations.
    Every model must provide the same strictly increasing rebalance calendar.
    Invalid or insufficient input returns rejected evidence instead of an
    optimistic estimate.
    """

    normalized, rejection_reason = _normalize_matrix(model_returns)
    if rejection_reason is not None:
        return _rejected_evidence(
            model_count=_safe_mapping_length(model_returns),
            rejection_reason=rejection_reason,
            block_count=block_count,
            purge_rebalance_cohorts=purge_rebalance_cohorts,
        )

    assert normalized is not None
    model_ids, rebalance_dates, returns_by_model = normalized
    matrix_digest = _matrix_digest(model_ids, rebalance_dates, returns_by_model)
    model_count = len(model_ids)
    date_count = len(rebalance_dates)
    scope = _pbo_scope(model_ids)

    block_rejection = _validate_block_count(block_count, date_count)
    if model_count < 3:
        return _rejected_evidence(
            model_count=model_count,
            date_count=date_count,
            matrix_digest=matrix_digest,
            scope=scope,
            block_count=block_count,
            purge_rebalance_cohorts=purge_rebalance_cohorts,
            rejection_reason="at least 3 models are required",
        )
    if block_rejection is not None:
        return _rejected_evidence(
            model_count=model_count,
            date_count=date_count,
            matrix_digest=matrix_digest,
            scope=scope,
            block_count=block_count,
            purge_rebalance_cohorts=purge_rebalance_cohorts,
            rejection_reason=block_rejection,
        )
    purge_rejection = _validate_purge_rebalance_cohorts(purge_rebalance_cohorts)
    if purge_rejection is not None:
        return _rejected_evidence(
            model_count=model_count,
            date_count=date_count,
            matrix_digest=matrix_digest,
            scope=scope,
            block_count=block_count,
            purge_rebalance_cohorts=purge_rebalance_cohorts,
            rejection_reason=purge_rejection,
        )

    blocks = _contiguous_blocks(date_count, block_count)
    training_block_count = block_count // 2
    selected_model_frequencies = dict.fromkeys(model_ids, 0)
    relative_rank_logits: list[float] = []
    purged_observation_counts: list[int] = []

    for training_blocks in combinations(range(block_count), training_block_count):
        training_block_set = frozenset(training_blocks)
        training_indices, testing_indices, purged_count = _purged_split_indices(
            blocks,
            training_block_set,
            purge_rebalance_cohorts=purge_rebalance_cohorts,
        )
        if not training_indices or not testing_indices:
            return _rejected_evidence(
                model_count=model_count,
                date_count=date_count,
                matrix_digest=matrix_digest,
                scope=scope,
                block_count=block_count,
                purge_rebalance_cohorts=purge_rebalance_cohorts,
                rejection_reason="purge removed every observation from a CSCV half",
            )
        purged_observation_counts.append(purged_count)

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
    probability = below_median_count / combination_count

    return {
        "probability": probability,
        "combination_count": combination_count,
        "fold_count": combination_count,
        "model_count": model_count,
        "date_count": date_count,
        "block_count": block_count,
        "purge_rebalance_cohorts": purge_rebalance_cohorts,
        "matrix_digest": matrix_digest,
        "method": CSCV_PBO_METHOD,
        "scope": scope,
        "search_process_coverage": PBO_SEARCH_PROCESS_COVERAGE,
        "selected_model_frequencies": selected_model_frequencies,
        "relative_rank_logits": relative_rank_logits,
        "purged_observation_counts": purged_observation_counts,
        "rejection_reason": None,
    }


def _normalize_matrix(
    model_returns: Mapping[str, Sequence[RankingV3DatedModelReturn]],
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
            if not isinstance(observation, RankingV3DatedModelReturn):
                return None, (
                    f"observations for model {model_id!r} must contain "
                    "RankingV3DatedModelReturn values"
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
            return None, "all models must use exactly the same rebalance dates"
        returns_by_model[model_id] = tuple(values)

    assert reference_dates is not None
    return (model_ids, reference_dates, returns_by_model), None


def _validate_block_count(block_count: int, date_count: int) -> str | None:
    if isinstance(block_count, bool) or not isinstance(block_count, int):
        return "block_count must be an integer"
    if block_count < 4:
        return "at least 4 contiguous time blocks are required"
    if block_count % 2:
        return "block_count must be even for symmetric half splits"
    if date_count < block_count * 2:
        return "date count must provide at least 2 observations per contiguous block"
    return None


def _contiguous_blocks(date_count: int, block_count: int) -> tuple[tuple[int, ...], ...]:
    block_size, remainder = divmod(date_count, block_count)
    blocks: list[tuple[int, ...]] = []
    cursor = 0
    for block_index in range(block_count):
        current_size = block_size + (1 if block_index < remainder else 0)
        blocks.append(tuple(range(cursor, cursor + current_size)))
        cursor += current_size
    return tuple(blocks)


def _validate_purge_rebalance_cohorts(value: int) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return "purge_rebalance_cohorts must be an integer"
    if value < 1:
        return "purge_rebalance_cohorts must be positive"
    return None


def _purged_split_indices(
    blocks: Sequence[Sequence[int]],
    training_blocks: frozenset[int],
    *,
    purge_rebalance_cohorts: int,
) -> tuple[tuple[int, ...], tuple[int, ...], int]:
    assignment: dict[int, bool] = {
        index: block_index in training_blocks
        for block_index, block in enumerate(blocks)
        for index in block
    }
    purged: set[int] = set()
    ordered_indices = sorted(assignment)
    for left, right in zip(ordered_indices, ordered_indices[1:]):
        if assignment[left] == assignment[right]:
            continue
        for distance in range(purge_rebalance_cohorts):
            purged.add(left - distance)
            purged.add(right + distance)
    training = tuple(
        index for index in ordered_indices if index not in purged and assignment[index]
    )
    testing = tuple(
        index for index in ordered_indices if index not in purged and not assignment[index]
    )
    return training, testing, len(purged & set(ordered_indices))


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


def _matrix_digest(
    model_ids: Sequence[str],
    rebalance_dates: Sequence[date],
    returns_by_model: Mapping[str, Sequence[float]],
) -> str:
    canonical_payload = {
        "schema_version": "ranking-v3-real-model-return-matrix-v1",
        "rebalance_dates": [item.isoformat() for item in rebalance_dates],
        "models": [
            {
                "model_id": model_id,
                "net_returns_hex": [value.hex() for value in returns_by_model[model_id]],
            }
            for model_id in model_ids
        ],
    }
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rejected_evidence(
    *,
    model_count: int,
    rejection_reason: str,
    block_count: object,
    purge_rebalance_cohorts: object,
    date_count: int = 0,
    matrix_digest: str | None = None,
    scope: str = PBO_SCOPE_PROVIDED_MODEL_FAMILY,
) -> SerializableEvidence:
    return {
        "probability": None,
        "combination_count": 0,
        "fold_count": 0,
        "model_count": model_count,
        "date_count": date_count,
        "block_count": (
            block_count
            if isinstance(block_count, int) and not isinstance(block_count, bool)
            else None
        ),
        "purge_rebalance_cohorts": (
            purge_rebalance_cohorts
            if isinstance(purge_rebalance_cohorts, int)
            and not isinstance(purge_rebalance_cohorts, bool)
            else None
        ),
        "matrix_digest": matrix_digest,
        "method": CSCV_PBO_METHOD,
        "scope": scope,
        "search_process_coverage": PBO_SEARCH_PROCESS_COVERAGE,
        "selected_model_frequencies": {},
        "relative_rank_logits": [],
        "purged_observation_counts": [],
        "rejection_reason": rejection_reason,
    }


def _pbo_scope(model_ids: tuple[str, ...]) -> str:
    if model_ids == tuple(sorted(RANKING_V3_FROZEN_PBO_MODEL_IDS)):
        return PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY
    return PBO_SCOPE_PROVIDED_MODEL_FAMILY


def _safe_mapping_length(value: object) -> int:
    return len(value) if isinstance(value, Mapping) else 0
