import time
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


def test_resume_before_deadline_does_not_repeat_paper_side_effect(monkeypatch):
    scheduler = AutomationScheduler()
    now = datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler.restore_checkpoint(
        AutomationSchedulerCheckpoint(
            run_count=1,
            last_started_at=now - timedelta(minutes=5, seconds=10),
            last_completed_at=now - timedelta(minutes=5),
            next_run_at=now + timedelta(minutes=5),
            scheduled_interval_seconds=600,
        )
    )
    paper_creations: list[str] = []

    def runner(cycle_settings: AutoProcessingSettings) -> AutoProcessingCycleResult:
        paper_creations.append("created")
        return AutoProcessingCycleResult(
            provider=cycle_settings.provider,
            started_at=now,
            finished_at=now,
            scan_status="skipped",
            paper_created=1,
        )

    state = scheduler.resume(
        AutoProcessingSettings(interval_seconds=600),
        runner,
    )

    assert state.next_run_at == now + timedelta(minutes=5)
    assert paper_creations == []
    assert scheduler.state().run_count == 1
    scheduler.stop()


def test_resume_runs_immediately_only_when_persisted_cycle_is_due(monkeypatch):
    scheduler = AutomationScheduler()
    now = datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler.restore_checkpoint(
        AutomationSchedulerCheckpoint(
            run_count=1,
            last_completed_at=now - timedelta(minutes=11),
            next_run_at=now - timedelta(minutes=1),
            scheduled_interval_seconds=600,
        )
    )
    completed = Event()

    def runner(cycle_settings: AutoProcessingSettings) -> AutoProcessingCycleResult:
        completed.set()
        return AutoProcessingCycleResult(
            provider=cycle_settings.provider,
            started_at=now,
            finished_at=now,
            scan_status="skipped",
        )

    state = scheduler.resume(
        AutoProcessingSettings(interval_seconds=600),
        runner,
    )

    assert state.next_run_at == now
    assert completed.wait(timeout=1.0)
    assert scheduler.state().run_count == 2
    scheduler.stop()


def test_resume_without_run_history_uses_one_interval_safe_fallback(monkeypatch):
    scheduler = AutomationScheduler()
    now = datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    completed = Event()

    state = scheduler.resume(
        AutoProcessingSettings(interval_seconds=600),
        lambda settings: _complete_cycle(settings, now, completed),
    )

    assert state.next_run_at == now + timedelta(minutes=10)
    assert completed.is_set() is False
    assert scheduler.state().run_count == 0
    scheduler.stop()


def test_resume_recalculates_deadline_with_current_interval(monkeypatch):
    scheduler = AutomationScheduler()
    now = datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler.restore_checkpoint(
        AutomationSchedulerCheckpoint(
            run_count=1,
            # A former 60-second cadence would be overdue. The restored
            # 120-second setting must be applied instead.
            last_completed_at=(now - timedelta(seconds=90)).replace(tzinfo=None),
            next_run_at=now - timedelta(seconds=30),
            scheduled_interval_seconds=60,
        )
    )
    completed = Event()

    state = scheduler.resume(
        AutoProcessingSettings(interval_seconds=120),
        lambda settings: _complete_cycle(settings, now, completed),
    )

    assert state.next_run_at == now + timedelta(seconds=30)
    assert completed.is_set() is False
    assert scheduler.state().run_count == 1
    scheduler.stop()


def test_resume_retries_explicit_in_flight_checkpoint(monkeypatch):
    scheduler = AutomationScheduler()
    now = datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler.restore_checkpoint(
        AutomationSchedulerCheckpoint(
            run_count=3,
            last_started_at=now - timedelta(minutes=2),
            last_completed_at=now - timedelta(hours=1),
            next_run_at=now - timedelta(minutes=2),
            in_flight=True,
            scheduled_interval_seconds=600,
        )
    )
    completed = Event()

    state = scheduler.resume(
        AutoProcessingSettings(interval_seconds=600),
        lambda settings: _complete_cycle(settings, now, completed),
    )

    assert state.next_run_at == now
    assert state.last_error == "interrupted_cycle_retry_after_restart"
    assert completed.wait(timeout=1.0)
    assert scheduler.state().run_count == 4
    scheduler.stop()


def test_active_interval_change_replans_without_immediate_cycle(monkeypatch):
    scheduler = AutomationScheduler()
    now = datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler.restore_checkpoint(
        AutomationSchedulerCheckpoint(
            run_count=1,
            last_completed_at=now - timedelta(seconds=30),
            next_run_at=now + timedelta(seconds=30),
            scheduled_interval_seconds=60,
        )
    )
    completed = Event()

    def runner(settings: AutoProcessingSettings) -> AutoProcessingCycleResult:
        return _complete_cycle(settings, now, completed)

    scheduler.resume(AutoProcessingSettings(interval_seconds=60), runner)

    state = scheduler.start(AutoProcessingSettings(interval_seconds=120), runner)

    assert state.settings.interval_seconds == 120
    assert state.next_run_at == now + timedelta(seconds=90)
    assert completed.is_set() is False
    scheduler.stop()


def test_stop_during_blocked_cycle_rejects_restart_until_runner_finishes():
    scheduler = AutomationScheduler()
    entered = Event()
    release = Event()

    def runner(settings: AutoProcessingSettings) -> AutoProcessingCycleResult:
        entered.set()
        release.wait(timeout=3.0)
        now = datetime.now(timezone.utc)
        return AutoProcessingCycleResult(
            provider=settings.provider,
            started_at=now,
            finished_at=now,
            scan_status="skipped",
        )

    scheduler.start(AutoProcessingSettings(interval_seconds=60), runner)
    assert entered.wait(timeout=1.0)

    stopped = scheduler.stop()
    assert stopped.status == "stopping"
    try:
        scheduler.start(AutoProcessingSettings(interval_seconds=120), runner)
    except RuntimeError as exc:
        assert str(exc) == "automation scheduler is still stopping"
    else:  # pragma: no cover - protects the single-worker invariant.
        raise AssertionError("restart should be rejected while the old runner is active")

    release.set()
    deadline = time.monotonic() + 1.0
    while scheduler._run_lock.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert scheduler._run_lock.locked() is False
    final_state = scheduler.state()
    assert final_state.enabled is False
    assert final_state.status == "idle"
    assert final_state.next_run_at is None


def _complete_cycle(
    settings: AutoProcessingSettings,
    now: datetime,
    completed: Event,
) -> AutoProcessingCycleResult:
    completed.set()
    return AutoProcessingCycleResult(
        provider=settings.provider,
        started_at=now,
        finished_at=now,
        scan_status="skipped",
    )
