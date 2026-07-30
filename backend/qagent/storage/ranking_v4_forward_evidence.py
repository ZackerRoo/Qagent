from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from qagent.backtesting.ranking_v4_forward_evidence import (
    DEFINITION_ATTESTATION_KIND,
    INVENTORY_ATTESTATION_KIND,
    PROOF_ATTESTATION_KIND,
    RETURN_ATTESTATION_KIND,
    RankingV4AttemptInventorySnapshot,
    RankingV4CommonDateReturnRecord,
    RankingV4EvidenceConflictError,
    RankingV4EvidenceIntegrityError,
    RankingV4EvidenceProof,
    RankingV4EvidenceSnapshot,
    RankingV4EvidenceStateError,
    RankingV4ProspectiveDefinition,
    RankingV4ProspectiveModelReturn,
    build_common_date_return_record,
    build_evidence_proof,
    stable_digest,
    verify_snapshot,
)
from qagent.backtesting.ranking_v4_protocol import build_ranking_v4_protocol
from qagent.backtesting.ranking_v4_validation import (
    RankingV4ReturnObservation,
    RankingV4TrialLedgerEvidence,
)
from qagent.security.ranking_v4_attestation import (
    RankingV4EvidenceAttestor,
    load_ranking_v4_attestation_key,
)
from qagent.storage.tables import (
    RankingV4EvidenceDefinitionRow,
    RankingV4EvidenceInventoryRow,
    RankingV4EvidenceProofRow,
    RankingV4EvidenceReturnRow,
)


