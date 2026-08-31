from __future__ import annotations

from datetime import date
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from qagent.factors.research_contract import FACTOR_RESEARCH_VERSION
from qagent.research.factor_candidate_queue import (
    FactorCandidate,
    build_factor_candidate_queue,
)


FACTOR_CANDIDATE_COVERAGE_SCHEMA = "factor-candidate-coverage-manifest-v1"


class FactorCoverageSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_mode: str = Field(min_length=1)
    dataset_revision: int = Field(gt=0)
    source_artifact_id: str = Field(min_length=1)
    feature_set_version: str = FACTOR_RESEARCH_VERSION
    temporal_policy: Literal["point_in_time_as_of_signal_date"] = (
        "point_in_time_as_of_signal_date"
    )
    research_start_date: date | None = None
    research_end_date: date | None = None
    inventory_stock_count: int | None = Field(default=None, ge=0)
    adjusted_bar_rows: int | None = Field(default=None, ge=0)
    read_policy: Literal["sqlite_mode_ro_immutable", "caller_supplied_frame"] = (
        "caller_supplied_frame"
    )


class FactorFieldCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: str
    non_null_samples: int = Field(ge=0)
    non_null_rate: float = Field(ge=0.0, le=1.0)


class FactorCandidateCoverageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    required_features: tuple[str, ...]
    coverage_status: Literal[
        "verified",
        "no_samples",
        "missing_required_fields",
        "future_capability",
    ]
    missing_features: tuple[str, ...] = ()
    sample_count: int = Field(ge=0)
    signal_sessions: int = Field(ge=0)
    covered_signal_sessions: int = Field(ge=0)
    joint_non_null_samples: int = Field(ge=0)
    joint_non_null_rate: float = Field(ge=0.0, le=1.0)
    first_signal_date: date | None = None
    last_signal_date: date | None = None
    covered_first_signal_date: date | None = None
    covered_last_signal_date: date | None = None
    field_coverage: list[FactorFieldCoverage] = Field(default_factory=list)
    experiment_start_allowed: Literal[False] = False
    decision_weight: Literal[False] = False
    production_ranking_effect: Literal["none"] = "none"
    paper_order_effect: Literal["none"] = "none"


class FactorCandidateCoverageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = FACTOR_CANDIDATE_COVERAGE_SCHEMA
    source: FactorCoverageSource
    candidates: list[FactorCandidateCoverageEvidence]
    warnings: list[str] = Field(default_factory=list)
    experiment_start_allowed: Literal[False] = False
    decision_weight: Literal[False] = False
    production_ranking_effect: Literal["none"] = "none"
    paper_order_effect: Literal["none"] = "none"


def audit_factor_candidate_coverage(
    frame: pd.DataFrame,
    source: FactorCoverageSource,
) -> FactorCandidateCoverageManifest:
    """Measure candidate coverage on an already-frozen point-in-time dataset.

    This is a pure, read-only audit. Coverage evidence never enables an experiment
    or changes any production or paper-trading decision surface.
    """

    if "signal_date" not in frame.columns:
        raise ValueError("factor candidate coverage requires signal_date")
    signal_dates = pd.to_datetime(frame["signal_date"], errors="coerce").dt.date
    valid_signal_dates = signal_dates.dropna()
    sample_count = len(frame)
    signal_sessions = int(valid_signal_dates.nunique())
    first_signal_date = min(valid_signal_dates) if not valid_signal_dates.empty else None
    last_signal_date = max(valid_signal_dates) if not valid_signal_dates.empty else None

    queue = build_factor_candidate_queue()
    evidence = [
        _audit_candidate(
            frame,
            signal_dates,
            candidate,
            sample_count=sample_count,
            signal_sessions=signal_sessions,
            first_signal_date=first_signal_date,
            last_signal_date=last_signal_date,
        )
        for candidate in queue.candidates
    ]
    return FactorCandidateCoverageManifest(
        source=source,
        candidates=evidence,
        warnings=[
            "覆盖证据仅描述冻结历史样本，不授权启动实验。",
            "任一必需字段缺失时该候选 fail-closed。",
            "催化剂仍是 future capability，不因历史因子覆盖而启用。",
        ],
    )


def _audit_candidate(
    frame: pd.DataFrame,
    signal_dates: pd.Series,
    candidate: FactorCandidate,
    *,
    sample_count: int,
    signal_sessions: int,
    first_signal_date: date | None,
    last_signal_date: date | None,
) -> FactorCandidateCoverageEvidence:
    if candidate.state == "future_capability":
        return FactorCandidateCoverageEvidence(
            candidate_id=candidate.candidate_id,
            required_features=candidate.required_features,
            coverage_status="future_capability",
            missing_features=candidate.required_features,
            sample_count=sample_count,
            signal_sessions=signal_sessions,
            covered_signal_sessions=0,
            joint_non_null_samples=0,
            joint_non_null_rate=0.0,
            first_signal_date=first_signal_date,
            last_signal_date=last_signal_date,
        )

    missing = tuple(feature for feature in candidate.required_features if feature not in frame)
    if missing:
        return FactorCandidateCoverageEvidence(
            candidate_id=candidate.candidate_id,
            required_features=candidate.required_features,
            coverage_status="missing_required_fields",
            missing_features=missing,
            sample_count=sample_count,
            signal_sessions=signal_sessions,
            covered_signal_sessions=0,
            joint_non_null_samples=0,
            joint_non_null_rate=0.0,
            first_signal_date=first_signal_date,
            last_signal_date=last_signal_date,
        )

    valid_by_feature: dict[str, pd.Series] = {}
    field_coverage: list[FactorFieldCoverage] = []
    for feature in candidate.required_features:
        numeric = pd.to_numeric(frame[feature], errors="coerce")
        valid = pd.Series(np.isfinite(numeric.to_numpy(dtype="float64")), index=frame.index)
        valid_by_feature[feature] = valid
        non_null_samples = int(valid.sum())
        field_coverage.append(
            FactorFieldCoverage(
                feature=feature,
                non_null_samples=non_null_samples,
                non_null_rate=_rate(non_null_samples, sample_count),
            )
        )
    joint = pd.concat(valid_by_feature.values(), axis=1).all(axis=1)
    joint_samples = int(joint.sum())
    covered_dates = signal_dates[joint].dropna()
    return FactorCandidateCoverageEvidence(
        candidate_id=candidate.candidate_id,
        required_features=candidate.required_features,
        coverage_status="verified" if joint_samples else "no_samples",
        sample_count=sample_count,
        signal_sessions=signal_sessions,
        covered_signal_sessions=int(covered_dates.nunique()),
        joint_non_null_samples=joint_samples,
        joint_non_null_rate=_rate(joint_samples, sample_count),
        first_signal_date=first_signal_date,
        last_signal_date=last_signal_date,
        covered_first_signal_date=min(covered_dates) if not covered_dates.empty else None,
        covered_last_signal_date=max(covered_dates) if not covered_dates.empty else None,
        field_coverage=field_coverage,
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
