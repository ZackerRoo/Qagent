from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

import qagent.providers.free_cn as free_cn
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
    calls = []

    def fake_zh_a_hist(symbol, period, start_date, end_date, adjust):
        assert symbol == "000001"
        calls.append(adjust)
        scale = 0.5 if adjust == "qfq" else 1.0
        return pd.DataFrame(
            {
                "日期": ["2026-01-02", "2026-01-05"],
                "开盘": [10.0 * scale, 10.2 * scale],
                "最高": [10.4 * scale, 10.5 * scale],
                "最低": [9.9 * scale, 10.1 * scale],
                "收盘": [10.3 * scale, 10.4 * scale],
                "成交量": [800_000, 820_000],
                "成交额": [8_000_000, 8_200_000],
            }
        )

    fake_ak = SimpleNamespace(stock_zh_a_hist=fake_zh_a_hist)
    monkeypatch.setattr("qagent.providers.free_cn.ak", fake_ak)

    provider = FreeCnMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 1, 31))

    assert bars["instrument_id"].tolist() == ["CN:000001", "CN:000001"]
    assert calls == ["", "qfq"]
    assert bars["provider"].eq("akshare_stock_paired").all()
    assert bars["adjustment_type"].eq("qfq").all()
    assert bars["close"].tolist() == [10.3, 10.4]
    assert bars["adjusted_close"].tolist() == [5.15, 5.2]
    assert bars["adjustment_factor"].tolist() == [0.5, 0.5]
    assert bars["turnover"].tolist() == [8_000_000, 8_200_000]
    assert bars["volume"].tolist() == [800_000, 820_000]


