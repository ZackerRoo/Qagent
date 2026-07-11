from datetime import date

from qagent.db import Base, create_db_engine, create_session_factory
from qagent.historical_evidence.models import (
    HistoricalEvidenceBundle,
    HistoricalIndexMembership,
    HistoricalIndexSnapshot,
    HistoricalIndustrySnapshot,
    HistoricalInstrumentProfile,
    HistoricalTradabilityPoint,
)
from qagent.storage.repository import QagentRepository


def test_repository_persists_and_summarizes_historical_evidence(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'historical-evidence.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    repo = QagentRepository(create_session_factory(database_url))
    bundle = HistoricalEvidenceBundle(
        tradability=[
            HistoricalTradabilityPoint(
                instrument_id="CN:000001",
                trade_date=date(2026, 1, 5),
                trading_status="trading",
                is_st=False,
                pct_change_pct=1.25,
                provider="baostock",
            ),
            HistoricalTradabilityPoint(
                instrument_id="CN:000001",
                trade_date=date(2026, 1, 6),
                trading_status="suspended",
                is_st=False,
                provider="baostock",
            ),
        ],
        profiles=[
            HistoricalInstrumentProfile(
                instrument_id="CN:000001",
                name="平安银行",
                snapshot_date=date(2026, 1, 9),
                listing_date=date(1991, 4, 3),
                delisting_date=None,
                security_type="1",
                listing_status="1",
                provider="baostock",
            )
        ],
        industries=[
            HistoricalIndustrySnapshot(
                instrument_id="CN:000001",
                snapshot_date=date(2026, 1, 9),
                industry="银行",
                classification="申万一级行业",
                provider="baostock",
            )
        ],
        index_snapshots=[
            HistoricalIndexSnapshot(
                index_id="CN:000300.IDX",
                snapshot_date=date(2026, 1, 9),
                status="ready",
                member_count=1,
                provider="baostock",
            )
        ],
        index_memberships=[
            HistoricalIndexMembership(
                index_id="CN:000300.IDX",
                snapshot_date=date(2026, 1, 9),
                instrument_id="CN:000001",
                provider="baostock",
            )
        ],
    )

    first = repo.upsert_historical_evidence("free", bundle)
    second = repo.upsert_historical_evidence("free", bundle)
    universe_rows = repo.upsert_historical_universe_snapshots(
        bundle.profiles,
        [date(2026, 1, 1), date(2026, 1, 9)],
    )
    stats = repo.historical_evidence_stats(
        "free",
        ["CN:000001"],
        date(2026, 1, 1),
        date(2026, 1, 9),
    )["CN:000001"]
    index_stats = repo.historical_index_snapshot_stats(
        "free",
        date(2026, 1, 1),
        date(2026, 1, 9),
    )

    assert first == {
        "tradability": 2,
        "profiles": 1,
        "industries": 1,
        "index_snapshots": 1,
        "index_memberships": 1,
    }
    assert second == first
    assert universe_rows == 2
    assert stats.tradability_rows == 2
    assert stats.suspended_rows == 1
    assert stats.profile_rows == 1
    assert stats.industry_rows == 1
    assert stats.benchmark_membership_rows == 1
    assert stats.industries == ["银行"]
    assert index_stats.ready_snapshots == 1
    assert index_stats.failed_snapshots == 0
    assert repo.tradable_universe_snapshot_stats(
        ["CN:000001"],
        date(2026, 1, 1),
        date(2026, 1, 9),
    )["CN:000001"] == (2, date(2026, 1, 1), date(2026, 1, 9))
