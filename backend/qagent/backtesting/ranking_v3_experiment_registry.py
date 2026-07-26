from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


EXPERIMENT_REGISTRY_SCHEMA_VERSION = "ranking-v3-experiment-registry-v2"
EXPERIMENT_REGISTRY_ID = "QAGENT-RANK-V3.1-PRIOR-ATTEMPTS-20260726"
EXPECTED_PRIOR_ATTEMPT_COUNT = 15
_FULL_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class RankingV3ExperimentRegistryError(RuntimeError):
    """Raised when experiment-history evidence is incomplete or inconsistent."""


class RankingV3ExperimentAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempt_id: str
    hypothesis_key: str
    registered_on: date
    source_revision: str
    evidence_uri: str
    disposition: Literal["rejected", "superseded", "retained_for_comparison"]
    counts_for_deflated_sharpe: bool = True
    counts_for_holm_family: bool = True
    confirmatory_p_value: float | None = None
    p_value_method: str | None = None
    oos_sharpe: float | None = None
    oos_sharpe_method: str | None = None
    result_artifact_digest: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> RankingV3ExperimentAttempt:
        if not self.attempt_id.strip() or not self.hypothesis_key.strip():
            raise ValueError("experiment attempt identity must not be blank")
        if not _FULL_GIT_REVISION.fullmatch(self.source_revision):
            raise ValueError("source_revision must be a full lowercase Git revision")
        if self.evidence_uri != f"git:{self.source_revision}":
            raise ValueError("evidence_uri must point to the registered Git revision")

        p_value = self.confirmatory_p_value
        oos_sharpe = self.oos_sharpe
        if p_value is None and self.p_value_method is not None:
            raise ValueError("p-value provenance cannot be present without an observed p-value")
        if oos_sharpe is None and self.oos_sharpe_method is not None:
            raise ValueError("Sharpe provenance cannot be present without an observed OOS Sharpe")
        if p_value is None and oos_sharpe is None:
            if self.result_artifact_digest is not None:
                raise ValueError("result provenance cannot be present without an observed result")
            return self

        if self.result_artifact_digest is None or not _SHA256_DIGEST.fullmatch(
            self.result_artifact_digest
        ):
            raise ValueError("observed results require a SHA-256 result artifact digest")

        if p_value is not None:
            if not self.counts_for_holm_family:
                raise ValueError("observed p-values cannot be excluded from the Holm family")
            if not math.isfinite(p_value) or not 0.0 <= p_value <= 1.0:
                raise ValueError("confirmatory_p_value must be finite and between 0 and 1")
            if not self.p_value_method or not self.p_value_method.strip():
                raise ValueError("observed p-values require a named test method")

        if oos_sharpe is not None:
            if not self.counts_for_deflated_sharpe:
                raise ValueError(
                    "observed OOS Sharpe results cannot be excluded from Deflated Sharpe"
                )
            if not math.isfinite(oos_sharpe):
                raise ValueError("oos_sharpe must be finite")
            if not self.oos_sharpe_method or not self.oos_sharpe_method.strip():
                raise ValueError("observed OOS Sharpe requires a named estimation method")
        return self


