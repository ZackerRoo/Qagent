from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


RANKING_V4_EXPERIMENT_REGISTRY_SCHEMA_VERSION = "ranking-v4-experiment-registry-v1"
RANKING_V4_EXPERIMENT_REGISTRY_ID = "QAGENT-RANK-V4-PRIOR-EVIDENCE-20260727"
RANKING_V3_REJECTED_EXPERIMENT_ID = "walk-forward-20260726164443-7fd44f0b"
RANKING_V3_REJECTED_CODE_REVISION = "dbd7fa0f6ec76990eca4de8325e14866dfbfe8e7"
RANKING_V3_REJECTED_DATASET_REVISION = 8939
RANKING_V3_REJECTED_SNAPSHOT_COUNT = 102
RANKING_V3_REJECTED_CANDIDATE_COVERAGE = Decimal("0.986179")
RANKING_V3_REJECTED_BENCHMARK_RETURN_PCT = Decimal("113.1521")
RANKING_V3_REJECTED_BENCHMARK_EXCESS_PCT = Decimal("-107.3842")
RANKING_V3_REJECTED_OFFICIAL_PAPER_TRADE_COUNT = 0

_FULL_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class RankingV4ExperimentRegistryError(RuntimeError):
    """Raised when frozen predecessor evidence is incomplete or inconsistent."""


class RankingV4ExperimentSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary_schema_version: str
    experiment_id: str
    model_generation: Literal["ranking_v3"]
    disposition: Literal["rejected"]
    evidence_class: Literal["exploratory_development_evidence"]
    evaluated_on: date
    source_revision: str
    dataset_revision: int
    configured_snapshot_count: int
    completed_snapshot_count: int
    candidate_outcome_coverage_ratio: Decimal
    historical_portfolio_benchmark_id: str
    historical_portfolio_benchmark_return_pct: Decimal
    benchmark_excess_return_pct: Decimal
    official_paper_trade_count: int
    failed_gates: tuple[str, ...]
    confirmatory_holm_adjusted_p_value: Decimal | None = None
    deflated_sharpe_probability: Decimal | None = None
    probability_of_backtest_overfit: Decimal | None = None
    unknown_statistics_policy: str = "null_means_unobserved_never_zero_or_passed"
    summary_digest: str

    @model_validator(mode="after")
    def validate_summary_shape(self) -> RankingV4ExperimentSummary:
        if not _FULL_GIT_REVISION.fullmatch(self.source_revision):
            raise ValueError("source_revision must be a full lowercase Git revision")
        if not _SHA256_DIGEST.fullmatch(self.summary_digest):
            raise ValueError("summary_digest must be a lowercase SHA-256 digest")
        for label, value in (
            ("candidate_outcome_coverage_ratio", self.candidate_outcome_coverage_ratio),
            (
                "historical_portfolio_benchmark_return_pct",
                self.historical_portfolio_benchmark_return_pct,
            ),
            ("benchmark_excess_return_pct", self.benchmark_excess_return_pct),
        ):
            if not value.is_finite():
                raise ValueError(f"{label} must be finite")
        for label, value in (
            ("confirmatory_holm_adjusted_p_value", self.confirmatory_holm_adjusted_p_value),
            ("deflated_sharpe_probability", self.deflated_sharpe_probability),
            ("probability_of_backtest_overfit", self.probability_of_backtest_overfit),
        ):
            if value is not None and (not value.is_finite() or not Decimal("0") <= value <= 1):
                raise ValueError(f"{label} must be null or between zero and one")
        return self

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"summary_digest"})

    def require_valid(self) -> None:
        _validate_summary(self)


