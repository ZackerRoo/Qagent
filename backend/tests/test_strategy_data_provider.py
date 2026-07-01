from datetime import date
from decimal import Decimal

from qagent.strategy_data.providers import FixtureStrategyDataProvider


def test_fixture_strategy_data_provider_returns_complete_us_earnings_event():
    provider = FixtureStrategyDataProvider()

    events = provider.get_earnings_events(
        ["US:TEST"],
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
    )

    assert len(events) == 1
    event = events[0]
    assert event.instrument_id == "US:TEST"
    assert event.announcement_date == date(2026, 3, 31)
    assert event.actual_eps == Decimal("1.34")
    assert event.estimated_eps == Decimal("1.05")
    assert event.has_pead_inputs is True


def test_fixture_strategy_data_provider_marks_incomplete_a_share_estimates():
    provider = FixtureStrategyDataProvider()

    events = provider.get_earnings_events(
        ["CN:000001"],
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
    )

    assert len(events) == 1
    assert events[0].instrument_id == "CN:000001"
    assert events[0].has_pead_inputs is False


def test_fixture_strategy_data_provider_returns_fundamental_snapshot():
    provider = FixtureStrategyDataProvider()

    snapshots = provider.get_fundamentals(
        ["US:TEST"],
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
    )

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.instrument_id == "US:TEST"
    assert snapshot.revenue_growth_pct == Decimal("32.5")
    assert snapshot.earnings_growth_pct == Decimal("41.2")
    assert snapshot.net_margin_pct == Decimal("18.0")
    assert snapshot.pe_ratio == Decimal("34.0")
    assert snapshot.peg_ratio == Decimal("0.95")
    assert snapshot.has_growth_inputs is True
    assert snapshot.has_valuation_inputs is True


def test_fixture_strategy_data_provider_returns_analyst_insight():
    provider = FixtureStrategyDataProvider()

    insights = provider.get_analyst_insights(
        ["US:TEST"],
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
    )

    assert len(insights) == 1
    insight = insights[0]
    assert insight.instrument_id == "US:TEST"
    assert insight.target_price == Decimal("64.0")
    assert insight.current_price == Decimal("50.0")
    assert insight.target_upside_pct == Decimal("28.0")
    assert insight.total_ratings == 25
    assert insight.bullish_rating_ratio == Decimal("0.8")