class RankingV3ExperimentRegistry(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str
    registry_id: str
    frozen_on: date
    expected_prior_attempt_count: int
    attempts: tuple[RankingV3ExperimentAttempt, ...]
    registry_digest: str

    @property
    def prior_attempt_count(self) -> int:
        return sum(item.counts_for_deflated_sharpe for item in self.attempts)

    @property
    def holm_prior_hypothesis_count(self) -> int:
        return sum(item.counts_for_holm_family for item in self.attempts)

    @property
    def unobserved_holm_p_value_count(self) -> int:
        return self.holm_prior_hypothesis_count - len(self.confirmatory_holm_p_values())

    @property
    def observed_deflated_sharpe_result_count(self) -> int:
        return len(self.deflated_sharpe_oos_results())

    @property
    def unobserved_deflated_sharpe_result_count(self) -> int:
        return self.prior_attempt_count - self.observed_deflated_sharpe_result_count

    def confirmatory_holm_p_values(self) -> tuple[float, ...]:
        return tuple(
            attempt.confirmatory_p_value
            for attempt in self.attempts
            if attempt.counts_for_holm_family and attempt.confirmatory_p_value is not None
        )

    def deflated_sharpe_oos_results(self) -> tuple[float, ...]:
        return tuple(
            attempt.oos_sharpe
            for attempt in self.attempts
            if attempt.counts_for_deflated_sharpe and attempt.oos_sharpe is not None
        )

    def complete_deflated_sharpe_oos_results(self) -> tuple[float, ...] | None:
        results = self.deflated_sharpe_oos_results()
        return results if len(results) == self.prior_attempt_count else None

    def stable_payload(self) -> dict[str, object]:
        return _registry_payload(
            schema_version=self.schema_version,
            registry_id=self.registry_id,
            frozen_on=self.frozen_on,
            expected_prior_attempt_count=self.expected_prior_attempt_count,
            attempts=self.attempts,
        )

    def require_valid(self) -> None:
        _validate_registry(self)


_REGISTERED_PRIOR_ATTEMPTS = (
    RankingV3ExperimentAttempt(
        attempt_id="market-regime-strategy-mix-v1",
        hypothesis_key="market_trend_gate_and_strategy_mix",
        registered_on=date(2026, 7, 24),
        source_revision="70814305d04577fda8cb1dab69b6d57bda104778",
        evidence_uri="git:70814305d04577fda8cb1dab69b6d57bda104778",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="recommendation-gate-replay-v1",
        hypothesis_key="honor_recommendation_gates_in_walk_forward",
        registered_on=date(2026, 7, 24),
        source_revision="c11bd0609006b1548126c6d0759f6aead47422e9",
        evidence_uri="git:c11bd0609006b1548126c6d0759f6aead47422e9",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="no-chase-execution-v1",
        hypothesis_key="reject_chased_backtest_entries",
        registered_on=date(2026, 7, 24),
        source_revision="c51ae7c7f58006b812930ebb1e5c810aa209d4ab",
        evidence_uri="git:c51ae7c7f58006b812930ebb1e5c810aa209d4ab",
        disposition="retained_for_comparison",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="dynamic-reranking-v1",
        hypothesis_key="dynamic_reranking_promotion",
        registered_on=date(2026, 7, 25),
        source_revision="e9762cd6d4cffb66098e80f5580e74b494326cb3",
        evidence_uri="git:e9762cd6d4cffb66098e80f5580e74b494326cb3",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="partial-index-evidence-v1",
        hypothesis_key="partial_index_evidence_tolerance",
        registered_on=date(2026, 7, 25),
        source_revision="8eee9647439effa710fee1860f1488bec4802b15",
        evidence_uri="git:8eee9647439effa710fee1860f1488bec4802b15",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="rerank-failure-gate-v1",
        hypothesis_key="reject_negative_rerank_challenger",
        registered_on=date(2026, 7, 25),
        source_revision="c94e4af9d0f6f3c9709747e8f4d6727819730e33",
        evidence_uri="git:c94e4af9d0f6f3c9709747e8f4d6727819730e33",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="rerank-promotion-margin-v2",
        hypothesis_key="hardened_dynamic_promotion_margin",
        registered_on=date(2026, 7, 25),
        source_revision="280dc6f321360fedfb7bd65b01e1e69557f41c5b",
        evidence_uri="git:280dc6f321360fedfb7bd65b01e1e69557f41c5b",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="rerank-membership-accounting-v2",
        hypothesis_key="turnover_membership_accounting",
        registered_on=date(2026, 7, 25),
        source_revision="e84a2f25585b1e6025fc75157dfd3b7d7cb944c1",
        evidence_uri="git:e84a2f25585b1e6025fc75157dfd3b7d7cb944c1",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="net-alpha-baseline-challenger-v1",
        hypothesis_key="net_alpha_baseline_replacement",
        registered_on=date(2026, 7, 25),
        source_revision="7016e07ade29e427d7ae373a25660cd40cba25a6",
        evidence_uri="git:7016e07ade29e427d7ae373a25660cd40cba25a6",
        disposition="retained_for_comparison",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="baseline-incumbent-retention-v2",
        hypothesis_key="retain_viable_baseline_incumbents",
        registered_on=date(2026, 7, 25),
        source_revision="7437f60a0256a3e23e2daa0e4f20f75de47e793c",
        evidence_uri="git:7437f60a0256a3e23e2daa0e4f20f75de47e793c",
        disposition="retained_for_comparison",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="adaptive-execution-v1",
        hypothesis_key="adaptive_confirmation_execution",
        registered_on=date(2026, 7, 26),
        source_revision="0af19880de88b08a05997b438bbbaf62644a39fb",
        evidence_uri="git:0af19880de88b08a05997b438bbbaf62644a39fb",
        disposition="retained_for_comparison",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="ranking-v3-initial",
        hypothesis_key="point_in_time_net_excess_ranking_v3",
        registered_on=date(2026, 7, 26),
        source_revision="2730914833eeddab944473da8cf74658c163edd1",
        evidence_uri="git:2730914833eeddab944473da8cf74658c163edd1",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="ranking-v3-embargo-v2",
        hypothesis_key="reserve_full_outcome_embargo",
        registered_on=date(2026, 7, 26),
        source_revision="f2add408f9f1a250bd6293ec73ca8a1a0b256418",
        evidence_uri="git:f2add408f9f1a250bd6293ec73ca8a1a0b256418",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="ranking-v3-statistics-v3",
        hypothesis_key="block_dependent_hardened_validation",
        registered_on=date(2026, 7, 26),
        source_revision="972f134a438eb6a6e78b0aff266b2e4622ffadea",
        evidence_uri="git:972f134a438eb6a6e78b0aff266b2e4622ffadea",
        disposition="superseded",
    ),
    RankingV3ExperimentAttempt(
        attempt_id="ranking-v3-factor-prefilter-v4",
        hypothesis_key="late_materialized_factor_prefilter",
        registered_on=date(2026, 7, 26),
        source_revision="128ffb013637348d3d268c6cae9975a489b5b255",
        evidence_uri="git:128ffb013637348d3d268c6cae9975a489b5b255",
        disposition="retained_for_comparison",
    ),
)


def build_ranking_v3_experiment_registry(
    *,
    attempts: tuple[RankingV3ExperimentAttempt, ...] | None = None,
    expected_prior_attempt_count: int | None = None,
) -> RankingV3ExperimentRegistry:
    source_attempts = _REGISTERED_PRIOR_ATTEMPTS if attempts is None else attempts
    registered = tuple(
        sorted(
            (
                RankingV3ExperimentAttempt.model_validate(item.model_dump(mode="python"))
                for item in source_attempts
            ),
            key=lambda item: (item.registered_on, item.attempt_id),
        )
    )
    expected = (
        EXPECTED_PRIOR_ATTEMPT_COUNT
        if expected_prior_attempt_count is None
        else expected_prior_attempt_count
    )
    payload = _registry_payload(
        schema_version=EXPERIMENT_REGISTRY_SCHEMA_VERSION,
        registry_id=EXPERIMENT_REGISTRY_ID,
        frozen_on=date(2026, 7, 26),
        expected_prior_attempt_count=expected,
        attempts=registered,
    )
    registry = RankingV3ExperimentRegistry(
        **payload,
        registry_digest=_digest(payload),
    )
    registry.require_valid()
    return registry


def _registry_payload(
    *,
    schema_version: str,
    registry_id: str,
    frozen_on: date,
    expected_prior_attempt_count: int,
    attempts: tuple[RankingV3ExperimentAttempt, ...],
) -> dict[str, object]:
    ordered = sorted(
        attempts,
        key=lambda item: (item.registered_on, item.attempt_id),
    )
    return {
        "schema_version": schema_version,
        "registry_id": registry_id,
        "frozen_on": frozen_on.isoformat(),
        "expected_prior_attempt_count": expected_prior_attempt_count,
        "attempts": [item.model_dump(mode="json") for item in ordered],
    }


def _validate_registry(registry: RankingV3ExperimentRegistry) -> None:
    if registry.schema_version != EXPERIMENT_REGISTRY_SCHEMA_VERSION:
        raise RankingV3ExperimentRegistryError(
            f"unsupported experiment registry schema: {registry.schema_version}"
        )
    if registry.registry_id != EXPERIMENT_REGISTRY_ID:
        raise RankingV3ExperimentRegistryError("unexpected experiment registry id")
    if registry.expected_prior_attempt_count <= 0:
        raise RankingV3ExperimentRegistryError("expected prior attempt count must be positive")
    if registry.prior_attempt_count != registry.expected_prior_attempt_count:
        raise RankingV3ExperimentRegistryError(
            "experiment registry count does not match frozen expectation"
        )
    if registry.holm_prior_hypothesis_count != registry.prior_attempt_count:
        raise RankingV3ExperimentRegistryError(
            "all registered prior experiments must occupy a Holm-family hypothesis slot"
        )

    attempt_ids = [item.attempt_id for item in registry.attempts]
    hypothesis_keys = [item.hypothesis_key for item in registry.attempts]
    source_revisions = [item.source_revision for item in registry.attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise RankingV3ExperimentRegistryError("duplicate experiment attempt id")
    if len(hypothesis_keys) != len(set(hypothesis_keys)):
        raise RankingV3ExperimentRegistryError("duplicate experiment hypothesis key")
    if len(source_revisions) != len(set(source_revisions)):
        raise RankingV3ExperimentRegistryError("duplicate experiment source revision")
    if registry.registry_digest != _digest(registry.stable_payload()):
        raise RankingV3ExperimentRegistryError("experiment registry digest mismatch")

    registry.confirmatory_holm_p_values()


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
