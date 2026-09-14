#!/usr/bin/env python3
"""Bounded current cashflow observations; offline fixtures or explicit live opt-in.

Fixture: {"retrieved_at": ISO timestamp with timezone, "instruments": {
"000001.SZ": {"cashflow": [raw rows], "income": [raw rows]}}}.
Only report_type=1 (consolidated cumulative) is consumed. No ranking or PIT claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

from rank_g2_consensus import publish

LIMIT = 12
SOURCE = "tushare_relay_promax"
SAFE_ERRORS = frozenset({"transport_error", "missing_config", "invalid_config", "http_error",
                         "upstream_pool_exhausted", "data_source_unavailable", "pending",
                         "upstream_error", "invalid_json", "table_schema", "catalogue_schema",
                         "unknown_api", "disabled_api", "forbidden_api", "missing_params"})


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("invalid_date")
    return datetime.strptime(value, "%Y%m%d").date()


def normalize(rows, symbol, api, retrieved):
    if not isinstance(rows, list) or len(rows) > LIMIT:
        raise ValueError("invalid_page")
    groups, excluded = {}, {}
    consumed = ("n_cashflow_act",) if api == "cashflow" else ("n_income", "total_revenue")
    for row in rows:
        if not isinstance(row, dict) or row.get("ts_code") != symbol:
            raise ValueError("symbol_mismatch")
        if str(row.get("report_type")) != "1":
            excluded["unsupported_report_type"] = excluded.get("unsupported_report_type", 0) + 1
            continue
        if str(row.get("comp_type")) != "1":
            excluded["unsupported_company_type"] = excluded.get("unsupported_company_type", 0) + 1
            continue
        period = day(row.get("end_date"))
        announcements = [day(row.get("ann_date"))]
        if row.get("f_ann_date") not in (None, ""):
            announcements.append(day(row["f_ann_date"]))
        if period.strftime("%m%d") not in {"0331", "0630", "0930", "1231"}:
            raise ValueError("unsupported_period")
        if period > min(announcements) or max(announcements) > retrieved:
            excluded["unavailable_at_retrieval"] = excluded.get("unavailable_at_retrieval", 0) + 1
            continue
        values = tuple(number(row.get(key)) for key in consumed)
        groups.setdefault(period.isoformat(), []).append((values, announcements))
    accepted, ambiguous = {}, []
    for period, versions in sorted(groups.items()):
        if len({values for values, _ in versions}) != 1:
            ambiguous.append(period)
            continue
        accepted[period] = {
            "values": dict(zip(consumed, versions[0][0])),
            "announcement_dates": sorted({d.isoformat() for _, dates in versions for d in dates}),
            "equivalent_consumed_revisions": len(versions),
        }
    return accepted, {"received_rows": len(rows), "page_limit_reached": len(rows) == LIMIT,
                      "excluded": excluded, "ambiguous_periods": ambiguous}


def analyze(payload):
    if not isinstance(payload, dict):
        raise ValueError("invalid_fixture")
    observed = datetime.fromisoformat(payload["retrieved_at"])
    if observed.tzinfo is None:
        raise ValueError("timezone_required")
    instruments = payload["instruments"]
    if not isinstance(instruments, dict) or not 1 <= len(instruments) <= 2:
        raise ValueError("instrument_budget")
    report = {
        "protocol": "cashflow-quality-current-v1", "retrieved_at": observed.isoformat(),
        "source": SOURCE, "decision_weight": False, "activation_allowed": False,
        "temporal_semantics": "current_observation_not_historical_pit",
        "limitations": ["At most two instruments and twelve rows per API; coverage incomplete.",
                        "Only consolidated cumulative report_type=1; no quarter conversion or TTM.",
                        "Company type 1 only; financial institutions excluded.",
                        "No rankings, sector comparability, performance or forward evidence established."],
        "instruments": {},
    }
    for symbol, tables in sorted(instruments.items()):
        if not re.fullmatch(r"(?:[03]\d{5}\.SZ|6\d{5}\.SH|(?:[48]\d{5}|92\d{4})\.BJ)", symbol):
            raise ValueError("invalid_symbol")
        if not isinstance(tables, dict):
            raise ValueError("invalid_tables")
        normalized, coverage = {}, {}
        for api in ("cashflow", "income"):
            normalized[api], coverage[api] = normalize(
                tables[api], symbol, api, observed.astimezone(ZoneInfo("Asia/Shanghai")).date())
        rows = []
        for period in sorted(normalized["cashflow"].keys() & normalized["income"].keys()):
            cash, inc = normalized["cashflow"][period], normalized["income"][period]
            values = {**cash["values"], **inc["values"]}
            ratios, reasons = {}, {}
            for name, denominator in (("operating_cashflow_to_netprofit", "n_income"),
                                      ("operating_cashflow_to_total_revenue", "total_revenue")):
                numerator, denom = values["n_cashflow_act"], values[denominator]
                reason = ("missing_or_nonfinite_input" if numerator is None or denom is None else
                          "nonpositive_denominator" if denom <= 0 else None)
                ratios[name] = None if reason else str(numerator / denom)
                if reason:
                    reasons[name] = reason
            rows.append({"report_period": period, "report_type": "1", "scope": "consolidated",
                         "period_basis": "year_to_date", "values": {
                             k: None if v is None else str(v) for k, v in values.items()},
                         "announcement_dates": {"cashflow": cash["announcement_dates"],
                                                "income": inc["announcement_dates"]},
                         "ratios": ratios, "ratio_exclusions": reasons})
        report["instruments"][symbol] = {
            "status": "observed" if rows else "no_matched_periods", "rows": rows,
            "coverage": coverage,
            "unmatched_periods": sorted(normalized["cashflow"].keys() ^ normalized["income"].keys()),
        }
    report["input_digest"] = digest(payload)
    report["result_digest"] = digest(report)
    return report


def fetch(client, symbols, observed):
    if not 1 <= len(symbols) <= 2 or len(set(symbols)) != len(symbols):
        raise ValueError("instrument_budget")
    # Validate identifiers before any request.
    for symbol in symbols:
        analyze({"retrieved_at": observed.isoformat(),
                 "instruments": {symbol: {"cashflow": [], "income": []}}})
    tables = {}
    for symbol in symbols:
        tables[symbol] = {}
        for api in ("cashflow", "income"):
            tables[symbol][api] = list(client.query(
                api, ts_code=symbol, report_type="1", limit=LIMIT).rows)
    return {"retrieved_at": observed.isoformat(), "instruments": tables}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path)
    source.add_argument("--live", action="store_true", help="Explicitly permit bounded research HTTP reads")
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.fixture:
            payload = json.loads(args.fixture.read_text())
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
            from qagent.providers.tushare_relay import TushareRelayClient
            client = TushareRelayClient(os.environ.get("QAGENT_TUSHARE_RELAY_KEY", ""),
                                        timeout_seconds=5, retries=0)
            payload = fetch(client, args.symbol, datetime.now(ZoneInfo("Asia/Shanghai")))
        report = analyze(payload)
        report["input_mode"] = "offline_fixture" if args.fixture else "live_research"
        report["implementation_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
        report.pop("result_digest")
        report["result_digest"] = digest(report)
        encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            publish(args.output, encoded)
        else:
            print(encoded, end="")
    except Exception as exc:
        # Never stringify exceptions or echo source rows, credential values or remote text.
        kind = getattr(exc, "kind", None)
        error = kind if isinstance(kind, str) and kind in SAFE_ERRORS else "cashflow_research_failed"
        print(json.dumps({"status": "error", "error": error,
                          "decision_weight": False, "activation_allowed": False}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
