from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateTable

from qagent import db
from qagent.db import Base, create_db_engine, initialize_database
from qagent.storage import tables as _tables  # noqa: F401


REVISION_SCOPED_LEGACY_KEYS = {
    "fundamental_snapshots": [
        "provider_mode",
        "instrument_id",
        "as_of_date",
        "source_provider",
    ],
    "historical_tradability": ["provider_mode", "instrument_id", "trade_date"],
    "historical_instrument_profiles": [
        "provider_mode",
        "instrument_id",
        "snapshot_date",
    ],
    "historical_industry_snapshots": [
        "provider_mode",
        "instrument_id",
        "snapshot_date",
        "source_provider",
    ],
    "historical_index_snapshots": ["provider_mode", "index_id", "snapshot_date"],
    "historical_index_memberships": [
        "provider_mode",
        "index_id",
        "snapshot_date",
        "instrument_id",
    ],
    "historical_replay_bars": ["provider_mode", "instrument_id", "trade_date"],
    "historical_corporate_actions": ["provider_mode", "instrument_id", "action_id"],
    "historical_corporate_action_coverage": [
        "provider_mode",
        "instrument_id",
        "start_date",
        "end_date",
    ],
}


def _replace_primary_key(ddl: str, current: list[str], legacy: list[str]) -> str:
    current_sql = "PRIMARY KEY (" + ", ".join(current) + ")"
    legacy_sql = "PRIMARY KEY (" + ", ".join(legacy) + ")"
    assert current_sql in ddl
    return ddl.replace(current_sql, legacy_sql)


def _downgrade_revision_primary_keys(connection) -> None:
    inspector = inspect(connection)
    for table_name, legacy_key in REVISION_SCOPED_LEGACY_KEYS.items():
        table = Base.metadata.tables[table_name]
        current_key = inspector.get_pk_constraint(table_name)["constrained_columns"]
        ddl = str(CreateTable(table).compile(dialect=connection.dialect))
        legacy_ddl = _replace_primary_key(ddl, current_key, legacy_key)
        backup = f"{table_name}_current_schema"
        connection.execute(text(f"ALTER TABLE {table_name} RENAME TO {backup}"))
        connection.execute(text(legacy_ddl))
        connection.execute(text(f"DROP TABLE {backup}"))


