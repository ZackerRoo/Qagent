from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from math import floor, isfinite, log
from typing import Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from qagent.features.models import FeatureSnapshot


class DriftStatus(StrEnum):
    STABLE = "stable"
    WATCH = "watch"
    DRIFT = "drift"
    INSUFFICIENT = "insufficient"


class DriftPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    coverage_watch_delta: float = Field(default=0.05, ge=0.0, le=1.0)
    coverage_drift_delta: float = Field(default=0.10, ge=0.0, le=1.0)
    psi_watch: float = Field(default=0.10, ge=0.0)
    psi_drift: float = Field(default=0.25, ge=0.0)
    distribution_watch_delta: float = Field(default=0.10, ge=0.0, le=1.0)
    distribution_drift_delta: float = Field(default=0.20, ge=0.0, le=1.0)
    top_n_watch_jaccard: float = Field(default=0.70, ge=0.0, le=1.0)
    top_n_drift_jaccard: float = Field(default=0.50, ge=0.0, le=1.0)
    industry_watch_hhi_delta: float = Field(default=0.05, ge=0.0, le=1.0)
    industry_drift_hhi_delta: float = Field(default=0.10, ge=0.0, le=1.0)
    top_n: int = Field(default=20, ge=1)
    psi_bins: int = Field(default=10, ge=2, le=50)
    min_psi_samples: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> Self:
        increasing = (
            (
                self.coverage_watch_delta,
                self.coverage_drift_delta,
                "coverage deltas",
            ),
            (self.psi_watch, self.psi_drift, "PSI thresholds"),
            (
                self.distribution_watch_delta,
                self.distribution_drift_delta,
                "distribution deltas",
            ),
            (
                self.industry_watch_hhi_delta,
                self.industry_drift_hhi_delta,
                "industry HHI deltas",
            ),
        )
        for watch, drift, label in increasing:
            if watch > drift:
                raise ValueError(f"watch threshold must not exceed drift threshold for {label}")
        if self.top_n_drift_jaccard > self.top_n_watch_jaccard:
            raise ValueError("top-N drift Jaccard must not exceed the watch threshold")
        return self


SourceValue = str | Mapping[str, str]


