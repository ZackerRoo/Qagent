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
    assert "factor_rankings" in body
    assert body["factor_rankings"]
    assert len(body["cards"]) >= 1
    assert body["cards"][0]["scenario"]["downside_pct"] < 0
    assert body["cards"][0]["signals"]
    assert body["cards"][0]["strategy_evaluations"]
    assert body["cards"][0]["primary_strategy_id"]
    assert body["cards"][0]["strategy_score"] >= 0
    assert body["cards"][0]["rank_score"] >= body["cards"][0]["strategy_score"]
    assert body["cards"][0]["rank_reasons"]
    assert body["cards"][0]["factor_score"] >= 0
    assert body["cards"][0]["factor_rank"] >= 1
    assert body["cards"][0]["factor_exposures"]
    assert body["cards"][0]["decision"]["action"] in {
        "candidate_entry",
        "watch_trigger",
        "wait_pullback",
        "avoid",
    }
    assert body["cards"][0]["decision"]["conviction_score"] >= 0
    assert body["cards"][0]["decision"]["failure_conditions"]
    assert body["cards"][0]["decision"]["verification_checks"]
    assert "risk_status" in body["cards"][0]["decision"]
    assert "risk_vetoes" in body["cards"][0]["decision"]
    assert body["cards"][0]["recommendation_summary"]["headline"]
    assert body["items"][0]["instrument_id"]
    assert body["items"][0]["factor_score"] is not None
    assert body["items"][0]["factor_rank"] is not None
    assert "blockers" in body["items"][0]
    assert body["items"][0]["strategies_passed"] >= 1
    assert body["strategy_health"]


def test_opportunities_endpoint_returns_cn_execution_context():
    client = TestClient(create_app())
    response = client.get("/api/opportunities?provider=fixture&symbols=CN:000001")

    assert response.status_code == 200
    card = response.json()["cards"][0]

    assert card["trading_constraints"]["board"] == "深市主板"
    assert card["trading_constraints"]["t_plus_one"] is True
    assert card["market_context"]["industry"] == "银行"
    assert "买点" in card["recommendation_summary"]["buy_timing"]
    assert "卖出" in card["recommendation_summary"]["sell_timing"]


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


def test_market_bars_endpoint_returns_chart_ready_series_and_trade_levels():
    client = TestClient(create_app())

    response = client.get("/api/market-bars?provider=fixture&instrument_id=US:TEST&days=80")

    assert response.status_code == 200
    body = response.json()
    assert body["instrument_id"] == "US:TEST"
    assert body["bars"]
    assert {"trade_date", "close", "ma20", "ma50"}.issubset(body["bars"][-1])
    assert body["levels"]["trigger_price"] is not None
    assert body["levels"]["initial_stop"] is not None
    assert body["data_health"]["provider"] == "fixture"


def test_intraday_radar_endpoint_returns_actionable_scan_items():
    client = TestClient(create_app())

    response = client.get("/api/intraday-radar?provider=fixture&symbols=US:TEST,CN:000001")

    assert response.status_code == 200
    body = response.json()
    assert body["items"]
    first = body["items"][0]
    assert first["instrument_id"] in {"US:TEST", "CN:000001"}
    assert first["latest_close"] is not None
    assert first["signal"] in {
        "approaching_trigger",
        "trigger_breakout",
        "near_stop",
        "near_target",
        "volume_surge",
        "overextended",
        "inside_plan",
        "no_setup",
    }
    assert first["message"]
    assert body["data_health"]["radar_items"] == str(len(body["items"]))


def test_opportunities_explains_unrecommended_symbols():
    client = TestClient(create_app())

    response = client.get("/api/opportunities?provider=fixture&symbols=US:TEST,US:UNKNOWN")

    assert response.status_code == 200
    items = {item["instrument_id"]: item for item in response.json()["items"]}
    assert items["US:UNKNOWN"]["status"] == "no_data"
    assert items["US:UNKNOWN"]["blockers"][0]["code"] == "no_daily_bars"


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


def test_daily_brief_run_api_saves_lists_loads_and_exports_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-brief-runs.db'}")
    client = TestClient(create_app())

    save_response = client.post("/api/daily-brief/runs?provider=fixture&include_news=false")

    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["brief_id"].startswith("brief-")
    assert saved["headline"]

    list_response = client.get("/api/daily-brief/runs")
    assert list_response.status_code == 200
    runs = list_response.json()["runs"]
    assert runs[0]["brief_id"] == saved["brief_id"]
    assert runs[0]["opportunity_count"] == saved["opportunity_count"]

    detail_response = client.get(f"/api/daily-brief/runs/{saved['brief_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["brief"]["headline"] == saved["headline"]
    assert detail["brief"]["top_opportunities"]

    markdown_response = client.get(f"/api/daily-brief/runs/{saved['brief_id']}/markdown")
    assert markdown_response.status_code == 200
    markdown = markdown_response.json()["markdown"]
    assert markdown.startswith("# Qagent Daily Brief")
    assert "## Top Opportunities" in markdown


def test_daily_brief_delivery_api_queues_lists_and_marks_sent(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-deliveries.db'}")
    client = TestClient(create_app())
    save_response = client.post("/api/daily-brief/runs?provider=fixture&include_news=false")
    saved = save_response.json()

    queue_response = client.post(
        f"/api/daily-brief/runs/{saved['brief_id']}/deliveries"
        "?channel=markdown&recipient=local"
    )
    list_response = client.get("/api/deliveries?status=queued")
    sent_response = client.post(f"/api/deliveries/{queue_response.json()['delivery_id']}/mark-sent")

    assert queue_response.status_code == 200
    queued = queue_response.json()
    assert queued["brief_id"] == saved["brief_id"]
    assert queued["status"] == "queued"
    assert queued["channel"] == "markdown"
    assert queued["markdown"].startswith("# Qagent Daily Brief")
    assert list_response.status_code == 200
    assert list_response.json()["deliveries"][0]["delivery_id"] == queued["delivery_id"]
    assert sent_response.status_code == 200
    assert sent_response.json()["status"] == "sent"
    assert sent_response.json()["sent_at"] is not None


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