def test_initialize_database_adds_adjustment_columns_to_legacy_market_cache(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-cache.db'}"
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE market_bar_cache (
                    provider_mode VARCHAR(32) NOT NULL,
                    instrument_id VARCHAR(32) NOT NULL,
                    trade_date DATE NOT NULL,
                    source_provider VARCHAR(64) NOT NULL DEFAULT '',
                    open NUMERIC(18, 6) NOT NULL,
                    high NUMERIC(18, 6) NOT NULL,
                    low NUMERIC(18, 6) NOT NULL,
                    close NUMERIC(18, 6) NOT NULL,
                    volume NUMERIC(24, 4) NOT NULL,
                    cached_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (provider_mode, instrument_id, trade_date)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO market_bar_cache (
                    provider_mode, instrument_id, trade_date, source_provider,
                    open, high, low, close, volume, cached_at, updated_at
                ) VALUES (
                    'free', 'CN:000001', '2025-01-02', 'legacy-provider',
                    10.0, 10.5, 9.8, 10.2, 1000, '2025-01-03', '2025-01-03'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tradable_universe_snapshots (
                    as_of_date DATE NOT NULL,
                    instrument_id VARCHAR(32) NOT NULL,
                    symbol VARCHAR(16) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    asset_type VARCHAR(32) NOT NULL,
                    exchange VARCHAR(16) NOT NULL,
                    source VARCHAR(96) NOT NULL DEFAULT '',
                    active BOOLEAN NOT NULL DEFAULT 1,
                    captured_at DATETIME NOT NULL,
                    PRIMARY KEY (as_of_date, instrument_id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO tradable_universe_snapshots (
                    as_of_date, instrument_id, symbol, name, asset_type,
                    exchange, source, active, captured_at
                ) VALUES (
                    '2025-01-02', 'CN:000001', '000001', 'Legacy Bank',
                    'stock', 'SZSE', 'legacy-catalog', 1, '2025-01-03'
                )
                """
            )
        )

    migrated = initialize_database(database_url)
    columns = {column["name"] for column in inspect(migrated).get_columns("market_bar_cache")}

    assert {
        "turnover",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "adjustment_factor",
        "adjustment_type",
    }.issubset(columns)
    assert inspect(migrated).get_pk_constraint("tradable_universe_snapshots")[
        "constrained_columns"
    ] == ["as_of_date", "instrument_id"]
    with migrated.connect() as connection:
        market_row = connection.execute(
            text(
                "SELECT source_provider, close FROM market_bar_cache "
                "WHERE provider_mode = 'free' AND instrument_id = 'CN:000001'"
            )
        ).one()
        universe_row = connection.execute(
            text(
                "SELECT name, source FROM tradable_universe_snapshots "
                "WHERE as_of_date = '2025-01-02' AND instrument_id = 'CN:000001'"
            )
        ).one()

    assert market_row == ("legacy-provider", 10.2)
    assert universe_row == ("Legacy Bank", "legacy-catalog")
    assert "historical_replay_universe_members" in inspect(migrated).get_table_names()
    assert inspect(migrated).get_pk_constraint("historical_trading_rules")[
        "constrained_columns"
    ] == ["rule_set_version", "limit_rule_key", "effective_from"]
    assert inspect(migrated).get_pk_constraint("historical_fee_rules")["constrained_columns"] == [
        "fee_schedule_version",
        "fee_rule_key",
        "effective_from",
        "side",
    ]


def test_initialize_database_adds_paper_probe_allocation_column(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-paper-probe.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE paper_trades DROP COLUMN allocation_multiplier"))

    migrated = initialize_database(database_url)
    columns = {column["name"] for column in inspect(migrated).get_columns("paper_trades")}

    assert "allocation_multiplier" in columns


def test_initialize_database_backfills_legacy_paper_event_instrument_id(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-paper-events.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_paper_trade_events_instrument_id"))
        connection.execute(text("ALTER TABLE paper_trade_events DROP COLUMN instrument_id"))
        connection.execute(
            text(
                """
                INSERT INTO paper_trades (
                    trade_id, source_snapshot_id, provider, instrument_id, status,
                    signal_date, trigger_price, allocation_multiplier, holding_days,
                    notes, created_at, updated_at
                ) VALUES (
                    'paper-legacy', 'snapshot-legacy', 'free', 'CN:000001', 'pending',
                    '2025-01-02', 10.0, 1.0, 0, '', '2025-01-02', '2025-01-02'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO paper_trade_events (
                    event_id, trade_id, sequence, idempotency_key, event_type,
                    from_status, to_status, occurred_at, trade_date, price,
                    reason_code, note, source, created_at
                ) VALUES (
                    'event-legacy', 'paper-legacy', 1, 'legacy-key', 'created',
                    NULL, 'pending', '2025-01-02', '2025-01-02', 10.0,
                    'paper_trade.created.pending', '', 'legacy', '2025-01-02'
                )
                """
            )
        )

    migrated = initialize_database(database_url)
    inspector = inspect(migrated)

    assert "instrument_id" in {
        column["name"] for column in inspector.get_columns("paper_trade_events")
    }
    assert "ix_paper_trade_events_instrument_id" in {
        index["name"] for index in inspector.get_indexes("paper_trade_events")
    }
    with migrated.connect() as connection:
        instrument_id = connection.execute(
            text("SELECT instrument_id FROM paper_trade_events WHERE event_id = 'event-legacy'")
        ).scalar_one()
    assert instrument_id == "CN:000001"


def test_initialize_database_rebuilds_revision_scoped_tables_without_data_loss(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-replay-revisions.db'}"
    engine = create_db_engine(database_url)
    now = datetime(2025, 1, 3, tzinfo=timezone.utc)
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        _downgrade_revision_primary_keys(connection)
        rows = {
            "fundamental_snapshots": {
                "provider_mode": "free",
                "instrument_id": "CN:000001",
                "as_of_date": date(2025, 1, 2),
                "source_provider": "legacy",
                "dataset_revision": 7,
                "cached_at": now,
                "updated_at": now,
            },
            "historical_tradability": {
                "provider_mode": "free",
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 2),
                "trading_status": "trading",
                "source_provider": "legacy",
                "dataset_revision": 7,
                "fetched_at": now,
            },
            "historical_industry_snapshots": {
                "provider_mode": "free",
                "instrument_id": "CN:000001",
                "snapshot_date": date(2025, 1, 2),
                "source_provider": "legacy",
                "industry": "Banking",
                "dataset_revision": 7,
                "fetched_at": now,
            },
            "historical_index_snapshots": {
                "provider_mode": "free",
                "index_id": "CN:000300.IDX",
                "snapshot_date": date(2025, 1, 2),
                "status": "ready",
                "member_count": 1,
                "source_provider": "legacy",
                "dataset_revision": 7,
                "fetched_at": now,
            },
            "historical_index_memberships": {
                "provider_mode": "free",
                "index_id": "CN:000300.IDX",
                "snapshot_date": date(2025, 1, 2),
                "instrument_id": "CN:000001",
                "source_provider": "legacy",
                "dataset_revision": 7,
                "fetched_at": now,
            },
            "historical_replay_bars": {
                "provider_mode": "free",
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 2),
                "raw_open": Decimal("10"),
                "raw_high": Decimal("10"),
                "raw_low": Decimal("10"),
                "raw_close": Decimal("10"),
                "volume": Decimal("1000"),
                "adjustment_mode": "none",
                "source_provider": "legacy",
                "dataset_revision": 7,
                "fetched_at": now,
            },
            "historical_corporate_actions": {
                "provider_mode": "free",
                "instrument_id": "CN:000001",
                "action_id": "cash-2025",
                "announcement_date": date(2024, 12, 20),
                "record_date": date(2025, 1, 2),
                "ex_date": date(2025, 1, 3),
                "effective_date": date(2025, 1, 3),
                "payable_date": date(2025, 1, 10),
                "action_type": "cash_dividend",
                "cash_per_share": Decimal("0.25"),
                "source_provider": "legacy",
                "dataset_revision": 7,
                "fetched_at": now,
            },
            "historical_corporate_action_coverage": {
                "provider_mode": "free",
                "instrument_id": "CN:000001",
                "start_date": date(2025, 1, 1),
                "end_date": date(2025, 12, 31),
                "status": "ready",
                "action_count": 1,
                "source_provider": "legacy",
                "dataset_revision": 7,
                "fetched_at": now,
            },
        }
        for table_name, row in rows.items():
            connection.execute(Base.metadata.tables[table_name].insert().values(row))

    migrated = initialize_database(database_url)
    inspector = inspect(migrated)

    for table_name, row in rows.items():
        expected_key = list(REVISION_SCOPED_LEGACY_KEYS[table_name]) + [
            column
            for column in ("source_provider", "dataset_revision")
            if column not in REVISION_SCOPED_LEGACY_KEYS[table_name]
        ]
        assert inspector.get_pk_constraint(table_name)["constrained_columns"] == expected_key
        assert f"ix_{table_name}_dataset_revision" in {
            index["name"] for index in inspector.get_indexes(table_name)
        }
        with migrated.connect() as connection:
            stored = connection.execute(
                text(
                    f"SELECT source_provider, dataset_revision FROM {table_name} "
                    "WHERE provider_mode = 'free'"
                )
            ).one()
        assert stored == (row["source_provider"], row["dataset_revision"])

    for table_name in (
        "historical_universe_manifests",
        "historical_replay_universe_members",
    ):
        assert f"ix_{table_name}_owner_run_id" in {
            index["name"] for index in inspector.get_indexes(table_name)
        }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("historical_corporate_action_coverage")
    } == {
        "ck_historical_corporate_action_coverage_count",
        "ck_historical_corporate_action_coverage_status",
    }


def test_legacy_row_without_revision_inherits_current_provider_revision(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-revision-backfill.db'}"
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        _downgrade_revision_primary_keys(connection)
        connection.execute(text("ALTER TABLE historical_tradability DROP COLUMN dataset_revision"))
        connection.execute(
            text(
                "INSERT INTO historical_data_revisions "
                "(provider_mode, revision, updated_at) "
                "VALUES ('free', 9, '2025-01-03 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO historical_tradability "
                "(provider_mode, instrument_id, trade_date, trading_status, "
                "source_provider, fetched_at) VALUES "
                "('free', 'CN:000001', '2025-01-02', 'trading', "
                "'legacy', '2025-01-03 00:00:00')"
            )
        )

    migrated = initialize_database(database_url)

    with migrated.connect() as connection:
        revision = connection.execute(
            text("SELECT dataset_revision FROM historical_tradability")
        ).scalar_one()
    assert revision == 9
    assert inspect(migrated).get_pk_constraint("historical_tradability")["constrained_columns"] == [
        "provider_mode",
        "instrument_id",
        "trade_date",
        "source_provider",
        "dataset_revision",
    ]


def test_revision_zero_rows_survive_repeated_initialize_after_revision_one_append(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'restart-safe-replay-migration.db'}"
    engine = create_db_engine(database_url)
    now = datetime(2025, 1, 3, tzinfo=timezone.utc)
    legacy_rows = {
        "fundamental_snapshots": {
            "provider_mode": "free",
            "instrument_id": "CN:000001",
            "as_of_date": date(2025, 1, 2),
            "source_provider": "legacy",
            "dataset_revision": 0,
            "cached_at": now,
            "updated_at": now,
        },
        "historical_tradability": {
            "provider_mode": "free",
            "instrument_id": "CN:000001",
            "trade_date": date(2025, 1, 2),
            "trading_status": "trading",
            "source_provider": "legacy",
            "dataset_revision": 0,
            "fetched_at": now,
        },
        "historical_instrument_profiles": {
            "provider_mode": "free",
            "instrument_id": "CN:000001",
            "snapshot_date": date(2025, 1, 2),
            "listing_date": date(1991, 4, 3),
            "security_type": "stock",
            "source_provider": "legacy",
            "dataset_revision": 0,
            "fetched_at": now,
        },
        "historical_industry_snapshots": {
            "provider_mode": "free",
            "instrument_id": "CN:000001",
            "snapshot_date": date(2025, 1, 2),
            "source_provider": "legacy",
            "industry": "Banking",
            "dataset_revision": 0,
            "fetched_at": now,
        },
        "historical_index_snapshots": {
            "provider_mode": "free",
            "index_id": "CN:000300.IDX",
            "snapshot_date": date(2025, 1, 2),
            "status": "ready",
            "member_count": 1,
            "source_provider": "legacy",
            "dataset_revision": 0,
            "fetched_at": now,
        },
        "historical_index_memberships": {
            "provider_mode": "free",
            "index_id": "CN:000300.IDX",
            "snapshot_date": date(2025, 1, 2),
            "instrument_id": "CN:000001",
            "source_provider": "legacy",
            "dataset_revision": 0,
            "fetched_at": now,
        },
        "historical_replay_bars": {
            "provider_mode": "free",
            "instrument_id": "CN:000001",
            "trade_date": date(2025, 1, 2),
            "raw_open": Decimal("10"),
            "raw_high": Decimal("10"),
            "raw_low": Decimal("10"),
            "raw_close": Decimal("10"),
            "volume": Decimal("1000"),
            "adjustment_mode": "none",
            "source_provider": "legacy",
            "dataset_revision": 0,
            "fetched_at": now,
        },
        "historical_corporate_actions": {
            "provider_mode": "free",
            "instrument_id": "CN:000001",
            "action_id": "cash-2025",
            "announcement_date": date(2024, 12, 20),
            "record_date": date(2025, 1, 2),
            "ex_date": date(2025, 1, 3),
            "effective_date": date(2025, 1, 3),
            "payable_date": date(2025, 1, 10),
            "action_type": "cash_dividend",
            "cash_per_share": Decimal("0.25"),
            "source_provider": "legacy",
            "dataset_revision": 0,
            "fetched_at": now,
        },
        "historical_corporate_action_coverage": {
            "provider_mode": "free",
            "instrument_id": "CN:000001",
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 12, 31),
            "status": "ready",
            "action_count": 1,
            "source_provider": "legacy",
            "dataset_revision": 0,
            "fetched_at": now,
        },
    }
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        _downgrade_revision_primary_keys(connection)
        for table_name, row in legacy_rows.items():
            connection.execute(Base.metadata.tables[table_name].insert().values(row))

    first = initialize_database(database_url)
    with first.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO historical_data_revisions "
                "(provider_mode, revision, updated_at) "
                "VALUES ('free', 1, '2025-01-04 00:00:00')"
            )
        )
        for table_name, legacy_row in legacy_rows.items():
            revision_one = {**legacy_row, "dataset_revision": 1}
            connection.execute(Base.metadata.tables[table_name].insert().values(revision_one))

    for _ in range(2):
        db._initialized_urls.discard(database_url)
        initialize_database(database_url)

    with create_db_engine(database_url).connect() as connection:
        for table_name in legacy_rows:
            revisions = list(
                connection.execute(
                    text(
                        f"SELECT dataset_revision FROM {table_name} "
                        "WHERE provider_mode = 'free' ORDER BY dataset_revision"
                    )
                ).scalars()
            )
            assert revisions == [0, 1], table_name
