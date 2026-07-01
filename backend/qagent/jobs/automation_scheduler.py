from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread

from pydantic import BaseModel, Field


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
    seed_paper: bool = True
    seed_limit: int = Field(default=5, ge=1, le=50)
    update_paper: bool = True
    run_alerts: bool = True
    queue_alerts: bool = True


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
    data_health: dict[str, str] = Field(default_factory=dict)


class AutoProcessingState(BaseModel):
    enabled: bool = False
    status: str = "idle"
    settings: AutoProcessingSettings
    run_count: int = 0
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str | None = None
    last_result: AutoProcessingCycleResult | None = None


CycleRunner = Callable[[AutoProcessingSettings], AutoProcessingCycleResult]


class AutomationScheduler:
    def __init__(self) -> None:
        self._lock = Lock()
        self._run_lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._settings = AutoProcessingSettings()
        self._enabled = False
        self._status = "idle"
        self._run_count = 0
        self._last_started_at: datetime | None = None
        self._last_completed_at: datetime | None = None
        self._next_run_at: datetime | None = None
        self._last_error: str | None = None
        self._last_result: AutoProcessingCycleResult | None = None

    def state(self) -> AutoProcessingState:
        with self._lock:
            return self._state_unlocked()

    def start(self, settings: AutoProcessingSettings, runner: CycleRunner) -> AutoProcessingState:
        with self._lock:
            self._settings = settings
            if self._enabled and self._thread and self._thread.is_alive():
                self._next_run_at = self._next_run_at or _utc_now()
                return self._state_unlocked()
            self._enabled = True
            self._status = "idle"
            self._last_error = None
            self._next_run_at = _utc_now()
            self._stop_event.clear()
            self._thread = Thread(target=self._loop, args=(runner,), daemon=True)
            self._thread.start()
            return self._state_unlocked()

    def stop(self) -> AutoProcessingState:
        thread: Thread | None
        with self._lock:
            self._enabled = False
            self._status = "idle"
            self._next_run_at = None
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        with self._lock:
            return self._state_unlocked()

    def run_once(
        self,
        settings: AutoProcessingSettings,
        runner: CycleRunner,
    ) -> AutoProcessingState:
        self._execute(settings, runner)
        with self._lock:
            return self._state_unlocked()

    def _loop(self, runner: CycleRunner) -> None:
        while not self._stop_event.is_set():
            settings = self.state().settings
            self._execute(settings, runner)
            if self._stop_event.wait(settings.interval_seconds):
                break
        with self._lock:
            self._enabled = False
            if self._status == "running":
                self._status = "idle"
            self._next_run_at = None

    def _execute(self, settings: AutoProcessingSettings, runner: CycleRunner) -> None:
        if not self._run_lock.acquire(blocking=False):
            return
        started_at = _utc_now()
        with self._lock:
            self._settings = settings
            self._status = "running"
            self._last_started_at = started_at
            self._last_error = None
        try:
            result = runner(settings)
        except Exception as exc:  # pragma: no cover - route-level tests cover state output.
            finished_at = _utc_now()
            result = AutoProcessingCycleResult(
                provider=settings.provider,
                started_at=started_at,
                finished_at=finished_at,
                scan_status="failed",
                errors=[str(exc)],
                data_health={"automation_scheduler_error": str(exc)[:500]},
            )
            with self._lock:
                self._last_error = str(exc)
        else:
            finished_at = result.finished_at
        finally:
            with self._lock:
                self._status = "idle" if self._enabled else "idle"
                self._run_count += 1
                self._last_completed_at = finished_at
                self._last_result = result
                self._next_run_at = (
                    finished_at + timedelta(seconds=settings.interval_seconds)
                    if self._enabled
                    else None
                )
            self._run_lock.release()

    def _state_unlocked(self) -> AutoProcessingState:
        return AutoProcessingState(
            enabled=self._enabled,
            status=self._status,
            settings=self._settings,
            run_count=self._run_count,
            last_started_at=self._last_started_at,
            last_completed_at=self._last_completed_at,
            next_run_at=self._next_run_at,
            last_error=self._last_error,
            last_result=self._last_result,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
