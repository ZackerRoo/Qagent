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
from typing import Literal, TypeAlias

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
RANKING_V4_PBO_MINIMUM_DATES_PER_HALF = _PROTOCOL.thresholds.minimum_rebalance_dates

_V41_MATRIX_SCHEMA_VERSION = "ranking-v4.1-real-model-return-matrix-v1"
_V41_EVIDENCE_SCHEMA_VERSION = "ranking-v4.1-cscv-pbo-evidence-v1"
_V42_MATRIX_SCHEMA_VERSION = "ranking-v4.2-real-model-return-matrix-v1"
_V42_EVIDENCE_SCHEMA_VERSION = "ranking-v4.2-cscv-pbo-evidence-v1"
_V43_MATRIX_SCHEMA_VERSION = "ranking-v4.3-real-model-return-matrix-v1"
_V43_EVIDENCE_SCHEMA_VERSION = "ranking-v4.3-cscv-pbo-evidence-v1"
_V44_MATRIX_SCHEMA_VERSION = "ranking-v4.4-real-model-return-matrix-v1"
_V44_EVIDENCE_SCHEMA_VERSION = "ranking-v4.4-cscv-pbo-evidence-v1"
_V45_MATRIX_SCHEMA_VERSION = "ranking-v4.5-real-model-return-matrix-v1"
_V45_EVIDENCE_SCHEMA_VERSION = "ranking-v4.5-cscv-pbo-evidence-v1"
_MATRIX_SCHEMA_VERSION = _V45_MATRIX_SCHEMA_VERSION
_EVIDENCE_SCHEMA_VERSION = _V45_EVIDENCE_SCHEMA_VERSION
_MATRIX_RETURN_SEMANTICS = (
    "caller_supplied_common_rebalance_calendar_with_invalid_or_missing_model_dates_"
    "explicitly_filled_as_cash_zero_return_before_evaluation"
)
_V44_MATRIX_RETURN_SEMANTICS = (
    "union_of_caller_supplied_model_rebalance_dates_with_numeric_zero_preserved_as_"
    "observed_cash_zero_and_unavailable_returns_serialized_as_null_then_evaluated_as_"
    "cash_zero_only_after_each_model_meets_frozen_date_coverage"
)
RANKING_V44_PBO_MINIMUM_MODEL_DATE_COVERAGE_RATIO = _STATISTICS.pbo_date_coverage_threshold
RANKING_V44_PBO_REMAINDER_POLICY = (
    "drop_latest_tail_dates_to_largest_chronological_prefix_divisible_by_block_count"
)

SerializableEvidence: TypeAlias = dict[str, object]
ProtocolVersion: TypeAlias = Literal["4.1", "4.2", "4.3", "4.4", "4.5"]


@dataclass(frozen=True, slots=True)
class _PBOConfig:
    version: ProtocolVersion
    method: str
    scope: str
    model_ids: tuple[str, ...]
    block_count: int
    purge_rebalance_cohorts: int
    minimum_date_count: int
    minimum_dates_per_half: int | None
    matrix_schema_version: str
    evidence_schema_version: str
    matrix_return_semantics: str
    minimum_model_date_coverage_ratio: Decimal | None
    equal_block_remainder_policy: str | None


@dataclass(frozen=True, slots=True)
class _NormalizedMatrix:
    model_ids: tuple[str, ...]
    rebalance_dates: tuple[date, ...]
    returns_by_model: dict[str, tuple[float, ...]]
    availability_by_model: dict[str, tuple[bool, ...]]


@dataclass(frozen=True, slots=True)
class RankingV4DatedModelReturn:
    """One realized net model return on a genuine common rebalance date."""

    rebalance_date: date
    net_return: float | None


