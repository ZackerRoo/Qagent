from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from qagent.backtesting.ranking_v4_forward_evidence import (
    build_prospective_definition,
    RankingV4EvidenceSnapshot,
    RankingV4ProspectiveModelReturn,
    build_attempt_inventory_snapshot,
    build_common_date_return_record,
    build_evidence_proof,
)
from qagent.backtesting.ranking_v4_prospective_release import (
    CHECKPOINT_HOLM_ALPHA,
    PREREGISTRATION_COMMIT,
    PREREGISTRATION_DOCUMENT_SHA256,
    REGISTERED_CHECKPOINTS,
    RankingV4ProspectiveExecutionSummary,
    RankingV4ProspectiveReleaseIntegrityError,
    RankingV4ProspectiveReleasePolicy,
    RankingV4ProspectiveReleaseStateError,
    build_prospective_execution_summary,
    build_prospective_release_policy,
    evaluate_prospective_release,
)
from qagent.security.ranking_v4_attestation import RankingV4EvidenceAttestor


ATTESTOR = RankingV4EvidenceAttestor(b"k" * 32)
REGISTERED_AT = datetime(2026, 7, 30, 13, 0, tzinfo=timezone.utc)
RAW_EVIDENCE_DIGESTS = {
    "completed_trade_evidence_digest": "1" * 64,
    "outcome_coverage_evidence_digest": "2" * 64,
    "cost_evidence_digest": "3" * 64,
    "benchmark_evidence_digest": "4" * 64,
    "capital_constraint_evidence_digest": "5" * 64,
}


def _definition():
    return build_prospective_definition(
        epoch_id="ranking-v45-forward-release-test",
        code_revision="a" * 40,
        dataset_revision=8939,
        evidence_start_date=date(2026, 7, 31),
        frozen_at=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        attestor=ATTESTOR,
    )


def _policy():
    definition = _definition()
    return build_prospective_release_policy(
        definition_digest=definition.definition_digest,
        model_protocol_digest=definition.identity.protocol_digest,
        experiment_registry_digest=(
            definition.identity.experiment_registry_digest
        ),
        registered_at=REGISTERED_AT,
        attestor=ATTESTOR,
    )


def test_release_policy_binds_preregistration_and_strengthens_holm_gate():
    policy = _policy()

    assert policy.preregistration_commit == PREREGISTRATION_COMMIT
    assert (
        policy.preregistration_document_sha256
        == PREREGISTRATION_DOCUMENT_SHA256
    )
    assert policy.checkpoint_common_date_counts == REGISTERED_CHECKPOINTS
    assert policy.maximum_checkpoint_holm_adjusted_p_value == CHECKPOINT_HOLM_ALPHA
    assert policy.minimum_completed_trades == 60
    assert policy.policy_digest
    assert ATTESTOR.verify(
        policy.attestation,
        expected_kind=policy.attestation.kind,
        expected_payload_digest=policy.policy_digest,
    )


def test_release_policy_rejects_weakened_or_unregistered_thresholds():
    policy = _policy()
    payload = policy.model_dump(mode="json")

    with pytest.raises(ValidationError, match="checkpoints"):
        RankingV4ProspectiveReleasePolicy.model_validate(
            {
                **payload,
                "checkpoint_common_date_counts": [48, 64, 80],
            }
        )
    with pytest.raises(ValidationError, match="minimum_completed_trades"):
        RankingV4ProspectiveReleasePolicy.model_validate(
            {
                **payload,
                "minimum_completed_trades": 10,
            }
        )
    with pytest.raises(ValidationError, match="Holm"):
        RankingV4ProspectiveReleasePolicy.model_validate(
            {
                **payload,
                "maximum_checkpoint_holm_adjusted_p_value": "0.05",
            }
        )


def test_execution_summary_is_signed_and_chained():
    definition = _definition()
    policy = _policy()
    first = build_prospective_execution_summary(
        definition_digest=definition.definition_digest,
        policy_digest=policy.policy_digest,
        sequence=1,
        source_result_digest="v2:first",
        dataset_revision=8939,
        execution_start_date=date(2026, 7, 31),
        execution_end_date=date(2029, 12, 31),
        latest_mature_rebalance_date=date(2029, 11, 1),
        common_date_count=80,
        completed_trade_count=60,
        valid_outcome_count=400,
        expected_outcome_count=400,
        maximum_drawdown_pct=Decimal("-8.5"),
        **RAW_EVIDENCE_DIGESTS,
        previous_summary_digest=None,
        recorded_at=datetime(2029, 12, 31, tzinfo=timezone.utc),
        attestor=ATTESTOR,
    )
    second = build_prospective_execution_summary(
        definition_digest=definition.definition_digest,
        policy_digest=policy.policy_digest,
        sequence=2,
        source_result_digest="v2:second",
        dataset_revision=8940,
        execution_start_date=date(2026, 7, 31),
        execution_end_date=date(2030, 6, 30),
        latest_mature_rebalance_date=date(2030, 5, 15),
        common_date_count=96,
        completed_trade_count=72,
        valid_outcome_count=480,
        expected_outcome_count=480,
        maximum_drawdown_pct=Decimal("-9.25"),
        **RAW_EVIDENCE_DIGESTS,
        previous_summary_digest=first.summary_digest,
        recorded_at=datetime(2030, 6, 30, tzinfo=timezone.utc),
        attestor=ATTESTOR,
    )

    assert second.previous_summary_digest == first.summary_digest
    assert second.dataset_revision > first.dataset_revision
    assert second.common_date_count > first.common_date_count
    assert ATTESTOR.verify(
        second.attestation,
        expected_kind=second.attestation.kind,
        expected_payload_digest=second.summary_digest,
    )


