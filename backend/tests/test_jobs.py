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
    assert len(result.items) == 2
    assert result.items[0].instrument_id in {"US:TEST", "CN:000001"}
    assert {item.status for item in result.items} == {"setup_ready"}
    assert result.strategy_health
    assert any(item.strategy_id == "breakout_volume_confirmation" for item in result.strategy_health)
    assert result.items[0].strategies_passed >= 1
    assert result.items[0].strategies_missing_data >= 1
    assert result.cards[0].rank_score >= result.cards[-1].rank_score
    assert result.cards[0].rank_reasons


def test_daily_scan_promotes_pead_when_earnings_fixture_is_available():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
    )

    assert len(result.cards) == 1
    assert result.cards[0].primary_strategy_id == "pead_earnings_drift"
    assert result.cards[0].entry_plan.entry_type == "pead"
    assert result.data_health["strategy_data_provider"] == "fixture_strategy_data"


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
    assert result.items[0].instrument_id == "US:MISS"
    assert result.items[0].status == "no_data"
    assert result.items[0].reason == "No daily bars returned by provider."


class StrategyDataProviderWithRecords:
    name = "strategy_records"
    last_errors = ["fmp: rate limited"]

    def get_earnings_events(self, instrument_ids, start, end):
        return []

    def get_filings(self, instrument_ids, start, end):
        from datetime import date

        from qagent.strategy_data.models import FilingEvent

        return [
            FilingEvent(
                instrument_id=instrument_ids[0],
                form="10-Q",
                filing_date=date(2026, 1, 31),
                accession_number="0001",
                provider="sec_edgar",
            )
        ]

    def get_announcements(self, instrument_ids, start, end):
        from datetime import date

        from qagent.strategy_data.models import AnnouncementEvent

        return [
            AnnouncementEvent(
                instrument_id=instrument_ids[0],
                title="2025年度报告",
                published_at=date(2026, 1, 31),
                provider="cninfo",
            )
        ]


def test_daily_scan_surfaces_strategy_data_counts_and_errors():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
        strategy_data_provider=StrategyDataProviderWithRecords(),
    )

    assert result.data_health["strategy_filings"] == "1"
    assert result.data_health["strategy_announcements"] == "1"
    assert result.data_health["strategy_data_errors"] == "fmp: rate limited"
