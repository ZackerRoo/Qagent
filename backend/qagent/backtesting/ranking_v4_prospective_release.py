from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from statistics import NormalDist
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from qagent.backtesting.ranking_v4_forward_evidence import (
    RankingV4AttemptInventorySnapshot,
    RankingV4CommonDateReturnRecord,
    RankingV4EvidenceProof,
    RankingV4EvidenceSnapshot,
    RankingV4ProspectiveDefinition,
    stable_digest,
    verify_snapshot,
)
from qagent.backtesting.ranking_v4_pbo import (
    RankingV4DatedModelReturn,
    evaluate_ranking_v4_cscv_pbo,
)
from qagent.backtesting.ranking_v4_protocol import build_ranking_v4_protocol
from qagent.backtesting.ranking_v4_validation import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_PERMUTATION_SAMPLES,
    DEFAULT_RANDOM_SEED,
    _block_sign_flip_p_value,
    _compound_return,
    _contiguous_subperiods,
    _expected_maximum_sharpe,
    _non_overlapping_block_means,
    _one_sided_moving_block_lower_bound,
    _profit_factor,
    _sample_sharpe,
    _sample_skewness_and_kurtosis,
    _sample_standard_deviation,
    holm_bonferroni,
)
from qagent.security.ranking_v4_attestation import (
    RankingV4AttestationEnvelope,
    RankingV4EvidenceAttestor,
)


RELEASE_POLICY_SCHEMA_VERSION = "ranking-v4.5-prospective-release-policy-v1"
EXECUTION_SUMMARY_SCHEMA_VERSION = "ranking-v4.5-prospective-execution-summary-v1"
RELEASE_PROOF_SCHEMA_VERSION = "ranking-v4.5-prospective-release-proof-v1"

RELEASE_POLICY_ATTESTATION_KIND = "ranking-v4.5-prospective-release-policy"
EXECUTION_SUMMARY_ATTESTATION_KIND = "ranking-v4.5-prospective-execution-summary"
RELEASE_PROOF_ATTESTATION_KIND = "ranking-v4.5-prospective-release-proof"

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
    dependence_block_length: Literal[3] = 3
    bootstrap_samples: Literal[5000] = 5000
    permutation_samples: Literal[10000] = 10000
    random_seed: Literal[404] = 404
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
        statistics = protocol.statistics_definition
        if (
            self.dependence_block_length != statistics.dependence_block_length
            or self.bootstrap_samples != statistics.bootstrap_samples
            or self.permutation_samples != statistics.permutation_samples
            or self.random_seed != statistics.random_seed
        ):
            raise ValueError("prospective statistical sampling differs from V4.5")
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
    completed_trade_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_coverage_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    capital_constraint_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
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


class RankingV4ProspectiveReleaseGate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=96)
    status: Literal["pass", "fail", "unavailable"]
    observed: str
    required: str
    reason: str


