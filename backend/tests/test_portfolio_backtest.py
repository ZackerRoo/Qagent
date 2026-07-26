from datetime import date
from decimal import Decimal

import pandas as pd

from qagent.backtesting.engine import BacktestSignal
from qagent.backtesting.portfolio import (
    ADAPTIVE_CONFIRMATION_EXECUTION_PROFILE,
    _candidate_from_signal,
    _size_trade,
    run_portfolio_backtest,
    run_signal_portfolio_backtest,
)
from qagent.backtesting.sensitivity import build_parameter_sensitivity
from qagent.providers.fixtures import FixtureMarketDataProvider


def test_run_portfolio_backtest_returns_trades_equity_and_summary():
    result = run_portfolio_backtest(
        instrument_ids=["US:TEST", "CN:000001"],
        provider=FixtureMarketDataProvider(),
        start=date(2026, 1, 30),
        end=date(2026, 3, 20),
        step_days=5,
        initial_capital=Decimal("100000"),
        risk_per_trade_pct=Decimal("1"),
        max_positions=2,
        transaction_cost_bps=Decimal("5"),
        slippage_bps=Decimal("5"),
    )

    assert result.summary.provider == "fixture"
    assert result.summary.initial_capital == Decimal("100000")
    assert result.summary.trade_count > 0
    assert result.summary.final_equity != Decimal("100000")
    assert result.summary.total_return_pct is not None
    assert result.summary.max_drawdown_pct is not None
    assert result.summary.win_rate is not None
    assert result.summary.profit_factor is None or result.summary.profit_factor >= 0
    assert result.trades
    assert result.equity_curve[0].equity == Decimal("100000")
    assert result.equity_curve[-1].equity == result.summary.final_equity
    assert result.monthly_returns
    assert result.monthly_returns[0].month == "2026-01"
    assert result.monthly_returns[-1].ending_equity == result.summary.final_equity
    assert all(trade.entry_date <= trade.exit_date for trade in result.trades)
    assert all(trade.shares > Decimal("0") for trade in result.trades)
    assert result.data_health["lookahead_guard"] == "signals_generated_before_exits"
    assert result.data_health["portfolio_model"] == "fixed_risk_stop_target_time_exit"


def test_signal_portfolio_backtest_never_reads_or_realizes_beyond_end_date():
    class AuditedFrameProvider:
        name = "audited-frame"

        def __init__(self, frame):
            self.frame = frame
            self.requested_windows = []

        def get_daily_bars(self, instrument_ids, start, end):
            self.requested_windows.append((start, end))
            return self.frame.loc[
                self.frame["instrument_id"].isin(instrument_ids)
                & (self.frame["trade_date"] >= start)
                & (self.frame["trade_date"] <= end)
            ].copy()

    signal = BacktestSignal(
        snapshot_id="hard-cutoff",
        instrument_id="CN:000001",
        signal_date=date(2025, 12, 29),
        primary_strategy_id="trend_momentum_stage2",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("11"),
        outcome_status="pending",
    )
    provider = AuditedFrameProvider(
        pd.DataFrame(
            [
                {
                    "instrument_id": "CN:000001",
                    "trade_date": trade_date,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 1_000_000,
                }
                for trade_date, open_price, high, low, close in (
                    (date(2025, 12, 29), 9.8, 9.9, 9.7, 9.8),
                    (date(2025, 12, 30), 10.0, 10.3, 9.8, 10.2),
                    (date(2025, 12, 31), 10.2, 10.4, 9.8, 10.1),
                    (date(2026, 1, 5), 11.0, 11.2, 10.8, 11.0),
                )
            ]
        )
    )

    result = run_signal_portfolio_backtest(
        signals=[signal],
        instrument_ids=["CN:000001"],
        provider=provider,
        start=date(2025, 12, 29),
        end=date(2025, 12, 31),
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
    )

    assert provider.requested_windows == [
        (date(2025, 12, 29), date(2025, 12, 31))
    ]
    assert result.trades == []
    assert result.summary.final_equity == Decimal("100000")
    assert result.data_health["history_cutoff"] == "hard_end_date_no_future_bars"


