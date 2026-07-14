from datetime import date
from decimal import Decimal

import pandas as pd
from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.api import routes


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
    duplicate_instrument = client.post(
        "/api/paper-trades/from-opportunity",
        json={**opportunity, "card_id": "card_test_0002"},
    )
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
    assert duplicate_instrument.status_code == 200
    assert duplicate_instrument.json()["created"] is False
    assert duplicate_instrument.json()["message"] == "already_tracking_instrument"
    assert duplicate_instrument.json()["trade"]["trade_id"] == created.json()["trade"]["trade_id"]
    assert blocked.status_code == 400
    assert listed.json()["summary"]["total"] == 1


def test_paper_trade_from_opportunity_rejects_recently_invalidated_price_data(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-invalidated-card.db'}",
    )
    client = TestClient(create_app())
    payload = {
        "card_id": "card_invalidated_0001",
        "provider": "free",
        "instrument_id": "CN:159516",
        "strategy_id": "trend_momentum_stage2",
        "trigger_price": "1.75",
        "initial_stop": "1.68",
        "target_1": "1.90",
        "rank_score": 0.82,
        "action": "watch_trigger",
        "risk_status": "clear",
    }
    created = client.post("/api/paper-trades/from-opportunity", json=payload)
    trade_id = created.json()["trade"]["trade_id"]
    routes._paper_repo().update_trade(
        trade_id,
        status="invalidated",
        exit_date=date.today(),
        latest_date=date.today(),
        latest_price=Decimal("0.85"),
        notes="推荐快照与首个盘中价格口径跳变超过 45%，样本作废并释放名额。",
    )

    response = client.post(
        "/api/paper-trades/from-opportunity",
        json={**payload, "card_id": "card_invalidated_0002"},
    )

    assert response.status_code == 400
    assert "price data was invalidated" in response.json()["detail"]


def test_paper_trade_from_opportunity_enforces_account_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-card-capacity.db'}",
    )
    client = TestClient(create_app())
    client.post(
        "/api/paper-trades/session/start",
        json={
            "label": "单仓测试",
            "reset_existing": True,
            "initial_capital": "100000",
            "allocation_per_trade_pct": "10",
            "max_positions": 1,
            "transaction_cost_bps": "5",
            "slippage_bps": "5",
            "take_profit_pct": "50",
        },
    )
    base = {
        "provider": "fixture",
        "strategy_id": "trend_momentum_stage2",
        "trigger_price": "12.00",
        "initial_stop": "11.40",
        "target_1": "13.20",
        "rank_score": 0.82,
        "action": "watch_trigger",
        "risk_status": "clear",
    }

    first = client.post(
        "/api/paper-trades/from-opportunity",
        json={**base, "card_id": "capacity_1", "instrument_id": "US:ONE"},
    )
    second = client.post(
        "/api/paper-trades/from-opportunity",
        json={**base, "card_id": "capacity_2", "instrument_id": "US:TWO"},
    )

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert second.status_code == 409
    assert second.json()["detail"] == "paper portfolio is full (1/1)"


def test_paper_candidate_pool_blocks_recently_invalidated_price_basis(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-invalidated-candidate.db'}",
    )
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    seeded = client.post("/api/paper-trades/seed?provider=fixture&limit=1")
    assert seeded.status_code == 200
    trade = client.get("/api/paper-trades?provider=fixture").json()["trades"][0]
    trigger = Decimal(trade["trigger_price"])
    routes._paper_repo().update_trade(
        trade["trade_id"],
        status="invalidated",
        exit_date=date.today(),
        latest_date=date.today(),
        latest_price=trigger * Decimal("0.40"),
        notes="推荐快照与首个盘中价格口径跳变超过 45%，样本作废并释放名额。",
    )

    response = client.get(
        "/api/paper-trades/candidate-pool?provider=fixture&include_etfs=true&limit=10"
    )

    assert response.status_code == 200
    item = next(
        candidate
        for candidate in response.json()["items"]
        if candidate["instrument_id"] == "US:TEST"
    )
    assert item["status"] == "blocked_by_data"
    assert item["price_basis_consistent"] is False
    assert item["action"] == "价格基准不一致"


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


def test_paper_trade_api_filters_by_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-provider-filter.db'}")
    client = TestClient(create_app())
    base = {
        "instrument_id": "CN:000001",
        "strategy_id": "trend_momentum",
        "trigger_price": "12.00",
        "initial_stop": "11.40",
        "target_1": "13.20",
        "rank_score": 0.82,
        "action": "watch_trigger",
        "risk_status": "clear",
    }
    client.post(
        "/api/paper-trades/from-opportunity",
        json={**base, "card_id": "card_filter_free", "provider": "free"},
    )
    client.post(
        "/api/paper-trades/from-opportunity",
        json={**base, "card_id": "card_filter_fixture", "provider": "fixture"},
    )

    listed = client.get("/api/paper-trades?provider=free")
    ledger = client.get("/api/paper-trades/ledger?provider=free")
    validation = client.get("/api/paper-trades/validation?provider=free")
    report = client.get("/api/paper-trades/daily-report?provider=free")

    assert listed.status_code == 200
    assert listed.json()["summary"]["total"] == 1
    assert listed.json()["trades"][0]["provider"] == "free"
    assert ledger.json()["summary"]["total_trades"] == 1
    assert validation.json()["summary"]["total_trades"] == 1
    assert report.json()["summary"]["total_trades"] == 1


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