def evaluate_ranking_v4_cscv_pbo(
    model_returns: Mapping[str, Sequence[RankingV4DatedModelReturn]],
    *,
    protocol_version: ProtocolVersion = "4.5",
) -> SerializableEvidence:
    """Evaluate frozen-family Ranking V4 PBO from a real-date model matrix.

    V4.1-V4.3 retain the historical complete-calendar contract. V4.4 aligns the
    union of supplied dates, records unavailable returns separately from
    observed cash zeroes, and fails closed unless every model has frozen 95%
    date coverage.
    """

    config = _pbo_config(protocol_version)
    normalized, rejection_reason = _normalize_matrix(
        model_returns,
        allow_unavailable=config.version in {"4.4", "4.5"},
    )
    if normalized is None:
        return _rejected_evidence(
            config=config,
            model_count=_safe_mapping_length(model_returns),
            rejection_reason=rejection_reason or "model-return matrix is invalid",
        )

    model_ids = normalized.model_ids
    rebalance_dates = normalized.rebalance_dates
    returns_by_model = normalized.returns_by_model
    availability_by_model = normalized.availability_by_model
    serialized_matrix = _serialize_matrix(
        model_ids,
        rebalance_dates,
        returns_by_model,
        availability_by_model=(
            availability_by_model if config.version in {"4.4", "4.5"} else None
        ),
    )
    matrix_digest = _matrix_digest(
        serialized_matrix,
        schema_version=config.matrix_schema_version,
    )
    model_count = len(model_ids)
    date_count = len(rebalance_dates)
    coverage_evidence = _model_date_coverage_evidence(
        rebalance_dates,
        returns_by_model,
        availability_by_model,
    )
    blocks, dropped_indices = _block_partition(
        date_count,
        config.block_count,
        equal_sized=config.equal_block_remainder_policy is not None,
    )
    partition_evidence = _partition_evidence(
        config,
        rebalance_dates,
        blocks,
        dropped_indices,
    )
    v44_evidence = (
        {
            "minimum_model_date_coverage_ratio": float(config.minimum_model_date_coverage_ratio),
            "model_date_coverage": coverage_evidence,
            **partition_evidence,
        }
        if config.minimum_model_date_coverage_ratio is not None
        else {}
    )

    if model_ids != tuple(sorted(config.model_ids)):
        return _rejected_evidence(
            config=config,
            model_count=model_count,
            date_count=date_count,
            matrix_digest=matrix_digest,
            model_return_matrix=serialized_matrix,
            rejection_reason=(
                "model family must match the frozen Ranking V4 eight-model family exactly"
            ),
            extra_evidence=v44_evidence,
        )
    if date_count < config.minimum_date_count:
        return _rejected_evidence(
            config=config,
            model_count=model_count,
            date_count=date_count,
            matrix_digest=matrix_digest,
            model_return_matrix=serialized_matrix,
            rejection_reason=(
                "date count must meet the Ranking V4 minimum of "
                f"{config.minimum_date_count} genuine rebalance dates"
            ),
            extra_evidence=v44_evidence,
        )
    if config.minimum_model_date_coverage_ratio is not None:
        insufficient_models = [
            model_id
            for model_id in model_ids
            if (
                Decimal(coverage_evidence[model_id]["available_date_count"]) / Decimal(date_count)
                < config.minimum_model_date_coverage_ratio
            )
        ]
        if insufficient_models:
            coverage_summary = ", ".join(
                f"{model_id}={coverage_evidence[model_id]['coverage_ratio']:.2%}"
                for model_id in insufficient_models
            )
            return _rejected_evidence(
                config=config,
                model_count=model_count,
                date_count=date_count,
                matrix_digest=matrix_digest,
                model_return_matrix=serialized_matrix,
                rejection_reason=(
                    "per-model date coverage is below frozen "
                    f"{config.minimum_model_date_coverage_ratio:.0%}: "
                    f"{coverage_summary}"
                ),
                extra_evidence=v44_evidence,
            )

    training_block_count = config.block_count // 2
    selected_model_frequencies = dict.fromkeys(model_ids, 0)
    relative_rank_logits: list[float] = []
    purged_observation_counts: list[int] = []
    fold_observation_counts: list[dict[str, int]] = []

    for training_blocks in combinations(
        range(config.block_count),
        training_block_count,
    ):
        training_indices, testing_indices, purged_count = _purged_split_indices(
            blocks,
            frozenset(training_blocks),
            purge_rebalance_cohorts=config.purge_rebalance_cohorts,
        )
        if not training_indices or not testing_indices:
            return _rejected_evidence(
                config=config,
                model_count=model_count,
                date_count=date_count,
                matrix_digest=matrix_digest,
                model_return_matrix=serialized_matrix,
                rejection_reason="purge removed every observation from a CSCV half",
                extra_evidence=v44_evidence,
            )
        if config.minimum_dates_per_half is not None and (
            len(training_indices) < config.minimum_dates_per_half
            or len(testing_indices) < config.minimum_dates_per_half
        ):
            return _rejected_evidence(
                config=config,
                model_count=model_count,
                date_count=date_count,
                matrix_digest=matrix_digest,
                model_return_matrix=serialized_matrix,
                rejection_reason=(
                    "purged CSCV fold has fewer than "
                    f"{config.minimum_dates_per_half} genuine rebalance dates "
                    "in a half-sample"
                ),
                extra_evidence=v44_evidence,
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
        "evidence_schema_version": config.evidence_schema_version,
        "probability": below_median_count / combination_count,
        "combination_count": combination_count,
        "fold_count": combination_count,
        "model_count": model_count,
        "date_count": date_count,
        "block_count": config.block_count,
        "purge_rebalance_cohorts": config.purge_rebalance_cohorts,
        "matrix_digest": matrix_digest,
        "model_return_matrix": serialized_matrix,
        "matrix_return_semantics": config.matrix_return_semantics,
        "method": config.method,
        "scope": config.scope,
        "search_process_coverage": RANKING_V4_PBO_SEARCH_PROCESS_COVERAGE,
        "registered_model_ids": list(config.model_ids),
        "selected_model_frequencies": selected_model_frequencies,
        "relative_rank_logits": relative_rank_logits,
        "purged_observation_counts": purged_observation_counts,
        "fold_observation_counts": fold_observation_counts,
        "rejection_reason": None,
    }
    evidence.update(v44_evidence)
    if config.minimum_dates_per_half is not None:
        evidence["minimum_dates_per_half"] = config.minimum_dates_per_half
    evidence["evidence_digest"] = _evidence_digest(evidence)
    return evidence