class DriftSnapshotMetadata(BaseModel):
    """Optional operational dimensions not present in the canonical feature snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    sources: dict[str, str | dict[str, str]] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("sources", "source_by_instrument", "feature_sources"),
    )
    flags: dict[str, tuple[str, ...]] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("flags", "flags_by_instrument"),
    )
    top_n: tuple[str, ...] = Field(
        default_factory=tuple,
        validation_alias=AliasChoices("top_n", "top_ids", "top_n_ids"),
    )
    industries: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("industries", "industry_by_instrument"),
    )
    rejection_reasons: dict[str, tuple[str, ...]] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "rejection_reasons",
            "rejection_reasons_by_instrument",
        ),
    )

    @field_validator("sources", mode="before")
    @classmethod
    def normalize_sources(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("sources must be a mapping")
        normalized: dict[str, str | dict[str, str]] = {}
        for instrument_id, source in value.items():
            if isinstance(source, Mapping):
                normalized[str(instrument_id)] = {
                    str(feature_id): str(provider).strip()
                    for feature_id, provider in source.items()
                    if provider is not None and str(provider).strip()
                }
            elif source is not None and str(source).strip():
                normalized[str(instrument_id)] = str(source).strip()
        return normalized

    @field_validator("flags", "rejection_reasons", mode="before")
    @classmethod
    def normalize_multilabel_values(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("multi-label metadata must be a mapping")
        return {
            str(instrument_id): _normalize_labels(labels)
            for instrument_id, labels in value.items()
        }

    @field_validator("top_n", mode="before")
    @classmethod
    def normalize_top_n(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise ValueError("top_n must be an iterable of instrument identifiers")
        return tuple(
            dict.fromkeys(
                str(item).strip()
                for item in value
                if item is not None and str(item).strip()
            )
        )

    @field_validator("industries", mode="before")
    @classmethod
    def normalize_industries(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("industries must be a mapping")
        return {
            str(instrument_id): str(industry).strip()
            for instrument_id, industry in value.items()
            if industry is not None and str(industry).strip()
        }


class CoverageDriftMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    feature_id: str
    reference_coverage: float = Field(ge=0.0, le=1.0)
    current_coverage: float = Field(ge=0.0, le=1.0)
    coverage_delta: float = Field(ge=-1.0, le=1.0)
    reference_missing_rate: float = Field(ge=0.0, le=1.0)
    current_missing_rate: float = Field(ge=0.0, le=1.0)
    missing_rate_delta: float = Field(ge=-1.0, le=1.0)
    status: DriftStatus


class ContinuousPSIMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    feature_id: str
    reference_count: int = Field(ge=0)
    current_count: int = Field(ge=0)
    psi: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    status: DriftStatus


class DistributionDriftMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_size: int = Field(ge=0)
    current_size: int = Field(ge=0)
    reference_distribution: dict[str, float] = Field(default_factory=dict)
    current_distribution: dict[str, float] = Field(default_factory=dict)
    max_abs_delta: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    status: DriftStatus


class TopNJaccardMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_top_n: int = Field(ge=1)
    reference_ids: tuple[str, ...] = ()
    current_ids: tuple[str, ...] = ()
    intersection_count: int = Field(default=0, ge=0)
    union_count: int = Field(default=0, ge=0)
    jaccard: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    status: DriftStatus


class IndustryConcentrationMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_hhi: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    current_hhi: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    hhi_delta: float | None = Field(default=None, ge=-1.0, le=1.0, allow_inf_nan=False)
    reference_top_share: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    current_top_share: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    reference_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    current_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    status: DriftStatus


class DriftReport(BaseModel):
    """Audit-only drift result. It deliberately cannot carry a weight mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DriftStatus
    reason: str
    reference_version: str
    current_version: str
    reference_dataset_revision: str
    current_dataset_revision: str
    coverage: dict[str, CoverageDriftMetric] = Field(default_factory=dict)
    continuous_psi: dict[str, ContinuousPSIMetric] = Field(default_factory=dict)
    source_distribution: DistributionDriftMetric
    flag_distribution: DistributionDriftMetric
    top_n_jaccard: TopNJaccardMetric
    industry_concentration: IndustryConcentrationMetric
    rejection_reason_distribution: DistributionDriftMetric
    insufficient_metrics: tuple[str, ...] = ()
    auto_adjust_weights: Literal[False] = False
    weight_action: Literal["none"] = "none"


