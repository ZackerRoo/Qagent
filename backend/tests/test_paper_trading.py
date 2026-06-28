from datetime import date
from decimal import Decimal

from qagent.jobs.daily_scan import run_daily_scan
from qagent.paper_trading.engine import (
    build_paper_ledger,
    seed_paper_trades_from_snapshots,
    update_paper_trades,
)
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.storage.paper import PaperTradingRepository

from test_state_repository import make_repo


def test_paper_trading_seeds_unique_trades_from_opportunity_snapshots(tmp_path):
    repo = make_repo(tmp_path)
    scan = run_daily_scan(["US:TEST"], FixtureMarketDataProvider())
    repo.save_scan_run(provider="fixture", mode="fixture", symbols=["US:TEST"], result=scan)
    snapshots = repo.list_opportunity_snapshots(limit=5)
    paper_repo = PaperTradingRepository(repo.session_factory)

    first = seed_paper_trades_from_snapshots(paper_repo, snapshots, provider="fixture")
    second = seed_paper_trades_from_snapshots(paper_repo, snapshots, provider="fixture")
    trades = paper_repo.list_trades()

    assert first.created == 1
    assert second.created == 0
    assert trades[0].source_snapshot_id == snapshots[0].snapshot_id
    assert trades[0].instrument_id == "US:TEST"
    assert trades[0].status == "pending"
    assert trades[0].trigger_price == Decimal("83.2000")
    assert trades[0].initial_stop == Decimal("80.9000")
    assert trades[0].target_1 == Decimal("89.7600")


