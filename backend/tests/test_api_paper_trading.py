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


def test_paper_trade_api_deletes_trade(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-delete.db'}")
    client = TestClient(create_app())
    opportunity = {
        "card_id": "card_delete_0001",
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
    trade_id = created.json()["trade"]["trade_id"]
    deleted = client.delete(f"/api/paper-trades/{trade_id}")
    listed = client.get("/api/paper-trades")
    deleted_again = client.delete(f"/api/paper-trades/{trade_id}")

    assert created.status_code == 200
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["trade_id"] == trade_id
    assert listed.json()["summary"]["total"] == 0
    assert deleted_again.status_code == 404


def test_paper_trade_session_start_resets_records_and_saves_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-session.db'}")
    client = TestClient(create_app())
    client.post(
        "/api/paper-trades/from-opportunity",
        json={
            "card_id": "card_session_0001",
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

    started = client.post(
        "/api/paper-trades/session/start",
        json={
            "label": "A股正式模拟盘",
            "reset_existing": True,
            "initial_capital": "100000",
            "allocation_per_trade_pct": "10",
            "max_positions": 5,
            "transaction_cost_bps": "5",
            "slippage_bps": "5",
            "take_profit_pct": "50",
        },
    )
    listed = client.get("/api/paper-trades")
    session = client.get("/api/paper-trades/session")
    ledger = client.get("/api/paper-trades/ledger")

    assert started.status_code == 200
    body = started.json()
    assert body["cleared_trades"] == 1
    assert body["account"]["label"] == "A股正式模拟盘"
    assert body["account"]["status"] == "active"
    assert body["account"]["initial_capital"] == "100000.0000"
    assert body["account"]["max_positions"] == 5
    assert body["account"]["transaction_cost_bps"] == "5.0000"
    assert body["ledger"]["summary"]["max_positions"] == 5
    assert body["ledger"]["summary"]["transaction_cost_bps"] == 5.0
    assert listed.json()["summary"]["total"] == 0
    assert session.json()["account"]["label"] == "A股正式模拟盘"
    assert ledger.json()["summary"]["take_profit_pct"] == 50.0
    assert ledger.json()["summary"]["max_positions"] == 5
    assert ledger.json()["data_health"]["paper_session_status"] == "active"


def test_paper_trade_api_returns_ledger_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-ledger.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    client.post("/api/paper-trades/seed?provider=fixture&limit=5")
    client.post("/api/paper-trades/update?provider=fixture")

    response = client.get("/api/paper-trades/ledger?initial_capital=100000")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_trades"] == 1
    assert body["summary"]["closed_trades"] == 1
    assert body["summary"]["total_equity"] == "99855.77"
    assert body["curve"]
    assert body["items"][0]["outcome"] == "止损离场"
    assert "transactions" in body
    assert "positions" in body


def test_paper_trade_auto_validation_reports_5_10_20_day_outcomes(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-validation.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    client.post("/api/paper-trades/seed?provider=fixture&limit=5")

    response = client.post("/api/paper-trades/validation/run?provider=fixture")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_trades"] == 1
    assert body["summary"]["closed_trades"] == 1
    assert body["summary"]["primary_window_days"] == 20
    assert body["summary"]["verdict"] in {"profitable", "risk", "building_sample", "no_data"}
    assert [window["window_days"] for window in body["windows"]] == [5, 10, 20]
    assert body["windows"][0]["evaluated_trades"] == 1
    assert body["items"][0]["instrument_id"] == "US:TEST"
    assert body["items"][0]["validation_state"] in {"closed", "open", "waiting_entry", "expired"}
    assert body["curve"]
    assert body["sample_age"]["average_days_since_signal"] >= 0
    assert body["sample_age"]["mature_5d"] >= 0
    assert body["sample_age"]["mature_10d"] >= 0
    assert body["sample_age"]["mature_20d"] >= 0
    assert body["batches"]
    assert body["batches"][0]["batch_date"]
    assert body["batches"][0]["total_trades"] == 1
    assert [window["window_days"] for window in body["batches"][0]["windows"]] == [5, 10, 20]
    assert body["credibility"]["level"] in {"high", "medium", "low", "insufficient"}
    assert body["credibility"]["score"] >= 0
    assert body["credibility"]["summary"]
    assert body["data_health"]["validation_windows"] == "5,10,20"


def test_paper_trade_api_returns_flow_ledger_with_costs(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-flow-ledger.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    client.post("/api/paper-trades/seed?provider=fixture&limit=5")
    client.post("/api/paper-trades/update?provider=fixture")

    response = client.get(
        "/api/paper-trades/ledger"
        "?initial_capital=100000&transaction_cost_bps=3&slippage_bps=5&take_profit_pct=50"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_fees"] != "0.00"
    assert body["summary"]["total_slippage"] != "0.00"
    assert body["summary"]["turnover"] != "0.00"
    assert body["transactions"][0]["action"] == "entry_buy"
    assert body["transactions"][0]["cash_flow"].startswith("-")


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
