import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.api import routes


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
    assert "sector_strength" in body
    assert "portfolio_plan" in body
    assert body["factor_rankings"]
    assert body["sector_strength"]
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
    assert body["cards"][0]["asset_type"]
    assert body["cards"][0]["opportunity_bucket"]
    assert isinstance(body["cards"][0]["opportunity_tags"], list)
    assert "rotation_note" in body["cards"][0]
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
    assert body["cards"][0]["strategy_calibration"]["strategy_id"]
    assert body["cards"][0]["tradability"]["label"]
    assert body["portfolio_plan"]["max_positions"] >= 1
    assert "不可交易" in "；".join(body["portfolio_plan"]["rules"])
    assert body["items"][0]["instrument_id"]
    assert body["items"][0]["factor_score"] is not None
    assert body["items"][0]["factor_rank"] is not None
    assert "trading_status" in body["items"][0]
    assert "tradability" in body["items"][0]
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
    assert card["trading_status"]["status"] == "limit_up"
    assert card["trading_status"]["can_buy"] is False
    assert "不建议追买" in card["trading_status"]["notes"][0]
    assert card["tradability"]["can_open"] is False
    assert card["strategy_calibration"]["readiness"]
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


def test_today_scan_task_endpoint_returns_pollable_result(monkeypatch):
    def fake_full_market_scan_payload(provider, max_symbols, include_etfs, sync_if_empty):
        return {
            "symbols": ["CN:000001"],
            "cards": [],
            "items": [],
            "strategy_health": [],
            "factor_rankings": [],
            "sector_strength": [],
            "portfolio_plan": {},
            "data_health": {
                "provider": provider,
                "full_market_requested": str(max_symbols),
                "full_market_include_etfs": str(include_etfs).lower(),
                "sync_if_empty": str(sync_if_empty).lower(),
            },
        }

    monkeypatch.setattr(routes, "_full_market_scan_payload", fake_full_market_scan_payload)
    client = TestClient(create_app())

    response = client.post("/api/scan-tasks/today?provider=free&max_symbols=1&include_etfs=true")

    assert response.status_code == 200
    task_id = response.json()["task_id"]
    detail = None
    for _ in range(20):
        poll_response = client.get(f"/api/scan-tasks/{task_id}")
        assert poll_response.status_code == 200
        detail = poll_response.json()
        if detail["status"] == "succeeded":
            break
        time.sleep(0.05)

    assert detail is not None
    assert detail["status"] == "succeeded"
    assert detail["result"]["symbols"] == ["CN:000001"]
    assert detail["result"]["data_health"]["full_market_requested"] == "1"


