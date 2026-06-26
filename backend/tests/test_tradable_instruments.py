from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.market.tradable import load_cn_tradable_instruments, search_cn_tradable_instruments
from qagent.market.instruments import format_instrument_label
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.full_market import build_full_market_symbols, sync_cn_tradable_catalog
from qagent.storage.repository import QagentRepository


def test_load_cn_tradable_instruments_combines_a_shares_and_etfs(monkeypatch):
    def fake_stocks():
        return pd.DataFrame({"code": ["000001", "688981"], "name": ["平安银行", "中芯国际"]})

    def fake_etfs():
        return pd.DataFrame({"代码": ["588000", "510300"], "名称": ["科创50ETF", "沪深300ETF"]})

    monkeypatch.setattr(
        "qagent.market.tradable.ak",
        SimpleNamespace(stock_info_a_code_name=fake_stocks, fund_etf_spot_em=fake_etfs),
    )

    catalog = load_cn_tradable_instruments()

    assert [item.instrument_id for item in catalog.items] == [
        "CN:000001",
        "CN:688981",
        "CN:588000",
        "CN:510300",
    ]
    assert catalog.data_health["tradable_a_shares"] == "2"
    assert catalog.data_health["tradable_etfs"] == "2"
    assert format_instrument_label("CN:688981") == "中芯国际 688981.SH"


def test_search_cn_tradable_instruments_matches_name_code_and_label(monkeypatch):
    def fake_stocks():
        return pd.DataFrame(
            {
                "code": ["000001", "688981", "300730"],
                "name": ["平安银行", "中芯国际", "科创信息"],
            }
        )

    def fake_etfs():
        return pd.DataFrame({"代码": ["588000"], "名称": ["科创50ETF"]})

    monkeypatch.setattr(
        "qagent.market.tradable.ak",
        SimpleNamespace(stock_info_a_code_name=fake_stocks, fund_etf_spot_em=fake_etfs),
    )

    by_name = search_cn_tradable_instruments("中芯", limit=10)
    by_code = search_cn_tradable_instruments("588", limit=10)
    by_exchange_label = search_cn_tradable_instruments("000001.SZ", limit=10)
    by_theme = search_cn_tradable_instruments("科创", limit=2)

    assert [item.instrument_id for item in by_name.items] == ["CN:688981"]
    assert [item.instrument_id for item in by_code.items] == ["CN:588000"]
    assert [item.instrument_id for item in by_exchange_label.items] == ["CN:000001"]
    assert [item.instrument_id for item in by_theme.items] == ["CN:588000", "CN:300730"]


def test_tradable_instruments_api_returns_searchable_items(monkeypatch, tmp_path):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'tradable.db'}")
    monkeypatch.setenv("QAGENT_TRADABLE_CACHE_DIR", str(tmp_path / "tradable-cache"))

    def fake_stocks():
        return pd.DataFrame({"code": ["000001", "688981"], "name": ["平安银行", "中芯国际"]})

    def fake_etfs():
        return pd.DataFrame({"代码": ["588000"], "名称": ["科创50ETF"]})

    monkeypatch.setattr(
        "qagent.market.tradable.ak",
        SimpleNamespace(stock_info_a_code_name=fake_stocks, fund_etf_spot_em=fake_etfs),
    )

    client = TestClient(create_app())
    response = client.get("/api/instruments/search?q=科创&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["instrument_id"] == "CN:588000"
    assert body["items"][0]["label"] == "科创50ETF 588000.SH"
    assert body["data_health"]["tradable_total"] == "7"
    assert body["data_health"]["tradable_etf_coverage"] == "core"


def test_sync_cn_tradable_catalog_persists_full_stock_and_etf_universe(monkeypatch, tmp_path):
    db_url = f"sqlite:///{tmp_path / 'tradable-catalog.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", db_url)
    monkeypatch.setenv("QAGENT_TRADABLE_CACHE_DIR", str(tmp_path / "tradable-cache"))

    def fake_stocks():
        return pd.DataFrame({"code": ["000001", "688981"], "name": ["平安银行", "中芯国际"]})

    def fake_etfs():
        return pd.DataFrame(
            {"代码": ["588000", "512480"], "名称": ["科创50ETF", "半导体ETF"]}
        )

    monkeypatch.setattr(
        "qagent.market.tradable.ak",
        SimpleNamespace(stock_info_a_code_name=fake_stocks, fund_etf_spot_em=fake_etfs),
    )

    initialize_database(db_url)
    repo = QagentRepository(create_session_factory(db_url))
    result = sync_cn_tradable_catalog(repo=repo)
    browse = repo.search_tradable_instruments("", limit=2)
    search = repo.search_tradable_instruments("半导体", limit=10)
    symbols = build_full_market_symbols(repo=repo, max_symbols=10)

    assert result.summary.total_count == 4
    assert result.summary.stock_count == 2
    assert result.summary.etf_count == 2
    assert [item.instrument_id for item in browse.items] == ["CN:000001", "CN:688981"]
    assert search.items[0].instrument_id == "CN:512480"
    assert search.items[0].asset_type == "etf"
    assert "CN:588000" in symbols
    assert "CN:000001" in symbols


