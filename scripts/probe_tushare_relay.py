"""Isolated, bounded relay check. Never persists market data or credentials."""

import argparse
import json
import math
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE = "https://pcd.mobcvb.cn/tushare/pro"
SAFE_FIELDS = frozenset("ts_code trade_date trade_time datetime time open high low close vol amount adj_factor".split())
APIS = ("daily", "adj_factor", "stk_mins", "rt_min_daily")


def data_day(value):
    if not isinstance(value, str):
        raise ValueError
    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?", value):
        return datetime.fromisoformat(value).strftime("%Y%m%d")
    raise ValueError


def summarize(body, result, requested, symbol):
    if not isinstance(body, dict):
        result["status"] = "invalid_payload"
        return
    code = body.get("code")
    if type(code) in (int, float) and math.isfinite(code):
        result["code"] = code
    if body.get("ok") is False or type(code) not in (int, float) or code != 0:
        result["status"] = "upstream_error"
        return
    data = body.get("data")
    if not isinstance(data, dict):
        result["status"] = "invalid_payload"
        return
    fields, rows = data.get("fields"), data.get("items")
    if (not isinstance(fields, list) or not all(isinstance(f, str) for f in fields)
            or len(set(fields)) != len(fields) or not isinstance(rows, list)):
        result["status"] = "invalid_payload"
        return
    result.update(fields=[f for f in fields if f in SAFE_FIELDS], rows=len(rows))
    if len(rows) > 5:
        result["status"] = "row_limit_exceeded"
        return
    if not rows:
        result["status"] = "no_rows"
        return
    minute = result["api"] in APIS[2:]
    date_field = next((f for f in ("trade_time", "datetime", "time") if f in fields), None) if minute else "trade_date"
    required = {"ts_code", "adj_factor"} if result["api"] == "adj_factor" else {"ts_code", "open", "high", "low", "close"}
    if not minute:
        required.add("trade_date")
    if not required.issubset(fields) or date_field is None:
        result["status"] = "missing_fields"
        return
    dates, seen = [], set()
    try:
        for row in rows:
            if not isinstance(row, list) or len(row) != len(fields):
                raise ValueError
            values = dict(zip(fields, row))
            if values["ts_code"] != symbol:
                result["status"] = "symbol_mismatch"
                return
            identity = values[date_field]
            if not isinstance(identity, str):
                raise ValueError
            if minute and not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", identity):
                raise ValueError
            if identity in seen:
                result["status"] = "duplicate_rows"
                return
            seen.add(identity)
            dates.append(data_day(values[date_field]))
            if result["api"] == "adj_factor":
                factor = values["adj_factor"]
                if type(factor) not in (int, float) or not math.isfinite(factor) or factor <= 0:
                    result["status"] = "invalid_factor"
                    return
            else:
                prices = [values[f] for f in ("open", "high", "low", "close")]
                if any(type(p) not in (int, float) or not math.isfinite(p) or p <= 0 for p in prices):
                    result["status"] = "invalid_ohlc"
                    return
                opening, high, low, close = prices
                if not low <= min(opening, close) <= max(opening, close) <= high:
                    result["status"] = "invalid_ohlc"
                    return
    except (ValueError, TypeError, OverflowError):
        result["status"] = "invalid_rows"
        return
    result.update(min_date=min(dates), max_date=max(dates))
    result["status"] = "sample_valid" if all(d == requested for d in dates) else "date_mismatch"


def probe(api, key, symbol, date):
    result = {"api": api, "status": "transport_error"}
    params = {"ts_code": symbol, "__probe": 0, "limit": 5}
    expected = date
    if api in APIS[:2]:
        params.update(start_date=date, end_date=date)
    elif api == "stk_mins":
        day = datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d")
        params.update(freq="1min", start_date=f"{day} 09:30:00", end_date=f"{day} 15:00:00")
    else:
        params["freq"] = "1min"
        today = datetime.now(ZoneInfo("Asia/Shanghai"))
        expected = today.strftime("%Y%m%d")
        result["caveat"] = ("weekend_cannot_verify_realtime" if today.weekday() >= 5
                            else "single_sample_cannot_verify_realtime_latency")
    start = time.monotonic()
    try:
        response = requests.get(f"{BASE}/{api}", params=params, headers={"X-API-Key": key},
                                timeout=30, verify=True, allow_redirects=False)
        try:
            status = response.status_code
            result["http"] = status
            if status in (401, 403):
                result["status"] = "auth_error"
            elif status == 429:
                result["status"] = "rate_limited"
            elif status == 202:
                result["status"] = "pending_unverified"
            elif 300 <= status < 400:
                result["status"] = "redirect_blocked"
            elif not 200 <= status < 300:
                result["status"] = "http_error"
            else:
                try:
                    body = response.json()
                except ValueError:
                    result["status"] = "non_json"
                else:
                    summarize(body, result, expected, symbol)
        finally:
            response.close()
    except requests.RequestException:
        pass
    result["latency_ms"] = round((time.monotonic() - start) * 1000)
    return result


def main(argv=None):
    # Keep the original four-call audit available; broader modes use the catalogue gate.
    import sys
    values = sys.argv[1:] if argv is None else argv
    if any(flag in values for flag in ("--catalog", "--probe-all", "--formal")):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("relay_catalog_audit", Path(__file__).with_name("audit_tushare_relay.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.main(values)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Historical date YYYYMMDD")
    parser.add_argument("--symbol", default="000001.SZ")
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"\d{8}", args.date):
            raise ValueError
        data_day(args.date)
        if not re.fullmatch(r"\d{6}\.(SZ|SH|BJ)", args.symbol):
            raise ValueError
    except ValueError:
        print('{"status":"invalid_arguments"}')
        return 2
    key = os.environ.get("TUSHARE_RELAY_KEY", "").strip()
    if not key:
        print('{"status":"blocked_missing_key"}')
        return 2
    results = [probe(api, key, args.symbol, args.date) for api in APIS]
    for result in results:
        print(json.dumps(result, allow_nan=False))
    return 0 if all(r["status"] == "sample_valid" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
