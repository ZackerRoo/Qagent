from datetime import date, datetime

import pandas as pd
import pytest

from qagent.config import Settings
from qagent.providers import factory
from qagent.providers.datahubco import DatahubcoError
from qagent.providers.datahubco_market import DatahubcoMarketDataProvider
from qagent.providers.daily_fallback import DailyFallbackMarketDataProvider
from qagent.providers.status import build_provider_status
from qagent.providers.tushare_relay import RelayTable
from qagent.providers.tushare_relay_market import TushareRelayMarketDataProvider

DAY = date(2026, 1, 5)


class Client:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def query(self, api, **params):
        self.calls.append((api, params))
        if self.error:
            raise self.error
        row = dict(ts_code=params["ts_code"], trade_date="20260105", open=10,
                   high=12, low=9, close=11, vol=20, amount=22)
        return RelayTable(api, tuple(row), (row,), 500, source="datahubco")


def test_raw_provenance_units_and_two_instrument_budget():
    client = Client()
    provider = DatahubcoMarketDataProvider(client)
    bars = provider.get_daily_bars(["CN:000001", "CN:600000", "CN:300001"], DAY, DAY)
    assert len(client.calls) == 2
    assert set(bars.provider) == {"datahubco_daily_raw"}
    assert set(bars.adjustment_type) == {"raw"}
    assert bars.adjusted_close.isna().all()
    assert bars.volume.tolist() == [2000, 2000]
    assert bars.turnover.tolist() == [22000, 22000]
    assert provider.last_errors == ["datahubco:runtime_instrument_limit"]


def test_error_identity_and_circuit_are_basic_service(monkeypatch):
    import qagent.providers.tushare_relay_market as shared
    clock = [0.0]
    monkeypatch.setattr(shared, "monotonic", lambda: clock[0])
    client = Client(DatahubcoError("transport_error"))
    provider = DatahubcoMarketDataProvider(client)
    assert provider.get_daily_bars(["CN:000001", "CN:600000"], DAY, DAY).empty
    assert len(client.calls) == 1
    assert provider.last_errors == ["CN:000001: datahubco:transport_error"]
    assert provider.get_historical_daily_bars(["CN:000001"], DAY, DAY).empty
    assert provider.last_errors == ["datahubco:runtime_circuit_open"]
    assert len(client.calls) == 1
    clock[0] = 301
    provider.get_daily_bars(["CN:000001"], DAY, DAY)
    assert len(client.calls) == 2


def test_primary_and_intraday_paths_preserved(monkeypatch):
    import qagent.providers.tushare_relay_market as shared
    class Morning(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 5, 10, tzinfo=tz)
    monkeypatch.setattr(shared, "datetime", Morning)
    client = Client()
    provider = DatahubcoMarketDataProvider(client)
    assert provider.get_daily_bars(["CN:000001"], DAY, DAY).empty
    assert client.calls == []
    frame = pd.DataFrame([dict(instrument_id="CN:000001", close=999)])
    class Primary:
        name = "primary"
        def get_daily_bars(self, *args):
            return frame
        def get_snapshot(self, *args):
            return frame
        def get_minute_bars(self, *args):
            return frame
    wrapped = DailyFallbackMarketDataProvider(Primary(), provider)
    assert wrapped.get_daily_bars(["CN:000001"], DAY, DAY).close.tolist() == [999]
    assert wrapped.get_snapshot(["CN:000001"]) is frame
    assert wrapped.get_minute_bars([], datetime.now(), datetime.now()) is frame
    assert client.calls == []


@pytest.mark.parametrize("enabled,key,allow,expected", [
    (False, "basic-test", True, False), (True, None, True, False),
    (True, "basic-test", False, False), (True, "basic-test", True, True),
])
def test_factory_three_explicit_requirements_and_status(monkeypatch, enabled, key, allow, expected):
    settings = Settings(_env_file=None, datahubco_key=key, datahubco_enabled=enabled,
                        datahubco_allow_insecure_http=allow, fuyao_api_key=None,
                        tushare_relay_market_enabled=True, tushare_relay_key="relay-test")
    monkeypatch.setattr(factory, "get_settings", lambda: settings)
    monkeypatch.setattr(factory, "_with_market_cache", lambda provider, *a, **kw: provider)
    cn = factory.build_market_data_provider("free").providers_by_market["CN"]
    assert isinstance(cn.fallback, DatahubcoMarketDataProvider) == expected
    if expected:
        assert isinstance(cn.primary.fallback, TushareRelayMarketDataProvider)
        assert cn.fallback.client.timeout_seconds == 5
        assert cn.max_fallback_instruments == 2
        assert cn.max_fallback_batches == 1
    status = next(row for row in build_provider_status(settings) if row.provider_id == "datahubco")
    assert status.status == ("configured" if expected else "disabled" if not enabled else "missing_config")
    assert "healthy" in status.notes
    assert "basic-test" not in repr(settings)
