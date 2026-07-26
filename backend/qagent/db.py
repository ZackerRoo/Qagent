from collections.abc import Generator
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock

from sqlalchemy import BigInteger, DateTime, Numeric, create_engine
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Dialect
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.types import TypeDecorator

from qagent.config import get_settings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Persist aware datetimes as UTC and restore aware UTC values on reads."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        return dialect.type_descriptor(DateTime(timezone=dialect.name != "sqlite"))

    def process_bind_param(self, value: datetime | None, dialect: Dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        normalized = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(self, value: datetime | None, _dialect: Dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class SQLiteScaledDecimal(TypeDecorator[Decimal]):
    """Use scaled integers on SQLite and native fixed precision elsewhere."""

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int, scale: int):
        self.precision = precision
        self.scale = scale
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(BigInteger())
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value: Decimal | int | str | None, dialect: Dialect):
        if value is None:
            return None
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if not decimal_value.is_finite():
            raise ValueError("SQLiteScaledDecimal requires a finite value")
        scaled_value = decimal_value.scaleb(self.scale)
        if scaled_value != scaled_value.to_integral_value():
            raise ValueError(f"decimal value exceeds scale {self.scale}")
        if dialect.name == "sqlite":
            integer_value = int(scaled_value)
            if not -(2**63) <= integer_value <= 2**63 - 1:
                raise OverflowError("scaled decimal exceeds signed 64-bit SQLite range")
            return integer_value
        return decimal_value

    def process_result_value(self, value, dialect: Dialect):
        if value is None:
            return None
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if dialect.name == "sqlite":
            return decimal_value.scaleb(-self.scale)
        return decimal_value


_schema_lock = Lock()
_initialized_urls: set[str] = set()


def create_db_engine(database_url: str | None = None):
    settings = get_settings()
    url = database_url or settings.database_url
    parsed = make_url(url)
    is_file_sqlite = parsed.drivername.startswith("sqlite") and parsed.database not in (
        None,
        "",
        ":memory:",
    )
    engine_kwargs = {}
    if is_file_sqlite:
        Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        # API repositories create short-lived engines. Do not leave one pooled
        # SQLite descriptor behind for every request.
        engine_kwargs["poolclass"] = NullPool
    engine = create_engine(url, future=True, **engine_kwargs)
    if is_file_sqlite:
        _configure_sqlite_pragmas(engine)
    return engine


def _configure_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()


def initialize_database(database_url: str | None = None):
    # Import table mappings before create_all so direct db initialization sees every table.
    from qagent.storage import tables as _tables  # noqa: F401

    url = database_url or get_settings().database_url
    engine = create_db_engine(url)
    with _schema_lock:
        if url not in _initialized_urls:
            Base.metadata.create_all(engine)
            _apply_additive_migrations(engine)
            _initialized_urls.add(url)
    return engine


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(database_url), expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session_factory = create_session_factory()
    with session_factory() as session:
        yield session