class RankingV4ProspectiveReleaseProof(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ranking-v4.5-prospective-release-proof-v1"] = (
        RELEASE_PROOF_SCHEMA_VERSION
    )
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    inventory_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_summary_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    latest_return_record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    returns_chain_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    model_protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    experiment_registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_revision: int = Field(ge=1)
    checkpoint_common_date_count: Literal[80, 96, 112]
    completed_trade_count: int = Field(ge=0)
    valid_outcome_coverage_ratio: Decimal | None
    cumulative_benchmark_excess_return_pct: Decimal | None
    cumulative_stress_cost_adjusted_return_pct: Decimal | None
    maximum_drawdown_pct: Decimal
    profit_factor: Decimal | None
    profit_factor_is_infinite: bool
    bootstrap_one_sided_95_lower_bound_pct: Decimal | None
    holm_adjusted_positive_edge_p_value: Decimal | None
    pbo_probability: Decimal | None
    pbo_evidence_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    deflated_sharpe_probability: Decimal | None
    positive_subperiod_count: int = Field(ge=0)
    subperiod_count: int = Field(ge=0)
    pre_epoch_unverifiable_attempt_count: int = Field(ge=0)
    effective_dsr_trial_count: int = Field(ge=0)
    gates: tuple[RankingV4ProspectiveReleaseGate, ...]
    evaluation_status: Literal["approved", "continue_collecting", "rejected"]
    release_scope: Literal["shadow_only", "official_paper"]
    official_release_allowed: bool
    evaluated_at: datetime
    release_proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation: RankingV4AttestationEnvelope

    @field_validator(
        "valid_outcome_coverage_ratio",
        "cumulative_benchmark_excess_return_pct",
        "cumulative_stress_cost_adjusted_return_pct",
        "maximum_drawdown_pct",
        "profit_factor",
        "bootstrap_one_sided_95_lower_bound_pct",
        "holm_adjusted_positive_edge_p_value",
        "pbo_probability",
        "deflated_sharpe_probability",
    )
    @classmethod
    def require_finite_metric(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("release metrics must be finite")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def require_aware_evaluation_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_release_proof(self):
        keys = tuple(gate.key for gate in self.gates)
        if len(keys) != len(set(keys)) or set(keys) != set(_RELEASE_GATE_KEYS):
            raise ValueError("release proof does not contain the exact gate family")
        all_pass = all(gate.status == "pass" for gate in self.gates)
        expected_status = (
            "approved"
            if all_pass
            else "rejected"
            if self.checkpoint_common_date_count == REGISTERED_CHECKPOINTS[-1]
            else "continue_collecting"
        )
        if self.evaluation_status != expected_status:
            raise ValueError("release evaluation status is inconsistent with its gates")
        if self.official_release_allowed != all_pass:
            raise ValueError("official release flag is inconsistent with its gates")
        expected_scope = "official_paper" if all_pass else "shadow_only"
        if self.release_scope != expected_scope:
            raise ValueError("release scope is inconsistent with its gates")
        if self.release_proof_digest != stable_digest(self.stable_payload()):
            raise ValueError("prospective release-proof digest mismatch")
        if (
            self.attestation.kind != RELEASE_PROOF_ATTESTATION_KIND
            or self.attestation.payload_digest != self.release_proof_digest
        ):
            raise ValueError("release-proof attestation context mismatch")
        return self

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"release_proof_digest", "attestation"},
        )


_RELEASE_GATE_KEYS = (
    "signed_evidence_chain",
    "registered_checkpoint",
    "completed_trades",
    "valid_outcome_coverage",
    "positive_benchmark_excess",
    "positive_stress_cost_return",
    "maximum_drawdown",
    "minimum_profit_factor",
    "bootstrap_positive_lower_bound",
    "holm_registered_family_sequential",
    "positive_subperiods",
    "pbo",
    "deflated_sharpe_count_penalized",
)


