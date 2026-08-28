from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread

from pydantic import BaseModel, Field

from qagent.storage.automation_runtime import (
    AutomationCycleBusyError,
    AutomationCycleConflictError,
    AutomationCycleTerminatedError,
    AutomationLeaseLostError,
    TERMINAL_CYCLE_STATUSES,
)
from qagent.jobs.automation_retry import retry_backoff


class AutoProcessingSettings(BaseModel):
    provider: str = "free"
    symbols: str | None = None
    interval_seconds: int = Field(default=1800, ge=5, le=24 * 60 * 60)
    include_etfs: bool = True
    run_scan: bool = True
    scan_max_age_minutes: int = Field(default=240, ge=5, le=7 * 24 * 60)
    batch_size: int = Field(default=200, ge=1, le=1000)
    max_symbols: int | None = Field(default=None, ge=1, le=20_000)
    sync_if_empty: bool = True
    sync_catalog_daily: bool = True
    seed_paper: bool = True
    seed_limit: int = Field(default=10, ge=1, le=50)
    update_paper: bool = True
    run_alerts: bool = True
    queue_alerts: bool = True
    run_forward_evidence: bool = True


class AutoProcessingCycleResult(BaseModel):
    provider: str
    started_at: datetime
    finished_at: datetime
    scan_status: str
    scan_started: bool = False
    scan_job_id: str | None = None
    paper_created: int = 0
    paper_total: int = 0
    paper_closed: int = 0
    alerts_triggered: int = 0
    errors: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


class AutoProcessingState(BaseModel):
    enabled: bool = False
    status: str = "idle"
    settings: AutoProcessingSettings
    run_count: int = 0
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    next_run_at: datetime | None = None
    cycle_due_at: datetime | None = None
    last_error: str | None = None
    last_result: AutoProcessingCycleResult | None = None


class AutomationSchedulerCheckpoint(BaseModel):
    """Runtime state that remains meaningful across an API restart."""

    run_count: int = Field(default=0, ge=0)
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_error: str | None = None
    last_result: AutoProcessingCycleResult | None = None
    next_run_at: datetime | None = None
    cycle_due_at: datetime | None = None
    in_flight: bool = False
    scheduled_interval_seconds: int | None = Field(default=None, ge=5, le=24 * 60 * 60)


CycleRunner = Callable[[AutoProcessingSettings], AutoProcessingCycleResult]
StateListener = Callable[[AutoProcessingState], None]
SCHEDULER_CLOCK_RECHECK_SECONDS = 5.0


@dataclass(frozen=True)
class AutomationCycleInvocation:
    trigger: str
    due_at: datetime | None = None
    idempotency_key: str | None = None


_cycle_invocation: ContextVar[AutomationCycleInvocation | None] = ContextVar(
    "automation_cycle_invocation",
    default=None,
)


def current_automation_cycle_invocation() -> AutomationCycleInvocation:
    return _cycle_invocation.get() or AutomationCycleInvocation(trigger="manual")


