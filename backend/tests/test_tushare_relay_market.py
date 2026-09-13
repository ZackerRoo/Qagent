from datetime import date, datetime

import pandas as pd
import pytest

from qagent.config import Settings
from qagent.providers import factory
from qagent.providers.daily_fallback import DailyFallbackMarketDataProvider
from qagent.providers.tushare_relay import RelayError, RelayTable
from qagent.providers.tushare_relay_market import TushareRelayMarketDataProvider


START, END = date(2026, 1, 5), date(2026, 1, 6)


class Client:
    def __init__(self, change=None, error=None):
        self.calls = []
        self.change = change
        self.error = error

    def query(self, api, **params):
        self.calls.append((api, params))
        if self.error:
            raise self.error
        rows = [dict(ts_code=params["ts_code"], trade_date=day,
                     open=10, high=12, low=9, close=11, vol=20, amount=22,
                     adj_factor=factor)
                for day, factor in [("20260105", 1), ("20260106", 2)]]
        if self.change:
            self.change(api, rows)
        return RelayTable(api, tuple(rows[0]) if rows else (), tuple(rows), 500)


def test_raw_units_source_and_no_fabricated_adjustment():
    provider = TushareRelayMarketDataProvider(Client())
    bars = provider.get_daily_bars(["CN:000001"], START, END)
    assert bars.close.tolist() == [11, 11]
    assert bars.adjusted_close.isna().all()
    assert bars.adjustment_factor.isna().all()
    assert set(bars.adjustment_type) == {"raw"}
    assert bars.volume.tolist() == [2000, 2000]
    assert bars.turnover.tolist() == [22000, 22000]
    assert set(bars.provider) == {"tushare_relay_promax_daily_raw"}
    assert bars.trade_date.tolist() == [START, END]


@pytest.mark.parametrize("field,value", [
    ("ts_code", "600000.SH"), ("trade_date", "20260107"), ("open", -1),
    ("high", 8), ("vol", None), ("vol", -1), ("amount", float("inf")),
    ("close", True),
])
def test_invalid_rows_fail_closed(field, value):
    client = Client(change=lambda api, rows: rows[0].update({field: value}))
    provider = TushareRelayMarketDataProvider(client)
    assert provider.get_daily_bars(["CN:000001"], START, END).empty
    assert provider.last_errors


def test_duplicate_daily_rejects_whole_instrument():
    def change(api, rows):
        rows.append(rows[0].copy())
    provider = TushareRelayMarketDataProvider(Client(change=change))
    assert provider.get_daily_bars(["CN:000001"], START, END).empty


def test_failure_stops_batch_and_circuits_repeated_calls(monkeypatch):
    import qagent.providers.tushare_relay_market as module
    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    client = Client(error=RelayError("transport_error"))
    provider = TushareRelayMarketDataProvider(client)
    assert provider.get_daily_bars(["CN:000001", "CN:600000"], START, END).empty
    assert len(client.calls) == 1
    provider.get_daily_bars(["CN:600000"], START, END)
    assert len(client.calls) == 1
    now[0] += 301
    provider.get_daily_bars(["CN:600000"], START, END)
    assert len(client.calls) == 2


def test_request_and_time_limits(monkeypatch):
    import qagent.providers.tushare_relay_market as module
    client = Client()
    provider = TushareRelayMarketDataProvider(client)
    provider.get_daily_bars(["CN:000001", "CN:600000", "CN:300001"], START, END)
    assert len(client.calls) == 2
    assert "tushare_relay:runtime_instrument_limit" in provider.last_errors
    times = iter([0, 31])
    monkeypatch.setattr(module, "monotonic", lambda: next(times))
    provider._retry_after = -1
    # Circuit check, then batch start, then elapsed check.
    times = iter([0, 0, 31])
    assert provider.get_daily_bars(["CN:000001"], START, END).empty
    assert len(client.calls) == 2


def test_current_unfinished_session_excluded(monkeypatch):
    import qagent.providers.tushare_relay_market as module
    class Morning(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 6, 10, tzinfo=tz)
    monkeypatch.setattr(module, "datetime", Morning)
    client = Client()
    provider = TushareRelayMarketDataProvider(client)
    assert provider.get_daily_bars(["CN:000001"], END, END).empty
    assert client.calls == []


def test_primary_preserved_and_snapshot_minutes_never_use_relay():
    primary_frame = pd.DataFrame([dict(instrument_id="CN:000001", trade_date=START,
                                       close=999, provider="primary")])
    class Primary:
        name = "primary"
        def get_daily_bars(self, *args):
            return primary_frame
        def get_snapshot(self, *args):
            return primary_frame
        def get_minute_bars(self, *args):
            return primary_frame
    client = Client()
    wrapper = DailyFallbackMarketDataProvider(Primary(), TushareRelayMarketDataProvider(client))
    bars = wrapper.get_daily_bars(["CN:000001", "CN:600000"], START, END)
    assert bars[bars.instrument_id == "CN:000001"].close.tolist() == [999]
    assert {params["ts_code"] for _, params in client.calls} == {"600000.SH"}
    assert wrapper.get_snapshot(["CN:000001"]) is primary_frame
    assert wrapper.get_minute_bars([], datetime.now(), datetime.now()) is primary_frame
    assert len(client.calls) == 1


@pytest.mark.parametrize("enabled,key,expected", [
    (False, "test-key", False), (True, None, False), (True, "test-key", True),
])
def test_factory_explicit_opt_in(monkeypatch, enabled, key, expected):
    settings = Settings(_env_file=None, tushare_relay_market_enabled=enabled,
                        tushare_relay_key=key, fuyao_api_key=None)
    monkeypatch.setattr(factory, "get_settings", lambda: settings)
    monkeypatch.setattr(factory, "_with_market_cache", lambda provider, *a, **kw: provider)
    cn = factory.build_market_data_provider("free").providers_by_market["CN"]
    assert isinstance(cn.fallback, TushareRelayMarketDataProvider) == expected
    if expected:
        assert cn.max_fallback_instruments == 2
        assert cn.max_fallback_batches == 1
        assert cn.fallback.client.retries == 0
        assert cn.fallback.client.timeout_seconds == 5


def test_factory_preserves_dedicated_fuyao_repair(monkeypatch):
    settings = Settings(_env_file=None, tushare_relay_market_enabled=True,
                        tushare_relay_key="test-key", fuyao_api_key="test-fuyao")
    monkeypatch.setattr(factory, "get_settings", lambda: settings)
    monkeypatch.setattr(factory, "_with_market_cache", lambda provider, *a, **kw: provider)
    composite = factory.build_market_data_provider("free")
    cn = composite.providers_by_market["CN"]
    calls = []
    monkeypatch.setattr(cn.snapshot_provider, "get_snapshot",
                        lambda ids: calls.append(ids) or pd.DataFrame())
    def forbidden(*args):
        pytest.fail("repair must not enter historical snapshot fallback")
    monkeypatch.setattr(cn.market_data_provider, "get_snapshot", forbidden)
    assert composite.get_repair_snapshot(["CN:000001"]).empty
    assert calls == [["CN:000001"]]