def compare_feature_snapshots(
    reference: FeatureSnapshot | Mapping[str, object],
    current: FeatureSnapshot | Mapping[str, object],
    *,
    reference_metadata: DriftSnapshotMetadata | Mapping[str, object] | None = None,
    current_metadata: DriftSnapshotMetadata | Mapping[str, object] | None = None,
    policy: DriftPolicy | Mapping[str, object] | None = None,
) -> DriftReport:
    """Compare two immutable feature snapshots without changing model configuration."""

    baseline = _feature_snapshot(reference)
    candidate = _feature_snapshot(current)
    selected_policy = _drift_policy(policy)
    baseline_metadata = _metadata(reference_metadata)
    candidate_metadata = _metadata(current_metadata)

    if baseline.feature_set_version != candidate.feature_set_version:
        return _insufficient_version_report(baseline, candidate, selected_policy)

    feature_ids = sorted(_feature_ids(baseline) | _feature_ids(candidate))
    baseline_coverage = _coverage_rates(baseline, feature_ids)
    candidate_coverage = _coverage_rates(candidate, feature_ids)
    coverage = {
        feature_id: _coverage_metric(
            feature_id,
            baseline_coverage[feature_id],
            candidate_coverage[feature_id],
            selected_policy,
        )
        for feature_id in [*feature_ids, "overall"]
    }
    continuous_psi = {
        feature_id: _continuous_metric(
            feature_id,
            _feature_values(baseline, feature_id),
            _feature_values(candidate, feature_id),
            selected_policy,
        )
        for feature_id in feature_ids
    }

    source_distribution = _source_distribution_metric(
        baseline,
        candidate,
        baseline_metadata,
        candidate_metadata,
        selected_policy,
    )
    flag_distribution = _multilabel_distribution_metric(
        baseline,
        candidate,
        baseline_metadata.flags,
        candidate_metadata.flags,
        selected_policy,
    )
    baseline_top = _top_ids(baseline, baseline_metadata, selected_policy.top_n)
    candidate_top = _top_ids(candidate, candidate_metadata, selected_policy.top_n)
    top_n_jaccard = _top_n_metric(
        baseline_top,
        candidate_top,
        selected_policy,
    )
    industry_concentration = _industry_metric(
        baseline_top,
        candidate_top,
        baseline_metadata.industries,
        candidate_metadata.industries,
        selected_policy,
    )
    rejection_reason_distribution = _multilabel_distribution_metric(
        baseline,
        candidate,
        baseline_metadata.rejection_reasons,
        candidate_metadata.rejection_reasons,
        selected_policy,
    )

    labelled_statuses: list[tuple[str, DriftStatus]] = [
        *((f"coverage.{key}", metric.status) for key, metric in coverage.items()),
        *((f"continuous_psi.{key}", metric.status) for key, metric in continuous_psi.items()),
        ("source_distribution", source_distribution.status),
        ("flag_distribution", flag_distribution.status),
        ("top_n_jaccard", top_n_jaccard.status),
        ("industry_concentration", industry_concentration.status),
        ("rejection_reason_distribution", rejection_reason_distribution.status),
    ]
    overall_status, reason = _overall_status(labelled_statuses)
    insufficient_metrics = tuple(
        label for label, status in labelled_statuses if status is DriftStatus.INSUFFICIENT
    )
    return DriftReport(
        status=overall_status,
        reason=reason,
        reference_version=baseline.feature_set_version,
        current_version=candidate.feature_set_version,
        reference_dataset_revision=str(baseline.dataset_revision),
        current_dataset_revision=str(candidate.dataset_revision),
        coverage=coverage,
        continuous_psi=continuous_psi,
        source_distribution=source_distribution,
        flag_distribution=flag_distribution,
        top_n_jaccard=top_n_jaccard,
        industry_concentration=industry_concentration,
        rejection_reason_distribution=rejection_reason_distribution,
        insufficient_metrics=insufficient_metrics,
    )


def population_stability_index(
    reference: Sequence[float],
    current: Sequence[float],
    *,
    bins: int = 10,
    min_samples: int = 1,
) -> float | None:
    """Return PSI using reference quantile bins, or None when evidence is insufficient."""

    if bins < 2:
        raise ValueError("bins must be at least two")
    if min_samples < 1:
        raise ValueError("min_samples must be positive")
    baseline = [value for item in reference if (value := _finite_float(item)) is not None]
    candidate = [value for item in current if (value := _finite_float(item)) is not None]
    if len(baseline) < min_samples or len(candidate) < min_samples:
        return None
    if baseline == candidate:
        return 0.0

    cut_points = _quantile_cut_points(baseline, bins)
    if not cut_points:
        combined = sorted(set([*baseline, *candidate]))
        cut_points = [
            (left + right) / 2.0
            for left, right in zip(combined, combined[1:], strict=False)
        ][: bins - 1]
    baseline_counts = _histogram(baseline, cut_points)
    candidate_counts = _histogram(candidate, cut_points)
    epsilon = 1e-6
    result = 0.0
    for baseline_count, candidate_count in zip(
        baseline_counts,
        candidate_counts,
        strict=True,
    ):
        baseline_rate = max(baseline_count / len(baseline), epsilon)
        candidate_rate = max(candidate_count / len(candidate), epsilon)
        result += (candidate_rate - baseline_rate) * log(candidate_rate / baseline_rate)
    return round(max(0.0, result), 6)


