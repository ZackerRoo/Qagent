from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from email.utils import parsedate_to_datetime
import random
import socket
from threading import Lock
import time
from typing import Callable


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class FailureCategory(StrEnum):
    TRANSPORT = "transport"
    DNS = "dns"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    AUTH = "auth"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED = "unsupported"
    NOT_LISTED = "not_listed"


COUNTED_FAILURES = {
    FailureCategory.TRANSPORT,
    FailureCategory.DNS,
    FailureCategory.TIMEOUT,
    FailureCategory.RATE_LIMIT,
    FailureCategory.SERVER,
    FailureCategory.AUTH,
}


@dataclass(frozen=True)
class FailureKey:
    provider: str
    origin: str
    capability: str


@dataclass(frozen=True)
class CircuitSnapshot:
    state: CircuitState
    consecutive_failures: int
    total_failures: int
    total_successes: int
    opened: int
    half_open_probes: int
    recoveries: int
    retry_after_seconds: float
    last_failure_category: str | None
    last_error_code: str | None


class CircuitOpenError(RuntimeError):
    def __init__(self, key: FailureKey, retry_after_seconds: float):
        self.key = key
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        super().__init__(
            f"{key.provider}/{key.capability} circuit open; "
            f"retry in {self.retry_after_seconds:.2f}s"
        )


@dataclass
class _Entry:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    opened: int = 0
    half_open_probes: int = 0
    recoveries: int = 0
    open_until: float = 0.0
    probe_in_flight: bool = False
    last_failure_category: str | None = None
    last_error_code: str | None = None


