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
