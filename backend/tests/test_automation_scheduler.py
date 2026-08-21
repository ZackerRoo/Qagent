from datetime import datetime, timedelta, timezone
from threading import Event, get_ident

from qagent.jobs.automation_scheduler import (
    AutomationScheduler,
    AutomationSchedulerCheckpoint,
    AutoProcessingCycleResult,
    AutoProcessingSettings,
)
from qagent.jobs import automation_scheduler as scheduler_module


def test_refresh_if_due_runs_overdue_cycle_off_caller_thread():
    scheduler = AutomationScheduler()
    settings = AutoProcessingSettings(
        provider="fixture",
        symbols="US:TEST",
        interval_seconds=60,
        run_scan=False,
        seed_paper=False,
        update_paper=False,
        run_alerts=False,
    )
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._settings = settings
        scheduler._next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    caller_thread = get_ident()
    runner_threads: list[int] = []
    completed = Event()

    def runner(cycle_settings: AutoProcessingSettings) -> AutoProcessingCycleResult:
        runner_threads.append(get_ident())
        now = datetime.now(timezone.utc)
        completed.set()
        return AutoProcessingCycleResult(
            provider=cycle_settings.provider,
            started_at=now,
            finished_at=now,
            scan_status="skipped",
        )

    state = scheduler.refresh_if_due(runner)

    assert state.enabled is True
    assert completed.wait(timeout=1.0)
    assert len(runner_threads) == 1
    assert runner_threads[0] != caller_thread
    scheduler.stop()


def test_refresh_if_due_wakes_live_thread_after_wall_clock_deadline_passes():
    scheduler = AutomationScheduler()
    settings = AutoProcessingSettings(
        provider="fixture",
        symbols="US:TEST",
        interval_seconds=60,
        run_scan=False,
        seed_paper=False,
        update_paper=False,
        run_alerts=False,
    )
    completed = Event()

    def runner(cycle_settings: AutoProcessingSettings) -> AutoProcessingCycleResult:
        now = datetime.now(timezone.utc)
        completed.set()
        return AutoProcessingCycleResult(
            provider=cycle_settings.provider,
            started_at=now,
            finished_at=now,
            scan_status="skipped",
        )

    with scheduler._lock:
        scheduler._enabled = True
        scheduler._settings = settings
        scheduler._next_run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    scheduler._ensure_loop_thread(runner)

    with scheduler._lock:
        scheduler._next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    state = scheduler.refresh_if_due(runner)

    assert state.enabled is True
    assert completed.wait(timeout=1.0)
    assert scheduler.state().run_count == 1
    scheduler.stop()


def test_loop_rechecks_wall_clock_without_status_request(monkeypatch):
    scheduler = AutomationScheduler()
    settings = AutoProcessingSettings(
        provider="fixture",
        symbols="US:TEST",
        interval_seconds=60,
        run_scan=False,
        seed_paper=False,
        update_paper=False,
        run_alerts=False,
    )
    current_time = [datetime(2026, 8, 11, 1, 0, tzinfo=timezone.utc)]
    completed = Event()

    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: current_time[0])
    monkeypatch.setattr(
        scheduler_module,
        "SCHEDULER_CLOCK_RECHECK_SECONDS",
        0.01,
    )

    def runner(cycle_settings: AutoProcessingSettings) -> AutoProcessingCycleResult:
        completed.set()
        return AutoProcessingCycleResult(
            provider=cycle_settings.provider,
            started_at=current_time[0],
            finished_at=current_time[0],
            scan_status="skipped",
        )

    with scheduler._lock:
        scheduler._enabled = True
        scheduler._settings = settings
        scheduler._next_run_at = current_time[0] + timedelta(hours=1)
    scheduler._ensure_loop_thread(runner)

    current_time[0] += timedelta(hours=2)

    assert completed.wait(timeout=1.0)
    assert scheduler.state().run_count == 1
    scheduler.stop()


def test_scheduler_restores_completed_cycle_checkpoint():
    scheduler = AutomationScheduler()
    now = datetime.now(timezone.utc)
    checkpoint = AutomationSchedulerCheckpoint(
        run_count=7,
        last_started_at=now - timedelta(seconds=2),
        last_completed_at=now,
        last_result=AutoProcessingCycleResult(
            provider="free",
            started_at=now - timedelta(seconds=2),
            finished_at=now,
            scan_status="completed",
            paper_created=3,
        ),
    )

    scheduler.restore_checkpoint(checkpoint)
    state = scheduler.state()

    assert state.run_count == 7
    assert state.last_completed_at == now
    assert state.last_result is not None
    assert state.last_result.paper_created == 3
