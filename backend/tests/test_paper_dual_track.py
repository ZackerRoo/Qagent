import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd

from qagent.db import Base, create_db_engine, create_session_factory
from qagent.paper_trading.dual_track import (
    build_dual_track_report,
    select_daily_top_recommendations,
)
from qagent.storage.paper import PaperTradeRecord
from qagent.storage.repository import OpportunitySnapshotRecord
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import OpportunitySnapshotRow, ScanRunRow


def _snapshot(
    snapshot_id: str,
    instrument_id: str,
    signal_date: date,
    rank_score: str,
) -> OpportunitySnapshotRecord:
    return OpportunitySnapshotRecord(
        snapshot_id=snapshot_id,
        run_id="run-dual",
        card_id=f"card-{snapshot_id}",
        instrument_id=instrument_id,
        market="CN",
        status="setup_ready",
        signal_date=signal_date,
        latest_close=Decimal("100"),
        primary_strategy_id="trend_momentum_stage2",
        score=Decimal(rank_score),
        strategy_score=Decimal(rank_score),
        rank_score=Decimal(rank_score),
        trigger_price=Decimal("103"),
        initial_stop=Decimal("98"),
        target_1=Decimal("112"),
        card={"instrument_label": f"测试标的 {instrument_id}"},
    )


def _trade(snapshot_id: str, instrument_id: str) -> PaperTradeRecord:
    return PaperTradeRecord(
        trade_id="paper-dual",
        source_snapshot_id=snapshot_id,
        provider="free",
        instrument_id=instrument_id,
        strategy_id="trend_momentum_stage2",
        status="open",
        signal_date=date(2026, 1, 2),
        trigger_price=Decimal("103"),
        initial_stop=Decimal("98"),
        target_1=Decimal("112"),
        rank_score=Decimal("0.9"),
        allocation_multiplier=Decimal("1"),
        entry_date=date(2026, 1, 6),
        entry_price=Decimal("103"),
        exit_date=None,
        exit_price=None,
        latest_date=date(2026, 1, 30),
        latest_price=Decimal("121"),
        unrealized_return_pct=17.47,
        realized_return_pct=None,
        holding_days=24,
        notes="测试成交",
    )


def _bars(instrument_id: str, *, base: float, periods: int = 24) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=periods)
    return pd.DataFrame(
        {
            "instrument_id": instrument_id,
            "trade_date": dates.date,
            "open": [base + index for index in range(periods)],
            "high": [base + index + 2 for index in range(periods)],
            "low": [base + index - 1 for index in range(periods)],
            "close": [base + index + 1 for index in range(periods)],
            "volume": [1_000_000] * periods,
        }
    )


def test_select_daily_top_recommendations_deduplicates_and_limits_each_day():
    signal_date = date(2026, 1, 2)
    snapshots = [
        _snapshot(f"snapshot-{index}", f"CN:{index:06d}", signal_date, f"0.{index}")
        for index in range(1, 8)
    ]
    snapshots.append(_snapshot("duplicate-top", "CN:000007", signal_date, "0.99"))

    selected = select_daily_top_recommendations(snapshots, top_n=5, as_of=signal_date)

    assert len(selected) == 5
    assert len({item.instrument_id for item in selected}) == 5
    assert selected[0].snapshot_id == "duplicate-top"
    assert {item.instrument_id for item in selected} == {
        "CN:000003",
        "CN:000004",
        "CN:000005",
        "CN:000006",
        "CN:000007",
    }


def test_dual_track_separates_next_open_selection_from_trigger_execution():
    snapshot = _snapshot("snapshot-main", "CN:000001", date(2026, 1, 2), "0.90")
    instrument_bars = _bars("CN:000001", base=100)
    benchmark_bars = pd.concat(
        [
            _bars("CN:000300.IDX", base=200),
            _bars("CN:000688.IDX", base=300),
        ],
        ignore_index=True,
    )

    report = build_dual_track_report(
        snapshots=[snapshot],
        trades=[_trade(snapshot.snapshot_id, snapshot.instrument_id)],
        instrument_bars=instrument_bars,
        benchmark_bars=benchmark_bars,
        as_of=date(2026, 2, 6),
        transaction_cost_bps=5,
        slippage_bps=5,
    )

    sample = report.samples[0]
    assert sample.selection_entry_date == date(2026, 1, 5)
    assert sample.selection_entry_price == Decimal("101.0")
    assert sample.execution_entry_date == date(2026, 1, 6)
    assert sample.execution_entry_price == Decimal("103")
    assert sample.selection_return_5d == 4.7505
    assert sample.execution_return_5d == 3.6835
    assert report.summary.execution_admitted == 1
    assert report.summary.execution_filled == 1
    assert report.summary.execution_fill_rate == 1.0
    assert report.windows[0].benchmarks[0].name == "沪深300"
    assert report.windows[0].selection.evaluated_count == 1
    assert report.windows[0].execution.evaluated_count == 1


def test_dual_track_keeps_untriggered_recommendation_out_of_execution_returns():
    snapshot = _snapshot("snapshot-pending", "CN:000002", date(2026, 1, 2), "0.88")
    pending = _trade(snapshot.snapshot_id, snapshot.instrument_id).model_copy(
        update={
            "trade_id": "paper-pending",
            "status": "pending",
            "entry_date": None,
            "entry_price": None,
            "latest_price": Decimal("101"),
            "unrealized_return_pct": None,
        }
    )

    report = build_dual_track_report(
        snapshots=[snapshot],
        trades=[pending],
        instrument_bars=_bars("CN:000002", base=100),
        benchmark_bars=pd.DataFrame(),
        as_of=date(2026, 2, 6),
    )

    assert report.summary.selection_started == 1
    assert report.summary.execution_admitted == 1
    assert report.summary.execution_filled == 0
    assert report.samples[0].selection_return_10d is not None
    assert report.samples[0].execution_return_10d is None
    assert report.samples[0].attribution == "选股已开始验证，买点尚未触发"