class AutomationScheduler:
    def __init__(self, state_listener: StateListener | None = None) -> None:
        self._lock = Lock()
        self._run_lock = Lock()
        self._stop_event = Event()
        self._wake_event = Event()
        self._thread: Thread | None = None
        self._generation = 0
        self._settings = AutoProcessingSettings()
        self._enabled = False
        self._status = "idle"
        self._run_count = 0
        self._last_started_at: datetime | None = None
        self._last_completed_at: datetime | None = None
        self._next_run_at: datetime | None = None
        self._cycle_due_at: datetime | None = None
        self._last_error: str | None = None
        self._last_result: AutoProcessingCycleResult | None = None
        self._restored_next_run_at: datetime | None = None
        self._restored_cycle_due_at: datetime | None = None
        self._restored_in_flight = False
        self._restored_interval_seconds: int | None = None
        self._state_listener = state_listener

    def state(self) -> AutoProcessingState:
        with self._lock:
            return self._state_unlocked()

    def configure(self, settings: AutoProcessingSettings) -> AutoProcessingState:
        with self._lock:
            self._settings = settings
            if not self._enabled:
                self._next_run_at = None
                self._cycle_due_at = None
            return self._state_unlocked()

    def set_state_listener(self, listener: StateListener | None) -> None:
        with self._lock:
            self._state_listener = listener

    def restore_checkpoint(self, checkpoint: AutomationSchedulerCheckpoint) -> None:
        """Restore persisted runtime facts before deciding how to resume."""

        with self._lock:
            self._run_count = checkpoint.run_count
            self._last_started_at = checkpoint.last_started_at
            self._last_completed_at = checkpoint.last_completed_at
            self._last_error = checkpoint.last_error
            self._last_result = checkpoint.last_result
            self._restored_next_run_at = checkpoint.next_run_at
            self._restored_cycle_due_at = checkpoint.cycle_due_at
            self._restored_in_flight = checkpoint.in_flight
            self._restored_interval_seconds = checkpoint.scheduled_interval_seconds

    def refresh_if_due(self, runner: CycleRunner) -> AutoProcessingState:
        # Status reads may restore a missing loop, but the request thread must never
        # execute a potentially slow processing cycle itself.
        self._ensure_loop_thread(runner)
        with self._lock:
            return self._state_unlocked()

    def start(self, settings: AutoProcessingSettings, runner: CycleRunner) -> AutoProcessingState:
        return self._start(settings, runner, next_run_at=_utc_now())

    def resume(
        self,
        settings: AutoProcessingSettings,
        runner: CycleRunner,
    ) -> AutoProcessingState:
        """Resume a persisted schedule without adding a restart-only cycle."""

        now = _utc_now()
        with self._lock:
            last_completed_at = _latest_utc_datetime(
                self._last_completed_at,
                self._last_result.finished_at if self._last_result is not None else None,
            )
            restored_due = _as_utc(self._restored_next_run_at)
            restored_cycle_due = _as_utc(self._restored_cycle_due_at)
            interval_changed = (
                self._restored_interval_seconds is not None
                and self._restored_interval_seconds != settings.interval_seconds
            )
            if self._restored_in_flight or (
                restored_due is None
                and self._last_started_at is not None
                and last_completed_at is None
            ):
                # The prior process died inside a cycle. Retrying is explicitly
                # at-least-once; do not mislabel its start time as a completion.
                cycle_due_at = (
                    restored_cycle_due
                    or restored_due
                    or _as_utc(self._last_started_at)
                    or now
                )
                next_run_at = now
                self._last_error = "interrupted_cycle_retry_after_restart"
            elif restored_due is not None and not interval_changed:
                next_run_at = restored_due
                cycle_due_at = restored_cycle_due or restored_due
            elif last_completed_at is not None:
                next_run_at = last_completed_at + timedelta(seconds=settings.interval_seconds)
                cycle_due_at = next_run_at
            else:
                # Legacy records without any trustworthy timing evidence cannot
                # prove a cycle is overdue. Wait one interval to avoid a
                # restart-only duplicate side effect.
                next_run_at = now + timedelta(seconds=settings.interval_seconds)
                cycle_due_at = next_run_at
            self._restored_next_run_at = None
            self._restored_cycle_due_at = None
            self._restored_in_flight = False
            self._restored_interval_seconds = None
        return self._start(
            settings,
            runner,
            next_run_at=max(next_run_at, now),
            cycle_due_at=cycle_due_at,
        )

    def _start(
        self,
        settings: AutoProcessingSettings,
        runner: CycleRunner,
        *,
        next_run_at: datetime,
        cycle_due_at: datetime | None = None,
    ) -> AutoProcessingState:
        with self._lock:
            if not self._enabled and self._run_lock.locked():
                self._status = "stopping"
                raise RuntimeError("automation scheduler is still stopping")
            self._settings = settings
            if self._enabled and self._thread and self._thread.is_alive():
                last_run_at = _latest_utc_datetime(
                    self._last_started_at,
                    self._last_completed_at,
                    self._last_result.finished_at if self._last_result is not None else None,
                )
                if last_run_at is not None:
                    self._next_run_at = max(
                        last_run_at + timedelta(seconds=settings.interval_seconds),
                        _utc_now(),
                    )
                    self._cycle_due_at = self._next_run_at
                else:
                    self._next_run_at = self._next_run_at or next_run_at
                    self._cycle_due_at = self._cycle_due_at or cycle_due_at or next_run_at
                self._wake_event.set()
                return self._state_unlocked()
            self._enabled = True
            self._status = "idle"
            self._next_run_at = next_run_at
            self._cycle_due_at = cycle_due_at or next_run_at
            self._generation += 1
            generation = self._generation
            self._stop_event = Event()
            stop_event = self._stop_event
            self._wake_event = Event()
            wake_event = self._wake_event
            self._thread = Thread(
                target=self._loop,
                args=(runner, stop_event, wake_event, generation),
                daemon=True,
            )
            self._thread.start()
            return self._state_unlocked()

    def stop(self) -> AutoProcessingState:
        thread: Thread | None
        with self._lock:
            self._generation += 1
            self._enabled = False
            self._status = "stopping" if self._run_lock.locked() else "idle"
            self._next_run_at = None
            self._cycle_due_at = None
            self._stop_event.set()
            self._wake_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        with self._lock:
            if not self._run_lock.locked():
                self._status = "idle"
            return self._state_unlocked()

    def shutdown(self) -> AutoProcessingState:
        """Stop this process's loop while preserving its persisted resume intent."""

        with self._lock:
            self._stop_event.set()
            self._wake_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        with self._lock:
            return self._state_unlocked()

    def run_once(
        self,
        settings: AutoProcessingSettings,
        runner: CycleRunner,
        *,
        idempotency_key: str | None = None,
    ) -> AutoProcessingState:
        self._execute(settings, runner, idempotency_key=idempotency_key)
        with self._lock:
            return self._state_unlocked()

    def _loop(
        self,
        runner: CycleRunner,
        stop_event: Event,
        wake_event: Event,
        generation: int,
    ) -> None:
        while not stop_event.is_set():
            with self._lock:
                if generation != self._generation:
                    return
                settings = self._settings
                next_run_at = self._next_run_at
            if next_run_at is not None:
                wait_seconds = (next_run_at - _utc_now()).total_seconds()
                if wait_seconds > 0:
                    # Event.wait() uses a relative monotonic timeout. That
                    # timeout may pause while a Mac sleeps, so a single long
                    # wait can remain pending even after the wall-clock
                    # deadline has passed. Recheck UTC in short slices so the
                    # scheduler compensates promptly after wake without an API
                    # request having to nudge it.
                    wake_event.wait(min(wait_seconds, SCHEDULER_CLOCK_RECHECK_SECONDS))
                    wake_event.clear()
                    continue
            if not self._execute(settings, runner, generation=generation):
                stop_event.wait(0.05)
        with self._lock:
            if generation == self._generation:
                self._enabled = False
                if self._status == "running":
                    self._status = "idle"
                self._next_run_at = None

    def _ensure_loop_thread(self, runner: CycleRunner) -> None:
        with self._lock:
            if not self._enabled:
                return
            if self._thread is not None and self._thread.is_alive():
                if self._next_run_at is not None and self._next_run_at <= _utc_now():
                    self._wake_event.set()
                return
            self._generation += 1
            generation = self._generation
            self._stop_event = Event()
            stop_event = self._stop_event
            self._wake_event = Event()
            wake_event = self._wake_event
            self._thread = Thread(
                target=self._loop,
                args=(runner, stop_event, wake_event, generation),
                daemon=True,
            )
            self._thread.start()

    def _execute(
        self,
        settings: AutoProcessingSettings,
        runner: CycleRunner,
        *,
        generation: int | None = None,
        idempotency_key: str | None = None,
    ) -> bool:
        if not self._run_lock.acquire(blocking=False):
            return False
        started_at = _utc_now()
        with self._lock:
            if generation is not None and generation != self._generation:
                self._run_lock.release()
                return False
            manual_scheduled_clock = (
                self._next_run_at,
                self._cycle_due_at,
                self._settings,
            )
            if generation is None and not self._enabled:
                self._settings = settings
            self._status = "running"
            due_at = self._cycle_due_at if generation is not None else None
            self._last_started_at = started_at
            if self._last_error != "interrupted_cycle_retry_after_restart":
                self._last_error = None
        invocation_token = _cycle_invocation.set(
            AutomationCycleInvocation(
                trigger="scheduled" if generation is not None else "manual",
                due_at=due_at,
                idempotency_key=idempotency_key,
            )
        )
        coordination_error: (
            AutomationCycleBusyError
            | AutomationLeaseLostError
            | AutomationCycleConflictError
            | None
        ) = None
        replayed = False
        cycle_terminal = True
        try:
            result = runner(settings)
        except (
            AutomationCycleBusyError,
            AutomationLeaseLostError,
            AutomationCycleConflictError,
        ) as exc:
            coordination_error = exc
            with self._lock:
                if generation is None or generation == self._generation:
                    self._status = "idle"
                if generation is not None and generation == self._generation:
                    # Coordination contention is not a cycle attempt, but the
                    # loop still needs a bounded recheck to avoid a hot spin.
                    self._next_run_at = _utc_now() + timedelta(
                        seconds=SCHEDULER_CLOCK_RECHECK_SECONDS
                    )
        except AutomationCycleTerminatedError as exc:
            cycle_terminal = True
            finished_at = _utc_now()
            result = AutoProcessingCycleResult(
                provider=settings.provider,
                started_at=started_at,
                finished_at=finished_at,
                scan_status="failed",
                errors=[str(exc)],
                data_health={
                    "automation_scheduler_error": str(exc)[:500],
                    "automation_cycle_status": "deferred_with_alert",
                    "automation_retry_terminal_reason": "unhandled_permanent_error",
                },
            )
            with self._lock:
                self._last_error = str(exc)
        except Exception as exc:  # pragma: no cover - route-level tests cover state output.
            cycle_terminal = False
            finished_at = _utc_now()
            result = AutoProcessingCycleResult(
                provider=settings.provider,
                started_at=started_at,
                finished_at=finished_at,
                scan_status="failed",
                errors=[str(exc)],
                data_health={
                    "automation_scheduler_error": str(exc)[:500],
                    "automation_cycle_status": "partial_retry_same_slot",
                    "automation_retry_terminal_reason": "finalization_unconfirmed",
                },
            )
            with self._lock:
                self._last_error = str(exc)
        else:
            finished_at = result.finished_at
            replayed = result.data_health.get("automation_cycle_replayed") == "true"
            cycle_terminal = (
                result.data_health.get("automation_cycle_status", "succeeded")
                in TERMINAL_CYCLE_STATUSES
            )
            with self._lock:
                reported = result.errors or result.issues
                self._last_error = "; ".join(reported)[:1000] if reported else None
        finally:
            _cycle_invocation.reset(invocation_token)
            with self._lock:
                if coordination_error is not None:
                    self._run_lock.release()
                else:
                    is_current = generation is None or generation == self._generation
                    if is_current:
                        self._status = "idle"
                    elif not self._enabled and self._status == "stopping":
                        # start() remains rejected while _run_lock is held, so a
                        # stopped stale generation can safely finish the stopping
                        # transition without overwriting a newer worker's state.
                        self._status = "idle"
                    if not replayed and cycle_terminal:
                        self._run_count += 1
                    self._last_completed_at = finished_at
                    self._last_result = result
                    if generation is None:
                        # A manual run is observational with respect to the
                        # background schedule, regardless of its outcome.
                        self._next_run_at, self._cycle_due_at = manual_scheduled_clock[:2]
                        if self._enabled:
                            self._settings = manual_scheduled_clock[2]
                    elif is_current and cycle_terminal:
                        self._next_run_at = (
                            finished_at + timedelta(seconds=self._settings.interval_seconds)
                            if self._enabled
                            else None
                        )
                        self._cycle_due_at = self._next_run_at
                    elif is_current:
                        # Preserve the immutable cycle_due_at and retry this
                        # partial slot instead of advancing the schedule.
                        retry_at = _retry_at_from_result(result)
                        self._next_run_at = retry_at or (
                            _utc_now() + retry_backoff(1)
                        )
            if coordination_error is None:
                if is_current:
                    self._notify_state_listener()
                self._run_lock.release()
        if coordination_error is not None:
            if generation is None:
                raise coordination_error
            return False
        return True

    def _notify_state_listener(self) -> None:
        with self._lock:
            listener = self._state_listener
            state = self._state_unlocked()
        if listener is None:
            return
        try:
            listener(state)
        except Exception:
            # A telemetry checkpoint must never stop the processing loop.
            return

    def _state_unlocked(self) -> AutoProcessingState:
        return AutoProcessingState(
            enabled=self._enabled,
            status=self._status,
            settings=self._settings,
            run_count=self._run_count,
            last_started_at=self._last_started_at,
            last_completed_at=self._last_completed_at,
            next_run_at=self._next_run_at,
            cycle_due_at=self._cycle_due_at,
            last_error=self._last_error,
            last_result=self._last_result,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _latest_utc_datetime(*values: datetime | None) -> datetime | None:
    normalized = [
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
        for value in values
        if value is not None
    ]
    return max(normalized, default=None)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _retry_at_from_result(result: AutoProcessingCycleResult) -> datetime | None:
    raw = result.data_health.get("automation_retry_next_at", "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)