def _normalize_matrix(
    model_returns: Mapping[str, Sequence[RankingV4DatedModelReturn]],
    *,
    allow_unavailable: bool,
) -> tuple[_NormalizedMatrix | None, str | None]:
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
    parsed_by_model: dict[str, dict[date, float | None]] = {}
    union_dates: set[date] = set()

    for model_id in model_ids:
        observations = model_returns[model_id]
        if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
            return None, f"observations for model {model_id!r} must be a sequence"
        if not observations and not allow_unavailable:
            return None, f"observations for model {model_id!r} must not be empty"

        dates: list[date] = []
        values_by_date: dict[date, float | None] = {}
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
            if value is None and allow_unavailable:
                normalized_value = None
            elif isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
                return None, f"model {model_id!r} contains a non-numeric return"
            else:
                normalized_value = float(value)
                if not math.isfinite(normalized_value):
                    return None, f"model {model_id!r} contains a non-finite return"

            dates.append(rebalance_date)
            values_by_date[rebalance_date] = normalized_value
            previous_date = rebalance_date

        current_dates = tuple(dates)
        if allow_unavailable:
            union_dates.update(current_dates)
        elif reference_dates is None:
            reference_dates = current_dates
        elif current_dates != reference_dates:
            return None, (
                "all eight models must use exactly the same genuine rebalance dates; "
                "the evaluator does not intersect or fill calendars"
            )
        parsed_by_model[model_id] = values_by_date

    if allow_unavailable:
        reference_dates = tuple(sorted(union_dates))
    assert reference_dates is not None

    returns_by_model: dict[str, tuple[float, ...]] = {}
    availability_by_model: dict[str, tuple[bool, ...]] = {}
    for model_id in model_ids:
        values_by_date = parsed_by_model[model_id]
        availability = tuple(
            rebalance_date in values_by_date and values_by_date[rebalance_date] is not None
            for rebalance_date in reference_dates
        )
        returns_by_model[model_id] = tuple(
            float(values_by_date[rebalance_date]) if is_available else 0.0
            for rebalance_date, is_available in zip(reference_dates, availability)
        )
        availability_by_model[model_id] = availability

    return (
        _NormalizedMatrix(
            model_ids=model_ids,
            rebalance_dates=reference_dates,
            returns_by_model=returns_by_model,
            availability_by_model=availability_by_model,
        ),
        None,
    )


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


