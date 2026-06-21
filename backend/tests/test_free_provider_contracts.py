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
