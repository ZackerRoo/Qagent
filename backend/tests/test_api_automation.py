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


def test_automation_scheduler_run_once_updates_paper_status(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'automation-scheduler-run-once.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    client = TestClient(create_app())
    created = client.post(
        "/api/paper-trades/from-opportunity",
        json={
            "card_id": "card_auto_scheduler",
            "provider": "fixture",
            "instrument_id": "US:TEST",
            "strategy_id": "breakout_volume_confirmation",
            "trigger_price": "82.00",
            "initial_stop": "78.72",
            "target_1": "88.56",
            "rank_score": 0.91,
            "action": "watch_trigger",
            "risk_status": "clear",
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=fixture&symbols=US:TEST&run_scan=false&run_alerts=false"
        "&seed_paper=false&update_paper=true"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["last_result"]["provider"] == "fixture"
    assert body["last_result"]["paper_total"] == 1
    assert body["last_result"]["paper_closed"] >= 0
    assert body["run_count"] == 1
    assert body["next_run_at"] is None


def test_automation_scheduler_start_and_stop_are_visible(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'automation-scheduler-start.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    client = TestClient(create_app())

    started = client.post(
        "/api/automation/scheduler/start"
        "?provider=fixture&symbols=US:TEST&interval_seconds=60&run_scan=false"
        "&run_alerts=false&seed_paper=false&update_paper=true"
    )
    state = client.get("/api/automation/scheduler")
    stopped = client.post("/api/automation/scheduler/stop")

    assert started.status_code == 200
    assert started.json()["enabled"] is True
    assert started.json()["settings"]["interval_seconds"] == 60
    assert started.json()["next_run_at"] is not None
    assert state.status_code == 200
    assert state.json()["enabled"] is True
    assert stopped.status_code == 200
    assert stopped.json()["enabled"] is False
    assert stopped.json()["next_run_at"] is None
