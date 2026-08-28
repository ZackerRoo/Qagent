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
    AutomationCircuitBreakerRow,
    AutomationCycleRow,
    AutomationCycleStageRow,
    AutomationIncidentRow,
    BriefRunRow,
    DeliveryOutboxRow,
    RuntimeLeaseRow,
)
from qagent.jobs.automation_retry import (
    AUTOMATION_RETRY_BUDGET,
    breaker_cooldown,
    retry_backoff,
)


AUTOMATION_LEASE_KEY = "automation:default"
AUTOMATION_LEASE_TTL = timedelta(hours=3)
AUTOMATION_PROBE_TTL = AUTOMATION_LEASE_TTL
TERMINAL_STAGE_STATUSES = {"completed", "skipped", "deferred", "deferred_with_alert"}
TERMINAL_CYCLE_STATUSES = {
    "succeeded",
    "completed_with_deferred_or_issues",
    "deferred_with_alert",
}


class AutomationCycleBusyError(RuntimeError):
    pass


class AutomationLeaseLostError(RuntimeError):
    pass


class AutomationCycleConflictError(ValueError):
    pass


class AutomationCycleTerminatedError(RuntimeError):
    """The runner raised after its persisted cycle was safely terminated."""

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
    retry_not_due_at: datetime | None = None
    attempt_count: int = 0


