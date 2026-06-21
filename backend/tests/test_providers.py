from datetime import date

from qagent.providers.fixtures import FixtureMarketDataProvider


def test_fixture_provider_loads_us_bars():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    assert not bars.empty
    assert {
        "instrument_id",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }.issubset(bars.columns)


def test_fixture_provider_filters_instrument_ids():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 3, 31))
    assert bars["instrument_id"].eq("CN:000001").all()


def test_fixture_provider_snapshot_returns_latest_bar_per_instrument():
    provider = FixtureMarketDataProvider()
    snapshot = provider.get_snapshot(["US:TEST", "CN:000001"])
    assert set(snapshot["instrument_id"]) == {"US:TEST", "CN:000001"}
    assert len(snapshot) == 2
