#!/usr/bin/env python3
"""Bounded daily research batch through the local system API; no account writes."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
from hashlib import sha256
import json
import math
import os
import stat
from pathlib import Path
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from collect_documented_research import collect, local_origin
from rank_g2_consensus import publish
from research_financial_enrichment import APIS, analyze, digest, request_params, validate_header
from rank_financial_candidate import rank_candidate

BATCH_APIS = (*APIS, "moneyflow", "fina_indicator", "balancesheet")
FINANCIAL_APIS = (*APIS, "fina_indicator", "balancesheet")


def batch_params(api, symbol, period, trade_date):
    if api in APIS:
        return request_params(api, symbol, period, trade_date)
    if api == "moneyflow":
        return {"ts_code": symbol, "trade_date": trade_date, "limit": 12}
    params = {"ts_code": symbol, "period": period, "limit": 12}
    if api == "balancesheet":
        params["report_type"] = "1"
    return params


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


@contextmanager
def batch_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / ".daily-research.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("invalid_lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def run_batch(symbols, period, trade_date, *, source="datahubco",
              base_url="http://127.0.0.1:8000", budget_seconds=600, query=collect):
    local_origin(base_url)
    if (not 1 <= len(symbols) <= 20 or len(set(symbols)) != len(symbols)
            or isinstance(budget_seconds, bool) or not math.isfinite(budget_seconds)
            or not 70 <= budget_seconds <= 1800):
        raise ValueError("invalid_budget")
    started_at, started = now(), time.monotonic()
    for symbol in symbols:
        validate_header({"source": source, "period": period, "trade_date": trade_date,
                         "retrieved_at": started_at, "instruments": {symbol: {}}})
    evidence, sections = {}, {}
    for symbol in symbols:
        evidence[symbol], sections[symbol] = {}, {}
        for api in BATCH_APIS:
            params = batch_params(api, symbol, period, trade_date)
            limit = params.pop("limit")
            request = {"source": source, "api": api, "params": params,
                       "limit": limit, "offset": 0, "fields": None}
            if budget_seconds - (time.monotonic() - started) < 70:
                response = {"status": "error", "error": "batch_budget_exhausted", "rows": []}
            else:
                try:
                    response = query(base_url, **request)
                    if (response.get("status") not in {"observed", "no_rows", "error"}
                            or response.get("source") != source
                            or response.get("decision_weight") is not False
                            or response.get("activation_allowed") is not False
                            or not isinstance(response.get("rows"), list)):
                        raise ValueError("invalid_response")
                except Exception:
                    response = {"status": "error", "error": "system_request_failed", "rows": []}
            classification = response["status"]
            if api in {"moneyflow", "fina_indicator", "balancesheet"} and response["status"] == "observed" and (
                    not response["rows"] or len(response["rows"]) > limit or any(
                        not isinstance(row, dict) or row.get("ts_code") != symbol
                        or row.get("trade_date" if api == "moneyflow" else "end_date") != (
                            trade_date if api == "moneyflow" else period) for row in response["rows"])):
                classification = "invalid_data"
            evidence[symbol][api] = {"request": request, "received_at": now(), "response": response,
                                     "classification": classification,
                                     "coverage": {"received_rows": len(response["rows"]),
                                                  "page_limit_reached": len(response["rows"]) == limit}}
            if api in FINANCIAL_APIS:
                sections[symbol][api] = {"status": response["status"], "rows": response["rows"]}
                if response["status"] == "error":
                    sections[symbol][api]["error"] = response.get("error", "section_failed")
    finished_at = now()
    reports = [analyze({"analysis_version": 2, "source": source, "period": period, "trade_date": trade_date,
                        "retrieved_at": finished_at, "instruments": {symbol: sections[symbol]}})
               for symbol in symbols]
    try:
        candidate = rank_candidate(reports, symbols, top_k=min(5, len(symbols)))
    except Exception:
        candidate = {"status": "error", "error": "candidate_failed",
                     "decision_weight": False, "activation_allowed": False}
    report = {"protocol": "daily-documented-research-v2", "source": source,
              "period": period, "trade_date": trade_date, "symbols": symbols,
              "universe": {"kind": "explicit_observation_order", "symbols": list(symbols),
                           "digest": digest({"symbols": list(symbols)}),
                           "production_baseline_verified": False},
              "started_at": started_at, "finished_at": finished_at,
              "budget_seconds": budget_seconds, "system_evidence": evidence,
              "enrichment_reports": reports, "decision_weight": False, "activation_allowed": False,
              "financial_candidate": candidate,
              "analysis_coverage": {
                  "complete_reports": sum(r["status"] == "observed" for r in reports),
                  "incomplete_reports": sum(r["status"] != "observed" for r in reports),
                  "incomplete_instruments": [symbol for symbol, r in zip(symbols, reports)
                                             if r["status"] != "observed"],
                  "candidate_exclusions": candidate.get("coverage", {}).get("excluded", []),
              },
              "status_semantics": "observed means all source sections observed and candidate ranked; analysis exclusions remain explicit",
              "status": "observed" if candidate["status"] == "ranked" and all(
                  e["classification"] == "observed" for apis in evidence.values() for e in apis.values()) else "incomplete",
              "limitations": ["At most 20 explicitly supplied stocks and seven APIs; no full-market coverage claim.",
                              "Current observation only; no historical PIT or production ranking effect.",
                              "Derived observations use collection completion, never pretend availability before fetch.",
                              "Budget is a request-start budget reserving 70 seconds per system call; no retries."]}
    report["implementation_sha256"] = {name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("collect_daily_documented_research.py", "collect_documented_research.py",
                     "rank_financial_candidate.py", "research_financial_enrichment.py", "research_cashflow_quality.py")}
    report["result_digest"] = digest(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    universe = parser.add_mutually_exclusive_group(required=True)
    universe.add_argument("--symbol", action="append")
    universe.add_argument("--symbols-file", type=Path, help="Explicit ordered JSON list, at most 20 stock symbols")
    parser.add_argument("--period", required=True)
    dates = parser.add_mutually_exclusive_group(required=True)
    dates.add_argument("--trade-date")
    dates.add_argument("--today-close", "--today-afterclose", action="store_true",
                       help="Use Shanghai calendar date after 15:00; holidays remain real no_rows")
    parser.add_argument("--source", choices=("datahubco", "promax"), default="datahubco")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--budget-seconds", type=float, default=600)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        symbols = json.loads(args.symbols_file.read_text()) if args.symbols_file else args.symbol
        if not isinstance(symbols, list) or any(not isinstance(s, str) for s in symbols):
            raise ValueError("invalid_symbols")
        trade_date = args.trade_date
        if args.today_close:
            local = datetime.now(ZoneInfo("Asia/Shanghai"))
            if local.hour < 15:
                raise ValueError("session_not_closed")
            trade_date = local.strftime("%Y%m%d")
        with batch_lock(args.output_dir):
            report = run_batch(symbols, args.period, trade_date, source=args.source,
                               base_url=args.base_url, budget_seconds=args.budget_seconds)
            filename = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex + ".json"
            output = args.output_dir / filename
            publish(output, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            output.chmod(0o400)
        print(json.dumps({"status": report["status"], "output": str(output),
                          "result_digest": report["result_digest"]}))
        return 0 if report["status"] == "observed" else 1
    except BlockingIOError:
        print(json.dumps({"status": "skipped", "reason": "batch_locked"}))
        return 0
    except Exception:
        print(json.dumps({"status": "error", "error": "daily_documented_research_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