def test_dual_track_quality_filter_uses_only_signal_date_snapshot_fields():
    clean = _snapshot("snapshot-clean", "CN:000001", date(2026, 1, 2), "0.90")
    overheated = _snapshot("snapshot-hot", "CN:000002", date(2026, 1, 2), "0.88")
    overheated.card["factor_flags"] = ["high_volatility", "overextended"]
    bars = pd.concat(
        [
            _bars(clean.instrument_id, base=100),
            _bars(overheated.instrument_id, base=120),
        ],
        ignore_index=True,
    )

    report = build_dual_track_report(
        snapshots=[clean, overheated],
        trades=[],
        instrument_bars=bars,
        benchmark_bars=pd.DataFrame(),
        as_of=date(2026, 2, 6),
    )

    samples = {sample.instrument_id: sample for sample in report.samples}
    assert samples[clean.instrument_id].calibrated_eligible is True
    assert samples[overheated.instrument_id].calibrated_eligible is False
    assert samples[overheated.instrument_id].calibrated_reason == "高波动与短线过热同时出现"
    assert report.summary.recommendations == 2
    assert report.summary.calibrated_admitted == 1
    assert report.summary.calibrated_filter_rate == 0.5
    assert report.windows[0].selection.sample_count == 2
    assert report.windows[0].calibrated.sample_count == 1


def test_dual_track_reads_data_quality_audit_instead_of_legacy_data_quality_key():
    blocked = _snapshot("snapshot-data-quality", "CN:000001", date(2026, 1, 2), "0.90")
    blocked.card["data_quality_audit"] = {
        "status": "blocked",
        "can_recommend": False,
        "score": 0.10,
        "summary": "point-in-time bars failed the quality gate",
    }
    blocked.card["data_quality"] = {"score": 1.0}
    ready = _snapshot("snapshot-data-ready", "CN:000002", date(2026, 1, 2), "0.89")
    ready.card["data_quality_audit"] = {
        "status": "ready",
        "can_recommend": True,
        "score": 1.0,
        "summary": "point-in-time bars passed the quality gate",
    }
    ready.card["data_quality"] = {"score": 0.10}

    report = build_dual_track_report(
        snapshots=[blocked, ready],
        trades=[],
        instrument_bars=pd.concat(
            [
                _bars(blocked.instrument_id, base=100),
                _bars(ready.instrument_id, base=120),
            ],
            ignore_index=True,
        ),
        benchmark_bars=pd.DataFrame(),
        as_of=date(2026, 2, 6),
    )

    samples = {sample.snapshot_id: sample for sample in report.samples}
    assert samples[blocked.snapshot_id].calibrated_eligible is False
    assert "数据质量" in samples[blocked.snapshot_id].calibrated_reason
    assert samples[ready.snapshot_id].calibrated_eligible is True
    assert report.summary.calibrated_admitted == 1


def test_repository_selects_unique_daily_top_recommendations_per_provider(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'dual-track.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(database_url)
    repo = QagentRepository(session_factory)
    signal_date = date(2026, 1, 2)

    with session_factory() as session:
        session.add_all(
            [
                ScanRunRow(
                    run_id="run-free",
                    provider="free",
                    mode="full_market",
                    symbols="[]",
                    scanned=4,
                    cards=4,
                    data_health="{}",
                ),
                ScanRunRow(
                    run_id="run-fixture",
                    provider="fixture",
                    mode="full_market",
                    symbols="[]",
                    scanned=1,
                    cards=1,
                    data_health="{}",
                ),
            ]
        )
        session.add_all(
            [
                _snapshot_row("free-a-low", "run-free", "CN:000001", signal_date, "0.80", 1),
                _snapshot_row("free-a-top", "run-free", "CN:000001", signal_date, "0.95", 2),
                _snapshot_row("free-b", "run-free", "CN:000002", signal_date, "0.90", 3),
                _snapshot_row("free-c", "run-free", "CN:000003", signal_date, "0.70", 4),
                _snapshot_row("fixture-top", "run-fixture", "CN:999999", signal_date, "0.99", 5),
            ]
        )
        session.commit()

    selected = repo.list_top_daily_opportunity_snapshots(
        start=signal_date,
        end=signal_date,
        top_n=2,
        provider="free",
    )

    assert [item.snapshot_id for item in selected] == ["free-a-top", "free-b"]
    assert len({item.instrument_id for item in selected}) == 2


def _snapshot_row(
    snapshot_id: str,
    run_id: str,
    instrument_id: str,
    signal_date: date,
    rank_score: str,
    minute: int,
) -> OpportunitySnapshotRow:
    return OpportunitySnapshotRow(
        snapshot_id=snapshot_id,
        run_id=run_id,
        card_id=f"card-{snapshot_id}",
        instrument_id=instrument_id,
        market="CN",
        status="setup_ready",
        signal_date=signal_date,
        latest_close=Decimal("10"),
        primary_strategy_id="trend_momentum_stage2",
        score=Decimal(rank_score),
        strategy_score=Decimal(rank_score),
        rank_score=Decimal(rank_score),
        trigger_price=Decimal("10.2"),
        initial_stop=Decimal("9.5"),
        target_1=Decimal("11"),
        card_json=json.dumps({"instrument_label": instrument_id}),
        created_at=datetime(2026, 1, 2, 9, minute, tzinfo=timezone.utc),
    )
