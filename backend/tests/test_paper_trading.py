import json
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from qagent.jobs.daily_scan import run_daily_scan
from qagent.paper_trading.engine import (
    build_paper_daily_report,
    build_paper_ledger,
    build_paper_validation,
    seed_paper_trades_from_snapshots,
    update_paper_trades,
)
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.storage.paper import PaperTradingRepository
from qagent.storage.tables import OpportunitySnapshotRow, ScanRunRow

from test_state_repository import make_repo


class SingleDayCnProvider:
    name = "single_day_cn"
    last_errors: list[str] = []

    def get_daily_bars(self, instrument_ids, start, end):
        return pd.DataFrame(
            [
                {
                    "instrument_id": instrument_ids[0],
                    "trade_date": date(2026, 7, 1),
                    "open": Decimal("2.00"),
                    "high": Decimal("2.20"),
                    "low": Decimal("1.98"),
                    "close": Decimal("2.10"),
                    "volume": 100000,
                }
            ]
        )

    def get_snapshot(self, instrument_ids):
        return pd.DataFrame()


class MinuteCnProvider:
    name = "minute_cn"
    last_errors: list[str] = []

    def __init__(self, rows):
        self.rows = rows

    def get_daily_bars(self, instrument_ids, start, end):
        return pd.DataFrame()

    def get_minute_bars(self, instrument_ids, start, end):
        return pd.DataFrame(self.rows)

    def get_snapshot(self, instrument_ids):
        return pd.DataFrame()


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


