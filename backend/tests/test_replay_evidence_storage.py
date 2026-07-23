from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import delete, event, func, inspect, select, text, update

from qagent.db import Base, create_db_engine, create_session_factory, initialize_database
from qagent.historical_evidence.models import (
    HistoricalCorporateAction,
    HistoricalEvidenceBundle,
    HistoricalIndexMembership,
    HistoricalIndexSnapshot,
    HistoricalIndustrySnapshot,
    HistoricalInstrumentProfile,
    HistoricalLifecycleManifest,
    HistoricalReplayBar,
    HistoricalTradabilityPoint,
)
from qagent.storage.replay_evidence import (
    ActionCoverageRecord,
    DatasetLeaseBusy,
    ReplayEvidenceRepository,
    ReplayEvidenceUnavailable,
    SourceWriteBlocked,
    StaleCheckpointRevision,
)
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import (
    FundamentalSnapshotRow,
    HistoricalCorporateActionCoverageRow,
    HistoricalCorporateActionRow,
    HistoricalDataRevisionRow,
    HistoricalDatasetLeaseRow,
    HistoricalIndexMembershipRow,
    HistoricalIndexSnapshotRow,
    HistoricalIndustrySnapshotRow,
    HistoricalInstrumentProfileRow,
    HistoricalLifecycleManifestRow,
    HistoricalReplayBarRow,
    HistoricalReplayUniverseMemberRow,
    HistoricalTradabilityRow,
)
from qagent.strategy_data.models import FundamentalSnapshot


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def storage(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'replay-evidence.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(database_url)
    clock = MutableClock()
    statuses: dict[str, str] = {}

    def make_repo(
        provider_mode: str = "free",
        owner_run_id: str | None = None,
    ) -> ReplayEvidenceRepository:
        return ReplayEvidenceRepository(
            session_factory,
            provider_mode=provider_mode,
            owner_run_id=owner_run_id,
            clock=clock,
            run_status_lookup=statuses.get,
        )

    return session_factory, clock, statuses, make_repo


def _bar(*, revision: int = 1, close: str = "10.25") -> HistoricalReplayBar:
    price = Decimal(close)
    return HistoricalReplayBar(
        provider_mode="free",
        instrument_id="CN:000001",
        trade_date=date(2025, 1, 2),
        raw_open=price,
        raw_high=price,
        raw_low=price,
        raw_close=price,
        adjusted_open=price,
        adjusted_high=price,
        adjusted_low=price,
        adjusted_close=price,
        volume=Decimal("1000"),
        turnover=Decimal("10250"),
        adjustment_factor=Decimal("1"),
        adjustment_mode="qfq",
        source_provider="fixture",
        dataset_revision=revision,
        fetched_at=datetime(2025, 1, 2, 9, 0, tzinfo=timezone.utc),
    )


def _action(
    *, revision: int = 1, cash_per_share: str = "0.25"
) -> HistoricalCorporateAction:
    return HistoricalCorporateAction(
        provider_mode="free",
        instrument_id="CN:000001",
        action_id="cash-2025",
        announcement_date=date(2024, 12, 20),
        record_date=date(2025, 1, 2),
        ex_date=date(2025, 1, 3),
        effective_date=date(2025, 1, 3),
        payable_date=date(2025, 1, 10),
        action_type="cash_dividend",
        cash_per_share=Decimal(cash_per_share),
        source_provider="fixture",
        dataset_revision=revision,
        fetched_at=datetime(2024, 12, 20, 9, 0, tzinfo=timezone.utc),
    )


def _profile(
    instrument_id: str,
    *,
    listing_date: date | None = date(2020, 1, 1),
    delisting_date: date | None = None,
    security_type: str | None = "1",
) -> HistoricalInstrumentProfile:
    return HistoricalInstrumentProfile(
        instrument_id=instrument_id,
        snapshot_date=date(2025, 12, 31),
        listing_date=listing_date,
        delisting_date=delisting_date,
        security_type=security_type,
        listing_status="1",
        provider="fixture",
    )


def _lifecycle_manifest(
    provider_mode: str,
    revision: int,
    count: int,
) -> HistoricalLifecycleManifest:
    return HistoricalLifecycleManifest(
        provider_mode=provider_mode,
        source_revision=revision,
        status="ready",
        expected_count=count,
        stored_count=count,
        effective_through=date(2025, 12, 31),
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_bar_and_action_upserts_are_idempotent(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo()

    assert repo.upsert_replay_bars([_bar(), _bar()], revision=1) == 1
    assert repo.upsert_replay_bars([_bar()], revision=1) == 1
    assert repo.upsert_corporate_actions([_action(revision=2)], revision=2) == 1
    assert repo.upsert_corporate_actions([_action(revision=2)], revision=2) == 1

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(HistoricalReplayBarRow)) == 1
        assert (
            session.scalar(select(func.count()).select_from(HistoricalCorporateActionRow))
            == 1
        )
    assert repo.current_revision() == 2
    assert repo.replay_bars(
        ["CN:000001"], date(2025, 1, 1), date(2025, 1, 3), 2
    )[0].raw_close == Decimal("10.25000000")
    assert repo.replay_bar_rows(
        ["CN:000001"], date(2025, 1, 1), date(2025, 1, 3), 2
    )[0].raw_close == Decimal("10.25000000")


def test_same_revision_bar_payload_is_immutable(storage):
    _, _, _, make_repo = storage
    repo = make_repo()
    repo.upsert_replay_bars([_bar(close="10")], revision=1)

    with pytest.raises(ValueError, match="immutable"):
        repo.upsert_replay_bars([_bar(close="11")], revision=1)

    stored = repo.replay_bars(
        ["CN:000001"], date(2025, 1, 2), date(2025, 1, 2), revision=1
    )
    assert stored[0].raw_close == Decimal("10.00000000")


def test_conflicting_duplicate_identity_in_one_source_batch_is_rejected(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo()

    with pytest.raises(ValueError, match="immutable"):
        repo.upsert_replay_bars(
            [_bar(close="10"), _bar(close="11")], revision=1
        )

    assert repo.current_revision() == 0
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(HistoricalReplayBarRow)
        ) == 0


def test_same_revision_action_payload_is_immutable(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo()
    repo.upsert_corporate_actions([_action(cash_per_share="0.25")], revision=1)

    with pytest.raises(ValueError, match="immutable"):
        repo.upsert_corporate_actions(
            [_action(cash_per_share="0.30")], revision=1
        )

    with session_factory() as session:
        stored = session.scalar(select(HistoricalCorporateActionRow))
    assert stored is not None
    assert stored.cash_per_share == Decimal("0.25000000")


def test_bar_and_action_revisions_preserve_old_payloads(storage):
    session_factory, _, _, make_repo = storage
    bars = make_repo("bars")
    bars.upsert_replay_bars(
        [_bar(revision=1, close="10").model_copy(update={"provider_mode": "bars"})],
        revision=1,
    )
    bars.upsert_replay_bars(
        [_bar(revision=2, close="11").model_copy(update={"provider_mode": "bars"})],
        revision=2,
    )
    actions = make_repo("actions")
    actions.upsert_corporate_actions(
        [
            _action(revision=1, cash_per_share="0.25").model_copy(
                update={"provider_mode": "actions"}
            )
        ],
        revision=1,
    )
    actions.upsert_corporate_actions(
        [
            _action(revision=2, cash_per_share="0.30").model_copy(
                update={"provider_mode": "actions"}
            )
        ],
        revision=2,
    )

    old_bars = bars.replay_bars(
        ["CN:000001"], date(2025, 1, 2), date(2025, 1, 2), revision=1
    )
    new_bars = bars.replay_bars(
        ["CN:000001"], date(2025, 1, 2), date(2025, 1, 2), revision=2
    )
    with session_factory() as session:
        stored_bar_revisions = list(
            session.scalars(
                select(HistoricalReplayBarRow.dataset_revision)
                .where(HistoricalReplayBarRow.provider_mode == "bars")
                .order_by(HistoricalReplayBarRow.dataset_revision)
            )
        )
        stored_action_payloads = list(
            session.execute(
                select(
                    HistoricalCorporateActionRow.dataset_revision,
                    HistoricalCorporateActionRow.cash_per_share,
                )
                .where(HistoricalCorporateActionRow.provider_mode == "actions")
                .order_by(HistoricalCorporateActionRow.dataset_revision)
            )
        )

    assert [bar.raw_close for bar in old_bars] == [Decimal("10.00000000")]
    assert [bar.raw_close for bar in new_bars] == [Decimal("11.00000000")]
    assert stored_bar_revisions == [1, 2]
    assert stored_action_payloads == [
        (1, Decimal("0.25000000")),
        (2, Decimal("0.30000000")),
    ]


def test_fundamental_and_action_coverage_reads_select_requested_revision(storage):
    session_factory, _, _, make_repo = storage
    fundamentals = make_repo("fundamentals")
    first = FundamentalSnapshot(
        instrument_id="CN:000001",
        as_of_date=date(2025, 3, 31),
        pe_ratio=Decimal("8.5"),
        provider="fixture",
    )
    fundamentals.upsert_fundamentals([first], revision=1)
    fundamentals.upsert_fundamentals(
        [first.model_copy(update={"pe_ratio": Decimal("9.5")})], revision=2
    )
    coverage = make_repo("coverage")
    first_coverage = ActionCoverageRecord(
        instrument_id="CN:000001",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        status="ready",
        action_count=1,
        source_provider="fixture",
    )
    coverage.upsert_action_coverage([first_coverage], revision=1)
    coverage.upsert_action_coverage(
        [first_coverage.model_copy(update={"status": "partial", "action_count": 0})],
        revision=2,
    )

    old_fundamental = fundamentals.fundamentals_as_of(
        ["CN:000001"], date(2025, 6, 30), revision=1
    )
    new_fundamental = fundamentals.fundamentals_as_of(
        ["CN:000001"], date(2025, 6, 30), revision=2
    )
    old_coverage = coverage.action_coverage(
        ["CN:000001"], date(2025, 1, 1), date(2025, 12, 31), revision=1
    )
    new_coverage = coverage.action_coverage(
        ["CN:000001"], date(2025, 1, 1), date(2025, 12, 31), revision=2
    )
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(FundamentalSnapshotRow).where(
                FundamentalSnapshotRow.provider_mode == "fundamentals"
            )
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(HistoricalCorporateActionCoverageRow).where(
                HistoricalCorporateActionCoverageRow.provider_mode == "coverage"
            )
        ) == 2

    assert old_fundamental["CN:000001"].pe_ratio == Decimal("8.500000")
    assert new_fundamental["CN:000001"].pe_ratio == Decimal("9.500000")
    assert old_coverage["CN:000001"].status == "ready"
    assert new_coverage["CN:000001"].status == "partial"


def test_point_in_time_revisions_preserve_old_payloads_without_duplicate_reads(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo("point-in-time")

    def bundle(*, trading_status: str, industry: str) -> HistoricalEvidenceBundle:
        return HistoricalEvidenceBundle(
            tradability=[
                HistoricalTradabilityPoint(
                    instrument_id="CN:000001",
                    trade_date=date(2025, 3, 31),
                    trading_status=trading_status,
                    provider="fixture",
                )
            ],
            industries=[
                HistoricalIndustrySnapshot(
                    instrument_id="CN:000001",
                    snapshot_date=date(2025, 3, 31),
                    industry=industry,
                    provider="fixture",
                )
            ],
            index_snapshots=[
                HistoricalIndexSnapshot(
                    index_id="CN:000300.IDX",
                    snapshot_date=date(2025, 3, 31),
                    status="ready",
                    member_count=1,
                    provider="fixture",
                )
            ],
            index_memberships=[
                HistoricalIndexMembership(
                    index_id="CN:000300.IDX",
                    snapshot_date=date(2025, 3, 31),
                    instrument_id="CN:000001",
                    provider="fixture",
                )
            ],
        )

    repo.upsert_point_in_time_evidence(
        bundle(trading_status="trading", industry="Banking"), revision=1
    )
    repo.upsert_point_in_time_evidence(
        bundle(trading_status="suspended", industry="Fintech"), revision=2
    )

    old_tradability = repo.tradability_on(
        ["CN:000001"], date(2025, 3, 31), revision=1
    )
    new_tradability = repo.tradability_on(
        ["CN:000001"], date(2025, 3, 31), revision=2
    )
    old_industry = repo.industries_as_of(
        ["CN:000001"], date(2025, 3, 31), revision=1
    )
    new_industry = repo.industries_as_of(
        ["CN:000001"], date(2025, 3, 31), revision=2
    )
    memberships = repo.memberships_as_of(
        ["CN:000001"], date(2025, 3, 31), revision=2
    )
    with session_factory() as session:
        revision_counts = {
            model.__tablename__: session.scalar(
                select(func.count()).select_from(model).where(
                    model.provider_mode == "point-in-time"
                )
            )
            for model in (
                HistoricalTradabilityRow,
                HistoricalIndustrySnapshotRow,
                HistoricalIndexSnapshotRow,
                HistoricalIndexMembershipRow,
            )
        }

    assert old_tradability["CN:000001"].trading_status == "trading"
    assert new_tradability["CN:000001"].trading_status == "suspended"
    assert old_industry["CN:000001"].industry == "Banking"
    assert new_industry["CN:000001"].industry == "Fintech"
    assert [item.index_id for item in memberships["CN:000001"]] == [
        "CN:000300.IDX"
    ]
    assert revision_counts == {
        "historical_tradability": 2,
        "historical_industry_snapshots": 2,
        "historical_index_snapshots": 2,
        "historical_index_memberships": 2,
    }


def test_same_revision_generic_source_payload_is_immutable(storage):
    _, _, _, make_repo = storage
    repo = make_repo()
    first = HistoricalEvidenceBundle(
        tradability=[
            HistoricalTradabilityPoint(
                instrument_id="CN:000001",
                trade_date=date(2025, 1, 2),
                trading_status="trading",
                provider="fixture",
            )
        ]
    )
    conflicting = first.model_copy(deep=True)
    conflicting.tradability[0].trading_status = "suspended"
    repo.upsert_point_in_time_evidence(first, revision=1)

    with pytest.raises(ValueError, match="immutable"):
        repo.upsert_point_in_time_evidence(conflicting, revision=1)

    stored = repo.tradability_on(["CN:000001"], date(2025, 1, 2), revision=1)
    assert stored["CN:000001"].trading_status == "trading"


def test_same_revision_identical_generic_payload_is_idempotent(storage):
    session_factory, clock, _, make_repo = storage
    repo = make_repo()
    bundle = HistoricalEvidenceBundle(
        tradability=[
            HistoricalTradabilityPoint(
                instrument_id="CN:000001",
                trade_date=date(2025, 1, 2),
                trading_status="trading",
                provider="fixture",
            )
        ]
    )
    repo.upsert_point_in_time_evidence(bundle, revision=1)
    with session_factory() as session:
        first_fetched_at = session.scalar(select(HistoricalTradabilityRow.fetched_at))
    clock.advance(timedelta(minutes=1))

    assert repo.upsert_point_in_time_evidence(bundle, revision=1)["tradability"] == 1

    with session_factory() as session:
        retried_fetched_at = session.scalar(select(HistoricalTradabilityRow.fetched_at))
    assert retried_fetched_at == first_fetched_at


def test_empty_evidence_bundle_does_not_create_or_increment_revision(storage):
    _, _, _, make_repo = storage
    repo = make_repo()

    result = repo.upsert_point_in_time_evidence(
        HistoricalEvidenceBundle(data_health={"historical_evidence_cache": "reused"})
    )

    assert result == {
        "tradability": 0,
        "profiles": 0,
        "industries": 0,
        "index_snapshots": 0,
        "index_memberships": 0,
    }
    assert repo.current_revision() == 0


def test_identical_fundamental_retry_batch_is_idempotent(storage):
    session_factory, clock, _, _ = storage

    def advancing_clock():
        value = clock()
        clock.advance(timedelta(microseconds=1))
        return value

    repo = ReplayEvidenceRepository(session_factory, clock=advancing_clock)
    snapshot = FundamentalSnapshot(
        instrument_id="CN:000001",
        as_of_date=date(2025, 3, 31),
        pe_ratio=Decimal("8.5"),
        provider="fixture",
    )
    repo.upsert_fundamentals([snapshot], revision=1)
    with session_factory() as session:
        first = session.scalar(select(FundamentalSnapshotRow))
        assert first is not None
        first_cached_at = first.cached_at
        first_updated_at = first.updated_at
    clock.advance(timedelta(minutes=1))

    assert repo.upsert_fundamentals([snapshot, snapshot], revision=1) == 1

    with session_factory() as session:
        retried = session.scalar(select(FundamentalSnapshotRow))
        assert retried is not None
        assert retried.pe_ratio == Decimal("8.500000")
        assert retried.cached_at == first_cached_at
        assert retried.updated_at == first_updated_at


def test_immutable_retry_verification_query_count_is_bounded_for_100_rows(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo("retry-batch")
    snapshots = [
        FundamentalSnapshot(
            instrument_id=f"CN:{index:06d}",
            as_of_date=date(2025, 3, 31),
            pe_ratio=Decimal("8.5"),
            provider="fixture",
        )
        for index in range(100)
    ]
    repo.upsert_fundamentals(snapshots, revision=1)
    engine = session_factory.kw["bind"]
    statements: list[str] = []

    def count_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        assert repo.upsert_fundamentals(snapshots, revision=1) == 100
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert len(statements) <= 4


def test_conflicting_fundamental_retry_batch_is_rejected(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo()
    first = FundamentalSnapshot(
        instrument_id="CN:000001",
        as_of_date=date(2025, 3, 31),
        pe_ratio=Decimal("8.5"),
        provider="fixture",
    )
    conflicting = first.model_copy(update={"pe_ratio": Decimal("9.5")})
    repo.upsert_fundamentals([first], revision=1)

    with pytest.raises(ValueError, match="immutable"):
        repo.upsert_fundamentals([first, conflicting], revision=1)

    with session_factory() as session:
        stored = session.scalar(select(FundamentalSnapshotRow))
        assert stored is not None
        assert stored.pe_ratio == Decimal("8.500000")


def test_fundamental_as_of_never_returns_future_snapshot(storage):
    _, _, _, make_repo = storage
    repo = make_repo()
    repo.upsert_fundamentals(
        [
            FundamentalSnapshot(
                instrument_id="CN:000001",
                as_of_date=date(2025, 3, 31),
                pe_ratio=Decimal("8.5"),
                provider="fixture",
            ),
            FundamentalSnapshot(
                instrument_id="CN:000001",
                as_of_date=date(2025, 9, 30),
                pe_ratio=Decimal("9.5"),
                provider="fixture",
            ),
        ],
        revision=1,
    )

    result = repo.fundamentals_as_of(
        ["CN:000001"], date(2025, 6, 30), revision=1
    )

    assert result["CN:000001"].as_of_date == date(2025, 3, 31)
    assert result["CN:000001"].pe_ratio == Decimal("8.500000")


def test_latest_as_of_queries_load_only_100_selected_rows(storage):
    session_factory, _, _, make_repo = storage
    fundamentals = make_repo("fundamental-performance")
    industries = make_repo("industry-performance")
    instrument_ids = [f"CN:{index:06d}" for index in range(100)]
    snapshot_dates = [date(2024, month, 1) for month in range(1, 6)]
    fundamentals.upsert_fundamentals(
        [
            FundamentalSnapshot(
                instrument_id=instrument_id,
                as_of_date=snapshot_date,
                pe_ratio=Decimal(str(8 + month)),
                provider="fixture",
            )
            for instrument_id in instrument_ids
            for month, snapshot_date in enumerate(snapshot_dates)
        ],
        revision=1,
    )
    industries.upsert_point_in_time_evidence(
        HistoricalEvidenceBundle(
            industries=[
                HistoricalIndustrySnapshot(
                    instrument_id=instrument_id,
                    snapshot_date=snapshot_date,
                    industry=f"Industry {month}",
                    provider="fixture",
                )
                for instrument_id in instrument_ids
                for month, snapshot_date in enumerate(snapshot_dates)
            ]
        ),
        revision=1,
    )
    engine = session_factory.kw["bind"]
    statements: list[str] = []
    loaded = {"fundamentals": 0, "industries": 0}

    def count_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    def count_fundamental_load(_target, _context):
        loaded["fundamentals"] += 1

    def count_industry_load(_target, _context):
        loaded["industries"] += 1

    event.listen(engine, "before_cursor_execute", count_statement)
    event.listen(FundamentalSnapshotRow, "load", count_fundamental_load)
    event.listen(HistoricalIndustrySnapshotRow, "load", count_industry_load)
    try:
        fundamental_result = fundamentals.fundamentals_as_of(
            instrument_ids, date(2024, 12, 31), revision=1
        )
        fundamental_query_count = len(statements)
        statements.clear()
        industry_result = industries.industries_as_of(
            instrument_ids, date(2024, 12, 31), revision=1
        )
        industry_query_count = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)
        event.remove(FundamentalSnapshotRow, "load", count_fundamental_load)
        event.remove(HistoricalIndustrySnapshotRow, "load", count_industry_load)

    assert len(fundamental_result) == len(industry_result) == 100
    assert fundamental_query_count == industry_query_count == 1
    assert loaded == {"fundamentals": 100, "industries": 100}


def test_large_bar_insert_chunks_stay_below_sqlite_bind_limit(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo("bulk-bars")
    bars = [
        _bar().model_copy(
            update={
                "provider_mode": "bulk-bars",
                "trade_date": date(2020, 1, 1) + timedelta(days=offset),
            }
        )
        for offset in range(1000)
    ]
    engine = session_factory.kw["bind"]
    insert_statements: list[str] = []

    def capture_inserts(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().startswith("INSERT INTO historical_replay_bars"):
            insert_statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_inserts)
    try:
        assert repo.upsert_replay_bars(bars, revision=1) == 1000
    finally:
        event.remove(engine, "before_cursor_execute", capture_inserts)

    assert len(insert_statements) > 1
    assert max(statement.count("?") for statement in insert_statements) <= 900
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(HistoricalReplayBarRow).where(
                HistoricalReplayBarRow.provider_mode == "bulk-bars"
            )
        ) == 1000


def test_industry_and_membership_reads_use_latest_ready_as_of_snapshot(storage):
    _, _, _, make_repo = storage
    repo = make_repo()
    repo.upsert_point_in_time_evidence(
        HistoricalEvidenceBundle(
            industries=[
                HistoricalIndustrySnapshot(
                    instrument_id="CN:000001",
                    snapshot_date=date(2025, 3, 31),
                    industry="Banking",
                    provider="fixture",
                ),
                HistoricalIndustrySnapshot(
                    instrument_id="CN:000001",
                    snapshot_date=date(2025, 9, 30),
                    industry="Future Banking",
                    provider="fixture",
                ),
            ],
            index_snapshots=[
                HistoricalIndexSnapshot(
                    index_id="CN:000300.IDX",
                    snapshot_date=date(2025, 3, 31),
                    status="ready",
                    member_count=1,
                    provider="fixture",
                ),
                HistoricalIndexSnapshot(
                    index_id="CN:000300.IDX",
                    snapshot_date=date(2025, 5, 31),
                    status="failed",
                    member_count=0,
                    provider="fixture",
                ),
                HistoricalIndexSnapshot(
                    index_id="CN:000300.IDX",
                    snapshot_date=date(2025, 9, 30),
                    status="ready",
                    member_count=1,
                    provider="fixture",
                ),
            ],
            index_memberships=[
                HistoricalIndexMembership(
                    index_id="CN:000300.IDX",
                    snapshot_date=date(2025, 3, 31),
                    instrument_id="CN:000001",
                    provider="fixture",
                ),
                HistoricalIndexMembership(
                    index_id="CN:000300.IDX",
                    snapshot_date=date(2025, 9, 30),
                    instrument_id="CN:000001",
                    provider="fixture",
                ),
            ],
        ),
        revision=1,
    )

    industries = repo.industries_as_of(
        ["CN:000001"], date(2025, 6, 30), revision=1
    )
    memberships = repo.memberships_as_of(
        ["CN:000001"], date(2025, 6, 30), revision=1
    )

    assert industries["CN:000001"].snapshot_date == date(2025, 3, 31)
    assert industries["CN:000001"].industry == "Banking"
    assert [item.index_id for item in memberships["CN:000001"]] == [
        "CN:000300.IDX"
    ]
    assert memberships["CN:000001"][0].snapshot_date == date(2025, 3, 31)


def test_ready_index_snapshot_rejects_incomplete_membership_write(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo()
    bundle = HistoricalEvidenceBundle(
        index_snapshots=[
            HistoricalIndexSnapshot(
                index_id="CN:000300.IDX",
                snapshot_date=date(2025, 3, 31),
                status="ready",
                member_count=2,
                provider="fixture",
            )
        ],
        index_memberships=[
            HistoricalIndexMembership(
                index_id="CN:000300.IDX",
                snapshot_date=date(2025, 3, 31),
                instrument_id="CN:000001",
                provider="fixture",
            )
        ],
    )

    with pytest.raises(ReplayEvidenceUnavailable, match="member_count=2.*stored=1"):
        repo.upsert_point_in_time_evidence(bundle, revision=1)

    assert repo.current_revision() == 0
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(HistoricalIndexSnapshotRow)) == 0
        assert session.scalar(
            select(func.count()).select_from(HistoricalIndexMembershipRow)
        ) == 0


def test_membership_read_rejects_incomplete_ready_snapshot(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo()
    snapshot_date = date(2025, 3, 31)
    repo.upsert_point_in_time_evidence(
        HistoricalEvidenceBundle(
            index_snapshots=[
                HistoricalIndexSnapshot(
                    index_id="CN:000300.IDX",
                    snapshot_date=snapshot_date,
                    status="ready",
                    member_count=2,
                    provider="fixture",
                )
            ],
            index_memberships=[
                HistoricalIndexMembership(
                    index_id="CN:000300.IDX",
                    snapshot_date=snapshot_date,
                    instrument_id=instrument_id,
                    provider="fixture",
                )
                for instrument_id in ("CN:000001", "CN:000002")
            ],
        ),
        revision=1,
    )
    with session_factory() as session:
        session.execute(
            delete(HistoricalIndexMembershipRow).where(
                HistoricalIndexMembershipRow.instrument_id == "CN:000002"
            )
        )
        session.commit()

    with pytest.raises(ReplayEvidenceUnavailable, match="member_count=2.*stored=1"):
        repo.memberships_as_of(["CN:000001"], snapshot_date, revision=1)


def test_memberships_as_of_query_count_is_bounded_for_100_indexes(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo()
    snapshot_date = date(2025, 3, 31)
    repo.upsert_point_in_time_evidence(
        HistoricalEvidenceBundle(
            index_snapshots=[
                HistoricalIndexSnapshot(
                    index_id=f"CN:{index:06d}.IDX",
                    snapshot_date=snapshot_date,
                    status="ready",
                    member_count=1,
                    provider="fixture",
                )
                for index in range(100)
            ],
            index_memberships=[
                HistoricalIndexMembership(
                    index_id=f"CN:{index:06d}.IDX",
                    snapshot_date=snapshot_date,
                    instrument_id="CN:000001",
                    provider="fixture",
                )
                for index in range(100)
            ],
        ),
        revision=1,
    )
    engine = session_factory.kw["bind"]
    statements: list[str] = []

    def count_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        result = repo.memberships_as_of(["CN:000001"], snapshot_date, revision=1)
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert len(result["CN:000001"]) == 100
    assert len(statements) <= 3


def test_membership_bulk_queries_stay_below_sqlite_bind_limit(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo("membership-bind-limit")
    snapshot_date = date(2025, 3, 31)
    repo.upsert_point_in_time_evidence(
        HistoricalEvidenceBundle(
            index_snapshots=[
                HistoricalIndexSnapshot(
                    index_id=f"CN:{index:06d}.IDX",
                    snapshot_date=snapshot_date,
                    status="ready",
                    member_count=1,
                    provider="fixture",
                )
                for index in range(300)
            ],
            index_memberships=[
                HistoricalIndexMembership(
                    index_id=f"CN:{index:06d}.IDX",
                    snapshot_date=snapshot_date,
                    instrument_id="CN:000001",
                    provider="fixture",
                )
                for index in range(300)
            ],
        ),
        revision=1,
    )
    engine = session_factory.kw["bind"]
    statements: list[str] = []

    def capture_statements(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statements)
    try:
        result = repo.memberships_as_of(["CN:000001"], snapshot_date, revision=1)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statements)

    assert len(result["CN:000001"]) == 300
    assert max(statement.count("?") for statement in statements) <= 900


def test_legacy_repository_ignores_superseded_replay_revisions(storage):
    session_factory, _, _, make_repo = storage
    replay = make_repo("legacy-stats")

    def evidence(industry: str, member_id: str) -> HistoricalEvidenceBundle:
        return HistoricalEvidenceBundle(
            tradability=[
                HistoricalTradabilityPoint(
                    instrument_id="CN:000001",
                    trade_date=date(2025, 3, 31),
                    trading_status="trading",
                    provider="fixture",
                )
            ],
            industries=[
                HistoricalIndustrySnapshot(
                    instrument_id="CN:000001",
                    snapshot_date=date(2025, 3, 31),
                    industry=industry,
                    provider="fixture",
                )
            ],
            index_snapshots=[
                HistoricalIndexSnapshot(
                    index_id="CN:000300.IDX",
                    snapshot_date=date(2025, 3, 31),
                    status="ready",
                    member_count=1,
                    provider="fixture",
                )
            ],
            index_memberships=[
                HistoricalIndexMembership(
                    index_id="CN:000300.IDX",
                    snapshot_date=date(2025, 3, 31),
                    instrument_id=member_id,
                    provider="fixture",
                )
            ],
        )

    replay.upsert_point_in_time_evidence(
        evidence("Banking", "CN:000001"), revision=1
    )
    replay.upsert_point_in_time_evidence(
        evidence("Fintech", "CN:000002"), revision=2
    )
    fundamentals = make_repo("legacy-fundamentals")
    first = FundamentalSnapshot(
        instrument_id="CN:000001",
        as_of_date=date(2025, 3, 31),
        pe_ratio=Decimal("8.5"),
        provider="fixture",
    )
    fundamentals.upsert_fundamentals([first], revision=1)
    fundamentals.upsert_fundamentals(
        [first.model_copy(update={"pe_ratio": Decimal("9.5")})], revision=2
    )
    legacy = QagentRepository(session_factory)

    evidence_stats = legacy.historical_evidence_stats(
        "legacy-stats", ["CN:000001"], date(2025, 1, 1), date(2025, 12, 31)
    )["CN:000001"]
    index_stats = legacy.historical_index_snapshot_stats(
        "legacy-stats", date(2025, 1, 1), date(2025, 12, 31)
    )
    listed = legacy.list_fundamental_snapshots(
        "legacy-fundamentals", ["CN:000001"]
    )
    fundamental_stats = legacy.fundamental_snapshot_stats(
        "legacy-fundamentals", ["CN:000001"], date(2025, 12, 31)
    )

    assert evidence_stats.tradability_rows == 1
    assert evidence_stats.industry_rows == 1
    assert evidence_stats.industries == ["Fintech"]
    assert evidence_stats.benchmark_membership_rows == 0
    assert evidence_stats.benchmark_ids == []
    assert index_stats.total_snapshots == index_stats.ready_snapshots == 1
    assert len(listed) == 1
    assert listed[0].pe_ratio == Decimal("9.500000")
    assert fundamental_stats["CN:000001"] == (1, date(2025, 3, 31), date(2025, 3, 31))


def test_tradability_requires_an_exact_date_row(storage):
    _, _, _, make_repo = storage
    repo = make_repo()
    repo.upsert_point_in_time_evidence(
        HistoricalEvidenceBundle(
            tradability=[
                HistoricalTradabilityPoint(
                    instrument_id="CN:000001",
                    trade_date=date(2025, 6, 27),
                    trading_status="trading",
                    provider="fixture",
                ),
                HistoricalTradabilityPoint(
                    instrument_id="CN:000002",
                    trade_date=date(2025, 6, 30),
                    trading_status="suspended",
                    provider="fixture",
                ),
            ]
        ),
        revision=1,
    )

    result = repo.tradability_on(
        ["CN:000001", "CN:000002"], date(2025, 6, 30), revision=1
    )

    assert set(result) == {"CN:000002"}
    assert result["CN:000002"].trading_status == "suspended"


def test_exact_date_members_are_provider_and_revision_scoped(storage):
    _, _, _, make_repo = storage
    free = make_repo("free")
    free.upsert_lifecycle_inventory(
        [_profile("CN:000001")], _lifecycle_manifest("free", 1, 1)
    )
    free_owner = make_repo("free", "run-free-1")
    free_owner.acquire_dataset_lease()
    first = free_owner.materialize_universe(date(2025, 6, 30), revision=1)
    free_owner.release_dataset_lease()

    free.upsert_lifecycle_inventory(
        [_profile("CN:000001"), _profile("CN:000002")],
        _lifecycle_manifest("free", 2, 2),
    )
    free_owner_2 = make_repo("free", "run-free-2")
    free_owner_2.acquire_dataset_lease()
    second = free_owner_2.materialize_universe(date(2025, 6, 30), revision=2)
    free_owner_2.release_dataset_lease()

    premium = make_repo("premium")
    premium.upsert_lifecycle_inventory(
        [_profile("CN:600000")], _lifecycle_manifest("premium", 1, 1)
    )
    premium_owner = make_repo("premium", "run-premium")
    premium_owner.acquire_dataset_lease()
    third = premium_owner.materialize_universe(date(2025, 6, 30), revision=1)

    assert [item.instrument_id for item in first.members] == ["CN:000001"]
    assert [item.instrument_id for item in second.members] == [
        "CN:000001",
        "CN:000002",
    ]
    assert [item.instrument_id for item in third.members] == ["CN:600000"]
    assert [
        item.instrument_id
        for item in free.universe_members_on(date(2025, 6, 30), revision=1)
    ] == ["CN:000001"]


def test_lifecycle_inventory_uses_latest_ready_manifest_at_revision(storage):
    _, _, _, make_repo = storage
    repo = make_repo()
    repo.upsert_lifecycle_inventory(
        [_profile("CN:000001")], _lifecycle_manifest("free", 1, 1)
    )
    repo.upsert_lifecycle_inventory(
        [_profile("CN:000001"), _profile("CN:000002")],
        _lifecycle_manifest("free", 2, 2),
    )

    assert [item.instrument_id for item in repo.lifecycle_inventory(1)] == [
        "CN:000001"
    ]
    assert [item.instrument_id for item in repo.lifecycle_inventory(2)] == [
        "CN:000001",
        "CN:000002",
    ]


def test_recoverable_lifecycle_profiles_revalidates_legacy_baostock_rows(storage):
    session_factory, _, _, make_repo = storage
    with session_factory() as session:
        session.add_all(
            [
                HistoricalInstrumentProfileRow(
                    provider_mode="free",
                    instrument_id="CN:000001",
                    snapshot_date=date(2026, 7, 1),
                    listing_date=date(1991, 4, 3),
                    delisting_date=date(2026, 6, 30),
                    security_type="1",
                    listing_status="0",
                    source_provider="baostock",
                    dataset_revision=0,
                    fetched_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                ),
                HistoricalInstrumentProfileRow(
                    provider_mode="free",
                    instrument_id="CN:001999",
                    snapshot_date=date(2026, 7, 1),
                    listing_date=date(2026, 1, 2),
                    security_type="1",
                    listing_status="1",
                    source_provider="baostock",
                    dataset_revision=0,
                    fetched_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    profiles = make_repo().recoverable_lifecycle_profiles(date(2025, 12, 31))

    assert len(profiles) == 1
    assert profiles[0].instrument_id == "CN:000001"
    assert profiles[0].listing_status == "active"
    assert profiles[0].delisting_date is None
    assert profiles[0].provider == "baostock_cached_lifecycle_recovery"


def test_legacy_lifecycle_profiles_migrate_to_revision_scoped_identity(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-lifecycle.db'}"
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE historical_instrument_profiles (
                    provider_mode VARCHAR(32) NOT NULL,
                    instrument_id VARCHAR(32) NOT NULL,
                    snapshot_date DATE NOT NULL,
                    listing_date DATE,
                    delisting_date DATE,
                    security_type VARCHAR(32),
                    listing_status VARCHAR(32),
                    source_provider VARCHAR(64) NOT NULL,
                    fetched_at DATETIME NOT NULL,
                    PRIMARY KEY (provider_mode, instrument_id, snapshot_date)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO historical_instrument_profiles (
                    provider_mode, instrument_id, snapshot_date, listing_date,
                    security_type, listing_status, source_provider, fetched_at
                ) VALUES (
                    'free', 'CN:000001', '2025-12-31', '2020-01-01',
                    '1', '1', 'legacy', '2026-01-01 00:00:00'
                )
                """
            )
        )

    migrated = initialize_database(database_url)

    assert inspect(migrated).get_pk_constraint("historical_instrument_profiles")[
        "constrained_columns"
    ] == ["provider_mode", "instrument_id", "snapshot_date", "dataset_revision"]
    with migrated.connect() as connection:
        assert connection.execute(
            text(
                "SELECT instrument_id, dataset_revision "
                "FROM historical_instrument_profiles"
            )
        ).one() == ("CN:000001", 0)


def test_action_coverage_distinguishes_ready_none_from_unsupported(storage):
    _, _, _, make_repo = storage
    repo = make_repo()
    repo.upsert_action_coverage(
        [
            ActionCoverageRecord(
                instrument_id="CN:000001",
                start_date=date(2025, 6, 30),
                end_date=date(2025, 9, 30),
                status="ready_none",
                action_count=0,
                source_provider="fixture",
            ),
            ActionCoverageRecord(
                instrument_id="CN:000002",
                start_date=date(2025, 6, 30),
                end_date=date(2025, 9, 30),
                status="unsupported",
                action_count=1,
                source_provider="fixture",
            ),
        ],
        revision=1,
    )

    coverage = repo.action_coverage(
        ["CN:000001", "CN:000002"],
        date(2025, 6, 30),
        date(2025, 9, 30),
        revision=1,
    )

    assert coverage["CN:000001"].status == "ready_none"
    assert coverage["CN:000002"].status == "unsupported"


def test_revisions_are_monotonic(storage):
    _, _, _, make_repo = storage
    repo = make_repo()

    repo.upsert_replay_bars([_bar()], revision=1)
    with pytest.raises(ValueError, match="monotonic"):
        repo.upsert_corporate_actions([_action(revision=3)], revision=3)

    assert repo.current_revision() == 1


def test_provider_identity_is_normalized_before_persistence_and_reads(storage):
    session_factory, _, _, make_repo = storage
    padded = make_repo(" FREE ")
    padded.upsert_replay_bars(
        [
            _bar().model_copy(
                update={
                    "provider_mode": " FREE ",
                    "source_provider": " FIXTURE ",
                }
            )
        ],
        revision=1,
    )
    padded.upsert_lifecycle_inventory(
        [_profile("CN:000001")], _lifecycle_manifest(" FREE ", 2, 1)
    )

    normalized = make_repo("free")

    assert normalized.replay_bars(
        ["CN:000001"], date(2025, 1, 2), date(2025, 1, 2), revision=2
    )
    assert [item.instrument_id for item in normalized.lifecycle_inventory(2)] == [
        "CN:000001"
    ]
    with session_factory() as session:
        identities = {
            session.scalar(select(HistoricalDataRevisionRow.provider_mode)),
            session.scalar(select(HistoricalReplayBarRow.provider_mode)),
            session.scalar(select(HistoricalLifecycleManifestRow.provider_mode)),
        }
        source_provider = session.scalar(
            select(HistoricalReplayBarRow.source_provider)
        )
    assert identities == {"free"}
    assert source_provider == "fixture"


def test_source_writes_are_forbidden_under_an_active_lease(storage):
    _, _, _, make_repo = storage
    owner = make_repo(owner_run_id="run-a")
    owner.acquire_dataset_lease()

    with pytest.raises(SourceWriteBlocked):
        make_repo().upsert_replay_bars([_bar()], revision=1)


def test_legacy_repository_source_writes_cannot_bypass_active_lease(storage):
    session_factory, _, _, make_repo = storage
    make_repo(owner_run_id="run-a").acquire_dataset_lease()
    legacy_repo = QagentRepository(session_factory)

    with pytest.raises(SourceWriteBlocked):
        legacy_repo.upsert_fundamental_snapshots(
            "free",
            [
                FundamentalSnapshot(
                    instrument_id="CN:000001",
                    as_of_date=date(2025, 3, 31),
                    provider="fixture",
                )
            ],
        )


def test_lease_renews_before_expiry(storage):
    _, clock, _, make_repo = storage
    repo = make_repo(owner_run_id="run-a")
    lease = repo.acquire_dataset_lease()
    clock.advance(timedelta(minutes=4))

    renewed = repo.renew_dataset_lease()

    assert renewed.revision == lease.revision
    assert renewed.heartbeat_at == clock.now
    assert renewed.lease_expires_at > lease.lease_expires_at


def test_lease_maintenance_atomically_recovers_expired_owner(storage):
    _, clock, statuses, make_repo = storage
    statuses["run-a"] = "running"
    repo = make_repo(owner_run_id="run-a")
    lease = repo.acquire_dataset_lease()
    clock.advance(timedelta(minutes=6))

    maintained = repo.maintain_dataset_lease(expected_revision=lease.revision)

    assert maintained.action == "expired_reacquired"
    assert maintained.lease.owner_run_id == "run-a"
    assert maintained.lease.revision == lease.revision
    assert maintained.lease.heartbeat_at == clock.now
    assert maintained.lease.lease_expires_at > clock.now


def test_lease_maintenance_rejects_changed_dataset_revision(storage):
    session_factory, _, statuses, make_repo = storage
    statuses["run-a"] = "running"
    repo = make_repo(owner_run_id="run-a")
    lease = repo.acquire_dataset_lease()
    with session_factory() as session:
        session.execute(
            update(HistoricalDataRevisionRow)
            .where(HistoricalDataRevisionRow.provider_mode == "free")
            .values(revision=lease.revision + 1)
        )
        session.commit()

    with pytest.raises(StaleCheckpointRevision, match="expected revision"):
        repo.maintain_dataset_lease(expected_revision=lease.revision)


def test_expired_owner_must_reenter_before_checkpoint_write(storage):
    _, clock, _, make_repo = storage
    repo = make_repo(owner_run_id="run-a")
    lease = repo.acquire_dataset_lease()
    clock.advance(timedelta(minutes=6))

    with pytest.raises(DatasetLeaseBusy, match="expired"):
        with repo.checkpoint_transaction(lease.revision):
            pytest.fail("expired lease must not authorize a checkpoint")

    repo.acquire_dataset_lease()
    repo.verify_checkpoint_revision(lease.revision)


def test_competing_owner_cannot_acquire_live_lease(storage):
    _, _, _, make_repo = storage
    make_repo(owner_run_id="run-a").acquire_dataset_lease()

    with pytest.raises(DatasetLeaseBusy):
        make_repo(owner_run_id="run-b").acquire_dataset_lease()


def test_concurrent_lease_acquire_has_one_success_and_one_busy(storage):
    session_factory, _, statuses, make_repo = storage
    statuses.update({"run-a": "running", "run-b": "running"})
    start = Barrier(2)

    def acquire(owner_run_id: str) -> tuple[str, str]:
        repo = make_repo(owner_run_id=owner_run_id)
        start.wait(timeout=5)
        try:
            lease = repo.acquire_dataset_lease()
        except DatasetLeaseBusy:
            return "busy", owner_run_id
        return "success", lease.owner_run_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(acquire, ("run-a", "run-b")))

    assert sorted(status for status, _ in outcomes) == ["busy", "success"]
    successful_owner = next(owner for status, owner in outcomes if status == "success")
    with session_factory() as session:
        leases = list(session.scalars(select(HistoricalDatasetLeaseRow)))
    assert len(leases) == 1
    assert leases[0].owner_run_id == successful_owner


def test_original_run_reenters_stale_lease(storage):
    _, clock, statuses, make_repo = storage
    statuses["run-a"] = "running"
    repo = make_repo(owner_run_id="run-a")
    lease = repo.acquire_dataset_lease()
    clock.advance(timedelta(minutes=11))

    recovered = repo.acquire_dataset_lease()

    assert recovered.owner_run_id == "run-a"
    assert recovered.revision == lease.revision
    assert recovered.heartbeat_at == clock.now


def test_different_run_cannot_take_stale_nonterminal_lease(storage):
    _, clock, statuses, make_repo = storage
    statuses["run-a"] = "running"
    make_repo(owner_run_id="run-a").acquire_dataset_lease()
    clock.advance(timedelta(minutes=11))

    with pytest.raises(DatasetLeaseBusy):
        make_repo(owner_run_id="run-b").acquire_dataset_lease()


def test_terminal_orphan_lease_is_released(storage):
    _, clock, statuses, make_repo = storage
    statuses["run-a"] = "running"
    make_repo(owner_run_id="run-a").acquire_dataset_lease()
    statuses["run-a"] = "succeeded"

    lease = make_repo(owner_run_id="run-b").acquire_dataset_lease()

    assert lease.owner_run_id == "run-b"


def test_owner_scope_preserves_terminal_run_lookup(storage):
    _, clock, statuses, make_repo = storage
    repository = make_repo()
    statuses["run-a"] = "running"
    repository.for_owner("run-a").acquire_dataset_lease()
    statuses["run-a"] = "failed"
    clock.advance(timedelta(minutes=11))

    lease = repository.for_owner("run-b").acquire_dataset_lease()

    assert lease.owner_run_id == "run-b"


@pytest.mark.parametrize(
    "terminal_status", ["succeeded", "blocked_data", "failed", "cancelled"]
)
def test_terminal_run_cannot_acquire_without_existing_lease(storage, terminal_status):
    session_factory, _, statuses, make_repo = storage
    statuses["run-a"] = terminal_status

    with pytest.raises(DatasetLeaseBusy, match="terminal"):
        make_repo(owner_run_id="run-a").acquire_dataset_lease()

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(HistoricalDatasetLeaseRow)
        ) == 0


def test_terminal_status_check_runs_inside_immediate_transaction(storage):
    session_factory, clock, _, _ = storage
    engine = session_factory.kw["bind"]
    immediate_started = False
    lookup_observations: list[bool] = []

    def observe_begin(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal immediate_started
        if statement.strip() == "BEGIN IMMEDIATE":
            immediate_started = True

    def terminal_status(_run_id):
        lookup_observations.append(immediate_started)
        return "failed"

    repo = ReplayEvidenceRepository(
        session_factory,
        provider_mode="free",
        owner_run_id="run-a",
        clock=clock,
        run_status_lookup=terminal_status,
    )
    event.listen(engine, "before_cursor_execute", observe_begin)
    try:
        with pytest.raises(DatasetLeaseBusy, match="terminal"):
            repo.acquire_dataset_lease()
    finally:
        event.remove(engine, "before_cursor_execute", observe_begin)

    assert lookup_observations == [True]
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(HistoricalDatasetLeaseRow)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(HistoricalDataRevisionRow)
        ) == 0


def test_terminal_owner_cannot_reenter_or_renew_its_stale_lease(storage):
    _, clock, statuses, make_repo = storage
    statuses["run-a"] = "running"
    owner = make_repo(owner_run_id="run-a")
    owner.acquire_dataset_lease()
    clock.advance(timedelta(minutes=11))
    statuses["run-a"] = "succeeded"

    with pytest.raises(DatasetLeaseBusy, match="terminal"):
        owner.acquire_dataset_lease()
    with pytest.raises(DatasetLeaseBusy, match="terminal"):
        owner.renew_dataset_lease()

    replacement = make_repo(owner_run_id="run-b").acquire_dataset_lease()
    assert replacement.owner_run_id == "run-b"


def test_revision_change_invalidates_checkpoint(storage):
    session_factory, _, _, make_repo = storage
    repo = make_repo(owner_run_id="run-a")
    lease = repo.acquire_dataset_lease()
    with session_factory() as session:
        session.execute(
            update(HistoricalDataRevisionRow)
            .where(HistoricalDataRevisionRow.provider_mode == "free")
            .values(revision=lease.revision + 1)
        )
        session.commit()

    with pytest.raises(StaleCheckpointRevision):
        with repo.checkpoint_transaction(lease.revision):
            pytest.fail("stale checkpoint transaction must not start")


def test_lease_owner_can_materialize_revision_scoped_universe_without_revision_increment(
    storage,
):
    session_factory, _, _, make_repo = storage
    source = make_repo()
    source.upsert_lifecycle_inventory(
        [_profile("CN:000001")], _lifecycle_manifest("free", 1, 1)
    )
    owner = make_repo(owner_run_id="run-a")
    owner.acquire_dataset_lease()

    materialized = owner.materialize_universe(date(2025, 6, 30), revision=1)

    assert materialized.manifest.source_revision == 1
    assert materialized.manifest.status == "ready"
    assert owner.current_revision() == 1
    with session_factory() as session:
        rows = list(session.scalars(select(HistoricalReplayUniverseMemberRow)))
    assert [(row.provider_mode, row.source_revision) for row in rows] == [("free", 1)]


def test_nonowner_cannot_materialize_universe_under_lease(storage):
    _, _, _, make_repo = storage
    source = make_repo()
    source.upsert_lifecycle_inventory(
        [_profile("CN:000001")], _lifecycle_manifest("free", 1, 1)
    )
    make_repo(owner_run_id="run-a").acquire_dataset_lease()

    with pytest.raises(DatasetLeaseBusy):
        make_repo(owner_run_id="run-b").materialize_universe(
            date(2025, 6, 30), revision=1
        )


def test_derived_universe_owner_persists_after_lease_release(storage):
    session_factory, _, _, make_repo = storage
    source = make_repo()
    source.upsert_lifecycle_inventory(
        [_profile("CN:000001")], _lifecycle_manifest("free", 1, 1)
    )
    run_a = make_repo(owner_run_id="run-a")
    run_a.acquire_dataset_lease()
    run_a.materialize_universe(date(2025, 6, 30), revision=1)
    run_a.release_dataset_lease()
    run_b = make_repo(owner_run_id="run-b")
    run_b.acquire_dataset_lease()

    with pytest.raises(ValueError, match="owned by run-a"):
        run_b.materialize_universe(date(2025, 6, 30), revision=1)

    with session_factory() as session:
        member = session.scalar(select(HistoricalReplayUniverseMemberRow))
    assert member is not None
    assert member.owner_run_id == "run-a"


def test_same_owner_universe_materialization_is_strictly_idempotent(storage):
    session_factory, clock, _, make_repo = storage
    source = make_repo()
    source.upsert_lifecycle_inventory(
        [_profile("CN:000001")], _lifecycle_manifest("free", 1, 1)
    )
    owner = make_repo(owner_run_id="run-a")
    owner.acquire_dataset_lease()
    first = owner.materialize_universe(date(2025, 6, 30), revision=1)
    with session_factory() as session:
        first_fetched_at = session.scalar(
            select(HistoricalReplayUniverseMemberRow.fetched_at)
        )
    clock.advance(timedelta(seconds=30))

    second = owner.materialize_universe(date(2025, 6, 30), revision=1)

    assert second == first
    with session_factory() as session:
        member = session.scalar(select(HistoricalReplayUniverseMemberRow))
    assert member is not None
    assert member.owner_run_id == "run-a"
    assert member.fetched_at == first_fetched_at


def test_materialize_universe_rejects_missing_lifecycle_inventory(storage):
    _, _, _, make_repo = storage
    owner = make_repo(owner_run_id="run-a")
    owner.acquire_dataset_lease()

    with pytest.raises(ReplayEvidenceUnavailable, match="lifecycle"):
        owner.materialize_universe(date(2025, 6, 30), revision=0)


def test_materialize_universe_rejects_unknown_listing_date(storage):
    session_factory, _, _, make_repo = storage
    source = make_repo()
    source.upsert_lifecycle_inventory(
        [_profile("CN:000001", listing_date=None)],
        _lifecycle_manifest("free", 1, 1),
    )
    owner = make_repo(owner_run_id="run-a")
    owner.acquire_dataset_lease()

    with pytest.raises(ReplayEvidenceUnavailable, match="listing_date"):
        owner.materialize_universe(date(2025, 6, 30), revision=1)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(
            HistoricalReplayUniverseMemberRow
        )) == 0


def test_materialize_universe_rejects_unknown_security_type(storage):
    _, _, _, make_repo = storage
    source = make_repo()
    source.upsert_lifecycle_inventory(
        [_profile("CN:000001", security_type=None)],
        _lifecycle_manifest("free", 1, 1),
    )
    owner = make_repo(owner_run_id="run-a")
    owner.acquire_dataset_lease()

    with pytest.raises(ReplayEvidenceUnavailable, match="security_type"):
        owner.materialize_universe(date(2025, 6, 30), revision=1)


@pytest.mark.parametrize("security_type", ["", "   "])
def test_materialize_universe_rejects_blank_security_type(storage, security_type):
    _, _, _, make_repo = storage
    source = make_repo()
    source.upsert_lifecycle_inventory(
        [_profile("CN:000001", security_type=security_type)],
        _lifecycle_manifest("free", 1, 1),
    )
    owner = make_repo(owner_run_id="run-a")
    owner.acquire_dataset_lease()

    with pytest.raises(ReplayEvidenceUnavailable, match="security_type"):
        owner.materialize_universe(date(2025, 6, 30), revision=1)


def test_lifecycle_security_type_is_normalized_before_storage(storage):
    session_factory, _, _, make_repo = storage
    source = make_repo()
    source.upsert_lifecycle_inventory(
        [_profile("CN:510300", security_type="  ETF  ")],
        _lifecycle_manifest("free", 1, 1),
    )

    with session_factory() as session:
        stored = session.scalar(select(HistoricalInstrumentProfileRow))
    assert stored is not None
    assert stored.security_type == "etf"


def test_legacy_derived_universe_rows_receive_explicit_owner(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-derived-universe.db'}"
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE historical_universe_manifests (
                    provider_mode VARCHAR(32) NOT NULL,
                    snapshot_date DATE NOT NULL,
                    source_revision INTEGER NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    expected_count INTEGER,
                    stored_count INTEGER NOT NULL,
                    error TEXT,
                    fetched_at DATETIME NOT NULL,
                    PRIMARY KEY (provider_mode, snapshot_date, source_revision)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE historical_replay_universe_members (
                    provider_mode VARCHAR(32) NOT NULL,
                    snapshot_date DATE NOT NULL,
                    source_revision INTEGER NOT NULL,
                    instrument_id VARCHAR(32) NOT NULL,
                    security_type VARCHAR(32) NOT NULL,
                    listing_date DATE,
                    delisting_date DATE,
                    active BOOLEAN NOT NULL,
                    source_provider VARCHAR(64) NOT NULL,
                    fetched_at DATETIME NOT NULL,
                    PRIMARY KEY (
                        provider_mode, snapshot_date, source_revision, instrument_id
                    )
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO historical_universe_manifests VALUES (
                    'free', '2025-06-30', 1, 'ready', 1, 1, NULL,
                    '2025-06-30 08:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO historical_replay_universe_members VALUES (
                    'free', '2025-06-30', 1, 'CN:000001', '1', '2020-01-01',
                    NULL, 1, 'legacy', '2025-06-30 08:00:00'
                )
                """
            )
        )

    migrated = initialize_database(database_url)

    with migrated.connect() as connection:
        manifest_owner = connection.execute(
            text("SELECT owner_run_id FROM historical_universe_manifests")
        ).scalar_one()
        member_owner = connection.execute(
            text("SELECT owner_run_id FROM historical_replay_universe_members")
        ).scalar_one()
    assert manifest_owner == "legacy-unknown-owner"
    assert member_owner == "legacy-unknown-owner"
