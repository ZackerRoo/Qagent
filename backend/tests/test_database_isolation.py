from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as sqlalchemy_create_engine

from qagent.app import create_app
from qagent.api import routes
from qagent.config import WORKSPACE_ROOT, get_settings
from qagent.db import create_db_engine, create_session_factory, initialize_database


REAL_DATABASE = (WORKSPACE_ROOT / "data" / "qagent.db").resolve()
REAL_DATABASE_URL = f"sqlite:///{REAL_DATABASE}"


def test_pytest_default_database_is_isolated_from_workspace():
    settings = get_settings()
    assert settings.database_url != REAL_DATABASE_URL
    assert settings.data_dir.resolve() != REAL_DATABASE.parent
    assert Path(settings.database_url.removeprefix("sqlite:///")).resolve() != REAL_DATABASE


@pytest.mark.parametrize(
    "factory",
    [
        create_db_engine,
        create_session_factory,
        initialize_database,
    ],
)
def test_pytest_database_guard_blocks_explicit_workspace_database(factory):
    with pytest.raises(RuntimeError, match="blocked workspace data/qagent.db"):
        factory(REAL_DATABASE_URL)


def test_pytest_database_guard_blocks_direct_sqlalchemy_engine():
    with pytest.raises(RuntimeError, match="blocked workspace data/qagent.db"):
        sqlalchemy_create_engine(REAL_DATABASE_URL)


def test_pytest_database_guard_blocks_default_when_environment_is_real(monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", REAL_DATABASE_URL)
    with pytest.raises(RuntimeError, match="blocked workspace data/qagent.db"):
        initialize_database()


def test_api_repository_path_fails_closed_before_workspace_migration(monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", REAL_DATABASE_URL)
    with pytest.raises(RuntimeError, match="blocked workspace data/qagent.db"):
        routes._repo()


def test_app_lifespan_and_migration_use_only_per_test_database():
    settings = get_settings()
    test_database = Path(settings.database_url.removeprefix("sqlite:///")).resolve()
    assert test_database != REAL_DATABASE
    assert not test_database.exists()

    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200

    assert test_database.exists()
    engine = create_db_engine(settings.database_url)
    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "automation_migration_audits" in tables
    assert "brief_runs" in tables
