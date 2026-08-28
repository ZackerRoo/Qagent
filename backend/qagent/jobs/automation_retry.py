from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta


AUTOMATION_RETRY_BUDGET = 4
AUTOMATION_RETRY_BASE_SECONDS = 5 * 60
AUTOMATION_RETRY_MAX_SECONDS = 20 * 60
AUTOMATION_BREAKER_BASE_SECONDS = 30 * 60
AUTOMATION_BREAKER_MAX_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class AutomationErrorClassification:
    retryable: bool
    error_kind: str
    fingerprint: str


def classify_automation_error(
    stage_key: str,
    provider: str,
    error: str,
    health: Mapping[str, object] | None = None,
) -> AutomationErrorClassification:
    normalized = _normalized_error(error)
    lower = normalized.lower()
    structured = {str(key): str(value).strip() for key, value in (health or {}).items()}
    kind = _health_value(structured, "provider_error_kind").lower()
    code = _health_value(structured, "provider_error_code")
    raw_retryable = _health_value(structured, "provider_error_retryable").lower()
    permanent_kinds = {
        "unsupported",
        "not_listed",
        "invalid_request",
        "permanent",
        "contract",
        "invalid",
    }
    retryable_kinds = {
        "transport",
        "dns",
        "timeout",
        "rate_limit",
        "server",
    }
    permanent_markers = (
        "telemetry is missing or invalid",
        "illegal paper trade status transition",
        "integrityerror",
        "validationerror",
        "contract violation",
        "different facts",
        "unauthorized",
        "forbidden",
        "authentication",
        "authorization",
        "auth failed",
        "auth error",
        "invalid api key",
        "invalid credential",
        "missing api key",
        "http 401",
        "http 403",
    )
    retryable_markers = (
        "provider",
        "timeout",
        "timed out",
        "connection",
        "rate limit",
        "http 429",
        "http 5",
        "price coverage=",
        "candidate_data_partially_stale_filtered",
        "candidate_data_stale_filtered",
    )
    if raw_retryable in {"false", "0", "no"} and (kind not in {"", "none"} or code):
        retryable = False
        error_kind = (
            "permanent_configuration/auth"
            if kind == "auth"
            else kind or "permanent_provider"
        )
    elif kind == "auth":
        retryable = False
        error_kind = "permanent_configuration/auth"
    elif kind in permanent_kinds:
        retryable = False
        error_kind = kind
    elif kind in retryable_kinds:
        retryable = True
        error_kind = kind
    elif raw_retryable in {"true", "1", "yes"}:
        retryable = True
        error_kind = kind or "provider_retryable"
    elif any(marker in lower for marker in permanent_markers):
        retryable = False
        error_kind = (
            "permanent_configuration/auth"
            if any(
                marker in lower
                for marker in (
                    "unauthorized",
                    "forbidden",
                    "authentication",
                    "authorization",
                    "auth failed",
                    "auth error",
                    "invalid api key",
                    "invalid credential",
                    "missing api key",
                    "http 401",
                    "http 403",
                )
            )
            else "permanent_contract"
        )
    elif stage_key in {"paper_update", "scan", "alerts"} and any(
        marker in lower for marker in retryable_markers
    ):
        retryable = True
        error_kind = "provider_or_coverage"
    else:
        retryable = False
        error_kind = "permanent_unknown"
    facts = {
        "stage": stage_key,
        "provider": provider.strip().lower(),
        "kind": error_kind,
        "code": code,
        "error": normalized,
    }
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return AutomationErrorClassification(
        retryable=retryable,
        error_kind=error_kind,
        fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


def _health_value(health: Mapping[str, str], suffix: str) -> str:
    exact = health.get(suffix)
    if exact is not None:
        return exact
    matches = [value for key, value in sorted(health.items()) if key.endswith(f"_{suffix}")]
    return matches[0] if matches else ""


def retry_backoff(attempt_count: int) -> timedelta:
    """Return 5m/10m/20m for failed attempts one through three."""

    exponent = max(int(attempt_count) - 1, 0)
    seconds = min(
        AUTOMATION_RETRY_BASE_SECONDS * (2**exponent),
        AUTOMATION_RETRY_MAX_SECONDS,
    )
    return timedelta(seconds=seconds)


def breaker_cooldown(open_count: int) -> timedelta:
    exponent = max(int(open_count) - 1, 0)
    seconds = min(
        AUTOMATION_BREAKER_BASE_SECONDS * (2**exponent),
        AUTOMATION_BREAKER_MAX_SECONDS,
    )
    return timedelta(seconds=seconds)


def _normalized_error(error: str) -> str:
    value = " ".join(str(error).strip().split())
    value = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b",
        "<uuid>",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+Z?\b",
        "<timestamp>",
        value,
    )
    value = re.sub(r"\b(request|trace|job)[-_ ]?id[=: ]+[^ ]+", r"\1_id=<id>", value, flags=re.I)
    return value[:1000]
