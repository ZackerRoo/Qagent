from fastapi.testclient import TestClient

from qagent.app import create_app


def test_opportunities_endpoint_returns_cards():
    client = TestClient(create_app())
    response = client.get("/api/opportunities")
    assert response.status_code == 200
    body = response.json()
    assert "cards" in body
    assert "items" in body
    assert "data_health" in body
    assert len(body["cards"]) >= 1
    assert body["cards"][0]["scenario"]["downside_pct"] < 0
    assert body["items"][0]["instrument_id"]


def test_overview_endpoint_returns_markets_and_cards():
    client = TestClient(create_app())
    response = client.get("/api/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["market_regime"]["US"]
    assert body["market_regime"]["CN"]
    assert body["top_cards"]


def test_agent_endpoint_answers_from_card_context():
    client = TestClient(create_app())
    response = client.post("/api/agent/query", json={"question": "Why is US:TEST on the list?"})
    assert response.status_code == 200
    assert response.json()["answer"]
