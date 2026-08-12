from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DatabaseError

from qagent.db import create_session_factory, initialize_database
from qagent.storage.fuyao_research import FuyaoResearchRepository
from qagent.storage.tables import FuyaoResearchSnapshotRow


def test_fuyao_research_snapshots_are_deduplicated_and_immutable(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fuyao-research.db'}"
    engine = initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    repository = FuyaoResearchRepository(session_factory)
    payload = {
        "classification": "research_only",
        "decision_weight_applied": False,
        "sections": {"valuation": {"item": [{"pe_ttm": 20.0}]}},
    }

    first = repository.append(
        research_type="stock",
        identity={"instrument_id": "CN:600519", "thscode": "600519.SH"},
        payload=payload,
        source_request_id="req-1",
        source_timestamp="2026-08-12T10:00:00+08:00",
        observed_at=datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc),
    )
    duplicate = repository.append(
        research_type="stock",
        identity={"thscode": "600519.SH", "instrument_id": "CN:600519"},
        payload=payload,
        source_request_id="req-2",
        source_timestamp="2026-08-12T10:01:00+08:00",
    )
    latest = repository.latest(
        research_type="stock",
        identity={"instrument_id": "CN:600519", "thscode": "600519.SH"},
    )

    assert duplicate.snapshot_id == first.snapshot_id
    assert latest is not None
    assert latest.payload == payload
    assert latest.classification == "research_only"
    assert latest.decision_weight_applied is False
    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(FuyaoResearchSnapshotRow))
    assert count == 1

    with pytest.raises(DatabaseError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE fuyao_research_snapshots "
                    "SET classification = 'decision_input' "
                    "WHERE snapshot_id = :snapshot_id"
                ),
                {"snapshot_id": first.snapshot_id},
            )

    with pytest.raises(DatabaseError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM fuyao_research_snapshots "
                    "WHERE snapshot_id = :snapshot_id"
                ),
                {"snapshot_id": first.snapshot_id},
            )
