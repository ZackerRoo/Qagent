from datetime import datetime, timedelta, timezone
from threading import Event, get_ident

from qagent.jobs.automation_scheduler import (
    AutomationScheduler,
    AutoProcessingCycleResult,
    AutoProcessingSettings,
)


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