def evaluate_prospective_release(
    *,
    snapshot: RankingV4EvidenceSnapshot,
    policy: RankingV4ProspectiveReleasePolicy,
    execution_summary: RankingV4ProspectiveExecutionSummary,
    evaluated_at: datetime,
    attestor: RankingV4EvidenceAttestor,
) -> RankingV4ProspectiveReleaseProof:
    """Recompute every prospective release gate from signed raw evidence."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise RankingV4ProspectiveReleaseStateError(
            "release evaluation timestamp must be timezone-aware"
        )
    evaluated_at = evaluated_at.astimezone(timezone.utc)
    if evaluated_at < execution_summary.recorded_at:
        raise RankingV4ProspectiveReleaseStateError(
            "release evaluation cannot predate its execution evidence"
        )
    verify_snapshot(snapshot, attestor=attestor)
    definition = snapshot.definition
    if (
        policy.definition_digest != definition.definition_digest
        or policy.model_protocol_digest != definition.identity.protocol_digest
        or policy.experiment_registry_digest
        != definition.identity.experiment_registry_digest
        or execution_summary.definition_digest != definition.definition_digest
        or execution_summary.policy_digest != policy.policy_digest
    ):
        raise RankingV4ProspectiveReleaseIntegrityError(
            "prospective release identities do not match"
        )
    if not attestor.verify(
        policy.attestation,
        expected_kind=RELEASE_POLICY_ATTESTATION_KIND,
        expected_payload_digest=policy.policy_digest,
    ) or not attestor.verify(
        execution_summary.attestation,
        expected_kind=EXECUTION_SUMMARY_ATTESTATION_KIND,
        expected_payload_digest=execution_summary.summary_digest,
    ):
        raise RankingV4ProspectiveReleaseIntegrityError(
            "prospective release source signature is invalid"
        )
    if not snapshot.inventories or not snapshot.return_records or not snapshot.proofs:
        raise RankingV4ProspectiveReleaseStateError(
            "release evaluation requires complete signed evidence"
        )
    inventory = snapshot.inventories[-1]
    records = snapshot.return_records
    evidence_proof = snapshot.proofs[-1]
    _verify_release_prefix(
        definition=definition,
        inventory=inventory,
        records=records,
        evidence_proof=evidence_proof,
        execution_summary=execution_summary,
    )

    checkpoint = len(records)
    matrix = _return_matrix(definition, records)
    model_ids = definition.registered_model_ids
    baseline_id = "constraint_matched_baseline"
    current_id = "ranking_v45_full"
    if baseline_id not in matrix or current_id not in matrix:
        raise RankingV4ProspectiveReleaseIntegrityError(
            "frozen baseline or release model is absent"
        )
    dates = tuple(item[0] for item in matrix[current_id])
    baseline_values = tuple(item[1] for item in matrix[baseline_id])
    current_values = tuple(item[1] for item in matrix[current_id])
    paired = tuple(
        current - baseline
        for current, baseline in zip(current_values, baseline_values, strict=True)
    )
    paired_by_date = tuple(zip(dates, paired, strict=True))
    stress_values = _stress_returns(records, current_id)

    bootstrap_lower = _one_sided_moving_block_lower_bound(
        paired,
        samples=policy.bootstrap_samples,
        seed=policy.random_seed,
        block_length=policy.dependence_block_length,
    )
    family_p_values = tuple(
        _block_sign_flip_p_value(
            tuple(
                model_row[1] - baseline_row[1]
                for model_row, baseline_row in zip(
                    matrix[model_id],
                    matrix[baseline_id],
                    strict=True,
                )
            ),
            samples=policy.permutation_samples,
            seed=policy.random_seed + 100 + index,
            block_length=policy.dependence_block_length,
        )
        for index, model_id in enumerate(model_ids)
    )
    holm_adjusted: float | None = None
    if all(value is not None for value in family_p_values):
        adjusted = holm_bonferroni(
            tuple(float(value) for value in family_p_values if value is not None)
        )
        holm_adjusted = adjusted[model_ids.index(current_id)]

    pbo_evidence = evaluate_ranking_v4_cscv_pbo(
        {
            model_id: tuple(
                RankingV4DatedModelReturn(
                    rebalance_date=rebalance_date,
                    net_return=net_return,
                )
                for rebalance_date, net_return in rows
            )
            for model_id, rows in matrix.items()
        },
        protocol_version="4.5",
    )
    raw_pbo_probability = pbo_evidence.get("probability")
    pbo_probability = (
        float(raw_pbo_probability)
        if isinstance(raw_pbo_probability, (int, float))
        and not isinstance(raw_pbo_probability, bool)
        and math.isfinite(float(raw_pbo_probability))
        else None
    )
    pbo_digest = pbo_evidence.get("evidence_digest")
    if not isinstance(pbo_digest, str) or len(pbo_digest) != 64:
        pbo_digest = None

    prior_attempt_count = len(inventory.pre_epoch_unverifiable_attempt_ids)
    dsr_probability = _count_penalized_deflated_sharpe_probability(
        matrix,
        current_id=current_id,
        baseline_id=baseline_id,
        prior_attempt_count=prior_attempt_count,
        block_length=policy.dependence_block_length,
    )
    profit_factor, profit_factor_is_infinite = _profit_factor(current_values)
    subperiods = _contiguous_subperiods(
        paired_by_date,
        required_subperiods=policy.required_subperiods,
    )
    positive_subperiod_count = sum(item.positive for item in subperiods)
    coverage_ratio = (
        execution_summary.valid_outcome_count
        / execution_summary.expected_outcome_count
        if execution_summary.expected_outcome_count > 0
        else None
    )
    benchmark_excess = _compound_return(current_values) - _compound_return(
        baseline_values
    )
    stress_return = (
        _compound_return(stress_values) if stress_values is not None else None
    )

    gates = (
        _release_gate(
            "signed_evidence_chain",
            True,
            f"{len(records)} signed common dates",
            "complete signed append-only source chain",
            "Every release fact must bind the current immutable evidence prefix.",
        ),
        _release_gate(
            "registered_checkpoint",
            checkpoint in policy.checkpoint_common_date_counts,
            str(checkpoint),
            "exactly one of 80, 96, or 112 common dates",
            "Evaluation outside a preregistered checkpoint is forbidden.",
        ),
        _numeric_release_gate(
            "completed_trades",
            float(execution_summary.completed_trade_count),
            comparator=lambda value: value >= policy.minimum_completed_trades,
            observed=str(execution_summary.completed_trade_count),
            required=f">={policy.minimum_completed_trades}",
            reason="Only completed capital-constrained trades count.",
        ),
        _numeric_release_gate(
            "valid_outcome_coverage",
            coverage_ratio,
            comparator=lambda value: value
            >= float(policy.minimum_valid_outcome_coverage_ratio),
            observed=_format_optional(coverage_ratio, ".2%"),
            required=f">={policy.minimum_valid_outcome_coverage_ratio:.0%}",
            reason="Missing outcomes cannot be silently dropped.",
        ),
        _numeric_release_gate(
            "positive_benchmark_excess",
            benchmark_excess,
            comparator=lambda value: value > 0,
            observed=_format_optional(benchmark_excess, "+.6f"),
            required=">0 cumulative percentage points",
            reason="The release model must beat the frozen executable baseline.",
        ),
        _numeric_release_gate(
            "positive_stress_cost_return",
            stress_return,
            comparator=lambda value: value > 0,
            observed=_format_optional(stress_return, "+.6f"),
            required=">0 cumulative stress-cost return",
            reason="The release model must remain profitable under frozen stress costs.",
        ),
        _numeric_release_gate(
            "maximum_drawdown",
            float(execution_summary.maximum_drawdown_pct),
            comparator=lambda value: value
            >= float(policy.maximum_drawdown_floor_pct),
            observed=f"{execution_summary.maximum_drawdown_pct}%",
            required=f">={policy.maximum_drawdown_floor_pct}%",
            reason="Drawdown must remain within the frozen floor.",
        ),
        _release_gate(
            "minimum_profit_factor",
            profit_factor_is_infinite
            or (
                profit_factor is not None
                and profit_factor >= float(policy.minimum_profit_factor)
            ),
            "infinite"
            if profit_factor_is_infinite
            else _format_optional(profit_factor, ".6f"),
            f">={policy.minimum_profit_factor}",
            "Gross gains must cover gross losses by the frozen margin.",
            unavailable=profit_factor is None and not profit_factor_is_infinite,
        ),
        _numeric_release_gate(
            "bootstrap_positive_lower_bound",
            bootstrap_lower,
            comparator=lambda value: value > 0,
            observed=_format_optional(bootstrap_lower, "+.6f"),
            required="one-sided 95% lower bound >0",
            reason="Moving-block bootstrap must support positive prospective edge.",
        ),
        _numeric_release_gate(
            "holm_registered_family_sequential",
            holm_adjusted,
            comparator=lambda value: value
            <= float(policy.maximum_checkpoint_holm_adjusted_p_value),
            observed=_format_optional(holm_adjusted, ".6f"),
            required=f"<={policy.maximum_checkpoint_holm_adjusted_p_value}",
            reason="Holm control includes the frozen family and all three checkpoints.",
        ),
        _release_gate(
            "positive_subperiods",
            len(subperiods) == policy.required_subperiods
            and positive_subperiod_count >= policy.minimum_positive_subperiods,
            f"{positive_subperiod_count}/{len(subperiods)}",
            f">={policy.minimum_positive_subperiods}/{policy.required_subperiods}",
            "At least four contiguous prospective subperiods must be positive.",
            unavailable=len(subperiods) != policy.required_subperiods,
        ),
        _numeric_release_gate(
            "pbo",
            pbo_probability,
            comparator=lambda value: value
            <= float(policy.maximum_probability_of_backtest_overfit),
            observed=_format_optional(pbo_probability, ".2%"),
            required=f"<={policy.maximum_probability_of_backtest_overfit:.0%}",
            reason=str(
                pbo_evidence.get("rejection_reason")
                or "PBO uses the frozen eight-model real-return matrix."
            ),
        ),
        _numeric_release_gate(
            "deflated_sharpe_count_penalized",
            dsr_probability,
            comparator=lambda value: value
            >= float(policy.minimum_deflated_sharpe_probability),
            observed=_format_optional(dsr_probability, ".2%"),
            required=f">={policy.minimum_deflated_sharpe_probability:.0%}",
            reason=(
                "DSR uses only prospective model returns while pre-epoch attempts "
                "conservatively increase the expected-maximum trial count."
            ),
        ),
    )
    all_pass = all(gate.status == "pass" for gate in gates)
    status = (
        "approved"
        if all_pass
        else "rejected"
        if checkpoint == policy.maximum_checkpoint_common_date_count
        else "continue_collecting"
    )
    payload = {
        "schema_version": RELEASE_PROOF_SCHEMA_VERSION,
        "definition_digest": definition.definition_digest,
        "policy_digest": policy.policy_digest,
        "inventory_digest": inventory.inventory_digest,
        "evidence_proof_digest": evidence_proof.proof_digest,
        "execution_summary_digest": execution_summary.summary_digest,
        "latest_return_record_digest": records[-1].record_digest,
        "returns_chain_digest": evidence_proof.returns_chain_digest,
        "code_revision": definition.identity.code_revision,
        "model_protocol_digest": definition.identity.protocol_digest,
        "experiment_registry_digest": (
            definition.identity.experiment_registry_digest
        ),
        "dataset_revision": execution_summary.dataset_revision,
        "checkpoint_common_date_count": checkpoint,
        "completed_trade_count": execution_summary.completed_trade_count,
        "valid_outcome_coverage_ratio": _decimal_metric(coverage_ratio),
        "cumulative_benchmark_excess_return_pct": _decimal_metric(
            benchmark_excess
        ),
        "cumulative_stress_cost_adjusted_return_pct": _decimal_metric(
            stress_return
        ),
        "maximum_drawdown_pct": execution_summary.maximum_drawdown_pct,
        "profit_factor": _decimal_metric(profit_factor),
        "profit_factor_is_infinite": profit_factor_is_infinite,
        "bootstrap_one_sided_95_lower_bound_pct": _decimal_metric(
            bootstrap_lower
        ),
        "holm_adjusted_positive_edge_p_value": _decimal_metric(holm_adjusted),
        "pbo_probability": _decimal_metric(pbo_probability),
        "pbo_evidence_digest": pbo_digest,
        "deflated_sharpe_probability": _decimal_metric(dsr_probability),
        "positive_subperiod_count": positive_subperiod_count,
        "subperiod_count": len(subperiods),
        "pre_epoch_unverifiable_attempt_count": prior_attempt_count,
        "effective_dsr_trial_count": len(model_ids) + prior_attempt_count,
        "gates": [gate.model_dump(mode="json") for gate in gates],
        "evaluation_status": status,
        "release_scope": "official_paper" if all_pass else "shadow_only",
        "official_release_allowed": all_pass,
        "evaluated_at": _utc_json_timestamp(evaluated_at),
    }
    digest = stable_digest(payload)
    return RankingV4ProspectiveReleaseProof(
        **payload,
        release_proof_digest=digest,
        attestation=attestor.sign(RELEASE_PROOF_ATTESTATION_KIND, digest),
    )


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
        "dependence_block_length": 3,
        "bootstrap_samples": DEFAULT_BOOTSTRAP_SAMPLES,
        "permutation_samples": DEFAULT_PERMUTATION_SAMPLES,
        "random_seed": DEFAULT_RANDOM_SEED,
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
    completed_trade_evidence_digest: str,
    outcome_coverage_evidence_digest: str,
    cost_evidence_digest: str,
    benchmark_evidence_digest: str,
    capital_constraint_evidence_digest: str,
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
        "completed_trade_evidence_digest": completed_trade_evidence_digest,
        "outcome_coverage_evidence_digest": outcome_coverage_evidence_digest,
        "cost_evidence_digest": cost_evidence_digest,
        "benchmark_evidence_digest": benchmark_evidence_digest,
        "capital_constraint_evidence_digest": (
            capital_constraint_evidence_digest
        ),
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


def _verify_release_prefix(
    *,
    definition: RankingV4ProspectiveDefinition,
    inventory: RankingV4AttemptInventorySnapshot,
    records: tuple[RankingV4CommonDateReturnRecord, ...],
    evidence_proof: RankingV4EvidenceProof,
    execution_summary: RankingV4ProspectiveExecutionSummary,
) -> None:
    checkpoint = len(records)
    if checkpoint not in REGISTERED_CHECKPOINTS:
        raise RankingV4ProspectiveReleaseStateError(
            "release evaluation is allowed only at a registered checkpoint"
        )
    expected_chain_digest = stable_digest(
        {
            "definition_digest": definition.definition_digest,
            "record_digests": [item.record_digest for item in records],
        }
    )
    if (
        inventory.definition_digest != definition.definition_digest
        or evidence_proof.definition_digest != definition.definition_digest
        or evidence_proof.inventory_digest != inventory.inventory_digest
        or evidence_proof.return_record_count != checkpoint
        or evidence_proof.first_rebalance_date != records[0].rebalance_date
        or evidence_proof.latest_rebalance_date != records[-1].rebalance_date
        or evidence_proof.returns_chain_digest != expected_chain_digest
        or execution_summary.common_date_count != checkpoint
        or execution_summary.latest_mature_rebalance_date
        != records[-1].rebalance_date
        or execution_summary.source_result_digest
        != records[-1].source_result_digest
        or execution_summary.dataset_revision != records[-1].dataset_revision
        or execution_summary.execution_start_date
        != definition.identity.evidence_start_date
        or execution_summary.recorded_at < records[-1].recorded_at
    ):
        raise RankingV4ProspectiveReleaseIntegrityError(
            "release inputs do not describe the same complete evidence prefix"
        )


def _return_matrix(
    definition: RankingV4ProspectiveDefinition,
    records: tuple[RankingV4CommonDateReturnRecord, ...],
) -> dict[str, tuple[tuple[date, float], ...]]:
    matrix: dict[str, list[tuple[date, float]]] = {
        model_id: [] for model_id in definition.registered_model_ids
    }
    for record in records:
        if tuple(item.model_id for item in record.model_returns) != (
            definition.registered_model_ids
        ):
            raise RankingV4ProspectiveReleaseIntegrityError(
                "return matrix differs from the frozen model family"
            )
        for item in record.model_returns:
            value = float(item.net_return_pct)
            if not math.isfinite(value):
                raise RankingV4ProspectiveReleaseIntegrityError(
                    "return matrix contains a non-finite value"
                )
            matrix[item.model_id].append((record.rebalance_date, value))
    return {model_id: tuple(rows) for model_id, rows in matrix.items()}


def _stress_returns(
    records: tuple[RankingV4CommonDateReturnRecord, ...],
    current_id: str,
) -> tuple[float, ...] | None:
    values: list[float] = []
    for record in records:
        item = next(
            model_return
            for model_return in record.model_returns
            if model_return.model_id == current_id
        )
        if item.stress_net_return_pct is None:
            return None
        value = float(item.stress_net_return_pct)
        if not math.isfinite(value):
            return None
        values.append(value)
    return tuple(values)


def _count_penalized_deflated_sharpe_probability(
    matrix: Mapping[str, Sequence[tuple[date, float]]],
    *,
    current_id: str,
    baseline_id: str,
    prior_attempt_count: int,
    block_length: int,
) -> float | None:
    if current_id not in matrix or baseline_id not in matrix:
        return None
    baseline = matrix[baseline_id]
    if not baseline:
        return None
    reference_dates = tuple(item[0] for item in baseline)
    block_matrix: dict[str, tuple[float, ...]] = {}
    for model_id, rows in matrix.items():
        if tuple(item[0] for item in rows) != reference_dates:
            return None
        excess = tuple(
            row[1] - baseline_row[1]
            for row, baseline_row in zip(rows, baseline, strict=True)
        )
        blocks = _non_overlapping_block_means(
            excess,
            block_length=block_length,
        )
        if len(blocks) < 3:
            return None
        block_matrix[model_id] = blocks

    trial_sharpes: list[float] = []
    for model_id in sorted(block_matrix):
        sharpe = _sample_sharpe(
            block_matrix[model_id],
            allow_zero_series=model_id == baseline_id,
        )
        if sharpe is None:
            return None
        trial_sharpes.append(sharpe)
    current_blocks = block_matrix[current_id]
    current_sharpe = _sample_sharpe(current_blocks)
    sharpe_std = _sample_standard_deviation(trial_sharpes)
    if current_sharpe is None or sharpe_std is None or sharpe_std <= 0:
        return None
    expected_maximum = _expected_maximum_sharpe(
        mean_sharpe=math.fsum(trial_sharpes) / len(trial_sharpes),
        sharpe_standard_deviation=sharpe_std,
        trial_count=len(trial_sharpes) + prior_attempt_count,
    )
    if expected_maximum is None:
        return None
    skewness, kurtosis = _sample_skewness_and_kurtosis(current_blocks)
    denominator = (
        1
        - skewness * current_sharpe
        + ((kurtosis - 1) / 4) * current_sharpe * current_sharpe
    )
    if not math.isfinite(denominator) or denominator <= 0:
        return None
    statistic = (
        (current_sharpe - expected_maximum)
        * math.sqrt(len(current_blocks) - 1)
        / math.sqrt(denominator)
    )
    probability = NormalDist().cdf(statistic)
    return probability if math.isfinite(probability) else None


def _release_gate(
    key: str,
    passed: bool,
    observed: str,
    required: str,
    reason: str,
    *,
    unavailable: bool = False,
) -> RankingV4ProspectiveReleaseGate:
    return RankingV4ProspectiveReleaseGate(
        key=key,
        status="unavailable" if unavailable else "pass" if passed else "fail",
        observed=observed,
        required=required,
        reason=reason,
    )


def _numeric_release_gate(
    key: str,
    value: float | None,
    *,
    comparator,
    observed: str,
    required: str,
    reason: str,
) -> RankingV4ProspectiveReleaseGate:
    return _release_gate(
        key,
        bool(value is not None and math.isfinite(value) and comparator(value)),
        observed,
        required,
        reason,
        unavailable=value is None or not math.isfinite(value),
    )


def _decimal_metric(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 12)))


def _format_optional(value: float | None, format_spec: str) -> str:
    return "unavailable" if value is None else format(value, format_spec)


def _utc_json_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
