from datetime import date
from decimal import Decimal

from qagent.monitoring.outcomes import compute_forward_returns, compute_opportunity_outcome
from qagent.monitoring.portfolio import PositionInput, analyze_position_risk
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.storage.repository import OpportunitySnapshotRecord


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


def test_analyze_position_risk_inside_plan():
    position = PositionInput(
        instrument_id="US:TEST",
        shares="10",
        entry_price="82.00",
        entry_date="2026-03-31",
        strategy_tag="breakout",
        initial_stop="78.72",
        target_1="88.56",
    )

    risk = analyze_position_risk(position, current_price="82.00")

    assert risk.unrealized_return_pct == 0
    assert risk.stop_distance_pct == 4.0
    assert risk.target_1_distance_pct == 8.0
    assert risk.status == "inside_plan"


def _snapshot(signal_date: date, target_1: Decimal | None = Decimal("60.20")):
    return OpportunitySnapshotRecord(
        snapshot_id="snap-1",
        run_id="run-1",
        card_id="card-1",
        instrument_id="US:TEST",
        market="US",
        status="setup_ready",
        signal_date=signal_date,
        latest_close=Decimal("57.35"),
        primary_strategy_id="breakout_volume_confirmation",
        score=Decimal("0.82"),
        strategy_score=Decimal("0.88"),
        rank_score=Decimal("0.76"),
        trigger_price=Decimal("57.35"),
        initial_stop=Decimal("55.00"),
        target_1=target_1,
        card={"instrument_id": "US:TEST"},
    )


def test_compute_opportunity_outcome_marks_target_hit():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))

    outcome = compute_opportunity_outcome(
        _snapshot(date(2026, 1, 30)),
        bars,
        horizons=(5, 10),
    )

    assert outcome.snapshot_id == "snap-1"
    assert outcome.instrument_id == "US:TEST"
    assert outcome.primary_strategy_id == "breakout_volume_confirmation"
    assert outcome.outcome_status == "target_1_hit"
    assert outcome.return_5d is not None
    assert outcome.return_10d is not None
    assert outcome.max_runup_pct >= 4
    assert outcome.max_drawdown_pct <= 0


def test_compute_opportunity_outcome_marks_pending_when_future_bars_are_unavailable():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))

    outcome = compute_opportunity_outcome(
        _snapshot(date(2026, 3, 31)),
        bars,
        horizons=(5, 10),
    )

    assert outcome.outcome_status == "pending"
    assert outcome.return_5d is None
    assert outcome.return_10d is None
    assert outcome.max_drawdown_pct is None
    assert outcome.max_runup_pct is None
