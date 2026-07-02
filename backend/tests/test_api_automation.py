import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

import qagent.api.routes as routes
from qagent.app import create_app
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.automation_scheduler import AutomationScheduler, AutoProcessingSettings
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import OpportunitySnapshotRow, ScanRunRow


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


def test_automation_scheduler_seeds_latest_signal_day_not_latest_inserted_rows(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'automation-latest-signal.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setattr(routes, "_automation_scheduler", AutomationScheduler())
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                ScanRunRow(
                    run_id="scan-latest-signal",
                    provider="free",
                    mode="full_market",
                    symbols=json.dumps(["CN:588850", "CN:588190"]),
                    scanned=2,
                    cards=2,
                    data_health="{}",
                    created_at=now - timedelta(minutes=10),
                ),
                ScanRunRow(
                    run_id="scan-old-inserted-last",
                    provider="free",
                    mode="full_market",
                    symbols=json.dumps(["CN:589300", "CN:589630"]),
                    scanned=2,
                    cards=2,
                    data_health="{}",
                    created_at=now,
                ),
            ]
        )
        for index, (run_id, instrument_id, signal_date, rank_score, created_at) in enumerate(
            [
                (
                    "scan-latest-signal",
                    "CN:588850",
                    date(2026, 7, 1),
                    Decimal("0.91"),
                    now - timedelta(minutes=10),
                ),
                (
                    "scan-latest-signal",
                    "CN:588190",
                    date(2026, 7, 1),
                    Decimal("0.88"),
                    now - timedelta(minutes=10),
                ),
                (
                    "scan-old-inserted-last",
                    "CN:589300",
                    date(2026, 6, 26),
                    Decimal("0.99"),
                    now,
                ),
                (
                    "scan-old-inserted-last",
                    "CN:589630",
                    date(2026, 6, 26),
                    Decimal("0.98"),
                    now,
                ),
            ],
            start=1,
        ):
            session.add(
                OpportunitySnapshotRow(
                    snapshot_id=f"{run_id}:card-{index}",
                    run_id=run_id,
                    card_id=f"card-{index}",
                    instrument_id=instrument_id,
                    market="CN",
                    status="setup_ready",
                    signal_date=signal_date,
                    latest_close=Decimal("2.00"),
                    primary_strategy_id="sector_rotation_relative_strength",
                    score=rank_score,
                    strategy_score=rank_score,
                    rank_score=rank_score,
                    trigger_price=Decimal("2.10"),
                    initial_stop=Decimal("1.95"),
                    target_1=Decimal("2.40"),
                    card_json=json.dumps(
                        {
                            "instrument_id": instrument_id,
                            "instrument_label": instrument_id,
                        },
                        sort_keys=True,
                    ),
                    created_at=created_at,
                )
            )
        session.commit()

    client = TestClient(create_app())
    response = client.post(
        "/api/automation/scheduler/run-once"
        "?provider=free&run_scan=false&run_alerts=false&update_paper=false"
        "&seed_paper=true&seed_limit=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["last_result"]["paper_created"] == 2
    assert body["last_result"]["data_health"]["automation_seed_latest_signal_date"] == "2026-07-01"

    trades = client.get("/api/paper-trades?limit=10").json()["trades"]
    assert [trade["instrument_id"] for trade in trades] == ["CN:588190", "CN:588850"]
    assert {trade["signal_date"] for trade in trades} == {"2026-07-01"}


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


def test_automation_scheduler_state_runs_overdue_cycle(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'automation-scheduler-overdue.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    scheduler = AutomationScheduler()
    overdue_at = datetime.now(timezone.utc) - timedelta(minutes=45)
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._status = "idle"
        scheduler._settings = AutoProcessingSettings(
            provider="fixture",
            symbols="US:TEST",
            interval_seconds=60,
            run_scan=False,
            seed_paper=False,
            update_paper=False,
            run_alerts=False,
        )
        scheduler._next_run_at = overdue_at
    monkeypatch.setattr(routes, "_automation_scheduler", scheduler)
    client = TestClient(create_app())

    response = client.get("/api/automation/scheduler")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["run_count"] == 1
    assert body["last_completed_at"] is not None
    assert datetime.fromisoformat(body["next_run_at"]) > datetime.now(timezone.utc)
