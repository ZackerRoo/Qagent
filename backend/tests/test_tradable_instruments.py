from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.market.tradable import load_cn_tradable_instruments, search_cn_tradable_instruments
from qagent.market.instruments import format_instrument_label


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
