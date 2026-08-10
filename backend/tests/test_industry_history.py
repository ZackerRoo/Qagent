from __future__ import annotations

from datetime import date, datetime, timezone

from qagent.db import create_session_factory, initialize_database
from qagent.historical_evidence.models import (
    HistoricalEvidenceBundle,
    HistoricalIndustrySnapshot,
    HistoricalInstrumentProfile,
    HistoricalLifecycleManifest,
)
from qagent.jobs.industry_history import sync_point_in_time_industries
from qagent.storage.replay_evidence import ReplayEvidenceRepository


class _IndustryProvider:
    def get_industry_evidence(self, instrument_ids, start, end):
        del start
        return HistoricalEvidenceBundle(
            industries=[
                HistoricalIndustrySnapshot(
                    instrument_id=instrument_id,
                    snapshot_date=end,
                    industry=f"行业-{index % 6}",
                    classification="fixture point in time",
                    provider="fixture_industry",
                )
                for index, instrument_id in enumerate(instrument_ids)
            ]
        )


def test_industry_history_sync_covers_frozen_inventory(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'industry-history.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    replay = ReplayEvidenceRepository(session_factory, "free")
    effective_through = date(2025, 12, 31)
    profiles = [
        HistoricalInstrumentProfile(
            instrument_id=f"CN:{index:06d}",
            snapshot_date=effective_through,
            listing_date=date(2020, 1, 1),
            security_type="stock",
            listing_status="active",
            provider="fixture_inventory",
        )
        for index in range(1, 61)
    ]
    replay.upsert_lifecycle_inventory(
        profiles,
        HistoricalLifecycleManifest(
            provider_mode="free",
            source_revision=1,
            status="ready",
            expected_count=len(profiles),
            stored_count=len(profiles),
            effective_through=effective_through,
            fetched_at=datetime.now(timezone.utc),
        ),
    )
    monkeypatch.setattr(
        "qagent.jobs.industry_history.build_historical_evidence_provider",
        lambda _mode: _IndustryProvider(),
    )

    result = sync_point_in_time_industries(
        provider_mode="free",
        start_date=date(2025, 1, 1),
        end_date=effective_through,
        database_url=database_url,
    )

    assert result["base_dataset_revision"] == 1
    assert result["dataset_revision"] == 2
    assert result["industry_snapshot_rows"] == 60
    assert result["industry_snapshot_instruments"] == 60
    assert result["industry_coverage_ratio"] == 1.0
    industries = replay.industries_as_of(
        [item.instrument_id for item in profiles],
        effective_through,
        revision=2,
    )
    assert len(industries) == 60