class RankingV4EvidenceRepository:
    """Append-only persistence for post-freeze Ranking V4 evidence."""

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

    def freeze_definition(
        self,
        definition: RankingV4ProspectiveDefinition,
    ) -> RankingV4ProspectiveDefinition:
        _require_payload_digest(
            definition.definition_digest,
            definition.stable_payload(),
            label="definition",
        )
        self._verify_definition(definition)
        with self.session_factory() as session:
            _begin_immediate(session)
            existing = session.get(
                RankingV4EvidenceDefinitionRow,
                definition.identity.epoch_id,
            )
            if existing is not None:
                persisted = _definition_from_row(existing)
                if persisted != definition:
                    raise RankingV4EvidenceConflictError(
                        "prospective epoch id is already bound to another definition"
                    )
                session.commit()
                return persisted
            digest_row = session.execute(
                select(RankingV4EvidenceDefinitionRow).where(
                    RankingV4EvidenceDefinitionRow.definition_digest
                    == definition.definition_digest
                )
            ).scalar_one_or_none()
            if digest_row is not None:
                raise RankingV4EvidenceConflictError(
                    "definition digest is already bound to another epoch"
                )
            session.add(
                RankingV4EvidenceDefinitionRow(
                    epoch_id=definition.identity.epoch_id,
                    definition_digest=definition.definition_digest,
                    protocol_id=definition.identity.protocol_id,
                    protocol_digest=definition.identity.protocol_digest,
                    model_version=definition.identity.model_version,
                    code_revision=definition.identity.code_revision,
                    experiment_registry_digest=(
                        definition.identity.experiment_registry_digest
                    ),
                    dataset_revision=definition.identity.dataset_revision,
                    evidence_start_date=definition.identity.evidence_start_date,
                    collection_mode=definition.collection_mode,
                    release_scope=definition.release_scope,
                    registered_model_ids_json=_dump(
                        list(definition.registered_model_ids)
                    ),
                    payload_json=_dump(definition.stable_payload()),
                    attestation_json=_dump(
                        definition.attestation.model_dump(mode="json")
                    ),
                    frozen_at=definition.frozen_at,
                )
            )
            session.commit()
            return definition

    def append_inventory(
        self,
        inventory: RankingV4AttemptInventorySnapshot,
    ) -> RankingV4AttemptInventorySnapshot:
        _require_payload_digest(
            inventory.inventory_digest,
            inventory.stable_payload(),
            label="inventory",
        )
        if not self.attestor.verify(
            inventory.attestation,
            expected_kind=INVENTORY_ATTESTATION_KIND,
            expected_payload_digest=inventory.inventory_digest,
        ):
            raise RankingV4EvidenceIntegrityError("inventory signature is invalid")
        with self.session_factory() as session:
            _begin_immediate(session)
            definition = self._definition_for_digest(
                session,
                inventory.definition_digest,
            )
            existing = session.get(
                RankingV4EvidenceInventoryRow,
                inventory.inventory_digest,
            )
            if existing is not None:
                persisted = _inventory_from_row(existing)
                if persisted != inventory:
                    raise RankingV4EvidenceConflictError(
                        "inventory digest was reused with different facts"
                    )
                session.commit()
                return persisted
            latest_row = session.execute(
                select(RankingV4EvidenceInventoryRow)
                .where(
                    RankingV4EvidenceInventoryRow.definition_digest
                    == inventory.definition_digest
                )
                .order_by(RankingV4EvidenceInventoryRow.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
            latest = _inventory_from_row(latest_row) if latest_row is not None else None
            expected_sequence = 1 if latest is None else latest.sequence + 1
            expected_previous = None if latest is None else latest.inventory_digest
            if (
                inventory.sequence != expected_sequence
                or inventory.previous_inventory_digest != expected_previous
            ):
                raise RankingV4EvidenceConflictError(
                    "inventory sequence or predecessor is not append-only"
                )
            if inventory.as_of_date < definition.frozen_at.date():
                raise RankingV4EvidenceIntegrityError(
                    "inventory snapshot cannot predate the signed freeze"
                )
            if latest is not None:
                _require_inventory_extension(latest, inventory)
            session.add(
                RankingV4EvidenceInventoryRow(
                    inventory_digest=inventory.inventory_digest,
                    definition_digest=inventory.definition_digest,
                    sequence=inventory.sequence,
                    as_of_date=inventory.as_of_date,
                    previous_inventory_digest=inventory.previous_inventory_digest,
                    payload_json=_dump(inventory.stable_payload()),
                    attestation_json=_dump(
                        inventory.attestation.model_dump(mode="json")
                    ),
                    recorded_at=inventory.recorded_at,
                )
            )
            session.commit()
            return inventory

    def append_return_record(
        self,
        record: RankingV4CommonDateReturnRecord,
    ) -> RankingV4CommonDateReturnRecord:
        _require_payload_digest(
            record.record_digest,
            record.stable_payload(),
            label="return record",
        )
        if not self.attestor.verify(
            record.attestation,
            expected_kind=RETURN_ATTESTATION_KIND,
            expected_payload_digest=record.record_digest,
        ):
            raise RankingV4EvidenceIntegrityError("return-record signature is invalid")
        with self.session_factory() as session:
            _begin_immediate(session)
            definition = self._definition_for_digest(
                session,
                record.definition_digest,
            )
            if not session.execute(
                select(RankingV4EvidenceInventoryRow.inventory_digest)
                .where(
                    RankingV4EvidenceInventoryRow.definition_digest
                    == record.definition_digest
                )
                .limit(1)
            ).scalar_one_or_none():
                raise RankingV4EvidenceStateError(
                    "returns cannot be recorded before the attempt inventory"
                )
            existing = session.get(RankingV4EvidenceReturnRow, record.record_digest)
            if existing is not None:
                persisted = _return_from_row(existing)
                if persisted != record:
                    raise RankingV4EvidenceConflictError(
                        "return digest was reused with different facts"
                    )
                session.commit()
                return persisted
            latest_row = session.execute(
                select(RankingV4EvidenceReturnRow)
                .where(
                    RankingV4EvidenceReturnRow.definition_digest
                    == record.definition_digest
                )
                .order_by(RankingV4EvidenceReturnRow.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
            latest = _return_from_row(latest_row) if latest_row is not None else None
            expected_sequence = 1 if latest is None else latest.sequence + 1
            expected_previous = None if latest is None else latest.record_digest
            if (
                record.sequence != expected_sequence
                or record.previous_record_digest != expected_previous
            ):
                raise RankingV4EvidenceConflictError(
                    "return sequence or predecessor is not append-only"
                )
            if record.rebalance_date < definition.identity.evidence_start_date:
                raise RankingV4EvidenceIntegrityError(
                    "historical or pre-freeze returns are forbidden"
                )
            if latest is not None and record.rebalance_date <= latest.rebalance_date:
                raise RankingV4EvidenceConflictError(
                    "common rebalance dates must be strictly increasing"
                )
            if record.dataset_revision < definition.identity.dataset_revision:
                raise RankingV4EvidenceIntegrityError(
                    "return data revision predates the frozen baseline"
                )
            if latest is not None and record.dataset_revision < latest.dataset_revision:
                raise RankingV4EvidenceConflictError(
                    "return data revisions must be monotonic"
                )
            if tuple(item.model_id for item in record.model_returns) != (
                definition.registered_model_ids
            ):
                raise RankingV4EvidenceIntegrityError(
                    "return record does not cover the frozen model family"
                )
            session.add(
                RankingV4EvidenceReturnRow(
                    record_digest=record.record_digest,
                    definition_digest=record.definition_digest,
                    sequence=record.sequence,
                    rebalance_date=record.rebalance_date,
                    dataset_revision=record.dataset_revision,
                    previous_record_digest=record.previous_record_digest,
                    model_count=len(record.model_returns),
                    payload_json=_dump(record.stable_payload()),
                    attestation_json=_dump(record.attestation.model_dump(mode="json")),
                    recorded_at=record.recorded_at,
                )
            )
            session.commit()
            return record

    def append_proof(self, proof: RankingV4EvidenceProof) -> RankingV4EvidenceProof:
        _require_payload_digest(
            proof.proof_digest,
            proof.stable_payload(),
            label="proof",
        )
        if not self.attestor.verify(
            proof.attestation,
            expected_kind=PROOF_ATTESTATION_KIND,
            expected_payload_digest=proof.proof_digest,
        ):
            raise RankingV4EvidenceIntegrityError("proof signature is invalid")
        with self.session_factory() as session:
            _begin_immediate(session)
            definition = self._definition_for_digest(
                session,
                proof.definition_digest,
            )
            existing = session.get(RankingV4EvidenceProofRow, proof.proof_digest)
            if existing is not None:
                persisted = _proof_from_row(existing)
                if persisted != proof:
                    raise RankingV4EvidenceConflictError(
                        "proof digest was reused with different facts"
                    )
                session.commit()
                return persisted
            inventories = tuple(
                _inventory_from_row(row)
                for row in session.execute(
                    select(RankingV4EvidenceInventoryRow)
                    .where(
                        RankingV4EvidenceInventoryRow.definition_digest
                        == proof.definition_digest
                    )
                    .order_by(RankingV4EvidenceInventoryRow.sequence)
                ).scalars()
            )
            returns = tuple(
                _return_from_row(row)
                for row in session.execute(
                    select(RankingV4EvidenceReturnRow)
                    .where(
                        RankingV4EvidenceReturnRow.definition_digest
                        == proof.definition_digest
                    )
                    .order_by(RankingV4EvidenceReturnRow.sequence)
                ).scalars()
            )
            if not inventories:
                raise RankingV4EvidenceStateError(
                    "proof cannot be appended without an inventory"
                )
            expected = _expected_proof(
                definition,
                inventories,
                returns,
                generated_at=proof.generated_at,
                attestor=self.attestor,
            )
            if expected != proof:
                raise RankingV4EvidenceIntegrityError(
                    "proof does not attest the current complete evidence chain"
                )
            session.add(
                RankingV4EvidenceProofRow(
                    proof_digest=proof.proof_digest,
                    definition_digest=proof.definition_digest,
                    inventory_digest=proof.inventory_digest,
                    return_record_count=proof.return_record_count,
                    first_rebalance_date=proof.first_rebalance_date,
                    latest_rebalance_date=proof.latest_rebalance_date,
                    returns_chain_digest=proof.returns_chain_digest,
                    release_scope=proof.release_scope,
                    official_release_allowed=proof.official_release_allowed,
                    payload_json=_dump(proof.stable_payload()),
                    attestation_json=_dump(proof.attestation.model_dump(mode="json")),
                    generated_at=proof.generated_at,
                )
            )
            session.commit()
            return proof

    def append_trial_ledger(
        self,
        epoch_id: str,
        *,
        attempt_id: str,
        code_revision: str,
        protocol_digest: str,
        experiment_registry_digest: str,
        dataset_revision: int,
        execution_start_date: date,
        source_result_digest: str,
        trial_ledger: RankingV4TrialLedgerEvidence,
        recorded_at: datetime,
    ) -> RankingV4EvidenceProof:
        snapshot = self.load_snapshot(epoch_id)
        if snapshot is None:
            raise RankingV4EvidenceStateError("prospective evidence epoch does not exist")
        definition = snapshot.definition
        identity = definition.identity
        if (
            code_revision != identity.code_revision
            or protocol_digest != identity.protocol_digest
            or experiment_registry_digest != identity.experiment_registry_digest
        ):
            raise RankingV4EvidenceIntegrityError(
                "walk-forward result identity differs from the frozen definition"
            )
        if dataset_revision < identity.dataset_revision:
            raise RankingV4EvidenceIntegrityError(
                "walk-forward result predates the frozen dataset baseline"
            )
        if execution_start_date != identity.evidence_start_date:
            raise RankingV4EvidenceIntegrityError(
                "prospective result must begin at the frozen evidence start"
            )
        if not source_result_digest.strip():
            raise RankingV4EvidenceIntegrityError("source result digest is missing")
        if not snapshot.inventories:
            raise RankingV4EvidenceStateError(
                "prospective returns require a signed attempt inventory"
            )
        latest_inventory = snapshot.inventories[-1]
        attempts = {
            item.attempt_id: item.definition_digest
            for item in latest_inventory.prospective_attempts
        }
        if attempts.get(attempt_id) != definition.definition_digest:
            raise RankingV4EvidenceIntegrityError(
                "walk-forward attempt was not registered before the evidence epoch"
            )
        if not trial_ledger.immutable:
            raise RankingV4EvidenceIntegrityError("trial ledger is not immutable")
        if trial_ledger.ledger_digest != stable_digest(
            trial_ledger.stable_payload()
        ):
            raise RankingV4EvidenceIntegrityError("trial ledger digest is invalid")
        if (
            trial_ledger.experiment_registry_digest
            != identity.experiment_registry_digest
        ):
            raise RankingV4EvidenceIntegrityError(
                "trial ledger registry differs from the frozen definition"
            )
        if tuple(sorted(trial_ledger.research_attempt_ids)) != (
            latest_inventory.pre_epoch_unverifiable_attempt_ids
        ):
            raise RankingV4EvidenceIntegrityError(
                "trial ledger attempt inventory differs from the signed snapshot"
            )

        series_by_id = {
            item.trial_id: item
            for item in trial_ledger.trial_series
        }
        if tuple(sorted(series_by_id)) != definition.registered_model_ids:
            raise RankingV4EvidenceIntegrityError(
                "trial ledger does not contain the frozen model family"
            )
        observations_by_model: dict[
            str,
            dict[date, RankingV4ReturnObservation],
        ] = {}
        common_dates: tuple[date, ...] | None = None
        for model_id in definition.registered_model_ids:
            observations = series_by_id[model_id].returns
            by_date = {item.rebalance_date: item for item in observations}
            dates = tuple(sorted(by_date))
            if len(by_date) != len(observations):
                raise RankingV4EvidenceIntegrityError(
                    "trial ledger contains duplicate model dates"
                )
            if common_dates is None:
                common_dates = dates
            elif dates != common_dates:
                raise RankingV4EvidenceIntegrityError(
                    "trial ledger model calendars are not identical"
                )
            observations_by_model[model_id] = by_date
        if not common_dates or common_dates[0] != identity.evidence_start_date:
            raise RankingV4EvidenceIntegrityError(
                "trial ledger does not start on the frozen evidence date"
            )
        if any(item < identity.evidence_start_date for item in common_dates):
            raise RankingV4EvidenceIntegrityError(
                "historical returns cannot enter the prospective ledger"
            )

        existing_by_date = {
            item.rebalance_date: item for item in snapshot.return_records
        }
        existing_dates = tuple(sorted(existing_by_date))
        if common_dates[: len(existing_dates)] != existing_dates:
            raise RankingV4EvidenceConflictError(
                "trial ledger does not preserve the persisted date prefix"
            )
        previous_digest = (
            snapshot.return_records[-1].record_digest
            if snapshot.return_records
            else None
        )
        next_sequence = len(snapshot.return_records) + 1
        for rebalance_date in common_dates:
            source_digest = stable_digest(
                {
                    "source_result_digest": source_result_digest,
                    "trial_ledger_digest": trial_ledger.ledger_digest,
                    "rebalance_date": rebalance_date.isoformat(),
                }
            )
            model_returns = tuple(
                RankingV4ProspectiveModelReturn(
                    model_id=model_id,
                    net_return_pct=Decimal(
                        str(
                            observations_by_model[model_id][
                                rebalance_date
                            ].net_return_pct
                        )
                    ),
                    stress_net_return_pct=(
                        Decimal(
                            str(
                                observations_by_model[model_id][
                                    rebalance_date
                                ].stress_net_return_pct
                            )
                        )
                        if observations_by_model[model_id][
                            rebalance_date
                        ].stress_net_return_pct
                        is not None
                        else None
                    ),
                    source_snapshot_digest=source_digest,
                )
                for model_id in definition.registered_model_ids
            )
            existing = existing_by_date.get(rebalance_date)
            if existing is not None:
                if not _same_observed_returns(existing.model_returns, model_returns):
                    raise RankingV4EvidenceConflictError(
                        "trial ledger changed a persisted common-date return"
                    )
                continue
            record = self.append_return_record(
                build_common_date_return_record(
                    definition=definition,
                    sequence=next_sequence,
                    rebalance_date=rebalance_date,
                    dataset_revision=dataset_revision,
                    source_result_digest=source_result_digest,
                    model_returns=model_returns,
                    previous_record_digest=previous_digest,
                    recorded_at=recorded_at,
                    attestor=self.attestor,
                )
            )
            previous_digest = record.record_digest
            next_sequence += 1
        return self.create_proof(epoch_id, generated_at=recorded_at)

    def create_proof(
        self,
        epoch_id: str,
        *,
        generated_at: datetime,
    ) -> RankingV4EvidenceProof:
        snapshot = self.load_snapshot(epoch_id)
        if snapshot is None:
            raise RankingV4EvidenceStateError("prospective evidence epoch does not exist")
        if snapshot.inventories:
            current_inventory_digest = snapshot.inventories[-1].inventory_digest
            current_record_count = len(snapshot.return_records)
            for proof in reversed(snapshot.proofs):
                if (
                    proof.inventory_digest == current_inventory_digest
                    and proof.return_record_count == current_record_count
                ):
                    return proof
        return self.append_proof(
            build_evidence_proof(
                snapshot,
                generated_at=generated_at,
                attestor=self.attestor,
            )
        )

    def load_snapshot(self, epoch_id: str) -> RankingV4EvidenceSnapshot | None:
        with self.session_factory() as session:
            row = session.get(RankingV4EvidenceDefinitionRow, epoch_id)
            if row is None:
                return None
            definition = _definition_from_row(row)
            inventories = tuple(
                _inventory_from_row(item)
                for item in session.execute(
                    select(RankingV4EvidenceInventoryRow)
                    .where(
                        RankingV4EvidenceInventoryRow.definition_digest
                        == definition.definition_digest
                    )
                    .order_by(RankingV4EvidenceInventoryRow.sequence)
                ).scalars()
            )
            returns = tuple(
                _return_from_row(item)
                for item in session.execute(
                    select(RankingV4EvidenceReturnRow)
                    .where(
                        RankingV4EvidenceReturnRow.definition_digest
                        == definition.definition_digest
                    )
                    .order_by(RankingV4EvidenceReturnRow.sequence)
                ).scalars()
            )
            proofs = tuple(
                _proof_from_row(item)
                for item in session.execute(
                    select(RankingV4EvidenceProofRow)
                    .where(
                        RankingV4EvidenceProofRow.definition_digest
                        == definition.definition_digest
                    )
                    .order_by(
                        RankingV4EvidenceProofRow.return_record_count,
                        RankingV4EvidenceProofRow.generated_at,
                    )
                ).scalars()
            )
        snapshot = RankingV4EvidenceSnapshot(
            definition=definition,
            inventories=inventories,
            return_records=returns,
            proofs=proofs,
        )
        verify_snapshot(snapshot, attestor=self.attestor)
        for proof in proofs:
            inventory_index = next(
                (
                    index
                    for index, inventory in enumerate(inventories)
                    if inventory.inventory_digest == proof.inventory_digest
                ),
                None,
            )
            if inventory_index is None:
                raise RankingV4EvidenceIntegrityError(
                    "persisted proof references an unknown inventory"
                )
            expected = _expected_proof(
                definition,
                inventories[: inventory_index + 1],
                returns[: proof.return_record_count],
                generated_at=proof.generated_at,
                attestor=self.attestor,
            )
            if expected != proof:
                raise RankingV4EvidenceIntegrityError(
                    "persisted proof does not match its evidence prefix"
                )
        return snapshot

    def _verify_definition(self, definition: RankingV4ProspectiveDefinition) -> None:
        active = build_ranking_v4_protocol()
        identity = definition.identity
        if (
            identity.protocol_id != active.protocol_id
            or identity.protocol_digest != active.protocol_digest
            or identity.model_version != active.model_version
            or identity.experiment_registry_digest
            != active.experiment_registry.registry_digest
            or definition.registered_model_ids
            != tuple(sorted(active.statistics_definition.pbo_model_ids))
        ):
            raise RankingV4EvidenceIntegrityError(
                "definition is not bound to the active frozen Ranking V4 protocol"
            )
        if not self.attestor.verify(
            definition.attestation,
            expected_kind=DEFINITION_ATTESTATION_KIND,
            expected_payload_digest=definition.definition_digest,
        ):
            raise RankingV4EvidenceIntegrityError("definition signature is invalid")

    @staticmethod
    def _definition_for_digest(
        session: Session,
        definition_digest: str,
    ) -> RankingV4ProspectiveDefinition:
        row = session.execute(
            select(RankingV4EvidenceDefinitionRow).where(
                RankingV4EvidenceDefinitionRow.definition_digest
                == definition_digest
            )
        ).scalar_one_or_none()
        if row is None:
            raise RankingV4EvidenceStateError("frozen definition does not exist")
        return _definition_from_row(row)


def _expected_proof(
    definition: RankingV4ProspectiveDefinition,
    inventories: tuple[RankingV4AttemptInventorySnapshot, ...],
    returns: tuple[RankingV4CommonDateReturnRecord, ...],
    *,
    generated_at: datetime,
    attestor: RankingV4EvidenceAttestor,
) -> RankingV4EvidenceProof:
    return build_evidence_proof(
        RankingV4EvidenceSnapshot(
            definition=definition,
            inventories=inventories,
            return_records=returns,
            proofs=(),
        ),
        generated_at=generated_at,
        attestor=attestor,
    )


def _require_payload_digest(
    claimed_digest: str,
    payload: object,
    *,
    label: str,
) -> None:
    if claimed_digest != stable_digest(payload):
        raise RankingV4EvidenceIntegrityError(f"{label} digest is invalid")


def _same_observed_returns(
    persisted: tuple[RankingV4ProspectiveModelReturn, ...],
    observed: tuple[RankingV4ProspectiveModelReturn, ...],
) -> bool:
    return tuple(
        (item.model_id, item.net_return_pct, item.stress_net_return_pct)
        for item in persisted
    ) == tuple(
        (item.model_id, item.net_return_pct, item.stress_net_return_pct)
        for item in observed
    )


def _require_inventory_extension(
    previous: RankingV4AttemptInventorySnapshot,
    current: RankingV4AttemptInventorySnapshot,
) -> None:
    if current.as_of_date < previous.as_of_date:
        raise RankingV4EvidenceConflictError("inventory dates cannot go backwards")
    if not set(previous.pre_epoch_unverifiable_attempt_ids).issubset(
        current.pre_epoch_unverifiable_attempt_ids
    ):
        raise RankingV4EvidenceConflictError("pre-epoch attempt inventory cannot shrink")
    previous_attempts = {
        item.attempt_id: item.definition_digest for item in previous.prospective_attempts
    }
    current_attempts = {
        item.attempt_id: item.definition_digest for item in current.prospective_attempts
    }
    if any(current_attempts.get(key) != value for key, value in previous_attempts.items()):
        raise RankingV4EvidenceConflictError(
            "prospective attempt definitions cannot change or disappear"
        )


def _definition_from_row(
    row: RankingV4EvidenceDefinitionRow,
) -> RankingV4ProspectiveDefinition:
    return RankingV4ProspectiveDefinition.model_validate(
        {
            **json.loads(row.payload_json),
            "definition_digest": row.definition_digest,
            "attestation": json.loads(row.attestation_json),
        }
    )


def _inventory_from_row(
    row: RankingV4EvidenceInventoryRow,
) -> RankingV4AttemptInventorySnapshot:
    return RankingV4AttemptInventorySnapshot.model_validate(
        {
            **json.loads(row.payload_json),
            "inventory_digest": row.inventory_digest,
            "attestation": json.loads(row.attestation_json),
        }
    )


def _return_from_row(row: RankingV4EvidenceReturnRow) -> RankingV4CommonDateReturnRecord:
    return RankingV4CommonDateReturnRecord.model_validate(
        {
            **json.loads(row.payload_json),
            "record_digest": row.record_digest,
            "attestation": json.loads(row.attestation_json),
        }
    )


def _proof_from_row(row: RankingV4EvidenceProofRow) -> RankingV4EvidenceProof:
    return RankingV4EvidenceProof.model_validate(
        {
            **json.loads(row.payload_json),
            "proof_digest": row.proof_digest,
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
