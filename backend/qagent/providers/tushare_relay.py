"""Read-only access to the user-designated ProMax relay, never official Tushare.

The live catalogue grants data-route access, not data quality or trading authority.
Market daily fallback requires explicit opt-in. Credentials are header-only.
"""

from dataclasses import dataclass, field
import math
import re
import time
from threading import Lock
from typing import Callable

import httpx


RELAY_BASE_URL = "https://pcd.mobcvb.cn/tushare"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_PARAM = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,79}\Z")
_FORBIDDEN = {"token", "api_key", "key", "authorization", "x_api_key", "password"}
_MUTATIONS = {"save", "delete", "create", "update", "cancel", "order", "orders", "execute"}
_RATE_LOCK = Lock()
_last_request_at = 0.0


class RelayError(RuntimeError):
    """Safe structured failure: never retain remote text, credentials or requests."""

    def __init__(self, kind: str, *, status_code: int | None = None):
        self.kind = kind
        self.status_code = status_code
        super().__init__(f"tushare_relay:{kind}" + (f" (HTTP {status_code})" if status_code else ""))


@dataclass(frozen=True)
class RelayTable:
    api: str
    fields: tuple[str, ...]
    rows: tuple[dict[str, object], ...]
    # Deliberately not 'complete': a successful page need not cover the query.
    row_limit: int
    source: str = "tushare_relay_promax"


