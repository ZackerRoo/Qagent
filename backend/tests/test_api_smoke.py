from fastapi.testclient import TestClient

from qagent.app import create_app


def test_health_endpoint():
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cors_allows_fallback_vite_dev_port():
    client = TestClient(create_app())
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:5174",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"


def test_overview_returns_market_rotation_radar():
    client = TestClient(create_app())

    response = client.get("/api/overview?provider=fixture&symbols=CN:000001")

    assert response.status_code == 200
    body = response.json()
    radar = body["rotation_radar"]
    assert radar["data_health"]["rotation_cards"] == "1"
    assert radar["themes"]
    assert radar["themes"][0]["leaders"][0]["instrument_label"] == "平安银行 000001.SZ"
    assert body["research_center"]["portfolio_advisor"]["summary"]
    assert body["research_center"]["walk_forward_validation"]["windows"]


def test_opportunities_return_signal_hub_for_cards():
    client = TestClient(create_app())

    response = client.get("/api/opportunities?provider=fixture&symbols=CN:000001")

    assert response.status_code == 200
    card = response.json()["cards"][0]
    assert card["signal_hub"]["components"]
    assert card["signal_hub"]["similar_validation"]["summary"]
    assert any(item["kind"] == "signal_weakened" for item in card["signal_hub"]["alert_suggestions"])