@dataclass(frozen=True)
class CycleRetryState:
    attempt_count: int = 0
    retry_budget: int = AUTOMATION_RETRY_BUDGET
    next_retry_at: datetime | None = None
    retry_backoff_seconds: int | None = None
    last_error_fingerprint: str | None = None
    terminal_reason: str | None = None


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
            breakers = session.scalars(
                select(AutomationCircuitBreakerRow).where(
                    AutomationCircuitBreakerRow.state == "half_open",
                    AutomationCircuitBreakerRow.half_open_cycle_slot
                    == grant.cycle_slot,
                )
            ).all()
            for breaker in breakers:
                breaker.probe_expires_at = current + AUTOMATION_PROBE_TTL
                breaker.revision += 1
                breaker.updated_at = current
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
                        attempt_count=existing.attempt_count,
                    )
                if (
                    existing.next_retry_at is not None
                    and _as_utc(existing.next_retry_at) > current
                ):
                    return CycleStart(
                        grant=None,
                        replay_result=_json_object(existing.result_json),
                        replay_status=existing.status,
                        retry_not_due_at=_as_utc(existing.next_retry_at),
                        attempt_count=existing.attempt_count,
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
                        attempt_count=row.attempt_count,
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
                        attempt_count=0,
                        retry_budget=AUTOMATION_RETRY_BUDGET,
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
                    row.next_retry_at = None
                    row.retry_backoff_seconds = None
                    row.updated_at = current
                session.flush()
            return CycleStart(grant=grant, attempt_count=row.attempt_count)
        except Exception:
            self.release(grant, now=current)
            raise

    def begin_stage(
        self,
        grant: LeaseGrant,
        stage_key: str,
        *,
        retry_scope: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        current = _as_utc(now or datetime.now(timezone.utc))
        grant = self.heartbeat(grant, now=current)
        with self._write_session() as session:
            lease = session.get(RuntimeLeaseRow, grant.lease_key)
            self._require_current_lease(lease, grant, current)
            row = session.get(AutomationCycleStageRow, (grant.cycle_slot, stage_key))
            if row is not None and row.status in TERMINAL_STAGE_STATUSES:
                return _json_object(row.output_json)
            cycle = session.get(AutomationCycleRow, grant.cycle_slot)
            retry_budget = int(
                cycle.retry_budget if cycle is not None else AUTOMATION_RETRY_BUDGET
            )
            if (
                row is not None
                and row.status == "error"
                and int(row.attempt_count or 0) >= retry_budget
            ):
                return {
                    "stage_terminal_error": row.error_text or "stage retry budget exhausted",
                    "data_health": {
                        f"automation_stage_{stage_key}_recovered_terminal_attempt": str(
                            int(row.attempt_count or 0)
                        )
                    },
                }
            breaker = (
                session.get(AutomationCircuitBreakerRow, retry_scope)
                if retry_scope
                else None
            )
            if breaker is not None and breaker.state in {"open", "half_open"}:
                probe_due = (
                    breaker.next_probe_at is None
                    or _as_utc(breaker.next_probe_at) <= current
                )
                probe_expired = (
                    breaker.state == "half_open"
                    and (
                        breaker.probe_expires_at is None
                        or _as_utc(breaker.probe_expires_at) <= current
                    )
                )
                owns_probe = (
                    breaker.state == "half_open"
                    and breaker.half_open_cycle_slot == grant.cycle_slot
                    and not probe_expired
                )
                if (breaker.state == "open" and probe_due) or probe_expired:
                    breaker.state = "half_open"
                    breaker.half_open_cycle_slot = grant.cycle_slot
                    breaker.probe_expires_at = current + AUTOMATION_PROBE_TTL
                    breaker.next_probe_at = None
                    breaker.revision += 1
                    breaker.updated_at = current
                    owns_probe = True
                if not owns_probe:
                    retry_at = (
                        breaker.probe_expires_at
                        if breaker.state == "half_open"
                        else breaker.next_probe_at
                    )
                    checkpoint = {
                        "stage_issue": (
                            f"{stage_key}: circuit open until "
                            f"{_as_utc(retry_at).isoformat() if retry_at else 'next probe'}"
                        ),
                        "data_health": {
                            f"automation_{stage_key}_circuit_state": breaker.state,
                            f"automation_{stage_key}_circuit_next_probe_at": (
                                _as_utc(retry_at).isoformat()
                                if retry_at
                                else ""
                            ),
                        },
                    }
                    encoded = _json_dumps(checkpoint)
                    if row is None:
                        row = AutomationCycleStageRow(
                            cycle_slot=grant.cycle_slot,
                            stage_key=stage_key,
                            status="deferred_with_alert",
                            owner_token=grant.owner_token,
                            fencing_token=grant.fencing_token,
                            retry_scope=retry_scope,
                            output_json=encoded,
                            output_digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                            started_at=current,
                            completed_at=current,
                            updated_at=current,
                        )
                        session.add(row)
                    else:
                        row.status = "deferred_with_alert"
                        row.owner_token = grant.owner_token
                        row.fencing_token = grant.fencing_token
                        row.retry_scope = retry_scope
                        row.output_json = encoded
                        row.output_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                        row.completed_at = current
                        row.updated_at = current
                    return checkpoint
            if row is None:
                row = AutomationCycleStageRow(
                    cycle_slot=grant.cycle_slot,
                    stage_key=stage_key,
                    status="running",
                    owner_token=grant.owner_token,
                    fencing_token=grant.fencing_token,
                    retry_scope=retry_scope,
                    started_at=current,
                    updated_at=current,
                )
                session.add(row)
            else:
                row.status = "running"
                row.owner_token = grant.owner_token
                row.fencing_token = grant.fencing_token
                row.retry_scope = retry_scope or row.retry_scope
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
        retry_scope: str | None = None,
        now: datetime | None = None,
    ) -> None:
        if status not in TERMINAL_STAGE_STATUSES:
            raise ValueError("invalid terminal automation stage status")
        current = _as_utc(now or datetime.now(timezone.utc))
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
            row.next_retry_at = None
            row.retry_backoff_seconds = None
            row.completed_at = current
            row.updated_at = current
            scope = retry_scope or row.retry_scope
            breaker = session.get(AutomationCircuitBreakerRow, scope) if scope else None
            if (
                breaker is not None
                and breaker.state == "half_open"
                and breaker.half_open_cycle_slot == grant.cycle_slot
            ):
                if status == "completed":
                    breaker.state = "closed"
                    breaker.failure_count = 0
                    breaker.open_count = 0
                    breaker.next_probe_at = None
                else:
                    breaker.state = "open"
                    breaker.next_probe_at = current + breaker_cooldown(
                        max(int(breaker.open_count or 0), 1)
                    )
                breaker.half_open_cycle_slot = None
                breaker.probe_expires_at = None
                breaker.revision += 1
                breaker.updated_at = current

    def fail_stage(
        self,
        grant: LeaseGrant,
        stage_key: str,
        error: str,
        *,
        retry_scope: str | None = None,
        error_fingerprint: str | None = None,
        error_kind: str | None = None,
        retryable: bool = True,
        now: datetime | None = None,
    ) -> None:
        current = _as_utc(now or datetime.now(timezone.utc))
        with self._write_session() as session:
            lease = session.get(RuntimeLeaseRow, grant.lease_key)
            self._require_current_lease(lease, grant, current)
            row = session.get(AutomationCycleStageRow, (grant.cycle_slot, stage_key))
            if row is None or row.owner_token != grant.owner_token or row.fencing_token != grant.fencing_token:
                raise AutomationLeaseLostError("automation stage owner was fenced")
            row.status = "error"
            row.error_text = error[:2000]
            row.attempt_count = int(row.attempt_count or 0) + 1
            row.retry_scope = retry_scope or row.retry_scope
            row.last_error_fingerprint = error_fingerprint
            row.last_error_kind = error_kind
            row.last_error_retryable = retryable
            row.last_error_at = current
            row.completed_at = current
            row.updated_at = current
            scope = retry_scope or row.retry_scope
            breaker = session.get(AutomationCircuitBreakerRow, scope) if scope else None
            if (
                breaker is not None
                and breaker.state == "half_open"
                and breaker.half_open_cycle_slot == grant.cycle_slot
            ):
                if retryable:
                    breaker.state = "open"
                    breaker.failure_count = int(breaker.failure_count or 0) + 1
                    breaker.open_count = int(breaker.open_count or 0) + 1
                    breaker.last_error_fingerprint = error_fingerprint
                    breaker.last_error_text = error[:2000]
                    breaker.next_probe_at = current + breaker_cooldown(
                        breaker.open_count
                    )
                else:
                    # A permanent contract/configuration failure belongs to
                    # the incident, not the provider retry circuit.
                    breaker.state = "closed"
                    breaker.failure_count = 0
                    breaker.open_count = 0
                    breaker.next_probe_at = None
                breaker.half_open_cycle_slot = None
                breaker.probe_expires_at = None
                breaker.revision += 1
                breaker.updated_at = current

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
            error_stages = [stage for stage in stages if stage.status == "error"]
            if errors:
                observed_attempt = max(
                    (int(stage.attempt_count or 0) for stage in error_stages),
                    default=int(cycle.attempt_count or 0) + 1,
                )
                prior_attempt = int(cycle.attempt_count or 0)
                retry_budget = int(
                    cycle.retry_budget or AUTOMATION_RETRY_BUDGET
                )
                cycle.attempt_count = (
                    max(prior_attempt, observed_attempt)
                    if prior_attempt >= retry_budget
                    and observed_attempt >= retry_budget
                    else max(prior_attempt + 1, observed_attempt)
                )
                fingerprints = sorted(
                    {
                        str(stage.last_error_fingerprint)
                        for stage in error_stages
                        if stage.last_error_fingerprint
                    }
                )
                cycle.last_error_fingerprint = canonical_digest(fingerprints or errors)
                cycle.last_error_text = str(errors[0])[:2000]
                cycle.last_error_at = current
                permanent = any(stage.last_error_retryable is not True for stage in error_stages)
                half_open_failure = any(
                    stage.retry_scope
                    and (
                        breaker := session.get(
                            AutomationCircuitBreakerRow,
                            stage.retry_scope,
                        )
                    )
                    is not None
                    and (
                        (
                            breaker.state == "half_open"
                            and breaker.half_open_cycle_slot == grant.cycle_slot
                        )
                        or (
                            breaker.state == "open"
                            and stage.last_error_retryable is True
                            and breaker.last_error_fingerprint
                            == stage.last_error_fingerprint
                        )
                    )
                    for stage in error_stages
                )
                unkeyed_manual = cycle.cycle_kind == "manual" and not cycle.idempotency_key
                exhausted = (
                    permanent
                    or half_open_failure
                    or unkeyed_manual
                    or cycle.attempt_count >= int(cycle.retry_budget or AUTOMATION_RETRY_BUDGET)
                )
                if exhausted:
                    cycle.status = "deferred_with_alert"
                    cycle.next_retry_at = None
                    cycle.retry_backoff_seconds = None
                    cycle.terminal_reason = (
                        "permanent_error"
                        if permanent
                        else "manual_one_shot"
                        if unkeyed_manual
                        else "half_open_probe_failed"
                        if half_open_failure
                        else "retry_budget_exhausted"
                    )
                    for stage in error_stages:
                        stage.status = "deferred_with_alert"
                        stage.next_retry_at = None
                        stage.retry_backoff_seconds = None
                        stage.updated_at = current
                        breaker = self._open_breaker_for_stage(
                            session,
                            stage,
                            cycle_slot=grant.cycle_slot,
                            current=current,
                        )
                        self._record_retry_incident(
                            session,
                            cycle,
                            stage,
                            breaker=breaker,
                            current=current,
                        )
                else:
                    delay = retry_backoff(cycle.attempt_count)
                    cycle.status = "partial_retry_same_slot"
                    cycle.retry_backoff_seconds = int(delay.total_seconds())
                    cycle.next_retry_at = current + delay
                    cycle.terminal_reason = None
                    for stage in error_stages:
                        stage.retry_backoff_seconds = int(delay.total_seconds())
                        stage.next_retry_at = cycle.next_retry_at
                        stage.updated_at = current
            else:
                terminal = all(
                    stage_status.get(stage) in TERMINAL_STAGE_STATUSES
                    for stage in required_stages
                )
                has_alert_deferred = any(
                    stage_status.get(stage) == "deferred_with_alert"
                    for stage in required_stages
                )
                has_deferred = any(
                    stage_status.get(stage) == "deferred" for stage in required_stages
                )
                cycle.status = (
                    "partial_retry_same_slot"
                    if not terminal
                    else "deferred_with_alert"
                    if has_alert_deferred
                    else "completed_with_deferred_or_issues"
                    if has_deferred or issues
                    else "succeeded"
                )
                cycle.next_retry_at = None
                cycle.retry_backoff_seconds = None
                if cycle.status in TERMINAL_CYCLE_STATUSES:
                    cycle.terminal_reason = None
            cycle.result_json = _json_dumps(result)
            cycle.error_json = _json_dumps(errors)
            cycle.finalized_at = current
            cycle.updated_at = current
            lease.heartbeat_at = current
            lease.expires_at = current
            lease.updated_at = current
            return cycle.status

    def cycle_retry_state(self, cycle_slot: str) -> CycleRetryState:
        with self.session_factory() as session:
            row = session.get(AutomationCycleRow, cycle_slot)
            if row is None:
                return CycleRetryState()
            return CycleRetryState(
                attempt_count=int(row.attempt_count or 0),
                retry_budget=int(row.retry_budget or AUTOMATION_RETRY_BUDGET),
                next_retry_at=(
                    _as_utc(row.next_retry_at) if row.next_retry_at is not None else None
                ),
                retry_backoff_seconds=row.retry_backoff_seconds,
                last_error_fingerprint=row.last_error_fingerprint,
                terminal_reason=row.terminal_reason,
            )

    @staticmethod
    def _open_breaker_for_stage(
        session: Session,
        stage: AutomationCycleStageRow,
        *,
        cycle_slot: str,
        current: datetime,
    ) -> AutomationCircuitBreakerRow | None:
        if not stage.retry_scope or stage.last_error_retryable is not True:
            return None
        breaker = session.get(AutomationCircuitBreakerRow, stage.retry_scope)
        if breaker is None:
            breaker = AutomationCircuitBreakerRow(
                scope_key=stage.retry_scope,
                state="open",
                failure_count=1,
                open_count=1,
                last_error_fingerprint=stage.last_error_fingerprint,
                last_error_text=stage.error_text,
                revision=1,
                created_at=current,
                updated_at=current,
            )
            session.add(breaker)
        else:
            if (
                breaker.state == "open"
                and breaker.half_open_cycle_slot is None
                and breaker.last_error_fingerprint == stage.last_error_fingerprint
            ):
                return breaker
            breaker.state = "open"
            breaker.failure_count = int(breaker.failure_count or 0) + 1
            breaker.open_count = int(breaker.open_count or 0) + 1
            breaker.last_error_fingerprint = stage.last_error_fingerprint
            breaker.last_error_text = stage.error_text
            breaker.half_open_cycle_slot = None
            breaker.probe_expires_at = None
            breaker.revision += 1
            breaker.updated_at = current
        cooldown = breaker_cooldown(breaker.open_count)
        breaker.next_probe_at = current + cooldown
        return breaker

    @staticmethod
    def _record_retry_incident(
        session: Session,
        cycle: AutomationCycleRow,
        stage: AutomationCycleStageRow,
        *,
        breaker: AutomationCircuitBreakerRow | None,
        current: datetime,
    ) -> None:
        scope = stage.retry_scope or f"{stage.stage_key}:unknown"
        fingerprint = stage.last_error_fingerprint or canonical_digest(stage.error_text or "")
        open_count = int(breaker.open_count or 0) if breaker is not None else 0
        identity = canonical_digest(
            {
                "cycle_slot": cycle.cycle_slot,
                "scope": scope,
                "fingerprint": fingerprint,
                "open_count": open_count,
            }
        )
        incident_id = f"automation-incident-{identity[:32]}"
        brief_id = f"automation-brief-{identity[:32]}"
        alert_key = f"automation-retry-alert:{identity}"
        payload = {
            "incident_id": incident_id,
            "cycle_slot": cycle.cycle_slot,
            "stage": stage.stage_key,
            "scope": scope,
            "attempt_count": int(cycle.attempt_count or 0),
            "error_fingerprint": fingerprint,
            "open_count": open_count,
            "next_probe_at": (
                _as_utc(breaker.next_probe_at).isoformat()
                if breaker is not None and breaker.next_probe_at is not None
                else None
            ),
        }
        subject = f"Qagent automation deferred: {stage.stage_key}"
        markdown = (
            f"Automation stage `{stage.stage_key}` reached its retry boundary. "
            f"Fingerprint: `{fingerprint}`. Error: {stage.error_text or 'unknown'}"
        )
        provider = scope.rsplit(":", 1)[-1] if ":" in scope else "automation"
        brief_health = {
            "automation_incident_id": incident_id,
            "automation_cycle_slot": cycle.cycle_slot,
            "automation_stage": stage.stage_key,
            "automation_retry_scope": scope,
            "automation_error_fingerprint": fingerprint,
            "automation_attempt_count": str(int(cycle.attempt_count or 0)),
            "automation_open_count": str(open_count),
        }
        brief_payload = {
            "brief_id": brief_id,
            "provider": provider,
            "symbols": [],
            "headline": subject,
            "top_opportunities": [],
            "entry_watch": [],
            "risk_alerts": [payload],
            "catalyst_watch": [],
            "strategy_validation": [],
            "data_health": brief_health,
        }
        brief_facts = {
            "provider": provider,
            "symbols": "[]",
            "headline": subject,
            "opportunity_count": 0,
            "entry_watch_count": 0,
            "risk_alert_count": 1,
            "catalyst_count": 0,
            "validation_count": 0,
            "data_health": _json_dumps(brief_health),
            "brief_json": _json_dumps(brief_payload),
        }
        existing_brief = session.get(BriefRunRow, brief_id)
        if existing_brief is None:
            session.add(
                BriefRunRow(
                    brief_id=brief_id,
                    **brief_facts,
                    created_at=current,
                )
            )
        else:
            _require_row_facts(existing_brief, brief_facts, "automation synthetic brief")

        incident_facts = {
            "cycle_slot": cycle.cycle_slot,
            "stage_key": stage.stage_key,
            "scope_key": scope,
            "error_fingerprint": fingerprint,
            "error_text": (stage.error_text or "unknown automation error")[:2000],
            "attempt_count": int(cycle.attempt_count or 0),
            "open_count": open_count,
            "next_probe_at": breaker.next_probe_at if breaker is not None else None,
            "alert_idempotency_key": alert_key,
        }
        existing_incident = session.get(AutomationIncidentRow, incident_id)
        if existing_incident is None:
            session.add(
                AutomationIncidentRow(
                    incident_id=incident_id,
                    **incident_facts,
                    created_at=current,
                )
            )
        else:
            _require_row_facts(existing_incident, incident_facts, "automation incident")
        existing_delivery = session.scalar(
            select(DeliveryOutboxRow).where(DeliveryOutboxRow.idempotency_key == alert_key)
        )
        digest_facts = {
            "brief_id": brief_id,
            "channel": "markdown",
            "recipient": None,
            "subject": subject,
            "markdown": markdown,
            "payload": payload,
        }
        payload_digest = canonical_digest(digest_facts)
        if existing_delivery is None:
            session.add(
                DeliveryOutboxRow(
                    delivery_id=f"automation-alert-{identity[:32]}",
                    brief_id=brief_id,
                    channel="markdown",
                    recipient=None,
                    subject=subject,
                    markdown=markdown,
                    payload_json=_json_dumps(payload),
                    idempotency_key=alert_key,
                    payload_digest=payload_digest,
                    status="queued",
                    created_at=current,
                    updated_at=current,
                )
            )
        else:
            _require_row_facts(
                existing_delivery,
                {
                    "brief_id": brief_id,
                    "channel": "markdown",
                    "recipient": None,
                    "subject": subject,
                    "markdown": markdown,
                    "payload_json": _json_dumps(payload),
                    "idempotency_key": alert_key,
                    "payload_digest": payload_digest,
                    "status": "queued",
                },
                "automation alert delivery",
            )

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
            cycle.status = "deferred_with_alert"
            cycle.attempt_count = int(cycle.attempt_count or 0) + 1
            cycle.next_retry_at = None
            cycle.retry_backoff_seconds = None
            cycle.last_error_fingerprint = canonical_digest(
                {"stage": "cycle_runtime", "error": error[:2000]}
            )
            cycle.last_error_text = error[:2000]
            cycle.last_error_at = current
            cycle.terminal_reason = "unhandled_permanent_error"
            cycle.error_json = _json_dumps([error[:2000]])
            cycle.finalized_at = current
            cycle.updated_at = current
            stage = session.get(
                AutomationCycleStageRow,
                (grant.cycle_slot, "cycle_runtime"),
            )
            if stage is None:
                stage = AutomationCycleStageRow(
                    cycle_slot=grant.cycle_slot,
                    stage_key="cycle_runtime",
                    status="deferred_with_alert",
                    owner_token=grant.owner_token,
                    fencing_token=grant.fencing_token,
                    error_text=error[:2000],
                    attempt_count=1,
                    retry_scope="cycle_runtime:unknown",
                    last_error_fingerprint=cycle.last_error_fingerprint,
                    last_error_kind="permanent_unknown",
                    last_error_retryable=False,
                    last_error_at=current,
                    started_at=current,
                    completed_at=current,
                    updated_at=current,
                )
                session.add(stage)
            self._record_retry_incident(
                session,
                cycle,
                stage,
                breaker=None,
                current=current,
            )
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


def _require_row_facts(row: object, expected: dict[str, object], label: str) -> None:
    mismatches = {
        key: {"stored": getattr(row, key), "expected": value}
        for key, value in expected.items()
        if getattr(row, key) != value
    }
    if mismatches:
        raise AutomationCycleConflictError(
            f"{label} idempotency identity is bound to different facts: "
            f"{_json_dumps(mismatches)}"
        )


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    return str(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
