from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from qagent.backtesting.ranking_v4_forward_evidence import (
    RankingV4EvidenceConflictError,
    RankingV4EvidenceIntegrityError,
    RankingV4EvidenceStateError,
    stable_digest,
)
from qagent.backtesting.ranking_v4_prospective_release import (
    EXECUTION_SUMMARY_ATTESTATION_KIND,
    RELEASE_PROOF_ATTESTATION_KIND,
    RELEASE_POLICY_ATTESTATION_KIND,
    RankingV4ProspectiveExecutionSummary,
    RankingV4ProspectiveReleaseProof,
    RankingV4ProspectiveReleasePolicy,
    evaluate_prospective_release,
)
from qagent.security.ranking_v4_attestation import (
    RankingV4EvidenceAttestor,
    load_ranking_v4_attestation_key,
)
from qagent.storage.tables import (
    RankingV4EvidenceDefinitionRow,
    RankingV4EvidenceReturnRow,
    RankingV4ProspectiveExecutionSummaryRow,
    RankingV4ProspectiveReleaseProofRow,
    RankingV4ProspectiveReleasePolicyRow,
)
from qagent.storage.ranking_v4_forward_evidence import RankingV4EvidenceRepository


class RankingV4ProspectiveReleaseRepository:
    """Append-only storage for frozen release policy and execution evidence."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        attestor: RankingV4EvidenceAttestor | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.attestor = attestor or RankingV4EvidenceAttestor(
            load_ranking_v4_attestation_key()
        )

    def register_policy(
        self,
        policy: RankingV4ProspectiveReleasePolicy,
    ) -> RankingV4ProspectiveReleasePolicy:
        self._verify_signed_payload(
            digest=policy.policy_digest,
            payload=policy.stable_payload(),
            kind=RELEASE_POLICY_ATTESTATION_KIND,
            attestation=policy.attestation,
            label="release policy",
        )
        with self.session_factory() as session:
            _begin_immediate(session)
            definition = _definition_for_digest(session, policy.definition_digest)
            if (
                policy.model_protocol_digest != definition.protocol_digest
                or policy.experiment_registry_digest
                != definition.experiment_registry_digest
                or policy.registered_at < definition.frozen_at
            ):
                raise RankingV4EvidenceIntegrityError(
                    "release policy differs from the frozen definition"
                )
            existing = session.get(
                RankingV4ProspectiveReleasePolicyRow,
                policy.policy_digest,
            )
            if existing is not None:
                persisted = _policy_from_row(existing)
                if persisted != policy:
                    raise RankingV4EvidenceConflictError(
                        "release-policy digest was reused with different facts"
                    )
                session.commit()
                return persisted
            definition_policy = session.execute(
                select(RankingV4ProspectiveReleasePolicyRow).where(
                    RankingV4ProspectiveReleasePolicyRow.definition_digest
                    == policy.definition_digest
                )
            ).scalar_one_or_none()
            if definition_policy is not None:
                raise RankingV4EvidenceConflictError(
                    "frozen definition is already bound to another release policy"
                )
            session.add(
                RankingV4ProspectiveReleasePolicyRow(
                    policy_digest=policy.policy_digest,
                    definition_digest=policy.definition_digest,
                    model_protocol_digest=policy.model_protocol_digest,
                    experiment_registry_digest=policy.experiment_registry_digest,
                    preregistration_commit=policy.preregistration_commit,
                    preregistration_document_sha256=(
                        policy.preregistration_document_sha256
                    ),
                    maximum_checkpoint_common_date_count=(
                        policy.maximum_checkpoint_common_date_count
                    ),
                    payload_json=_dump(policy.stable_payload()),
                    attestation_json=_dump(
                        policy.attestation.model_dump(mode="json")
                    ),
                    registered_at=policy.registered_at,
                )
            )
            session.commit()
            return policy

    def append_execution_summary(
        self,
        summary: RankingV4ProspectiveExecutionSummary,
    ) -> RankingV4ProspectiveExecutionSummary:
        self._verify_signed_payload(
            digest=summary.summary_digest,
            payload=summary.stable_payload(),
            kind=EXECUTION_SUMMARY_ATTESTATION_KIND,
            attestation=summary.attestation,
            label="execution summary",
        )
        with self.session_factory() as session:
            _begin_immediate(session)
            definition = _definition_for_digest(session, summary.definition_digest)
            policy_row = session.get(
                RankingV4ProspectiveReleasePolicyRow,
                summary.policy_digest,
            )
            if (
                policy_row is None
                or policy_row.definition_digest != summary.definition_digest
            ):
                raise RankingV4EvidenceStateError(
                    "execution summary is not bound to the frozen release policy"
                )
            existing = session.get(
                RankingV4ProspectiveExecutionSummaryRow,
                summary.summary_digest,
            )
            if existing is not None:
                persisted = _summary_from_row(existing)
                if persisted != summary:
                    raise RankingV4EvidenceConflictError(
                        "execution-summary digest was reused with different facts"
                    )
                session.commit()
                return persisted
            latest_row = session.execute(
                select(RankingV4ProspectiveExecutionSummaryRow)
                .where(
                    RankingV4ProspectiveExecutionSummaryRow.definition_digest
                    == summary.definition_digest
                )
                .order_by(RankingV4ProspectiveExecutionSummaryRow.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
            latest = _summary_from_row(latest_row) if latest_row is not None else None
            expected_sequence = 1 if latest is None else latest.sequence + 1
            expected_previous = None if latest is None else latest.summary_digest
            if (
                summary.sequence != expected_sequence
                or summary.previous_summary_digest != expected_previous
            ):
                raise RankingV4EvidenceConflictError(
                    "execution-summary sequence or predecessor is not append-only"
                )
            return_rows = tuple(
                session.execute(
                    select(RankingV4EvidenceReturnRow)
                    .where(
                        RankingV4EvidenceReturnRow.definition_digest
                        == summary.definition_digest
                    )
                    .order_by(RankingV4EvidenceReturnRow.sequence)
                ).scalars()
            )
            if not return_rows:
                raise RankingV4EvidenceStateError(
                    "execution summary requires prospective common-date returns"
                )
            latest_return = return_rows[-1]
            latest_payload = json.loads(latest_return.payload_json)
            if (
                summary.execution_start_date != definition.evidence_start_date
                or summary.dataset_revision < definition.dataset_revision
                or summary.common_date_count != len(return_rows)
                or summary.latest_mature_rebalance_date
                != latest_return.rebalance_date
                or summary.source_result_digest
                != latest_payload.get("source_result_digest")
            ):
                raise RankingV4EvidenceIntegrityError(
                    "execution summary differs from the prospective evidence chain"
                )
            if latest is not None:
                _require_monotonic_summary(latest, summary)
            session.add(
                RankingV4ProspectiveExecutionSummaryRow(
                    summary_digest=summary.summary_digest,
                    definition_digest=summary.definition_digest,
                    policy_digest=summary.policy_digest,
                    sequence=summary.sequence,
                    source_result_digest=summary.source_result_digest,
                    dataset_revision=summary.dataset_revision,
                    execution_start_date=summary.execution_start_date,
                    execution_end_date=summary.execution_end_date,
                    latest_mature_rebalance_date=(
                        summary.latest_mature_rebalance_date
                    ),
                    common_date_count=summary.common_date_count,
                    completed_trade_count=summary.completed_trade_count,
                    valid_outcome_count=summary.valid_outcome_count,
                    expected_outcome_count=summary.expected_outcome_count,
                    maximum_drawdown_pct=summary.maximum_drawdown_pct,
                    benchmark_evidence_complete=summary.benchmark_evidence_complete,
                    cost_evidence_complete=summary.cost_evidence_complete,
                    capital_constraint_evidence_complete=(
                        summary.capital_constraint_evidence_complete
                    ),
                    terminal_force_close_used=summary.terminal_force_close_used,
                    previous_summary_digest=summary.previous_summary_digest,
                    payload_json=_dump(summary.stable_payload()),
                    attestation_json=_dump(
                        summary.attestation.model_dump(mode="json")
                    ),
                    recorded_at=summary.recorded_at,
                )
            )
            session.commit()
            return summary

    def load_policy(
        self,
        definition_digest: str,
    ) -> RankingV4ProspectiveReleasePolicy | None:
        with self.session_factory() as session:
            row = session.execute(
                select(RankingV4ProspectiveReleasePolicyRow).where(
                    RankingV4ProspectiveReleasePolicyRow.definition_digest
                    == definition_digest
                )
            ).scalar_one_or_none()
        return _policy_from_row(row) if row is not None else None

    def evaluate_checkpoint(
        self,
        epoch_id: str,
        *,
        evaluated_at: datetime,
    ) -> RankingV4ProspectiveReleaseProof:
        snapshot = RankingV4EvidenceRepository(
            self.session_factory,
            attestor=self.attestor,
        ).load_snapshot(epoch_id)
        if snapshot is None:
            raise RankingV4EvidenceStateError("prospective evidence epoch does not exist")
        policy = self.load_policy(snapshot.definition.definition_digest)
        summaries = self.load_execution_summaries(
            snapshot.definition.definition_digest
        )
        if policy is None or not summaries:
            raise RankingV4EvidenceStateError(
                "release evaluation requires a frozen policy and execution summary"
            )
        proof = evaluate_prospective_release(
            snapshot=snapshot,
            policy=policy,
            execution_summary=summaries[-1],
            evaluated_at=evaluated_at,
            attestor=self.attestor,
        )
        return self.append_release_proof(proof)

    def append_release_proof(
        self,
        proof: RankingV4ProspectiveReleaseProof,
    ) -> RankingV4ProspectiveReleaseProof:
        self._verify_signed_payload(
            digest=proof.release_proof_digest,
            payload=proof.stable_payload(),
            kind=RELEASE_PROOF_ATTESTATION_KIND,
            attestation=proof.attestation,
            label="release proof",
        )
        with self.session_factory() as session:
            existing = session.get(
                RankingV4ProspectiveReleaseProofRow,
                proof.release_proof_digest,
            )
            if existing is not None:
                persisted = _release_proof_from_row(existing)
                if persisted != proof:
                    raise RankingV4EvidenceConflictError(
                        "release-proof digest was reused with different facts"
                    )
                return persisted

        evidence_repository = RankingV4EvidenceRepository(
            self.session_factory,
            attestor=self.attestor,
        )
        with self.session_factory() as session:
            definition_row = _definition_for_digest(
                session,
                proof.definition_digest,
            )
            epoch_id = definition_row.epoch_id
        snapshot = evidence_repository.load_snapshot(epoch_id)
        policy = self.load_policy(proof.definition_digest)
        summaries = self.load_execution_summaries(proof.definition_digest)
        if snapshot is None or policy is None or not summaries:
            raise RankingV4EvidenceStateError(
                "release proof sources are incomplete"
            )
        expected = evaluate_prospective_release(
            snapshot=snapshot,
            policy=policy,
            execution_summary=summaries[-1],
            evaluated_at=proof.evaluated_at,
            attestor=self.attestor,
        )
        if expected != proof:
            raise RankingV4EvidenceIntegrityError(
                "release proof does not match an independent gate recomputation"
            )

        with self.session_factory() as session:
            _begin_immediate(session)
            prior_rows = tuple(
                session.execute(
                    select(RankingV4ProspectiveReleaseProofRow)
                    .where(
                        RankingV4ProspectiveReleaseProofRow.definition_digest
                        == proof.definition_digest
                    )
                    .order_by(
                        RankingV4ProspectiveReleaseProofRow.checkpoint_common_date_count
                    )
                ).scalars()
            )
            expected_checkpoint = (
                80
                if not prior_rows
                else 96
                if prior_rows[-1].checkpoint_common_date_count == 80
                else 112
                if prior_rows[-1].checkpoint_common_date_count == 96
                else None
            )
            if expected_checkpoint != proof.checkpoint_common_date_count:
                raise RankingV4EvidenceConflictError(
                    "release checkpoints must be appended in preregistered order"
                )
            if prior_rows and prior_rows[-1].evaluation_status in {
                "approved",
                "rejected",
            }:
                raise RankingV4EvidenceConflictError(
                    "release evaluation is already terminal"
                )
            session.add(
                RankingV4ProspectiveReleaseProofRow(
                    release_proof_digest=proof.release_proof_digest,
                    definition_digest=proof.definition_digest,
                    policy_digest=proof.policy_digest,
                    inventory_digest=proof.inventory_digest,
                    evidence_proof_digest=proof.evidence_proof_digest,
                    execution_summary_digest=proof.execution_summary_digest,
                    latest_return_record_digest=(
                        proof.latest_return_record_digest
                    ),
                    returns_chain_digest=proof.returns_chain_digest,
                    code_revision=proof.code_revision,
                    model_protocol_digest=proof.model_protocol_digest,
                    experiment_registry_digest=(
                        proof.experiment_registry_digest
                    ),
                    dataset_revision=proof.dataset_revision,
                    checkpoint_common_date_count=(
                        proof.checkpoint_common_date_count
                    ),
                    completed_trade_count=proof.completed_trade_count,
                    evaluation_status=proof.evaluation_status,
                    release_scope=proof.release_scope,
                    official_release_allowed=proof.official_release_allowed,
                    payload_json=_dump(proof.stable_payload()),
                    attestation_json=_dump(
                        proof.attestation.model_dump(mode="json")
                    ),
                    evaluated_at=proof.evaluated_at,
                )
            )
            session.commit()
        return proof

    def load_release_proofs(
        self,
        definition_digest: str,
    ) -> tuple[RankingV4ProspectiveReleaseProof, ...]:
        with self.session_factory() as session:
            rows = tuple(
                session.execute(
                    select(RankingV4ProspectiveReleaseProofRow)
                    .where(
                        RankingV4ProspectiveReleaseProofRow.definition_digest
                        == definition_digest
                    )
                    .order_by(
                        RankingV4ProspectiveReleaseProofRow.checkpoint_common_date_count
                    )
                ).scalars()
            )
        return tuple(_release_proof_from_row(row) for row in rows)

    def load_execution_summaries(
        self,
        definition_digest: str,
    ) -> tuple[RankingV4ProspectiveExecutionSummary, ...]:
        with self.session_factory() as session:
            rows = tuple(
                session.execute(
                    select(RankingV4ProspectiveExecutionSummaryRow)
                    .where(
                        RankingV4ProspectiveExecutionSummaryRow.definition_digest
                        == definition_digest
                    )
                    .order_by(RankingV4ProspectiveExecutionSummaryRow.sequence)
                ).scalars()
            )
        summaries = tuple(_summary_from_row(row) for row in rows)
        for previous, current in zip(summaries, summaries[1:], strict=False):
            if current.previous_summary_digest != previous.summary_digest:
                raise RankingV4EvidenceIntegrityError(
                    "persisted execution-summary chain is broken"
                )
            _require_monotonic_summary(previous, current)
        return summaries

    def _verify_signed_payload(
        self,
        *,
        digest: str,
        payload: object,
        kind: str,
        attestation,
        label: str,
    ) -> None:
        if digest != stable_digest(payload):
            raise RankingV4EvidenceIntegrityError(f"{label} digest is invalid")
        if not self.attestor.verify(
            attestation,
            expected_kind=kind,
            expected_payload_digest=digest,
        ):
            raise RankingV4EvidenceIntegrityError(f"{label} signature is invalid")


def _definition_for_digest(
    session: Session,
    definition_digest: str,
) -> RankingV4EvidenceDefinitionRow:
    row = session.execute(
        select(RankingV4EvidenceDefinitionRow).where(
            RankingV4EvidenceDefinitionRow.definition_digest == definition_digest
        )
    ).scalar_one_or_none()
    if row is None:
        raise RankingV4EvidenceStateError("frozen definition does not exist")
    return row


def _require_monotonic_summary(
    previous: RankingV4ProspectiveExecutionSummary,
    current: RankingV4ProspectiveExecutionSummary,
) -> None:
    if (
        current.dataset_revision < previous.dataset_revision
        or current.execution_end_date < previous.execution_end_date
        or current.latest_mature_rebalance_date
        < previous.latest_mature_rebalance_date
        or current.common_date_count < previous.common_date_count
        or current.completed_trade_count < previous.completed_trade_count
        or current.valid_outcome_count < previous.valid_outcome_count
        or current.expected_outcome_count < previous.expected_outcome_count
        or current.recorded_at < previous.recorded_at
    ):
        raise RankingV4EvidenceConflictError(
            "execution-summary cumulative facts must be monotonic"
        )


def _policy_from_row(
    row: RankingV4ProspectiveReleasePolicyRow,
) -> RankingV4ProspectiveReleasePolicy:
    return RankingV4ProspectiveReleasePolicy.model_validate(
        {
            **json.loads(row.payload_json),
            "policy_digest": row.policy_digest,
            "attestation": json.loads(row.attestation_json),
        }
    )


def _summary_from_row(
    row: RankingV4ProspectiveExecutionSummaryRow,
) -> RankingV4ProspectiveExecutionSummary:
    return RankingV4ProspectiveExecutionSummary.model_validate(
        {
            **json.loads(row.payload_json),
            "summary_digest": row.summary_digest,
            "attestation": json.loads(row.attestation_json),
        }
    )


def _release_proof_from_row(
    row: RankingV4ProspectiveReleaseProofRow,
) -> RankingV4ProspectiveReleaseProof:
    return RankingV4ProspectiveReleaseProof.model_validate(
        {
            **json.loads(row.payload_json),
            "release_proof_digest": row.release_proof_digest,
            "attestation": json.loads(row.attestation_json),
        }
    )


def _dump(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _begin_immediate(session: Session) -> None:
    session.execute(text("BEGIN IMMEDIATE"))
