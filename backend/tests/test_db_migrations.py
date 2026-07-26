from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateTable

from qagent import db
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardIdentity,
    RankingV3ForwardSessionInput,
    RankingV3ShadowCandidateInput,
    stable_digest,
)
from qagent.db import (
    Base,
    create_db_engine,
    create_session_factory,
    initialize_database,
)
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository
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


def test_initialize_database_repairs_legacy_scoped_baostock_index_counts(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-index-counts.db'}"
    engine = create_db_engine(database_url)
    now = datetime.now(timezone.utc)
    snapshots = Base.metadata.tables["historical_index_snapshots"]
    memberships = Base.metadata.tables["historical_index_memberships"]
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        connection.execute(
            snapshots.insert(),
            [
                {
                    "provider_mode": "free",
                    "index_id": "CN:000300.IDX",
                    "snapshot_date": date(2025, 1, 2),
                    "status": "ready",
                    "member_count": 300,
                    "source_provider": "baostock",
                    "dataset_revision": 0,
                    "fetched_at": now,
                },
                {
                    "provider_mode": "free",
                    "index_id": "CN:000905.IDX",
                    "snapshot_date": date(2025, 1, 2),
                    "status": "ready",
                    "member_count": 500,
                    "source_provider": "baostock",
                    "dataset_revision": 1,
                    "fetched_at": now,
                },
            ],
        )
        connection.execute(
            memberships.insert(),
            [
                {
                    "provider_mode": "free",
                    "index_id": "CN:000300.IDX",
                    "snapshot_date": date(2025, 1, 2),
                    "instrument_id": "CN:000001",
                    "source_provider": "baostock",
                    "dataset_revision": 0,
                    "fetched_at": now,
                },
                {
                    "provider_mode": "free",
                    "index_id": "CN:000905.IDX",
                    "snapshot_date": date(2025, 1, 2),
                    "instrument_id": "CN:000002",
                    "source_provider": "baostock",
                    "dataset_revision": 1,
                    "fetched_at": now,
                },
            ],
        )

    migrated = initialize_database(database_url)
    with migrated.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT dataset_revision, member_count "
                "FROM historical_index_snapshots ORDER BY dataset_revision"
            )
        ).all()

    assert counts == [(0, 1), (1, 500)]


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


def test_initialize_database_restores_walk_forward_lookup_indexes(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'walk-forward-indexes.db'}"
    engine = initialize_database(database_url)
    index_names = {
        "historical_tradability": "ix_historical_tradability_replay_lookup_v2",
        "historical_replay_bars": "ix_historical_replay_bars_lookup_v2",
    }
    with engine.begin() as connection:
        for index_name in index_names.values():
            connection.execute(text(f"DROP INDEX {index_name}"))
        connection.execute(
            text(
                "CREATE INDEX ix_historical_replay_bars_lookup "
                "ON historical_replay_bars "
                "(provider_mode, instrument_id, trade_date, "
                "dataset_revision, source_provider)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_historical_tradability_replay_lookup "
                "ON historical_tradability "
                "(provider_mode, instrument_id, trade_date, "
                "dataset_revision, source_provider)"
            )
        )
    db._initialized_urls.discard(database_url)

    migrated = initialize_database(database_url)
    inspector = inspect(migrated)

    with migrated.connect() as connection:
        for table_name, index_name in index_names.items():
            indexes = {item["name"]: item for item in inspector.get_indexes(table_name)}
            assert index_name in indexes
            directions = {
                row.name: row.desc
                for row in connection.execute(text(f'PRAGMA index_xinfo("{index_name}")'))
                if row.key
            }
            assert directions["dataset_revision"] == 1
            assert index_name.removesuffix("_v2") not in indexes


