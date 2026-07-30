from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from qagent.backtesting.ranking_v4_forward_evidence import (
    build_prospective_definition,
)
from qagent.backtesting.ranking_v4_prospective_release import (
    CHECKPOINT_HOLM_ALPHA,
    PREREGISTRATION_COMMIT,
    PREREGISTRATION_DOCUMENT_SHA256,
    REGISTERED_CHECKPOINTS,
    RankingV4ProspectiveExecutionSummary,
    RankingV4ProspectiveReleasePolicy,
    build_prospective_execution_summary,
    build_prospective_release_policy,
)
from qagent.security.ranking_v4_attestation import RankingV4EvidenceAttestor


ATTESTOR = RankingV4EvidenceAttestor(b"k" * 32)
REGISTERED_AT = datetime(2026, 7, 30, 13, 0, tzinfo=timezone.utc)


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