def test_update_paper_trades_marks_target_hit_from_future_bars(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    paper_repo.create_trade(
        source_snapshot_id="manual-US-TEST",
        provider="fixture",
        instrument_id="US:TEST",
        strategy_id="pead_earnings_drift",
        signal_date=date(2026, 3, 20),
        trigger_price=Decimal("70.80"),
        initial_stop=Decimal("67.00"),
        target_1=Decimal("74.00"),
        rank_score=Decimal("0.91"),
    )

    result = update_paper_trades(
        paper_repo,
        provider=FixtureMarketDataProvider(),
        max_holding_days=20,
    )
    trade = paper_repo.list_trades()[0]

    assert result.summary.total == 1
    assert result.summary.target_hit_count == 1
    assert result.summary.win_rate == 1.0
    assert trade.status == "target_1_hit"
    assert trade.entry_price == Decimal("70.8000")
    assert trade.exit_price == Decimal("74.0000")
    assert trade.realized_return_pct == 4.5198


def test_build_paper_ledger_summarizes_cash_equity_and_recommendation_outcomes(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    winning = paper_repo.create_trade(
        source_snapshot_id="ledger-win",
        provider="fixture",
        instrument_id="CN:688059",
        strategy_id="breakout_volume_confirmation",
        signal_date=date(2026, 6, 1),
        trigger_price=Decimal("100"),
        initial_stop=Decimal("95"),
        target_1=Decimal("110"),
        rank_score=Decimal("0.90"),
    )
    losing = paper_repo.create_trade(
        source_snapshot_id="ledger-loss",
        provider="fixture",
        instrument_id="CN:000001",
        strategy_id="pullback_to_rising_20dma",
        signal_date=date(2026, 6, 2),
        trigger_price=Decimal("100"),
        initial_stop=Decimal("95"),
        target_1=Decimal("110"),
        rank_score=Decimal("0.70"),
    )
    open_trade = paper_repo.create_trade(
        source_snapshot_id="ledger-open",
        provider="fixture",
        instrument_id="CN:159915",
        strategy_id="sector_rotation_relative_strength",
        signal_date=date(2026, 6, 3),
        trigger_price=Decimal("50"),
        initial_stop=Decimal("47"),
        target_1=Decimal("58"),
        rank_score=Decimal("0.80"),
    )
    pending = paper_repo.create_trade(
        source_snapshot_id="ledger-pending",
        provider="fixture",
        instrument_id="CN:300750",
        strategy_id="breakout_volume_confirmation",
        signal_date=date(2026, 6, 4),
        trigger_price=Decimal("200"),
        initial_stop=Decimal("190"),
        target_1=Decimal("220"),
        rank_score=Decimal("0.60"),
    )
    paper_repo.update_trade(
        winning.trade_id,
        status="target_1_hit",
        entry_date=date(2026, 6, 5),
        entry_price=Decimal("100"),
        exit_date=date(2026, 6, 10),
        exit_price=Decimal("110"),
        latest_date=date(2026, 6, 10),
        latest_price=Decimal("110"),
        realized_return_pct=Decimal("10"),
        holding_days=5,
    )
    paper_repo.update_trade(
        losing.trade_id,
        status="stopped",
        entry_date=date(2026, 6, 6),
        entry_price=Decimal("100"),
        exit_date=date(2026, 6, 12),
        exit_price=Decimal("95"),
        latest_date=date(2026, 6, 12),
        latest_price=Decimal("95"),
        realized_return_pct=Decimal("-5"),
        holding_days=6,
    )
    paper_repo.update_trade(
        open_trade.trade_id,
        status="open",
        entry_date=date(2026, 6, 7),
        entry_price=Decimal("50"),
        latest_date=date(2026, 6, 14),
        latest_price=Decimal("55"),
        unrealized_return_pct=Decimal("10"),
        holding_days=7,
    )

    ledger = build_paper_ledger(
        paper_repo.list_trades(limit=10),
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("10"),
    )

    assert ledger.summary.total_trades == 4
    assert ledger.summary.closed_trades == 2
    assert ledger.summary.open_trades == 1
    assert ledger.summary.pending_trades == 1
    assert ledger.summary.total_equity == Decimal("101500.00")
    assert ledger.summary.cash_available == Decimal("90500.00")
    assert ledger.summary.market_value == Decimal("11000.00")
    assert ledger.summary.realized_pnl == Decimal("500.00")
    assert ledger.summary.unrealized_pnl == Decimal("1000.00")
    assert ledger.summary.win_rate == 0.5
    assert ledger.summary.max_drawdown_pct < 0
    assert ledger.curve[-1].equity == Decimal("101500.00")
    assert ledger.items[0].instrument_id == pending.instrument_id
    assert any(item.outcome == "浮盈跟踪" for item in ledger.items)


def test_build_paper_ledger_generates_trade_flows_fees_slippage_and_positions(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    target_trade = paper_repo.create_trade(
        source_snapshot_id="ledger-target-flow",
        provider="fixture",
        instrument_id="CN:688059",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 6, 1),
        trigger_price=Decimal("100"),
        initial_stop=Decimal("95"),
        target_1=Decimal("110"),
        rank_score=Decimal("0.90"),
    )
    open_trade = paper_repo.create_trade(
        source_snapshot_id="ledger-open-flow",
        provider="fixture",
        instrument_id="CN:159915",
        strategy_id="sector_rotation_relative_strength",
        signal_date=date(2026, 6, 2),
        trigger_price=Decimal("50"),
        initial_stop=Decimal("47"),
        target_1=Decimal("58"),
        rank_score=Decimal("0.80"),
    )
    paper_repo.update_trade(
        target_trade.trade_id,
        status="target_1_hit",
        entry_date=date(2026, 6, 3),
        entry_price=Decimal("100"),
        exit_date=date(2026, 6, 10),
        exit_price=Decimal("110"),
        latest_date=date(2026, 6, 10),
        latest_price=Decimal("110"),
        realized_return_pct=Decimal("10"),
        holding_days=7,
    )
    paper_repo.update_trade(
        open_trade.trade_id,
        status="open",
        entry_date=date(2026, 6, 4),
        entry_price=Decimal("50"),
        latest_date=date(2026, 6, 12),
        latest_price=Decimal("55"),
        unrealized_return_pct=Decimal("10"),
        holding_days=8,
    )

    ledger = build_paper_ledger(
        paper_repo.list_trades(limit=10),
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("10"),
        transaction_cost_bps=Decimal("3"),
        slippage_bps=Decimal("5"),
        take_profit_pct=Decimal("50"),
    )

    actions = [transaction.action for transaction in ledger.transactions]
    assert actions.count("entry_buy") == 2
    assert "partial_take_profit" in actions
    assert "final_take_profit" in actions
    assert ledger.summary.total_fees > Decimal("0")
    assert ledger.summary.total_slippage > Decimal("0")
    assert ledger.summary.turnover > Decimal("0")
    assert ledger.summary.cash_available > Decimal("0")
    assert ledger.summary.open_exposure_pct < 100
    assert ledger.positions[0].instrument_id == open_trade.instrument_id
    assert ledger.positions[0].weight_pct > 0
    assert ledger.transactions[0].cash_flow < Decimal("0")
    assert ledger.transactions[-1].cash_balance == ledger.summary.cash_available