def test_build_full_market_symbols_keeps_stock_coverage_when_etfs_are_many(monkeypatch, tmp_path):
    db_url = f"sqlite:///{tmp_path / 'tradable-balanced.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", db_url)
    monkeypatch.setenv("QAGENT_TRADABLE_CACHE_DIR", str(tmp_path / "tradable-balanced-cache"))

    def fake_stocks():
        return pd.DataFrame(
            {
                "code": ["000001", "000063", "300750", "600519", "688981"],
                "name": ["平安银行", "中兴通讯", "宁德时代", "贵州茅台", "中芯国际"],
            }
        )

    def fake_etfs():
        return pd.DataFrame(
            {
                "代码": ["510300", "510500", "512480", "588000", "588080", "159915", "159995"],
                "名称": [
                    "沪深300ETF",
                    "中证500ETF",
                    "半导体ETF",
                    "科创50ETF",
                    "科创板50ETF",
                    "创业板ETF",
                    "芯片ETF",
                ],
            }
        )

    monkeypatch.setattr(
        "qagent.market.tradable.ak",
        SimpleNamespace(stock_info_a_code_name=fake_stocks, fund_etf_spot_em=fake_etfs),
    )

    initialize_database(db_url)
    repo = QagentRepository(create_session_factory(db_url))
    sync_cn_tradable_catalog(repo=repo)
    symbols = build_full_market_symbols(repo=repo, max_symbols=6)

    assert len(symbols) == 6
    assert sum(symbol in {"CN:510300", "CN:510500", "CN:512480", "CN:588000", "CN:588080", "CN:159915", "CN:159995"} for symbol in symbols) <= 2
    assert sum(symbol in {"CN:000001", "CN:000063", "CN:300750", "CN:600519", "CN:688981"} for symbol in symbols) >= 4


def test_tradable_catalog_api_syncs_searches_and_scans(monkeypatch, tmp_path):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'tradable-api.db'}")
    monkeypatch.setenv("QAGENT_TRADABLE_CACHE_DIR", str(tmp_path / "tradable-api-cache"))

    def fake_stocks():
        return pd.DataFrame({"code": ["000001", "688981"], "name": ["平安银行", "中芯国际"]})

    def fake_etfs():
        return pd.DataFrame({"代码": ["588000"], "名称": ["科创50ETF"]})

    monkeypatch.setattr(
        "qagent.market.tradable.ak",
        SimpleNamespace(stock_info_a_code_name=fake_stocks, fund_etf_spot_em=fake_etfs),
    )

    client = TestClient(create_app())
    sync_response = client.post("/api/tradable-catalog/sync")
    search_response = client.get("/api/tradable-catalog?q=科创&limit=5")
    scan_response = client.post("/api/full-market/scan?provider=fixture&max_symbols=3")

    assert sync_response.status_code == 200
    assert sync_response.json()["summary"]["total_count"] == 3
    assert search_response.status_code == 200
    assert search_response.json()["items"][0]["instrument_id"] == "CN:588000"
    assert scan_response.status_code == 200
    assert scan_response.json()["data_health"]["full_market_catalog"] == "sqlite"
    assert scan_response.json()["data_health"]["full_market_requested"] == "3"
