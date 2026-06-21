from fastapi.testclient import TestClient

from qagent.app import create_app


def test_watchlist_api_adds_and_lists_items(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-state.db'}")
    client = TestClient(create_app())

    create_response = client.post(
        "/api/watchlist",
        json={
            "instrument_id": "CN:000001",
            "thesis": "Track A-share setup",
            "status": "watch",
            "tags": ["cn", "bank"],
        },
    )

    assert create_response.status_code == 200
    list_response = client.get("/api/watchlist")
    assert list_response.status_code == 200
    body = list_response.json()
    assert body["items"][0]["instrument_id"] == "CN:000001"
    assert body["items"][0]["tags"] == ["cn", "bank"]


def test_positions_api_adds_and_lists_positions(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-position.db'}")
    client = TestClient(create_app())

    create_response = client.post(
        "/api/positions",
        json={
            "instrument_id": "US:TEST",
            "shares": "10",
            "entry_price": "82.00",
            "entry_date": "2026-03-31",
            "strategy_tag": "breakout",
            "initial_stop": "78.72",
            "target_1": "88.56",
            "thesis": "Fixture breakout",
        },
    )

    assert create_response.status_code == 200
    list_response = client.get("/api/positions")
    assert list_response.status_code == 200
    body = list_response.json()
    assert body["positions"][0]["instrument_id"] == "US:TEST"
    assert body["positions"][0]["strategy_tag"] == "breakout"
