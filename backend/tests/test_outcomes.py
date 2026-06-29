from datetime import date
from decimal import Decimal

from qagent.monitoring.outcomes import (
    OpportunityOutcome,
    compute_forward_returns,
    compute_opportunity_outcome,
    summarize_recommendation_closure,
    summarize_strategy_performance,
)
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
    assert risk.action == "hold"
    assert "继续持有" in risk.management_note


def test_analyze_position_risk_triggers_stop_loss_action():
    position = PositionInput(
        instrument_id="CN:000001",
        shares="100",
        entry_price="10.00",
        entry_date="2026-03-01",
        strategy_tag="breakout",
        initial_stop="9.50",
        target_1="11.00",
    )

    risk = analyze_position_risk(position, current_price="9.45", current_date=date(2026, 3, 10))

    assert risk.status == "stop_breached"
    assert risk.action == "stop_loss"
    assert risk.severity == "block"
    assert risk.should_exit is True
    assert "止损" in risk.management_note


def test_analyze_position_risk_flags_near_target_trim_action():
    position = PositionInput(
        instrument_id="CN:000001",
        shares="100",
        entry_price="10.00",
        entry_date="2026-03-01",
        strategy_tag="breakout",
        initial_stop="9.50",
        target_1="11.00",
    )

    risk = analyze_position_risk(position, current_price="10.9", current_date=date(2026, 3, 8))

    assert risk.status == "near_target"
    assert risk.action == "trim_or_raise_stop"
    assert risk.severity == "warning"
    assert "接近目标" in risk.management_note


def test_analyze_position_risk_flags_time_exit_when_trade_stalls():
    position = PositionInput(
        instrument_id="CN:000001",
        shares="100",
        entry_price="10.00",
        entry_date="2026-03-01",
        strategy_tag="breakout",
        initial_stop="9.50",
        target_1="11.00",
    )

    risk = analyze_position_risk(
        position,
        current_price="10.05",
        current_date=date(2026, 4, 10),
        max_holding_days=20,
    )

    assert risk.status == "time_exit_watch"
    assert risk.action == "time_exit"
    assert risk.holding_days == 40
    assert "时间退出" in risk.management_note


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


def test_summarize_strategy_performance_groups_replayed_outcomes():
    outcomes = [
        OpportunityOutcome(
            snapshot_id="snap-1",
            run_id="run-1",
            instrument_id="US:AAA",
            primary_strategy_id="breakout_volume_confirmation",
            signal_date=date(2026, 1, 10),
            outcome_status="target_1_hit",
            return_5d=4.0,
            return_10d=6.0,
            max_drawdown_pct=-1.0,
            max_runup_pct=8.0,
        ),
        OpportunityOutcome(
            snapshot_id="snap-2",
            run_id="run-1",
            instrument_id="US:BBB",
            primary_strategy_id="breakout_volume_confirmation",
            signal_date=date(2026, 1, 11),
            outcome_status="lagging",
            return_5d=-2.0,
            return_10d=-3.0,
            max_drawdown_pct=-5.0,
            max_runup_pct=1.0,
        ),
        OpportunityOutcome(
            snapshot_id="snap-3",
            run_id="run-1",
            instrument_id="US:CCC",
            primary_strategy_id="pead_earnings_drift",
            signal_date=date(2026, 1, 12),
            outcome_status="pending",
        ),
    ]

    performance = summarize_strategy_performance(outcomes)
    by_strategy = {item.strategy_id: item for item in performance}

    breakout = by_strategy["breakout_volume_confirmation"]
    assert breakout.sample_count == 2
    assert breakout.target_hit_rate == 0.5
    assert breakout.positive_rate_10d == 0.5
    assert breakout.avg_return_10d == 1.5
    assert breakout.max_drawdown_pct == -5.0
    assert by_strategy["pead_earnings_drift"].pending_count == 1


def test_summarize_recommendation_closure_tracks_recent_windows():
    outcomes = [
        OpportunityOutcome(
            snapshot_id="snap-1",
            run_id="run-1",
            instrument_id="CN:AAA",
            primary_strategy_id="breakout_volume_confirmation",
            signal_date=date(2026, 6, 20),
            outcome_status="target_1_hit",
            triggered=True,
            return_10d=6.0,
            return_20d=9.0,
            max_drawdown_pct=-1.5,
            max_runup_pct=10.0,
        ),
        OpportunityOutcome(
            snapshot_id="snap-2",
            run_id="run-1",
            instrument_id="CN:BBB",
            primary_strategy_id="breakout_volume_confirmation",
            signal_date=date(2026, 6, 10),
            outcome_status="stopped",
            triggered=True,
            return_10d=-4.0,
            return_20d=-6.0,
            max_drawdown_pct=-8.0,
            max_runup_pct=2.0,
        ),
        OpportunityOutcome(
            snapshot_id="snap-3",
            run_id="run-1",
            instrument_id="CN:CCC",
            primary_strategy_id="pead_earnings_drift",
            signal_date=date(2026, 5, 15),
            outcome_status="working",
            triggered=False,
            return_10d=2.0,
            return_20d=3.0,
            max_drawdown_pct=-2.0,
            max_runup_pct=5.0,
        ),
        OpportunityOutcome(
            snapshot_id="snap-4",
            run_id="run-1",
            instrument_id="CN:DDD",
            primary_strategy_id="pead_earnings_drift",
            signal_date=date(2026, 6, 25),
            outcome_status="pending",
            triggered=False,
        ),
        OpportunityOutcome(
            snapshot_id="snap-old",
            run_id="run-1",
            instrument_id="CN:OLD",
            primary_strategy_id="old_setup",
            signal_date=date(2026, 2, 1),
            outcome_status="target_1_hit",
            triggered=True,
            return_10d=8.0,
            max_drawdown_pct=-1.0,
            max_runup_pct=12.0,
        ),
    ]

    closure = summarize_recommendation_closure(
        outcomes,
        as_of=date(2026, 6, 29),
        windows=(30, 60, 90),
    )

    by_window = {item.window_days: item for item in closure.windows}
    recent = by_window[30]
    assert recent.sample_count == 3
    assert recent.completed_count == 2
    assert recent.pending_count == 1
    assert recent.triggered_count == 2
    assert recent.target_hit_count == 1
    assert recent.stopped_count == 1
    assert recent.win_count == 1
    assert recent.trigger_rate == 0.6667
    assert recent.target_hit_rate == 0.5
    assert recent.stop_rate == 0.5
    assert recent.win_rate == 0.5
    assert recent.avg_return_10d == 1.0
    assert recent.avg_return_20d == 1.5
    assert recent.max_drawdown_pct == -8.0
    assert recent.best_runup_pct == 10.0

    assert by_window[60].sample_count == 4
    assert by_window[90].sample_count == 4
    assert [outcome.instrument_id for outcome in closure.latest_outcomes] == [
        "CN:DDD",
        "CN:AAA",
        "CN:BBB",
        "CN:CCC",
    ]
