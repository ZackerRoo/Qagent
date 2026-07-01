from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from qagent.domain.models import AShareEnhancedSnapshot
from qagent.storage.tables import AShareEnhancedCacheRow


class AShareEnhancedCacheRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def load_snapshot(
        self,
        *,
        provider: str,
        instrument_id: str,
        as_of: date,
        max_age: timedelta,
    ) -> AShareEnhancedSnapshot | None:
        min_cached_at = datetime.now(timezone.utc) - max_age
        with self.session_factory() as session:
            row = (
                session.query(AShareEnhancedCacheRow)
                .filter(
                    AShareEnhancedCacheRow.provider == provider,
                    AShareEnhancedCacheRow.instrument_id == instrument_id,
                    AShareEnhancedCacheRow.as_of == as_of,
                    AShareEnhancedCacheRow.cached_at >= min_cached_at,
                )
                .first()
            )
            if row is None:
                return None
            return AShareEnhancedSnapshot.model_validate(json.loads(row.payload_json))

    def save_snapshot(self, snapshot: AShareEnhancedSnapshot, instrument_id: str) -> None:
        now = datetime.now(timezone.utc)
        values = {
            "provider": snapshot.provider,
            "instrument_id": instrument_id,
            "as_of": snapshot.as_of,
            "payload_json": json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False),
            "cached_at": now,
            "updated_at": now,
        }
        with self.session_factory() as session:
            statement = sqlite_insert(AShareEnhancedCacheRow).values(values)
            excluded = statement.excluded
            statement = statement.on_conflict_do_update(
                index_elements=[
                    AShareEnhancedCacheRow.provider,
                    AShareEnhancedCacheRow.instrument_id,
                    AShareEnhancedCacheRow.as_of,
                ],
                set_={
                    "payload_json": excluded.payload_json,
                    "cached_at": excluded.cached_at,
                    "updated_at": excluded.updated_at,
                },
            )
            session.execute(statement)
            session.commit()