def jaccard_similarity(reference: Iterable[str], current: Iterable[str]) -> float | None:
    baseline = set(reference)
    candidate = set(current)
    union = baseline | candidate
    if not union:
        return None
    return len(baseline & candidate) / len(union)


def herfindahl_index(labels: Iterable[str]) -> float | None:
    values = [str(label).strip() for label in labels if str(label).strip()]
    if not values:
        return None
    counts = Counter(values)
    size = len(values)
    return sum((count / size) ** 2 for count in counts.values())


def _insufficient_version_report(
    reference: FeatureSnapshot,
    current: FeatureSnapshot,
    policy: DriftPolicy,
) -> DriftReport:
    distribution = _empty_distribution_metric()
    return DriftReport(
        status=DriftStatus.INSUFFICIENT,
        reason=(
            "feature_set_version mismatch: "
            f"{reference.feature_set_version!r} != {current.feature_set_version!r}"
        ),
        reference_version=reference.feature_set_version,
        current_version=current.feature_set_version,
        reference_dataset_revision=str(reference.dataset_revision),
        current_dataset_revision=str(current.dataset_revision),
        source_distribution=distribution,
        flag_distribution=distribution.model_copy(deep=True),
        top_n_jaccard=TopNJaccardMetric(
            requested_top_n=policy.top_n,
            status=DriftStatus.INSUFFICIENT,
        ),
        industry_concentration=IndustryConcentrationMetric(
            status=DriftStatus.INSUFFICIENT,
        ),
        rejection_reason_distribution=distribution.model_copy(deep=True),
        insufficient_metrics=("feature_set_version",),
    )


def _coverage_rates(
    snapshot: FeatureSnapshot,
    feature_ids: Sequence[str],
) -> dict[str, float]:
    universe_size = len(snapshot.raw_scores)
    rates = {
        feature_id: (
            sum(
                _finite_float(scores.get(feature_id)) is not None
                for scores in snapshot.raw_scores.values()
            )
            / universe_size
            if universe_size
            else 0.0
        )
        for feature_id in feature_ids
    }
    rates["overall"] = sum(rates.values()) / len(rates) if rates else 0.0
    return rates


def _coverage_metric(
    feature_id: str,
    reference_rate: float,
    current_rate: float,
    policy: DriftPolicy,
) -> CoverageDriftMetric:
    delta = current_rate - reference_rate
    return CoverageDriftMetric(
        feature_id=feature_id,
        reference_coverage=round(reference_rate, 6),
        current_coverage=round(current_rate, 6),
        coverage_delta=round(delta, 6),
        reference_missing_rate=round(1.0 - reference_rate, 6),
        current_missing_rate=round(1.0 - current_rate, 6),
        missing_rate_delta=round(-delta, 6),
        status=_high_is_bad_status(
            abs(delta),
            policy.coverage_watch_delta,
            policy.coverage_drift_delta,
        ),
    )


def _continuous_metric(
    feature_id: str,
    reference_values: list[float],
    current_values: list[float],
    policy: DriftPolicy,
) -> ContinuousPSIMetric:
    psi = population_stability_index(
        reference_values,
        current_values,
        bins=policy.psi_bins,
        min_samples=policy.min_psi_samples,
    )
    return ContinuousPSIMetric(
        feature_id=feature_id,
        reference_count=len(reference_values),
        current_count=len(current_values),
        psi=psi,
        status=(
            DriftStatus.INSUFFICIENT
            if psi is None
            else _high_is_bad_status(psi, policy.psi_watch, policy.psi_drift)
        ),
    )


