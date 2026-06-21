from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from qagent.config import get_settings


class Base(DeclarativeBase):
    pass


def create_db_engine(database_url: str | None = None):
    settings = get_settings()
    return create_engine(database_url or settings.database_url, future=True)


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(database_url), expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session_factory = create_session_factory()
    with session_factory() as session:
        yield session
