from fastapi.testclient import TestClient

from qagent.app import create_app


def test_paper_trade_from_opportunity_creates_once_and_rejects_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-from-card.db'}")
    client = TestClient(create_app())
    opportunity = {
        "card_id": "card_test_0001",
        "provider": "fixture",
        "instrument_id": "US:TEST",
        "strategy_id": "breakout_volume_confirmation",
        "trigger_price": "82.00",
        "initial_stop": "78.72",
        "target_1": "88.56",
        "rank_score": 0.91,
        "action": "watch_trigger",
        "risk_status": "clear",
    }

    created = client.post("/api/paper-trades/from-opportunity", json=opportunity)
    duplicate = client.post("/api/paper-trades/from-opportunity", json=opportunity)
    blocked = client.post(
        "/api/paper-trades/from-opportunity",
        json={**opportunity, "card_id": "card_blocked", "risk_status": "blocked"},
    )
    listed = client.get("/api/paper-trades")

    assert created.status_code == 200
    assert created.json()["created"] is True
    assert created.json()["trade"]["instrument_id"] == "US:TEST"
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert duplicate.json()["trade"]["trade_id"] == created.json()["trade"]["trade_id"]
    assert blocked.status_code == 400
    assert listed.json()["summary"]["total"] == 1


def test_agent_answers_from_paper_trade_context(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-agent.db'}")
    client = TestClient(create_app())
    client.post(
        "/api/paper-trades/from-opportunity",
        json={
            "card_id": "card_agent_0001",
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

    response = client.post(
        "/api/agent/query",
        json={"question": "我买了这个现在怎么办？", "instrument_id": "US:TEST"},
    )

    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "模拟盘" in answer
    assert "US:TEST" in answer
    assert "不是个性化投资建议" in answer


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
