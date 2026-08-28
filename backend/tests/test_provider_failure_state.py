from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from qagent.providers.failure_state import (
    CircuitOpenError,
    CircuitState,
    FailureCategory,
    FailureKey,
    ProviderFailureStateRegistry,
    provider_failure_state_data_health,
)


def test_failure_state_backoff_half_open_single_probe_and_recovery():
    now = [100.0]
    registry = ProviderFailureStateRegistry(
        failure_threshold=2,
        base_backoff_seconds=10,
        max_backoff_seconds=60,
        jitter_ratio=0,
        clock=lambda: now[0],
    )
    key = FailureKey("fuyao", "https://example.test", "daily")

    registry.acquire(key)
    registry.failure(key, FailureCategory.DNS)
    registry.acquire(key)
    registry.failure(key, FailureCategory.TIMEOUT)

    assert registry.snapshot(key).state == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        registry.acquire(key)

    now[0] += 10
    results: list[str] = []

    def acquire() -> None:
        try:
            registry.acquire(key)
            results.append("probe")
        except CircuitOpenError:
            results.append("blocked")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: acquire(), range(8)))

    assert results.count("probe") == 1
    assert results.count("blocked") == 7
    registry.success(key)
    snapshot = registry.snapshot(key)
    assert snapshot.state == CircuitState.CLOSED
    assert snapshot.half_open_probes == 1
    assert snapshot.recoveries == 1


def test_invalid_and_unsupported_outcomes_do_not_open_or_negative_cache_transport():
    registry = ProviderFailureStateRegistry(failure_threshold=1, jitter_ratio=0)
    key = FailureKey("baostock", "public-api.baostock.com", "daily")

    for category in (
        FailureCategory.INVALID_REQUEST,
        FailureCategory.UNSUPPORTED,
        FailureCategory.NOT_LISTED,
    ):
        registry.acquire(key)
        registry.failure(key, category)

    assert registry.snapshot(key).state == CircuitState.CLOSED
    assert registry.snapshot(key).total_failures == 0

    registry.acquire(key)
    registry.failure(key, FailureCategory.TRANSPORT)
    assert registry.snapshot(key).state == CircuitState.OPEN


def test_provider_failure_health_exposes_stable_structured_classification():
    class Provider:
        failure_registry = ProviderFailureStateRegistry(failure_threshold=1, jitter_ratio=0)

    provider = Provider()
    key = FailureKey("tickflow_free", "https://example.test", "daily")
    provider.failure_registry.acquire(key)
    provider.failure_registry.failure(
        key,
        FailureCategory.RATE_LIMIT,
        retry_after_seconds=30,
        error_code=429,
    )

    health = provider_failure_state_data_health(provider)

    assert health["provider_error_kind"] == "rate_limit"
    assert health["provider_error_code"] == "429"
    assert health["provider_error_retryable"] == "true"
    assert health["provider_circuit_state"] == "open"
    assert health["provider_circuit_open_capabilities"] == "1"
    assert float(health["provider_circuit_retry_after_seconds"]) > 0


def test_unsupported_health_is_not_retryable_and_does_not_open_circuit():
    class Provider:
        failure_registry = ProviderFailureStateRegistry(failure_threshold=1, jitter_ratio=0)

    provider = Provider()
    key = FailureKey("baostock", "public-api.baostock.com", "daily")
    provider.failure_registry.acquire(key)
    provider.failure_registry.failure(
        key,
        FailureCategory.UNSUPPORTED,
        error_code="bj_unsupported",
    )

    health = provider_failure_state_data_health(provider)
    assert health["provider_error_kind"] == "unsupported"
    assert health["provider_error_code"] == "bj_unsupported"
    assert health["provider_error_retryable"] == "false"
    assert health["provider_circuit_state"] == "closed"


def test_auth_health_is_configuration_failure_with_long_request_cooldown():
    now = [100.0]

    class Provider:
        failure_registry = ProviderFailureStateRegistry(
            failure_threshold=99,
            base_backoff_seconds=1,
            max_backoff_seconds=5,
            auth_backoff_seconds=15 * 60,
            jitter_ratio=0,
            clock=lambda: now[0],
        )

    provider = Provider()
    key = FailureKey("fuyao", "https://example.test", "snapshot")
    provider.failure_registry.acquire(key)
    provider.failure_registry.failure(
        key,
        FailureCategory.AUTH,
        error_code=401,
    )

    health = provider_failure_state_data_health(provider)
    assert health["provider_error_kind"] == "auth"
    assert health["provider_error_code"] == "configuration_auth"
    assert health["provider_error_retryable"] == "false"
    assert health["provider_circuit_state"] == "open"
    assert float(health["provider_circuit_retry_after_seconds"]) == 900
    with pytest.raises(CircuitOpenError):
        provider.failure_registry.acquire(key)