def _apply_additive_migrations(engine: Engine) -> None:
    if not engine.dialect.name.startswith("sqlite"):
        return
    inspector = inspect(engine)
    with engine.begin() as connection:
        _add_missing_columns(
            connection,
            inspector,
            "market_bar_cache",
            {
                "turnover": "NUMERIC(28, 4)",
                "adjusted_open": "NUMERIC(18, 6)",
                "adjusted_high": "NUMERIC(18, 6)",
                "adjusted_low": "NUMERIC(18, 6)",
                "adjusted_close": "NUMERIC(18, 6)",
                "adjustment_factor": "NUMERIC(20, 10)",
                "adjustment_type": "VARCHAR(32)",
            },
        )
        _add_missing_columns(
            connection,
            inspector,
            "paper_trades",
            {
                "allocation_multiplier": "NUMERIC(8, 4) NOT NULL DEFAULT 1.0",
            },
        )
        _add_missing_columns(
            connection,
            inspector,
            "walk_forward_jobs",
            {
                "lease_maintenance_count": "INTEGER NOT NULL DEFAULT 0",
                "lease_recovery_count": "INTEGER NOT NULL DEFAULT 0",
                "last_lease_heartbeat_at": "DATETIME",
            },
        )
        added_event_columns = _add_missing_columns(
            connection,
            inspector,
            "paper_trade_events",
            {
                "instrument_id": "VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN'",
            },
        )
        if "instrument_id" in added_event_columns:
            _backfill_paper_trade_event_instrument_ids(connection)
        _add_missing_columns(
            connection,
            inspector,
            "historical_trading_rules",
            {
                "minimum_order_quantity": "INTEGER NOT NULL DEFAULT 100",
                "quantity_step": "INTEGER NOT NULL DEFAULT 100",
            },
        )
        _add_missing_columns(
            connection,
            inspector,
            "historical_instrument_rule_metadata",
            {
                "board_lot": "INTEGER NOT NULL DEFAULT 100",
                "minimum_order_quantity": "INTEGER NOT NULL DEFAULT 100",
                "quantity_step": "INTEGER NOT NULL DEFAULT 100",
            },
        )
        for table_name in (
            "fundamental_snapshots",
            "historical_tradability",
            "historical_instrument_profiles",
            "historical_industry_snapshots",
            "historical_index_snapshots",
            "historical_index_memberships",
            "historical_replay_bars",
            "historical_corporate_actions",
            "historical_corporate_action_coverage",
        ):
            added_columns = _add_missing_columns(
                connection,
                inspector,
                table_name,
                {"dataset_revision": "INTEGER NOT NULL DEFAULT 0"},
            )
            if "dataset_revision" in added_columns:
                _backfill_dataset_revision(connection, table_name)
        for table_name in (
            "historical_universe_manifests",
            "historical_replay_universe_members",
        ):
            _add_missing_columns(
                connection,
                inspector,
                table_name,
                {"owner_run_id": ("VARCHAR(64) NOT NULL DEFAULT 'legacy-unknown-owner'")},
            )
        _add_strategy_governance_columns(connection, inspector)
        _rebuild_revision_scoped_tables(connection)
        _repair_legacy_scoped_index_snapshot_counts(connection)
        _create_missing_metadata_indexes(connection)
        _drop_obsolete_walk_forward_indexes(connection)
        _create_strategy_governance_indexes(connection)
        _create_immutable_strategy_governance_triggers(connection)


def _add_missing_columns(connection, inspector, table_name: str, additions) -> set[str]:
    if not inspector.has_table(table_name):
        return set()
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    added = set()
    for column, sql_type in additions.items():
        if column not in existing:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column} {sql_type}"))
            added.add(column)
    return added


def _backfill_dataset_revision(connection, table_name: str) -> None:
    if not inspect(connection).has_table(table_name):
        return
    connection.execute(
        text(
            f"UPDATE {table_name} SET dataset_revision = COALESCE(("
            "SELECT revision FROM historical_data_revisions "
            f"WHERE historical_data_revisions.provider_mode = {table_name}.provider_mode"
            "), 0) WHERE dataset_revision = 0"
        )
    )