def _source_distribution_metric(
    reference: FeatureSnapshot,
    current: FeatureSnapshot,
    reference_metadata: DriftSnapshotMetadata,
    current_metadata: DriftSnapshotMetadata,
    policy: DriftPolicy,
) -> DistributionDriftMetric:
    if not reference_metadata.sources or not current_metadata.sources:
        return _empty_distribution_metric()
    reference_labels = _source_labels(reference, reference_metadata.sources)
    current_labels = _source_labels(current, current_metadata.sources)
    return _single_label_distribution_metric(reference_labels, current_labels, policy)


def _source_labels(
    snapshot: FeatureSnapshot,
    sources: Mapping[str, SourceValue],
) -> list[str]:
    labels: list[str] = []
    instrument_ids = sorted(set(snapshot.raw_scores) | set(sources))
    for instrument_id in instrument_ids:
        source = sources.get(instrument_id)
        if source is None:
            labels.append("__missing__")
        elif isinstance(source, Mapping):
            providers = [str(provider).strip() for provider in source.values() if str(provider).strip()]
            labels.extend(providers or ["__missing__"])
        else:
            labels.append(str(source).strip() or "__missing__")
    return labels


def _single_label_distribution_metric(
    reference_labels: Sequence[str],
    current_labels: Sequence[str],
    policy: DriftPolicy,
) -> DistributionDriftMetric:
    if not reference_labels or not current_labels:
        return _empty_distribution_metric()
    reference_distribution = _category_rates(reference_labels, len(reference_labels))
    current_distribution = _category_rates(current_labels, len(current_labels))
    return _distribution_result(
        len(reference_labels),
        len(current_labels),
        reference_distribution,
        current_distribution,
        policy,
    )


def _multilabel_distribution_metric(
    reference: FeatureSnapshot,
    current: FeatureSnapshot,
    reference_labels: Mapping[str, Sequence[str]],
    current_labels: Mapping[str, Sequence[str]],
    policy: DriftPolicy,
) -> DistributionDriftMetric:
    if not reference_labels or not current_labels:
        return _empty_distribution_metric()
    reference_ids = sorted(set(reference.raw_scores) | set(reference_labels))
    current_ids = sorted(set(current.raw_scores) | set(current_labels))
    if not reference_ids or not current_ids:
        return _empty_distribution_metric()
    baseline = _multilabel_rates(reference_ids, reference_labels)
    candidate = _multilabel_rates(current_ids, current_labels)
    return _distribution_result(
        len(reference_ids),
        len(current_ids),
        baseline,
        candidate,
        policy,
    )


def _multilabel_rates(
    instrument_ids: Sequence[str],
    labels_by_instrument: Mapping[str, Sequence[str]],
) -> dict[str, float]:
    counts: Counter[str] = Counter()
    for instrument_id in instrument_ids:
        labels = set(_normalize_labels(labels_by_instrument.get(instrument_id, ())))
        if labels:
            counts.update(labels)
        else:
            counts["__none__"] += 1
    return {
        label: round(count / len(instrument_ids), 6)
        for label, count in sorted(counts.items())
    }


def _category_rates(labels: Sequence[str], size: int) -> dict[str, float]:
    counts = Counter(labels)
    return {label: round(count / size, 6) for label, count in sorted(counts.items())}


def _distribution_result(
    reference_size: int,
    current_size: int,
    reference_distribution: Mapping[str, float],
    current_distribution: Mapping[str, float],
    policy: DriftPolicy,
) -> DistributionDriftMetric:
    categories = set(reference_distribution) | set(current_distribution)
    max_delta = max(
        (
            abs(current_distribution.get(category, 0.0) - reference_distribution.get(category, 0.0))
            for category in categories
        ),
        default=0.0,
    )
    return DistributionDriftMetric(
        reference_size=reference_size,
        current_size=current_size,
        reference_distribution=dict(reference_distribution),
        current_distribution=dict(current_distribution),
        max_abs_delta=round(max_delta, 6),
        status=_high_is_bad_status(
            max_delta,
            policy.distribution_watch_delta,
            policy.distribution_drift_delta,
        ),
    )


