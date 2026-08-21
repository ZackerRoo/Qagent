from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pandas as pd

from qagent.api import fuyao_routes
from qagent.app import create_app
from qagent.db import create_session_factory, initialize_database
from qagent.providers.fuyao import FuyaoProviderError
from qagent.storage.market_cache import MarketDataCacheRepository


class StubFuyaoClient:
    last_request = SimpleNamespace(
        request_id="fuyao-test-request",
        timestamp="2026-08-12T15:00:00+08:00",
    )

    def search_tickers(self, query, **kwargs):
        return {"item": [{"thscode": "600519.SH", "name": "贵州茅台"}], "query": query}

    def list_tickers(self, **kwargs):
        return {"item": [{"thscode": "600519.SH"}], **kwargs}

    def get_stock_market_page(self, **kwargs):
        return {"item": [{"thscode": "600519.SH"}], **kwargs}

    def get_trading_days(self):
        return {"item": [{"date": "2026-08-12"}]}

    def get_stock_snapshot_data(self, thscodes):
        return {"item": [{"thscode": thscodes[0], "last_price": 1500.0}]}

    def get_valuations(self, thscodes):
        return {"item": [{"thscode": thscodes[0], "pe_ttm": 24.5}]}

    def get_latest_financial_indicators(self, thscode, **kwargs):
        return {"thscode": thscode, "report": "2026-2", "abilities": []}

    def get_financial_indicators(self, thscode, report):
        return {"thscode": thscode, "report": report, "abilities": []}

    def get_corporate_actions(self, thscode, **kwargs):
        return {"thscode": thscode, "item": []}

    def get_hot_stock_rank_trend(self, thscode, **kwargs):
        return {"item": [{"thscode": thscode, "rank": 12}]}

    def get_anomaly_analysis(self, **kwargs):
        raise FuyaoProviderError("temporary upstream error", code=5003, request_id="req-1")

    def get_financial_statements(self, thscode, statement, **kwargs):
        return {"thscode": thscode, "statement": statement, "item": []}

    def get_limit_up_pool(self, **kwargs):
        return {"item": [{"thscode": "000001.SZ"}]}

    def get_limit_up_ladder(self):
        return {"item": []}

    def get_hot_stock_list(self, **kwargs):
        return {"item": []}

    def get_hot_stock_history(self, trade_date):
        return {"date": trade_date.isoformat(), "item": []}

    def get_skyrocket_list(self, **kwargs):
        return {"item": []}

    def get_dragon_tiger(self, **kwargs):
        return {"stock_items": []}

    def get_index_catalog(self, tag):
        return {"tag": tag, "item": []}

    def get_index_snapshot_data(self, thscodes):
        return {"item": [{"thscode": thscodes[0]}]}

    def get_index_constituents(self, thscode):
        return {"thscode": thscode, "item": []}

    def get_index_history_data(self, thscode, start, end):
        return {"thscode": thscode, "item": []}

    def get_fund_profile(self, thscode):
        return {"item": [{"thscode": thscode, "fund_name": "沪深300ETF"}]}

    def get_fund_holdings(self, thscode):
        return {"item": [{"thscode": "600519.SH", "hold_ratio": 4.0}]}

    def get_fund_snapshot_data(self, thscode):
        return {"item": [{"thscode": thscode, "last_price": 4.2}]}

    def get_fund_holders(self, thscode):
        return {"item": []}

    def get_fund_nav(self, thscode):
        return {"item": []}

    def get_fund_returns(self, thscode):
        return {"item": []}

    def get_fund_history_data(self, thscode, start, end):
        return {"item": []}


class UnavailableFuyaoClient:
    telemetry_snapshot = None
    last_request = None

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise FuyaoProviderError(
                f"{name} unavailable",
                code="upstream_unavailable",
                request_id="req-down",
            )

        return fail


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'fuyao-research-api.db'}",
    )
    monkeypatch.setattr(fuyao_routes, "_configured_client", lambda: StubFuyaoClient())
    return TestClient(create_app())