class ProviderFailureStateRegistry:
    """Thread-safe provider/capability circuits with one half-open probe."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        base_backoff_seconds: float = 5.0,
        max_backoff_seconds: float = 300.0,
        auth_backoff_seconds: float = 900.0,
        jitter_ratio: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.base_backoff_seconds = max(0.0, base_backoff_seconds)
        self.max_backoff_seconds = max(self.base_backoff_seconds, max_backoff_seconds)
        self.auth_backoff_seconds = max(0.0, auth_backoff_seconds)
        self.jitter_ratio = min(1.0, max(0.0, jitter_ratio))
        self._clock = clock
        self._random_value = random_value
        self._lock = Lock()
        self._entries: dict[FailureKey, _Entry] = {}

    def acquire(self, key: FailureKey) -> None:
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            now = self._clock()
            if entry.state == CircuitState.OPEN:
                if now < entry.open_until:
                    raise CircuitOpenError(key, entry.open_until - now)
                entry.state = CircuitState.HALF_OPEN
                entry.probe_in_flight = False
            if entry.state == CircuitState.HALF_OPEN:
                if entry.probe_in_flight:
                    raise CircuitOpenError(key, max(0.0, entry.open_until - now))
                entry.probe_in_flight = True
                entry.half_open_probes += 1

    def success(self, key: FailureKey) -> None:
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            was_degraded = entry.state != CircuitState.CLOSED or entry.consecutive_failures > 0
            entry.total_successes += 1
            entry.state = CircuitState.CLOSED
            entry.consecutive_failures = 0
            entry.open_until = 0.0
            entry.probe_in_flight = False
            entry.last_failure_category = None
            entry.last_error_code = None
            if was_degraded:
                entry.recoveries += 1

    def failure(
        self,
        key: FailureKey,
        category: FailureCategory,
        *,
        retry_after_seconds: float | None = None,
        error_code: str | int | None = None,
    ) -> None:
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            entry.last_failure_category = category.value
            entry.last_error_code = str(error_code) if error_code is not None else category.value
            if category not in COUNTED_FAILURES:
                entry.probe_in_flight = False
                return
            entry.total_failures += 1
            entry.consecutive_failures += 1
            should_open = (
                entry.state == CircuitState.HALF_OPEN
                or entry.consecutive_failures >= self.failure_threshold
                or category in {FailureCategory.AUTH, FailureCategory.RATE_LIMIT}
            )
            if not should_open:
                entry.probe_in_flight = False
                return
            exponent = max(0, entry.consecutive_failures - self.failure_threshold)
            delay = min(self.max_backoff_seconds, self.base_backoff_seconds * (2**exponent))
            if category == FailureCategory.AUTH:
                delay = max(delay, self.auth_backoff_seconds)
            if retry_after_seconds is not None:
                delay = max(delay, max(0.0, retry_after_seconds))
            jitter = delay * self.jitter_ratio * self._random_value()
            entry.state = CircuitState.OPEN
            entry.open_until = self._clock() + delay + jitter
            entry.probe_in_flight = False
            entry.opened += 1

    def ignored(self, key: FailureKey) -> None:
        """Release a half-open probe for a valid non-provider outcome."""
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            entry.probe_in_flight = False

    def retry_after_seconds(self, key: FailureKey) -> float:
        return self.snapshot(key).retry_after_seconds

    def snapshot(self, key: FailureKey) -> CircuitSnapshot:
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            retry_after = (
                max(0.0, entry.open_until - self._clock())
                if entry.state == CircuitState.OPEN
                else 0.0
            )
            return CircuitSnapshot(
                state=entry.state,
                consecutive_failures=entry.consecutive_failures,
                total_failures=entry.total_failures,
                total_successes=entry.total_successes,
                opened=entry.opened,
                half_open_probes=entry.half_open_probes,
                recoveries=entry.recoveries,
                retry_after_seconds=retry_after,
                last_failure_category=entry.last_failure_category,
                last_error_code=entry.last_error_code,
            )

    def snapshots(self) -> dict[FailureKey, CircuitSnapshot]:
        with self._lock:
            keys = list(self._entries)
        return {key: self.snapshot(key) for key in keys}


def classify_http_status(status_code: int | None) -> FailureCategory:
    if status_code in {401, 403}:
        return FailureCategory.AUTH
    if status_code == 429:
        return FailureCategory.RATE_LIMIT
    if status_code is not None and status_code >= 500:
        return FailureCategory.SERVER
    return FailureCategory.INVALID_REQUEST


def classify_exception(exc: BaseException) -> FailureCategory:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return classify_http_status(status_code)
    if isinstance(exc, ValueError):
        return FailureCategory.INVALID_REQUEST
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return FailureCategory.TIMEOUT
    detail = str(exc).lower()
    if any(token in detail for token in ("name resolution", "getaddrinfo", "dns")):
        return FailureCategory.DNS
    if "timed out" in detail or "timeout" in detail:
        return FailureCategory.TIMEOUT
    return FailureCategory.TRANSPORT


def retry_after_from_exception(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("Retry-After")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(str(raw))
            return max(0.0, parsed.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def provider_failure_state_data_health(provider: object) -> dict[str, str]:
    registries = _provider_failure_registries(provider)
    snapshots = [snapshot for registry in registries for snapshot in registry.snapshots().values()]
    if not snapshots:
        return (
            {
                "provider_error_kind": "none",
                "provider_error_code": "",
                "provider_error_retryable": "false",
                "provider_circuit_state": CircuitState.CLOSED.value,
                "provider_circuit_capabilities": "0",
                "provider_circuit_open_capabilities": "0",
                "provider_circuit_half_open_capabilities": "0",
                "provider_circuit_failures": "0",
                "provider_circuit_successes": "0",
                "provider_circuit_opened": "0",
                "provider_circuit_half_open_probes": "0",
                "provider_circuit_recoveries": "0",
                "provider_circuit_retry_after_seconds": "0.000",
            }
            if registries
            else {}
        )
    state = (
        CircuitState.OPEN
        if any(item.state == CircuitState.OPEN for item in snapshots)
        else CircuitState.HALF_OPEN
        if any(item.state == CircuitState.HALF_OPEN for item in snapshots)
        else CircuitState.CLOSED
    )
    active_errors = [item for item in snapshots if item.last_failure_category]
    worst = max(active_errors, key=_failure_snapshot_severity) if active_errors else None
    return {
        "provider_error_kind": worst.last_failure_category if worst else "none",
        "provider_error_code": (
            "configuration_auth"
            if worst and worst.last_failure_category == FailureCategory.AUTH.value
            else worst.last_error_code
            if worst
            else ""
        ),
        "provider_error_retryable": str(
            bool(
                worst
                and worst.last_failure_category
                in {
                    FailureCategory.TRANSPORT.value,
                    FailureCategory.DNS.value,
                    FailureCategory.TIMEOUT.value,
                    FailureCategory.RATE_LIMIT.value,
                    FailureCategory.SERVER.value,
                }
            )
        ).lower(),
        "provider_circuit_state": state.value,
        "provider_circuit_capabilities": str(len(snapshots)),
        "provider_circuit_open_capabilities": str(
            sum(item.state == CircuitState.OPEN for item in snapshots)
        ),
        "provider_circuit_half_open_capabilities": str(
            sum(item.state == CircuitState.HALF_OPEN for item in snapshots)
        ),
        "provider_circuit_failures": str(sum(item.total_failures for item in snapshots)),
        "provider_circuit_successes": str(sum(item.total_successes for item in snapshots)),
        "provider_circuit_opened": str(sum(item.opened for item in snapshots)),
        "provider_circuit_half_open_probes": str(
            sum(item.half_open_probes for item in snapshots)
        ),
        "provider_circuit_recoveries": str(sum(item.recoveries for item in snapshots)),
        "provider_circuit_retry_after_seconds": (
            f"{max(item.retry_after_seconds for item in snapshots):.3f}"
        ),
    }


def _failure_snapshot_severity(snapshot: CircuitSnapshot) -> int:
    return {
        FailureCategory.AUTH.value: 9,
        FailureCategory.SERVER.value: 8,
        FailureCategory.RATE_LIMIT.value: 7,
        FailureCategory.TIMEOUT.value: 6,
        FailureCategory.DNS.value: 5,
        FailureCategory.TRANSPORT.value: 4,
        FailureCategory.INVALID_REQUEST.value: 3,
        FailureCategory.UNSUPPORTED.value: 2,
        FailureCategory.NOT_LISTED.value: 1,
    }.get(snapshot.last_failure_category or "", 0)


def _provider_failure_registries(provider: object) -> list[ProviderFailureStateRegistry]:
    stack = [provider]
    seen_objects: set[int] = set()
    seen_registries: set[int] = set()
    registries: list[ProviderFailureStateRegistry] = []
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen_objects:
            continue
        seen_objects.add(id(current))
        registry = getattr(current, "failure_registry", None)
        if isinstance(registry, ProviderFailureStateRegistry) and id(registry) not in seen_registries:
            seen_registries.add(id(registry))
            registries.append(registry)
        for attribute in (
            "provider",
            "market_data_provider",
            "snapshot_provider",
            "primary",
            "fallback",
        ):
            stack.append(getattr(current, attribute, None))
        providers_by_market = getattr(current, "providers_by_market", None)
        if isinstance(providers_by_market, dict):
            stack.extend(providers_by_market.values())
    return registries
