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
    is_file_sqlite = parsed.drivername.startswith("sqlite") and parsed.database not in (None, "", ":memory:")
    engine_kwargs = {}
    if is_file_sqlite:
        Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
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
                "adjusted_close": "NUMERIC(18, 6)",
                "adjustment_factor": "NUMERIC(20, 10)",
                "adjustment_type": "VARCHAR(32)",
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
            _add_missing_columns(
                connection,
                inspector,
                table_name,
                {"dataset_revision": "INTEGER NOT NULL DEFAULT 0"},
            )
            _backfill_dataset_revision(connection, table_name)
        for table_name in (
            "historical_universe_manifests",
            "historical_replay_universe_members",
        ):
            _add_missing_columns(
                connection,
                inspector,
                table_name,
                {
                    "owner_run_id": (
                        "VARCHAR(64) NOT NULL DEFAULT 'legacy-unknown-owner'"
                    )
                },
            )
        _rebuild_revision_scoped_tables(connection)
        _create_missing_metadata_indexes(connection)


def _add_missing_columns(connection, inspector, table_name: str, additions) -> None:
    if not inspector.has_table(table_name):
        return
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    for column, sql_type in additions.items():
        if column not in existing:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column} {sql_type}"))


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


def _rebuild_table_primary_key(
    connection, table_name: str, expected_key: list[str]
) -> None:
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
        text(
            f"INSERT INTO {table_name} ({columns_sql}) "
            f"SELECT {columns_sql} FROM {backup_name}"
        )
    )
    connection.execute(text(f"DROP TABLE {backup_name}"))


def _create_missing_metadata_indexes(connection) -> None:
    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        for index in table.indexes:
            index.create(connection, checkfirst=True)
