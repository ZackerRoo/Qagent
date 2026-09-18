"""System-level raw research gateway; never writes storage or builds a strategy."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re

from qagent.config import Settings
from qagent.providers.datahubco import DatahubcoClient, DatahubcoError, DOCUMENTED_APIS, READ_APIS
from qagent.providers.tushare_relay import TushareRelayClient, RelayError, _MUTATIONS

NAME = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
PARAM = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,79}\Z")
FORBIDDEN_PARAMS = {"key", "token", "api_key", "auth", "authorization", "password", "x_api_key",
                    "headers", "url", "base_url", "baseurl", "host", "path", "endpoint", "proxy", "proxies",
                    "limit", "offset", "fields", "timeout", "verify", "transport"}
SAFE_ERRORS = frozenset({"source_disabled", "missing_config", "insecure_http_not_authorized",
    "invalid_config", "invalid_params", "unknown_api", "http_unsupported_api", "forbidden_api",
    "transport_error", "http_error", "invalid_json", "upstream_error", "table_schema",
    "unsafe_response", "catalogue_schema", "disabled_api", "missing_params", "pending",
    "upstream_pool_exhausted", "data_source_unavailable", "retry_deferred", "invalid_date",
    "invalid_date_range", "date_pair_required", "date_conflict", "invalid_fields", "invalid_pagination"})


def safe_error(exc):
    kind = getattr(exc, "kind", None)
    return kind if isinstance(kind, str) and kind in SAFE_ERRORS else "research_failed"


def seal(report):
    report.update(research_only=True, decision_weight=False, activation_allowed=False)
    report["result_digest"] = sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return report


def read_only_name(name):
    return bool(isinstance(name, str) and NAME.fullmatch(name) and not name.startswith("p_")
                and not _MUTATIONS.intersection(name.split("_")))


class DocumentedResearch:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _secrets(self):
        return tuple(value.get_secret_value() for value in (
            self.settings.datahubco_key, self.settings.tushare_relay_key) if value and value.get_secret_value())

    def _reject_secret(self, value):
        encoded = json.dumps(value, allow_nan=False)
        if any(secret in encoded for secret in self._secrets()):
            raise RelayError("unsafe_response")

    def _client(self, source):
        if source == "datahubco":
            if not self.settings.datahubco_enabled:
                raise DatahubcoError("source_disabled")
            if not self.settings.datahubco_allow_insecure_http:
                raise DatahubcoError("insecure_http_not_authorized")
            if not self.settings.datahubco_key:
                raise DatahubcoError("missing_config")
            return DatahubcoClient(self.settings.datahubco_key.get_secret_value(),
                                   allow_insecure_http=True, timeout_seconds=30)
        if source == "promax":
            if not self.settings.tushare_relay_research_enabled:
                raise RelayError("source_disabled")
            if not self.settings.tushare_relay_key:
                raise RelayError("missing_config")
            return TushareRelayClient(self.settings.tushare_relay_key.get_secret_value(),
                                      timeout_seconds=30, retries=0)
        raise RelayError("invalid_params")

    def catalogue(self):
        sources = {}
        basic = {"catalogue_origin": "documented_20260827_plus_stock_basic_example", "entries": [
            {"api": name, "enabled": name in READ_APIS, "read_only": name in READ_APIS,
             "callable": False, "classification": "read_only" if name in READ_APIS else "http_unsupported"}
            for name in sorted(DOCUMENTED_APIS)]}
        sources["datahubco"] = basic
        try:
            self._client("datahubco")  # local configuration validation only
            basic["status"] = "configured"
            for entry in basic["entries"]:
                entry["callable"] = entry["read_only"]
        except Exception as exc:
            basic.update(status="error", error=safe_error(exc))
        relay = {"catalogue_origin": "live_capabilities", "entries": []}
        sources["promax"] = relay
        try:
            catalogue = self._client("promax").capabilities()
            for name, entry in sorted(catalogue.items()):
                if not NAME.fullmatch(name) or not isinstance(entry, dict):
                    raise RelayError("catalogue_schema")
                enabled = entry.get("enabled") is True
                readonly = read_only_name(name) and isinstance(entry.get("methods"), list) and "GET" in entry["methods"]
                relay["entries"].append({"api": name, "enabled": enabled, "read_only": readonly,
                                         "callable": enabled and readonly,
                                         "classification": "read_only" if readonly else "blocked_operation"})
            self._reject_secret(relay)
            relay["status"] = "observed"
        except Exception as exc:
            relay.update(status="error", error=safe_error(exc), entries=[])
        for source in sources.values():
            source["count"] = len(source["entries"])
            source["callable_count"] = sum(entry["callable"] for entry in source["entries"])
        return seal({"sources": sources,
                     "status": "incomplete" if any(s["status"] == "error" for s in sources.values()) else "observed",
                     "fetched_at": datetime.now(timezone.utc).isoformat(),
                     "warnings": ["Catalogue routing is not business-data validation or trading authorization."]})

    def query(self, source, api, params=None, limit=100, offset=0, fields=None):
        response = {"source": source if source in ("datahubco", "promax") else None,
                    "fields": [], "rows": [], "request": None,
                    "warnings": ["Raw research table; semantics, coverage and historical PIT unverified."]}
        try:
            if (source not in ("datahubco", "promax") or not isinstance(api, str) or not NAME.fullmatch(api)
                    or type(limit) is not int or not 1 <= limit <= 5000
                    or type(offset) is not int or not 0 <= offset <= 1000000):
                raise RelayError("invalid_params")
            params = {} if params is None else params
            if not isinstance(params, dict) or len(params) > 100:
                raise RelayError("invalid_params")
            for key, value in params.items():
                if (not isinstance(key, str) or not PARAM.fullmatch(key)
                        or key.lower() in FORBIDDEN_PARAMS
                        or type(value) not in (str, int, float, bool, type(None))
                        or len(str(value)) > 4096
                        or (isinstance(value, float) and not math.isfinite(value))):
                    raise RelayError("invalid_params")
            if fields is not None and (not isinstance(fields, str) or len(fields.split(",")) > 300
                    or len(set(fields.split(","))) != len(fields.split(","))
                    or any(not PARAM.fullmatch(field) for field in fields.split(","))):
                raise RelayError("invalid_fields")
            request = {"api": api, "params": params, "limit": limit, "offset": offset, "fields": fields}
            self._reject_secret(request)
            response["request"] = request
            if not read_only_name(api):
                raise RelayError("forbidden_api")
            table = self._client(source).query(api, limit=limit, offset=offset, fields=fields, **params)
            self._reject_secret({"fields": table.fields, "rows": table.rows})
            response.update(status="observed" if table.rows else "no_rows", fields=list(table.fields),
                            rows=list(table.rows), data_source=table.source,
                            coverage={"rows": len(table.rows), "row_limit": limit,
                                      "page_limit_reached": len(table.rows) == limit})
        except Exception as exc:
            response.update(status="error", error=safe_error(exc))
        response["fetched_at"] = datetime.now(timezone.utc).isoformat()
        return seal(response)
