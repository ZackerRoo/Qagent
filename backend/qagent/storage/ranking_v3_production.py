from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from qagent.backtesting.ranking_v3_production import (
    PRODUCTION_BATCH_SCHEMA_VERSION,
    RankingV3ProductionAdmissionBinding,
    RankingV3ProductionBatch,
    RankingV3ProductionConflictError,
    RankingV3ProductionIdentity,
    RankingV3ProductionIntegrityError,
    RankingV3ProductionSelectionItem,
    require_current_ranking_v3_production_batch,
    require_ranking_v3_production_batch_integrity,
)
from qagent.security.ranking_v3_attestation import RankingV3Attestor, load_attestation_key
from qagent.storage.tables import (
    RankingV3ProductionBatchRow,
    RankingV3ProductionIdempotencyKeyRow,
    RankingV3ProductionSelectionRow,
    OpportunitySnapshotRow,
    ScanRunRow,
)


class RankingV3ProductionRepository:
    """SQLite-backed append-only store for approved production selections."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        attestor: RankingV3Attestor | None = None,
    ):
        self.session_factory = session_factory
        self.attestor = attestor or RankingV3Attestor(load_attestation_key())

    def get_batch_for_session(
        self,
        identity: RankingV3ProductionIdentity,
        session_date: date,
    ) -> RankingV3ProductionBatch | None:
        with self.session_factory() as session:
            row = (
                session.query(RankingV3ProductionBatchRow)
                .filter(
                    RankingV3ProductionBatchRow.identity_digest == identity.identity_digest,
                    RankingV3ProductionBatchRow.session_date == session_date,
                )
                .one_or_none()
            )
            if row is None:
                return None
            batch = _batch_from_row(session, row)
            _require_identity(batch, identity)
            require_ranking_v3_production_batch_integrity(batch, self.attestor)
            _require_source_scan_facts(session, batch, allow_legacy=True)
            return batch

    def get_batch_by_idempotency_key(
        self,
        identity: RankingV3ProductionIdentity,
        idempotency_key: str,
    ) -> RankingV3ProductionBatch | None:
        with self.session_factory() as session:
            alias = session.get(
                RankingV3ProductionIdempotencyKeyRow,
                (identity.identity_digest, idempotency_key),
            )
            if alias is None:
                return None
            _require_alias_integrity(alias)
            row = session.get(RankingV3ProductionBatchRow, alias.batch_fact_digest)
            if row is None:
                raise RankingV3ProductionIntegrityError(
                    "production idempotency alias references a missing batch"
                )
            batch = _batch_from_row(session, row)
            _require_identity(batch, identity)
            require_ranking_v3_production_batch_integrity(batch, self.attestor)
            _require_source_scan_facts(session, batch, allow_legacy=True)
            return batch

    def get_batch_by_fact_digest(
        self,
        identity: RankingV3ProductionIdentity,
        fact_digest: str,
    ) -> RankingV3ProductionBatch | None:
        with self.session_factory() as session:
            row = session.get(RankingV3ProductionBatchRow, fact_digest)
            if row is None:
                return None
            batch = _batch_from_row(session, row)
            _require_identity(batch, identity)
            require_ranking_v3_production_batch_integrity(batch, self.attestor)
            _require_source_scan_facts(session, batch, allow_legacy=True)
            return batch

    def get_selection_by_source_snapshot(
        self,
        identity: RankingV3ProductionIdentity,
        source_snapshot_id: str,
    ) -> RankingV3ProductionAdmissionBinding | None:
        with self.session_factory() as session:
            row = (
                session.query(RankingV3ProductionSelectionRow)
                .filter(
                    RankingV3ProductionSelectionRow.identity_digest == identity.identity_digest,
                    RankingV3ProductionSelectionRow.source_snapshot_id == source_snapshot_id,
                )
                .order_by(RankingV3ProductionSelectionRow.recorded_at.desc())
                .first()
            )
            if row is None:
                return None
            batch = session.get(RankingV3ProductionBatchRow, row.batch_fact_digest)
            if batch is None or batch.identity_digest != identity.identity_digest:
                raise RankingV3ProductionIntegrityError(
                    "production selection is not bound to the requested identity"
                )
            persisted_batch = _batch_from_row(session, batch)
            _require_identity(persisted_batch, identity)
            require_ranking_v3_production_batch_integrity(
                persisted_batch,
                self.attestor,
            )
            _require_source_scan_facts(session, persisted_batch, allow_legacy=True)
            selection = _selection_from_row(row)
            if selection not in persisted_batch.selections:
                raise RankingV3ProductionIntegrityError(
                    "production selection is not a canonical member of its batch"
                )
            return RankingV3ProductionAdmissionBinding.from_batch(
                persisted_batch,
                selection,
            )

    def list_batches(
        self,
        identity: RankingV3ProductionIdentity,
        *,
        limit: int = 100,
    ) -> tuple[RankingV3ProductionBatch, ...]:
        bounded_limit = max(1, min(int(limit), 1000))
        with self.session_factory() as session:
            rows = (
                session.query(RankingV3ProductionBatchRow)
                .filter(RankingV3ProductionBatchRow.identity_digest == identity.identity_digest)
                .order_by(RankingV3ProductionBatchRow.session_date.desc())
                .limit(bounded_limit)
                .all()
            )
            batches = tuple(_batch_from_row(session, row) for row in rows)
            for batch in batches:
                require_ranking_v3_production_batch_integrity(batch, self.attestor)
                _require_source_scan_facts(session, batch, allow_legacy=True)
            return batches

    def append_batch(
        self,
        batch: RankingV3ProductionBatch,
    ) -> RankingV3ProductionBatch:
        requested = RankingV3ProductionBatch.model_validate(batch)
        require_ranking_v3_production_batch_integrity(requested, self.attestor)

        with self.session_factory() as session:
            _begin_immediate(session)
            _require_source_scan_facts(session, requested)
            self._append_batch_row(session, requested)
            self._append_selection_rows(session, requested)
            self._append_alias_row(session, requested)
            row = session.get(RankingV3ProductionBatchRow, requested.fact_digest)
            if row is None:
                raise RankingV3ProductionConflictError(
                    "production batch append did not persist an immutable row"
                )
            persisted = _batch_from_row(session, row)
            require_ranking_v3_production_batch_integrity(persisted, self.attestor)
            _assert_same_batch_facts(
                persisted,
                requested,
                "production batch append resolved to different immutable facts",
            )
            session.commit()
            return persisted

    @staticmethod
    def _append_batch_row(
        session: Session,
        batch: RankingV3ProductionBatch,
    ) -> None:
        identity = batch.identity
        session.execute(
            sqlite_insert(RankingV3ProductionBatchRow)
            .values(
                fact_digest=batch.fact_digest,
                identity_digest=identity.identity_digest,
                release_proof_digest=identity.release_proof_digest,
                validation_run_id=identity.validation_run_id,
                data_revision=identity.data_revision,
                protocol_id=identity.protocol_identity.protocol_id,
                protocol_digest=identity.protocol_identity.protocol_digest,
                model_version=identity.protocol_identity.model_version,
                session_date=batch.session_date,
                candidate_snapshot_digest=batch.candidate_snapshot_digest,
                selection_batch_digest=batch.selection_batch_digest,
                selected_count=batch.selected_count,
                payload_json=_canonical_json(batch.model_dump(mode="json")),
                recorded_at=batch.recorded_at,
            )
            .on_conflict_do_nothing()
        )

        by_fact = session.get(RankingV3ProductionBatchRow, batch.fact_digest)
        by_session = (
            session.query(RankingV3ProductionBatchRow)
            .filter(
                RankingV3ProductionBatchRow.identity_digest == identity.identity_digest,
                RankingV3ProductionBatchRow.session_date == batch.session_date,
            )
            .one_or_none()
        )
        if by_fact is None and by_session is None:
            raise RankingV3ProductionConflictError(
                "production batch append conflicted with an unrelated immutable row"
            )
        if by_fact is None or by_session is None or by_fact.fact_digest != by_session.fact_digest:
            raise RankingV3ProductionConflictError(
                "production session already has a different immutable selection batch"
            )
        persisted = _batch_from_payload_row(by_fact)
        _assert_same_batch_facts(
            persisted,
            batch,
            "production session already has a different immutable selection batch",
        )

    @staticmethod
    def _append_selection_rows(
        session: Session,
        batch: RankingV3ProductionBatch,
    ) -> None:
        for item in batch.selections:
            session.execute(
                sqlite_insert(RankingV3ProductionSelectionRow)
                .values(
                    batch_fact_digest=batch.fact_digest,
                    item_digest=item.item_digest,
                    identity_digest=batch.identity.identity_digest,
                    candidate_id=item.candidate_id,
                    instrument_id=item.instrument_id,
                    source_snapshot_id=item.source_snapshot_id,
                    strategy_id=item.strategy_id,
                    rank=item.rank,
                    score=item.score,
                    source_rank_score=item.source_rank_score,
                    trigger_price=item.trigger_price,
                    initial_stop=item.initial_stop,
                    target_1=item.target_1,
                    allocation_multiplier=item.allocation_multiplier,
                    payload_json=_canonical_json(item.model_dump(mode="json")),
                    recorded_at=batch.recorded_at,
                )
                .on_conflict_do_nothing()
            )
            row = session.get(
                RankingV3ProductionSelectionRow,
                (batch.fact_digest, item.item_digest),
            )
            if row is None:
                raise RankingV3ProductionConflictError(
                    "production selection conflicts with immutable batch membership"
                )
            persisted = _selection_from_row(row)
            if persisted != item:
                raise RankingV3ProductionConflictError(
                    "production selection digest is bound to different facts"
                )

        rows = (
            session.query(RankingV3ProductionSelectionRow)
            .filter(RankingV3ProductionSelectionRow.batch_fact_digest == batch.fact_digest)
            .order_by(RankingV3ProductionSelectionRow.rank)
            .all()
        )
        persisted_items = tuple(_selection_from_row(row) for row in rows)
        if persisted_items != batch.selections:
            raise RankingV3ProductionConflictError(
                "production batch membership is incomplete or contains different selections"
            )

    @staticmethod
    def _append_alias_row(
        session: Session,
        batch: RankingV3ProductionBatch,
    ) -> None:
        alias_payload = {
            "identity_digest": batch.identity.identity_digest,
            "idempotency_key": batch.idempotency_key,
            "batch_fact_digest": batch.fact_digest,
        }
        session.execute(
            sqlite_insert(RankingV3ProductionIdempotencyKeyRow)
            .values(
                **alias_payload,
                payload_json=_canonical_json(alias_payload),
                recorded_at=batch.recorded_at,
            )
            .on_conflict_do_nothing()
        )
        row = session.get(
            RankingV3ProductionIdempotencyKeyRow,
            (batch.identity.identity_digest, batch.idempotency_key),
        )
        if row is None:
            raise RankingV3ProductionConflictError(
                "production idempotency alias append was not persisted"
            )
        _require_alias_integrity(row)
        if row.batch_fact_digest != batch.fact_digest:
            raise RankingV3ProductionConflictError(
                "production idempotency key is already bound to different facts"
            )


def _begin_immediate(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _require_source_scan_facts(
    session: Session,
    batch: RankingV3ProductionBatch,
    *,
    allow_legacy: bool = False,
) -> None:
    if batch.schema_version != PRODUCTION_BATCH_SCHEMA_VERSION and allow_legacy:
        return
    require_current_ranking_v3_production_batch(batch)
    if batch.source_scan_run_id is None:
        raise RankingV3ProductionIntegrityError(
            "current production batch has no source scan run"
        )
    run = session.get(ScanRunRow, batch.source_scan_run_id)
    if run is None:
        raise RankingV3ProductionIntegrityError(
            "current production batch references a missing source scan run"
        )
    expected_times = (
        batch.source_scan_started_at,
        batch.source_scan_completed_at,
        batch.source_scan_recorded_at,
    )
    observed_times = (run.started_at, run.completed_at, run.created_at)
    if observed_times != expected_times:
        raise RankingV3ProductionIntegrityError(
            "current production batch scan timestamps do not match the source run"
        )
    snapshot_run_ids = {
        run_id
        for (run_id,) in (
            session.query(OpportunitySnapshotRow.run_id)
            .filter(
                OpportunitySnapshotRow.snapshot_id.in_(
                    [item.source_snapshot_id for item in batch.selections]
                )
            )
            .distinct()
            .all()
        )
    }
    if batch.selections and snapshot_run_ids != {batch.source_scan_run_id}:
        raise RankingV3ProductionIntegrityError(
            "current production batch selections do not belong to its source scan"
        )


def _batch_from_row(
    session: Session,
    row: RankingV3ProductionBatchRow,
) -> RankingV3ProductionBatch:
    batch = _batch_from_payload_row(row)
    rows = (
        session.query(RankingV3ProductionSelectionRow)
        .filter(RankingV3ProductionSelectionRow.batch_fact_digest == row.fact_digest)
        .order_by(RankingV3ProductionSelectionRow.rank)
        .all()
    )
    persisted_items = tuple(_selection_from_row(item) for item in rows)
    if persisted_items != batch.selections:
        raise RankingV3ProductionIntegrityError(
            "production batch payload does not match persisted selection membership"
        )
    return batch


def _batch_from_payload_row(
    row: RankingV3ProductionBatchRow,
) -> RankingV3ProductionBatch:
    try:
        batch = RankingV3ProductionBatch.model_validate(json.loads(row.payload_json))
    except (TypeError, ValueError) as exc:
        raise RankingV3ProductionIntegrityError(
            "persisted production batch payload is invalid"
        ) from exc
    identity = batch.identity
    expected = {
        "fact_digest": batch.fact_digest,
        "identity_digest": identity.identity_digest,
        "release_proof_digest": identity.release_proof_digest,
        "validation_run_id": identity.validation_run_id,
        "data_revision": identity.data_revision,
        "protocol_id": identity.protocol_identity.protocol_id,
        "protocol_digest": identity.protocol_identity.protocol_digest,
        "model_version": identity.protocol_identity.model_version,
        "session_date": batch.session_date,
        "candidate_snapshot_digest": batch.candidate_snapshot_digest,
        "selection_batch_digest": batch.selection_batch_digest,
        "selected_count": batch.selected_count,
        "recorded_at": batch.recorded_at,
    }
    _assert_row_values(
        row,
        expected,
        "persisted production batch metadata does not match its canonical payload",
    )
    if _canonical_json(batch.model_dump(mode="json")) != row.payload_json:
        raise RankingV3ProductionIntegrityError(
            "persisted production batch payload is not canonical"
        )
    return batch


def _selection_from_row(
    row: RankingV3ProductionSelectionRow,
) -> RankingV3ProductionSelectionItem:
    try:
        item = RankingV3ProductionSelectionItem.model_validate(json.loads(row.payload_json))
    except (TypeError, ValueError) as exc:
        raise RankingV3ProductionIntegrityError(
            "persisted production selection payload is invalid"
        ) from exc
    _assert_row_values(
        row,
        {
            "item_digest": item.item_digest,
            "candidate_id": item.candidate_id,
            "instrument_id": item.instrument_id,
            "source_snapshot_id": item.source_snapshot_id,
            "strategy_id": item.strategy_id,
            "rank": item.rank,
            "score": item.score,
            "source_rank_score": item.source_rank_score,
            "trigger_price": item.trigger_price,
            "initial_stop": item.initial_stop,
            "target_1": item.target_1,
            "allocation_multiplier": item.allocation_multiplier,
        },
        "persisted production selection metadata does not match its canonical payload",
    )
    if _canonical_json(item.model_dump(mode="json")) != row.payload_json:
        raise RankingV3ProductionIntegrityError(
            "persisted production selection payload is not canonical"
        )
    return item


def _require_alias_integrity(row: RankingV3ProductionIdempotencyKeyRow) -> None:
    expected = {
        "identity_digest": row.identity_digest,
        "idempotency_key": row.idempotency_key,
        "batch_fact_digest": row.batch_fact_digest,
    }
    try:
        payload = json.loads(row.payload_json)
    except (TypeError, ValueError) as exc:
        raise RankingV3ProductionIntegrityError(
            "persisted production idempotency alias payload is invalid"
        ) from exc
    if payload != expected or _canonical_json(payload) != row.payload_json:
        raise RankingV3ProductionIntegrityError(
            "persisted production idempotency alias is not canonical"
        )


def _require_identity(
    batch: RankingV3ProductionBatch,
    identity: RankingV3ProductionIdentity,
) -> None:
    if batch.identity != identity:
        raise RankingV3ProductionIntegrityError(
            "persisted production batch belongs to a different identity"
        )


def _assert_same_batch_facts(
    persisted: RankingV3ProductionBatch,
    expected: RankingV3ProductionBatch,
    message: str,
) -> None:
    if persisted.fact_digest != expected.fact_digest or _batch_fact_payload(
        persisted
    ) != _batch_fact_payload(expected):
        raise RankingV3ProductionConflictError(message)


def _batch_fact_payload(batch: RankingV3ProductionBatch) -> dict[str, object]:
    return {
        "identity": batch.identity.model_dump(mode="json"),
        "batch": batch.model_dump(
            mode="json",
            exclude={"identity", "fact_digest", "idempotency_key", "recorded_at"},
        ),
    }


def _assert_row_values(row, expected: dict[str, object], message: str) -> None:
    for column, value in expected.items():
        if getattr(row, column) != value:
            raise RankingV3ProductionIntegrityError(message)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