def test_today_scan_task_returns_recent_sqlite_cache_without_recompute(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'today-cache.db'}")
    cached_payload = {
        "symbols": ["CN:000001"],
        "cards": [],
        "items": [],
        "strategy_health": [],
        "factor_rankings": [],
        "sector_strength": [],
        "portfolio_plan": {"profile": "balanced"},
        "data_health": {
            "provider": "free",
            "full_market_requested": "30",
            "full_market_include_etfs": "true",
        },
    }
    routes._repo().save_scan_result_cache(
        cache_key="today_scan:free:30:true:true",
        provider="free",
        mode="today_scan",
        symbols=["CN:000001"],
        payload=cached_payload,
    )
    recompute_calls = []

    def unexpected_full_market_scan_payload(provider, max_symbols, include_etfs, sync_if_empty):
        recompute_calls.append((provider, max_symbols, include_etfs, sync_if_empty))
        return cached_payload

    monkeypatch.setattr(routes, "_full_market_scan_payload", unexpected_full_market_scan_payload)
    client = TestClient(create_app())

    response = client.post(
        "/api/scan-tasks/today"
        "?provider=free&max_symbols=30&include_etfs=true&cache_ttl_minutes=60"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["progress"] == 100
    assert body["result"]["symbols"] == ["CN:000001"]
    assert body["result"]["data_health"]["scan_result_cache"] == "hit"
    assert body["result"]["data_health"]["scan_result_cache_key"] == "today_scan:free:30:true:true"
    assert recompute_calls == []


def test_today_scan_task_reuses_recent_scan_run_when_result_cache_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'today-run-fallback.db'}")
    from qagent.jobs.daily_scan import run_daily_scan
    from qagent.providers.fixtures import FixtureMarketDataProvider

    scan = run_daily_scan(["CN:000001"], FixtureMarketDataProvider())
    routes._repo().save_scan_run(
        provider="free",
        mode="free",
        symbols=["CN:000001"],
        result=scan,
    )
    recompute_calls = []

    def unexpected_full_market_scan_payload(provider, max_symbols, include_etfs, sync_if_empty):
        recompute_calls.append((provider, max_symbols, include_etfs, sync_if_empty))
        return {}

    monkeypatch.setattr(routes, "_full_market_scan_payload", unexpected_full_market_scan_payload)
    client = TestClient(create_app())

    response = client.post(
        "/api/scan-tasks/today"
        "?provider=free&max_symbols=1&include_etfs=true&cache_ttl_minutes=60"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["symbols"] == ["CN:000001"]
    assert body["result"]["cards"][0]["instrument_id"] == "CN:000001"
    assert body["result"]["data_health"]["scan_result_cache"] == "scan_run_fallback"
    assert recompute_calls == []


def test_full_market_batch_scan_endpoint_creates_background_job(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'full-batch-api.db'}")
    routes._repo().replace_tradable_instruments(
        [
            SimpleNamespace(
                instrument_id="CN:000001",
                symbol="000001",
                name="平安银行",
                label="平安银行 000001.SZ",
                asset_type="stock",
                exchange="SZ",
                source="test",
            ),
            SimpleNamespace(
                instrument_id="CN:000002",
                symbol="000002",
                name="万科A",
                label="万科A 000002.SZ",
                asset_type="stock",
                exchange="SZ",
                source="test",
            ),
            SimpleNamespace(
                instrument_id="CN:159001",
                symbol="159001",
                name="货币ETF",
                label="货币ETF 159001.SZ",
                asset_type="etf",
                exchange="SZ",
                source="test",
            ),
        ]
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_task_executor", FakeExecutor())
    client = TestClient(create_app())

    response = client.post(
        "/api/full-market/batch-scan"
        "?provider=free&batch_size=2&max_symbols=3&include_etfs=true"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["total_symbols"] == 3
    assert body["batch_size"] == 2
    assert body["total_batches"] == 2
    assert body["progress"] == 0
    assert submitted

    detail = client.get(f"/api/full-market/batch-scan/{body['job_id']}")
    latest = client.get("/api/full-market/batch-scan/latest?provider=free")

    assert detail.status_code == 200
    assert detail.json()["job_id"] == body["job_id"]
    assert latest.status_code == 200
    assert latest.json()["job_id"] == body["job_id"]


def test_daily_brief_fast_mode_sets_snapshot_controls():
    client = TestClient(create_app())

    response = client.get("/api/daily-brief?provider=fixture&fast=true&limit=4&include_news=false")

    assert response.status_code == 200
    body = response.json()
    assert body["data_health"]["brief_mode"] == "fast"
    assert body["data_health"]["brief_backtest"] == "skipped"
    assert body["data_health"]["brief_skip_backtest"] == "true"
    assert body["data_health"]["brief_news"] == "skipped"
    assert len(body["top_opportunities"]) <= 4


def test_daily_brief_full_mode_can_skip_backtest():
    client = TestClient(create_app())

    response = client.get("/api/daily-brief?provider=fixture&fast=false&skip_backtest=true&limit=3")

    assert response.status_code == 200
    body = response.json()
    assert body["data_health"]["brief_mode"] == "full"
    assert body["data_health"]["brief_backtest"] == "skipped"
    assert body["data_health"]["brief_skip_backtest"] == "true"
    assert len(body["top_opportunities"]) <= 3


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