def _backfill_paper_trade_event_instrument_ids(connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("paper_trade_events") or not inspector.has_table("paper_trades"):
        return
    connection.execute(
        text(
            "UPDATE paper_trade_events SET instrument_id = COALESCE(("
            "SELECT paper_trades.instrument_id FROM paper_trades "
            "WHERE paper_trades.trade_id = paper_trade_events.trade_id"
            "), 'UNKNOWN') WHERE instrument_id IS NULL OR instrument_id = 'UNKNOWN'"
        )
    )


def _add_strategy_governance_columns(connection, inspector) -> None:
    _add_missing_columns(
        connection,
        inspector,
        "strategy_versions",
        {
            "definition_digest": "VARCHAR(64) NOT NULL DEFAULT ''",
            "definition_json": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
    )
    _add_missing_columns(
        connection,
        inspector,
        "policy_deployments",
        {
            "strategy_version": "VARCHAR(96) NOT NULL DEFAULT 'legacy'",
            "factor_version": "VARCHAR(96) NOT NULL DEFAULT 'legacy'",
            "parameter_version": "VARCHAR(96) NOT NULL DEFAULT 'legacy'",
            "universe_version": "VARCHAR(96) NOT NULL DEFAULT 'legacy'",
            "data_revision": "VARCHAR(128) NOT NULL DEFAULT 'unversioned'",
            "policy_digest": "VARCHAR(64) NOT NULL DEFAULT ''",
            "policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "previous_deployment_id": "VARCHAR(96)",
            "created_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
    )
    _add_missing_columns(
        connection,
        inspector,
        "strategy_states",
        {
            "current_deployment_id": "VARCHAR(96)",
            "previous_deployment_id": "VARCHAR(96)",
            "current_policy_version": "VARCHAR(96)",
            "previous_policy_version": "VARCHAR(96)",
            "effective_weight": "NUMERIC(12, 10) NOT NULL DEFAULT 0",
            "revision": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
            "updated_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
    )
    event_columns = _add_missing_columns(
        connection,
        inspector,
        "strategy_state_events",
        {
            "sequence": "INTEGER NOT NULL DEFAULT 0",
            "idempotency_key": "VARCHAR(160)",
            "event_type": "VARCHAR(64) NOT NULL DEFAULT 'legacy'",
            "action": "VARCHAR(64) NOT NULL DEFAULT 'legacy'",
            "deployment_id": "VARCHAR(96)",
            "previous_deployment_id": "VARCHAR(96)",
            "policy_version": "VARCHAR(96)",
            "effective_weight": "NUMERIC(12, 10) NOT NULL DEFAULT 0",
            "reason": "TEXT NOT NULL DEFAULT ''",
            "evidence_json": "TEXT NOT NULL DEFAULT '{}'",
            "decision_json": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
    )
    if "idempotency_key" in event_columns:
        connection.execute(
            text(
                "UPDATE strategy_state_events "
                "SET idempotency_key = 'legacy-governance-event-' || rowid "
                "WHERE idempotency_key IS NULL OR idempotency_key = ''"
            )
        )
    if "sequence" in event_columns:
        connection.execute(
            text(
                "UPDATE strategy_state_events AS current SET sequence = ("
                "SELECT COUNT(*) FROM strategy_state_events AS earlier "
                "WHERE earlier.strategy_id = current.strategy_id "
                "AND earlier.rowid <= current.rowid)"
            )
        )


def _create_strategy_governance_indexes(connection) -> None:
    inspector = inspect(connection)
    if inspector.has_table("policy_deployments"):
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_policy_deployments_strategy_policy_version "
                "ON policy_deployments (strategy_id, policy_version)"
            )
        )
    if inspector.has_table("strategy_state_events"):
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_strategy_state_events_idempotency_key "
                "ON strategy_state_events (idempotency_key)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_strategy_state_events_strategy_sequence "
                "ON strategy_state_events (strategy_id, sequence)"
            )
        )


def _create_immutable_strategy_governance_triggers(connection) -> None:
    inspector = inspect(connection)
    for table_name in ("strategy_versions", "policy_deployments"):
        if not inspector.has_table(table_name):
            continue
        for operation in ("UPDATE", "DELETE"):
            trigger_name = f"trg_{table_name}_immutable_{operation.lower()}"
            connection.execute(
                text(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger_name} "
                    f"BEFORE {operation} ON {table_name} "
                    "BEGIN "
                    f"SELECT RAISE(ABORT, '{table_name} rows are immutable'); "
                    "END"
                )
            )


