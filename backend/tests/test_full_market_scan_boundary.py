from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from qagent.db import Base, create_db_engine, create_session_factory
from qagent.jobs.daily_scan import ScanItem
from qagent.jobs.full_market import (
    _frozen_full_market_scan_end,
    _market_data_reliability_health,
)
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import OpportunitySnapshotRow, ScanRunRow


SIGNAL_DATE = date(2026, 7, 29)
PROVIDER = "free"


def _repo(tmp_path) -> QagentRepository:
    database_url = f"sqlite:///{tmp_path / 'full-market-boundary.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    return QagentRepository(create_session_factory(database_url))


def _valid_health(symbol_count: int, *, signal_date: date = SIGNAL_DATE) -> dict[str, str]:
    total_batches = max((symbol_count + 199) // 200, 1)
    return {
        "full_market_scan_mode": "full_market_batch",
        "full_market_total_symbols": str(symbol_count),
        "full_market_scanned_symbols": str(symbol_count),
        "full_market_total_batches": str(total_batches),
        "full_market_completed_batches": str(total_batches),
        "full_market_error_count": "0",
        "full_market_batches_complete": "true",
        "full_market_scan_complete": "true",
        "full_market_signal_date": signal_date.isoformat(),
    }


def _insert_run(
    repo: QagentRepository,
    *,
    run_id: str,
    symbol_count: int = 500,
    scanned: int | None = None,
    mode: str = "full_market_batch",
    health: dict[str, str] | None = None,
    snapshot_dates: list[date | None] | None = None,
    created_order: int = 0,
) -> None:
    symbols = [f"CN:{100000 + index:06d}" for index in range(symbol_count)]
    dates = [SIGNAL_DATE] if snapshot_dates is None else snapshot_dates
    created_at = datetime.combine(
        SIGNAL_DATE,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(seconds=created_order)
    with repo.session_factory.begin() as session:
        session.add(
            ScanRunRow(
                run_id=run_id,
                provider=PROVIDER,
                mode=mode,
                symbols=json.dumps(symbols),
                scanned=symbol_count if scanned is None else scanned,
                cards=len(dates),
                data_health=json.dumps(
                    _valid_health(symbol_count) if health is None else health,
                    sort_keys=True,
                ),
                started_at=created_at - timedelta(minutes=2),
                completed_at=created_at - timedelta(minutes=1),
                created_at=created_at,
            )
        )
        for index, snapshot_date in enumerate(dates):
            session.add(
                OpportunitySnapshotRow(
                    snapshot_id=f"{run_id}:snapshot-{index}",
                    run_id=run_id,
                    card_id=f"{run_id}:card-{index}",
                    instrument_id=symbols[index],
                    market="CN",
                    status="watch",
                    signal_date=snapshot_date,
                    latest_close=Decimal("10"),
                    primary_strategy_id="trend",
                    score=Decimal("0.8"),
                    strategy_score=Decimal("0.8"),
                    rank_score=Decimal("0.8"),
                    trigger_price=Decimal("10"),
                    initial_stop=Decimal("9"),
                    target_1=Decimal("12"),
                    card_json="{}",
                    created_at=created_at,
                )
            )


def test_complete_scan_ignores_newer_normal_fifty_symbol_scan(tmp_path):
    repo = _repo(tmp_path)
    _insert_run(repo, run_id="complete-full-market", created_order=1)
    _insert_run(
        repo,
        run_id="ordinary-fifty",
        symbol_count=50,
        mode="free",
        created_order=2,
    )

    bundle = repo.get_latest_complete_daily_scan_with_snapshots(
        provider=PROVIDER,
        signal_date=SIGNAL_DATE,
        minimum_scanned=50,
    )

    assert bundle is not None
    assert bundle.run.run_id == "complete-full-market"
    assert {snapshot.run_id for snapshot in bundle.snapshots} == {"complete-full-market"}


def test_normal_scan_never_qualifies_as_complete_full_market_scan(tmp_path):
    repo = _repo(tmp_path)
    _insert_run(
        repo,
        run_id="ordinary-fifty",
        symbol_count=50,
        mode="free",
    )

    assert (
        repo.get_latest_complete_daily_scan_with_snapshots(
            provider=PROVIDER,
            signal_date=SIGNAL_DATE,
            minimum_scanned=1,
        )
        is None
    )


@pytest.mark.parametrize(
    ("case", "scanned", "health"),
    [
        ("legacy", 500, {}),
        ("row-count-mismatch", 501, _valid_health(500)),
        (
            "health-scan-count-mismatch",
            500,
            {**_valid_health(500), "full_market_scanned_symbols": "499"},
        ),
        (
            "health-total-count-mismatch",
            500,
            {**_valid_health(500), "full_market_total_symbols": "499"},
        ),
        (
            "incomplete-batches",
            500,
            {**_valid_health(500), "full_market_completed_batches": "2"},
        ),
        (
            "batch-flag-false",
            500,
            {**_valid_health(500), "full_market_batches_complete": "false"},
        ),
        (
            "scan-flag-false",
            500,
            {**_valid_health(500), "full_market_scan_complete": "false"},
        ),
        (
            "has-errors",
            500,
            {**_valid_health(500), "full_market_error_count": "1"},
        ),
        (
            "wrong-signal-date",
            500,
            {
                **_valid_health(500),
                "full_market_signal_date": "2026-07-28",
            },
        ),
    ],
)
def test_incomplete_or_legacy_full_market_runs_fail_closed(
    tmp_path,
    case,
    scanned,
    health,
):
    repo = _repo(tmp_path)
    _insert_run(
        repo,
        run_id=f"invalid-{case}",
        scanned=scanned,
        health=health,
    )

    assert (
        repo.get_latest_complete_daily_scan_with_snapshots(
            provider=PROVIDER,
            signal_date=SIGNAL_DATE,
            minimum_scanned=500,
        )
        is None
    )


@pytest.mark.parametrize(
    "snapshot_dates",
    [
        [SIGNAL_DATE, date(2026, 7, 28)],
        [SIGNAL_DATE, None],
    ],
)
def test_scan_with_mixed_or_missing_snapshot_dates_fails_closed(
    tmp_path,
    snapshot_dates,
):
    repo = _repo(tmp_path)
    _insert_run(
        repo,
        run_id="invalid-snapshot-dates",
        snapshot_dates=snapshot_dates,
    )

    assert (
        repo.get_latest_complete_daily_scan_with_snapshots(
            provider=PROVIDER,
            signal_date=SIGNAL_DATE,
            minimum_scanned=500,
        )
        is None
    )


def test_complete_empty_candidate_batch_uses_explicit_signal_date(tmp_path):
    repo = _repo(tmp_path)
    _insert_run(
        repo,
        run_id="complete-empty",
        snapshot_dates=[],
    )

    bundle = repo.get_latest_complete_daily_scan_with_snapshots(
        provider=PROVIDER,
        signal_date=SIGNAL_DATE,
        minimum_scanned=500,
    )

    assert bundle is not None
    assert bundle.run.run_id == "complete-empty"
    assert bundle.snapshots == []


def test_market_data_reliability_health_reports_complete_latest_session():
    health = _market_data_reliability_health(
        [
            ScanItem(
                instrument_id="CN:000001",
                status="watch",
                reason="ready",
                bars=400,
                signals=1,
                latest_trade_date=SIGNAL_DATE,
                provider="akshare",
            ),
            ScanItem(
                instrument_id="CN:000002",
                status="watch",
                reason="ready",
                bars=400,
                signals=1,
                latest_trade_date=SIGNAL_DATE,
                provider="baostock",
            ),
        ],
        expected_trade_date=SIGNAL_DATE,
        error_count=0,
        provider_error_count=0,
    )

    assert health["market_data_reliability_state"] == "ready"
    assert health["market_data_latest_session_current"] == "2"
    assert health["market_data_latest_session_coverage"] == "1.000000"
    assert health["market_data_source_mix"] == "akshare=1,baostock=1"
    assert health["market_data_current_source_mix"] == "akshare=1,baostock=1"
    assert health["market_data_stale_source_mix"] == ""
    assert health["market_data_recovery_action"] == "none"


def test_market_data_reliability_health_fails_closed_for_stale_or_missing_data():
    health = _market_data_reliability_health(
        [
            ScanItem(
                instrument_id="CN:000001",
                status="watch",
                reason="stale",
                bars=400,
                signals=0,
                latest_trade_date=SIGNAL_DATE - timedelta(days=1),
                provider="akshare",
            ),
            ScanItem(
                instrument_id="CN:000002",
                status="no_data",
                reason="missing",
                bars=0,
                signals=0,
            ),
        ],
        expected_trade_date=SIGNAL_DATE,
        error_count=0,
        provider_error_count=0,
    )

    assert health["market_data_reliability_state"] == "risk"
    assert health["market_data_latest_session_stale"] == "1"
    assert health["market_data_latest_session_missing"] == "1"
    assert health["market_data_latest_session_coverage"] == "0.000000"
    assert health["market_data_stale_source_mix"] == "akshare=1"
    assert health["market_data_stale_age_mix"] == "1_session=1"
    assert health["market_data_missing_reason_mix"] == "no_data=1"
    assert health["market_data_problem_status_mix"] == "no_data=1,watch=1"
    assert health["market_data_problem_samples"] == "CN:000002,CN:000001"
    assert (
        health["market_data_recovery_action"]
        == "settlement_retry_then_provider_repair"
    )


def test_full_market_scan_resume_keeps_frozen_expected_trade_date():
    created_at = datetime(2026, 7, 30, 1, 0, tzinfo=timezone.utc)

    assert _frozen_full_market_scan_end(
        {"full_market_expected_trade_date": SIGNAL_DATE.isoformat()},
        created_at,
    ) == SIGNAL_DATE
