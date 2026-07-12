from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from qagent.db import Base, create_db_engine, create_session_factory
from qagent.historical_evidence.models import (
    HistoricalInstrumentProfile,
    HistoricalLifecycleManifest,
)
from qagent.historical_evidence.providers import BaoStockHistoricalEvidenceProvider
from qagent.storage.replay_evidence import (
    ReplayEvidenceRepository,
    ReplayEvidenceUnavailable,
)
from qagent.storage.tables import HistoricalLifecycleManifestRow


class FakeResult:
    def __init__(self, fields, rows, *, error_code="0", error_msg=""):
        self.fields = fields
        self.rows = rows
        self.error_code = error_code
        self.error_msg = error_msg
        self._index = -1

    def next(self):
        self._index += 1
        return self._index < len(self.rows)

    def get_row_data(self):
        return self.rows[self._index]


class FakeBaoStock:
    def __init__(self, rows):
        self.rows = rows

    def login(self):
        return FakeResult([], [])

    def logout(self):
        return FakeResult([], [])

    def query_stock_basic(self, code="", code_name=""):
        assert code == ""
        assert code_name == ""
        return FakeResult(
            ["code", "code_name", "ipoDate", "outDate", "type", "status"],
            self.rows,
        )


def _repository(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'historical-inventory.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(database_url)
    return session_factory, ReplayEvidenceRepository(session_factory, provider_mode="free")


def _storage_manifest(provider_manifest, revision):
    return HistoricalLifecycleManifest(
        provider_mode="free",
        source_revision=revision,
        status=provider_manifest.status,
        expected_count=provider_manifest.expected_count,
        stored_count=0,
        effective_through=provider_manifest.effective_through,
        error=provider_manifest.error,
        fetched_at=provider_manifest.fetched_at,
    )


def test_inventory_includes_delisted_stock_absent_from_current_catalog(tmp_path):
    client = FakeBaoStock(
        [
            ["sz.000001", "Current", "1991-04-03", "", "1", "1"],
            ["sz.000002", "Delisted", "1991-01-29", "2024-12-31", "1", "0"],
        ]
    )
    provider = BaoStockHistoricalEvidenceProvider(client=client)
    current_catalog = {"CN:000001"}

    profiles = provider.list_historical_instruments(date(2025, 12, 31))
    provider_manifest = provider.get_lifecycle_manifest()
    session_factory, repository = _repository(tmp_path)
    repository.upsert_lifecycle_inventory(
        profiles,
        _storage_manifest(provider_manifest, revision=1),
    )

    assert "CN:000002" not in current_catalog
    assert {profile.instrument_id for profile in profiles} == {
        "CN:000001",
        "CN:000002",
    }
    with session_factory() as session:
        manifest = session.get(HistoricalLifecycleManifestRow, ("free", 1))
    assert manifest is not None
    assert manifest.status == "ready"
    assert manifest.expected_count == 2
    assert manifest.stored_count == 2


def test_inventory_includes_historical_etf_and_listing_dates():
    provider = BaoStockHistoricalEvidenceProvider(
        client=FakeBaoStock(
            [
                ["sh.600000", "Stock", "1999-11-10", "", "1", "1"],
                ["sh.510300", "ETF", "2012-05-28", "2025-06-30", "5", "0"],
            ]
        )
    )

    profiles = provider.list_historical_instruments(date(2025, 12, 31))
    by_id = {profile.instrument_id: profile for profile in profiles}

    assert by_id["CN:600000"].security_type == "stock"
    assert by_id["CN:600000"].listing_date == date(1999, 11, 10)
    assert by_id["CN:510300"].security_type == "etf"
    assert by_id["CN:510300"].listing_date == date(2012, 5, 28)
    assert by_id["CN:510300"].delisting_date == date(2025, 6, 30)
    assert by_id["CN:510300"].listing_status == "delisted"
    assert by_id["CN:510300"].provider == "baostock"


def test_incomplete_inventory_manifest_is_not_ready(tmp_path):
    session_factory, repository = _repository(tmp_path)
    fetched_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repository.upsert_lifecycle_inventory(
        [],
        HistoricalLifecycleManifest(
            provider_mode="free",
            source_revision=1,
            status="ready",
            expected_count=None,
            stored_count=0,
            effective_through=date(2025, 12, 31),
            error="provider response omitted expected_count",
            fetched_at=fetched_at,
        ),
    )
    repository.upsert_lifecycle_inventory(
        [],
        HistoricalLifecycleManifest(
            provider_mode="free",
            source_revision=2,
            status="ready",
            expected_count=1,
            stored_count=1,
            effective_through=date(2025, 12, 31),
            fetched_at=fetched_at,
        ),
    )

    with session_factory() as session:
        manifests = list(
            session.scalars(
                select(HistoricalLifecycleManifestRow).order_by(
                    HistoricalLifecycleManifestRow.source_revision
                )
            )
        )
    assert [manifest.status for manifest in manifests] == ["partial", "partial"]
    assert manifests[0].expected_count is None
    assert manifests[1].expected_count == 1
    assert manifests[1].stored_count == 0
    assert "count mismatch" in (manifests[1].error or "")


def test_latest_partial_inventory_does_not_fall_back_to_ready_revision(tmp_path):
    _, repository = _repository(tmp_path)
    fetched_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repository.upsert_lifecycle_inventory(
        [
            HistoricalInstrumentProfile(
                instrument_id="CN:000001",
                snapshot_date=date(2025, 12, 31),
                listing_date=date(1991, 4, 3),
                security_type="stock",
                listing_status="listed",
                provider="baostock",
            )
        ],
        HistoricalLifecycleManifest(
            provider_mode="free",
            source_revision=1,
            status="ready",
            expected_count=1,
            stored_count=0,
            effective_through=date(2025, 12, 31),
            fetched_at=fetched_at,
        ),
    )
    repository.upsert_lifecycle_inventory(
        [],
        HistoricalLifecycleManifest(
            provider_mode="free",
            source_revision=2,
            status="partial",
            expected_count=None,
            stored_count=0,
            effective_through=date(2025, 12, 31),
            error="provider unavailable",
            fetched_at=fetched_at,
        ),
    )

    with pytest.raises(ReplayEvidenceUnavailable, match="revision 2"):
        repository.lifecycle_inventory(2)


def test_benchmark_inventory_requests_all_required_index_series():
    class FakeBenchmarkProvider:
        def __init__(self):
            self.calls = []

        def get_daily_bars(self, instrument_ids, start, end):
            self.calls.append((instrument_ids, start, end))
            return instrument_ids[0]

    benchmark_provider = FakeBenchmarkProvider()
    provider = BaoStockHistoricalEvidenceProvider(
        client=FakeBaoStock([]),
        benchmark_provider=benchmark_provider,
    )
    start = date(2025, 1, 1)
    end = date(2025, 12, 31)

    series = provider.get_benchmark_series([], start, end)

    required = {
        "CN:000300.IDX",
        "CN:000905.IDX",
        "CN:399006.IDX",
        "CN:000688.IDX",
    }
    assert set(series) == required
    assert {call[0][0] for call in benchmark_provider.calls} == required
    assert all(len(call[0]) == 1 for call in benchmark_provider.calls)
    assert all(call[1:] == (start, end) for call in benchmark_provider.calls)
