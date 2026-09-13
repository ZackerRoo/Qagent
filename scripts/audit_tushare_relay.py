"""Read-only relay catalogue and bounded A-share checks; stdout only, no database.

Examples (credential must already be in TUSHARE_RELAY_KEY):
  python scripts/audit_tushare_relay.py --catalog
  python scripts/audit_tushare_relay.py --catalog --probe-all
  python scripts/audit_tushare_relay.py --formal --date 20260911 --period 20251231
Probe samples never certify formal business availability or realtime freshness.
Exit 0 means catalogue fetched and requested checks yielded at least valid shape;
exit 1 includes no rows, failed/blocked checks, or unavailable catalogue. Exit 2
means missing credentials or invalid arguments/catalogue. No rows is not an error
from the provider, but remains unverified evidence for this audit.
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

BASE = "https://pcd.mobcvb.cn/tushare"
ERRORS = frozenset("invalid_params unauthorized unknown_api method_not_allowed rate_limited ip_rate_limited data_source_unavailable minute_data_pending upstream_timeout citydata_timeout upstream_pool_exhausted".split())
NAME = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
SPEC = importlib.util.spec_from_file_location("relay_validation", Path(__file__).with_name("probe_tushare_relay.py"))
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


class Client:
    """At most three attempts; all requests (including retries) spaced at 50/min."""

    def __init__(self, key):
        self.key = key
        self.last = None

    def get(self, path, params=None):
        if path != "/capabilities" and not re.fullmatch(r"/pro/[a-z][a-z0-9_]{0,79}", path):
            return {"status": "invalid_path"}, None
        result = {"status": "transport_error"}
        for attempt in range(3):
            if self.last is not None:
                time.sleep(max(0, 1.2 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            response = None
            body = None
            retry_after = None
            try:
                response = requests.get(BASE + path, params=params, headers={"X-API-Key": self.key},
                                        timeout=30, verify=True, allow_redirects=False)
                status = response.status_code
                result = {"http": status, "attempts": attempt + 1}
                try:
                    body = response.json()
                except ValueError:
                    pass
                error = body.get("error") if isinstance(body, dict) else None
                if isinstance(error, str) and error in ERRORS:
                    result["error"] = error
                if 300 <= status < 400:
                    result["status"] = "redirect_blocked"
                elif status in (401, 403):
                    result["status"] = "auth_error"
                elif status == 202:
                    result["status"] = "pending_unverified"
                elif status == 429:
                    result["status"] = "rate_limited"
                elif not 200 <= status < 300:
                    result["status"] = "http_error"
                elif body is None:
                    result["status"] = "non_json"
                else:
                    result["status"] = "received"
                    return result, body
                if status not in (429, 502, 503, 504) or error == "data_source_unavailable":
                    return result, None
                retry_after = response.headers.get("Retry-After")
            except requests.RequestException:
                result = {"status": "transport_error", "attempts": attempt + 1}
            finally:
                if response is not None:
                    response.close()
            delay = 2 ** attempt
            if isinstance(retry_after, str):
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        delay = max(delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        return {**result, "retry": "invalid_retry_after"}, None
                if not 0 <= delay <= 60:
                    return {**result, "retry": "deferred_retry_after"}, None
            if attempt < 2:
                time.sleep(delay)
        return result, None


def catalogue(body):
    """Accept list or API-keyed registries and common explicitly named envelopes."""
    if isinstance(body, dict):
        if body.get("ok") is False or body.get("code", 0) not in (0, None):
            raise ValueError
        for key in ("interfaces", "apis", "capabilities", "data", "items"):
            if key in body:
                return catalogue(body[key])
        entries = [{**value, "api_name": key} for key, value in body.items() if isinstance(value, dict)]
    elif isinstance(body, list):
        entries = body
    else:
        raise ValueError
    output = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError
        name = entry.get("api_name", entry.get("name", entry.get("api")))
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError
        methods = entry.get("methods", [])
        if isinstance(methods, str):
            methods = [methods]
        if not isinstance(methods, list):
            raise ValueError
        output.append({"api": name, "enabled": entry.get("enabled") if type(entry.get("enabled")) is bool else None,
                       "methods": [m for m in methods if m in ("GET", "POST", "DELETE", "PUT", "PATCH")],
                       "required": entry.get("required", []), "required_any": entry.get("required_any", [])})
    if not output or len({e["api"] for e in output}) != len(output):
        raise ValueError
    return output


def safe_get(entry):
    name = entry["api"]
    blocked = ("account", "portfolio", "save", "delete", "create", "update", "order", "token", "login")
    public_fund_holdings = name == "fund_portfolio"
    return (entry["enabled"] is True and entry["methods"] == ["GET"] and not name.startswith("p_")
            and (public_fund_holdings or not any(x in name for x in blocked)))


def shape(body):
    if not isinstance(body, dict) or body.get("ok") is False or type(body.get("code")) is not int or body["code"] != 0:
        return {"status": "upstream_error"}
    data = body.get("data")
    if not isinstance(data, dict):
        return {"status": "invalid_payload"}
    fields, rows = data.get("fields"), data.get("items")
    if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields) or len(set(fields)) != len(fields) or not isinstance(rows, list):
        return {"status": "invalid_payload"}
    if any(not isinstance(row, list) or len(row) != len(fields) for row in rows):
        return {"status": "invalid_rows"}
    if rows and not fields:
        return {"status": "invalid_payload"}
    if len(rows) > 5:
        return {"status": "row_limit_exceeded", "rows": len(rows)}
    return {"status": "valid_shape" if rows else "no_rows", "rows": len(rows), "field_count": len(fields)}


def formal_suite(symbol, date, period):
    """Parameter templates from supplied promax sections 8.1, 8.3, 8.4, 8.6, 8.7."""
    span = {"start_date": date, "end_date": date}
    queries = {api: {"ts_code": symbol, **span} for api in ("daily", "adj_factor", "moneyflow")}
    queries["daily_basic"] = {"ts_code": symbol, "trade_date": date}
    queries["stock_basic"] = {"exchange": "SSE", "list_status": "L"}
    queries["trade_cal"] = {"exchange": "SSE", **span}
    queries.update({api: {"ts_code": symbol, "period": period} for api in ("income", "balancesheet", "cashflow", "fina_indicator")})
    queries["index_daily"] = {"ts_code": "000300.SH", **span}
    queries["get_industries"] = {"level": "L1", "src": "SW2021"}
    queries["get_index_stocks"] = {"index_symbol": "000300.SH", "start_date": date}
    day = datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d")
    for api in ("a_share_mins", "stk_mins"):
        queries[api] = {"ts_code": symbol, "freq": "5min" if api == "a_share_mins" else "1min",
                        "start_date": day + " 09:30:00", "end_date": day + " 15:00:00"}
    for api in ("rt_min", "rt_min_daily"):
        queries[api] = {"ts_code": symbol, "freq": "1min"}
    queries["rt_k"] = {"ts_code": symbol}
    return {api: {**params, "__probe": 0, "limit": 5} for api, params in queries.items()}


def requirements_met(entry, params):
    required, alternatives = entry["required"], entry["required_any"]
    if not isinstance(required, list) or not all(isinstance(p, str) for p in required):
        return False
    if not all(params.get(p) not in (None, "") for p in required):
        return False
    if not isinstance(alternatives, list):
        return False
    if not alternatives:
        return True
    # Flat list = any one parameter; nested list = any complete group.
    return any(all(isinstance(p, str) and params.get(p) not in (None, "") for p in group)
               for group in ([x] if isinstance(x, str) else x for x in alternatives) if isinstance(group, list) and group)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", action="store_true")
    parser.add_argument("--probe-all", action="store_true")
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--date")
    parser.add_argument("--period", default="20251231")
    parser.add_argument("--symbol", default="000001.SZ")
    args = parser.parse_args(argv)
    key = os.environ.get("TUSHARE_RELAY_KEY", "").strip()
    if not key:
        print('{"status":"blocked_missing_key"}')
        return 2
    try:
        if args.formal:
            validation.data_day(args.date)
            validation.data_day(args.period)
            if not re.fullmatch(r"\d{8}", args.date) or not re.fullmatch(r"\d{8}", args.period) or not re.fullmatch(r"\d{6}\.(SZ|SH|BJ)", args.symbol):
                raise ValueError
        client = Client(key)
        transport, body = client.get("/capabilities")
        entries = catalogue(body) if body is not None else []
    except (ValueError, TypeError):
        print('{"status":"invalid_arguments_or_catalogue"}')
        return 2
    report = {"catalogue_transport": transport, "catalogue_count": len(entries),
              "enabled_count": sum(e["enabled"] is True for e in entries),
              "disabled_count": sum(e["enabled"] is False for e in entries),
              "unknown_enabled_count": sum(e["enabled"] is None for e in entries),
              "catalogue": [{"api": e["api"], "enabled": e["enabled"], "methods": e["methods"], "safe_get": safe_get(e)} for e in entries],
              "checks": [], "caveat": "catalogue_claims_and_probe_samples_do_not_verify_business_availability"}
    checks = report["checks"]
    if args.probe_all:
        for entry in entries:
            if safe_get(entry):
                meta, body = client.get("/pro/" + entry["api"], {"__probe": 1, "limit": 5})
                checks.append({"api": entry["api"], "mode": "local_probe", **meta, **(shape(body) if body is not None else {})})
                print(json.dumps({k: checks[-1][k] for k in ("api", "mode", "status")}).replace(key, "[REDACTED]"), file=sys.stderr, flush=True)
    if args.formal:
        index = {e["api"]: e for e in entries}
        for api, params in formal_suite(args.symbol, args.date, args.period).items():
            entry = index.get(api)
            if entry is None or not safe_get(entry) or not requirements_met(entry, params):
                checks.append({"api": api, "mode": "formal", "status": "catalogue_gate_blocked"})
                continue
            meta, body = client.get("/pro/" + api, params)
            result = {"api": api, "mode": "formal", **meta, **(shape(body) if body is not None else {})}
            if body is not None and api in ("daily", "adj_factor", "index_daily", "stk_mins", "a_share_mins", "rt_min", "rt_min_daily"):
                mapped = "stk_mins" if api in ("a_share_mins", "rt_min") else "daily" if api == "index_daily" else api
                quality = {"api": mapped}
                expected = datetime.now(validation.ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d") if api.startswith("rt_") else args.date
                validation.summarize(body, quality, expected, params["ts_code"])
                result.update({k: v for k, v in quality.items() if k != "api"})
                if result["status"] == "sample_valid":
                    result["status"] = "business_sample_valid"
            result["caveat"] = "single_sample_does_not_verify_coverage_or_realtime_freshness"
            checks.append(result)
            print(json.dumps({k: result[k] for k in ("api", "mode", "status")}).replace(key, "[REDACTED]"), file=sys.stderr, flush=True)
    # No raw responses, field values, arbitrary errors, URLs, or response headers.
    print(json.dumps(report, ensure_ascii=True, allow_nan=False).replace(key, "[REDACTED]"))
    return 0 if entries and all(c["status"] in ("valid_shape", "business_sample_valid") for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