def test_a_share_paper_trade_does_not_backfill_entry_on_signal_date(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    paper_repo.create_trade(
        source_snapshot_id="manual-CN-588850",
        provider="free",
        instrument_id="CN:588850",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("2.10"),
        initial_stop=Decimal("2.00"),
        target_1=Decimal("2.30"),
        rank_score=Decimal("0.91"),
    )

    result = update_paper_trades(
        paper_repo,
        provider=SingleDayCnProvider(),
        as_of=datetime(2026, 7, 2, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trade = paper_repo.list_trades()[0]

    assert result.data_health["paper_execution_session"] == "regular"
    assert result.data_health["paper_execution_fills_deferred"] == "1"
    assert trade.status == "pending"
    assert trade.entry_date is None
    assert "等待下个交易日" in trade.notes


def test_a_share_paper_trade_opens_from_post_signal_minute_cross(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot_id = _insert_cn_snapshot(
        repo,
        instrument_id="CN:688052",
        created_at=datetime(2026, 7, 2, 3, 38, tzinfo=timezone.utc),
        trigger_price=Decimal("10.00"),
        no_chase_above=Decimal("10.50"),
    )
    paper_repo.create_trade(
        source_snapshot_id=snapshot_id,
        provider="free",
        instrument_id="CN:688052",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 2),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.50"),
        target_1=Decimal("11.00"),
        rank_score=Decimal("0.91"),
    )
    provider = MinuteCnProvider(
        [
            {
                "instrument_id": "CN:688052",
                "timestamp": datetime(2026, 7, 2, 11, 30),
                "open": Decimal("9.80"),
                "high": Decimal("10.20"),
                "low": Decimal("9.70"),
                "close": Decimal("10.10"),
                "volume": 1000,
                "provider": "test_minute",
            },
            {
                "instrument_id": "CN:688052",
                "timestamp": datetime(2026, 7, 2, 13, 5),
                "open": Decimal("9.90"),
                "high": Decimal("10.20"),
                "low": Decimal("9.85"),
                "close": Decimal("10.10"),
                "volume": 1000,
                "provider": "test_minute",
            },
        ]
    )

    result = update_paper_trades(
        paper_repo,
        provider=provider,
        as_of=datetime(2026, 7, 2, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trade = paper_repo.list_trades()[0]

    assert result.data_health["paper_minute_checked"] == "1"
    assert result.data_health["paper_minute_rows"] == "2"
    assert trade.status == "open"
    assert trade.entry_date == date(2026, 7, 2)
    assert trade.entry_price == Decimal("10.0000")
    assert "分钟线" in trade.notes


def test_a_share_paper_trade_marks_missed_when_minute_price_gaps_above_chase_limit(
    tmp_path,
):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot_id = _insert_cn_snapshot(
        repo,
        instrument_id="CN:002747",
        created_at=datetime(2026, 7, 2, 3, 36, tzinfo=timezone.utc),
        trigger_price=Decimal("10.00"),
        no_chase_above=Decimal("10.30"),
    )
    paper_repo.create_trade(
        source_snapshot_id=snapshot_id,
        provider="free",
        instrument_id="CN:002747",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 2),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.50"),
        target_1=Decimal("11.00"),
        rank_score=Decimal("0.91"),
    )
    provider = MinuteCnProvider(
        [
            {
                "instrument_id": "CN:002747",
                "timestamp": datetime(2026, 7, 2, 13, 1),
                "open": Decimal("11.00"),
                "high": Decimal("11.20"),
                "low": Decimal("10.80"),
                "close": Decimal("11.10"),
                "volume": 1000,
                "provider": "test_minute",
            }
        ]
    )

    update_paper_trades(
        paper_repo,
        provider=provider,
        as_of=datetime(2026, 7, 2, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trade = paper_repo.list_trades()[0]

    assert trade.status == "missed_entry"
    assert trade.entry_date is None
    assert trade.realized_return_pct == 0.0
    assert "超过追高上限" in trade.notes


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


def test_paper_daily_report_summarizes_actions_and_benchmark_excess(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    new_trade = paper_repo.create_trade(
        source_snapshot_id="daily-new",
        provider="fixture",
        instrument_id="CN:000001",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 3),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9.5"),
        target_1=Decimal("11"),
        rank_score=Decimal("0.80"),
    )
    open_trade = paper_repo.create_trade(
        source_snapshot_id="daily-open",
        provider="fixture",
        instrument_id="CN:688059",
        strategy_id="factor_rotation_watch",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("20"),
        initial_stop=Decimal("19"),
        target_1=Decimal("23"),
        rank_score=Decimal("0.86"),
    )
    paper_repo.update_trade(
        open_trade.trade_id,
        status="open",
        entry_date=date(2026, 7, 2),
        entry_price=Decimal("20"),
        latest_date=date(2026, 7, 3),
        latest_price=Decimal("21"),
        unrealized_return_pct=Decimal("5"),
        holding_days=1,
    )
    target_trade = paper_repo.create_trade(
        source_snapshot_id="daily-target",
        provider="fixture",
        instrument_id="CN:588200",
        strategy_id="sector_rotation_relative_strength",
        signal_date=date(2026, 6, 28),
        trigger_price=Decimal("5"),
        initial_stop=Decimal("4.8"),
        target_1=Decimal("5.5"),
        rank_score=Decimal("0.90"),
    )
    paper_repo.update_trade(
        target_trade.trade_id,
        status="target_1_hit",
        entry_date=date(2026, 6, 30),
        entry_price=Decimal("5"),
        exit_date=date(2026, 7, 3),
        exit_price=Decimal("5.5"),
        latest_date=date(2026, 7, 3),
        latest_price=Decimal("5.5"),
        realized_return_pct=Decimal("10"),
        holding_days=3,
    )

    trades = paper_repo.list_trades(limit=20)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 3))
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 3),
        benchmark_items=[
            {
                "name": "沪深300",
                "return_pct": 1.2,
                "excess_return_pct": ledger.summary.total_return_pct - 1.2,
            }
        ],
    )

    assert report.report_date == date(2026, 7, 3)
    assert report.summary.new_opportunities == 1
    assert report.summary.open_positions == 1
    assert report.summary.closed_today == 1
    assert report.summary.target_hits_today == 1
    assert report.benchmark.items[0].name == "沪深300"
    assert report.next_trade_day_focus
    assert any(item.instrument_id == new_trade.instrument_id for item in report.new_opportunities)
    assert any(item.instrument_id == open_trade.instrument_id for item in report.holdings)
    assert any(item.instrument_id == target_trade.instrument_id for item in report.closed_today)


def _insert_cn_snapshot(
    repo,
    *,
    instrument_id: str,
    created_at: datetime,
    trigger_price: Decimal,
    no_chase_above: Decimal,
) -> str:
    snapshot_id = f"scan-minute:{instrument_id}"
    card = {
        "instrument_id": instrument_id,
        "instrument_label": instrument_id,
        "entry_plan": {
            "trigger_price": str(trigger_price),
            "no_chase_above": str(no_chase_above),
        },
    }
    with repo.session_factory() as session:
        session.add(
            ScanRunRow(
                run_id="scan-minute",
                provider="free",
                mode="full_market",
                symbols=json.dumps([instrument_id]),
                scanned=1,
                cards=1,
                data_health="{}",
                created_at=created_at,
            )
        )
        session.add(
            OpportunitySnapshotRow(
                snapshot_id=snapshot_id,
                run_id="scan-minute",
                card_id=f"card-{instrument_id}",
                instrument_id=instrument_id,
                market="CN",
                status="setup_ready",
                signal_date=date(2026, 7, 2),
                latest_close=trigger_price,
                primary_strategy_id="trend_momentum_stage2",
                score=Decimal("0.90"),
                strategy_score=Decimal("0.90"),
                rank_score=Decimal("0.90"),
                trigger_price=trigger_price,
                initial_stop=Decimal("9.50"),
                target_1=Decimal("11.00"),
                card_json=json.dumps(card, sort_keys=True),
                created_at=created_at,
            )
        )
        session.commit()
    return snapshot_id
