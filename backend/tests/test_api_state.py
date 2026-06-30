import json
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.db import create_session_factory, initialize_database
from qagent.storage.tables import OpportunitySnapshotRow, ScanRunRow


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
    assert body["risk"][0]["action"] == "hold"
    assert body["risk"][0]["management_note"]


def test_agent_answers_position_management_question(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-position-agent.db'}")
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

    response = client.post(
        "/api/agent/query",
        json={
            "question": "我买了这个现在要不要卖？",
            "provider": "fixture",
            "symbols": "US:TEST",
            "instrument_id": "US:TEST",
        },
    )

    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "US:TEST" in answer or "Test Growth" in answer
    assert "持有" in answer or "止损" in answer or "目标" in answer


def test_opportunities_api_records_scan_history_and_outcomes(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-history.db'}")
    client = TestClient(create_app())

    scan_response = client.get("/api/opportunities?provider=fixture")

    assert scan_response.status_code == 200
    scan_payload = scan_response.json()
    intelligence = scan_payload["market_intelligence"]
    assert intelligence["data_quality"]["summary"]
    assert intelligence["market_environment"]["regime"]
    assert intelligence["strategy_scheduler"]["weights"]
    assert intelligence["recommendation_calibration"]["rules_applied"]
    assert intelligence["event_hypotheses"]["summary"]
    action_center = scan_payload["manual_action_center"]
    assert action_center["headline"]
    assert action_center["today_actions"]
    assert action_center["alert_loop"]
    assert action_center["data_source_roadmap"]
    assert action_center["strategy_effectiveness"]
    assert scan_payload["cards"][0]["dynamic_score"] is not None
    assert scan_payload["cards"][0]["calibration_notes"]
    runs_response = client.get("/api/scan-runs")
    history_response = client.get("/api/opportunity-history")
    outcomes_response = client.get("/api/outcomes?provider=fixture")
    performance_response = client.get("/api/strategy-performance?provider=fixture")
    diagnostics_response = client.get("/api/strategy-diagnostics?provider=fixture")
    assert runs_response.status_code == 200
    assert history_response.status_code == 200
    assert outcomes_response.status_code == 200
    assert performance_response.status_code == 200
    assert diagnostics_response.status_code == 200
    runs = runs_response.json()["runs"]
    snapshots = history_response.json()["snapshots"]
    outcomes = outcomes_response.json()["outcomes"]
    assert len(runs) == 1
    assert runs[0]["provider"] == "fixture"
    assert runs[0]["cards"] == len(scan_payload["cards"])
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
    diagnostics = diagnostics_response.json()["diagnostics"]
    assert diagnostics
    assert diagnostics[0]["verdict"] in {"effective", "watch", "weak", "insufficient_sample"}
    assert diagnostics[0]["reason"]
    assert diagnostics_response.json()["data_health"]["diagnostics"] == str(len(diagnostics))


def test_recommendation_closure_api_summarizes_seeded_snapshots(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'api-closure.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            ScanRunRow(
                run_id="scan-closure",
                provider="fixture",
                mode="test",
                symbols=json.dumps(["CN:000001"]),
                scanned=1,
                cards=2,
                data_health="{}",
                created_at=now,
            )
        )
        for index, signal_date in enumerate([date(2026, 1, 30), date(2026, 2, 2)], start=1):
            session.add(
                OpportunitySnapshotRow(
                    snapshot_id=f"scan-closure:card-{index}",
                    run_id="scan-closure",
                    card_id=f"card-{index}",
                    instrument_id="CN:000001",
                    market="CN",
                    status="setup_ready",
                    signal_date=signal_date,
                    latest_close=Decimal("10.60"),
                    primary_strategy_id="breakout_volume_confirmation",
                    score=Decimal("0.80"),
                    strategy_score=Decimal("0.82"),
                    rank_score=Decimal("0.78"),
                    trigger_price=Decimal("10.60"),
                    initial_stop=Decimal("10.00"),
                    target_1=Decimal("11.00"),
                    card_json=json.dumps(
                        {
                            "instrument_id": "CN:000001",
                            "instrument_label": "平安银行 000001.SZ",
                        },
                        sort_keys=True,
                    ),
                    created_at=now,
                )
            )
        session.commit()

    client = TestClient(create_app())
    response = client.get("/api/recommendation-closure?provider=fixture&limit=10")

    assert response.status_code == 200
    closure = response.json()
    assert [item["window_days"] for item in closure["windows"]] == [30, 60, 90]
    assert closure["windows"][0]["sample_count"] == 2
    assert closure["windows"][0]["completed_count"] == 2
    assert closure["windows"][0]["triggered_count"] == 2
    assert closure["windows"][0]["trigger_rate"] == 1
    assert closure["latest_outcomes"]
    assert closure["latest_outcomes"][0]["instrument_label"]
    assert "triggered" in closure["latest_outcomes"][0]
    assert len(closure["completed_outcomes"]) == 2
    assert closure["completed_outcomes"][0]["return_10d"] is not None
    assert closure["data_health"]["closure_windows"] == "30,60,90"


def test_recommendation_followthrough_api_returns_user_facing_health_center(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'api-followthrough.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            ScanRunRow(
                run_id="scan-followthrough",
                provider="fixture",
                mode="test",
                symbols=json.dumps(["CN:000001"]),
                scanned=1,
                cards=3,
                data_health="{}",
                created_at=now,
            )
        )
        for index, signal_date in enumerate(
            [date(2026, 1, 27), date(2026, 1, 30), date(2026, 2, 2)],
            start=1,
        ):
            session.add(
                OpportunitySnapshotRow(
                    snapshot_id=f"scan-followthrough:card-{index}",
                    run_id="scan-followthrough",
                    card_id=f"card-{index}",
                    instrument_id="CN:000001",
                    market="CN",
                    status="setup_ready",
                    signal_date=signal_date,
                    latest_close=Decimal("10.60"),
                    primary_strategy_id="breakout_volume_confirmation",
                    score=Decimal("0.80"),
                    strategy_score=Decimal("0.82"),
                    rank_score=Decimal("0.78"),
                    trigger_price=Decimal("10.60"),
                    initial_stop=Decimal("10.00"),
                    target_1=Decimal("11.00"),
                    card_json=json.dumps(
                        {
                            "instrument_id": "CN:000001",
                            "instrument_label": "平安银行 000001.SZ",
                        },
                        sort_keys=True,
                    ),
                    created_at=now,
                )
            )
        session.commit()

    client = TestClient(create_app())
    response = client.get("/api/recommendation-followthrough?provider=fixture&limit=10")

    assert response.status_code == 200
    center = response.json()
    assert center["headline"]
    assert center["verdict"] in {"表现健康", "需要观察", "需要降权", "样本不足"}
    assert 0 <= center["health_score"] <= 1
    assert [item["window_days"] for item in center["windows"]] == [30, 60, 90]
    assert "expectancy_10d" in center["windows"][0]
    assert "profit_factor_10d" in center["windows"][0]
    assert "max_consecutive_losses" in center["windows"][0]
    assert center["windows"][0]["risk_verdict"] in {"健康", "观察", "降权", "样本不足"}
    assert center["focus_outcomes"]
    first = center["focus_outcomes"][0]
    assert first["instrument_label"] == "平安银行 000001.SZ"
    assert first["action"]
    assert first["reason"]
    assert center["action_items"]
    assert center["data_health"]["followthrough_windows"] == "30,60,90"


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
    assert body["benchmark"]["label"] == "Equal-weight scanned universe"
    assert body["benchmark"]["excess_return_10d"] is not None
    assert body["environment_breakdown"]
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