def test_portfolio_does_not_use_future_exit_liquidity_to_resize_entry():
    signal = BacktestSignal(
        snapshot_id="thin-exit",
        instrument_id="CN:000001",
        signal_date=date(2025, 1, 1),
        primary_strategy_id="trend_momentum_stage2",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("11"),
        outcome_status="pending",
    )
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 1),
                "open": 9.8,
                "high": 9.9,
                "low": 9.7,
                "close": 9.8,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 2),
                "open": 10,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 3),
                "open": 11,
                "high": 11.2,
                "low": 10.8,
                "close": 11,
                "volume": 100,
            },
        ]
    )
    candidate = _candidate_from_signal(
        signal,
        bars,
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
    )

    assert candidate is not None
    assert candidate.max_executable_shares == Decimal("1000000")
    assert candidate.exit_executable_shares == Decimal("100")
    assert (
        _size_trade(
            candidate,
            equity=Decimal("100000"),
            risk_per_trade_pct=Decimal("1"),
            max_positions=5,
            transaction_cost_bps=Decimal("0"),
        )
        is None
    )


def test_portfolio_fee_multiplier_reduces_returns_under_cost_stress():
    common = {
        "instrument_ids": ["US:TEST", "CN:000001"],
        "provider": FixtureMarketDataProvider(),
        "start": date(2026, 1, 30),
        "end": date(2026, 3, 20),
        "step_days": 5,
        "initial_capital": Decimal("100000"),
        "max_positions": 2,
        "slippage_bps": Decimal("5"),
    }

    base = run_portfolio_backtest(**common, fee_multiplier=Decimal("1"))
    stress = run_portfolio_backtest(**common, fee_multiplier=Decimal("2"))

    assert base.trades
    assert stress.trades
    assert sum(item.costs for item in stress.trades) > sum(item.costs for item in base.trades)
    assert stress.summary.total_return_pct < base.summary.total_return_pct
    assert stress.data_health["fee_multiplier"] == "2"


def test_cn_portfolio_candidate_skips_limit_up_entry_and_applies_t_plus_one_exit():
    signal = BacktestSignal(
        snapshot_id="test-cn",
        instrument_id="CN:000001",
        signal_date=date(2026, 1, 1),
        primary_strategy_id="breakout_volume_confirmation",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10.50"),
        initial_stop=Decimal("10.00"),
        target_1=Decimal("11.00"),
        outcome_status="pending",
    )
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 1),
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 2),
                "open": 11.0,
                "high": 11.0,
                "low": 11.0,
                "close": 11.0,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 5),
                "open": 10.4,
                "high": 11.2,
                "low": 9.8,
                "close": 10.8,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 6),
                "open": 10.9,
                "high": 11.2,
                "low": 10.7,
                "close": 11.1,
                "volume": 1_000_000,
            },
        ]
    )

    candidate = _candidate_from_signal(
        signal,
        bars,
        slippage_bps=Decimal("0"),
        max_entry_wait_days=4,
        max_holding_days=3,
    )

    assert candidate is not None
    assert candidate.entry_date == date(2026, 1, 5)
    assert candidate.exit_date == date(2026, 1, 6)


def test_portfolio_candidate_rejects_fill_above_no_chase_limit():
    signal = BacktestSignal(
        snapshot_id="test-no-chase",
        instrument_id="CN:000001",
        signal_date=date(2026, 1, 1),
        primary_strategy_id="trend_momentum_stage2",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.50"),
        target_1=Decimal("11.00"),
        no_chase_above=Decimal("10.30"),
        outcome_status="pending",
    )
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 1),
                "open": 9.8,
                "high": 9.9,
                "low": 9.7,
                "close": 9.8,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 2),
                "open": 10.50,
                "high": 10.80,
                "low": 10.40,
                "close": 10.60,
                "volume": 1_000_000,
            },
        ]
    )

    candidate = _candidate_from_signal(
        signal,
        bars,
        slippage_bps=Decimal("0"),
        max_entry_wait_days=1,
        max_holding_days=3,
    )

    assert candidate is None


def test_portfolio_candidate_rejects_fill_at_or_above_target():
    signal = BacktestSignal(
        snapshot_id="test-target-guard",
        instrument_id="US:TEST",
        signal_date=date(2026, 1, 1),
        primary_strategy_id="trend_momentum_stage2",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.50"),
        target_1=Decimal("11.00"),
        outcome_status="pending",
    )
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 1),
                "open": 9.8,
                "high": 9.9,
                "low": 9.7,
                "close": 9.8,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 2),
                "open": 11.00,
                "high": 11.20,
                "low": 10.90,
                "close": 11.10,
                "volume": 1_000_000,
            },
        ]
    )

    candidate = _candidate_from_signal(
        signal,
        bars,
        slippage_bps=Decimal("0"),
        max_entry_wait_days=1,
        max_holding_days=3,
    )

    assert candidate is None