def _top_n_metric(
    reference_ids: tuple[str, ...],
    current_ids: tuple[str, ...],
    policy: DriftPolicy,
) -> TopNJaccardMetric:
    similarity = jaccard_similarity(reference_ids, current_ids)
    reference_set = set(reference_ids)
    current_set = set(current_ids)
    return TopNJaccardMetric(
        requested_top_n=policy.top_n,
        reference_ids=reference_ids,
        current_ids=current_ids,
        intersection_count=len(reference_set & current_set),
        union_count=len(reference_set | current_set),
        jaccard=round(similarity, 6) if similarity is not None else None,
        status=(
            DriftStatus.INSUFFICIENT
            if similarity is None
            else _low_is_bad_status(
                similarity,
                policy.top_n_watch_jaccard,
                policy.top_n_drift_jaccard,
            )
        ),
    )


def _top_ids(
    snapshot: FeatureSnapshot,
    metadata: DriftSnapshotMetadata,
    top_n: int,
) -> tuple[str, ...]:
    if metadata.top_n:
        return metadata.top_n[:top_n]
    scored: list[tuple[float, str]] = []
    for instrument_id, scores in snapshot.cross_sectional_scores.items():
        values = [value for item in scores.values() if (value := _finite_float(item)) is not None]
        if values:
            scored.append((sum(values) / len(values), instrument_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(instrument_id for _, instrument_id in scored[:top_n])


def _industry_metric(
    reference_ids: tuple[str, ...],
    current_ids: tuple[str, ...],
    reference_industries: Mapping[str, str],
    current_industries: Mapping[str, str],
    policy: DriftPolicy,
) -> IndustryConcentrationMetric:
    reference_labels = [reference_industries[item] for item in reference_ids if item in reference_industries]
    current_labels = [current_industries[item] for item in current_ids if item in current_industries]
    reference_hhi = herfindahl_index(reference_labels)
    current_hhi = herfindahl_index(current_labels)
    if reference_hhi is None or current_hhi is None:
        return IndustryConcentrationMetric(
            reference_hhi=reference_hhi,
            current_hhi=current_hhi,
            reference_top_share=_top_share(reference_labels),
            current_top_share=_top_share(current_labels),
            reference_coverage=_known_rate(len(reference_labels), len(reference_ids)),
            current_coverage=_known_rate(len(current_labels), len(current_ids)),
            status=DriftStatus.INSUFFICIENT,
        )
    delta = current_hhi - reference_hhi
    return IndustryConcentrationMetric(
        reference_hhi=round(reference_hhi, 6),
        current_hhi=round(current_hhi, 6),
        hhi_delta=round(delta, 6),
        reference_top_share=_top_share(reference_labels),
        current_top_share=_top_share(current_labels),
        reference_coverage=_known_rate(len(reference_labels), len(reference_ids)),
        current_coverage=_known_rate(len(current_labels), len(current_ids)),
        status=_high_is_bad_status(
            abs(delta),
            policy.industry_watch_hhi_delta,
            policy.industry_drift_hhi_delta,
        ),
    )


def _top_share(labels: Sequence[str]) -> float | None:
    if not labels:
        return None
    return round(max(Counter(labels).values()) / len(labels), 6)


def _known_rate(known: int, total: int) -> float:
    return round(known / total, 6) if total else 0.0


def _overall_status(
    labelled_statuses: Sequence[tuple[str, DriftStatus]],
) -> tuple[DriftStatus, str]:
    for target in (DriftStatus.DRIFT, DriftStatus.WATCH):
        labels = [label for label, status in labelled_statuses if status is target]
        if labels:
            return target, f"{target.value} detected in: {', '.join(labels)}"
    available = [label for label, status in labelled_statuses if status is DriftStatus.STABLE]
    if available:
        return DriftStatus.STABLE, "available same-version drift metrics are stable"
    return DriftStatus.INSUFFICIENT, "no comparable same-version drift metrics are available"


def _high_is_bad_status(value: float, watch: float, drift: float) -> DriftStatus:
    if value >= drift:
        return DriftStatus.DRIFT
    if value >= watch:
        return DriftStatus.WATCH
    return DriftStatus.STABLE


def _low_is_bad_status(value: float, watch: float, drift: float) -> DriftStatus:
    if value <= drift:
        return DriftStatus.DRIFT
    if value <= watch:
        return DriftStatus.WATCH
    return DriftStatus.STABLE


def _empty_distribution_metric() -> DistributionDriftMetric:
    return DistributionDriftMetric(
        reference_size=0,
        current_size=0,
        status=DriftStatus.INSUFFICIENT,
    )


def _feature_values(snapshot: FeatureSnapshot, feature_id: str) -> list[float]:
    return [
        value
        for scores in snapshot.raw_scores.values()
        if (value := _finite_float(scores.get(feature_id))) is not None
    ]


def _feature_ids(snapshot: FeatureSnapshot) -> set[str]:
    return {
        feature_id
        for scores in snapshot.raw_scores.values()
        for feature_id in scores
    } | {feature_id for feature_id in snapshot.coverage if feature_id != "overall"}


def _quantile_cut_points(values: Sequence[float], bins: int) -> list[float]:
    ordered = sorted(values)
    cut_points: list[float] = []
    for index in range(1, bins):
        value = _quantile(ordered, index / bins)
        if not cut_points or value > cut_points[-1]:
            cut_points.append(value)
    if cut_points and cut_points[-1] >= ordered[-1]:
        cut_points.pop()
    return cut_points


def _quantile(ordered: Sequence[float], quantile: float) -> float:
    position = (len(ordered) - 1) * quantile
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _histogram(values: Sequence[float], cut_points: Sequence[float]) -> list[int]:
    counts = [0] * (len(cut_points) + 1)
    for value in values:
        counts[bisect_right(cut_points, value)] += 1
    return counts


def _normalize_labels(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items: Iterable[object] = (value,)
    elif isinstance(value, Iterable):
        items = value
    else:
        items = (value,)
    return tuple(
        sorted(
            set(
                str(item).strip()
                for item in items
                if item is not None and str(item).strip()
            )
        )
    )


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _feature_snapshot(snapshot: FeatureSnapshot | Mapping[str, object]) -> FeatureSnapshot:
    if isinstance(snapshot, FeatureSnapshot):
        return snapshot
    return FeatureSnapshot.model_validate(snapshot)


def _metadata(
    metadata: DriftSnapshotMetadata | Mapping[str, object] | None,
) -> DriftSnapshotMetadata:
    if metadata is None:
        return DriftSnapshotMetadata()
    if isinstance(metadata, DriftSnapshotMetadata):
        return metadata
    return DriftSnapshotMetadata.model_validate(metadata)


def _drift_policy(policy: DriftPolicy | Mapping[str, object] | None) -> DriftPolicy:
    if policy is None:
        return DriftPolicy()
    return policy if isinstance(policy, DriftPolicy) else DriftPolicy.model_validate(policy)


compare_snapshot_drift = compare_feature_snapshots
compare_feature_snapshot_drift = compare_feature_snapshots
monitor_feature_drift = compare_feature_snapshots
FeatureDriftReport = DriftReport
DriftThresholds = DriftPolicy
FeatureSnapshotMetadata = DriftSnapshotMetadata


__all__ = [
    "ContinuousPSIMetric",
    "CoverageDriftMetric",
    "DistributionDriftMetric",
    "DriftPolicy",
    "DriftReport",
    "DriftSnapshotMetadata",
    "DriftStatus",
    "DriftThresholds",
    "FeatureDriftReport",
    "FeatureSnapshotMetadata",
    "IndustryConcentrationMetric",
    "TopNJaccardMetric",
    "compare_feature_snapshots",
    "compare_feature_snapshot_drift",
    "compare_snapshot_drift",
    "herfindahl_index",
    "jaccard_similarity",
    "monitor_feature_drift",
    "population_stability_index",
]