def _block_partition(
    date_count: int,
    block_count: int,
    *,
    equal_sized: bool,
) -> tuple[tuple[tuple[int, ...], ...], tuple[int, ...]]:
    if not equal_sized:
        return _contiguous_blocks(date_count, block_count), ()

    block_size, remainder = divmod(date_count, block_count)
    evaluated_date_count = date_count - remainder
    blocks = tuple(
        tuple(range(block_index * block_size, (block_index + 1) * block_size))
        for block_index in range(block_count)
    )
    return blocks, tuple(range(evaluated_date_count, date_count))


def _model_date_coverage_evidence(
    rebalance_dates: Sequence[date],
    returns_by_model: Mapping[str, Sequence[float]],
    availability_by_model: Mapping[str, Sequence[bool]],
) -> dict[str, dict[str, object]]:
    date_count = len(rebalance_dates)
    evidence: dict[str, dict[str, object]] = {}
    for model_id in sorted(returns_by_model):
        availability = availability_by_model[model_id]
        available_date_count = sum(availability)
        evidence[model_id] = {
            "expected_date_count": date_count,
            "available_date_count": available_date_count,
            "missing_date_count": date_count - available_date_count,
            "observed_cash_zero_date_count": sum(
                is_available and returns_by_model[model_id][index] == 0.0
                for index, is_available in enumerate(availability)
            ),
            "coverage_ratio": (available_date_count / date_count if date_count else 0.0),
            "missing_rebalance_dates": [
                rebalance_dates[index].isoformat()
                for index, is_available in enumerate(availability)
                if not is_available
            ],
        }
    return evidence


def _partition_evidence(
    config: _PBOConfig,
    rebalance_dates: Sequence[date],
    blocks: Sequence[Sequence[int]],
    dropped_indices: Sequence[int],
) -> dict[str, object]:
    if config.equal_block_remainder_policy is None:
        return {}
    return {
        "evaluated_date_count": sum(len(block) for block in blocks),
        "block_size": len(blocks[0]) if blocks else 0,
        "block_observation_counts": [len(block) for block in blocks],
        "block_remainder_policy": config.equal_block_remainder_policy,
        "dropped_date_count": len(dropped_indices),
        "dropped_rebalance_dates": [
            rebalance_dates[index].isoformat() for index in dropped_indices
        ],
    }


def _purged_split_indices(
    blocks: Sequence[Sequence[int]],
    training_blocks: frozenset[int],
    *,
    purge_rebalance_cohorts: int,
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
        for distance in range(purge_rebalance_cohorts):
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
    *,
    availability_by_model: Mapping[str, Sequence[bool]] | None = None,
) -> dict[str, list[dict[str, object]]]:
    return {
        model_id: [
            {
                "rebalance_date": rebalance_date.isoformat(),
                "net_return": (
                    returns_by_model[model_id][index]
                    if availability_by_model is None or availability_by_model[model_id][index]
                    else None
                ),
            }
            for index, rebalance_date in enumerate(rebalance_dates)
        ]
        for model_id in model_ids
    }