def test_adaptive_execution_waits_for_close_confirmation_and_uses_next_open():
    signal = BacktestSignal(
        snapshot_id="adaptive-confirmation",
        instrument_id="US:TEST",
        signal_date=date(2026, 1, 1),
        primary_strategy_id="trend_momentum_stage2",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.90"),
        target_1=Decimal("11.00"),
        no_chase_above=Decimal("10.50"),
        outcome_status="pending",
    )
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 1),
                "open": 9.8,
                "high": 9.9,
                "low": 9.7,
                "close": 9.8,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 2),
                "open": 9.9,
                "high": 10.2,
                "low": 9.7,
                "close": 9.85,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 5),
                "open": 9.9,
                "high": 10.4,
                "low": 9.8,
                "close": 10.2,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 6),
                "open": 10.25,
                "high": 10.6,
                "low": 10.0,
                "close": 10.4,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 7),
                "open": 10.5,
                "high": 12.0,
                "low": 10.4,
                "close": 11.5,
                "volume": 1_000_000,
            },
        ]
    )

    candidate = _candidate_from_signal(
        signal,
        bars,
        slippage_bps=Decimal("0"),
        max_entry_wait_days=4,
        max_holding_days=4,
        execution_profile=ADAPTIVE_CONFIRMATION_EXECUTION_PROFILE,
    )

    assert candidate is not None
    assert candidate.entry_date == date(2026, 1, 6)
    assert candidate.entry_price == Decimal("10.25")
    assert candidate.stop_price == Decimal("9.4500")
    assert candidate.exit_reason == "target_1_hit"
    assert candidate.exit_date == date(2026, 1, 7)
    assert candidate.exit_price == Decimal("11.8500")


def test_adaptive_breakeven_stop_only_applies_from_next_session():
    signal = BacktestSignal(
        snapshot_id="adaptive-breakeven",
        instrument_id="US:TEST",
        signal_date=date(2026, 1, 1),
        primary_strategy_id="trend_momentum_stage2",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.80"),
        target_1=Decimal("12.00"),
        no_chase_above=Decimal("10.50"),
        outcome_status="pending",
    )
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 1),
                "open": 9.8,
                "high": 9.9,
                "low": 9.7,
                "close": 9.8,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 2),
                "open": 9.9,
                "high": 10.3,
                "low": 9.8,
                "close": 10.2,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 5),
                "open": 10.1,
                "high": 11.1,
                "low": 9.9,
                "close": 11.0,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 6),
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 9.9,
                "volume": 1_000_000,
            },
        ]
    )

    candidate = _candidate_from_signal(
        signal,
        bars,
        slippage_bps=Decimal("0"),
        max_entry_wait_days=3,
        max_holding_days=4,
        execution_profile=ADAPTIVE_CONFIRMATION_EXECUTION_PROFILE,
    )

    assert candidate is not None
    assert candidate.entry_date == date(2026, 1, 5)
    assert candidate.entry_price == Decimal("10.10")
    assert candidate.stop_price == Decimal("9.3000")
    assert candidate.exit_reason == "stopped"
    assert candidate.exit_date == date(2026, 1, 6)
    assert candidate.exit_price == Decimal("10.0")


def test_cn_portfolio_sizing_uses_round_lots():
    signal = BacktestSignal(
        snapshot_id="test-cn",
        instrument_id="CN:000001",
        signal_date=date(2026, 1, 1),
        primary_strategy_id="breakout_volume_confirmation",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10.50"),
        initial_stop=Decimal("10.00"),
        target_1=Decimal("11.00"),
        outcome_status="pending",
    )
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 1),
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 2),
                "open": 10.2,
                "high": 10.8,
                "low": 10.1,
                "close": 10.7,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 5),
                "open": 10.8,
                "high": 11.2,
                "low": 10.7,
                "close": 11.1,
                "volume": 1_000_000,
            },
        ]
    )
    candidate = _candidate_from_signal(
        signal,
        bars,
        slippage_bps=Decimal("0"),
        max_entry_wait_days=3,
        max_holding_days=2,
    )

    trade = _size_trade(
        candidate,
        equity=Decimal("100000"),
        risk_per_trade_pct=Decimal("1"),
        max_positions=5,
        transaction_cost_bps=Decimal("5"),
    )

    assert trade is not None
    assert trade.shares % Decimal("100") == 0


