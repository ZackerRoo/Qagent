from pathlib import Path

from qagent.config import get_settings


def test_default_database_path_is_workspace_scoped(monkeypatch):
    monkeypatch.delenv("QAGENT_DATABASE_URL", raising=False)
    settings = get_settings()
    workspace_root = Path(__file__).resolve().parents[2]

    assert settings.database_url == f"sqlite:///{workspace_root / 'data' / 'qagent.db'}"
    assert settings.data_dir == workspace_root / "data"
