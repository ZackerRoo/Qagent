from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from qagent.backtesting.ranking_v4_forward_evidence import stable_digest
from qagent.backtesting.ranking_v4_protocol import build_ranking_v4_protocol
from qagent.security.ranking_v4_attestation import (
    RankingV4AttestationEnvelope,
    RankingV4EvidenceAttestor,
)


RELEASE_POLICY_SCHEMA_VERSION = "ranking-v4.5-prospective-release-policy-v1"
EXECUTION_SUMMARY_SCHEMA_VERSION = "ranking-v4.5-prospective-execution-summary-v1"

RELEASE_POLICY_ATTESTATION_KIND = "ranking-v4.5-prospective-release-policy"
EXECUTION_SUMMARY_ATTESTATION_KIND = "ranking-v4.5-prospective-execution-summary"

PREREGISTRATION_COMMIT = "e9bf22cb684f71e2b3a3e12b771425aa1f759dde"
PREREGISTRATION_DOCUMENT_SHA256 = (
    "af43ab6f8dacfbfa56cd8ec2f80b40cdd600ab057e9f79c67684cb42fecef483"
)
REGISTERED_CHECKPOINTS = (80, 96, 112)
SEQUENTIAL_FAMILYWISE_ALPHA = Decimal("0.05")
CHECKPOINT_HOLM_ALPHA = Decimal("0.016666666666666666")


class RankingV4ProspectiveReleaseError(RuntimeError):
    """Base error for prospective release-policy and proof handling."""


class RankingV4ProspectiveReleaseIntegrityError(RankingV4ProspectiveReleaseError):
    """Raised when signed release evidence does not match frozen facts."""


class RankingV4ProspectiveReleaseStateError(RankingV4ProspectiveReleaseError):
    """Raised when release evidence is written outside the registered sequence."""


class RankingV4ProspectiveReleasePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ranking-v4.5-prospective-release-policy-v1"] = (
        RELEASE_POLICY_SCHEMA_VERSION
    )
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    experiment_registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    preregistration_commit: Literal[
        "e9bf22cb684f71e2b3a3e12b771425aa1f759dde"
    ] = PREREGISTRATION_COMMIT
    preregistration_document_sha256: Literal[
        "af43ab6f8dacfbfa56cd8ec2f80b40cdd600ab057e9f79c67684cb42fecef483"
    ] = PREREGISTRATION_DOCUMENT_SHA256
    qualification_scope: Literal["prospective_only_no_historical_reclassification"] = (
        "prospective_only_no_historical_reclassification"
    )
    historical_result_policy: Literal[
        "immutable_rejected_not_reclassified_not_release_evidence"
    ] = "immutable_rejected_not_reclassified_not_release_evidence"
    checkpoint_common_date_counts: tuple[int, int, int] = REGISTERED_CHECKPOINTS
    maximum_checkpoint_common_date_count: Literal[112] = 112
    sequential_familywise_alpha: Decimal = SEQUENTIAL_FAMILYWISE_ALPHA
    maximum_checkpoint_holm_adjusted_p_value: Decimal = CHECKPOINT_HOLM_ALPHA
    minimum_post_purge_half_dates: Literal[24] = 24
    minimum_completed_trades: Literal[60] = 60
    minimum_valid_outcome_coverage_ratio: Decimal = Decimal("0.95")
    minimum_profit_factor: Decimal = Decimal("1.10")
    minimum_positive_subperiods: Literal[4] = 4
    required_subperiods: Literal[5] = 5
    maximum_drawdown_floor_pct: Decimal = Decimal("-15")
    minimum_deflated_sharpe_probability: Decimal = Decimal("0.95")
    maximum_probability_of_backtest_overfit: Decimal = Decimal("0.20")
    bootstrap_lower_bound_comparator: Literal["strictly_greater_than_zero"] = (
        "strictly_greater_than_zero"
    )
    benchmark_excess_comparator: Literal["strictly_greater_than_zero"] = (
        "strictly_greater_than_zero"
    )
    stress_cost_adjusted_return_comparator: Literal["strictly_greater_than_zero"] = (
        "strictly_greater_than_zero"
    )
    entry_wait_sessions: Literal[5] = 5
    holding_sessions: Literal[20] = 20
    rebalance_step_sessions: Literal[10] = 10
    candidate_lookback_days: Literal[400] = 400
    prior_attempt_penalty_policy: Literal[
        "count_only_conservative_expected_maximum_no_synthetic_returns"
    ] = "count_only_conservative_expected_maximum_no_synthetic_returns"
    source_evidence_policy: Literal[
        "signed_raw_common_dates_plus_cumulative_execution_summary"
    ] = "signed_raw_common_dates_plus_cumulative_execution_summary"
    checkpoint_failure_policy: Literal[
        "continue_only_to_next_registered_checkpoint_reject_after_final"
    ] = "continue_only_to_next_registered_checkpoint_reject_after_final"
    registered_at: datetime
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation: RankingV4AttestationEnvelope

    @field_validator("registered_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registered_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_policy(self):
        protocol = build_ranking_v4_protocol(version="4.5")
        thresholds = protocol.thresholds
        if self.model_protocol_digest != protocol.protocol_digest:
            raise ValueError("release policy does not bind the frozen V4.5 protocol")
        if (
            self.experiment_registry_digest
            != protocol.experiment_registry.registry_digest
        ):
            raise ValueError("release policy does not bind the frozen experiment registry")
        if self.checkpoint_common_date_counts != REGISTERED_CHECKPOINTS:
            raise ValueError("release checkpoints differ from the preregistration")
        if (
            self.maximum_checkpoint_holm_adjusted_p_value
            > thresholds.maximum_holm_adjusted_p_value
            / Decimal(len(self.checkpoint_common_date_counts))
        ):
            raise ValueError("sequential Holm threshold weakens familywise control")
        if self.minimum_completed_trades < thresholds.minimum_completed_trades:
            raise ValueError("prospective completed-trade gate was weakened")
        if (
            self.minimum_valid_outcome_coverage_ratio
            < thresholds.minimum_valid_outcome_coverage_ratio
        ):
            raise ValueError("prospective outcome-coverage gate was weakened")
        if self.minimum_profit_factor < thresholds.minimum_profit_factor:
            raise ValueError("prospective profit-factor gate was weakened")
        if self.minimum_positive_subperiods < thresholds.minimum_positive_subperiods:
            raise ValueError("prospective subperiod gate was weakened")
        if self.required_subperiods != thresholds.required_subperiods:
            raise ValueError("prospective subperiod denominator changed")
        if self.maximum_drawdown_floor_pct < thresholds.maximum_drawdown_floor_pct:
            raise ValueError("prospective drawdown gate was weakened")
        if (
            self.minimum_deflated_sharpe_probability
            < thresholds.minimum_deflated_sharpe_probability
        ):
            raise ValueError("prospective DSR gate was weakened")
        if (
            self.maximum_probability_of_backtest_overfit
            > thresholds.maximum_probability_of_backtest_overfit
        ):
            raise ValueError("prospective PBO gate was weakened")
        if (
            self.entry_wait_sessions != protocol.temporal_definition.entry_wait_sessions
            or self.holding_sessions != protocol.temporal_definition.holding_sessions
            or self.rebalance_step_sessions
            != protocol.temporal_definition.rebalance_step_sessions
            or self.candidate_lookback_days
            != protocol.temporal_definition.candidate_lookback_days
        ):
            raise ValueError("prospective execution geometry differs from V4.5")
        if self.policy_digest != stable_digest(self.stable_payload()):
            raise ValueError("prospective release-policy digest mismatch")
        if (
            self.attestation.kind != RELEASE_POLICY_ATTESTATION_KIND
            or self.attestation.payload_digest != self.policy_digest
        ):
            raise ValueError("release-policy attestation context mismatch")
        return self

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"policy_digest", "attestation"})


