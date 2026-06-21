from qagent.jobs.daily_scan import run_daily_scan
from qagent.providers.fixtures import FixtureMarketDataProvider


def test_daily_scan_returns_cards_for_fixture_universe():
    result = run_daily_scan(
        instrument_ids=["US:TEST", "CN:000001"],
        provider=FixtureMarketDataProvider(),
    )
    assert len(result.cards) >= 1
    assert result.data_health["provider"] == "fixture"
    assert result.data_health["scanned"] == "2"
    assert result.data_health["cards"] == str(len(result.cards))


class EmptyProviderWithErrors:
    name = "empty"
    last_errors = ["US:MISS: source unavailable"]

    def get_daily_bars(self, instrument_ids, start, end):
        import pandas as pd

        return pd.DataFrame()

    def get_snapshot(self, instrument_ids):
        import pandas as pd

        return pd.DataFrame()


def test_daily_scan_surfaces_provider_errors():
    result = run_daily_scan(["US:MISS"], provider=EmptyProviderWithErrors(), mode="free")

    assert result.cards == []
    assert result.data_health["errors"] == "US:MISS: source unavailable"
