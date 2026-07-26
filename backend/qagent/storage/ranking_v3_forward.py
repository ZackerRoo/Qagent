from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardConflictError,
    RankingV3ForwardGateEvidence,
    RankingV3ForwardIdentity,
    RankingV3ForwardLedger,
    RankingV3ForwardLedgerSnapshot,
    RankingV3ForwardOutcomeInput,
    RankingV3ForwardReleaseProof,
    RankingV3ForwardSession,
    RankingV3ForwardSessionInput,
    RankingV3ForwardStateError,
    RankingV3ShadowCandidate,
    RankingV3ShadowCandidateInput,
    stable_digest,
    stable_release_proof_digest,
)
from qagent.storage.tables import (
    RankingV3ForwardCandidateRow,
    RankingV3ForwardGateEvidenceRow,
    RankingV3ForwardLedgerRow,
    RankingV3ForwardReleaseProofRow,
    RankingV3ForwardSessionRow,
    utc_now,
)


class RankingV3ForwardRepository:
    """Transactional persistence for an isolated Ranking V3 forward ledger."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def ensure_ledger(
        self,
        identity: RankingV3ForwardIdentity,
        data_revision: str,
    ) -> RankingV3ForwardLedger:
        with self.session_factory() as session:
            _begin_immediate(session)
            session.execute(
                sqlite_insert(RankingV3ForwardLedgerRow)
                .values(
                    **_identity_values(identity),
                    data_revision=data_revision,
                    status="pending",
                    rejection_reasons_json="[]",
                    integrity_status="verified",
                    quarantine_reason="",
                    revision=0,
                )
                .on_conflict_do_nothing()
            )
            session.commit()
            row = session.get(RankingV3ForwardLedgerRow, _identity_key(identity))
            if row is None:
                raise RankingV3ForwardStateError("forward ledger insert was not persisted")
            if row.data_revision != data_revision:
                raise RankingV3ForwardConflictError(
                    "data revision cannot change inside one protocol ledger"
                )
            return _ledger_from_row(row)

    def record_session(
        self,
        identity: RankingV3ForwardIdentity,
        item: RankingV3ForwardSessionInput,
        *,
        idempotency_key: str,
        fact_digest: str,
    ) -> RankingV3ForwardSession:
        with self.session_factory() as session:
            _begin_immediate(session)
            ledger = self._ledger_for_revision(session, identity, item.data_revision)
            result = session.execute(
                sqlite_insert(RankingV3ForwardSessionRow)
                .values(
                    **_identity_values(identity),
                    **item.model_dump(),
                    idempotency_key=idempotency_key,
                    fact_digest=fact_digest,
                )
                .on_conflict_do_nothing()
            )
            row = self._resolve_session_replay(
                session,
                identity,
                item,
                idempotency_key=idempotency_key,
                fact_digest=fact_digest,
            )
            if result.rowcount:
                self._require_pending(ledger)
                if (
                    ledger.latest_session_date is not None
                    and item.session_date <= ledger.latest_session_date
                ):
                    raise RankingV3ForwardConflictError(
                        "new forward sessions must be appended in chronological order"
                    )
                self._touch_pending(
                    session,
                    identity,
                    first_session_date=func.coalesce(
                        RankingV3ForwardLedgerRow.first_session_date,
                        item.session_date,
                    ),
                    latest_session_date=item.session_date,
                )
            session.commit()
            return _session_from_row(row)

    def record_candidate(
        self,
        identity: RankingV3ForwardIdentity,
        item: RankingV3ShadowCandidateInput,
        *,
        idempotency_key: str,
        fact_digest: str,
    ) -> RankingV3ShadowCandidate:
        with self.session_factory() as session:
            _begin_immediate(session)
            ledger = self._ledger_for_revision(session, identity, item.data_revision)
            session_row = session.get(
                RankingV3ForwardSessionRow,
                (*_identity_key(identity), item.session_date),
            )
            if session_row is None:
                raise LookupError("candidate session is not present in the forward ledger")
            result = session.execute(
                sqlite_insert(RankingV3ForwardCandidateRow)
                .values(
                    **_identity_values(identity),
                    **item.model_dump(),
                    idempotency_key=idempotency_key,
                    fact_digest=fact_digest,
                    integrity_status="verified",
                    quarantine_reason="",
                    outcome_status="pending",
                )
                .on_conflict_do_nothing()
            )
            row = self._resolve_candidate_replay(
                session,
                identity,
                item,
                idempotency_key=idempotency_key,
                fact_digest=fact_digest,
            )
            if result.rowcount:
                self._require_pending(ledger)
                self._touch_pending(session, identity)
            session.commit()
            return _candidate_from_row(row)

    def finalize_candidate(
        self,
        identity: RankingV3ForwardIdentity,
        candidate_id: str,
        item: RankingV3ForwardOutcomeInput,
        *,
        idempotency_key: str,
        outcome_digest: str,
        computed: Mapping[str, Decimal | None],
    ) -> RankingV3ShadowCandidate:
        with self.session_factory() as session:
            _begin_immediate(session)
            ledger = self._ledger_for_revision(session, identity, item.data_revision)
            row = session.get(
                RankingV3ForwardCandidateRow,
                (*_identity_key(identity), candidate_id),
            )
            if row is None:
                raise LookupError("shadow candidate does not exist")

            existing_by_key = (
                session.query(RankingV3ForwardCandidateRow)
                .filter(
                    *_identity_filters(RankingV3ForwardCandidateRow, identity),
                    RankingV3ForwardCandidateRow.outcome_idempotency_key == idempotency_key,
                )
                .one_or_none()
            )
            if existing_by_key is not None and existing_by_key is not row:
                raise RankingV3ForwardConflictError(
                    "outcome idempotency key belongs to another candidate"
                )
            if row.outcome_status != "pending":
                if (
                    row.outcome_idempotency_key == idempotency_key
                    and row.outcome_digest == outcome_digest
                ):
                    return _candidate_from_row(row)
                raise RankingV3ForwardConflictError(
                    "candidate outcome is final and cannot be replaced"
                )
            self._require_pending(ledger)
            if item.resolved_on < row.session_date:
                raise ValueError("outcome cannot resolve before the candidate session")
            resolution_session = session.get(
                RankingV3ForwardSessionRow,
                (*_identity_key(identity), item.resolved_on),
            )
            if resolution_session is None:
                raise LookupError("outcome resolution date is not a recorded trading session")

            row.outcome_status = item.status
            row.outcome_digest = outcome_digest
            row.outcome_idempotency_key = idempotency_key
            row.resolved_on = item.resolved_on
            row.gross_return_pct = item.gross_return_pct
            row.transaction_cost_pct = item.transaction_cost_pct
            row.stress_transaction_cost_pct = item.stress_transaction_cost_pct
            row.net_return_pct = computed["net_return_pct"]
            row.stress_net_return_pct = computed["stress_net_return_pct"]
            row.benchmark_return_pct = item.benchmark_return_pct
            row.benchmark_excess_pct = computed["benchmark_excess_pct"]
            row.stress_benchmark_excess_pct = computed["stress_benchmark_excess_pct"]
            row.max_drawdown_pct = item.max_drawdown_pct
            row.outcome_reason = item.reason
            self._touch_pending(session, identity)
            session.commit()
            session.refresh(row)
            return _candidate_from_row(row)

    def record_evidence(
        self,
        evidence: RankingV3ForwardGateEvidence,
    ) -> RankingV3ForwardGateEvidence:
        with self.session_factory() as session:
            _begin_immediate(session)
            ledger = self._ledger_for_revision(
                session,
                evidence.identity,
                evidence.data_revision,
            )
            sequence = (
                session.query(func.max(RankingV3ForwardGateEvidenceRow.sequence))
                .filter(
                    *_identity_filters(
                        RankingV3ForwardGateEvidenceRow,
                        evidence.identity,
                    ),
                    RankingV3ForwardGateEvidenceRow.evidence_kind == evidence.evidence_kind,
                )
                .scalar()
                or 0
            ) + 1
            result = session.execute(
                sqlite_insert(RankingV3ForwardGateEvidenceRow)
                .values(
                    evidence_digest=evidence.evidence_digest,
                    **_identity_values(evidence.identity),
                    evidence_kind=evidence.evidence_kind,
                    sequence=sequence,
                    data_revision=evidence.data_revision,
                    passed=evidence.passed,
                    payload_json=_json(evidence.payload),
                    idempotency_key=evidence.idempotency_key,
                    recorded_at=evidence.recorded_at,
                )
                .on_conflict_do_nothing()
            )
            row = self._resolve_evidence_replay(session, evidence)
            if result.rowcount:
                self._require_pending(ledger)
                self._touch_pending(session, evidence.identity)
            session.commit()
            return _evidence_from_row(row)

    def load_snapshot(
        self,
        identity: RankingV3ForwardIdentity,
    ) -> RankingV3ForwardLedgerSnapshot | None:
        with self.session_factory() as session:
            ledger = session.get(RankingV3ForwardLedgerRow, _identity_key(identity))
            if ledger is None:
                return None
            sessions = (
                session.query(RankingV3ForwardSessionRow)
                .filter(*_identity_filters(RankingV3ForwardSessionRow, identity))
                .order_by(RankingV3ForwardSessionRow.session_date)
                .all()
            )
            candidates = (
                session.query(RankingV3ForwardCandidateRow)
                .filter(
                    *_identity_filters(RankingV3ForwardCandidateRow, identity),
                    RankingV3ForwardCandidateRow.integrity_status == "verified",
                )
                .order_by(
                    RankingV3ForwardCandidateRow.session_date,
                    RankingV3ForwardCandidateRow.rank,
                    RankingV3ForwardCandidateRow.candidate_id,
                )
                .all()
            )
            evidence = (
                session.query(RankingV3ForwardGateEvidenceRow)
                .filter(*_identity_filters(RankingV3ForwardGateEvidenceRow, identity))
                .order_by(
                    RankingV3ForwardGateEvidenceRow.sequence,
                    RankingV3ForwardGateEvidenceRow.evidence_digest,
                )
                .all()
            )
            proof = None
            if ledger.current_release_proof_digest:
                proof_row = session.get(
                    RankingV3ForwardReleaseProofRow,
                    ledger.current_release_proof_digest,
                )
                if proof_row is not None:
                    proof = _proof_from_row(proof_row)
            return RankingV3ForwardLedgerSnapshot(
                ledger=_ledger_from_row(ledger),
                sessions=[_session_from_row(row) for row in sessions],
                candidates=[_candidate_from_row(row) for row in candidates],
                evidence=[_evidence_from_row(row) for row in evidence],
                release_proof=proof,
            )

    def approve(
        self,
        proof: RankingV3ForwardReleaseProof,
        *,
        expected_revision: int,
    ) -> RankingV3ForwardReleaseProof:
        if stable_release_proof_digest(proof) != proof.proof_digest:
            raise ValueError("release proof digest is invalid")
        with self.session_factory() as session:
            _begin_immediate(session)
            ledger = session.get(
                RankingV3ForwardLedgerRow,
                _identity_key(proof.identity),
            )
            if ledger is None:
                raise LookupError("Ranking V3 forward ledger does not exist")
            if ledger.status == "approved":
                if ledger.current_release_proof_digest != proof.proof_digest:
                    raise RankingV3ForwardConflictError(
                        "ledger is already approved with another proof"
                    )
                existing = session.get(
                    RankingV3ForwardReleaseProofRow,
                    proof.proof_digest,
                )
                if existing is None:
                    raise RankingV3ForwardStateError(
                        "approved ledger has no persisted release proof"
                    )
                return _proof_from_row(existing)
            if ledger.status != "pending":
                raise RankingV3ForwardStateError("rejected ledger cannot be approved")
            if ledger.revision != expected_revision:
                raise RankingV3ForwardConflictError(
                    "forward ledger changed while approval was evaluated"
                )
            if proof.ledger_revision != expected_revision:
                raise RankingV3ForwardConflictError(
                    "release proof does not match the evaluated ledger revision"
                )
            if ledger.data_revision != proof.data_revision:
                raise RankingV3ForwardConflictError(
                    "release proof data revision does not match the ledger"
                )
            session.execute(
                sqlite_insert(RankingV3ForwardReleaseProofRow)
                .values(
                    proof_digest=proof.proof_digest,
                    **_identity_values(proof.identity),
                    data_revision=proof.data_revision,
                    status="approved",
                    generated_at=proof.generated_at,
                    ledger_revision=proof.ledger_revision,
                    payload_json=_json(proof.model_dump(mode="json")),
                )
                .on_conflict_do_nothing()
            )
            persisted = self._resolve_release_proof_replay(session, proof)
            result = session.execute(
                update(RankingV3ForwardLedgerRow)
                .where(
                    *_identity_filters(RankingV3ForwardLedgerRow, proof.identity),
                    RankingV3ForwardLedgerRow.status == "pending",
                    RankingV3ForwardLedgerRow.revision == expected_revision,
                    RankingV3ForwardLedgerRow.data_revision == proof.data_revision,
                    RankingV3ForwardLedgerRow.integrity_status == "verified",
                )
                .values(
                    status="approved",
                    current_release_proof_digest=proof.proof_digest,
                    rejection_reasons_json="[]",
                    revision=RankingV3ForwardLedgerRow.revision + 1,
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                session.expire_all()
                current = session.get(
                    RankingV3ForwardLedgerRow,
                    _identity_key(proof.identity),
                )
                if (
                    current is not None
                    and current.status == "approved"
                    and current.current_release_proof_digest == proof.proof_digest
                ):
                    session.rollback()
                    return persisted
                raise RankingV3ForwardConflictError(
                    "forward ledger changed while approval was evaluated"
                )
            session.commit()
            return persisted

    def reject(
        self,
        identity: RankingV3ForwardIdentity,
        reasons: Sequence[str],
        *,
        expected_revision: int,
    ) -> RankingV3ForwardLedger:
        with self.session_factory() as session:
            _begin_immediate(session)
            ledger = session.get(RankingV3ForwardLedgerRow, _identity_key(identity))
            if ledger is None:
                raise LookupError("Ranking V3 forward ledger does not exist")
            canonical_reasons = list(
                dict.fromkeys(str(reason).strip() for reason in reasons if str(reason).strip())
            )
            if not canonical_reasons:
                raise ValueError("rejection requires at least one non-empty reason")
            if ledger.status == "rejected":
                if _json_load(ledger.rejection_reasons_json) != canonical_reasons:
                    raise RankingV3ForwardConflictError(
                        "ledger is already rejected with different reasons"
                    )
                return _ledger_from_row(ledger)
            if ledger.status != "pending":
                raise RankingV3ForwardStateError("approved ledger cannot be rejected")
            if ledger.revision != expected_revision:
                raise RankingV3ForwardConflictError(
                    "forward ledger changed while rejection was evaluated"
                )
            result = session.execute(
                update(RankingV3ForwardLedgerRow)
                .where(
                    *_identity_filters(RankingV3ForwardLedgerRow, identity),
                    RankingV3ForwardLedgerRow.status == "pending",
                    RankingV3ForwardLedgerRow.revision == expected_revision,
                    RankingV3ForwardLedgerRow.integrity_status == "verified",
                )
                .values(
                    status="rejected",
                    rejection_reasons_json=_json(canonical_reasons),
                    current_release_proof_digest=None,
                    revision=RankingV3ForwardLedgerRow.revision + 1,
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                session.expire_all()
                current = session.get(RankingV3ForwardLedgerRow, _identity_key(identity))
                if (
                    current is not None
                    and current.status == "rejected"
                    and _json_load(current.rejection_reasons_json) == canonical_reasons
                ):
                    session.rollback()
                    return _ledger_from_row(current)
                raise RankingV3ForwardConflictError(
                    "forward ledger changed while rejection was evaluated"
                )
            session.commit()
            session.expire_all()
            ledger = session.get(RankingV3ForwardLedgerRow, _identity_key(identity))
            if ledger is None:
                raise RankingV3ForwardStateError("rejected ledger disappeared")
            return _ledger_from_row(ledger)

    def get_release_proof(
        self,
        proof_digest: str,
    ) -> RankingV3ForwardReleaseProof | None:
        with self.session_factory() as session:
            row = session.get(RankingV3ForwardReleaseProofRow, proof_digest)
            if row is None:
                return None
            identity = _identity_from_row(row)
            ledger = session.get(RankingV3ForwardLedgerRow, _identity_key(identity))
            if (
                ledger is None
                or ledger.integrity_status != "verified"
                or ledger.status != "approved"
                or ledger.current_release_proof_digest != proof_digest
            ):
                return None
            return _proof_from_row(row)

    def _resolve_session_replay(
        self,
        session: Session,
        identity: RankingV3ForwardIdentity,
        item: RankingV3ForwardSessionInput,
        *,
        idempotency_key: str,
        fact_digest: str,
    ) -> RankingV3ForwardSessionRow:
        natural = session.get(
            RankingV3ForwardSessionRow,
            (*_identity_key(identity), item.session_date),
        )
        by_key = (
            session.query(RankingV3ForwardSessionRow)
            .filter(
                *_identity_filters(RankingV3ForwardSessionRow, identity),
                RankingV3ForwardSessionRow.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        row = _one_replayed_row(
            natural,
            by_key,
            conflict_message="session idempotency key was reused with different facts",
        )
        expected = {
            **_identity_values(identity),
            **item.model_dump(),
            "idempotency_key": idempotency_key,
            "fact_digest": fact_digest,
        }
        _assert_row_values(
            row,
            expected,
            (
                "session idempotency key was reused with different facts"
                if by_key is not None
                else "forward session is immutable once recorded"
            ),
        )
        return row

    def _resolve_candidate_replay(
        self,
        session: Session,
        identity: RankingV3ForwardIdentity,
        item: RankingV3ShadowCandidateInput,
        *,
        idempotency_key: str,
        fact_digest: str,
    ) -> RankingV3ForwardCandidateRow:
        natural = session.get(
            RankingV3ForwardCandidateRow,
            (*_identity_key(identity), item.candidate_id),
        )
        by_key = (
            session.query(RankingV3ForwardCandidateRow)
            .filter(
                *_identity_filters(RankingV3ForwardCandidateRow, identity),
                RankingV3ForwardCandidateRow.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        row = _one_replayed_row(
            natural,
            by_key,
            conflict_message="candidate idempotency key was reused with different facts",
        )
        expected = {
            **_identity_values(identity),
            **item.model_dump(),
            "idempotency_key": idempotency_key,
            "fact_digest": fact_digest,
            "integrity_status": "verified",
            "quarantine_reason": "",
        }
        _assert_row_values(
            row,
            expected,
            (
                "candidate idempotency key was reused with different facts"
                if by_key is not None
                else "shadow candidate is immutable once recorded"
            ),
        )
        return row

    def _resolve_evidence_replay(
        self,
        session: Session,
        evidence: RankingV3ForwardGateEvidence,
    ) -> RankingV3ForwardGateEvidenceRow:
        natural = session.get(
            RankingV3ForwardGateEvidenceRow,
            evidence.evidence_digest,
        )
        by_key = (
            session.query(RankingV3ForwardGateEvidenceRow)
            .filter(
                *_identity_filters(
                    RankingV3ForwardGateEvidenceRow,
                    evidence.identity,
                ),
                RankingV3ForwardGateEvidenceRow.idempotency_key == evidence.idempotency_key,
            )
            .one_or_none()
        )
        row = _one_replayed_row(
            natural,
            by_key,
            conflict_message="evidence idempotency key was reused with different facts",
        )
        expected = {
            **_identity_values(evidence.identity),
            "evidence_digest": evidence.evidence_digest,
            "evidence_kind": evidence.evidence_kind,
            "data_revision": evidence.data_revision,
            "passed": evidence.passed,
            "payload_json": _json(evidence.payload),
            "idempotency_key": evidence.idempotency_key,
            "recorded_at": evidence.recorded_at,
        }
        _assert_row_values(
            row,
            expected,
            (
                "evidence idempotency key was reused with different facts"
                if by_key is not None
                else "forward gate evidence is immutable once recorded"
            ),
        )
        return row

    def _resolve_release_proof_replay(
        self,
        session: Session,
        proof: RankingV3ForwardReleaseProof,
    ) -> RankingV3ForwardReleaseProof:
        natural = session.get(
            RankingV3ForwardReleaseProofRow,
            proof.proof_digest,
        )
        by_identity = (
            session.query(RankingV3ForwardReleaseProofRow)
            .filter(
                *_identity_filters(RankingV3ForwardReleaseProofRow, proof.identity),
            )
            .one_or_none()
        )
        row = _one_replayed_row(
            natural,
            by_identity,
            conflict_message="forward ledger already has a different release proof",
        )
        expected = {
            **_identity_values(proof.identity),
            "proof_digest": proof.proof_digest,
            "data_revision": proof.data_revision,
            "status": "approved",
            "generated_at": proof.generated_at,
            "ledger_revision": proof.ledger_revision,
            "payload_json": _json(proof.model_dump(mode="json")),
        }
        _assert_row_values(
            row,
            expected,
            "release proof digest was reused with different facts",
        )
        return _proof_from_row(row)

    @staticmethod
    def _ledger_for_revision(
        session: Session,
        identity: RankingV3ForwardIdentity,
        data_revision: str,
    ) -> RankingV3ForwardLedgerRow:
        ledger = session.get(RankingV3ForwardLedgerRow, _identity_key(identity))
        if ledger is None:
            raise LookupError("Ranking V3 forward ledger does not exist")
        if ledger.data_revision != data_revision:
            raise RankingV3ForwardConflictError(
                "data revision cannot change inside one protocol ledger"
            )
        if ledger.integrity_status != "verified":
            raise RankingV3ForwardStateError(
                ledger.quarantine_reason or "legacy/quarantined forward ledger is read-only"
            )
        return ledger

    @staticmethod
    def _require_pending(ledger: RankingV3ForwardLedgerRow) -> None:
        if ledger.integrity_status != "verified":
            raise RankingV3ForwardStateError(
                ledger.quarantine_reason or "legacy/quarantined forward ledger is read-only"
            )
        if ledger.status != "pending":
            raise RankingV3ForwardStateError(f"{ledger.status} forward ledger is immutable")

    @staticmethod
    def _touch_pending(
        session: Session,
        identity: RankingV3ForwardIdentity,
        **values,
    ) -> None:
        result = session.execute(
            update(RankingV3ForwardLedgerRow)
            .where(
                *_identity_filters(RankingV3ForwardLedgerRow, identity),
                RankingV3ForwardLedgerRow.status == "pending",
                RankingV3ForwardLedgerRow.integrity_status == "verified",
            )
            .values(
                **values,
                revision=RankingV3ForwardLedgerRow.revision + 1,
                updated_at=utc_now(),
            )
        )
        if result.rowcount == 1:
            return
        session.expire_all()
        ledger = session.get(RankingV3ForwardLedgerRow, _identity_key(identity))
        if ledger is None:
            raise LookupError("Ranking V3 forward ledger does not exist")
        RankingV3ForwardRepository._require_pending(ledger)
        raise RankingV3ForwardConflictError("forward ledger revision update was not applied")


def _identity_key(identity: RankingV3ForwardIdentity) -> tuple[str, str, str]:
    return identity.protocol_id, identity.protocol_digest, identity.model_version


def _identity_values(identity: RankingV3ForwardIdentity) -> dict[str, str]:
    return {
        "protocol_id": identity.protocol_id,
        "protocol_digest": identity.protocol_digest,
        "model_version": identity.model_version,
    }


def _identity_filters(row_type, identity: RankingV3ForwardIdentity):
    return (
        row_type.protocol_id == identity.protocol_id,
        row_type.protocol_digest == identity.protocol_digest,
        row_type.model_version == identity.model_version,
    )


def _identity_from_row(row) -> RankingV3ForwardIdentity:
    return RankingV3ForwardIdentity(
        protocol_id=row.protocol_id,
        protocol_digest=row.protocol_digest,
        model_version=row.model_version,
    )


def _begin_immediate(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _one_replayed_row(natural, by_idempotency, *, conflict_message: str):
    if natural is None and by_idempotency is None:
        raise RankingV3ForwardConflictError(
            "forward append conflicted with an unrelated immutable row"
        )
    if natural is not None and by_idempotency is not None and natural is not by_idempotency:
        raise RankingV3ForwardConflictError(conflict_message)
    return natural if natural is not None else by_idempotency


def _assert_row_values(row, expected: Mapping[str, object], message: str) -> None:
    for column, expected_value in expected.items():
        if getattr(row, column) != expected_value:
            raise RankingV3ForwardConflictError(message)


def _ledger_from_row(row: RankingV3ForwardLedgerRow) -> RankingV3ForwardLedger:
    rejection_reasons = _json_load(row.rejection_reasons_json)
    status = row.status
    if row.integrity_status != "verified":
        status = "rejected"
        quarantine_reason = (
            row.quarantine_reason or "legacy/quarantined forward ledger is read-only"
        )
        if quarantine_reason not in rejection_reasons:
            rejection_reasons.append(quarantine_reason)
    return RankingV3ForwardLedger(
        identity=_identity_from_row(row),
        data_revision=row.data_revision,
        status=status,
        first_session_date=row.first_session_date,
        latest_session_date=row.latest_session_date,
        rejection_reasons=rejection_reasons,
        current_release_proof_digest=row.current_release_proof_digest,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _session_from_row(row: RankingV3ForwardSessionRow) -> RankingV3ForwardSession:
    return RankingV3ForwardSession(
        identity=_identity_from_row(row),
        session_date=row.session_date,
        benchmark_id=row.benchmark_id,
        benchmark_return_pct=row.benchmark_return_pct,
        portfolio_equity=row.portfolio_equity,
        stress_portfolio_equity=row.stress_portfolio_equity,
        benchmark_equity=row.benchmark_equity,
        data_revision=row.data_revision,
        idempotency_key=row.idempotency_key,
        fact_digest=row.fact_digest,
        created_at=row.created_at,
    )


def _candidate_from_row(row: RankingV3ForwardCandidateRow) -> RankingV3ShadowCandidate:
    if row.integrity_status != "verified":
        raise RankingV3ForwardStateError(
            row.quarantine_reason or "legacy/quarantined forward candidate is unreadable"
        )
    if not str(row.source_snapshot_id or "").strip():
        raise RankingV3ForwardStateError(
            "persisted forward candidate has no server source snapshot reference"
        )
    source = RankingV3ShadowCandidateInput(
        candidate_id=row.candidate_id,
        source_snapshot_id=row.source_snapshot_id,
        session_date=row.session_date,
        maturity_session_date=row.maturity_session_date,
        instrument_id=row.instrument_id,
        strategy_id=row.strategy_id,
        rank=row.rank,
        score=row.score,
        benchmark_id=row.benchmark_id,
        data_revision=row.data_revision,
        selection_digest=row.selection_digest,
    )
    if stable_digest(source) != row.fact_digest:
        raise RankingV3ForwardStateError(
            "persisted forward candidate facts failed digest validation"
        )
    return RankingV3ShadowCandidate(
        identity=_identity_from_row(row),
        candidate_id=row.candidate_id,
        source_snapshot_id=row.source_snapshot_id,
        session_date=row.session_date,
        maturity_session_date=row.maturity_session_date,
        instrument_id=row.instrument_id,
        strategy_id=row.strategy_id,
        rank=row.rank,
        score=row.score,
        benchmark_id=row.benchmark_id,
        data_revision=row.data_revision,
        selection_digest=row.selection_digest,
        idempotency_key=row.idempotency_key,
        fact_digest=row.fact_digest,
        outcome_status=row.outcome_status,
        outcome_digest=row.outcome_digest,
        outcome_idempotency_key=row.outcome_idempotency_key,
        resolved_on=row.resolved_on,
        gross_return_pct=row.gross_return_pct,
        transaction_cost_pct=row.transaction_cost_pct,
        stress_transaction_cost_pct=row.stress_transaction_cost_pct,
        net_return_pct=row.net_return_pct,
        stress_net_return_pct=row.stress_net_return_pct,
        benchmark_return_pct=row.benchmark_return_pct,
        benchmark_excess_pct=row.benchmark_excess_pct,
        stress_benchmark_excess_pct=row.stress_benchmark_excess_pct,
        max_drawdown_pct=row.max_drawdown_pct,
        outcome_reason=row.outcome_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _evidence_from_row(
    row: RankingV3ForwardGateEvidenceRow,
) -> RankingV3ForwardGateEvidence:
    return RankingV3ForwardGateEvidence(
        identity=_identity_from_row(row),
        evidence_kind=row.evidence_kind,
        evidence_digest=row.evidence_digest,
        data_revision=row.data_revision,
        passed=row.passed,
        payload=_json_load(row.payload_json),
        sequence=row.sequence,
        idempotency_key=row.idempotency_key,
        recorded_at=row.recorded_at,
    )


def _proof_from_row(
    row: RankingV3ForwardReleaseProofRow,
) -> RankingV3ForwardReleaseProof:
    payload = _json_load(row.payload_json)
    proof = RankingV3ForwardReleaseProof.model_validate(payload)
    if (
        proof.proof_digest != row.proof_digest
        or proof.identity != _identity_from_row(row)
        or proof.data_revision != row.data_revision
        or proof.generated_at != row.generated_at
        or proof.ledger_revision != row.ledger_revision
    ):
        raise RankingV3ForwardStateError(
            "persisted release proof metadata does not match its payload"
        )
    return proof


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, Decimal)):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _json_load(value: str):
    return json.loads(value)
