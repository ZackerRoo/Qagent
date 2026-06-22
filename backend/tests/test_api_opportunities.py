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
    assert "strategy_health" in body
    assert len(body["cards"]) >= 1
    assert body["cards"][0]["scenario"]["downside_pct"] < 0
    assert body["cards"][0]["signals"]
    assert body["cards"][0]["strategy_evaluations"]
    assert body["cards"][0]["primary_strategy_id"]
    assert body["cards"][0]["strategy_score"] >= 0
    assert body["cards"][0]["rank_score"] >= body["cards"][0]["strategy_score"]
    assert body["cards"][0]["rank_reasons"]
    assert body["items"][0]["instrument_id"]
    assert body["items"][0]["strategies_passed"] >= 1
    assert body["strategy_health"]


def test_opportunities_endpoint_returns_pead_strategy_when_fixture_has_earnings():
    client = TestClient(create_app())
    response = client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    assert response.status_code == 200

    card = response.json()["cards"][0]
    by_id = {strategy["strategy_id"]: strategy for strategy in card["strategy_evaluations"]}

    assert card["primary_strategy_id"] == "pead_earnings_drift"
    assert card["entry_plan"]["entry_type"] == "pead"
    assert by_id["pead_earnings_drift"]["status"] == "passed"
    assert by_id["pead_earnings_drift"]["missing_data"] == []


def test_overview_endpoint_returns_markets_and_cards():
    client = TestClient(create_app())
    response = client.get("/api/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["market_regime"]["US"]
    assert body["market_regime"]["CN"]
    assert body["top_cards"]


def test_daily_brief_endpoint_returns_research_digest():
    client = TestClient(create_app())

    response = client.get("/api/daily-brief?provider=fixture&include_news=false")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "fixture"
    assert body["symbols"]
    assert body["headline"]
    assert body["top_opportunities"]
    assert body["entry_watch"]
    assert body["strategy_validation"]
    assert body["data_caveats"]
    assert body["next_steps"]
    assert body["data_health"]["brief_opportunities"] == str(len(body["top_opportunities"]))


def test_agent_endpoint_answers_from_card_context():
    client = TestClient(create_app())
    response = client.post("/api/agent/query", json={"question": "Why is US:TEST on the list?"})
    assert response.status_code == 200
    assert "trend_strength" in response.json()["answer"]


def test_agent_endpoint_answers_buy_scenario_from_card_context():
    client = TestClient(create_app())
    response = client.post(
        "/api/agent/query",
        json={"question": "If I buy this, what happens?", "instrument_id": "US:TEST"},
    )
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "trigger" in answer
    assert "not advice" in answer.lower()