def test_portfolio_marks_to_market_daily_and_captures_open_position_drawdown():
    class DailyFrameProvider:
        name = "daily-frame"

        def __init__(self, frame):
            self.frame = frame

        def get_daily_bars(self, instrument_ids, start, end):
            return self.frame.loc[
                self.frame["instrument_id"].isin(instrument_ids)
                & (self.frame["trade_date"] >= start)
                & (self.frame["trade_date"] <= end)
            ].copy()

    bars = pd.DataFrame(
        [
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 1),
                "open": 9,
                "high": 9,
                "low": 9,
                "close": 9,
                "volume": 10_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 2),
                "open": 10,
                "high": 10.5,
                "low": 9.5,
                "close": 10,
                "volume": 10_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 5),
                "open": 8,
                "high": 8.5,
                "low": 7,
                "close": 8,
                "volume": 10_000,
            },
            {
                "instrument_id": "US:TEST",
                "trade_date": date(2026, 1, 6),
                "open": 12,
                "high": 12,
                "low": 11,
                "close": 12,
                "volume": 10_000,
            },
        ]
    )
    signal = BacktestSignal(
        snapshot_id="daily-mark",
        instrument_id="US:TEST",
        signal_date=date(2026, 1, 1),
        primary_strategy_id="trend_momentum_stage2",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("5"),
        target_1=None,
        outcome_status="pending",
    )

    result = run_signal_portfolio_backtest(
        signals=[signal],
        instrument_ids=["US:TEST"],
        provider=DailyFrameProvider(bars),
        start=date(2026, 1, 1),
        end=date(2026, 1, 6),
        initial_capital=Decimal("10000"),
        risk_per_trade_pct=Decimal("10"),
        max_positions=1,
        transaction_cost_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        max_holding_days=3,
    )

    points = {point.date: point for point in result.equity_curve}
    drawdown = points[date(2026, 1, 5)]
    assert [point.date for point in result.equity_curve] == list(bars["trade_date"])
    assert all(point.cash + point.market_value == point.equity for point in result.equity_curve)
    assert drawdown.cash == Decimal("8000.00")
    assert drawdown.market_value == Decimal("1600.00")
    assert drawdown.equity == Decimal("9600.00")
    assert drawdown.drawdown_pct == -4.0
    assert result.summary.max_drawdown_pct == -4.0
    assert result.summary.final_equity == Decimal("10400.00")
    assert result.equity_curve[-1].market_value == Decimal("0.00")


def test_parameter_sensitivity_scores_stop_target_and_holding_grid():
    signals = [
        BacktestSignal(
            snapshot_id="win",
            instrument_id="CN:000001",
            signal_date=date(2026, 1, 1),
            primary_strategy_id="trend_momentum_stage2",
            status="setup_ready",
            rank_score=Decimal("0.82"),
            trigger_price=Decimal("10"),
            initial_stop=Decimal("9.5"),
            target_1=Decimal("11"),
            outcome_status="target_1_hit",
            return_5d=3.0,
            return_10d=7.0,
            return_20d=12.0,
            max_drawdown_pct=-2.0,
            max_runup_pct=8.0,
        ),
        BacktestSignal(
            snapshot_id="loss",
            instrument_id="CN:000002",
            signal_date=date(2026, 1, 2),
            primary_strategy_id="trend_momentum_stage2",
            status="setup_ready",
            rank_score=Decimal("0.76"),
            trigger_price=Decimal("20"),
            initial_stop=Decimal("19"),
            target_1=Decimal("22"),
            outcome_status="stop_hit",
            return_5d=-4.0,
            return_10d=-6.0,
            return_20d=-9.0,
            max_drawdown_pct=-7.0,
            max_runup_pct=1.0,
        ),
    ]

    result = build_parameter_sensitivity(
        signals,
        stop_loss_pcts=[3.0, 6.0],
        target_pcts=[5.0, 10.0],
        hold_days=[5, 10],
    )

    assert result.summary.scenario_count == 8
    assert result.summary.sample_count == 2
    assert result.recommended is not None
    assert result.recommended.stop_loss_pct == 3.0
    assert result.recommended.target_pct == 10.0
    assert result.recommended.hold_days == 10
    assert result.recommended.avg_return_pct == 2.0
    assert result.recommended.max_drawdown_pct == -3.0
    assert result.grid[0].is_recommended is True

    positive_only = build_parameter_sensitivity(
        [signals[0]],
        stop_loss_pcts=[3.0],
        target_pcts=[10.0],
        hold_days=[5],
    )
    assert positive_only.recommended is not None
    assert positive_only.recommended.max_drawdown_pct == 0.0
