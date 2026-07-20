from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from hashlib import sha256
import json
from math import isfinite
from typing import Any

from qagent.features.models import FeatureSnapshot


def build_feature_snapshot(
    *,
    as_of: date | datetime,
    feature_set_version: str,
    dataset_revision: int | str,
    raw_scores: Mapping[str, Mapping[str, float | None]],
    cross_sectional_scores: Mapping[str, Mapping[str, float]],
    universe_ids: Iterable[str] | None = None,
    input_metadata: Mapping[str, object] | None = None,
) -> FeatureSnapshot:
    """Build a canonical snapshot whose digests do not depend on input ordering."""

    universe = sorted(
        set(universe_ids or ()) | set(raw_scores) | set(cross_sectional_scores)
    )
    feature_ids = sorted(
        {
            feature_id
            for scores in [*raw_scores.values(), *cross_sectional_scores.values()]
            for feature_id in scores
        }
    )
    normalized_raw = {
        instrument_id: {
            feature_id: _finite_float(raw_scores.get(instrument_id, {}).get(feature_id))
            for feature_id in feature_ids
        }
        for instrument_id in universe
    }
    normalized_cross_sectional = {
        instrument_id: {
            feature_id: value
            for feature_id in feature_ids
            if (
                value := _finite_float(
                    cross_sectional_scores.get(instrument_id, {}).get(feature_id)
                )
            )
            is not None
        }
        for instrument_id in universe
    }
    coverage = _coverage(normalized_raw, feature_ids, len(universe))
    universe_digest = _digest(universe)
    input_digest = _digest(
        {
            "as_of": as_of.isoformat(),
            "feature_set_version": feature_set_version,
            "dataset_revision": dataset_revision,
            "universe_digest": universe_digest,
            "raw_scores": normalized_raw,
            "input_metadata": input_metadata or {},
        }
    )
    return FeatureSnapshot(
        as_of=as_of,
        feature_set_version=feature_set_version,
        dataset_revision=dataset_revision,
        universe_digest=universe_digest,
        input_digest=input_digest,
        coverage=coverage,
        raw_scores=normalized_raw,
        cross_sectional_scores=normalized_cross_sectional,
    )


def feature_snapshot_data_health(snapshot: FeatureSnapshot) -> dict[str, str]:
    overall_coverage = snapshot.coverage.get("overall", 0.0)
    return {
        "feature_snapshot_as_of": snapshot.as_of.isoformat(),
        "feature_set_version": snapshot.feature_set_version,
        "feature_dataset_revision": str(snapshot.dataset_revision),
        "feature_universe_digest": snapshot.universe_digest,
        "feature_input_digest": snapshot.input_digest,
        "feature_snapshot_coverage": f"{overall_coverage:.4f}",
        "feature_snapshot_universe_size": str(len(snapshot.raw_scores)),
    }


def _coverage(
    raw_scores: Mapping[str, Mapping[str, float | None]],
    feature_ids: list[str],
    universe_size: int,
) -> dict[str, float]:
    if universe_size == 0 or not feature_ids:
        return {"overall": 0.0}
    coverage = {
        feature_id: round(
            sum(raw_scores[instrument_id][feature_id] is not None for instrument_id in raw_scores)
            / universe_size,
            6,
        )
        for feature_id in feature_ids
    }
    coverage["overall"] = round(sum(coverage.values()) / len(feature_ids), 6)
    return coverage


def _finite_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(result):
        return None
    return 0.0 if result == 0 else result


def _digest(value: object) -> str:
    payload = json.dumps(
        _canonical_value(value),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical_value(value: object) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonical_value(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
        return items
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float):
        return _finite_float(value)
    return value
