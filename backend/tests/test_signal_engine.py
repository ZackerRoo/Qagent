from datetime import date

from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.signals.engine import SignalEngine


def test_signal_engine_generates_trend_and_volume_signals():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    signal_types = {signal.signal_type.value for signal in signals}
    assert "trend_strength" in signal_types
    assert "volume_anomaly" in signal_types


def test_signal_engine_generates_cn_limit_signal_when_near_limit():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("CN:000001", bars)
    assert any(signal.signal_type.value == "limit_status" for signal in signals)