def _rebuild_revision_scoped_tables(connection) -> None:
    expected_keys = {
        "fundamental_snapshots": [
            "provider_mode",
            "instrument_id",
            "as_of_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_tradability": [
            "provider_mode",
            "instrument_id",
            "trade_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_instrument_profiles": [
            "provider_mode",
            "instrument_id",
            "snapshot_date",
            "dataset_revision",
        ],
        "historical_industry_snapshots": [
            "provider_mode",
            "instrument_id",
            "snapshot_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_index_snapshots": [
            "provider_mode",
            "index_id",
            "snapshot_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_index_memberships": [
            "provider_mode",
            "index_id",
            "snapshot_date",
            "instrument_id",
            "source_provider",
            "dataset_revision",
        ],
        "historical_replay_bars": [
            "provider_mode",
            "instrument_id",
            "trade_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_corporate_actions": [
            "provider_mode",
            "instrument_id",
            "action_id",
            "source_provider",
            "dataset_revision",
        ],
        "historical_corporate_action_coverage": [
            "provider_mode",
            "instrument_id",
            "start_date",
            "end_date",
            "source_provider",
            "dataset_revision",
        ],
    }
    for table_name, expected_key in expected_keys.items():
        _rebuild_table_primary_key(connection, table_name, expected_key)


def _rebuild_table_primary_key(connection, table_name: str, expected_key: list[str]) -> None:
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return
    current_key = inspector.get_pk_constraint(table_name)["constrained_columns"]
    if current_key == expected_key:
        return
    table = Base.metadata.tables[table_name]
    column_names = [column.name for column in table.columns]
    columns_sql = ", ".join(column_names)
    backup_name = f"{table_name}_migration_backup"
    connection.execute(
        text(f"CREATE TEMP TABLE {backup_name} AS SELECT {columns_sql} FROM {table_name}")
    )
    connection.execute(text(f"DROP TABLE {table_name}"))
    table.create(connection)
    connection.execute(
        text(f"INSERT INTO {table_name} ({columns_sql}) SELECT {columns_sql} FROM {backup_name}")
    )
    connection.execute(text(f"DROP TABLE {backup_name}"))


def _create_missing_metadata_indexes(connection) -> None:
    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        for index in table.indexes:
            index.create(connection, checkfirst=True)


def _repair_legacy_scoped_index_snapshot_counts(connection) -> None:
    """Align legacy BaoStock snapshot metadata with its stored replay scope."""

    inspector = inspect(connection)
    required = {
        "historical_index_snapshots",
        "historical_index_memberships",
    }
    if not all(inspector.has_table(table_name) for table_name in required):
        return
    connection.execute(
        text(
            "UPDATE historical_index_snapshots AS snapshots "
            "SET member_count = ("
            "SELECT COUNT(*) FROM historical_index_memberships AS memberships "
            "WHERE memberships.provider_mode = snapshots.provider_mode "
            "AND memberships.index_id = snapshots.index_id "
            "AND memberships.snapshot_date = snapshots.snapshot_date "
            "AND memberships.source_provider = snapshots.source_provider "
            "AND memberships.dataset_revision = snapshots.dataset_revision"
            ") "
            "WHERE snapshots.source_provider = 'baostock' "
            "AND snapshots.dataset_revision = 0 "
            "AND snapshots.status = 'ready' "
            "AND (snapshots.error IS NULL OR snapshots.error = '') "
            "AND snapshots.member_count <> ("
            "SELECT COUNT(*) FROM historical_index_memberships AS memberships "
            "WHERE memberships.provider_mode = snapshots.provider_mode "
            "AND memberships.index_id = snapshots.index_id "
            "AND memberships.snapshot_date = snapshots.snapshot_date "
            "AND memberships.source_provider = snapshots.source_provider "
            "AND memberships.dataset_revision = snapshots.dataset_revision"
            ")"
        )
    )


def _drop_obsolete_walk_forward_indexes(connection) -> None:
    for index_name in (
        "ix_historical_replay_bars_lookup",
        "ix_historical_tradability_replay_lookup",
    ):
        connection.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
