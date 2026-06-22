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


def test_portfolio_api_returns_position_risk(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-portfolio.db'}")
    client = TestClient(create_app())
    client.post(
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

    response = client.get("/api/portfolio?provider=fixture")

    assert response.status_code == 200
    body = response.json()
    assert body["positions"][0]["instrument_id"] == "US:TEST"
    assert body["risk"][0]["instrument_id"] == "US:TEST"
    assert body["risk"][0]["current_price"] == "82.00"
    assert body["risk"][0]["status"] == "inside_plan"


def test_opportunities_api_records_scan_history_and_outcomes(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-history.db'}")
    client = TestClient(create_app())

    scan_response = client.get("/api/opportunities?provider=fixture")

    assert scan_response.status_code == 200
    runs_response = client.get("/api/scan-runs")
    history_response = client.get("/api/opportunity-history")
    outcomes_response = client.get("/api/outcomes?provider=fixture")
    performance_response = client.get("/api/strategy-performance?provider=fixture")
    assert runs_response.status_code == 200
    assert history_response.status_code == 200
    assert outcomes_response.status_code == 200
    assert performance_response.status_code == 200
    runs = runs_response.json()["runs"]
    snapshots = history_response.json()["snapshots"]
    outcomes = outcomes_response.json()["outcomes"]
    assert len(runs) == 1
    assert runs[0]["provider"] == "fixture"
    assert runs[0]["cards"] == len(scan_response.json()["cards"])
    assert snapshots
    assert snapshots[0]["instrument_id"] in {"US:TEST", "CN:000001"}
    assert snapshots[0]["card"]["instrument_id"] == snapshots[0]["instrument_id"]
    assert outcomes
    assert outcomes[0]["outcome_status"] in {
        "pending",
        "working",
        "lagging",
        "target_1_hit",
        "stopped",
    }
    assert outcomes_response.json()["data_health"]["snapshots"] == str(len(snapshots))
    performance = performance_response.json()["performance"]
    assert performance
    assert all(item["strategy_id"] for item in performance)


def test_opportunity_history_api_filters_by_instrument(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-history-filter.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture")

    response = client.get("/api/opportunity-history?instrument_id=US:TEST")

    assert response.status_code == 200
    snapshots = response.json()["snapshots"]
    assert snapshots
    assert {snapshot["instrument_id"] for snapshot in snapshots} == {"US:TEST"}


def test_backtest_api_returns_fixture_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-backtest.db'}")
    client = TestClient(create_app())

    response = client.get(
        "/api/backtest?provider=fixture&symbols=US:TEST,CN:000001"
        "&start=2026-01-30&end=2026-03-20&step_days=5"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["provider"] == "fixture"
    assert body["summary"]["scan_count"] > 0
    assert body["summary"]["evaluated_signals"] > 0
    assert body["performance"]
    assert body["signals"]
    assert body["data_health"]["lookahead_guard"] == "bars_limited_to_scan_date"


def test_portfolio_backtest_api_returns_account_level_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-portfolio-backtest.db'}")
    client = TestClient(create_app())

    response = client.get(
        "/api/portfolio-backtest?provider=fixture&symbols=US:TEST,CN:000001"
        "&start=2026-01-30&end=2026-03-20&step_days=5"
        "&initial_capital=100000&risk_per_trade_pct=1&max_positions=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["provider"] == "fixture"
    assert body["summary"]["initial_capital"] == "100000"
    assert body["summary"]["trade_count"] > 0
    assert body["summary"]["final_equity"] != "100000"
    assert body["summary"]["max_drawdown_pct"] is not None
    assert body["trades"]
    assert body["equity_curve"][0]["equity"] == "100000"
    assert body["data_health"]["lookahead_guard"] == "signals_generated_before_exits"


def test_backtest_api_rejects_reversed_date_range(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-backtest-invalid.db'}")
    client = TestClient(create_app())

    response = client.get("/api/backtest?provider=fixture&start=2026-03-20&end=2026-01-30")

    assert response.status_code == 400
    assert "start" in response.json()["detail"]
