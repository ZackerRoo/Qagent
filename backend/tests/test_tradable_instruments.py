from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.market import instruments, tradable
from qagent.market.tradable import load_cn_tradable_instruments, search_cn_tradable_instruments
from qagent.market.instruments import format_instrument_label
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.full_market import (
    _tradable_catalog_sync_rejection_reasons,
    build_full_market_symbols,
    sync_cn_tradable_catalog,
)
from qagent.storage.repository import QagentRepository


def test_empty_catalog_name_hydration_does_not_recurse(monkeypatch, tmp_path):
    monkeypatch.setenv("QAGENT_TRADABLE_CACHE_DIR", str(tmp_path / "tradable-cache"))
    monkeypatch.setattr(tradable, "_MEMORY_CACHE", {})
    monkeypatch.setattr(instruments, "_CN_INSTRUMENT_NAMES_READY", False)

    calls = {"stocks": 0, "etfs": 0}

    def fake_stocks():
        calls["stocks"] += 1
        return pd.DataFrame({"code": ["603000"], "name": ["新目录股票"]})

    def fake_etfs():
        calls["etfs"] += 1
        return pd.DataFrame({"代码": ["560650"], "名称": ["新目录ETF"]})

    monkeypatch.setattr(
        tradable,
        "ak",
        SimpleNamespace(stock_info_a_code_name=fake_stocks, fund_etf_spot_em=fake_etfs),
    )
    initialize_database()
    repo = QagentRepository(create_session_factory())
    assert repo.list_tradable_instruments(limit=10) == []

    assert format_instrument_label("CN:603000") == "新目录股票 603000.SH"
    assert format_instrument_label("CN:560650") == "新目录ETF 560650.SH"
    assert calls == {"stocks": 1, "etfs": 1}


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


def test_load_cn_tradable_instruments_excludes_terminal_names_but_keeps_st(monkeypatch):
    def fake_stocks():
        return pd.DataFrame(
            {
                "code": ["000001", "000004", "600193", "920305"],
                "name": ["平安银行", "国华退", "退市创兴", "*ST云创"],
            }
        )

    def fake_etfs():
        return pd.DataFrame({"代码": ["560650"], "名称": ["核心50ETF民生加银"]})

    monkeypatch.setattr(
        "qagent.market.tradable.ak",
        SimpleNamespace(stock_info_a_code_name=fake_stocks, fund_etf_spot_em=fake_etfs),
    )

    catalog = load_cn_tradable_instruments()

    assert [item.instrument_id for item in catalog.items] == [
        "CN:000001",
        "CN:920305",
        "CN:560650",
    ]
    assert catalog.data_health["tradable_inactive_excluded"] == "2"
    assert "CN:000004:国华退" in catalog.data_health["tradable_inactive_samples"]
    assert "CN:600193:退市创兴" in catalog.data_health["tradable_inactive_samples"]


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
        return pd.DataFrame({"代码": ["588000", "512480"], "名称": ["科创50ETF", "半导体ETF"]})

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


def test_sync_cn_tradable_catalog_retains_previous_on_source_fallback(monkeypatch, tmp_path):
    db_url = f"sqlite:///{tmp_path / 'tradable-fail-closed.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", db_url)
    monkeypatch.setenv("QAGENT_TRADABLE_CACHE_DIR", str(tmp_path / "tradable-cache"))

    monkeypatch.setattr(
        "qagent.market.tradable.ak",
        SimpleNamespace(
            stock_info_a_code_name=lambda: pd.DataFrame(
                {"code": ["000001", "688981"], "name": ["平安银行", "中芯国际"]}
            ),
            fund_etf_spot_em=lambda: pd.DataFrame({"代码": ["588000"], "名称": ["科创50ETF"]}),
        ),
    )
    initialize_database(db_url)
    repo = QagentRepository(create_session_factory(db_url))
    first = sync_cn_tradable_catalog(repo=repo)
    assert first.summary.total_count == 3

    def unavailable():
        raise RuntimeError("directory unavailable")

    monkeypatch.setattr(
        "qagent.market.tradable.ak",
        SimpleNamespace(stock_info_a_code_name=unavailable, fund_etf_spot_em=unavailable),
    )
    retained = sync_cn_tradable_catalog(repo=repo)

    assert retained.summary.total_count == 3
    assert retained.data_health["tradable_sync_status"] == "retained_previous"
    assert "stock_source_not_live" in retained.data_health["tradable_sync_rejection_reasons"]
    assert {item.instrument_id for item in repo.list_tradable_instruments(limit=10)} == {
        "CN:000001",
        "CN:688981",
        "CN:588000",
    }


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
    assert (
        sum(
            symbol
            in {
                "CN:510300",
                "CN:510500",
                "CN:512480",
                "CN:588000",
                "CN:588080",
                "CN:159915",
                "CN:159995",
            }
            for symbol in symbols
        )
        <= 2
    )
    assert (
        sum(
            symbol in {"CN:000001", "CN:000063", "CN:300750", "CN:600519", "CN:688981"}
            for symbol in symbols
        )
        >= 4
    )


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