class RankingV4ProspectiveExecutionSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ranking-v4.5-prospective-execution-summary-v1"] = (
        EXECUTION_SUMMARY_SCHEMA_VERSION
    )
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence: int = Field(ge=1)
    source_result_digest: str = Field(min_length=1, max_length=160)
    dataset_revision: int = Field(ge=1)
    execution_start_date: date
    execution_end_date: date
    latest_mature_rebalance_date: date
    rebalance_step_sessions: Literal[10] = 10
    lookback_days: Literal[400] = 400
    common_date_count: int = Field(ge=1)
    completed_trade_count: int = Field(ge=0)
    valid_outcome_count: int = Field(ge=0)
    expected_outcome_count: int = Field(ge=0)
    maximum_drawdown_pct: Decimal
    benchmark_evidence_complete: Literal[True] = True
    cost_evidence_complete: Literal[True] = True
    capital_constraint_evidence_complete: Literal[True] = True
    terminal_force_close_used: Literal[False] = False
    previous_summary_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    summary_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime
    attestation: RankingV4AttestationEnvelope

    @field_validator("maximum_drawdown_pct")
    @classmethod
    def require_finite_drawdown(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("maximum drawdown must be finite")
        return value

    @field_validator("recorded_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_summary(self):
        if self.execution_end_date < self.execution_start_date:
            raise ValueError("execution end precedes execution start")
        if not (
            self.execution_start_date
            <= self.latest_mature_rebalance_date
            <= self.execution_end_date
        ):
            raise ValueError("latest mature rebalance date is outside execution bounds")
        if self.valid_outcome_count > self.expected_outcome_count:
            raise ValueError("valid outcomes exceed expected outcomes")
        if self.sequence == 1 and self.previous_summary_digest is not None:
            raise ValueError("first execution summary cannot have a predecessor")
        if self.sequence > 1 and self.previous_summary_digest is None:
            raise ValueError("later execution summaries require a predecessor")
        if self.summary_digest != stable_digest(self.stable_payload()):
            raise ValueError("prospective execution-summary digest mismatch")
        if (
            self.attestation.kind != EXECUTION_SUMMARY_ATTESTATION_KIND
            or self.attestation.payload_digest != self.summary_digest
        ):
            raise ValueError("execution-summary attestation context mismatch")
        return self

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"summary_digest", "attestation"})


