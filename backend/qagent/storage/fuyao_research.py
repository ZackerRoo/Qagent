from __future__ import annotations

import json
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import FuyaoResearchSnapshotRow


class FuyaoResearchSnapshot(BaseModel):
    snapshot_id: str
    provider: str
    research_type: str
    identity: dict[str, Any]
    classification: str
    decision_weight_applied: bool
    payload_digest: str
    source_request_id: str | None = None
    source_timestamp: str | None = None
    observed_at: datetime
    payload: dict[str, Any]
    created_at: datetime


class FuyaoResearchRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def append(
        self,
        *,
        research_type: str,
        identity: dict[str, Any],
        payload: dict[str, Any],
        source_request_id: str | None = None,
        source_timestamp: str | None = None,
        observed_at: datetime | None = None,
    ) -> FuyaoResearchSnapshot:
        normalized_type = research_type.strip().lower()
        if not normalized_type:
            raise ValueError("research_type must not be empty")
        identity_json = _canonical_json(identity)
        identity_digest = _digest(identity_json)
        payload_json = _canonical_json(payload)
        payload_digest = _digest(payload_json)
        snapshot_identity = f"{normalized_type}:{identity_digest}:{payload_digest}"
        snapshot_id = f"fuyao-research-{_digest(snapshot_identity)}"
        observed = observed_at or datetime.now(timezone.utc)
        if observed.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")

        with self.session_factory() as session:
            existing = _matching_row(
                session,
                normalized_type,
                identity_digest,
                payload_digest,
            )
            if existing is not None:
                return _from_row(existing)
            row = FuyaoResearchSnapshotRow(
                snapshot_id=snapshot_id,
                provider="fuyao",
                research_type=normalized_type,
                identity_digest=identity_digest,
                identity_json=identity_json,
                classification="research_only",
                decision_weight_applied=False,
                payload_digest=payload_digest,
                source_request_id=source_request_id,
                source_timestamp=source_timestamp,
                observed_at=observed,
                payload_json=payload_json,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = _matching_row(
                    session,
                    normalized_type,
                    identity_digest,
                    payload_digest,
                )
                if existing is None:
                    raise
                return _from_row(existing)
            session.refresh(row)
            return _from_row(row)

    def latest(
        self,
        *,
        research_type: str,
        identity: dict[str, Any],
    ) -> FuyaoResearchSnapshot | None:
        identity_digest = _digest(_canonical_json(identity))
        with self.session_factory() as session:
            row = session.scalars(
                select(FuyaoResearchSnapshotRow)
                .where(
                    FuyaoResearchSnapshotRow.research_type
                    == research_type.strip().lower(),
                    FuyaoResearchSnapshotRow.identity_digest == identity_digest,
                )
                .order_by(
                    FuyaoResearchSnapshotRow.observed_at.desc(),
                    FuyaoResearchSnapshotRow.created_at.desc(),
                )
                .limit(1)
            ).first()
            return _from_row(row) if row is not None else None

    def latest_for_type(self, research_type: str) -> FuyaoResearchSnapshot | None:
        normalized_type = research_type.strip().lower()
        with self.session_factory() as session:
            row = session.scalars(
                select(FuyaoResearchSnapshotRow)
                .where(FuyaoResearchSnapshotRow.research_type == normalized_type)
                .order_by(
                    FuyaoResearchSnapshotRow.observed_at.desc(),
                    FuyaoResearchSnapshotRow.created_at.desc(),
                )
                .limit(1)
            ).first()
            return _from_row(row) if row is not None else None

    def list_for_type(
        self,
        research_type: str,
        *,
        limit: int = 250,
    ) -> list[FuyaoResearchSnapshot]:
        if limit <= 0 or limit > 2_000:
            raise ValueError("limit must be between 1 and 2000")
        normalized_type = research_type.strip().lower()
        with self.session_factory() as session:
            rows = session.scalars(
                select(FuyaoResearchSnapshotRow)
                .where(FuyaoResearchSnapshotRow.research_type == normalized_type)
                .order_by(
                    FuyaoResearchSnapshotRow.observed_at.desc(),
                    FuyaoResearchSnapshotRow.created_at.desc(),
                )
                .limit(limit)
            ).all()
            return [_from_row(row) for row in rows]


def _matching_row(
    session: Session,
    research_type: str,
    identity_digest: str,
    payload_digest: str,
) -> FuyaoResearchSnapshotRow | None:
    return session.scalars(
        select(FuyaoResearchSnapshotRow).where(
            FuyaoResearchSnapshotRow.research_type == research_type,
            FuyaoResearchSnapshotRow.identity_digest == identity_digest,
            FuyaoResearchSnapshotRow.payload_digest == payload_digest,
        )
    ).first()


def _from_row(row: FuyaoResearchSnapshotRow) -> FuyaoResearchSnapshot:
    return FuyaoResearchSnapshot(
        snapshot_id=row.snapshot_id,
        provider=row.provider,
        research_type=row.research_type,
        identity=json.loads(row.identity_json),
        classification=row.classification,
        decision_weight_applied=row.decision_weight_applied,
        payload_digest=row.payload_digest,
        source_request_id=row.source_request_id,
        source_timestamp=row.source_timestamp,
        observed_at=row.observed_at,
        payload=json.loads(row.payload_json),
        created_at=row.created_at,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
