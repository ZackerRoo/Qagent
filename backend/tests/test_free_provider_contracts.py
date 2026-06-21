from datetime import date
from types import SimpleNamespace

import pandas as pd

from qagent.providers.free_cn import FreeCnMarketDataProvider
from qagent.providers.free_us import FreeUsMarketDataProvider


def test_free_us_provider_normalizes_yfinance_download(monkeypatch):
    def fake_download(tickers, start, end, progress, auto_adjust):
        assert tickers == "AAPL"
        assert progress is False
        assert auto_adjust is False
        return pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.0, 102.0],
                "Volume": [1_000_000, 1_100_000],
            }
        ).set_index("Date")

    monkeypatch.setattr("qagent.providers.free_us.yf.download", fake_download)

    provider = FreeUsMarketDataProvider()
    bars = provider.get_daily_bars(["US:AAPL"], date(2026, 1, 1), date(2026, 1, 31))

    assert list(bars.columns) == [
        "instrument_id",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "provider",
    ]
    assert bars["instrument_id"].tolist() == ["US:AAPL", "US:AAPL"]
    assert bars["provider"].eq("yfinance").all()


def test_free_us_provider_flattens_yfinance_multi_index_columns(monkeypatch):
    def fake_download(tickers, start, end, progress, auto_adjust):
        assert tickers == "AAPL"
        columns = pd.MultiIndex.from_tuples(
            [
                ("Open", "AAPL"),
                ("High", "AAPL"),
                ("Low", "AAPL"),
                ("Close", "AAPL"),
                ("Volume", "AAPL"),
            ],
            names=["Price", "Ticker"],
        )
        return pd.DataFrame(
            [[100.0, 102.0, 99.0, 101.0, 1_000_000]],
            index=pd.to_datetime(["2026-01-02"]),
            columns=columns,
        )

    monkeypatch.setattr("qagent.providers.free_us.yf.download", fake_download)

    provider = FreeUsMarketDataProvider()
    bars = provider.get_daily_bars(["US:AAPL"], date(2026, 1, 1), date(2026, 1, 31))

    assert list(bars.columns) == [
        "instrument_id",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "provider",
    ]
    assert bars.iloc[0]["close"] == 101.0


def test_free_cn_provider_normalizes_akshare_daily(monkeypatch):
    def fake_zh_a_hist(symbol, period, start_date, end_date, adjust):
        assert symbol == "000001"
        return pd.DataFrame(
            {
                "日期": ["2026-01-02", "2026-01-05"],
                "开盘": [10.0, 10.2],
                "最高": [10.4, 10.5],
                "最低": [9.9, 10.1],
                "收盘": [10.3, 10.4],
                "成交量": [800_000, 820_000],
            }
        )

    fake_ak = SimpleNamespace(stock_zh_a_hist=fake_zh_a_hist)
    monkeypatch.setattr("qagent.providers.free_cn.ak", fake_ak)

    provider = FreeCnMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 1, 31))

    assert bars["instrument_id"].tolist() == ["CN:000001", "CN:000001"]
    assert bars["provider"].eq("akshare").all()
    assert bars["volume"].tolist() == [800_000, 820_000]


def test_free_cn_provider_records_source_errors(monkeypatch):
    def fake_zh_a_hist(symbol, period, start_date, end_date, adjust):
        raise ConnectionError("source closed connection")

    fake_ak = SimpleNamespace(stock_zh_a_hist=fake_zh_a_hist)
    monkeypatch.setattr("qagent.providers.free_cn.ak", fake_ak)
    fake_bs = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="1", error_msg="login failed"),
        logout=lambda: None,
    )
    monkeypatch.setattr("qagent.providers.free_cn.bs", fake_bs)

    provider = FreeCnMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 1, 31))

    assert bars.empty
    assert provider.last_errors == [
        "CN:000001: akshare: source closed connection; baostock: login failed"
    ]


def test_free_cn_provider_falls_back_to_baostock(monkeypatch):
    def fake_zh_a_hist(symbol, period, start_date, end_date, adjust):
        raise ConnectionError("source closed connection")

    class FakeQueryResult:
        error_code = "0"
        error_msg = "success"
        fields = ["date", "open", "high", "low", "close", "volume"]

        def __init__(self):
            self.rows = [["2026-01-05", "11.42", "11.51", "11.41", "11.50", "87549118"]]
            self.index = -1

        def next(self):
            self.index += 1
            return self.index < len(self.rows)

        def get_row_data(self):
            return self.rows[self.index]

    def fake_query_history_k_data_plus(code, fields, start_date, end_date, frequency, adjustflag):
        assert code == "sz.000001"
        assert fields == "date,open,high,low,close,volume"
        return FakeQueryResult()

    fake_ak = SimpleNamespace(stock_zh_a_hist=fake_zh_a_hist)
    fake_bs = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="0", error_msg="success"),
        query_history_k_data_plus=fake_query_history_k_data_plus,
        logout=lambda: None,
    )
    monkeypatch.setattr("qagent.providers.free_cn.ak", fake_ak)
    monkeypatch.setattr("qagent.providers.free_cn.bs", fake_bs)

    provider = FreeCnMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 1, 31))

    assert bars["instrument_id"].tolist() == ["CN:000001"]
    assert bars["provider"].tolist() == ["baostock"]
    assert bars.iloc[0]["close"] == 11.5
    assert provider.last_errors == []