def test_datahubco_catalog_uses_exchange_partitions_and_promax_etfs(monkeypatch):
    from qagent.providers.tushare_relay import RelayTable

    monkeypatch.setattr(tradable, "_DATAHUBCO_MIN_STOCKS", 3)
    monkeypatch.setattr(tradable, "_datahubco_enabled", lambda: True)
    calls = []

    class Client:
        def query(self, api, **kwargs):
            calls.append((api, kwargs))
            rows = {
                "SSE": ({"ts_code": "600519.SH", "name": "贵州茅台", "list_date": "20010827"},),
                "SZSE": ({"ts_code": "000001.SZ", "name": "平安银行", "list_date": "19910403"},),
                "BSE": ({"ts_code": "920305.BJ", "name": "北交股票", "list_date": "20220101"},),
            }[kwargs["exchange"]]
            return RelayTable(api, tuple(rows[0]), rows, kwargs["limit"])

    monkeypatch.setattr(tradable, "_build_datahubco_client", Client)
    monkeypatch.setattr(tradable, "_load_promax_etfs", lambda stocks: {"588000": "科创50ETF"})
    catalog = tradable.load_cn_tradable_instruments(prefer_datahubco=True)
    assert [item.source for item in catalog.items] == [
        "datahubco_stock_basic", "datahubco_stock_basic",
        "datahubco_stock_basic", "tushare_relay_promax_etf_basic",
    ]
    assert [params["exchange"] for _, params in calls] == ["SSE", "SZSE", "BSE"]
    assert all("offset" not in params and params["limit"] == 5000 for _, params in calls)
    assert catalog.data_health["tradable_source"] == "datahubco"


@pytest.mark.parametrize("bad_row,expected_error", [
    ({"ts_code": "600519.SZ", "name": "贵州茅台", "list_date": "20010827"},
     "invalid_stock_code"),
    ({"ts_code": "600519.SH", "name": "", "list_date": "20010827"},
     "invalid_stock_name"),
    ({"ts_code": "600519.SH", "name": "贵州茅台", "list_date": "20990101"},
     "invalid_stock_listing_date"),
])
def test_datahubco_catalog_rejects_bad_stock_fields_and_falls_back(
    monkeypatch, bad_row, expected_error,
):
    from qagent.providers.tushare_relay import RelayTable

    monkeypatch.setattr(tradable, "_DATAHUBCO_MIN_STOCKS", 1)
    monkeypatch.setattr(tradable, "_datahubco_enabled", lambda: True)

    class Client:
        def query(self, api, **kwargs):
            return RelayTable(api, tuple(bad_row), (bad_row,), kwargs["limit"])

    monkeypatch.setattr(tradable, "_build_datahubco_client", Client)
    monkeypatch.setattr(tradable.ak, "stock_info_a_code_name", lambda: pd.DataFrame(
        {"code": ["000001"], "name": ["平安银行"]}))
    catalog = tradable.load_cn_tradable_instruments(
        include_full_etfs=False, prefer_datahubco=True,
    )
    assert catalog.data_health["tradable_source"] == "akshare"
    assert catalog.data_health["tradable_datahubco_status"] == "rejected"
    assert expected_error in catalog.data_health["tradable_datahubco_error"]


