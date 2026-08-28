from __future__ import annotations

import hashlib
import json
import fcntl
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from threading import Event, Lock, Thread
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import (
    AutomationCycleRow,
    AutomationCycleStageRow,
    RuntimeLeaseRow,
)


AUTOMATION_LEASE_KEY = "automation:default"
AUTOMATION_LEASE_TTL = timedelta(hours=3)
TERMINAL_STAGE_STATUSES = {"completed", "skipped", "deferred"}
TERMINAL_CYCLE_STATUSES = {"succeeded", "completed_with_deferred_or_issues"}


class AutomationCycleBusyError(RuntimeError):
    pass


class AutomationLeaseLostError(RuntimeError):
    pass


class AutomationCycleConflictError(ValueError):
    pass


@dataclass(frozen=True)
class LeaseGrant:
    lease_key: str
    owner_token: str
    cycle_slot: str
    fencing_token: int
    expires_at: datetime


@dataclass(frozen=True)
class CycleStart:
    grant: LeaseGrant | None
    replay_result: dict[str, Any] | None = None
    replay_status: str | None = None


@dataclass
class ProcessFence:
    path: str
    fd: int

    def release(self) -> None:
        if self.fd < 0:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = -1


def canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def automation_settings_digest(settings: object) -> str:
    if hasattr(settings, "model_dump"):
        settings = settings.model_dump(mode="json")
    return canonical_digest(settings)


def scheduled_cycle_slot(due_at: datetime, settings_digest: str) -> str:
    normalized = _as_utc(due_at).isoformat(timespec="microseconds")
    return f"scheduled:{normalized}:{settings_digest}"


def manual_cycle_slot(idempotency_key: str | None = None) -> tuple[str, str | None]:
    normalized = (idempotency_key or "").strip()
    if not normalized:
        return f"manual:{uuid4().hex}", None
    if len(normalized) > 128:
        raise ValueError("idempotency_key must be at most 128 characters")
    return f"manual:{canonical_digest(normalized)}", f"automation-manual:{normalized}"


class AutomationRuntimeRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        bind = session_factory.kw.get("bind")
        if not isinstance(bind, Engine):
            raise TypeError("automation runtime requires an engine-bound session factory")
        if bind.dialect.name != "sqlite":
            raise RuntimeError(
                "automation runtime coordination is SQLite-only and fails closed "
                "without an implemented row-locking backend"
            )
        self.engine = bind
        database_path = bind.url.database
        if not database_path or database_path == ":memory:":
            raise RuntimeError("automation runtime process fencing requires file-backed SQLite")
        self.process_fence_path = str(
            Path(database_path).expanduser().resolve().with_suffix(".automation.lock")
        )

    def acquire_process_fence(self) -> ProcessFence | None:
        Path(self.process_fence_path).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.process_fence_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        return ProcessFence(path=self.process_fence_path, fd=fd)

    @contextmanager
    def _write_session(self) -> Iterator[Session]:
        with self.engine.connect() as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            session = Session(bind=connection, expire_on_commit=False)
            try:
                yield session
                session.flush()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                session.close()

    def acquire(
        self,
        *,
        lease_key: str,
        owner_token: str,
        cycle_slot: str,
        now: datetime | None = None,
        ttl: timedelta = AUTOMATION_LEASE_TTL,
        process_fence_held: bool = False,
    ) -> LeaseGrant | None:
        current = _as_utc(now or datetime.now(timezone.utc))
        expires_at = current + ttl
        with self._write_session() as session:
            row = session.get(RuntimeLeaseRow, lease_key)
            if row is None:
                row = RuntimeLeaseRow(
                    lease_key=lease_key,
                    owner_token=owner_token,
                    cycle_slot=cycle_slot,
                    fencing_token=1,
                    heartbeat_at=current,
                    expires_at=expires_at,
                    created_at=current,
                    updated_at=current,
                )
                session.add(row)
            elif (
                row.owner_token == owner_token
                and row.cycle_slot == cycle_slot
                and _as_utc(row.expires_at) > current
            ):
                row.heartbeat_at = current
                row.expires_at = expires_at
                row.updated_at = current
            elif _as_utc(row.expires_at) <= current or process_fence_held:
                row.owner_token = owner_token
                row.cycle_slot = cycle_slot
                row.fencing_token += 1
                row.heartbeat_at = current
                row.expires_at = expires_at
                row.updated_at = current
            else:
                return None
            session.flush()
            return LeaseGrant(
                lease_key=row.lease_key,
                owner_token=row.owner_token,
                cycle_slot=row.cycle_slot,
                fencing_token=row.fencing_token,
                expires_at=_as_utc(row.expires_at),
            )

    def heartbeat(
        self,
        grant: LeaseGrant,
        *,
        now: datetime | None = None,
        ttl: timedelta = AUTOMATION_LEASE_TTL,
    ) -> LeaseGrant:
        current = _as_utc(now or datetime.now(timezone.utc))
        expires_at = current + ttl
        with self._write_session() as session:
            row = session.get(RuntimeLeaseRow, grant.lease_key)
            self._require_current_lease(row, grant, current)
            row.heartbeat_at = current
            row.expires_at = expires_at
            row.updated_at = current
        return LeaseGrant(**{**grant.__dict__, "expires_at": expires_at})

    def assert_current(
        self,
        grant: LeaseGrant,
        *,
        now: datetime | None = None,
    ) -> None:
        current = _as_utc(now or datetime.now(timezone.utc))
        with self.session_factory() as session:
            row = session.get(RuntimeLeaseRow, grant.lease_key)
            self._require_current_lease(row, grant, current)

    def release(self, grant: LeaseGrant, *, now: datetime | None = None) -> bool:
        current = _as_utc(now or datetime.now(timezone.utc))
        with self._write_session() as session:
            row = session.get(RuntimeLeaseRow, grant.lease_key)
            if not self._lease_matches(row, grant):
                return False
            row.heartbeat_at = current
            row.expires_at = current
            row.updated_at = current
            return True

    def begin_cycle(
        self,
        *,
        cycle_slot: str,
        cycle_kind: str,
        settings_digest: str,
        due_at: datetime | None,
        idempotency_key: str | None,
        owner_token: str,
        now: datetime | None = None,
        ttl: timedelta = AUTOMATION_LEASE_TTL,
        process_fence_held: bool = False,
    ) -> CycleStart:
        current = _as_utc(now or datetime.now(timezone.utc))
        with self.session_factory() as session:
            existing = session.get(AutomationCycleRow, cycle_slot)
            if existing is not None:
                self._require_cycle_facts(
                    existing,
                    cycle_kind=cycle_kind,
                    settings_digest=settings_digest,
                    idempotency_key=idempotency_key,
                )
                if existing.status in TERMINAL_CYCLE_STATUSES:
                    return CycleStart(
                        grant=None,
                        replay_result=_json_object(existing.result_json),
                        replay_status=existing.status,
                    )
        grant = self.acquire(
            lease_key=AUTOMATION_LEASE_KEY,
            owner_token=owner_token,
            cycle_slot=cycle_slot,
            now=current,
            ttl=ttl,
            process_fence_held=process_fence_held,
        )
        if grant is None:
            raise AutomationCycleBusyError("automation cycle is already running")
        try:
            with self._write_session() as session:
                row = session.get(AutomationCycleRow, cycle_slot)
                if row is not None and row.status in TERMINAL_CYCLE_STATUSES:
                    replay = _json_object(row.result_json)
                    return CycleStart(
                        grant=grant,
                        replay_result=replay,
                        replay_status=row.status,
                    )
                if row is None:
                    row = AutomationCycleRow(
                        cycle_slot=cycle_slot,
                        cycle_kind=cycle_kind,
                        settings_digest=settings_digest,
                        idempotency_key=idempotency_key,
                        due_at=_as_utc(due_at) if due_at is not None else None,
                        status="running",
                        owner_token=owner_token,
                        fencing_token=grant.fencing_token,
                        started_at=current,
                        created_at=current,
                        updated_at=current,
                    )
                    session.add(row)
                else:
                    self._require_cycle_facts(
                        row,
                        cycle_kind=cycle_kind,
                        settings_digest=settings_digest,
                        idempotency_key=idempotency_key,
                    )
                    row.status = "running"
                    row.owner_token = owner_token
                    row.fencing_token = grant.fencing_token
                    row.updated_at = current
                session.flush()
            return CycleStart(grant=grant)
        except Exception:
            self.release(grant, now=current)
            raise

    def begin_stage(self, grant: LeaseGrant, stage_key: str) -> dict[str, Any] | None:
        current = datetime.now(timezone.utc)
        grant = self.heartbeat(grant, now=current)
        with self._write_session() as session:
            lease = session.get(RuntimeLeaseRow, grant.lease_key)
            self._require_current_lease(lease, grant, current)
            row = session.get(AutomationCycleStageRow, (grant.cycle_slot, stage_key))
            if row is not None and row.status in TERMINAL_STAGE_STATUSES:
                return _json_object(row.output_json)
            if row is None:
                row = AutomationCycleStageRow(
                    cycle_slot=grant.cycle_slot,
                    stage_key=stage_key,
                    status="running",
                    owner_token=grant.owner_token,
                    fencing_token=grant.fencing_token,
                    started_at=current,
                    updated_at=current,
                )
                session.add(row)
            else:
                row.status = "running"
                row.owner_token = grant.owner_token
                row.fencing_token = grant.fencing_token
                row.error_text = None
                row.started_at = current
                row.completed_at = None
                row.updated_at = current
            return None

    def complete_stage(
        self,
        grant: LeaseGrant,
        stage_key: str,
        output: dict[str, Any],
        *,
        status: str = "completed",
    ) -> None:
        if status not in TERMINAL_STAGE_STATUSES:
            raise ValueError("terminal stage status must be completed, skipped, or deferred")
        current = datetime.now(timezone.utc)
        encoded = _json_dumps(output)
        with self._write_session() as session:
            lease = session.get(RuntimeLeaseRow, grant.lease_key)
            self._require_current_lease(lease, grant, current)
            row = session.get(AutomationCycleStageRow, (grant.cycle_slot, stage_key))
            if row is None or row.owner_token != grant.owner_token or row.fencing_token != grant.fencing_token:
                raise AutomationLeaseLostError("automation stage owner was fenced")
            row.status = status
            row.output_json = encoded
            row.output_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            row.error_text = None
            row.completed_at = current
            row.updated_at = current

    def fail_stage(self, grant: LeaseGrant, stage_key: str, error: str) -> None:
        current = datetime.now(timezone.utc)
        with self._write_session() as session:
            lease = session.get(RuntimeLeaseRow, grant.lease_key)
            self._require_current_lease(lease, grant, current)
            row = session.get(AutomationCycleStageRow, (grant.cycle_slot, stage_key))
            if row is None or row.owner_token != grant.owner_token or row.fencing_token != grant.fencing_token:
                raise AutomationLeaseLostError("automation stage owner was fenced")
            row.status = "error"
            row.error_text = error[:2000]
            row.completed_at = current
            row.updated_at = current

    def finalize_cycle(
        self,
        grant: LeaseGrant,
        *,
        result: dict[str, Any],
        errors: list[str],
        issues: list[str],
        required_stages: set[str],
        now: datetime | None = None,
    ) -> str:
        current = _as_utc(now or datetime.now(timezone.utc))
        with self._write_session() as session:
            lease = session.get(RuntimeLeaseRow, grant.lease_key)
            self._require_current_lease(lease, grant, current)
            cycle = session.get(AutomationCycleRow, grant.cycle_slot)
            if cycle is None or cycle.owner_token != grant.owner_token or cycle.fencing_token != grant.fencing_token:
                raise AutomationLeaseLostError("automation cycle owner was fenced")
            if cycle.status in TERMINAL_CYCLE_STATUSES:
                return cycle.status
            stages = session.scalars(
                select(AutomationCycleStageRow).where(
                    AutomationCycleStageRow.cycle_slot == grant.cycle_slot
                )
            ).all()
            stage_status = {stage.stage_key: stage.status for stage in stages}
            terminal = not errors and all(
                stage_status.get(stage) in TERMINAL_STAGE_STATUSES
                for stage in required_stages
            )
            has_deferred = any(
                stage_status.get(stage) == "deferred" for stage in required_stages
            )
            cycle.status = (
                "partial_retry_same_slot"
                if not terminal
                else "completed_with_deferred_or_issues"
                if has_deferred or issues
                else "succeeded"
            )
            cycle.result_json = _json_dumps(result)
            cycle.error_json = _json_dumps(errors)
            cycle.finalized_at = current
            cycle.updated_at = current
            lease.heartbeat_at = current
            lease.expires_at = current
            lease.updated_at = current
            return cycle.status

    def abort_cycle(
        self,
        grant: LeaseGrant,
        *,
        error: str,
        now: datetime | None = None,
    ) -> bool:
        current = _as_utc(now or datetime.now(timezone.utc))
        with self._write_session() as session:
            lease = session.get(RuntimeLeaseRow, grant.lease_key)
            self._require_current_lease(lease, grant, current)
            cycle = session.get(AutomationCycleRow, grant.cycle_slot)
            if (
                cycle is None
                or cycle.owner_token != grant.owner_token
                or cycle.fencing_token != grant.fencing_token
                or cycle.status in TERMINAL_CYCLE_STATUSES
            ):
                return False
            cycle.status = "partial_retry_same_slot"
            cycle.error_json = _json_dumps([error[:2000]])
            cycle.finalized_at = current
            cycle.updated_at = current
            lease.heartbeat_at = current
            lease.expires_at = current
            lease.updated_at = current
            return True

    @staticmethod
    def _require_cycle_facts(
        row: AutomationCycleRow,
        *,
        cycle_kind: str,
        settings_digest: str,
        idempotency_key: str | None,
    ) -> None:
        if (
            row.cycle_kind != cycle_kind
            or row.settings_digest != settings_digest
            or row.idempotency_key != idempotency_key
        ):
            raise AutomationCycleConflictError(
                "automation cycle slot is bound to different facts"
            )

    @staticmethod
    def _lease_matches(row: RuntimeLeaseRow | None, grant: LeaseGrant) -> bool:
        return bool(
            row is not None
            and row.owner_token == grant.owner_token
            and row.cycle_slot == grant.cycle_slot
            and row.fencing_token == grant.fencing_token
        )

    @classmethod
    def _require_current_lease(
        cls,
        row: RuntimeLeaseRow | None,
        grant: LeaseGrant,
        now: datetime,
    ) -> None:
        if not cls._lease_matches(row, grant) or _as_utc(row.expires_at) <= _as_utc(now):
            raise AutomationLeaseLostError("automation lease expired or owner was fenced")


