from fastapi.testclient import TestClient

from qagent.app import create_app


def test_paper_trading_api_seeds_updates_and_lists_trades(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-api.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")

    seed_response = client.post("/api/paper-trades/seed?provider=fixture&limit=5")
    update_response = client.post("/api/paper-trades/update?provider=fixture")
    list_response = client.get("/api/paper-trades")

    assert seed_response.status_code == 200
    assert seed_response.json()["created"] == 1
    assert update_response.status_code == 200
    update_body = update_response.json()
    assert update_body["summary"]["total"] == 1
    assert update_body["summary"]["closed"] == 1
    assert list_response.status_code == 200
    assert list_response.json()["trades"][0]["instrument_id"] == "US:TEST"