class RankingV4ExperimentRegistry(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str
    registry_id: str
    frozen_on: date
    predecessor_summaries: tuple[RankingV4ExperimentSummary, ...]
    v4_registration_state: Literal["preregistered_code_not_yet_frozen"]
    registry_digest: str

    def stable_payload(self) -> dict[str, object]:
        return _registry_payload(
            schema_version=self.schema_version,
            registry_id=self.registry_id,
            frozen_on=self.frozen_on,
            predecessor_summaries=self.predecessor_summaries,
            v4_registration_state=self.v4_registration_state,
        )

    def require_valid(self) -> None:
        _validate_registry(self)


def build_ranking_v3_rejected_summary() -> RankingV4ExperimentSummary:
    payload = _ranking_v3_rejected_summary_payload()
    summary = RankingV4ExperimentSummary(
        **payload,
        summary_digest=_digest(payload),
    )
    summary.require_valid()
    return summary


def _ranking_v3_rejected_summary_payload() -> dict[str, object]:
    return {
        "summary_schema_version": "ranking-v4-predecessor-summary-v1",
        "experiment_id": RANKING_V3_REJECTED_EXPERIMENT_ID,
        "model_generation": "ranking_v3",
        "disposition": "rejected",
        "evidence_class": "exploratory_development_evidence",
        "evaluated_on": "2026-07-26",
        "source_revision": RANKING_V3_REJECTED_CODE_REVISION,
        "dataset_revision": RANKING_V3_REJECTED_DATASET_REVISION,
        "configured_snapshot_count": RANKING_V3_REJECTED_SNAPSHOT_COUNT,
        "completed_snapshot_count": RANKING_V3_REJECTED_SNAPSHOT_COUNT,
        "candidate_outcome_coverage_ratio": str(RANKING_V3_REJECTED_CANDIDATE_COVERAGE),
        "historical_portfolio_benchmark_id": "CN:EQUAL_WEIGHT_ELIGIBLE",
        "historical_portfolio_benchmark_return_pct": str(RANKING_V3_REJECTED_BENCHMARK_RETURN_PCT),
        "benchmark_excess_return_pct": str(RANKING_V3_REJECTED_BENCHMARK_EXCESS_PCT),
        "official_paper_trade_count": RANKING_V3_REJECTED_OFFICIAL_PAPER_TRADE_COUNT,
        "failed_gates": ["positive_benchmark_excess"],
        "confirmatory_holm_adjusted_p_value": None,
        "deflated_sharpe_probability": None,
        "probability_of_backtest_overfit": None,
        "unknown_statistics_policy": "null_means_unobserved_never_zero_or_passed",
    }


def build_ranking_v4_experiment_registry(
    *,
    predecessor_summaries: tuple[RankingV4ExperimentSummary, ...] | None = None,
) -> RankingV4ExperimentRegistry:
    summaries = (
        (build_ranking_v3_rejected_summary(),)
        if predecessor_summaries is None
        else tuple(
            sorted(
                predecessor_summaries,
                key=lambda item: (item.evaluated_on, item.experiment_id),
            )
        )
    )
    payload = _registry_payload(
        schema_version=RANKING_V4_EXPERIMENT_REGISTRY_SCHEMA_VERSION,
        registry_id=RANKING_V4_EXPERIMENT_REGISTRY_ID,
        frozen_on=date(2026, 7, 27),
        predecessor_summaries=summaries,
        v4_registration_state="preregistered_code_not_yet_frozen",
    )
    registry = RankingV4ExperimentRegistry(
        **payload,
        registry_digest=_digest(payload),
    )
    registry.require_valid()
    return registry


def ranking_v4_experiment_registry_digest_is_valid(
    registry: RankingV4ExperimentRegistry,
) -> bool:
    try:
        registry.require_valid()
    except (RankingV4ExperimentRegistryError, RuntimeError, ValueError):
        return False
    return registry.registry_digest == _digest(registry.stable_payload())


def _registry_payload(
    *,
    schema_version: str,
    registry_id: str,
    frozen_on: date,
    predecessor_summaries: tuple[RankingV4ExperimentSummary, ...],
    v4_registration_state: str,
) -> dict[str, object]:
    summaries = sorted(
        predecessor_summaries,
        key=lambda item: (item.evaluated_on, item.experiment_id),
    )
    return {
        "schema_version": schema_version,
        "registry_id": registry_id,
        "frozen_on": frozen_on.isoformat(),
        "predecessor_summaries": [item.model_dump(mode="json") for item in summaries],
        "v4_registration_state": v4_registration_state,
    }


def _validate_summary(summary: RankingV4ExperimentSummary) -> None:
    if summary.summary_digest != _digest(summary.stable_payload()):
        raise RankingV4ExperimentRegistryError("experiment summary digest mismatch")
    expected = _ranking_v3_rejected_summary_payload()
    if summary.stable_payload() != expected:
        raise RankingV4ExperimentRegistryError(
            "Ranking V3 rejected evidence cannot be rewritten by Ranking V4"
        )


def _validate_registry(registry: RankingV4ExperimentRegistry) -> None:
    if registry.schema_version != RANKING_V4_EXPERIMENT_REGISTRY_SCHEMA_VERSION:
        raise RankingV4ExperimentRegistryError("unsupported Ranking V4 registry schema")
    if registry.registry_id != RANKING_V4_EXPERIMENT_REGISTRY_ID:
        raise RankingV4ExperimentRegistryError("unexpected Ranking V4 registry id")
    if registry.v4_registration_state != "preregistered_code_not_yet_frozen":
        raise RankingV4ExperimentRegistryError(
            "V4 cannot claim confirmatory status before its code is frozen"
        )
    if len(registry.predecessor_summaries) != 1:
        raise RankingV4ExperimentRegistryError(
            "registry must contain exactly one frozen V3 predecessor summary"
        )
    for summary in registry.predecessor_summaries:
        summary.require_valid()
    if registry.registry_digest != _digest(registry.stable_payload()):
        raise RankingV4ExperimentRegistryError("experiment registry digest mismatch")


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