def test_initialize_database_adds_paper_probe_allocation_column(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-paper-probe.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE paper_trades DROP COLUMN allocation_multiplier"))

    migrated = initialize_database(database_url)
    columns = {column["name"] for column in inspect(migrated).get_columns("paper_trades")}

    assert "allocation_multiplier" in columns


def test_initialize_database_adds_walk_forward_lease_telemetry_columns(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-walk-forward-lease.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE walk_forward_jobs DROP COLUMN lease_maintenance_count")
        )
        connection.execute(text("ALTER TABLE walk_forward_jobs DROP COLUMN lease_recovery_count"))
        connection.execute(
            text("ALTER TABLE walk_forward_jobs DROP COLUMN last_lease_heartbeat_at")
        )

    migrated = initialize_database(database_url)
    columns = {
        column["name"]: column for column in inspect(migrated).get_columns("walk_forward_jobs")
    }

    assert columns["lease_maintenance_count"]["nullable"] is False
    assert columns["lease_recovery_count"]["nullable"] is False
    assert "last_lease_heartbeat_at" in columns


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


def test_initialize_database_adds_strategy_governance_schema_to_legacy_database(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'legacy-strategy-governance.db'}"
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE strategy_state_events (
                    event_id VARCHAR(96) NOT NULL PRIMARY KEY,
                    strategy_id VARCHAR(96) NOT NULL,
                    from_state VARCHAR(32),
                    to_state VARCHAR(32) NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO strategy_state_events "
                "(event_id, strategy_id, from_state, to_state) VALUES "
                "('legacy-event', 'legacy-strategy', NULL, 'research')"
            )
        )

    migrated = initialize_database(database_url)
    inspector = inspect(migrated)

    assert {
        "strategy_versions",
        "strategy_states",
        "strategy_state_events",
        "policy_deployments",
    }.issubset(inspector.get_table_names())
    event_columns = {column["name"] for column in inspector.get_columns("strategy_state_events")}
    assert {
        "sequence",
        "idempotency_key",
        "reason",
        "evidence_json",
        "decision_json",
        "created_at",
    }.issubset(event_columns)
    assert "uq_strategy_state_events_idempotency_key" in {
        index["name"] for index in inspector.get_indexes("strategy_state_events")
    }
    with migrated.connect() as connection:
        legacy = connection.execute(
            text(
                "SELECT sequence, idempotency_key, reason, evidence_json, decision_json "
                "FROM strategy_state_events WHERE event_id = 'legacy-event'"
            )
        ).one()
        trigger_names = set(
            connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND name LIKE 'trg_%_immutable_%'"
                )
            ).scalars()
        )

    assert legacy == (
        1,
        "legacy-governance-event-1",
        "",
        "{}",
        "{}",
    )
    assert {
        "trg_strategy_versions_immutable_update",
        "trg_strategy_versions_immutable_delete",
        "trg_policy_deployments_immutable_update",
        "trg_policy_deployments_immutable_delete",
    }.issubset(trigger_names)


def test_strategy_and_policy_snapshots_are_database_immutable(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'immutable-strategy-snapshots.db'}"
    engine = initialize_database(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO strategy_versions "
                "(strategy_id, strategy_version, definition_digest, definition_json, created_at) "
                "VALUES ('trend', 'v1', 'digest-v1', '{}', '2026-07-17 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO policy_deployments "
                "(deployment_id, strategy_id, policy_version, strategy_version, "
                "factor_version, parameter_version, universe_version, data_revision, "
                "policy_digest, policy_json, previous_deployment_id, created_at) "
                "VALUES ('deployment-v1', 'trend', 'policy-v1', 'v1', 'f1', 'p1', "
                "'u1', '1', 'policy-digest-v1', '{}', NULL, '2026-07-17 00:00:00')"
            )
        )

    with pytest.raises(DBAPIError, match="strategy_versions rows are immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE strategy_versions SET definition_json = 'changed' "
                    "WHERE strategy_id = 'trend' AND strategy_version = 'v1'"
                )
            )
    with pytest.raises(DBAPIError, match="policy_deployments rows are immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM policy_deployments WHERE deployment_id = 'deployment-v1'")
            )