@dataclass
class TushareRelayClient:
    api_key: str = field(repr=False)
    timeout_seconds: float = 30.0
    retries: int = 1
    # MockTransport injection preserves the production security policy.
    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _catalogue: dict[str, dict] | None = field(default=None, init=False, repr=False)
    _catalogue_at: float = field(default=0, init=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise RelayError("missing_config")
        if any(ord(c) < 33 or ord(c) > 126 for c in self.api_key):
            raise RelayError("invalid_config")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 30:
            raise RelayError("invalid_config")
        if type(self.retries) is not int or not 0 <= self.retries <= 2:
            raise RelayError("invalid_config")

    def _get(self, path: str, params: dict | None = None) -> dict:
        global _last_request_at
        if path != "/capabilities" and not re.fullmatch(r"/pro/[a-z][a-z0-9_]{0,79}", path):
            raise RelayError("forbidden_api")
        # Respect deployment proxy/CA environment like the documented requests path.
        # Keep a fixed HTTPS destination, certificate verification and no redirects.
        with httpx.Client(
            timeout=self.timeout_seconds, follow_redirects=False, verify=True,
            trust_env=self.transport is None, transport=self.transport,
        ) as client:
            for attempt in range(self.retries + 1):
                with _RATE_LOCK:
                    wait = 0.35 - (self.clock() - _last_request_at)
                    if wait > 0:
                        self.sleep(min(wait, 0.35))
                    _last_request_at = self.clock()
                try:
                    response = client.get(
                        RELAY_BASE_URL + path, params=params,
                        headers={"X-API-Key": self.api_key},
                    )
                except httpx.TransportError:
                    if attempt < self.retries:
                        self.sleep(2 ** attempt)
                        continue
                    raise RelayError("transport_error") from None
                status = response.status_code
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and body.get("error") == "data_source_unavailable":
                    raise RelayError("data_source_unavailable", status_code=status)
                if status in {429, 502, 503, 504} and attempt < self.retries:
                    delay = float(2 ** attempt)
                    retry_after = response.headers.get("Retry-After")
                    if retry_after is not None:
                        try:
                            delay = max(delay, float(retry_after))
                        except ValueError:
                            # Dates / unknown intervals are not safe to retry early.
                            raise RelayError("retry_deferred", status_code=status) from None
                        if not math.isfinite(delay) or delay > 4:
                            raise RelayError("retry_deferred", status_code=status)
                    self.sleep(delay)
                    continue
                if status != 200:
                    kind = "pending" if status == 202 else "http_error"
                    if (status == 503 and isinstance(body, dict)
                            and body.get("error") == "upstream_pool_exhausted"):
                        kind = "upstream_pool_exhausted"
                    raise RelayError(kind, status_code=status)
                if not isinstance(body, dict):
                    raise RelayError("invalid_json")
                if body.get("ok") is False or ("code" in body and body["code"] != 0):
                    raise RelayError("upstream_error", status_code=status)
                return body
        raise RelayError("transport_error")  # defensive; loop always returns or raises

    def capabilities(self, *, refresh: bool = False) -> dict[str, dict]:
        if self._catalogue is None or refresh or self.clock() - self._catalogue_at >= 300:
            body = self._get("/capabilities")
            entries = body.get("interfaces")
            if not isinstance(entries, list):
                raise RelayError("catalogue_schema")
            catalogue = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise RelayError("catalogue_schema")
                name = entry.get("api_name", entry.get("name"))
                if not isinstance(name, str) or not _NAME.fullmatch(name):
                    raise RelayError("catalogue_schema")
                if name in catalogue:
                    raise RelayError("catalogue_schema")
                catalogue[name] = entry.copy()
            self._catalogue = catalogue
            self._catalogue_at = self.clock()
        # Callers cannot modify the internally authorized catalogue.
        from copy import deepcopy
        return deepcopy(self._catalogue)

    def query(self, api: str, *, limit: int = 500, offset: int = 0,
              fields: str | None = None, **params) -> RelayTable:
        if (not isinstance(api, str) or not _NAME.fullmatch(api) or api.startswith("p_")
                or _MUTATIONS.intersection(api.split("_"))):
            raise RelayError("forbidden_api")
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise RelayError("invalid_params")
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise RelayError("invalid_params")
        clean = {}
        for key, value in params.items():
            if not _PARAM.fullmatch(key) or key.lower() in _FORBIDDEN:
                raise RelayError("invalid_params")
            if value is None:
                continue
            if type(value) not in {str, int, float, bool} or len(str(value)) > 4096:
                raise RelayError("invalid_params")
            if isinstance(value, float) and not math.isfinite(value):
                raise RelayError("invalid_params")
            if isinstance(value, str) and (not value.strip() or self.api_key in value):
                raise RelayError("invalid_params")
            clean[key] = value
        if fields is not None:
            if not isinstance(fields, str):
                raise RelayError("invalid_params")
            names = fields.split(",")
            if len(names) > 300 or len(names) != len(set(names)) or any(
                not _PARAM.fullmatch(name) for name in names
            ):
                raise RelayError("invalid_params")
        entry = self.capabilities().get(api)
        if entry is None:
            raise RelayError("unknown_api")
        if entry.get("enabled") is not True:
            raise RelayError("disabled_api")
        methods = entry.get("methods")
        if not isinstance(methods, list) or "GET" not in methods:
            raise RelayError("forbidden_api")
        required = entry.get("required", [])
        groups = entry.get("required_any", [])
        if not isinstance(required, list) or any(not isinstance(k, str) for k in required):
            raise RelayError("catalogue_schema")
        if any(k not in clean for k in required):
            raise RelayError("missing_params")
        if not isinstance(groups, list):
            raise RelayError("catalogue_schema")
        # A flat list means any parameter; nested lists mean any complete group.
        if groups:
            if all(isinstance(k, str) for k in groups):
                groups = [[k] for k in groups]
            if any(not isinstance(g, list) or not g or any(not isinstance(k, str) for k in g)
                   for g in groups):
                raise RelayError("catalogue_schema")
            if not any(all(k in clean for k in group) for group in groups):
                raise RelayError("missing_params")
        # Always request business data, even for legitimately unfiltered APIs.
        clean.update(limit=limit, offset=offset, __probe=0)
        if fields is not None:
            clean["fields"] = fields
        body = self._get("/pro/" + api, clean)
        if type(body.get("code")) is not int or body["code"] != 0:
            raise RelayError("upstream_error")
        data = body.get("data")
        if not isinstance(data, dict):
            raise RelayError("table_schema")
        columns, items = data.get("fields"), data.get("items")
        if (not isinstance(columns, list) or any(not isinstance(c, str) or not c for c in columns)
                or len(set(columns)) != len(columns) or not isinstance(items, list)
                or len(items) > limit
                or any(not isinstance(row, list) or len(row) != len(columns) for row in items)):
            raise RelayError("table_schema")
        return RelayTable(api, tuple(columns), tuple(dict(zip(columns, row)) for row in items), limit)
