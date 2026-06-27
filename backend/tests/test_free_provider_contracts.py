from datetime import date
from types import SimpleNamespace

import pandas as pd
import requests

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


def test_free_cn_provider_applies_timeout_to_akshare_requests(monkeypatch):
    captured_timeouts = []

    def fake_request(self, method, url, **kwargs):
        captured_timeouts.append(kwargs.get("timeout"))
        return SimpleNamespace(status_code=200, text="ok")

    def fake_zh_a_hist(symbol, period, start_date, end_date, adjust):
        requests.Session().request("GET", "https://example.test/history")
        return pd.DataFrame(
            {
                "日期": ["2026-01-02"],
                "开盘": [10.0],
                "最高": [10.4],
                "最低": [9.9],
                "收盘": [10.3],
                "成交量": [800_000],
            }
        )

    fake_ak = SimpleNamespace(stock_zh_a_hist=fake_zh_a_hist)
    monkeypatch.setattr("qagent.providers.free_cn.ak", fake_ak)
    monkeypatch.setattr("qagent.providers.free_cn.requests.sessions.Session.request", fake_request)

    provider = FreeCnMarketDataProvider(request_timeout_seconds=2)
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 1, 31))

    assert bars["instrument_id"].tolist() == ["CN:000001"]
    assert captured_timeouts == [(2, 2)]


def test_free_cn_provider_uses_etf_history_for_etf_symbols(monkeypatch):
    def fake_stock_hist(*args, **kwargs):
        raise AssertionError("ETF symbols must not use stock_zh_a_hist")

    def fake_fund_etf_hist(symbol, period, start_date, end_date, adjust):
        assert symbol == "588000"
        assert period == "daily"
        return pd.DataFrame(
            {
                "日期": ["2026-01-02", "2026-01-05"],
                "开盘": [1.0, 1.02],
                "最高": [1.03, 1.05],
                "最低": [0.99, 1.01],
                "收盘": [1.02, 1.04],
                "成交量": [8_000_000, 8_200_000],
            }
        )

    fake_ak = SimpleNamespace(
        stock_zh_a_hist=fake_stock_hist,
        fund_etf_hist_em=fake_fund_etf_hist,
    )
    monkeypatch.setattr("qagent.providers.free_cn.ak", fake_ak)

    provider = FreeCnMarketDataProvider()
    bars = provider.get_daily_bars(["CN:588000"], date(2026, 1, 1), date(2026, 1, 31))

    assert bars["instrument_id"].tolist() == ["CN:588000", "CN:588000"]
    assert bars["provider"].eq("akshare_etf").all()
    assert bars["close"].tolist() == [1.02, 1.04]


def test_free_cn_provider_drops_nonfinite_ohlc_rows(monkeypatch):
    def fake_zh_a_hist(symbol, period, start_date, end_date, adjust):
        return pd.DataFrame(
            {
                "日期": ["2026-01-02", "2026-01-05", "2026-01-06"],
                "开盘": [10.0, float("inf"), 10.2],
                "最高": [10.4, 10.5, 10.6],
                "最低": [9.9, 10.1, 10.1],
                "收盘": [10.3, 10.4, float("-inf")],
                "成交量": [800_000, 820_000, 830_000],
            }
        )

    fake_ak = SimpleNamespace(stock_zh_a_hist=fake_zh_a_hist)
    monkeypatch.setattr("qagent.providers.free_cn.ak", fake_ak)

    provider = FreeCnMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 1, 31))

    assert bars["trade_date"].tolist() == [date(2026, 1, 2)]
    assert bars["close"].tolist() == [10.3]


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


def test_free_cn_provider_circuit_breaker_skips_after_consecutive_source_failures(monkeypatch):
    stock_calls: list[str] = []
    login_calls: list[str] = []

    def fake_zh_a_hist(symbol, period, start_date, end_date, adjust):
        stock_calls.append(symbol)
        raise ConnectionError("source closed connection")

    fake_ak = SimpleNamespace(stock_zh_a_hist=fake_zh_a_hist)
    fake_bs = SimpleNamespace(
        login=lambda: login_calls.append("login") or SimpleNamespace(
            error_code="1",
            error_msg="login failed",
        ),
        logout=lambda: None,
    )
    monkeypatch.setattr("qagent.providers.free_cn.ak", fake_ak)
    monkeypatch.setattr("qagent.providers.free_cn.bs", fake_bs)

    provider = FreeCnMarketDataProvider(failure_circuit_breaker_threshold=2)
    bars = provider.get_daily_bars(
        ["CN:000001", "CN:000002", "CN:000003"],
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert bars.empty
    assert stock_calls == ["000001", "000002"]
    assert len(login_calls) == 2
    assert "skipped after 2 consecutive source failures" in provider.last_errors[-1]


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
