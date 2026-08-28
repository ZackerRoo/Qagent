import json
import os
import tempfile
from pathlib import Path

import pytest
from dotenv import dotenv_values


_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_REAL_DATABASE_PATH = (_WORKSPACE_ROOT / "data" / "qagent.db").resolve()
_BOOTSTRAP_TEST_ROOT = Path(tempfile.mkdtemp(prefix="qagent-pytest-bootstrap-"))
_BOOTSTRAP_DATABASE = _BOOTSTRAP_TEST_ROOT / "qagent.db"
_ORIGINAL_DATABASE_URL = os.environ.get("QAGENT_DATABASE_URL", "").strip()
_ENV_FILE_DATABASE_URL = str(
    dotenv_values(_WORKSPACE_ROOT / ".env").get("QAGENT_DATABASE_URL", "")
).strip()
_FORBIDDEN_DATABASE_URLS = [f"sqlite:///{_REAL_DATABASE_PATH}"]
_FORBIDDEN_DATABASE_URLS.extend(
    url for url in (_ORIGINAL_DATABASE_URL, _ENV_FILE_DATABASE_URL) if url
)

# These must be installed while conftest itself is imported. An autouse fixture
# runs too late: test modules may import qagent.app before fixture setup.
os.environ["QAGENT_TEST_DATABASE_GUARD"] = "1"
os.environ["QAGENT_TEST_FORBIDDEN_DATABASE_URLS"] = json.dumps(
    sorted(set(_FORBIDDEN_DATABASE_URLS))
)
os.environ["QAGENT_DATABASE_URL"] = f"sqlite:///{_BOOTSTRAP_DATABASE}"
os.environ["QAGENT_DATA_DIR"] = str(_BOOTSTRAP_TEST_ROOT)

import sqlalchemy  # noqa: E402

from qagent.db import _guard_pytest_database_url  # noqa: E402
from qagent.market import instruments  # noqa: E402


_SQLALCHEMY_CREATE_ENGINE = sqlalchemy.create_engine


def _guarded_sqlalchemy_create_engine(url, *args, **kwargs):
    _guard_pytest_database_url(str(url))
    return _SQLALCHEMY_CREATE_ENGINE(url, *args, **kwargs)


sqlalchemy.create_engine = _guarded_sqlalchemy_create_engine


_BASE_CN_INSTRUMENT_NAMES = instruments.CN_INSTRUMENT_NAMES.copy()


def _reset_cn_instrument_names() -> None:
    instruments.CN_INSTRUMENT_NAMES.clear()
    instruments.CN_INSTRUMENT_NAMES.update(_BASE_CN_INSTRUMENT_NAMES)
    instruments._CN_INSTRUMENT_NAMES_READY = True


@pytest.fixture(autouse=True)
def isolate_backend_test_state(tmp_path, monkeypatch):
    test_data_dir = tmp_path / "data"
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{test_data_dir / 'qagent.db'}")
    monkeypatch.setenv("QAGENT_DATA_DIR", str(test_data_dir))
    _reset_cn_instrument_names()
    yield
    _reset_cn_instrument_names()
