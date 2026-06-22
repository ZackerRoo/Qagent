from datetime import date
from decimal import Decimal

from qagent.backtesting.portfolio import run_portfolio_backtest
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
    assert all(trade.entry_date <= trade.exit_date for trade in result.trades)
    assert all(trade.shares > Decimal("0") for trade in result.trades)
    assert result.data_health["lookahead_guard"] == "signals_generated_before_exits"
    assert result.data_health["portfolio_model"] == "fixed_risk_stop_target_time_exit"
