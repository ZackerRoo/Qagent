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

    def get_fundamentals(self, instrument_ids, start, end):
        return []

    def get_analyst_insights(self, instrument_ids, start, end):
        return []


def test_daily_scan_surfaces_strategy_data_counts_and_errors():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
        strategy_data_provider=StrategyDataProviderWithRecords(),
    )

    assert result.data_health["strategy_filings"] == "1"
    assert result.data_health["strategy_announcements"] == "1"
    assert result.data_health["strategy_data_errors"] == "fmp: rate limited"


class StrategyDataProviderWithFreeFundamentals:
    name = "free_fundamental_records"
    last_errors: list[str] = []

    def get_earnings_events(self, instrument_ids, start, end):
        return []

    def get_filings(self, instrument_ids, start, end):
        return []

    def get_announcements(self, instrument_ids, start, end):
        return []

    def get_fundamentals(self, instrument_ids, start, end):
        from datetime import date

        from qagent.strategy_data.models import FundamentalSnapshot

        return [
            FundamentalSnapshot(
                instrument_id=instrument_ids[0],
                as_of_date=date(2026, 3, 31),
                revenue_growth_pct=32.5,
                earnings_growth_pct=41.2,
                gross_margin_pct=68,
                operating_margin_pct=24.5,
                net_margin_pct=18,
                return_on_equity_pct=29,
                market_cap=8_500_000_000,
                pe_ratio=34,
                forward_pe=28,
                peg_ratio=0.95,
                price_to_sales=7.5,
                provider="fixture_strategy_data",
            )
        ]

    def get_analyst_insights(self, instrument_ids, start, end):
        from datetime import date

        from qagent.strategy_data.models import AnalystInsight

        return [
            AnalystInsight(
                instrument_id=instrument_ids[0],
                as_of_date=date(2026, 3, 31),
                revision_date=date(2026, 3, 31),
                current_eps_estimate=1.35,
                prior_eps_estimate=1.1,
                current_revenue_estimate=152_000_000,
                prior_revenue_estimate=140_000_000,
                target_price=64,
                prior_target_price=54,
                current_price=50,
                strong_buy_count=6,
                buy_count=14,
                hold_count=4,
                sell_count=1,
                provider="fixture_strategy_data",
            )
        ]


def test_daily_scan_passes_free_fundamentals_into_strategy_stack():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
        strategy_data_provider=StrategyDataProviderWithFreeFundamentals(),
    )

    assert result.data_health["strategy_fundamentals"] == "1"
    assert result.data_health["strategy_analyst_insights"] == "1"
    card = result.cards[0]
    strategies = {item.strategy_id: item for item in card.strategy_evaluations}
    assert strategies["tam_adj_peg_growth"].status == "passed"
    assert strategies["bayesian_intrinsic_growth"].status == "passed"
    assert strategies["analyst_revision_momentum"].status == "passed"
