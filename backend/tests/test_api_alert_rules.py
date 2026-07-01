from fastapi.testclient import TestClient

from qagent.app import create_app


def test_alert_rule_api_saves_and_evaluates_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'alerts.db'}")
    client = TestClient(create_app())

    create_response = client.post(
        "/api/alert-rules",
        json={
            "rule_id": "entry-US-TEST",
            "instrument_id": "US:TEST",
            "kind": "entry_trigger",
            "operator": ">=",
            "threshold": "82.00",
        },
    )
    assert create_response.status_code == 200

    rules_response = client.get("/api/alert-rules")
    assert rules_response.status_code == 200
    assert rules_response.json()["rules"][0]["rule_id"] == "entry-US-TEST"

    evaluate_response = client.post(
        "/api/alerts/evaluate",
        json={"prices": {"US:TEST": "83.00"}},
    )
    assert evaluate_response.status_code == 200
    body = evaluate_response.json()
    assert body["alerts"][0]["kind"] == "entry_trigger"
    assert body["alerts"][0]["status"] == "triggered"


def test_alert_suggestions_api_uses_recent_opportunities(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'alert-suggestions.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture")

    response = client.get("/api/alert-suggestions")

    assert response.status_code == 200
    suggestions = response.json()["suggestions"]
    assert suggestions
    assert {"entry_trigger", "stop_guard", "target_1_reached"}.issubset(
        {item["kind"] for item in suggestions}
    )
    assert all(item["source_snapshot_id"] for item in suggestions)


def test_alert_run_api_uses_provider_snapshot_and_queues_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'alert-run.db'}")
    client = TestClient(create_app())
    client.post(
        "/api/alert-rules",
        json={
            "rule_id": "entry-US-TEST",
            "instrument_id": "US:TEST",
            "kind": "entry_trigger",
            "operator": ">=",
            "threshold": "82.00",
        },
    )

    response = client.post("/api/alerts/run?provider=fixture&queue=true&recipient=local")
    deliveries = client.get("/api/deliveries?status=queued").json()["deliveries"]

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["rules"] == 1
    assert body["summary"]["triggered"] == 1
    assert body["alerts"][0]["rule_id"] == "entry-US-TEST"
    assert body["delivery"]["status"] == "queued"
    assert deliveries[0]["delivery_id"] == body["delivery"]["delivery_id"]
