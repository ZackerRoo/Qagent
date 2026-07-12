from datetime import date
from decimal import Decimal

import pandas as pd

from qagent.backtesting.engine import BacktestSignal
from qagent.backtesting.portfolio import _candidate_from_signal, _size_trade, run_portfolio_backtest
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
    assert sum(item.costs for item in stress.trades) > sum(
        item.costs for item in base.trades
    )
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