def test_forward_candidate_source_snapshot_migration_quarantines_legacy_rows(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'legacy-forward-source.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    repository = RankingV3ForwardRepository(create_session_factory(database_url))
    identity = RankingV3ForwardIdentity(
        protocol_id="QAGENT-RANK-V3-LEGACY",
        protocol_digest="1" * 64,
        model_version="legacy-forward",
    )
    data_revision = "legacy-data-revision"
    session_date = date(2026, 7, 27)
    source = RankingV3ShadowCandidateInput(
        candidate_id="legacy-candidate",
        source_snapshot_id="source-that-legacy-schema-did-not-store",
        session_date=session_date,
        maturity_session_date=date(2026, 7, 28),
        instrument_id="CN:000001",
        strategy_id="ranking-v3",
        rank=1,
        score=Decimal("0.8"),
        benchmark_id="CN:000300",
        data_revision=data_revision,
        selection_digest="2" * 64,
    )
    repository.ensure_ledger(identity, data_revision)
    session_input = RankingV3ForwardSessionInput(
        session_date=session_date,
        benchmark_id="CN:000300",
        benchmark_return_pct=Decimal("0"),
        portfolio_equity=Decimal("100"),
        stress_portfolio_equity=Decimal("100"),
        benchmark_equity=Decimal("100"),
        data_revision=data_revision,
    )
    repository.record_session(
        identity,
        session_input,
        idempotency_key="legacy-session",
        fact_digest=stable_digest(session_input),
    )
    repository.record_candidate(
        identity,
        source,
        idempotency_key="legacy-candidate",
        fact_digest=stable_digest(source),
    )

    candidate_table = Base.metadata.tables["ranking_v3_forward_candidates"]
    current_ddl = str(CreateTable(candidate_table).compile(dialect=engine.dialect))
    legacy_ddl = current_ddl.replace(
        "\n\tsource_snapshot_id VARCHAR(192) NOT NULL, ",
        "",
    ).replace(
        ", \n\tCONSTRAINT ck_ranking_v3_forward_candidates_source_snapshot "
        "CHECK (length(trim(source_snapshot_id)) > 0)",
        "",
    )
    assert "source_snapshot_id" not in legacy_ddl
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE ranking_v3_forward_candidates "
                "RENAME TO ranking_v3_forward_candidates_current"
            )
        )
        connection.execute(text(legacy_ddl))
        current_columns = [
            column["name"]
            for column in inspect(connection).get_columns("ranking_v3_forward_candidates_current")
            if column["name"] != "source_snapshot_id"
        ]
        column_sql = ", ".join(current_columns)
        connection.execute(
            text(
                f"INSERT INTO ranking_v3_forward_candidates ({column_sql}) "
                f"SELECT {column_sql} "
                "FROM ranking_v3_forward_candidates_current"
            )
        )
        connection.execute(
            text("UPDATE ranking_v3_forward_candidates SET fact_digest = :legacy_digest"),
            {"legacy_digest": "3" * 64},
        )
        connection.execute(text("DROP TABLE ranking_v3_forward_candidates_current"))

    db._initialized_urls.discard(database_url)
    migrated = initialize_database(database_url)
    columns = {
        column["name"]: column
        for column in inspect(migrated).get_columns("ranking_v3_forward_candidates")
    }
    assert columns["source_snapshot_id"]["nullable"] is False
    with migrated.connect() as connection:
        candidate_state = connection.execute(
            text(
                "SELECT source_snapshot_id, integrity_status, quarantine_reason "
                "FROM ranking_v3_forward_candidates"
            )
        ).one()
        ledger_state = connection.execute(
            text(
                "SELECT status, integrity_status, quarantine_reason, "
                "rejection_reasons_json, revision "
                "FROM ranking_v3_forward_ledgers"
            )
        ).one()
        trigger_version = connection.execute(
            text(
                "SELECT version FROM qagent_schema_components "
                "WHERE component = 'ranking_v3_forward_triggers'"
            )
        ).scalar_one()
    assert candidate_state[0] == ""
    assert candidate_state[1] == "legacy_quarantined"
    assert "unverifiable" in candidate_state[2]
    assert ledger_state[0:2] == ("rejected", "legacy_quarantined")
    assert "unverifiable" in ledger_state[2]
    assert "legacy/quarantined" in ledger_state[3]
    assert trigger_version == 2

    restarted = RankingV3ForwardRepository(create_session_factory(database_url))
    snapshot = restarted.load_snapshot(identity)
    assert snapshot is not None
    assert snapshot.ledger.status == "rejected"
    assert snapshot.candidates == []
    assert any("legacy/quarantined" in reason for reason in snapshot.ledger.rejection_reasons)

    first_revision = ledger_state[4]
    db._initialized_urls.discard(database_url)
    initialize_database(database_url)
    with migrated.connect() as connection:
        assert (
            connection.execute(text("SELECT revision FROM ranking_v3_forward_ledgers")).scalar_one()
            == first_revision
        )

    with pytest.raises(
        DBAPIError,
        match="candidate selection facts are immutable",
    ):
        with migrated.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ranking_v3_forward_candidates "
                    "SET source_snapshot_id = 'forged-after-migration'"
                )
            )
    with pytest.raises(
        DBAPIError,
        match="source snapshot is required",
    ):
        with migrated.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ranking_v3_forward_candidates "
                    "SET source_snapshot_id = '' "
                    "WHERE candidate_id = 'legacy-candidate'"
                )
            )


