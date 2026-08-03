import json
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from qagent.backtesting.ranking_v3_protocol import RANKING_V3_MODEL_VERSION
from qagent.jobs.daily_scan import run_daily_scan
from qagent.paper_trading.engine import (
    build_paper_daily_report,
    build_paper_ledger as build_official_paper_ledger,
    build_paper_validation,
    paper_snapshot_price_basis_is_consistent,
    seed_paper_trades_from_snapshots,
    summarize_paper_trades,
    update_paper_trades,
)
from qagent.providers.cached import CachedMarketDataProvider
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.paper import (
    PaperTradeAdmissionProof,
    PaperTradeSourceContext,
    PaperTradingRepository,
)
from qagent.storage.repository import OpportunitySnapshotRecord
from qagent.storage.tables import OpportunitySnapshotRow, PaperTradeRow, ScanRunRow

from test_state_repository import make_repo


def build_paper_ledger(*args, **kwargs):
    kwargs.setdefault("reporting_scope", "legacy")
    return build_official_paper_ledger(*args, **kwargs)


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


class EmptyMinuteAndDailyProvider:
    name = "empty_live"
    last_errors: list[str] = ["live source unavailable"]

    def get_daily_bars(self, instrument_ids, start, end):
        return pd.DataFrame()

    def get_minute_bars(self, instrument_ids, start, end):
        return pd.DataFrame()

    def get_snapshot(self, instrument_ids):
        return pd.DataFrame()


class DailyRowsProvider:
    name = "daily_rows"
    last_errors: list[str] = []

    def __init__(self, rows):
        self.rows = rows

    def get_daily_bars(self, instrument_ids, start, end):
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
    assert trades[0].target_1 == Decimal("88.7600")