def test_promax_etf_catalog_rejects_non_etf_code_and_name(monkeypatch):
    from qagent.providers.tushare_relay import RelayTable

    monkeypatch.setattr(tradable, "get_settings", lambda: SimpleNamespace(
        tushare_relay_research_enabled=True,
        tushare_relay_key=SimpleNamespace(get_secret_value=lambda: "test-key"),
    ))
    monkeypatch.setattr(tradable, "_DATAHUBCO_MIN_ETFS", 1)

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def query(self, api, **kwargs):
            row = {"ts_code": "588000.SH", "extname": "", "csname": "",
                   "list_status": "L", "list_date": "20200101"}
            return RelayTable(api, tuple(row), (row,), kwargs["limit"])

    monkeypatch.setattr(tradable, "TushareRelayClient", Client)
    with pytest.raises(ValueError, match="invalid_etf_name"):
        tradable._load_promax_etfs({})


def test_promax_etf_short_page_is_incomplete(monkeypatch):
    from qagent.providers.tushare_relay import RelayTable

    monkeypatch.setattr(tradable, "_DATAHUBCO_MIN_ETFS", 1000)
    monkeypatch.setattr(tradable, "get_settings", lambda: SimpleNamespace(
        tushare_relay_research_enabled=True,
        tushare_relay_key=SimpleNamespace(get_secret_value=lambda: "test-key"),
    ))

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def query(self, api, **kwargs):
            assert kwargs["list_status"] == "L"
            row = {"ts_code": "588000.SH", "extname": "科创50ETF",
                   "list_status": "L", "list_date": "20200101"}
            return RelayTable(api, tuple(row), (row,), kwargs["limit"])

    monkeypatch.setattr(tradable, "TushareRelayClient", Client)
    with pytest.raises(ValueError, match="incomplete_etf_coverage:1"):
        tradable._load_promax_etfs({})


def test_opted_in_catalog_retains_previous_if_etf_sources_fail(monkeypatch, tmp_path):
    from qagent.providers.tushare_relay import RelayTable

    db_url = f"sqlite:///{tmp_path / 'tradable-opt-in-retain.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", db_url)
    monkeypatch.setattr(tradable, "_DATAHUBCO_MIN_STOCKS", 3)
    monkeypatch.setattr(tradable, "_datahubco_enabled", lambda: True)
    monkeypatch.setattr("qagent.jobs.full_market.get_settings", lambda: SimpleNamespace(
        tradable_datahubco_enabled=True))
    initialize_database(db_url)
    repo = QagentRepository(create_session_factory(db_url))
    first = tradable.TradableInstrumentCatalog(items=[
        tradable._instrument("000001", "平安银行", "stock", "akshare"),
        tradable._instrument("588000", "科创50ETF", "etf", "akshare"),
    ])
    repo.replace_tradable_instruments(first.items, first.data_health)

    class Client:
        def query(self, api, **kwargs):
            symbols = {"SSE": "600519.SH", "SZSE": "000001.SZ", "BSE": "920305.BJ"}
            row = {"ts_code": symbols[kwargs["exchange"]], "name": "新股票",
                   "list_date": "20200101"}
            return RelayTable(api, tuple(row), (row,), kwargs["limit"])

    monkeypatch.setattr(tradable, "_build_datahubco_client", Client)
    monkeypatch.setattr(tradable, "_load_promax_etfs", lambda stocks: (_ for _ in ()).throw(
        RuntimeError("upstream_503")))
    monkeypatch.setattr(tradable.ak, "fund_etf_spot_em", lambda: (_ for _ in ()).throw(
        RuntimeError("akshare_unavailable")))
    monkeypatch.setattr(tradable.ak, "stock_info_a_code_name", lambda: pd.DataFrame())

    result = sync_cn_tradable_catalog(repo=repo)
    assert result.data_health["tradable_sync_status"] == "retained_previous"
    assert result.summary.total_count == 2
    assert {item.instrument_id for item in repo.list_tradable_instruments(limit=10)} == {
        "CN:000001", "CN:588000",
    }


def test_datahubco_catalog_rejects_one_of_five_etfs_missing():
    previous = SimpleNamespace(total_count=6, stock_count=1, etf_count=5)
    catalog = tradable.TradableInstrumentCatalog(
        items=[tradable._instrument("000001", "平安银行", "stock", "datahubco")]
        + [tradable._instrument(symbol, "ETF", "etf", "promax") for symbol in (
            "588000", "510300", "510500", "512100",
        )],
        data_health={
            "tradable_source": "datahubco",
            "tradable_stock_source_status": "live",
            "tradable_etf_source_status": "live",
        },
    )
    assert "etf_coverage_drop:5->4" in _tradable_catalog_sync_rejection_reasons(
        previous, catalog, include_full_etfs=True,
    )
