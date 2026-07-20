from datetime import date

import pytest

from qagent.features import build_feature_snapshot
from qagent.monitoring.drift import (
    DriftPolicy,
    DriftSnapshotMetadata,
    DriftStatus,
    compare_feature_snapshots,
    population_stability_index,
)


def _snapshot(
    *,
    version: str = "factor-v2",
    revision: int = 1,
    values: list[float | None],
):
    raw_scores = {
        f"CN:{index:06d}": {
            "momentum": value,
            "quality": None if value is None else value / 10.0,
        }
        for index, value in enumerate(values)
    }
    cross_sectional_scores = {
        instrument_id: {
            "momentum": index / max(1, len(values) - 1),
            "quality": index / max(1, len(values) - 1),
        }
        for index, instrument_id in enumerate(raw_scores)
    }
    return build_feature_snapshot(
        as_of=date(2026, 7, min(28, 10 + revision)),
        feature_set_version=version,
        dataset_revision=revision,
        raw_scores=raw_scores,
        cross_sectional_scores=cross_sectional_scores,
    )


def _metadata(
    ids: list[str],
    *,
    top_n: list[str] | None = None,
    provider: str = "akshare",
) -> DriftSnapshotMetadata:
    return DriftSnapshotMetadata(
        sources={instrument_id: provider for instrument_id in ids},
        flags={instrument_id: () for instrument_id in ids},
        top_n=top_n or ids[:5],
        industries={instrument_id: f"industry-{index % 5}" for index, instrument_id in enumerate(ids)},
        rejection_reasons={instrument_id: () for instrument_id in ids},
    )


def test_identical_same_version_snapshots_are_stable_across_dataset_revisions():
    reference = _snapshot(revision=1, values=[float(index) for index in range(20)])
    current = _snapshot(revision=2, values=[float(index) for index in range(20)])
    ids = list(reference.raw_scores)
    metadata = _metadata(ids)

    report = compare_feature_snapshots(
        reference,
        current,
        reference_metadata=metadata,
        current_metadata=metadata,
        policy=DriftPolicy(top_n=5),
    )

    assert report.status is DriftStatus.STABLE
    assert report.reference_dataset_revision == "1"
    assert report.current_dataset_revision == "2"
    assert report.coverage["momentum"].coverage_delta == 0.0
    assert report.coverage["momentum"].missing_rate_delta == 0.0
    assert report.continuous_psi["momentum"].psi == 0.0
    assert report.source_distribution.max_abs_delta == 0.0
    assert report.flag_distribution.max_abs_delta == 0.0
    assert report.top_n_jaccard.jaccard == 1.0
    assert report.industry_concentration.hhi_delta == 0.0
    assert report.rejection_reason_distribution.max_abs_delta == 0.0
    assert report.auto_adjust_weights is False
    assert report.weight_action == "none"


def test_monitor_detects_coverage_psi_category_top_n_and_concentration_drift():
    reference = _snapshot(revision=1, values=[float(index) for index in range(20)])
    current = _snapshot(
        revision=2,
        values=[100.0 + index for index in range(12)] + [None] * 8,
    )
    ids = list(reference.raw_scores)
    reference_top = ids[:5]
    current_top = ids[5:10]
    reference_metadata = _metadata(ids, top_n=reference_top)
    current_metadata = DriftSnapshotMetadata(
        sources={
            instrument_id: "baostock" if index < 10 else "akshare"
            for index, instrument_id in enumerate(ids)
        },
        flags={
            instrument_id: ("stale_source",) if index < 10 else ()
            for index, instrument_id in enumerate(ids)
        },
        top_n=current_top,
        industries={instrument_id: "technology" for instrument_id in ids},
        rejection_reasons={
            instrument_id: ("missing_fundamental",) if index < 10 else ()
            for index, instrument_id in enumerate(ids)
        },
    )

    report = compare_feature_snapshots(
        reference,
        current,
        reference_metadata=reference_metadata,
        current_metadata=current_metadata,
        policy=DriftPolicy(top_n=5),
    )

    assert report.status is DriftStatus.DRIFT
    assert report.coverage["momentum"].current_coverage == 0.6
    assert report.coverage["momentum"].coverage_delta == -0.4
    assert report.coverage["momentum"].missing_rate_delta == 0.4
    assert report.coverage["momentum"].status is DriftStatus.DRIFT
    assert report.continuous_psi["momentum"].psi is not None
    assert report.continuous_psi["momentum"].psi > 0.25
    assert report.continuous_psi["momentum"].status is DriftStatus.DRIFT
    assert report.source_distribution.status is DriftStatus.DRIFT
    assert report.flag_distribution.status is DriftStatus.DRIFT
    assert report.top_n_jaccard.jaccard == 0.0
    assert report.top_n_jaccard.status is DriftStatus.DRIFT
    assert report.industry_concentration.reference_hhi == pytest.approx(0.2)
    assert report.industry_concentration.current_hhi == 1.0
    assert report.industry_concentration.status is DriftStatus.DRIFT
    assert report.rejection_reason_distribution.status is DriftStatus.DRIFT
    assert report.auto_adjust_weights is False
    assert report.weight_action == "none"


def test_version_mismatch_returns_insufficient_without_computing_drift():
    reference = _snapshot(version="factor-v1", revision=1, values=[1.0] * 10)
    current = _snapshot(version="factor-v2", revision=2, values=[100.0] * 10)

    report = compare_feature_snapshots(reference, current)

    assert report.status is DriftStatus.INSUFFICIENT
    assert "feature_set_version mismatch" in report.reason
    assert report.coverage == {}
    assert report.continuous_psi == {}
    assert report.insufficient_metrics == ("feature_set_version",)
    assert report.auto_adjust_weights is False
    assert report.weight_action == "none"


def test_missing_optional_metadata_only_marks_its_metrics_insufficient():
    reference = _snapshot(revision=1, values=[float(index) for index in range(10)])
    current = _snapshot(revision=2, values=[float(index) for index in range(10)])

    report = compare_feature_snapshots(reference, current, policy=DriftPolicy(top_n=5))

    assert report.status is DriftStatus.STABLE
    assert report.source_distribution.status is DriftStatus.INSUFFICIENT
    assert report.flag_distribution.status is DriftStatus.INSUFFICIENT
    assert report.top_n_jaccard.status is DriftStatus.STABLE
    assert report.industry_concentration.status is DriftStatus.INSUFFICIENT
    assert report.rejection_reason_distribution.status is DriftStatus.INSUFFICIENT
    assert "source_distribution" in report.insufficient_metrics
    assert report.continuous_psi["momentum"].status is DriftStatus.STABLE


def test_psi_is_distribution_based_and_reports_small_samples_as_insufficient():
    values = [float(index) for index in range(20)]

    assert population_stability_index(values, list(reversed(values))) == 0.0
    assert population_stability_index([1.0], [2.0], min_samples=2) is None


def test_report_is_json_persistence_friendly_and_contains_no_weight_changes():
    reference = _snapshot(revision=1, values=[float(index) for index in range(10)])
    current = _snapshot(revision=2, values=[float(index) for index in range(10)])

    payload = compare_feature_snapshots(reference, current).model_dump(mode="json")

    assert payload["status"] == "stable"
    assert payload["auto_adjust_weights"] is False
    assert payload["weight_action"] == "none"
    assert "weight_changes" not in payload
