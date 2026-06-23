from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.db import create_session_factory, initialize_database
from qagent.storage.repository import QagentRepository


def test_automation_run_api_saves_brief_and_queues_delivery(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'automation.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    client = TestClient(create_app())

    response = client.post(
        "/api/automation/run?provider=fixture&symbols=US:TEST&include_news=false&queue_brief=true&run_backtest=true"
    )
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["provider"] == "fixture"
    assert body["summary"]["cards"] == 1
    assert body["scan_run_id"].startswith("scan-")
    assert body["brief_id"].startswith("brief-")
    assert body["brief_delivery_id"].startswith("delivery-")
    assert body["backtest"]["summary"]["evaluated_signals"] >= 1
    assert repo.list_scan_runs(limit=5)
    assert repo.list_brief_runs(limit=5)
    assert repo.list_delivery_outbox(status="queued", limit=5)
