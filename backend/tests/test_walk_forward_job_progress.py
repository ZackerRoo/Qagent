from datetime import date, datetime, timedelta, timezone

from qagent.storage.repository import WalkForwardJobRecord


def _walk_forward_job(
    *,
    phase: str = "portfolio_channel_selection",
    current_date: date | None,
    decision_dates: list[date],
    total_snapshots: int | None = None,
) -> WalkForwardJobRecord:
    now = datetime.now(timezone.utc)
    return WalkForwardJobRecord(
        job_id="walk-forward-progress",
        provider="free",
        status="running",
        phase=phase,
        start_date=decision_dates[0],
        end_date=decision_dates[-1],
        dataset_revision=1,
        rebalance_step_sessions=10,
        lookback_days=400,
        total_snapshots=(len(decision_dates) if total_snapshots is None else total_snapshots),
        processed_snapshots=len(decision_dates),
        current_date=current_date,
        lease_maintenance_count=0,
        lease_recovery_count=0,
        last_lease_heartbeat_at=None,
        checkpoints=[
            {"decision_date": decision_date.isoformat()}
            for decision_date in reversed(decision_dates)
        ],
        experiment_manifest={},
        result_run_id=None,
        error=None,
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
    )


def test_portfolio_channel_selection_progress_tracks_processed_dates():
    decision_dates = [date(2025, 1, 2) + timedelta(days=index) for index in range(12)]

    progress = [
        _walk_forward_job(
            current_date=decision_dates[index],
            decision_dates=decision_dates,
        ).progress
        for index in (0, 3, 7, 11)
    ]

    assert progress == [92, 93, 94, 94]
    assert progress == sorted(progress)
    assert (
        _walk_forward_job(
            phase="portfolio_channel_backtests",
            current_date=decision_dates[-1],
            decision_dates=decision_dates,
        ).progress
        == 95
    )


def test_portfolio_channel_selection_requires_auditable_date_counts():
    decision_dates = [date(2025, 1, 2) + timedelta(days=index) for index in range(4)]

    assert (
        _walk_forward_job(
            current_date=None,
            decision_dates=decision_dates,
        ).progress
        == 92
    )
    assert (
        _walk_forward_job(
            current_date=decision_dates[-1],
            decision_dates=decision_dates,
            total_snapshots=5,
        ).progress
        == 92
    )
    assert (
        _walk_forward_job(
            current_date=date(2025, 2, 1),
            decision_dates=decision_dates,
        ).progress
        == 92
    )