def test_low_level_seed_cannot_bypass_unreleased_ranking_v3(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot = OpportunitySnapshotRecord(
        snapshot_id="ranking-v3-unreleased-direct",
        run_id="ranking-v3-unreleased-run",
        card_id="ranking-v3-unreleased-card",
        instrument_id="US:TEST",
        market="US",
        status="setup_ready",
        signal_date=date(2026, 7, 13),
        latest_close=Decimal("83.00"),
        primary_strategy_id="trend_momentum_stage2",
        score=Decimal("0.90"),
        strategy_score=Decimal("0.90"),
        rank_score=Decimal("0.90"),
        trigger_price=Decimal("83.20"),
        initial_stop=Decimal("80.90"),
        target_1=Decimal("88.76"),
        card={
            "ranking_v3": {
                "selection_source": "ranking_v3",
                "model_version": RANKING_V3_MODEL_VERSION,
                "deployment_scope": "paper",
                "official_release_allowed": True,
            }
        },
    )

    without_authority = seed_paper_trades_from_snapshots(
        paper_repo,
        [snapshot],
        provider="fixture",
    )
    with_authority = seed_paper_trades_from_snapshots(
        paper_repo,
        [snapshot.model_copy(update={"snapshot_id": "ranking-v3-unreleased-authority"})],
        provider="fixture",
        admission_repo=repo,
    )

    assert without_authority.created == 0
    assert without_authority.skipped == 1
    assert with_authority.created == 0
    assert with_authority.skipped == 1
    assert paper_repo.list_trades() == []


def test_paper_trading_rejects_snapshot_with_inconsistent_price_basis(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot = OpportunitySnapshotRecord(
        snapshot_id="price-basis-mismatch",
        run_id="run-price-basis",
        card_id="card-price-basis",
        instrument_id="CN:159558",
        market="CN",
        status="setup_ready",
        signal_date=date(2026, 7, 13),
        latest_close=Decimal("1.351"),
        primary_strategy_id="trend_momentum_stage2",
        score=Decimal("0.90"),
        strategy_score=Decimal("0.90"),
        rank_score=Decimal("0.90"),
        trigger_price=Decimal("4.15"),
        initial_stop=Decimal("3.98"),
        target_1=Decimal("4.49"),
        card={"instrument_label": "半导体设备ETF易方达 159558.SZ"},
    )

    result = seed_paper_trades_from_snapshots(
        paper_repo,
        [snapshot],
        provider="free",
    )

    assert result.created == 0
    assert result.skipped == 1
    assert paper_repo.list_trades() == []


def test_paper_price_basis_uses_card_latest_close_when_row_is_missing():
    snapshot = OpportunitySnapshotRecord(
        snapshot_id="card-price-basis",
        run_id="run-card-price-basis",
        card_id="card-card-price-basis",
        instrument_id="CN:159560",
        market="CN",
        status="setup_ready",
        signal_date=date(2026, 7, 14),
        latest_close=None,
        primary_strategy_id="trend_momentum_stage2",
        score=Decimal("0.90"),
        strategy_score=Decimal("0.90"),
        rank_score=Decimal("0.90"),
        trigger_price=Decimal("2.50"),
        initial_stop=Decimal("2.40"),
        target_1=Decimal("2.75"),
        card={"trading_status": {"latest_close": "2.92"}},
    )

    assert not paper_snapshot_price_basis_is_consistent(snapshot)


def test_paper_price_basis_respects_a_share_board_limits():
    common = {
        "snapshot_id": "board-price-basis",
        "run_id": "run-board-price-basis",
        "card_id": "card-board-price-basis",
        "market": "CN",
        "status": "setup_ready",
        "signal_date": date(2026, 7, 14),
        "latest_close": Decimal("8.50"),
        "primary_strategy_id": "trend_momentum_stage2",
        "score": Decimal("0.90"),
        "strategy_score": Decimal("0.90"),
        "rank_score": Decimal("0.90"),
        "trigger_price": Decimal("10.00"),
        "initial_stop": Decimal("9.50"),
        "target_1": Decimal("11.00"),
        "card": {},
    }
    main_board = OpportunitySnapshotRecord(instrument_id="CN:600000", **common)
    star_board = OpportunitySnapshotRecord(
        instrument_id="CN:688052",
        **{**common, "snapshot_id": "star-price-basis"},
    )

    assert not paper_snapshot_price_basis_is_consistent(main_board)
    assert paper_snapshot_price_basis_is_consistent(star_board)


def test_paper_update_repairs_legacy_replacement_status(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    trade = paper_repo.create_trade(
        source_snapshot_id="legacy-replacement",
        provider="free",
        instrument_id="CN:588850",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 10),
        trigger_price=Decimal("2.20"),
        initial_stop=Decimal("2.10"),
        target_1=Decimal("2.40"),
        rank_score=Decimal("0.70"),
        notes="候补替换：原单转入已跟踪，不再占用活跃名额。",
    )
    paper_repo.update_trade(
        trade.trade_id,
        status="missed_entry",
        exit_date=date(2026, 7, 11),
        realized_return_pct=Decimal("0"),
    )

    result = update_paper_trades(
        paper_repo,
        provider=EmptyMinuteAndDailyProvider(),
        provider_mode="free",
        as_of=datetime(2026, 7, 14, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    repaired = paper_repo.list_trades()[0]

    assert result.data_health["paper_repaired_replaced_statuses"] == "1"
    assert result.summary.missed_entry_count == 0
    assert result.summary.replaced_count == 1
    assert repaired.status == "replaced"
    assert repaired.realized_return_pct is None


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


def test_a_share_paper_trade_invalidates_discontinuous_price_basis(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot_id = _insert_cn_snapshot(
        repo,
        instrument_id="CN:159558",
        created_at=datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
        trigger_price=Decimal("4.15"),
        no_chase_above=Decimal("4.28"),
    )
    paper_repo.create_trade(
        source_snapshot_id=snapshot_id,
        provider="free",
        instrument_id="CN:159558",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 13),
        trigger_price=Decimal("4.15"),
        initial_stop=Decimal("3.98"),
        target_1=Decimal("4.49"),
        rank_score=Decimal("0.90"),
    )
    provider = MinuteCnProvider(
        [
            {
                "instrument_id": "CN:159558",
                "timestamp": datetime(2026, 7, 14, 9, 31),
                "open": Decimal("1.35"),
                "high": Decimal("1.36"),
                "low": Decimal("1.34"),
                "close": Decimal("1.35"),
                "volume": 1000,
                "provider": "test_minute",
            }
        ]
    )

    result = update_paper_trades(
        paper_repo,
        provider=provider,
        as_of=datetime(2026, 7, 14, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trade = paper_repo.list_trades()[0]

    assert trade.status == "invalidated"
    assert trade.entry_date is None
    assert trade.realized_return_pct is None
    assert result.summary.invalidated_count == 1
    assert result.data_health["paper_price_basis_invalidated"] == "1"
    assert "价格口径跳变超过12%" in trade.notes


def test_a_share_paper_trade_uses_card_price_basis_for_minute_fill(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot_id = _insert_cn_snapshot(
        repo,
        instrument_id="CN:600000",
        created_at=datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
        trigger_price=Decimal("10.00"),
        no_chase_above=Decimal("10.30"),
        store_latest_close_in_row=False,
        card_latest_close=Decimal("11.20"),
    )
    paper_repo.create_trade(
        source_snapshot_id=snapshot_id,
        provider="free",
        instrument_id="CN:600000",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 13),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.50"),
        target_1=Decimal("11.00"),
        rank_score=Decimal("0.90"),
    )
    provider = MinuteCnProvider(
        [
            {
                "instrument_id": "CN:600000",
                "timestamp": datetime(2026, 7, 14, 9, 31),
                "open": Decimal("9.00"),
                "high": Decimal("9.20"),
                "low": Decimal("8.95"),
                "close": Decimal("9.10"),
                "volume": 1000,
                "provider": "test_minute",
            }
        ]
    )

    update_paper_trades(
        paper_repo,
        provider=provider,
        as_of=datetime(2026, 7, 14, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trade = paper_repo.list_trades()[0]

    assert trade.status == "invalidated"
    assert "价格口径跳变超过12%" in trade.notes


def test_update_paper_trades_uses_cached_daily_bars_when_minute_source_is_empty(
    tmp_path,
):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    cache = MarketDataCacheRepository(repo.session_factory)
    cache.save_daily_bars(
        "free",
        pd.DataFrame(
            [
                {
                    "instrument_id": "CN:588850",
                    "trade_date": date(2026, 7, 2),
                    "open": Decimal("2.08"),
                    "high": Decimal("2.22"),
                    "low": Decimal("2.05"),
                    "close": Decimal("2.18"),
                    "volume": 100000,
                    "provider": "sqlite_cache",
                }
            ]
        ),
    )
    cache.record_coverage(
        "free",
        "CN:588850",
        date(2026, 7, 1),
        date(2026, 7, 2),
        row_count=1,
    )
    paper_repo.create_trade(
        source_snapshot_id="manual-CN-588850-cache",
        provider="free",
        instrument_id="CN:588850",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("2.10"),
        initial_stop=Decimal("2.00"),
        target_1=Decimal("2.30"),
        rank_score=Decimal("0.91"),
    )
    provider = CachedMarketDataProvider(
        EmptyMinuteAndDailyProvider(),
        cache=cache,
        provider_mode="free",
    )

    result = update_paper_trades(
        paper_repo,
        provider=provider,
        provider_mode="free",
        as_of=datetime(2026, 7, 2, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trade = paper_repo.list_trades(provider="free")[0]

    assert result.data_health["paper_daily_fallback_rows"] == "1"
    assert trade.status == "open"
    assert trade.entry_date == date(2026, 7, 2)
    assert trade.latest_price == Decimal("2.1800")
    assert "日内高点确认" in trade.notes


def test_open_a_share_trade_ignores_minute_bars_before_entry_date(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot_id = _insert_cn_snapshot(
        repo,
        instrument_id="CN:588850",
        created_at=datetime(2026, 7, 2, 3, 38, tzinfo=timezone.utc),
        trigger_price=Decimal("2.29"),
        no_chase_above=Decimal("2.35"),
    )
    trade = paper_repo.create_trade(
        source_snapshot_id=snapshot_id,
        provider="free",
        instrument_id="CN:588850",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 2),
        trigger_price=Decimal("2.29"),
        initial_stop=Decimal("2.20"),
        target_1=Decimal("2.47"),
        rank_score=Decimal("0.91"),
    )
    paper_repo.update_trade(
        trade.trade_id,
        status="open",
        entry_date=date(2026, 7, 3),
        entry_price=Decimal("2.29"),
        latest_date=date(2026, 7, 3),
        latest_price=Decimal("2.25"),
        unrealized_return_pct=Decimal("-1.7467"),
        holding_days=0,
    )
    provider = MinuteCnProvider(
        [
            {
                "instrument_id": "CN:588850",
                "timestamp": datetime(2026, 7, 2, 14, 0),
                "open": Decimal("2.24"),
                "high": Decimal("2.25"),
                "low": Decimal("2.18"),
                "close": Decimal("2.20"),
                "volume": 1000,
                "provider": "test_minute",
            },
            {
                "instrument_id": "CN:588850",
                "timestamp": datetime(2026, 7, 3, 10, 0),
                "open": Decimal("2.25"),
                "high": Decimal("2.28"),
                "low": Decimal("2.23"),
                "close": Decimal("2.26"),
                "volume": 1000,
                "provider": "test_minute",
            },
        ]
    )

    update_paper_trades(
        paper_repo,
        provider=provider,
        provider_mode="free",
        as_of=datetime(2026, 7, 3, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    refreshed = paper_repo.list_trades(provider="free")[0]

    assert refreshed.status == "open"
    assert refreshed.entry_date == date(2026, 7, 3)
    assert refreshed.exit_date is None
    assert refreshed.latest_date == date(2026, 7, 3)
    assert refreshed.latest_price == Decimal("2.2600")


def test_update_paper_trades_repairs_exit_before_entry_records(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot_id = _insert_cn_snapshot(
        repo,
        instrument_id="CN:588850",
        created_at=datetime(2026, 7, 2, 3, 38, tzinfo=timezone.utc),
        trigger_price=Decimal("2.29"),
        no_chase_above=Decimal("2.35"),
    )
    trade = paper_repo.create_trade(
        source_snapshot_id=snapshot_id,
        provider="free",
        instrument_id="CN:588850",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 2),
        trigger_price=Decimal("2.29"),
        initial_stop=Decimal("2.20"),
        target_1=Decimal("2.47"),
        rank_score=Decimal("0.91"),
    )
    paper_repo.update_trade(
        trade.trade_id,
        status="stopped",
        entry_date=date(2026, 7, 3),
        entry_price=Decimal("2.29"),
        exit_date=date(2026, 7, 2),
        exit_price=Decimal("2.20"),
        latest_date=date(2026, 7, 2),
        latest_price=Decimal("2.20"),
        realized_return_pct=Decimal("-3.9301"),
        holding_days=0,
    )
    provider = MinuteCnProvider(
        [
            {
                "instrument_id": "CN:588850",
                "timestamp": datetime(2026, 7, 3, 10, 0),
                "open": Decimal("2.25"),
                "high": Decimal("2.28"),
                "low": Decimal("2.23"),
                "close": Decimal("2.26"),
                "volume": 1000,
                "provider": "test_minute",
            },
        ]
    )

    result = update_paper_trades(
        paper_repo,
        provider=provider,
        provider_mode="free",
        as_of=datetime(2026, 7, 3, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    refreshed = paper_repo.list_trades(provider="free")[0]

    assert result.data_health["paper_repaired_invalid_dates"] == "1"
    assert refreshed.status == "open"
    assert refreshed.exit_date is None
    assert refreshed.realized_return_pct is None
    assert refreshed.latest_date == date(2026, 7, 3)
    assert "修复异常日期" in refreshed.notes


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


def test_a_share_execution_blocks_zero_volume_and_one_price_limit_before_gap_fill(
    tmp_path,
):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    paper_repo.create_trade(
        source_snapshot_id="shared-rules-gap-entry",
        provider="free",
        instrument_id="CN:600000",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.00"),
        target_1=Decimal("12.00"),
        rank_score=Decimal("0.90"),
    )
    provider = DailyRowsProvider(
        [
            {
                "instrument_id": "CN:600000",
                "trade_date": date(2026, 7, 2),
                "open": Decimal("10.00"),
                "high": Decimal("10.10"),
                "low": Decimal("9.90"),
                "close": Decimal("10.00"),
                "volume": 100_000,
                "previous_close": Decimal("10.00"),
                "suspended": True,
            },
            {
                "instrument_id": "CN:600000",
                "trade_date": date(2026, 7, 3),
                "open": Decimal("10.00"),
                "high": Decimal("10.10"),
                "low": Decimal("9.90"),
                "close": Decimal("10.00"),
                "volume": 0,
                "previous_close": Decimal("10.00"),
            },
            {
                "instrument_id": "CN:600000",
                "trade_date": date(2026, 7, 6),
                "open": Decimal("11.00"),
                "high": Decimal("11.00"),
                "low": Decimal("11.00"),
                "close": Decimal("11.00"),
                "volume": 100_000,
                "previous_close": Decimal("10.00"),
            },
            {
                "instrument_id": "CN:600000",
                "trade_date": date(2026, 7, 7),
                "open": Decimal("10.20"),
                "high": Decimal("10.30"),
                "low": Decimal("10.10"),
                "close": Decimal("10.25"),
                "volume": 100_000,
                "previous_close": Decimal("11.00"),
            },
        ]
    )

    result = update_paper_trades(
        paper_repo,
        provider=provider,
        provider_mode="free",
        as_of=datetime(2026, 7, 7, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trade = paper_repo.list_trades(provider="free")[0]

    assert result.data_health["paper_execution_fills_deferred"] == "3"
    assert trade.status == "open"
    assert trade.entry_price == Decimal("10.2000")
    assert trade.execution_facts is not None
    assert trade.execution_facts.entry.base_price == Decimal("10.20")
    assert trade.execution_facts.entry.quantity == 900
    assert "停牌" in trade.notes
    assert "零成交量" in trade.notes
    assert "一字涨跌停" in trade.notes


def test_a_share_execution_rejects_off_tick_trigger(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    paper_repo.create_trade(
        source_snapshot_id="shared-rules-bad-tick",
        provider="free",
        instrument_id="CN:600000",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("10.005"),
        initial_stop=Decimal("9.00"),
        target_1=Decimal("12.00"),
        rank_score=Decimal("0.90"),
    )
    provider = DailyRowsProvider(
        [
            {
                "instrument_id": "CN:600000",
                "trade_date": date(2026, 7, 2),
                "open": Decimal("10.00"),
                "high": Decimal("10.10"),
                "low": Decimal("9.90"),
                "close": Decimal("10.05"),
                "volume": 100_000,
                "previous_close": Decimal("10.00"),
            }
        ]
    )

    result = update_paper_trades(
        paper_repo,
        provider=provider,
        provider_mode="free",
        as_of=datetime(2026, 7, 2, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trade = paper_repo.list_trades(provider="free")[0]

    assert result.data_health["paper_execution_fills_deferred"] == "1"
    assert trade.status == "pending"
    assert trade.execution_facts is None
    assert "最小报价单位" in trade.notes


@pytest.mark.parametrize(
    ("t_plus_one", "expected_status"),
    [(True, "open"), (False, "stopped")],
)
def test_a_share_sellable_quantity_respects_snapshot_t_plus_rule(
    tmp_path,
    t_plus_one,
    expected_status,
):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot_id = _insert_cn_snapshot(
        repo,
        instrument_id="CN:600001",
        created_at=datetime(2026, 7, 2, 3, 38, tzinfo=timezone.utc),
        trigger_price=Decimal("10.00"),
        no_chase_above=Decimal("10.50"),
        t_plus_one=t_plus_one,
    )
    paper_repo.create_trade(
        source_snapshot_id=snapshot_id,
        provider="free",
        instrument_id="CN:600001",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 2),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.50"),
        target_1=Decimal("11.00"),
        rank_score=Decimal("0.90"),
    )
    provider = MinuteCnProvider(
        [
            {
                "instrument_id": "CN:600001",
                "timestamp": datetime(2026, 7, 2, 13, 0),
                "open": Decimal("9.90"),
                "high": Decimal("10.10"),
                "low": Decimal("9.90"),
                "close": Decimal("10.00"),
                "volume": 100_000,
            },
            {
                "instrument_id": "CN:600001",
                "timestamp": datetime(2026, 7, 2, 13, 5),
                "open": Decimal("9.40"),
                "high": Decimal("9.50"),
                "low": Decimal("9.30"),
                "close": Decimal("9.40"),
                "volume": 100_000,
            },
        ]
    )

    update_paper_trades(
        paper_repo,
        provider=provider,
        provider_mode="free",
        as_of=datetime(2026, 7, 2, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trade = paper_repo.list_trades(provider="free")[0]

    assert trade.status == expected_status
    assert trade.execution_facts is not None
    assert trade.execution_facts.rules.settlement_days == int(t_plus_one)
    if t_plus_one:
        assert trade.execution_facts.exit is None
        assert "T+1" in trade.notes
    else:
        assert trade.exit_date == trade.entry_date
        assert trade.execution_facts.exit is not None
        ledger = build_paper_ledger([trade])
        assert [item.side for item in ledger.transactions] == ["buy", "sell"]
        assert ledger.positions == []


def test_execution_facts_freeze_quantity_fees_and_slippage_after_settings_change(
    tmp_path,
):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    paper_repo.start_account_session(
        label="frozen execution",
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("10"),
        max_positions=5,
        transaction_cost_bps=Decimal("10"),
        slippage_bps=Decimal("10"),
        take_profit_pct=Decimal("100"),
    )
    paper_repo.create_trade(
        source_snapshot_id="immutable-execution-facts",
        provider="free",
        instrument_id="CN:600000",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.50"),
        target_1=Decimal("12.00"),
        rank_score=Decimal("0.90"),
    )
    entry_row = {
        "instrument_id": "CN:600000",
        "trade_date": date(2026, 7, 2),
        "open": Decimal("10.20"),
        "high": Decimal("10.30"),
        "low": Decimal("10.10"),
        "close": Decimal("10.25"),
        "volume": 100_000,
        "previous_close": Decimal("10.00"),
    }
    update_paper_trades(
        paper_repo,
        provider=DailyRowsProvider([entry_row]),
        provider_mode="free",
        as_of=datetime(2026, 7, 2, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    opened = paper_repo.list_trades(provider="free")[0]
    assert opened.execution_facts is not None
    assert opened.execution_facts.entry.quantity == 900
    assert opened.execution_facts.entry.base_price == Decimal("10.20")
    assert opened.execution_facts.entry.price == Decimal("10.22")
    assert opened.execution_facts.entry.commission == Decimal("9.20")
    assert opened.execution_facts.entry.slippage == Decimal("18.00")

    paper_repo.start_account_session(
        label="changed settings",
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("50"),
        max_positions=1,
        transaction_cost_bps=Decimal("100"),
        slippage_bps=Decimal("100"),
        take_profit_pct=Decimal("25"),
    )
    original_open_ledger = build_paper_ledger(
        [opened],
        transaction_cost_bps=Decimal("10"),
        slippage_bps=Decimal("10"),
    )
    changed_open_ledger = build_paper_ledger(
        [opened],
        allocation_per_trade_pct=Decimal("50"),
        max_positions=1,
        transaction_cost_bps=Decimal("100"),
        slippage_bps=Decimal("100"),
    )
    assert original_open_ledger.transactions == changed_open_ledger.transactions
    assert original_open_ledger.positions == changed_open_ledger.positions

    blocked_exit_row = {
        "instrument_id": "CN:600000",
        "trade_date": date(2026, 7, 3),
        "open": Decimal("9.23"),
        "high": Decimal("9.23"),
        "low": Decimal("9.23"),
        "close": Decimal("9.23"),
        "volume": 100_000,
        "previous_close": Decimal("10.25"),
    }
    exit_row = {
        "instrument_id": "CN:600000",
        "trade_date": date(2026, 7, 6),
        "open": Decimal("9.30"),
        "high": Decimal("9.40"),
        "low": Decimal("9.20"),
        "close": Decimal("9.25"),
        "volume": 100_000,
        "previous_close": Decimal("9.23"),
    }
    update_paper_trades(
        paper_repo,
        provider=DailyRowsProvider([entry_row, blocked_exit_row, exit_row]),
        provider_mode="free",
        as_of=datetime(2026, 7, 6, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    closed = paper_repo.list_trades(provider="free")[0]
    assert closed.execution_facts is not None
    assert closed.execution_facts.rules.slippage_bps == Decimal("10")
    assert closed.execution_facts.entry.quantity == 900
    assert closed.execution_facts.exit is not None
    assert closed.execution_facts.exit.quantity == 900
    assert closed.execution_facts.exit.base_price == Decimal("9.30")
    assert closed.execution_facts.exit.price == Decimal("9.29")
    assert closed.execution_facts.exit.slippage == Decimal("9.00")
    assert "一字涨跌停" in closed.notes

    original = build_paper_ledger(
        [closed],
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("10"),
        max_positions=5,
        transaction_cost_bps=Decimal("10"),
        slippage_bps=Decimal("10"),
        take_profit_pct=Decimal("100"),
    )
    changed = build_paper_ledger(
        [closed],
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("50"),
        max_positions=1,
        transaction_cost_bps=Decimal("100"),
        slippage_bps=Decimal("100"),
        take_profit_pct=Decimal("25"),
    )

    assert original.transactions == changed.transactions
    assert original.summary.total_fees == changed.summary.total_fees
    assert original.summary.total_slippage == changed.summary.total_slippage
    assert original.items[0].shares == changed.items[0].shares == Decimal("900")
    events = paper_repo.list_trade_events(closed.trade_id)
    assert events[-1].execution_facts == closed.execution_facts
    assert "[paper_execution_facts:v1]" in events[-1].note


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


def test_official_ranking_v3_statistics_exclude_legacy_records(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    official = paper_repo.create_trade(
        source_snapshot_id="official-ranking-v3",
        provider="fixture",
        instrument_id="CN:688981",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("100"),
        initial_stop=Decimal("95"),
        target_1=Decimal("110"),
        rank_score=Decimal("0.90"),
    )
    manual = paper_repo.create_trade(
        source_snapshot_id="legacy-manual",
        provider="fixture",
        instrument_id="CN:000001",
        strategy_id="legacy_strategy",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("100"),
        initial_stop=Decimal("90"),
        target_1=Decimal("120"),
        rank_score=Decimal("0.40"),
    )
    paper_repo.create_trade(
        source_snapshot_id="legacy-unknown",
        provider="fixture",
        instrument_id="CN:000002",
        strategy_id=None,
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("100"),
        initial_stop=Decimal("90"),
        target_1=Decimal("120"),
    )
    paper_repo.update_trade(
        official.trade_id,
        status="target_1_hit",
        entry_date=date(2026, 7, 2),
        entry_price=Decimal("100"),
        exit_date=date(2026, 7, 8),
        exit_price=Decimal("110"),
        latest_date=date(2026, 7, 8),
        latest_price=Decimal("110"),
        realized_return_pct=Decimal("10"),
        holding_days=4,
    )
    paper_repo.update_trade(
        manual.trade_id,
        status="stopped",
        entry_date=date(2026, 7, 2),
        entry_price=Decimal("100"),
        exit_date=date(2026, 7, 8),
        exit_price=Decimal("50"),
        latest_date=date(2026, 7, 8),
        latest_price=Decimal("50"),
        realized_return_pct=Decimal("-50"),
        holding_days=4,
    )
    stored = {trade.source_snapshot_id: trade for trade in paper_repo.list_trades(limit=10)}
    trades = [
        stored["official-ranking-v3"].model_copy(
            update={
                "admission_source": "ranking_v3_production",
                "production_identity_digest": "1" * 64,
                "production_batch_fact_digest": "2" * 64,
                "production_selection_item_digest": "3" * 64,
                "release_proof_digest": "4" * 64,
            }
        ),
        stored["legacy-manual"].model_copy(update={"admission_source": "legacy_manual"}),
        stored["legacy-unknown"],
    ]

    authenticated_trade_ids = {official.trade_id}
    summary = summarize_paper_trades(
        trades,
        authenticated_trade_ids=authenticated_trade_ids,
    )
    ledger = build_official_paper_ledger(
        trades,
        authenticated_trade_ids=authenticated_trade_ids,
    )
    validation = build_paper_validation(
        trades,
        ledger,
        as_of=date(2026, 7, 8),
    )
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 1),
    )

    assert summary.total == 1
    assert summary.win_rate == 1.0
    assert ledger.summary.total_trades == 1
    assert ledger.summary.total_return_pct > 0
    assert {item.trade_id for item in ledger.items} == {official.trade_id}
    assert ledger.data_health["paper_reporting_scope"] == "ranking_v3_production"
    assert ledger.data_health["paper_reporting_official"] == "1"
    assert ledger.data_health["paper_reporting_research_shadow"] == "0"
    assert ledger.data_health["paper_reporting_legacy_manual"] == "1"
    assert ledger.data_health["paper_reporting_legacy_unknown"] == "1"
    assert ledger.data_health["paper_reporting_excluded"] == "2"
    assert validation.summary.total_trades == 1
    assert validation.summary.win_rate == 1.0
    assert validation.data_health["validation_non_official_excluded"] == "2"
    assert [item.trade_id for item in report.new_opportunities] == [official.trade_id]
    assert report.summary.total_trades == 1
    assert report.summary.win_rate == 1.0
    assert report.data_health["paper_daily_report_non_official_excluded"] == "2"


def test_paper_reporting_rejects_unproven_official_claim_and_keeps_legacy_report(
    tmp_path,
):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    stored = paper_repo.create_trade(
        source_snapshot_id="forged-ranking-v3-paper",
        provider="fixture",
        instrument_id="CN:600001",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        rank_score=Decimal("0.90"),
    )
    forged = stored.model_copy(
        update={
            "admission_source": "ranking_v3_production",
            "production_identity_digest": None,
            "production_batch_fact_digest": None,
            "production_selection_item_digest": None,
            "release_proof_digest": None,
        }
    )

    official = build_official_paper_ledger([forged])
    legacy = build_official_paper_ledger([forged], reporting_scope="legacy")

    assert official.summary.total_trades == 0
    assert official.data_health["paper_reporting_scope"] == "ranking_v3_production"
    assert official.data_health["paper_reporting_fail_closed"] == "true"
    assert official.data_health["paper_reporting_invalid_official_claims"] == "1"
    assert legacy.summary.total_trades == 1
    assert legacy.data_health["paper_reporting_scope"] == "legacy_only"


def test_paper_reporting_identifies_research_shadow_separately_from_legacy(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    stored = paper_repo.create_trade(
        source_snapshot_id="ranking-v4-research-shadow",
        provider="fixture",
        instrument_id="CN:600002",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        rank_score=Decimal("0.80"),
        admission_proof=PaperTradeAdmissionProof(
            admission_source="ranking_v4_shadow",
        ),
    )

    official = build_official_paper_ledger([stored])
    research = build_official_paper_ledger([stored], reporting_scope="legacy")

    assert official.summary.total_trades == 0
    assert official.data_health["paper_reporting_research_shadow"] == "1"
    assert official.data_health["paper_reporting_legacy_unknown"] == "0"
    assert research.summary.total_trades == 1
    assert research.data_health["paper_reporting_research_shadow"] == "1"


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


def test_paper_validation_excludes_missed_entries_and_requires_horizon_age(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    stopped = paper_repo.create_trade(
        source_snapshot_id="validation-stopped",
        provider="free",
        instrument_id="CN:560800",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 2),
        trigger_price=Decimal("1.00"),
        initial_stop=Decimal("0.95"),
        target_1=Decimal("1.10"),
        rank_score=Decimal("0.80"),
    )
    missed = paper_repo.create_trade(
        source_snapshot_id="validation-missed",
        provider="free",
        instrument_id="CN:588850",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 2),
        trigger_price=Decimal("2.00"),
        initial_stop=Decimal("1.90"),
        target_1=Decimal("2.20"),
        rank_score=Decimal("0.75"),
    )
    paper_repo.update_trade(
        stopped.trade_id,
        status="stopped",
        entry_date=date(2026, 7, 3),
        entry_price=Decimal("1.00"),
        exit_date=date(2026, 7, 6),
        exit_price=Decimal("0.95"),
        latest_date=date(2026, 7, 6),
        latest_price=Decimal("0.95"),
        realized_return_pct=Decimal("-5"),
        holding_days=3,
    )
    paper_repo.update_trade(
        missed.trade_id,
        status="missed_entry",
        exit_date=date(2026, 7, 2),
        latest_date=date(2026, 7, 2),
        latest_price=Decimal("2.30"),
        holding_days=0,
    )

    trades = paper_repo.list_trades(limit=20)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 10))
    windows = {window.window_days: window for window in validation.windows}

    assert validation.summary.total_trades == 2
    assert validation.summary.triggered_trades == 1
    assert validation.summary.closed_trades == 1
    assert validation.summary.missed_entry_count == 1
    assert windows[5].evaluated_trades == 1
    assert windows[5].negative_trades == 1
    assert windows[5].win_rate == 0.0
    assert windows[10].evaluated_trades == 0
    assert windows[20].evaluated_trades == 0
    assert validation.sample_age.mature_5d == 1
    assert validation.sample_age.mature_10d == 0
    assert validation.sample_age.mature_20d == 0
    assert validation.credibility.level != "high"
    assert "已成交 1 笔" in validation.credibility.evidence
    assert "错过买点 1 笔" in validation.credibility.evidence
    assert validation.batches[0].total_return_pct == windows[5].total_return_pct


def test_paper_validation_maturity_uses_a_share_trading_sessions(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    trade = paper_repo.create_trade(
        source_snapshot_id="validation-holiday",
        provider="free",
        instrument_id="CN:000001",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 9, 30),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.50"),
        target_1=Decimal("11.00"),
        rank_score=Decimal("0.80"),
    )
    paper_repo.update_trade(
        trade.trade_id,
        status="open",
        entry_date=date(2026, 9, 30),
        entry_price=Decimal("10.00"),
        latest_date=date(2026, 10, 9),
        latest_price=Decimal("10.10"),
        unrealized_return_pct=Decimal("1"),
        holding_days=2,
    )

    trades = paper_repo.list_trades(limit=10)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 10, 9))

    assert validation.sample_age.oldest_days_since_signal == 2
    assert validation.sample_age.mature_5d == 0
    assert validation.sample_age.days_to_next_5d == 3


def test_paper_daily_report_groups_stock_and_etf_performance(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    stock = paper_repo.create_trade(
        source_snapshot_id="daily-stock",
        provider="fixture",
        instrument_id="CN:605589",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("20"),
        initial_stop=Decimal("19"),
        target_1=Decimal("23"),
        rank_score=Decimal("0.80"),
    )
    etf = paper_repo.create_trade(
        source_snapshot_id="daily-etf",
        provider="fixture",
        instrument_id="CN:588850",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("2.20"),
        initial_stop=Decimal("2.10"),
        target_1=Decimal("2.40"),
        rank_score=Decimal("0.70"),
    )
    paper_repo.update_trade(
        stock.trade_id,
        status="target_1_hit",
        entry_date=date(2026, 7, 2),
        entry_price=Decimal("20"),
        exit_date=date(2026, 7, 4),
        exit_price=Decimal("23"),
        latest_date=date(2026, 7, 4),
        latest_price=Decimal("23"),
        realized_return_pct=Decimal("15"),
        holding_days=2,
    )
    paper_repo.update_trade(
        etf.trade_id,
        status="open",
        entry_date=date(2026, 7, 2),
        entry_price=Decimal("2.20"),
        latest_date=date(2026, 7, 4),
        latest_price=Decimal("2.112"),
        unrealized_return_pct=Decimal("-4"),
        holding_days=2,
    )

    trades = paper_repo.list_trades(limit=20)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 4))
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 4),
        asset_type_by_instrument={"CN:605589": "stock", "CN:588850": "etf"},
    )

    groups = {group.asset_type: group for group in report.asset_groups}
    assert groups["stock"].label == "股票"
    assert groups["stock"].total_trades == 1
    assert groups["stock"].closed_trades == 1
    assert groups["stock"].win_rate == 1.0
    assert groups["stock"].total_return_pct > 0
    assert groups["etf"].label == "ETF"
    assert groups["etf"].open_trades == 1
    assert groups["etf"].average_return_pct == -4.0
    assert groups["etf"].total_return_pct < 0


def test_paper_daily_report_explains_risk_gate_failures_and_event_timeline(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    stopped = paper_repo.create_trade(
        source_snapshot_id="daily-risk-stopped",
        provider="fixture",
        instrument_id="CN:002747",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("30"),
        initial_stop=Decimal("28.50"),
        target_1=Decimal("33"),
        rank_score=Decimal("0.80"),
    )
    paper_repo.update_trade(
        stopped.trade_id,
        status="stopped",
        entry_date=date(2026, 7, 2),
        entry_price=Decimal("30"),
        exit_date=date(2026, 7, 4),
        exit_price=Decimal("28.50"),
        latest_date=date(2026, 7, 4),
        latest_price=Decimal("28.50"),
        realized_return_pct=Decimal("-5"),
        holding_days=2,
    )
    for index in range(5):
        extra = paper_repo.create_trade(
            source_snapshot_id=f"daily-risk-extra-{index}",
            provider="fixture",
            instrument_id=f"CN:30060{index}",
            strategy_id="trend_momentum_stage2",
            signal_date=date(2026, 7, 1),
            trigger_price=Decimal("20"),
            initial_stop=Decimal("19"),
            target_1=Decimal("22"),
            rank_score=Decimal("0.70"),
        )
        paper_repo.update_trade(
            extra.trade_id,
            status="stopped",
            entry_date=date(2026, 7, 2),
            entry_price=Decimal("20"),
            exit_date=date(2026, 7, 4),
            exit_price=Decimal("18.40"),
            latest_date=date(2026, 7, 4),
            latest_price=Decimal("18.40"),
            realized_return_pct=Decimal("-8"),
            holding_days=2,
        )
    open_trade = paper_repo.create_trade(
        source_snapshot_id="daily-risk-open",
        provider="fixture",
        instrument_id="CN:588850",
        strategy_id="factor_rotation_watch",
        signal_date=date(2026, 7, 2),
        trigger_price=Decimal("2.20"),
        initial_stop=Decimal("2.10"),
        target_1=Decimal("2.40"),
        rank_score=Decimal("0.70"),
    )
    paper_repo.update_trade(
        open_trade.trade_id,
        status="open",
        entry_date=date(2026, 7, 3),
        entry_price=Decimal("2.20"),
        latest_date=date(2026, 7, 4),
        latest_price=Decimal("2.12"),
        unrealized_return_pct=Decimal("-3.64"),
        holding_days=1,
        notes="风控恢复探针：本轮最多 1 笔。",
    )

    trades = paper_repo.list_trades(limit=20)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 4))
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 4),
        asset_type_by_instrument={"CN:002747": "stock", "CN:588850": "etf"},
    )

    assert report.risk_gate.action == "throttle_new_entries"
    assert report.risk_gate.can_add_entries is True
    assert report.risk_gate.max_new_entries == 1
    assert report.risk_gate.position_size_multiplier == 0.35
    assert report.risk_gate.reasons
    assert any(item.dimension == "strategy" for item in report.failure_attribution)
    assert any(
        item.dimension == "asset" and item.key == "etf" for item in report.failure_attribution
    )
    worst = report.failure_attribution[0]
    assert worst.total_pnl < 0
    assert worst.note
    event_types = {item.event_type for item in report.event_timeline}
    assert {"signal", "entry", "exit"}.issubset(event_types)
    assert any(
        item.trade_id == stopped.trade_id and item.event_type == "exit"
        for item in report.event_timeline
    )


def test_paper_daily_report_does_not_attribute_legacy_stop_to_current_strategy(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    stopped = paper_repo.create_trade(
        source_snapshot_id="diagnostic-risk-filter",
        provider="fixture",
        instrument_id="CN:688981",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("100"),
        initial_stop=Decimal("95"),
        target_1=Decimal("110"),
        rank_score=Decimal("0.86"),
    )
    paper_repo.update_trade(
        stopped.trade_id,
        status="stopped",
        entry_date=date(2026, 7, 2),
        entry_price=Decimal("100"),
        exit_date=date(2026, 7, 4),
        exit_price=Decimal("95"),
        latest_date=date(2026, 7, 4),
        latest_price=Decimal("95"),
        realized_return_pct=Decimal("-5"),
        holding_days=2,
    )
    trades = paper_repo.list_trades(limit=10)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 4))
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 4),
        source_context_by_trade={
            stopped.trade_id: PaperTradeSourceContext(
                source_snapshot_id=stopped.source_snapshot_id,
                created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                latest_close=Decimal("99"),
                card={
                    "instrument_label": "中芯国际 688981.SH",
                    "factor_flags": ["high_volatility", "overextended"],
                },
            )
        },
    )

    diagnostic = report.trade_diagnostics[0]
    assert diagnostic.instrument_label == "中芯国际 688981.SH"
    assert diagnostic.root_cause == "legacy_execution_evidence"
    assert diagnostic.severity == "warning"
    assert diagnostic.factor_signals == ["high_volatility", "overextended"]
    assert diagnostic.execution_evidence_status == "legacy_unverified"
    assert diagnostic.strategy_attribution_eligible is False
    assert report.execution_evidence.legacy_closed_trades == 1
    assert report.execution_evidence.comparable_closed_trades == 0
    assert any(
        item.dimension == "cause" and item.key == "legacy_execution_evidence"
        for item in report.failure_attribution
    )
    assert any(
        item.dimension == "signal" and item.key == "high_volatility"
        for item in report.failure_attribution
    )


def test_paper_daily_report_attributes_audited_stop_to_current_strategy(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    stopped = paper_repo.create_trade(
        source_snapshot_id="diagnostic-audited-risk-filter",
        provider="free",
        instrument_id="CN:688981",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("100.00"),
        initial_stop=Decimal("95.00"),
        target_1=Decimal("110.00"),
        rank_score=Decimal("0.86"),
    )
    update_paper_trades(
        paper_repo,
        provider=DailyRowsProvider(
            [
                {
                    "instrument_id": "CN:688981",
                    "trade_date": date(2026, 7, 2),
                    "open": Decimal("100.00"),
                    "high": Decimal("101.00"),
                    "low": Decimal("99.00"),
                    "close": Decimal("100.00"),
                    "volume": 100_000,
                    "previous_close": Decimal("99.00"),
                }
            ]
        ),
        provider_mode="free",
        as_of=datetime(2026, 7, 2, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    update_paper_trades(
        paper_repo,
        provider=DailyRowsProvider(
            [
                {
                    "instrument_id": "CN:688981",
                    "trade_date": date(2026, 7, 3),
                    "open": Decimal("94.00"),
                    "high": Decimal("96.00"),
                    "low": Decimal("93.00"),
                    "close": Decimal("94.00"),
                    "volume": 100_000,
                    "previous_close": Decimal("100.00"),
                }
            ]
        ),
        provider_mode="free",
        as_of=datetime(2026, 7, 3, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    trades = paper_repo.list_trades(limit=10)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 3))
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 3),
        source_context_by_trade={
            stopped.trade_id: PaperTradeSourceContext(
                source_snapshot_id=stopped.source_snapshot_id,
                created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                signal_date=date(2026, 7, 1),
                source_status="frozen",
                card={
                    "instrument_label": "中芯国际 688981.SH",
                    "factor_flags": ["high_volatility", "overextended"],
                },
            )
        },
    )

    diagnostic = report.trade_diagnostics[0]
    assert diagnostic.root_cause == "risk_filter_failure"
    assert diagnostic.execution_evidence_status == "complete"
    assert diagnostic.strategy_attribution_eligible is True
    assert report.execution_evidence.audited_closed_trades == 1
    assert report.execution_evidence.comparable_closed_trades == 1
    assert report.data_health["paper_execution_evidence_comparable_closed"] == "1"


def test_paper_daily_report_does_not_diagnose_pending_trade_as_failure(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    pending = paper_repo.create_trade(
        source_snapshot_id="diagnostic-pending",
        provider="fixture",
        instrument_id="CN:588200",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("5"),
        initial_stop=Decimal("4.8"),
        target_1=Decimal("5.5"),
        rank_score=Decimal("0.80"),
    )
    trades = paper_repo.list_trades(limit=10)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 2))
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 2),
        source_context_by_trade={
            pending.trade_id: PaperTradeSourceContext(
                source_snapshot_id=pending.source_snapshot_id,
                created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                latest_close=Decimal("4.95"),
                card={
                    "instrument_label": "科创芯片ETF嘉实 588200.SH",
                    "factor_flags": ["overextended"],
                },
            )
        },
    )

    assert report.trade_diagnostics == []
    assert not any(
        item.dimension == "cause" and item.key == "chasing_entry"
        for item in report.failure_attribution
    )


def test_paper_daily_report_explains_recovery_market_context_and_trigger_quality(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    for index, loss_pct in enumerate((Decimal("-4"), Decimal("-2")), start=1):
        stopped = paper_repo.create_trade(
            source_snapshot_id=f"daily-recovery-stopped-{index}",
            provider="fixture",
            instrument_id=f"CN:30{index:04d}",
            strategy_id="trend_momentum_stage2",
            signal_date=date(2026, 7, 1),
            trigger_price=Decimal("10"),
            initial_stop=Decimal("9.50"),
            target_1=Decimal("11"),
            rank_score=Decimal("0.80"),
        )
        exit_price = Decimal("10") * (Decimal("1") + loss_pct / Decimal("100"))
        paper_repo.update_trade(
            stopped.trade_id,
            status="stopped",
            entry_date=date(2026, 7, 2),
            entry_price=Decimal("10"),
            exit_date=date(2026, 7, 4),
            exit_price=exit_price,
            latest_date=date(2026, 7, 4),
            latest_price=exit_price,
            realized_return_pct=loss_pct,
            holding_days=2,
            notes="触发止损",
        )
    missed = paper_repo.create_trade(
        source_snapshot_id="daily-recovery-missed",
        provider="fixture",
        instrument_id="CN:002747",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 3),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9.50"),
        target_1=Decimal("11"),
        rank_score=Decimal("0.80"),
    )
    paper_repo.update_trade(
        missed.trade_id,
        status="missed_entry",
        exit_date=date(2026, 7, 6),
        latest_date=date(2026, 7, 6),
        latest_price=Decimal("10.80"),
        realized_return_pct=Decimal("0"),
        notes="价格超过不追高价，标记为错过买点。",
    )
    open_trade = paper_repo.create_trade(
        source_snapshot_id="daily-recovery-open",
        provider="fixture",
        instrument_id="CN:588200",
        strategy_id="factor_rotation_watch",
        signal_date=date(2026, 7, 4),
        trigger_price=Decimal("12"),
        initial_stop=Decimal("11.40"),
        target_1=Decimal("13.20"),
        rank_score=Decimal("0.82"),
    )
    paper_repo.update_trade(
        open_trade.trade_id,
        status="open",
        entry_date=date(2026, 7, 7),
        entry_price=Decimal("12"),
        latest_date=date(2026, 7, 9),
        latest_price=Decimal("11.88"),
        unrealized_return_pct=Decimal("-1"),
        holding_days=2,
        notes="持仓观察",
    )
    paper_repo.create_trade(
        source_snapshot_id="daily-recovery-pending",
        provider="fixture",
        instrument_id="CN:588850",
        strategy_id="factor_rotation_watch",
        signal_date=date(2026, 7, 8),
        trigger_price=Decimal("9.90"),
        initial_stop=Decimal("9.40"),
        target_1=Decimal("10.80"),
        rank_score=Decimal("0.84"),
        notes="等待触发价",
    )

    trades = paper_repo.list_trades(limit=20)
    ledger = build_paper_ledger(
        trades,
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("10"),
        transaction_cost_bps=Decimal("5"),
        slippage_bps=Decimal("5"),
        take_profit_pct=Decimal("50"),
    )
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 9))
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 9),
        benchmark_items=[
            {
                "benchmark_id": "CN:000300",
                "name": "沪深300",
                "return_pct": 3.2,
                "excess_return_pct": ledger.summary.total_return_pct - 3.2,
            }
        ],
        asset_type_by_instrument={"CN:588200": "etf", "CN:588850": "etf"},
    )

    assert report.market_context.regime == "strategy_underperforming"
    assert "跑输" in report.market_context.summary
    assert report.trigger_quality.missed_entry_count == 1
    assert report.trigger_quality.pending_count == 1
    assert report.trigger_quality.verdict in {"needs_tighter_entry", "watch"}
    assert report.risk_gate.action == "allow_new_entries"
    assert report.risk_gate.can_add_entries is True
    assert report.risk_gate.max_new_entries == 3
    assert report.risk_gate.position_size_multiplier == 1.0


def test_paper_trade_freezes_normalized_point_in_time_source_context(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot_id = _insert_cn_snapshot(
        repo,
        instrument_id="CN:688981",
        created_at=datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc),
        trigger_price=Decimal("100"),
        no_chase_above=Decimal("103"),
        card_overrides={
            "market_context": {
                "industry": "半导体",
                "themes": ["先进制程", "国产替代"],
            },
            "market_regime": {"regime": "risk_off"},
            "factor_flags": ["overextended"],
            "factor_exposures": [
                {"factor_id": "quality", "score": 0.82},
                {"factor_id": "trend_quality", "score": 0.76},
            ],
        },
    )

    trade = paper_repo.create_trade(
        source_snapshot_id=snapshot_id,
        provider="free",
        instrument_id="CN:688981",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 2),
        trigger_price=Decimal("100"),
        initial_stop=Decimal("95"),
        target_1=Decimal("110"),
    )
    context = paper_repo.get_trade_source_context(trade.source_snapshot_id)

    assert context is not None
    assert context.source_status == "frozen"
    assert context.signal_date == date(2026, 7, 2)
    assert context.industry == "半导体"
    assert context.themes == ["先进制程", "国产替代"]
    assert context.market_regime == "risk_off"
    assert context.factor_ids == [
        "overextended",
        "quality",
        "trend_quality",
    ]

    with repo.session_factory() as session:
        snapshot = session.get(OpportunitySnapshotRow, snapshot_id)
        assert snapshot is not None
        snapshot.card_json = json.dumps(
            {
                "market_context": {"industry": "银行", "themes": ["红利"]},
                "market_regime": {"regime": "risk_on"},
                "factor_flags": ["low_risk"],
            }
        )
        session.commit()

    frozen = paper_repo.get_trade_source_context(trade.source_snapshot_id)
    assert frozen is not None
    assert frozen.industry == "半导体"
    assert frozen.market_regime == "risk_off"
    assert frozen.factor_ids == context.factor_ids


def test_legacy_paper_trade_without_frozen_context_stays_unknown(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    snapshot_id = _insert_cn_snapshot(
        repo,
        instrument_id="CN:688008",
        created_at=datetime(2026, 6, 30, 7, 0, tzinfo=timezone.utc),
        trigger_price=Decimal("50"),
        no_chase_above=Decimal("52"),
        card_overrides={
            "market_context": {
                "industry": "半导体",
                "themes": ["AI芯片"],
            },
            "market_regime": "risk_off",
            "factor_exposures": [{"factor_id": "quality", "score": 0.8}],
        },
    )
    with repo.session_factory() as session:
        session.add(
            PaperTradeRow(
                trade_id="legacy-paper-context",
                source_snapshot_id=snapshot_id,
                provider="free",
                instrument_id="CN:688008",
                strategy_id="quality_compounder",
                status="pending",
                signal_date=date(2026, 7, 1),
                trigger_price=Decimal("50"),
                initial_stop=Decimal("47"),
                target_1=Decimal("56"),
                notes="旧记录没有冻结上下文事件。",
            )
        )
        session.commit()

    context = paper_repo.get_trade_source_context(snapshot_id)

    assert context is not None
    assert context.source_status == "unknown"
    assert context.signal_date == date(2026, 7, 1)
    assert context.industry == "unknown"
    assert context.themes == []
    assert context.market_regime == "unknown"
    assert context.factor_ids == []
    assert context.card == {}

    with repo.session_factory() as session:
        snapshot = session.get(OpportunitySnapshotRow, snapshot_id)
        assert snapshot is not None
        snapshot.card_json = json.dumps(
            {
                "market_context": {
                    "industry": "银行",
                    "themes": ["红利"],
                },
                "market_regime": "risk_on",
                "factor_flags": ["low_risk"],
            }
        )
        session.commit()

    unchanged = paper_repo.get_trade_source_context(snapshot_id)
    assert unchanged is not None
    assert unchanged.source_status == "unknown"
    assert unchanged.industry == "unknown"
    assert unchanged.market_regime == "unknown"
    assert unchanged.factor_ids == []


def test_paper_daily_report_uses_only_saved_pit_context_for_historical_attribution(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    stopped = paper_repo.create_trade(
        source_snapshot_id="pit-attribution",
        provider="fixture",
        instrument_id="CN:688981",
        strategy_id="trend_momentum_stage2",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("100"),
        initial_stop=Decimal("90"),
        target_1=Decimal("115"),
        rank_score=Decimal("0.82"),
    )
    paper_repo.update_trade(
        stopped.trade_id,
        status="stopped",
        entry_date=date(2026, 7, 2),
        entry_price=Decimal("100"),
        exit_date=date(2026, 7, 8),
        exit_price=Decimal("90"),
        latest_date=date(2026, 7, 8),
        latest_price=Decimal("90"),
        realized_return_pct=Decimal("-10"),
        holding_days=4,
    )
    trades = paper_repo.list_trades(limit=10)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 8))
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 8),
        benchmark_items=[
            {
                "name": "沪深300",
                "return_pct": -4.0,
                "excess_return_pct": -1.0,
            }
        ],
        source_context_by_trade={
            stopped.trade_id: PaperTradeSourceContext(
                source_snapshot_id=stopped.source_snapshot_id,
                created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                signal_date=date(2026, 7, 1),
                industry="半导体",
                themes=["国产替代"],
                market_regime="risk_on",
                factor_ids=["quality", "trend_quality"],
                source_status="frozen",
                card={"instrument_label": "中芯国际 688981.SH"},
            )
        },
    )

    dimensions = {(item.dimension, item.key) for item in report.failure_attribution}
    assert report.market_context.regime == "market_drag"
    assert ("strategy", "trend_momentum_stage2") in dimensions
    assert ("factor", "quality") in dimensions
    assert ("industry", "半导体") in dimensions
    assert ("market_regime", "risk_on") in dimensions
    assert ("market_regime", "market_drag") not in dimensions
    assert report.trade_diagnostics[0].source_market_regime == "risk_on"
    assert report.trade_diagnostics[0].root_cause != "market_regime"
    assert report.data_health["paper_pit_context_frozen"] == "1"
    assert report.data_health["paper_pit_context_unknown"] == "0"


def test_paper_daily_report_marks_missing_legacy_context_unknown(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    trade = paper_repo.create_trade(
        source_snapshot_id="legacy-context-missing",
        provider="fixture",
        instrument_id="CN:000001",
        strategy_id=None,
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
    )
    trades = paper_repo.list_trades(limit=10)
    ledger = build_paper_ledger(trades)
    validation = build_paper_validation(trades, ledger, as_of=date(2026, 7, 2))
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=date(2026, 7, 2),
    )

    dimensions = {(item.dimension, item.key) for item in report.failure_attribution}
    assert ("industry", "unknown") in dimensions
    assert ("factor", "unknown") in dimensions
    assert ("market_regime", "unknown") in dimensions
    assert report.data_health["paper_pit_context_unknown"] == "1"
    context = paper_repo.get_trade_source_context(trade.source_snapshot_id)
    assert context is not None
    assert context.source_status == "unknown"


def _insert_cn_snapshot(
    repo,
    *,
    instrument_id: str,
    created_at: datetime,
    trigger_price: Decimal,
    no_chase_above: Decimal,
    store_latest_close_in_row: bool = True,
    card_latest_close: Decimal | None = None,
    t_plus_one: bool | None = None,
    card_overrides: dict[str, object] | None = None,
) -> str:
    snapshot_id = f"scan-minute:{instrument_id}"
    card = {
        "instrument_id": instrument_id,
        "instrument_label": instrument_id,
        "entry_plan": {
            "trigger_price": str(trigger_price),
            "no_chase_above": str(no_chase_above),
        },
        "trading_status": {
            "latest_close": str(card_latest_close or trigger_price),
        },
    }
    if t_plus_one is not None:
        card["trading_constraints"] = {
            "t_plus_one": t_plus_one,
            "min_lot": 100,
            "price_limit_pct": 10,
        }
    card.update(card_overrides or {})
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
                latest_close=(trigger_price if store_latest_close_in_row else None),
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