def test_forward_evidence_migration_adds_portfolio_kind_and_preserves_rows(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-forward-evidence.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    evidence_table = Base.metadata.tables["ranking_v3_forward_gate_evidence"]
    current_ddl = str(CreateTable(evidence_table).compile(dialect=engine.dialect))
    legacy_ddl = current_ddl.replace(
        "'historical_gates', 'pbo', 'portfolio'",
        "'historical_gates', 'pbo'",
    )
    assert legacy_ddl != current_ddl

    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE ranking_v3_forward_gate_evidence "
                "RENAME TO ranking_v3_forward_gate_evidence_current"
            )
        )
        connection.execute(text(legacy_ddl))
        connection.execute(
            text(
                "INSERT INTO ranking_v3_forward_gate_evidence ("
                "evidence_digest, protocol_id, protocol_digest, model_version, "
                "evidence_kind, sequence, data_revision, passed, payload_json, "
                "idempotency_key, recorded_at"
                ") VALUES ("
                ":evidence_digest, 'QAGENT-RANK-V3', :protocol_digest, 'v3', "
                "'pbo', 1, 'revision-1', 1, '{}', 'pbo-1', :recorded_at"
                ")"
            ),
            {
                "evidence_digest": "a" * 64,
                "protocol_digest": "b" * 64,
                "recorded_at": datetime(2026, 7, 27, tzinfo=timezone.utc),
            },
        )
        connection.execute(text("DROP TABLE ranking_v3_forward_gate_evidence_current"))

    db._initialized_urls.discard(database_url)
    migrated = initialize_database(database_url)
    with migrated.begin() as connection:
        table_sql = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' "
                "AND name = 'ranking_v3_forward_gate_evidence'"
            )
        ).scalar_one()
        assert "'portfolio'" in table_sql
        assert (
            connection.execute(
                text(
                    "SELECT evidence_kind FROM ranking_v3_forward_gate_evidence "
                    "WHERE evidence_digest = :evidence_digest"
                ),
                {"evidence_digest": "a" * 64},
            ).scalar_one()
            == "pbo"
        )
        connection.execute(
            text(
                "INSERT INTO ranking_v3_forward_gate_evidence ("
                "evidence_digest, protocol_id, protocol_digest, model_version, "
                "evidence_kind, sequence, data_revision, passed, payload_json, "
                "idempotency_key, recorded_at"
                ") VALUES ("
                ":evidence_digest, 'QAGENT-RANK-V3', :protocol_digest, 'v3', "
                "'portfolio', 1, 'revision-1', 1, '{}', 'portfolio-1', :recorded_at"
                ")"
            ),
            {
                "evidence_digest": "c" * 64,
                "protocol_digest": "b" * 64,
                "recorded_at": datetime(2026, 7, 28, tzinfo=timezone.utc),
            },
        )

    with pytest.raises(
        DBAPIError,
        match="ranking_v3_forward_gate_evidence rows are immutable",
    ):
        with migrated.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ranking_v3_forward_gate_evidence "
                    "SET passed = 0 WHERE evidence_digest = :evidence_digest"
                ),
                {"evidence_digest": "a" * 64},
            )


def test_forward_trigger_bundle_is_versioned_and_replaced_on_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'forward-trigger-version.db'}"
    engine = initialize_database(database_url)
    trigger_name = "trg_ranking_v3_forward_ledgers_transition_update"

    with engine.begin() as connection:
        connection.execute(text(f'DROP TRIGGER "{trigger_name}"'))
        connection.execute(
            text(
                f"CREATE TRIGGER {trigger_name} "
                "BEFORE UPDATE ON ranking_v3_forward_ledgers "
                "BEGIN SELECT RAISE(ABORT, 'stale trigger body'); END"
            )
        )
        connection.execute(
            text(
                "UPDATE qagent_schema_components SET version = 1 "
                "WHERE component = 'ranking_v3_forward_triggers'"
            )
        )

    db._initialized_urls.discard(database_url)
    migrated = initialize_database(database_url)
    with migrated.connect() as connection:
        trigger_sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name"),
            {"name": trigger_name},
        ).scalar_one()
        version = connection.execute(
            text(
                "SELECT version FROM qagent_schema_components "
                "WHERE component = 'ranking_v3_forward_triggers'"
            )
        ).scalar_one()

    assert "stale trigger body" not in trigger_sql
    assert "ledger transition is invalid" in trigger_sql
    assert version == 2


