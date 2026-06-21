from collections.abc import Generator
from pathlib import Path
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from qagent.config import get_settings


class Base(DeclarativeBase):
    pass


_schema_lock = Lock()
_initialized_urls: set[str] = set()


def create_db_engine(database_url: str | None = None):
    settings = get_settings()
    url = database_url or settings.database_url
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True)


def initialize_database(database_url: str | None = None):
    url = database_url or get_settings().database_url
    engine = create_db_engine(url)
    with _schema_lock:
        if url not in _initialized_urls:
            Base.metadata.create_all(engine)
            _initialized_urls.add(url)
    return engine


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(database_url), expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session_factory = create_session_factory()
    with session_factory() as session:
        yield session