def test_free_cn_provider_keeps_raw_dates_without_fabricating_adjusted_rows(
    monkeypatch,
):
    def fake_zh_a_hist(symbol, period, start_date, end_date, adjust):
        dates = ["2026-01-02"] if adjust == "qfq" else ["2026-01-02", "2026-01-05"]
        return pd.DataFrame(
            {
                "日期": dates,
                "开盘": [5.0] if adjust == "qfq" else [10.0, 10.2],
                "最高": [5.2] if adjust == "qfq" else [10.4, 10.5],
                "最低": [4.95] if adjust == "qfq" else [9.9, 10.1],
                "收盘": [5.15] if adjust == "qfq" else [10.3, 10.4],
                "成交量": [800_000] if adjust == "qfq" else [800_000, 820_000],
            }
        )

    monkeypatch.setattr(
        "qagent.providers.free_cn.ak",
        SimpleNamespace(stock_zh_a_hist=fake_zh_a_hist),
    )

    bars = FreeCnMarketDataProvider().get_daily_bars(
        ["CN:000001"],
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert bars["trade_date"].tolist() == [date(2026, 1, 2), date(2026, 1, 5)]
    assert bars["close"].tolist() == [10.3, 10.4]
    assert bars.iloc[0]["adjusted_close"] == 5.15
    assert pd.isna(bars.iloc[1]["adjusted_close"])
    assert pd.isna(bars.iloc[1]["adjustment_factor"])
    assert pd.isna(bars.iloc[1]["adjustment_type"])


def test_free_cn_provider_keeps_raw_rows_when_adjusted_source_is_empty(monkeypatch):
    def fake_zh_a_hist(symbol, period, start_date, end_date, adjust):
        if adjust == "qfq":
            return pd.DataFrame()
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

    monkeypatch.setattr(
        "qagent.providers.free_cn.ak",
        SimpleNamespace(stock_zh_a_hist=fake_zh_a_hist),
    )

    bars = FreeCnMarketDataProvider().get_daily_bars(
        ["CN:000001"], date(2026, 1, 1), date(2026, 1, 31)
    )

    assert bars["close"].tolist() == [10.3]
    assert bars["provider"].tolist() == ["akshare_stock_paired"]
    assert pd.isna(bars.iloc[0]["adjusted_close"])
    assert pd.isna(bars.iloc[0]["adjustment_factor"])


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
    assert captured_timeouts == [(2, 2), (2, 2)]


def test_free_cn_provider_uses_etf_history_for_etf_symbols(monkeypatch):
    def fake_stock_hist(*args, **kwargs):
        raise AssertionError("ETF symbols must not use stock_zh_a_hist")

    def fake_fund_etf_hist(symbol, period, start_date, end_date, adjust):
        assert symbol == "588000"
        assert period == "daily"
        assert adjust in {"", "qfq"}
        scale = 0.8 if adjust == "qfq" else 1.0
        return pd.DataFrame(
            {
                "日期": ["2026-01-02", "2026-01-05"],
                "开盘": [1.0 * scale, 1.02 * scale],
                "最高": [1.03 * scale, 1.05 * scale],
                "最低": [0.99 * scale, 1.01 * scale],
                "收盘": [1.02 * scale, 1.04 * scale],
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
    assert bars["provider"].eq("akshare_etf_paired").all()
    assert bars["adjustment_type"].eq("qfq").all()
    assert bars["close"].tolist() == [1.02, 1.04]
    assert bars["adjusted_close"].tolist() == [pytest.approx(0.816), pytest.approx(0.832)]


def test_free_cn_provider_uses_adjusted_yfinance_etf_fallback(monkeypatch):
    def failing_etf_history(*args, **kwargs):
        raise ConnectionError("eastmoney disconnected")

    columns = pd.MultiIndex.from_tuples(
        [
            ("Open", "159915.SZ"),
            ("High", "159915.SZ"),
            ("Low", "159915.SZ"),
            ("Close", "159915.SZ"),
            ("Adj Close", "159915.SZ"),
            ("Volume", "159915.SZ"),
        ]
    )

    def fake_download(tickers, start, end, progress, auto_adjust, timeout):
        assert tickers == "159915.SZ"
        assert auto_adjust is False
        assert timeout == 3
        return pd.DataFrame(
            [[1.0, 1.1, 0.9, 1.05, 0.84, 8_000_000]],
            index=pd.to_datetime(["2026-01-05"]),
            columns=columns,
        )

    monkeypatch.setattr(
        free_cn,
        "ak",
        SimpleNamespace(fund_etf_hist_em=failing_etf_history),
    )
    monkeypatch.setattr(
        free_cn,
        "yf",
        SimpleNamespace(download=fake_download),
        raising=False,
    )
    monkeypatch.setattr(
        free_cn,
        "bs",
        SimpleNamespace(login=lambda: (_ for _ in ()).throw(AssertionError())),
    )

    provider = FreeCnMarketDataProvider()
    bars = provider.get_daily_bars(
        ["CN:159915"],
        date(2026, 1, 1),
        date(2026, 1, 9),
    )

    assert bars["instrument_id"].tolist() == ["CN:159915"]
    assert bars["provider"].tolist() == ["yfinance_cn_etf_paired"]
    assert bars["adjustment_type"].tolist() == ["qfq"]
    assert bars["close"].tolist() == [1.05]
    assert bars["adjusted_close"].tolist() == [0.84]
    assert bars["adjustment_factor"].tolist() == [pytest.approx(0.8)]


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
        fields = ["date", "open", "high", "low", "close", "volume", "amount"]

        def __init__(self, adjustflag):
            scale = Decimal("0.8") if adjustflag == "2" else Decimal("1")
            self.rows = [[
                "2026-01-05",
                str(Decimal("11.42") * scale),
                str(Decimal("11.51") * scale),
                str(Decimal("11.41") * scale),
                str(Decimal("11.50") * scale),
                "87549118",
                "1000000000",
            ]]
            self.index = -1

        def next(self):
            self.index += 1
            return self.index < len(self.rows)

        def get_row_data(self):
            return self.rows[self.index]

    def fake_query_history_k_data_plus(code, fields, start_date, end_date, frequency, adjustflag):
        assert code == "sz.000001"
        assert fields == "date,open,high,low,close,volume,amount"
        assert adjustflag in {"2", "3"}
        return FakeQueryResult(adjustflag)

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
    assert bars["provider"].tolist() == ["baostock_paired"]
    assert bars["adjustment_type"].tolist() == ["qfq"]
    assert bars.iloc[0]["close"] == 11.5
    assert bars.iloc[0]["adjusted_close"] == 9.2
    assert provider.last_errors == []