def test_execution_summary_rejects_missing_predecessor_and_invalid_counts():
    definition = _definition()
    policy = _policy()
    valid = build_prospective_execution_summary(
        definition_digest=definition.definition_digest,
        policy_digest=policy.policy_digest,
        sequence=1,
        source_result_digest="v2:test",
        dataset_revision=8939,
        execution_start_date=date(2026, 7, 31),
        execution_end_date=date(2029, 12, 31),
        latest_mature_rebalance_date=date(2029, 11, 1),
        common_date_count=80,
        completed_trade_count=60,
        valid_outcome_count=400,
        expected_outcome_count=400,
        maximum_drawdown_pct=Decimal("-8.5"),
        **RAW_EVIDENCE_DIGESTS,
        previous_summary_digest=None,
        recorded_at=datetime(2029, 12, 31, tzinfo=timezone.utc),
        attestor=ATTESTOR,
    )
    base = valid.model_dump(mode="json")
    with pytest.raises(ValidationError, match="valid outcomes"):
        RankingV4ProspectiveExecutionSummary.model_validate(
            {
                **base,
                "valid_outcome_count": 401,
            }
        )
    with pytest.raises(ValidationError, match="predecessor"):
        RankingV4ProspectiveExecutionSummary.model_validate(
            {
                **base,
                "sequence": 2,
                "previous_summary_digest": None,
            }
        )


