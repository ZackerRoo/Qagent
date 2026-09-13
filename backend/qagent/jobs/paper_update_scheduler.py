"""Independent Shanghai-session paper ticks; durable replay belongs to the writer.

The runner must persist completed slot identities under the account writer and
retain the engine's event idempotence for partially completed attempts. ``due_at``
is scheduling metadata only: market evaluation must use the actual current time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo

from qagent.market.calendars import trading_sessions_in_range

SHANGHAI = ZoneInfo("Asia/Shanghai")
INTERVAL_SECONDS = 600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("paper scheduler requires timezone-aware timestamps")
    return value.astimezone(timezone.utc)


def _is_session(day: date) -> bool:
    return day in trading_sessions_in_range(day, day)


def current_slot(
    now: datetime, is_session: Callable[[date], bool] = _is_session,
) -> datetime | None:
    """Latest session slot, including one minute to dispatch each closing tick."""
    local = _aware(now).astimezone(SHANGHAI)
    if not is_session(local.date()):
        return None
    for opening, closing in ((time(9, 30), time(11, 30)), (time(13), time(15))):
        start = datetime.combine(local.date(), opening, SHANGHAI)
        end = datetime.combine(local.date(), closing, SHANGHAI)
        if end <= local < end + timedelta(minutes=1):
            return end.astimezone(timezone.utc)
        if start <= local < end:
            offset = int((local - start).total_seconds()) // INTERVAL_SECONDS
            return (start + timedelta(seconds=offset * INTERVAL_SECONDS)).astimezone(timezone.utc)
    return None


@dataclass(frozen=True)
class PaperUpdateTick:
    slot_id: str
    due_at: datetime
    started_at: datetime


@dataclass(frozen=True)
class PaperUpdateState:
    enabled: bool = False
    status: str = "stopped"
    slot_id: str | None = None
    due_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    lateness_seconds: float | None = None
    last_error: str | None = None
    attempts: int = 0
    completed: int = 0
    skipped_slots: int = 0
    skip_reason: str | None = None


class PaperUpdateScheduler:
    """One daemon worker, bounded join, no overlapping callbacks or catch-up queue."""

    def __init__(
        self,
        runner: Callable[[PaperUpdateTick], object],
        *,
        clock: Callable[[], datetime] = _utc_now,
        is_session: Callable[[date], bool] = _is_session,
        poll_seconds: float = 1.0,
        retry_seconds: float = 30.0,
    ) -> None:
        if poll_seconds <= 0 or retry_seconds <= 0:
            raise ValueError("poll and retry intervals must be positive")
        self._runner = runner
        self._clock = clock
        self._is_session = is_session
        self._poll_seconds = poll_seconds
        self._retry_seconds = retry_seconds
        self._lock = Lock()
        self._run_lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._state = PaperUpdateState()
        self._finished_slot: datetime | None = None
        self._attempted_slot: datetime | None = None
        self._retry_at: datetime | None = None

    def state(self) -> PaperUpdateState:
        with self._lock:
            return replace(self._state)

    def start(self) -> PaperUpdateState:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stop.is_set():
                    raise RuntimeError("paper update worker is still stopping")
                return replace(self._state)
            self._stop.clear()
            self._state = replace(self._state, enabled=True, status="waiting")
            self._thread = Thread(target=self._loop, name="paper-update", daemon=True)
            self._thread.start()
            return replace(self._state)

    def stop(self, timeout: float = 1.0) -> bool:
        if timeout < 0:
            raise ValueError("stop timeout must be nonnegative")
        with self._lock:
            self._stop.set()
            thread = self._thread
            self._state = replace(self._state, enabled=False, status="stopping")
        if thread is not None:
            thread.join(timeout=timeout)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            with self._lock:
                self._state = replace(self._state, status="stopped")
        return stopped

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                self.run_due()
                self._stop.wait(self._poll_seconds)
        finally:
            with self._lock:
                self._state = replace(self._state, enabled=False, status="stopped")

    def run_due(self) -> bool:
        """Attempt only the current slot. Explicit test/worker entry, never a status read."""
        if not self._run_lock.acquire(blocking=False):
            return False
        try:
            return self._run_due()
        except Exception as exc:
            with self._lock:
                self._state = replace(self._state, status="error", last_error=type(exc).__name__)
            return False
        finally:
            self._run_lock.release()

    def _run_due(self) -> bool:
        now = _aware(self._clock())
        due = current_slot(now, self._is_session)
        if due is None:
            with self._lock:
                self._state = replace(self._state, status="outside_session")
            return False
        if due == self._finished_slot:
            return False
        if due == self._attempted_slot and self._retry_at is not None and now < self._retry_at:
            return False
        skipped = 0
        if self._attempted_slot is not None and due > self._attempted_slot:
            # Count only real active-session slots, excluding nights, lunch and holidays.
            cursor = self._attempted_slot + timedelta(seconds=INTERVAL_SECONDS)
            # Bound diagnostics after long downtime; execution never replays this history.
            while cursor < due and (due - cursor).days < 7:
                if current_slot(cursor, self._is_session) == cursor:
                    skipped += 1
                cursor += timedelta(seconds=INTERVAL_SECONDS)
            if self._attempted_slot != self._finished_slot:
                skipped += 1
        tick = PaperUpdateTick(f"paper-update:v1:{due.isoformat()}", due, now)
        self._attempted_slot = due
        with self._lock:
            self._state = replace(
                self._state, status="running", slot_id=tick.slot_id, due_at=due,
                started_at=now, finished_at=None, lateness_seconds=(now - due).total_seconds(),
                last_error=None, attempts=self._state.attempts + 1,
                skipped_slots=self._state.skipped_slots + skipped,
                skip_reason="expired_slots_not_replayed" if skipped else None,
            )
        try:
            self._runner(tick)
        except Exception as exc:
            finished = _aware(self._clock())
            self._retry_at = finished + timedelta(seconds=self._retry_seconds)
            with self._lock:
                self._state = replace(
                    self._state, status="error", finished_at=finished,
                    last_error=type(exc).__name__,
                )
            return False
        self._finished_slot = due
        self._retry_at = None
        with self._lock:
            self._state = replace(
                self._state, status="waiting", finished_at=_aware(self._clock()),
                completed=self._state.completed + 1,
            )
        return True