def test_fuyao_capability_api_does_not_require_a_key(monkeypatch):
    monkeypatch.setattr(
        fuyao_routes,
        "get_settings",
        lambda: SimpleNamespace(fuyao_api_key=None),
    )

    response = TestClient(create_app()).get("/api/fuyao/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["minute_bars_supported"] is False


def test_fuyao_stock_research_is_partial_and_has_no_decision_side_effect(monkeypatch, tmp_path):
    response = _client(monkeypatch, tmp_path).get(
        "/api/fuyao/research/stock",
        params={"instrument_id": "CN:600519", "include_statements": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["classification"] == "research_only"
    assert body["decision_weight_applied"] is False
    assert body["paper_order_side_effect"] is False
    assert body["status"] == "partial"
    assert body["identity"]["thscode"] == "600519.SH"
    assert "valuation" in body["sections"]
    assert "income_statement" in body["sections"]
    assert body["freshness"] == "live"
    assert body["snapshot"]["persisted"] is True
    assert {
        metric["key"] for metric in body["summary"]["metrics"]
    } >= {"latest_price", "pe_ttm", "hot_rank"}
    assert body["errors"] == [
        {
            "section": "anomaly_analysis",
            "code": 5003,
            "message": "temporary upstream error",
            "request_id": "req-1",
        }
    ]


def test_fuyao_stock_research_rejects_etf_and_index_ids(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    etf = client.get("/api/fuyao/research/stock?instrument_id=CN:510300")
    index = client.get("/api/fuyao/research/stock?instrument_id=CN:000300.IDX")

    assert etf.status_code == 422
    assert "fund research" in etf.json()["detail"]
    assert index.status_code == 422
    assert "does not accept index" in index.json()["detail"]


def test_fuyao_market_research_reuses_complete_snapshot_unless_refreshed(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    reuse_values: list[bool] = []

    def capture_stub(*args, **kwargs):
        reuse_values.append(bool(kwargs["reuse_existing"]))
        return SimpleNamespace(response={"reuse_existing": kwargs["reuse_existing"]})

    monkeypatch.setattr(fuyao_routes, "capture_fuyao_market_research", capture_stub)

    reused = client.get("/api/fuyao/research/market?trade_date=2026-08-12")
    refreshed = client.get(
        "/api/fuyao/research/market?trade_date=2026-08-12&refresh=true"
    )

    assert reused.status_code == 200
    assert reused.json()["reuse_existing"] is True
    assert refreshed.status_code == 200
    assert refreshed.json()["reuse_existing"] is False
    assert reuse_values == [True, False]


def test_fuyao_theme_research_reuses_daily_snapshot_unless_refreshed(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    reuse_values: list[bool] = []

    def capture_stub(*args, **kwargs):
        reuse_values.append(bool(kwargs["reuse_existing"]))
        return SimpleNamespace(response={"reuse_existing": kwargs["reuse_existing"]})

    monkeypatch.setattr(fuyao_routes, "capture_fuyao_theme_strength", capture_stub)

    reused = client.get("/api/fuyao/research/themes?trade_date=2026-08-12")
    refreshed = client.get("/api/fuyao/research/themes?trade_date=2026-08-12&refresh=true")

    assert reused.status_code == 200
    assert reused.json()["reuse_existing"] is True
    assert refreshed.status_code == 200
    assert refreshed.json()["reuse_existing"] is False
    assert reuse_values == [True, False]


def test_fuyao_market_and_fund_research_endpoints_preserve_raw_sections(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    market = client.get("/api/fuyao/research/market?period=hour")
    fund = client.get("/api/fuyao/research/fund?instrument_id=CN:510300")

    assert market.status_code == 200
    assert market.json()["status"] == "partial"
    assert market.json()["sections"]["limit_up_pool"]["item"][0]["thscode"] == "000001.SZ"
    assert market.json()["sections"]["derived_sentiment"]["classification"] == "research_only"
    assert market.json()["decision_weight_applied"] is False
    assert market.json()["paper_order_side_effect"] is False
    assert market.json()["snapshot"]["persisted"] is True
    latest = client.get("/api/fuyao/research/market/latest")
    latest_raw = client.get("/api/fuyao/research/market/latest?include_raw=true")
    evaluation = client.get("/api/fuyao/research/market/shadow-evaluation")
    assert latest.status_code == 200
    assert latest.json()["snapshot"]["snapshot_id"] == market.json()["snapshot"]["snapshot_id"]
    assert latest.json()["raw_sections_included"] is False
    assert set(latest.json()["sections"]) == {"derived_sentiment"}
    assert "limit_up_pool" in latest.json()["raw_sections_available"]
    assert latest_raw.status_code == 200
    assert "limit_up_pool" in latest_raw.json()["sections"]
    assert evaluation.status_code == 200
    assert evaluation.json()["evaluation"]["snapshot_count"] == 1
    assert evaluation.json()["evaluation"]["decision_weight_applied"] is False
    assert fund.status_code == 200
    assert fund.json()["status"] == "ready"
    assert fund.json()["sections"]["holdings"]["item"][0]["hold_ratio"] == 4.0
    assert {
        metric["key"] for metric in fund.json()["summary"]["metrics"]
    } >= {"latest_price", "holdings_count", "top_holding_ratio"}


def test_fuyao_stock_research_reconciles_same_session_local_close(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    initialize_database()
    MarketDataCacheRepository(create_session_factory()).save_daily_bars(
        "free",
        pd.DataFrame(
            [
                {
                    "instrument_id": "CN:600519",
                    "trade_date": date(2026, 8, 12),
                    "open": 1490.0,
                    "high": 1510.0,
                    "low": 1480.0,
                    "close": 1500.0,
                    "volume": 1000,
                    "turnover": 1_500_000,
                    "provider": "fixture",
                }
            ]
        ),
    )

    response = client.get("/api/fuyao/research/stock?instrument_id=CN:600519")

    assert response.status_code == 200
    quality = response.json()["quality_comparison"]
    assert quality["state"] == "aligned"
    assert quality["same_session"] is True
    assert quality["difference_pct"] == 0.0
    assert quality["classification"] == "research_only"


def test_fuyao_research_falls_back_to_latest_persisted_snapshot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = client.get("/api/fuyao/research/stock?instrument_id=CN:600519")
    snapshot_id = first.json()["snapshot"]["snapshot_id"]
    monkeypatch.setattr(
        fuyao_routes,
        "_configured_client",
        lambda: UnavailableFuyaoClient(),
    )

    fallback = client.get("/api/fuyao/research/stock?instrument_id=CN:600519")

    assert fallback.status_code == 200
    body = fallback.json()
    assert body["status"] == "stale"
    assert body["freshness"] == "stored_fallback"
    assert body["summary"]["metrics"]
    assert body["snapshot"]["snapshot_id"] == snapshot_id
    assert body["errors"]


def test_fuyao_catalog_calendar_and_paginated_snapshot_endpoints(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    tickers = client.get("/api/fuyao/tickers?asset_type=stock&limit=20&offset=40")
    calendar = client.get("/api/fuyao/market/trading-calendar")
    market = client.get("/api/fuyao/market/snapshot-page?limit=100&offset=200")

    assert tickers.status_code == 200
    assert tickers.json()["sections"]["catalog"]["offset"] == 40
    assert calendar.status_code == 200
    assert calendar.json()["sections"]["calendar"]["item"][0]["date"] == "2026-08-12"
    assert market.status_code == 200
    assert market.json()["sections"]["snapshot"]["offset"] == 200


def test_fuyao_historical_hot_list_requires_explicit_date(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    missing = client.get(
        "/api/fuyao/research/market?include_historical_hot_list=true"
    )
    ready = client.get(
        "/api/fuyao/research/market",
        params={"include_historical_hot_list": True, "trade_date": "2026-08-12"},
    )

    assert missing.status_code == 422
    assert "trade_date is required" in missing.json()["detail"]
    assert ready.status_code == 200
    assert ready.json()["sections"]["hot_stock_history"]["date"] == "2026-08-12"
