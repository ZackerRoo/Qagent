from datetime import date

from qagent.monitoring.outcomes import compute_forward_returns
from qagent.providers.fixtures import FixtureMarketDataProvider


def test_compute_forward_returns():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    result = compute_forward_returns(
        bars, signal_date=bars["trade_date"].iloc[20], horizons=(1, 5, 10)
    )
    assert set(result.keys()) == {"return_1d", "return_5d", "return_10d"}


def test_compute_forward_returns_uses_none_when_horizon_unavailable():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    result = compute_forward_returns(bars, signal_date=bars["trade_date"].iloc[-1], horizons=(1,))
    assert result["return_1d"] is None