def test_initialize_database_adds_production_schema_and_backfills_legacy_paper_trades(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'legacy-production-selection.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    production_tables = (
        "ranking_v3_production_idempotency_keys",
        "ranking_v3_production_selections",
        "ranking_v3_production_batches",
    )
    paper_columns = (
        "admission_source",
        "production_identity_digest",
        "production_batch_fact_digest",
        "production_selection_item_digest",
        "release_proof_digest",
    )
    with engine.begin() as connection:
        for table_name in production_tables:
            connection.execute(text(f"DROP TABLE {table_name}"))
        for column in paper_columns[1:]:
            connection.execute(text(f"DROP INDEX ix_paper_trades_{column}"))
        for column in paper_columns:
            connection.execute(text(f"ALTER TABLE paper_trades DROP COLUMN {column}"))
        connection.execute(
            text(
                "INSERT INTO paper_trades ("
                "trade_id, source_snapshot_id, provider, instrument_id, strategy_id, "
                "status, signal_date, trigger_price, allocation_multiplier, holding_days, "
                "notes, created_at, updated_at"
                ") VALUES ("
                "'legacy-paper', 'legacy-snapshot', 'free', 'CN:000001', 'legacy', "
                "'pending', '2026-07-01', 10, 1, 0, '', "
                "'2026-07-01 00:00:00', '2026-07-01 00:00:00'"
                ")"
            )
        )

    migrated = initialize_database(database_url)
    inspector = inspect(migrated)
    assert set(production_tables).issubset(inspector.get_table_names())
    assert set(paper_columns).issubset(
        {column["name"] for column in inspector.get_columns("paper_trades")}
    )
    with migrated.connect() as connection:
        legacy = connection.execute(
            text(
                "SELECT admission_source, production_identity_digest, "
                "production_batch_fact_digest, production_selection_item_digest, "
                "release_proof_digest FROM paper_trades "
                "WHERE trade_id = 'legacy-paper'"
            )
        ).one()
        version = connection.execute(
            text(
                "SELECT version FROM qagent_schema_components "
                "WHERE component = 'ranking_v3_production_triggers'"
            )
        ).scalar_one()
        triggers = set(
            connection.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND (name LIKE 'trg_ranking_v3_production_%' "
                    "OR name LIKE 'trg_opportunity_snapshots_production_reference_%' "
                    "OR name LIKE 'trg_paper_trades_ranking_v3_production_%')"
                )
            ).scalars()
        )
    assert legacy == ("legacy_unknown", None, None, None, None)
    assert version == 2
    assert {
        "trg_ranking_v3_production_batches_immutable_update",
        "trg_ranking_v3_production_batches_immutable_delete",
        "trg_ranking_v3_production_selections_batch_reference_insert",
        "trg_opportunity_snapshots_production_reference_update",
        "trg_opportunity_snapshots_production_reference_delete",
        "trg_paper_trades_ranking_v3_production_insert",
        "trg_paper_trades_ranking_v3_production_update",
    }.issubset(triggers)

    for _ in range(2):
        db._initialized_urls.discard(database_url)
        initialize_database(database_url)
    with migrated.connect() as connection:
        assert (
            connection.execute(
                text("SELECT admission_source FROM paper_trades WHERE trade_id = 'legacy-paper'")
            ).scalar_one()
            == "legacy_unknown"
        )


def test_production_trigger_bundle_is_versioned_and_replaced_on_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'production-trigger-version.db'}"
    engine = initialize_database(database_url)
    trigger_name = "trg_ranking_v3_production_batches_immutable_update"
    with engine.begin() as connection:
        connection.execute(text(f'DROP TRIGGER "{trigger_name}"'))
        connection.execute(
            text(
                f"CREATE TRIGGER {trigger_name} "
                "BEFORE UPDATE ON ranking_v3_production_batches "
                "BEGIN SELECT RAISE(ABORT, 'stale production trigger'); END"
            )
        )
        connection.execute(
            text(
                "UPDATE qagent_schema_components SET version = 0 "
                "WHERE component = 'ranking_v3_production_triggers'"
            )
        )

    db._initialized_urls.discard(database_url)
    migrated = initialize_database(database_url)
    with migrated.connect() as connection:
        trigger_sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name"),
            {"name": trigger_name},
        ).scalar_one()
        version = connection.execute(
            text(
                "SELECT version FROM qagent_schema_components "
                "WHERE component = 'ranking_v3_production_triggers'"
            )
        ).scalar_one()
    assert "stale production trigger" not in trigger_sql
    assert "rows are immutable" in trigger_sql
    assert version == 2