def build_prospective_release_policy(
    *,
    definition_digest: str,
    model_protocol_digest: str,
    experiment_registry_digest: str,
    registered_at: datetime,
    attestor: RankingV4EvidenceAttestor,
) -> RankingV4ProspectiveReleasePolicy:
    payload = {
        "schema_version": RELEASE_POLICY_SCHEMA_VERSION,
        "definition_digest": definition_digest,
        "model_protocol_digest": model_protocol_digest,
        "experiment_registry_digest": experiment_registry_digest,
        "preregistration_commit": PREREGISTRATION_COMMIT,
        "preregistration_document_sha256": PREREGISTRATION_DOCUMENT_SHA256,
        "qualification_scope": "prospective_only_no_historical_reclassification",
        "historical_result_policy": (
            "immutable_rejected_not_reclassified_not_release_evidence"
        ),
        "checkpoint_common_date_counts": list(REGISTERED_CHECKPOINTS),
        "maximum_checkpoint_common_date_count": REGISTERED_CHECKPOINTS[-1],
        "sequential_familywise_alpha": str(SEQUENTIAL_FAMILYWISE_ALPHA),
        "maximum_checkpoint_holm_adjusted_p_value": str(CHECKPOINT_HOLM_ALPHA),
        "minimum_post_purge_half_dates": 24,
        "minimum_completed_trades": 60,
        "minimum_valid_outcome_coverage_ratio": "0.95",
        "minimum_profit_factor": "1.10",
        "minimum_positive_subperiods": 4,
        "required_subperiods": 5,
        "maximum_drawdown_floor_pct": "-15",
        "minimum_deflated_sharpe_probability": "0.95",
        "maximum_probability_of_backtest_overfit": "0.20",
        "bootstrap_lower_bound_comparator": "strictly_greater_than_zero",
        "benchmark_excess_comparator": "strictly_greater_than_zero",
        "stress_cost_adjusted_return_comparator": "strictly_greater_than_zero",
        "entry_wait_sessions": 5,
        "holding_sessions": 20,
        "rebalance_step_sessions": 10,
        "candidate_lookback_days": 400,
        "prior_attempt_penalty_policy": (
            "count_only_conservative_expected_maximum_no_synthetic_returns"
        ),
        "source_evidence_policy": (
            "signed_raw_common_dates_plus_cumulative_execution_summary"
        ),
        "checkpoint_failure_policy": (
            "continue_only_to_next_registered_checkpoint_reject_after_final"
        ),
        "registered_at": _utc_json_timestamp(registered_at),
    }
    digest = stable_digest(payload)
    return RankingV4ProspectiveReleasePolicy(
        **payload,
        policy_digest=digest,
        attestation=attestor.sign(RELEASE_POLICY_ATTESTATION_KIND, digest),
    )


def build_prospective_execution_summary(
    *,
    definition_digest: str,
    policy_digest: str,
    sequence: int,
    source_result_digest: str,
    dataset_revision: int,
    execution_start_date: date,
    execution_end_date: date,
    latest_mature_rebalance_date: date,
    common_date_count: int,
    completed_trade_count: int,
    valid_outcome_count: int,
    expected_outcome_count: int,
    maximum_drawdown_pct: Decimal,
    previous_summary_digest: str | None,
    recorded_at: datetime,
    attestor: RankingV4EvidenceAttestor,
) -> RankingV4ProspectiveExecutionSummary:
    payload = {
        "schema_version": EXECUTION_SUMMARY_SCHEMA_VERSION,
        "definition_digest": definition_digest,
        "policy_digest": policy_digest,
        "sequence": sequence,
        "source_result_digest": source_result_digest,
        "dataset_revision": dataset_revision,
        "execution_start_date": execution_start_date.isoformat(),
        "execution_end_date": execution_end_date.isoformat(),
        "latest_mature_rebalance_date": latest_mature_rebalance_date.isoformat(),
        "rebalance_step_sessions": 10,
        "lookback_days": 400,
        "common_date_count": common_date_count,
        "completed_trade_count": completed_trade_count,
        "valid_outcome_count": valid_outcome_count,
        "expected_outcome_count": expected_outcome_count,
        "maximum_drawdown_pct": str(maximum_drawdown_pct),
        "benchmark_evidence_complete": True,
        "cost_evidence_complete": True,
        "capital_constraint_evidence_complete": True,
        "terminal_force_close_used": False,
        "previous_summary_digest": previous_summary_digest,
        "recorded_at": _utc_json_timestamp(recorded_at),
    }
    digest = stable_digest(payload)
    return RankingV4ProspectiveExecutionSummary(
        **payload,
        summary_digest=digest,
        attestation=attestor.sign(EXECUTION_SUMMARY_ATTESTATION_KIND, digest),
    )


def _utc_json_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
