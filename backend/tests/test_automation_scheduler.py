import time
from datetime import datetime, timedelta, timezone
from threading import Event, get_ident
import pytest

from qagent.jobs.automation_scheduler import (
    AutomationScheduler,
    AutomationSchedulerCheckpoint,
    AutoProcessingCycleResult,
    AutoProcessingSettings,
    current_automation_cycle_invocation,
)
from qagent.jobs import automation_scheduler as scheduler_module
from qagent.storage.automation_runtime import (
    AutomationCycleBusyError,
    AutomationCycleConflictError,
    AutomationCycleTerminatedError,
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


def test_busy_loser_does_not_emit_listener_checkpoint():
    events = []
    scheduler = AutomationScheduler(state_listener=events.append)

    def busy(_settings):
        raise AutomationCycleBusyError("winner owns lease")

    with pytest.raises(AutomationCycleBusyError):
        scheduler.run_once(AutoProcessingSettings(), busy)
    assert events == []
    assert scheduler.state().run_count == 0


def test_manual_cycle_conflict_is_not_swallowed_by_generic_error_path():
    scheduler = AutomationScheduler()

    def conflict(_settings):
        raise AutomationCycleConflictError("different facts")

    with pytest.raises(AutomationCycleConflictError, match="different facts"):
        scheduler.run_once(AutoProcessingSettings(), conflict)
    assert scheduler.state().run_count == 0
    assert scheduler.state().last_result is None


def test_scheduled_coordination_error_does_not_consume_or_hot_spin(monkeypatch):
    now = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
    due = now - timedelta(minutes=1)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler = AutomationScheduler()
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._generation = 4
        scheduler._cycle_due_at = due
        scheduler._next_run_at = now

    def busy(_settings):
        raise AutomationCycleBusyError("winner owns lease")

    assert scheduler._execute(AutoProcessingSettings(), busy, generation=4) is False
    state = scheduler.state()
    assert state.run_count == 0
    assert state.last_result is None
    assert state.cycle_due_at == due
    assert state.next_run_at == now + timedelta(
        seconds=scheduler_module.SCHEDULER_CLOCK_RECHECK_SECONDS
    )


def test_unconfirmed_generic_failure_does_not_advance_or_report_terminal(monkeypatch):
    now = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
    due = now - timedelta(minutes=1)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler = AutomationScheduler()
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._generation = 5
        scheduler._cycle_due_at = due
        scheduler._next_run_at = now

    def failed_finalize(_settings):
        raise RuntimeError("terminal persistence failed")

    assert scheduler._execute(
        AutoProcessingSettings(),
        failed_finalize,
        generation=5,
    ) is True
    state = scheduler.state()
    assert state.run_count == 0
    assert state.cycle_due_at == due
    assert state.next_run_at == now + timedelta(minutes=5)
    assert state.last_result is not None
    assert (
        state.last_result.data_health["automation_cycle_status"]
        == "partial_retry_same_slot"
    )
    assert (
        state.last_result.data_health["automation_retry_terminal_reason"]
        == "finalization_unconfirmed"
    )


def test_confirmed_abort_exception_reports_terminal_and_advances(monkeypatch):
    now = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler = AutomationScheduler()
    settings = AutoProcessingSettings(interval_seconds=60)
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._generation = 6
        scheduler._settings = settings
        scheduler._cycle_due_at = now
        scheduler._next_run_at = now

    def confirmed_abort(_settings):
        raise AutomationCycleTerminatedError("persisted terminal failure")

    assert scheduler._execute(
        settings,
        confirmed_abort,
        generation=6,
    ) is True
    state = scheduler.state()
    assert state.run_count == 1
    assert state.last_result is not None
    assert state.last_result.data_health["automation_cycle_status"] == "deferred_with_alert"
    assert state.next_run_at == now + timedelta(minutes=1)


def test_scheduled_partial_retries_same_immutable_cycle_due(monkeypatch):
    now = datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)
    due = now - timedelta(minutes=2)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler = AutomationScheduler()
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._generation = 7
        scheduler._cycle_due_at = due
        scheduler._next_run_at = now

    def partial(settings):
        return AutoProcessingCycleResult(
            provider=settings.provider,
            started_at=now,
            finished_at=now,
            scan_status="waiting",
            errors=["waiting_snapshot"],
            data_health={"automation_cycle_status": "partial"},
        )

    assert scheduler._execute(AutoProcessingSettings(), partial, generation=7) is True
    state = scheduler.state()
    assert state.run_count == 0
    assert state.cycle_due_at == due
    assert state.next_run_at == now + timedelta(seconds=300)


def test_scheduled_partial_uses_persisted_retry_time(monkeypatch):
    now = datetime(2026, 8, 29, 4, 0, tzinfo=timezone.utc)
    retry_at = now + timedelta(minutes=17)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler = AutomationScheduler()
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._generation = 8
        scheduler._cycle_due_at = now - timedelta(minutes=1)
        scheduler._next_run_at = now

    def partial(settings):
        return AutoProcessingCycleResult(
            provider=settings.provider,
            started_at=now,
            finished_at=now,
            scan_status="failed",
            errors=["provider timeout"],
            data_health={
                "automation_cycle_status": "partial_retry_same_slot",
                "automation_retry_next_at": retry_at.isoformat(),
            },
        )

    assert scheduler._execute(AutoProcessingSettings(), partial, generation=8) is True
    assert scheduler.state().next_run_at == retry_at


