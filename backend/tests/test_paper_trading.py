from datetime import date
from decimal import Decimal

from qagent.jobs.daily_scan import run_daily_scan
from qagent.paper_trading.engine import (
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
