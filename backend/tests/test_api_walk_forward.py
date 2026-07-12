import json
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from qagent.api import routes
from qagent.app import create_app
from qagent.storage.tables import WalkForwardRunRow


def test_walk_forward_run_queries_return_latest_and_complete_payload(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-api.db'}",
    )
    now = datetime.now(timezone.utc)
    payload = {
        "owner_run_id": "api-walk-forward-1",
        "snapshots": [{"decision_date": "2025-01-02"}],
        "cost_sensitivity": [{"key": "stress"}],
    }
    data_health = {
        "walk_forward_top_5_oos_gate": "insufficient",
        "walk_forward_equal_weight_benchmark": "ready",
    }
    with routes._repo().session_factory() as session:
        session.add(
            WalkForwardRunRow(
                run_id="api-walk-forward-1",
                provider="free",
                status="succeeded",
                start_date=date(2024, 1, 2),
                end_date=date(2025, 1, 2),
                dataset_revision=9,
                rebalance_step_sessions=5,
                lookback_days=400,
                snapshot_count=52,
                top_5_trade_count=24,
                top_10_trade_count=48,
                top_5_return_pct=Decimal("8.25"),
                top_10_return_pct=Decimal("7.10"),
                top_5_oos_trades=12,
                top_10_oos_trades=18,
                top_5_oos_gate="insufficient",
                top_10_oos_gate="insufficient",
                reproducibility_digest="digest-1",
                payload_json=json.dumps(payload),
                data_health=json.dumps(data_health),
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    client = TestClient(create_app())
    listed = client.get("/api/walk-forward/runs?provider=free&limit=5")
    latest = client.get("/api/walk-forward/runs/latest?provider=free")
    detail = client.get("/api/walk-forward/runs/api-walk-forward-1")
    missing = client.get("/api/walk-forward/runs/missing")

    assert listed.status_code == 200
    assert listed.json()["runs"][0]["run_id"] == "api-walk-forward-1"
    assert latest.status_code == 200
    assert latest.json()["top_5_return_pct"] == 8.25
    assert detail.status_code == 200
    assert detail.json()["payload"]["cost_sensitivity"][0]["key"] == "stress"
    assert detail.json()["data_health"]["walk_forward_equal_weight_benchmark"] == "ready"
    assert missing.status_code == 404