def test_manual_run_never_moves_enabled_scheduled_clock(monkeypatch):
    now = datetime(2026, 8, 29, 4, 0, tzinfo=timezone.utc)
    scheduled_next = now + timedelta(minutes=23)
    scheduled_due = now + timedelta(minutes=20)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    scheduler = AutomationScheduler()
    scheduled_settings = AutoProcessingSettings(provider="free", interval_seconds=1800)
    manual_settings = AutoProcessingSettings(provider="fixture", interval_seconds=60)
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._generation = 9
        scheduler._settings = scheduled_settings
        scheduler._next_run_at = scheduled_next
        scheduler._cycle_due_at = scheduled_due

    def manual(settings):
        assert settings == manual_settings
        return AutoProcessingCycleResult(
            provider=settings.provider,
            started_at=now,
            finished_at=now,
            scan_status="failed",
            errors=["provider timeout"],
            data_health={
                "automation_cycle_status": "partial_retry_same_slot",
                "automation_retry_next_at": (now + timedelta(minutes=5)).isoformat(),
            },
        )

    assert scheduler._execute(manual_settings, manual) is True
    state = scheduler.state()
    assert state.next_run_at == scheduled_next
    assert state.cycle_due_at == scheduled_due
    assert state.settings == scheduled_settings


def test_scheduled_deferred_cycle_advances_slot_and_runs_paper_next_cycle(monkeypatch):
    current_time = [datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)]
    first_due = current_time[0] - timedelta(minutes=2)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: current_time[0])
    scheduler = AutomationScheduler()
    settings = AutoProcessingSettings(interval_seconds=600)
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._generation = 9
        scheduler._settings = settings
        scheduler._cycle_due_at = first_due
        scheduler._next_run_at = current_time[0]
    observed_due: list[datetime | None] = []
    paper_runs = 0

    def runner(cycle_settings):
        nonlocal paper_runs
        paper_runs += 1
        observed_due.append(current_automation_cycle_invocation().due_at)
        issue_cycle = paper_runs == 1
        return AutoProcessingCycleResult(
            provider=cycle_settings.provider,
            started_at=current_time[0],
            finished_at=current_time[0],
            scan_status="skipped",
            paper_total=paper_runs,
            issues=["factor_shadow: waiting_for_maturity"] if issue_cycle else [],
            data_health={
                "automation_cycle_status": (
                    "completed_with_deferred_or_issues" if issue_cycle else "succeeded"
                )
            },
        )

    assert scheduler._execute(settings, runner, generation=9) is True
    first_state = scheduler.state()
    assert first_state.run_count == 1
    assert first_state.cycle_due_at == current_time[0] + timedelta(seconds=600)
    assert first_state.last_error == "factor_shadow: waiting_for_maturity"

    current_time[0] = first_state.cycle_due_at
    assert scheduler._execute(settings, runner, generation=9) is True
    assert paper_runs == 2
    assert observed_due == [first_due, first_state.cycle_due_at]
    assert scheduler.state().run_count == 2


def test_inflight_cycle_due_survives_two_restart_checkpoints(monkeypatch):
    now = datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)
    original_due = now - timedelta(minutes=3)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: now)
    first = AutomationScheduler()
    first.restore_checkpoint(
        AutomationSchedulerCheckpoint(
            next_run_at=now - timedelta(minutes=1),
            cycle_due_at=original_due,
            in_flight=True,
            scheduled_interval_seconds=600,
        )
    )
    first.resume(
        AutoProcessingSettings(interval_seconds=600),
        lambda settings: _partial(settings, now),
    )
    deadline = time.monotonic() + 1
    while first.state().last_result is None and time.monotonic() < deadline:
        time.sleep(0.01)
    first_state = first.state()
    assert first_state.cycle_due_at == original_due
    first.shutdown()

    second = AutomationScheduler()
    second.restore_checkpoint(
        AutomationSchedulerCheckpoint(
            run_count=first_state.run_count,
            last_started_at=first_state.last_started_at,
            last_completed_at=first_state.last_completed_at,
            last_error=first_state.last_error,
            last_result=first_state.last_result,
            next_run_at=first_state.next_run_at,
            cycle_due_at=first_state.cycle_due_at,
            in_flight=True,
            scheduled_interval_seconds=600,
        )
    )
    second.resume(
        AutoProcessingSettings(interval_seconds=600),
        lambda settings: _partial(settings, now),
    )
    assert second.state().cycle_due_at == original_due
    second.stop()


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


def _partial(settings: AutoProcessingSettings, now: datetime) -> AutoProcessingCycleResult:
    return AutoProcessingCycleResult(
        provider=settings.provider,
        started_at=now,
        finished_at=now,
        scan_status="waiting",
        errors=["waiting"],
        data_health={"automation_cycle_status": "partial"},
    )
