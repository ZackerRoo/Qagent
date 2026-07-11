from collections.abc import Generator
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock

from sqlalchemy import DateTime, Numeric, String, create_engine
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


class SQLiteExactDecimal(TypeDecorator[Decimal]):
    """Use canonical decimal text on SQLite and native fixed precision elsewhere."""

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int, scale: int):
        self.precision = precision
        self.scale = scale
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value: Decimal | int | str | None, dialect: Dialect):
        if value is None:
            return None
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if not decimal_value.is_finite():
            raise ValueError("SQLiteExactDecimal requires a finite value")
        _, digits, exponent = decimal_value.as_tuple()
        fractional_digits = max(-exponent, 0)
        integer_digits = max(len(digits) + exponent, 0)
        if fractional_digits > self.scale or integer_digits > self.precision - self.scale:
            raise ValueError(
                f"decimal value exceeds NUMERIC({self.precision}, {self.scale})"
            )
        if dialect.name == "sqlite":
            return format(decimal_value, "f")
        return decimal_value

    def process_result_value(self, value, _dialect: Dialect):
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))


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
    if not inspector.has_table("market_bar_cache"):
        return
    existing = {column["name"] for column in inspector.get_columns("market_bar_cache")}
    additions = {
        "adjusted_close": "NUMERIC(18, 6)",
        "adjustment_factor": "NUMERIC(20, 10)",
        "adjustment_type": "VARCHAR(32)",
    }
    with engine.begin() as connection:
        for column, sql_type in additions.items():
            if column not in existing:
                connection.execute(
                    text(f"ALTER TABLE market_bar_cache ADD COLUMN {column} {sql_type}")
                )
