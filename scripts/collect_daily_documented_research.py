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
import urllib.parse
import urllib.request
from uuid import uuid4
from zoneinfo import ZoneInfo

from collect_documented_research import NoRedirect, collect, local_origin
from rank_g2_consensus import publish
from research_financial_enrichment import APIS, analyze, digest, request_params, validate_header
from rank_financial_candidate import rank_candidate

BATCH_APIS = (*APIS, "moneyflow", "fina_indicator", "balancesheet")
FINANCIAL_APIS = (*APIS, "fina_indicator", "balancesheet")
CANDIDATE_POOL_PATH = "/api/paper-trades/candidate-pool"
CANDIDATE_POOL_REQUEST_LIMIT = 100
CANDIDATE_POOL_SELECTED_LIMIT = 20
FUND_ASSET_TYPES = frozenset({"etf", "fund", "index_fund"})


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


def collect_candidate_pool(base_url, *, opener=None):
    """Read the existing paper candidate pool without provider credentials or redirects."""
    origin = local_origin(base_url)
    query = urllib.parse.urlencode({"provider": "free", "include_etfs": "false",
                                    "limit": CANDIDATE_POOL_REQUEST_LIMIT})
    request = urllib.request.Request(f"{origin}{CANDIDATE_POOL_PATH}?{query}", method="GET")
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=70) as response:
        if response.status != 200:
            raise ValueError("candidate_pool_request_failed")
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("candidate_pool_response_limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("invalid_candidate_pool")
    return payload


def candidate_pool_universe(payload, expected_trade_date):
    """Fail closed and convert one current A-share pool response to ordered Tushare IDs."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("invalid_candidate_pool")
    items = payload["items"]
    summary, health = payload.get("summary"), payload.get("data_health")
    if not isinstance(summary, dict) or not isinstance(health, dict):
        raise ValueError("invalid_candidate_pool_evidence")
    try:
        expected = datetime.strptime(expected_trade_date, "%Y%m%d").date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_expected_trade_date") from exc
    required_health = {
        "paper_candidate_pool_endpoint": "true",
        "paper_candidate_pool_limit": str(CANDIDATE_POOL_REQUEST_LIMIT),
        "paper_candidate_freshness_gate": "fresh",
        "paper_candidate_expected_signal_date": expected,
        "paper_candidate_signal_date_mismatch": "0",
    }
    if any(health.get(key) != value for key, value in required_health.items()):
        raise ValueError("invalid_candidate_pool_health")
    if (type(summary.get("shown_candidates")) is not int
            or summary["shown_candidates"] != len(items)
            or type(summary.get("total_candidates")) is not int
            or summary["total_candidates"] < len(items)
            or health.get("paper_candidate_pool_total") != str(summary["total_candidates"])):
        raise ValueError("invalid_candidate_pool_summary")
    if not 1 <= len(items) <= CANDIDATE_POOL_REQUEST_LIMIT:
        raise ValueError("invalid_candidate_pool_size")

    stock_items, stock_symbols, excluded_items = [], set(), []
    for position, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("invalid_candidate_pool_item")
        instrument_id = item.get("instrument_id")
        if not isinstance(instrument_id, str) or not instrument_id.startswith("CN:"):
            raise ValueError("invalid_candidate_pool_instrument")
        if item.get("signal_date") != expected or item.get("signal_date_fresh") is not True:
            raise ValueError("invalid_candidate_pool_item_evidence")
        asset_type = item.get("asset_type")
        if asset_type not in {"stock", *FUND_ASSET_TYPES}:
            raise ValueError("invalid_candidate_pool_asset_type")
        market_id = instrument_id.removeprefix("CN:")
        parts = market_id.split(".")
        ticker = parts[0]
        exchange = None
        if asset_type == "stock" and len(ticker) == 6 and ticker.isdigit():
            if ticker.startswith(("000", "001", "002", "003", "300", "301")):
                exchange = "SZ"
            elif ticker.startswith(("600", "601", "603", "605", "688", "689")):
                exchange = "SH"
            elif ticker.startswith(("430", "82", "83", "87", "88", "920")):
                exchange = "BJ"
        elif asset_type in FUND_ASSET_TYPES and len(ticker) == 6 and ticker.isdigit():
            if ticker.startswith(("15", "16")):
                exchange = "SZ"
            elif ticker.startswith(("50", "51", "52", "56", "58")):
                exchange = "SH"
        if exchange is None or len(parts) > 2 or (len(parts) == 2 and parts[1] != exchange):
            raise ValueError("invalid_candidate_pool_instrument")
        symbol = f"{ticker}.{exchange}"
        if asset_type in FUND_ASSET_TYPES:
            excluded_items.append({"source_position": position, "instrument_id": instrument_id,
                                   "asset_type": asset_type, "symbol": symbol,
                                   "reason": "explicit_fund_asset_type"})
            continue
        if symbol in stock_symbols:
            raise ValueError("duplicate_candidate_pool_instrument")
        stock_symbols.add(symbol)
        stock_items.append({"source_position": position, "instrument_id": instrument_id,
                            "asset_type": asset_type, "symbol": symbol})

    if len(stock_items) < 5:
        raise ValueError("invalid_candidate_pool_stock_count")
    selected = stock_items[:CANDIDATE_POOL_SELECTED_LIMIT]
    for item in stock_items[CANDIDATE_POOL_SELECTED_LIMIT:]:
        excluded_items.append({**item, "reason": "selected_limit"})
    excluded_items.sort(key=lambda item: item["source_position"])
    symbols = [item["symbol"] for item in selected]
    selection = [
        {"position": position, **item}
        for position, item in enumerate(selected, 1)
    ]
    excluded_reasons = {
        reason: sum(item["reason"] == reason for item in excluded_items)
        for reason in sorted({item["reason"] for item in excluded_items})
    }

    source_digest = digest(payload)
    universe = {
        "kind": "paper_candidate_pool_order",
        "source": "paper_candidate_pool",
        "endpoint": CANDIDATE_POOL_PATH,
        "provider": "free",
        "include_etfs": False,
        "requested_pool_limit": CANDIDATE_POOL_REQUEST_LIMIT,
        "selected_limit": CANDIDATE_POOL_SELECTED_LIMIT,
        "selected_count": len(symbols),
        "eligible_stock_count": len(stock_items),
        "source_shown_count": summary["shown_candidates"],
        "source_total_count": summary["total_candidates"],
        "expected_signal_date": expected,
        "symbols": symbols,
        "selection_order": selection,
        "excluded_items": excluded_items,
        "excluded_reasons": excluded_reasons,
        "excluded_count": len(excluded_items),
        "excluded_digest": digest(excluded_items),
        "response_summary": summary,
        "response_data_health": health,
        "response_digest": source_digest,
        "digest": digest({"symbols": symbols, "response_digest": source_digest}),
        "production_baseline_verified": False,
        "limitations": [
            "Uses only the existing read-only paper candidate pool and preserves its returned order.",
            "Reads at most 100 source items and selects the first 20 validated A-share stocks.",
            "Explicit fund types are evidence-backed exclusions; other identity errors fail closed.",
            "This replaces the fixed research observation set only.",
            "Candidate-pool admission and financial ranking are distinct; no trading authority is added.",
        ],
    }
    return symbols, universe


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
              base_url="http://127.0.0.1:8000", budget_seconds=600, query=collect,
              universe=None):
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
              "universe": universe or {
                  "kind": "explicit_observation_order", "symbols": list(symbols),
                  "digest": digest({"symbols": list(symbols)}),
                  "production_baseline_verified": False,
              },
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
              "limitations": ["At most 20 A-share stocks and seven APIs; no full-market coverage claim.",
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
    universe.add_argument("--candidate-pool", action="store_true",
                          help="Use 5-20 current A-share stocks from the existing local paper candidate pool")
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
        trade_date = args.trade_date
        if args.today_close:
            local = datetime.now(ZoneInfo("Asia/Shanghai"))
            if local.hour < 15:
                raise ValueError("session_not_closed")
            trade_date = local.strftime("%Y%m%d")
        universe_evidence = None
        if args.candidate_pool:
            payload = collect_candidate_pool(args.base_url)
            symbols, universe_evidence = candidate_pool_universe(payload, trade_date)
        else:
            symbols = json.loads(args.symbols_file.read_text()) if args.symbols_file else args.symbol
        if not isinstance(symbols, list) or any(not isinstance(s, str) for s in symbols):
            raise ValueError("invalid_symbols")
        with batch_lock(args.output_dir):
            report = run_batch(symbols, args.period, trade_date, source=args.source,
                               base_url=args.base_url, budget_seconds=args.budget_seconds,
                               universe=universe_evidence)
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