def _checkpoint_evidence(
    *,
    date_count: int = 80,
    missing_stress: bool = False,
):
    definition = _definition()
    inventory = build_attempt_inventory_snapshot(
        definition=definition,
        sequence=1,
        as_of_date=REGISTERED_AT.date(),
        pre_epoch_unverifiable_attempt_ids=("legacy-a", "legacy-b"),
        prospective_attempts={
            definition.identity.epoch_id: definition.definition_digest,
        },
        previous_inventory_digest=None,
        recorded_at=REGISTERED_AT,
        attestor=ATTESTOR,
    )
    means = {
        "constraint_matched_baseline": 0.0,
        "ranking_v45_full": 1.2,
        "channel_baseline": 0.05,
        "channel_trend": 0.15,
        "channel_breakout": 0.25,
        "channel_quality_value": 0.35,
        "channel_defensive_low_vol": 0.10,
        "channel_etf_industry": 0.20,
    }
    records = []
    previous_digest = None
    for row_index in range(date_count):
        rebalance_date = date(2026, 7, 31) + timedelta(days=14 * row_index)
        model_returns = []
        for model_index, model_id in enumerate(definition.registered_model_ids):
            if model_id == "constraint_matched_baseline":
                value = 0.0
            elif model_id == "ranking_v45_full":
                value = (
                    means[model_id]
                    + 0.22 * math.sin(row_index * 1.17)
                    + 0.09 * math.cos(row_index * 0.43)
                )
            else:
                value = (
                    means[model_id]
                    + 0.45 * math.sin(row_index * 0.71 + model_index)
                    + 0.25 * math.cos(row_index * 0.31 + model_index)
                )
            stress = value - 0.15
            if (
                missing_stress
                and row_index == date_count - 1
                and model_id == "ranking_v45_full"
            ):
                stress = None
            model_returns.append(
                RankingV4ProspectiveModelReturn(
                    model_id=model_id,
                    net_return_pct=Decimal(str(value)),
                    stress_net_return_pct=(
                        None if stress is None else Decimal(str(stress))
                    ),
                    source_snapshot_digest=(
                        f"{row_index + 1:x}{model_index + 1:x}" * 64
                    )[:64],
                )
            )
        record = build_common_date_return_record(
            definition=definition,
            sequence=row_index + 1,
            rebalance_date=rebalance_date,
            dataset_revision=8939,
            source_result_digest=f"v2:checkpoint-{date_count}",
            model_returns=tuple(model_returns),
            previous_record_digest=previous_digest,
            recorded_at=datetime.combine(
                rebalance_date + timedelta(days=30),
                datetime.min.time(),
                tzinfo=timezone.utc,
            ),
            attestor=ATTESTOR,
        )
        records.append(record)
        previous_digest = record.record_digest
    unsigned_snapshot = RankingV4EvidenceSnapshot(
        definition=definition,
        inventories=(inventory,),
        return_records=tuple(records),
        proofs=(),
    )
    final_timestamp = datetime.combine(
        records[-1].rebalance_date + timedelta(days=31),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    proof = build_evidence_proof(
        unsigned_snapshot,
        generated_at=final_timestamp,
        attestor=ATTESTOR,
    )
    snapshot = unsigned_snapshot.model_copy(update={"proofs": (proof,)})
    policy = _policy()
    summary = build_prospective_execution_summary(
        definition_digest=definition.definition_digest,
        policy_digest=policy.policy_digest,
        sequence=1,
        source_result_digest=f"v2:checkpoint-{date_count}",
        dataset_revision=8939,
        execution_start_date=definition.identity.evidence_start_date,
        execution_end_date=records[-1].rebalance_date + timedelta(days=30),
        latest_mature_rebalance_date=records[-1].rebalance_date,
        common_date_count=date_count,
        completed_trade_count=60,
        valid_outcome_count=100,
        expected_outcome_count=100,
        maximum_drawdown_pct=Decimal("-5"),
        **RAW_EVIDENCE_DIGESTS,
        previous_summary_digest=None,
        recorded_at=final_timestamp,
        attestor=ATTESTOR,
    )
    return snapshot, policy, summary, final_timestamp


def test_release_evaluation_approves_only_when_every_registered_gate_passes():
    snapshot, policy, summary, evaluated_at = _checkpoint_evidence()

    proof = evaluate_prospective_release(
        snapshot=snapshot,
        policy=policy,
        execution_summary=summary,
        evaluated_at=evaluated_at,
        attestor=ATTESTOR,
    )

    assert proof.evaluation_status == "approved"
    assert proof.release_scope == "official_paper"
    assert proof.official_release_allowed is True
    assert all(gate.status == "pass" for gate in proof.gates)
    assert proof.checkpoint_common_date_count == 80
    assert proof.pre_epoch_unverifiable_attempt_count == 2
    assert proof.effective_dsr_trial_count == 10
    assert proof.deflated_sharpe_probability is not None
    assert proof.deflated_sharpe_probability >= Decimal("0.95")
    assert ATTESTOR.verify(
        proof.attestation,
        expected_kind=proof.attestation.kind,
        expected_payload_digest=proof.release_proof_digest,
    )


def test_release_evaluation_fails_closed_on_missing_stress_evidence():
    snapshot, policy, summary, evaluated_at = _checkpoint_evidence(
        missing_stress=True
    )

    proof = evaluate_prospective_release(
        snapshot=snapshot,
        policy=policy,
        execution_summary=summary,
        evaluated_at=evaluated_at,
        attestor=ATTESTOR,
    )

    assert proof.evaluation_status == "continue_collecting"
    assert proof.release_scope == "shadow_only"
    assert proof.official_release_allowed is False
    stress_gate = next(
        gate for gate in proof.gates if gate.key == "positive_stress_cost_return"
    )
    assert stress_gate.status == "unavailable"


def test_release_evaluation_rejects_unregistered_checkpoint():
    snapshot, policy, summary, evaluated_at = _checkpoint_evidence(date_count=79)

    with pytest.raises(RankingV4ProspectiveReleaseStateError, match="checkpoint"):
        evaluate_prospective_release(
            snapshot=snapshot,
            policy=policy,
            execution_summary=summary,
            evaluated_at=evaluated_at,
            attestor=ATTESTOR,
        )


def test_release_evaluation_rejects_mismatched_source_summary():
    snapshot, policy, summary, evaluated_at = _checkpoint_evidence()
    mismatched = build_prospective_execution_summary(
        definition_digest=summary.definition_digest,
        policy_digest=summary.policy_digest,
        sequence=summary.sequence,
        source_result_digest="v2:other-source",
        dataset_revision=summary.dataset_revision,
        execution_start_date=summary.execution_start_date,
        execution_end_date=summary.execution_end_date,
        latest_mature_rebalance_date=summary.latest_mature_rebalance_date,
        common_date_count=summary.common_date_count,
        completed_trade_count=summary.completed_trade_count,
        valid_outcome_count=summary.valid_outcome_count,
        expected_outcome_count=summary.expected_outcome_count,
        maximum_drawdown_pct=summary.maximum_drawdown_pct,
        **RAW_EVIDENCE_DIGESTS,
        previous_summary_digest=summary.previous_summary_digest,
        recorded_at=summary.recorded_at,
        attestor=ATTESTOR,
    )

    with pytest.raises(RankingV4ProspectiveReleaseIntegrityError, match="prefix"):
        evaluate_prospective_release(
            snapshot=snapshot,
            policy=policy,
            execution_summary=mismatched,
            evaluated_at=evaluated_at,
            attestor=ATTESTOR,
        )