def _matrix_digest(
    model_return_matrix: Mapping[str, object],
    *,
    schema_version: str = _MATRIX_SCHEMA_VERSION,
) -> str:
    canonical_payload = {
        "schema_version": schema_version,
        "model_return_matrix": {
            model_id: [
                {
                    "rebalance_date": row["rebalance_date"],
                    "net_return_hex": (
                        float(row["net_return"]).hex() if row["net_return"] is not None else None
                    ),
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
    config: _PBOConfig,
    model_count: int,
    rejection_reason: str,
    date_count: int = 0,
    matrix_digest: str | None = None,
    model_return_matrix: Mapping[str, object] | None = None,
    extra_evidence: Mapping[str, object] | None = None,
) -> SerializableEvidence:
    evidence: SerializableEvidence = {
        "evidence_schema_version": config.evidence_schema_version,
        "probability": None,
        "combination_count": 0,
        "fold_count": 0,
        "model_count": model_count,
        "date_count": date_count,
        "block_count": config.block_count,
        "purge_rebalance_cohorts": config.purge_rebalance_cohorts,
        "matrix_digest": matrix_digest,
        "model_return_matrix": dict(model_return_matrix or {}),
        "matrix_return_semantics": config.matrix_return_semantics,
        "method": config.method,
        "scope": config.scope,
        "search_process_coverage": RANKING_V4_PBO_SEARCH_PROCESS_COVERAGE,
        "registered_model_ids": list(config.model_ids),
        "selected_model_frequencies": {},
        "relative_rank_logits": [],
        "purged_observation_counts": [],
        "fold_observation_counts": [],
        "rejection_reason": rejection_reason,
    }
    evidence.update(extra_evidence or {})
    if config.minimum_dates_per_half is not None:
        evidence["minimum_dates_per_half"] = config.minimum_dates_per_half
    evidence["evidence_digest"] = _evidence_digest(evidence)
    return evidence


def _safe_mapping_length(value: object) -> int:
    return len(value) if isinstance(value, Mapping) else 0


def _pbo_config(version: ProtocolVersion) -> _PBOConfig:
    protocol = build_ranking_v4_protocol(version=version)
    statistics = protocol.statistics_definition
    return _PBOConfig(
        version=version,
        method=statistics.pbo_method,
        scope=statistics.pbo_scope,
        model_ids=statistics.pbo_model_ids,
        block_count=statistics.pbo_block_count,
        purge_rebalance_cohorts=statistics.pbo_purge_rebalance_cohorts,
        minimum_date_count=protocol.thresholds.minimum_rebalance_dates,
        minimum_dates_per_half=(
            protocol.thresholds.minimum_rebalance_dates if version != "4.1" else None
        ),
        matrix_schema_version=(
            _V41_MATRIX_SCHEMA_VERSION
            if version == "4.1"
            else (
                _V42_MATRIX_SCHEMA_VERSION
                if version == "4.2"
                else (
                    _V43_MATRIX_SCHEMA_VERSION
                    if version == "4.3"
                    else (
                        _V44_MATRIX_SCHEMA_VERSION
                        if version == "4.4"
                        else _V45_MATRIX_SCHEMA_VERSION
                    )
                )
            )
        ),
        evidence_schema_version=(
            _V41_EVIDENCE_SCHEMA_VERSION
            if version == "4.1"
            else (
                _V42_EVIDENCE_SCHEMA_VERSION
                if version == "4.2"
                else (
                    _V43_EVIDENCE_SCHEMA_VERSION
                    if version == "4.3"
                    else (
                        _V44_EVIDENCE_SCHEMA_VERSION
                        if version == "4.4"
                        else _V45_EVIDENCE_SCHEMA_VERSION
                    )
                )
            )
        ),
        matrix_return_semantics=(
            _V44_MATRIX_RETURN_SEMANTICS
            if version in {"4.4", "4.5"}
            else _MATRIX_RETURN_SEMANTICS
        ),
        minimum_model_date_coverage_ratio=(
            RANKING_V44_PBO_MINIMUM_MODEL_DATE_COVERAGE_RATIO
            if version in {"4.4", "4.5"}
            else None
        ),
        equal_block_remainder_policy=(
            RANKING_V44_PBO_REMAINDER_POLICY if version in {"4.4", "4.5"} else None
        ),
    )