class RuntimeLeaseGuard:
    """Maintain a lease while a long-running stage is outside a DB transaction."""

    def __init__(
        self,
        repository: AutomationRuntimeRepository,
        grant: LeaseGrant,
        *,
        ttl: timedelta = AUTOMATION_LEASE_TTL,
        heartbeat_interval: timedelta | None = None,
    ) -> None:
        self.repository = repository
        self.grant = grant
        self.ttl = ttl
        requested = heartbeat_interval or ttl / 3
        self.heartbeat_interval = min(requested, ttl / 3)
        self._stop = Event()
        self._lost = Event()
        self._lock = Lock()
        self._error: Exception | None = None
        self._thread = Thread(target=self._maintain, daemon=True)

    def start(self) -> RuntimeLeaseGuard:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(self.heartbeat_interval.total_seconds() * 2, 1.0))

    def assert_current(self) -> None:
        if self._lost.is_set():
            detail = f": {self._error}" if self._error is not None else ""
            raise AutomationLeaseLostError(f"automation lease heartbeat was lost{detail}")
        self.repository.assert_current(self.grant)

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def _maintain(self) -> None:
        interval = max(self.heartbeat_interval.total_seconds(), 0.01)
        while not self._stop.wait(interval):
            try:
                refreshed = self.repository.heartbeat(self.grant, ttl=self.ttl)
                with self._lock:
                    self.grant = refreshed
            except Exception as exc:
                with self._lock:
                    self._error = exc
                self._lost.set()
                return


def _json_dumps(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _json_object(value: str) -> dict[str, Any]:
    decoded = json.loads(value or "{}")
    return decoded if isinstance(decoded, dict) else {}


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    return str(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