def test_paper_trade_api_returns_daily_report(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-daily-report.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    client.post("/api/paper-trades/seed?provider=fixture&limit=5")
    client.post("/api/paper-trades/update?provider=fixture")

    response = client.get("/api/paper-trades/daily-report")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_trades"] == 1
    assert "next_trade_day_focus" in body
    assert body["data_health"]["paper_daily_report"] == "ready"


def test_paper_trade_daily_report_uses_cached_benchmarks_only(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-daily-report-cache.db'}"
    )
    client = TestClient(create_app())
    client.post(
        "/api/paper-trades/from-opportunity",
        json={
            "card_id": "card_report_cache_0001",
            "provider": "free",
            "instrument_id": "CN:000001",
            "strategy_id": "trend_momentum",
            "trigger_price": "12.00",
            "initial_stop": "11.40",
            "target_1": "13.20",
            "rank_score": 0.82,
            "action": "watch_trigger",
            "risk_status": "clear",
        },
    )

    def fail_live_provider(*_args, **_kwargs):
        raise AssertionError("daily report should not fetch live benchmark data")

    monkeypatch.setattr("qagent.api.routes.build_market_data_provider", fail_live_provider)

    response = client.get("/api/paper-trades/daily-report?provider=free")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_trades"] == 1
    assert body["benchmark"]["items"]
    assert body["data_health"]["paper_daily_benchmarks_source"] == "market_cache_only"


def test_paper_trade_daily_report_uses_cached_etf_proxy_benchmarks(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-daily-report-proxy.db'}"
    )
    client = TestClient(create_app())
    trade = routes._paper_repo().create_trade(
        source_snapshot_id="opportunity:card_report_proxy_0001",
        provider="free",
        instrument_id="CN:002747",
        strategy_id="trend_momentum",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("30.00"),
        initial_stop=Decimal("28.50"),
        target_1=Decimal("33.00"),
        rank_score=Decimal("0.82"),
        notes="test proxy benchmark trade",
    )
    routes._paper_repo().update_trade(
        trade.trade_id,
        latest_date=date(2026, 7, 6),
        latest_price=Decimal("30.60"),
    )
    routes._market_cache_repo().save_daily_bars(
        "free",
        pd.DataFrame(
            [
                {
                    "instrument_id": proxy_id,
                    "trade_date": trade_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000,
                    "provider": "proxy_fixture",
                }
                for proxy_id, first_close, last_close in [
                    ("CN:510300", 100, 102),
                    ("CN:510500", 100, 101),
                    ("CN:159915", 100, 98),
                    ("CN:588000", 100, 104),
                ]
                for trade_date, close in [
                    (date(2026, 7, 1), first_close),
                    (date(2026, 7, 6), last_close),
                ]
            ]
        ),
    )

    def fail_live_provider(*_args, **_kwargs):
        raise AssertionError("daily report should not fetch live benchmark data")

    monkeypatch.setattr("qagent.api.routes.build_market_data_provider", fail_live_provider)

    response = client.get("/api/paper-trades/daily-report?provider=free")

    assert response.status_code == 200
    body = response.json()
    assert body["data_health"]["paper_daily_benchmarks_source"] == "market_cache_only"
    assert body["data_health"]["paper_daily_benchmark_rows"] == "8"
    assert {item["name"] for item in body["benchmark"]["items"]} == {
        "沪深300",
        "中证500",
        "创业板指",
        "科创50",
    }
    assert all(item["return_pct"] is not None for item in body["benchmark"]["items"])


def test_paper_trade_daily_report_falls_back_to_recent_cached_benchmarks(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-daily-report-recent-proxy.db'}"
    )
    client = TestClient(create_app())
    trade = routes._paper_repo().create_trade(
        source_snapshot_id="opportunity:card_report_recent_proxy_0001",
        provider="free",
        instrument_id="CN:002747",
        strategy_id="trend_momentum",
        signal_date=date(2026, 7, 6),
        trigger_price=Decimal("30.00"),
        initial_stop=Decimal("28.50"),
        target_1=Decimal("33.00"),
        rank_score=Decimal("0.82"),
        notes="test recent proxy benchmark fallback",
    )
    routes._paper_repo().update_trade(
        trade.trade_id,
        latest_date=date(2026, 7, 6),
        latest_price=Decimal("30.60"),
    )
    routes._market_cache_repo().save_daily_bars(
        "free",
        pd.DataFrame(
            [
                {
                    "instrument_id": proxy_id,
                    "trade_date": trade_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000,
                    "provider": "proxy_fixture",
                }
                for proxy_id, first_close, last_close in [
                    ("CN:510300", 100, 102),
                    ("CN:510500", 100, 101),
                    ("CN:159915", 100, 98),
                    ("CN:588000", 100, 104),
                ]
                for trade_date, close in [
                    (date(2026, 6, 20), first_close),
                    (date(2026, 6, 26), last_close),
                ]
            ]
        ),
    )

    def fail_live_provider(*_args, **_kwargs):
        raise AssertionError("daily report should not fetch live benchmark data")

    monkeypatch.setattr("qagent.api.routes.build_market_data_provider", fail_live_provider)

    response = client.get("/api/paper-trades/daily-report?provider=free")

    assert response.status_code == 200
    body = response.json()
    assert body["data_health"]["paper_daily_benchmark_rows"] == "8"
    assert all(item["return_pct"] is not None for item in body["benchmark"]["items"])


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
    assert body["windows"][0]["evaluated_trades"] == 0
    assert body["windows"][1]["evaluated_trades"] == 0
    assert body["windows"][2]["evaluated_trades"] == 0
    assert body["items"][0]["instrument_id"] == "US:TEST"
    assert body["items"][0]["validation_state"] in {
        "closed",
        "open",
        "waiting_entry",
        "expired",
        "missed_entry",
    }
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
