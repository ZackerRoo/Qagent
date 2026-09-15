#!/usr/bin/env python3
"""Seal same-day financial rule signals, then evaluate from read-only adjusted bars."""
import argparse
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from compare_g2_selections import validate_signal as validate_g2
from rank_financial_candidate import POLICY_V2, rank_candidate, verify_digest
from rank_g2_consensus import publish
from research_financial_enrichment import digest
from qagent.market.calendars import trading_sessions_in_range
from qagent.research.factor_shadow_outcomes import (
    FACTOR_SHADOW_HORIZONS, _adjusted_price, _load_cached_bars, _return_pct,
    factor_shadow_outcome_dates,
)
import qagent.research.factor_shadow_outcomes as factor_shadow_runtime
from qagent.storage.market_cache import MarketDataCacheRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")
POLICY = {
    "protocol": "financial-rule-forward-v1", "ranking_policy": POLICY_V2,
    "horizons": list(FACTOR_SHADOW_HORIZONS), "benchmark_id": "CN:000300.IDX",
    "round_trip_cost_bps": 10, "top_k": 5,
    "signal_timing": "same_exchange_day_collection_and_seal_at_or_after_1530_Shanghai",
    "baseline": "same_day_archived_G2_full_features_restricted_to_all_financial_eligible_ids",
    "missing": "no_imputation_no_price_based_reselection_no_backfill",
    "decision_weight": False, "activation_allowed": False,
}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _instrument_matches_symbol(instrument_id, symbol, asset_type):
    if not isinstance(instrument_id, str) or not isinstance(symbol, str):
        return False
    ticker, separator, exchange = symbol.partition(".")
    if not separator or len(ticker) != 6 or not ticker.isdigit():
        return False
    if asset_type == "stock":
        expected = ("SZ" if ticker.startswith(("000", "001", "002", "003", "300", "301"))
                    else "SH" if ticker.startswith(("600", "601", "603", "605", "688", "689"))
                    else "BJ" if ticker.startswith(("430", "82", "83", "87", "88", "920"))
                    else None)
    else:
        expected = ("SZ" if ticker.startswith(("15", "16"))
                    else "SH" if ticker.startswith(("50", "51", "52", "56", "58"))
                    else None)
    if exchange != expected:
        return False
    return instrument_id in {f"CN:{ticker}", f"CN:{ticker}.{exchange}"}


def validate_source_universe(universe, symbols, day):
    if not isinstance(universe, dict):
        raise ValueError("invalid_universe")
    if universe.get("kind") == "explicit_observation_order":
        # Preserve the original explicit-universe digest contract unchanged.
        if universe.get("digest") != digest({"symbols": symbols}):
            raise ValueError("universe_digest_mismatch")
        return
    if universe.get("kind") != "paper_candidate_pool_order":
        raise ValueError("unsupported_universe_kind")
    fixed = {
        "source": "paper_candidate_pool",
        "endpoint": "/api/paper-trades/candidate-pool",
        "provider": "free",
        "include_etfs": False,
        "requested_pool_limit": 100,
        "selected_limit": 20,
        "production_baseline_verified": False,
        "expected_signal_date": str(day),
    }
    if any(universe.get(key) != value for key, value in fixed.items()):
        raise ValueError("candidate_pool_universe_identity_mismatch")
    selected_count = universe.get("selected_count")
    eligible = universe.get("eligible_stock_count")
    shown = universe.get("source_shown_count")
    total = universe.get("source_total_count")
    if (type(selected_count) is not int or selected_count != len(symbols)
            or not 5 <= selected_count <= 20 or universe.get("symbols") != symbols
            or type(eligible) is not int or eligible < selected_count
            or selected_count != min(20, eligible)
            or type(shown) is not int or not selected_count <= shown <= 100
            or type(total) is not int or total < shown):
        raise ValueError("candidate_pool_universe_count_mismatch")
    response_digest = universe.get("response_digest")
    if not isinstance(response_digest, str) or HEX64.fullmatch(response_digest) is None:
        raise ValueError("candidate_pool_response_digest_invalid")
    if universe.get("digest") != digest({"symbols": symbols, "response_digest": response_digest}):
        raise ValueError("universe_digest_mismatch")

    selection = universe.get("selection_order")
    if not isinstance(selection, list) or len(selection) != selected_count:
        raise ValueError("candidate_pool_selection_invalid")
    selected_positions = []
    for position, (item, symbol) in enumerate(zip(selection, symbols), 1):
        if (not isinstance(item, dict)
                or item.get("position") != position
                or item.get("asset_type") != "stock"
                or item.get("symbol") != symbol
                or not _instrument_matches_symbol(item.get("instrument_id"), symbol, "stock")
                or type(item.get("source_position")) is not int):
            raise ValueError("candidate_pool_selection_invalid")
        selected_positions.append(item["source_position"])
    if selected_positions != sorted(selected_positions) or len(set(selected_positions)) != len(selection):
        raise ValueError("candidate_pool_selection_order_invalid")

    excluded = universe.get("excluded_items")
    reasons = universe.get("excluded_reasons")
    excluded_count = universe.get("excluded_count")
    if (not isinstance(excluded, list) or type(excluded_count) is not int
            or excluded_count != len(excluded)
            or universe.get("excluded_digest") != digest(excluded)
            or not isinstance(reasons, dict)
            or set(reasons) - {"explicit_fund_asset_type", "selected_limit"}
            or any(type(value) is not int or value <= 0 for value in reasons.values())
            or sum(reasons.values()) != excluded_count):
        raise ValueError("candidate_pool_exclusions_invalid")
    excluded_positions, observed_reasons = [], {}
    for item in excluded:
        if not isinstance(item, dict) or type(item.get("source_position")) is not int:
            raise ValueError("candidate_pool_exclusions_invalid")
        reason = item.get("reason")
        asset_type = item.get("asset_type")
        symbol = item.get("symbol")
        if (reason == "explicit_fund_asset_type"
                and asset_type not in {"etf", "fund", "index_fund"}):
            raise ValueError("candidate_pool_exclusions_invalid")
        if reason == "selected_limit" and asset_type != "stock":
            raise ValueError("candidate_pool_exclusions_invalid")
        if reason not in {"explicit_fund_asset_type", "selected_limit"} or not _instrument_matches_symbol(
                item.get("instrument_id"), symbol, asset_type):
            raise ValueError("candidate_pool_exclusions_invalid")
        excluded_positions.append(item["source_position"])
        observed_reasons[reason] = observed_reasons.get(reason, 0) + 1
    if (excluded_positions != sorted(excluded_positions)
            or observed_reasons != reasons
            or sorted(selected_positions + excluded_positions) != list(range(1, shown + 1))
            or eligible != selected_count + reasons.get("selected_limit", 0)
            or shown != eligible + reasons.get("explicit_fund_asset_type", 0)
            or any(item["source_position"] <= selected_positions[-1]
                   for item in excluded if item["reason"] == "selected_limit")):
        raise ValueError("candidate_pool_exclusion_count_mismatch")

    summary, health = universe.get("response_summary"), universe.get("response_data_health")
    if (not isinstance(summary, dict) or summary.get("shown_candidates") != shown
            or summary.get("total_candidates") != total or not isinstance(health, dict)):
        raise ValueError("candidate_pool_response_summary_invalid")
    expected_health = {
        "paper_candidate_pool_endpoint": "true",
        "paper_candidate_pool_limit": "100",
        "paper_candidate_pool_total": str(total),
        "paper_candidate_freshness_gate": "fresh",
        "paper_candidate_expected_signal_date": str(day),
        "paper_candidate_signal_date_mismatch": "0",
    }
    if any(health.get(key) != value for key, value in expected_health.items()):
        raise ValueError("candidate_pool_response_health_invalid")


def evaluation_runtime_identity():
    backend_root = Path(factor_shadow_runtime.__file__).resolve().parents[2]
    return {
        "backend_root_resolved": str(backend_root),
        "evaluator_path_resolved": str(Path(__file__).resolve()),
        "evaluator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "factor_shadow_outcomes_path_resolved": str(Path(factor_shadow_runtime.__file__).resolve()),
        "factor_shadow_outcomes_sha256": sha256(
            Path(factor_shadow_runtime.__file__).read_bytes()).hexdigest(),
    }


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result.astimezone(SHANGHAI)


def after_close(value, day):
    result = timestamp(value)
    if result.date() != day or result.time() < time(15, 30):
        raise ValueError("not_contemporaneous_afterclose_signal")
    return result


def seal(document, baseline=None, *, now=None):
    """No prices or database are consulted when fixing the eligible set."""
    now = now or datetime.now(timezone.utc)
    verify_digest(document)
    if document.get("protocol") != "daily-documented-research-v2":
        raise ValueError("requires_daily_v2")
    symbols = document.get("symbols")
    if not isinstance(symbols, list) or not 5 <= len(symbols) <= 20:
        raise ValueError("bounded_observation_universe_required")
    day = datetime.strptime(document["trade_date"], "%Y%m%d").date()
    validate_source_universe(document.get("universe"), symbols, day)
    if not trading_sessions_in_range(day, day):
        raise ValueError("not_exchange_session")
    sealed = after_close(now.isoformat(), day)
    started = after_close(document["started_at"], day)
    finished = after_close(document["finished_at"], day)
    if not started <= finished <= sealed:
        raise ValueError("capture_time_order")
    for report in document["enrichment_reports"]:
        if timestamp(report["retrieved_at"]) != finished:
            raise ValueError("enrichment_capture_mismatch")
        for symbol, raw in report["raw_evidence"]["instruments"].items():
            for api, item in raw.items():
                evidence = document["system_evidence"][symbol][api]
                received = after_close(evidence["received_at"], day)
                if not started <= received <= finished:
                    raise ValueError("section_capture_time_order")
                response = evidence["response"]
                if response.get("fetched_at"):
                    fetched = after_close(response["fetched_at"], day)
                    if not started <= fetched <= received:
                        raise ValueError("system_fetch_time_order")
                if response["rows"] != item["rows"] or response["status"] != item["status"]:
                    raise ValueError("raw_system_evidence_mismatch")
    ranked = rank_candidate([document], document["symbols"], top_k=5)
    declared = document["financial_candidate"]
    verify_digest(declared)
    if ranked["policy"] != POLICY_V2 or any(declared.get(k) != ranked[k] for k in (
        "policy", "policy_digest", "rankings", "coverage", "same_cohort_baseline_order"
    )):
        raise ValueError("candidate_replay_mismatch")
    if ranked["coverage"]["eligible_count"] < 5:
        raise ValueError("fewer_than_five_eligible_stocks")
    rankings = [{"instrument_id": "CN:" + row["stock_id"].split(".")[0],
                 "rank": row["rank"], "score_exact": row["score_exact"]}
                for row in ranked["rankings"]]
    ids = {r["instrument_id"] for r in rankings}
    if len(ids) != len(rankings):
        raise ValueError("instrument_mapping_collision")
    baseline_order, reasons = None, ["baseline_not_supplied"]
    if baseline is not None:
        try:
            rows = validate_g2(baseline)
            if baseline["signal_date"] != str(day):
                raise ValueError("baseline_date_mismatch")
            bs = after_close(baseline["collection_started_at_utc"], day)
            be = after_close(baseline["collected_at_utc"], day)
            if not bs <= be <= sealed:
                raise ValueError("baseline_time_order")
            if not ids <= {r["instrument_id"] for r in rows}:
                raise ValueError("baseline_cohort_incomplete")
            baseline_order = [r["instrument_id"] for r in sorted(
                rows, key=lambda r: r["full_features"]["rank"]) if r["instrument_id"] in ids]
            reasons = []
        except (ValueError, KeyError, TypeError):
            reasons = ["baseline_invalid_date_cohort_or_archive"]
    result = {
        "protocol": POLICY["protocol"], "policy": POLICY, "policy_digest": digest(POLICY),
        "signal_date": str(day), "sealed_at": sealed.isoformat(),
        "source_result_digest": document["result_digest"], "source": document,
        "baseline_source": baseline, "baseline_order": baseline_order,
        "baseline_status": "available" if baseline_order else "baseline_unavailable",
        "baseline_reasons": reasons, "rankings": rankings, "coverage": ranked["coverage"],
        "baseline_kind": "G2_full_features_research_not_production",
        "status": "sealed", "decision_weight": False, "activation_allowed": False,
        "implementation_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    result["result_digest"] = digest(result)
    return result


def validate_archive(signal):
    verify_digest(signal)
    replay = seal(signal["source"], signal["baseline_source"], now=timestamp(signal["sealed_at"]))
    # A new implementation can audit an old archive, but cannot alter its identity/content.
    for key in replay:
        if key not in {"result_digest", "implementation_sha256"} and replay[key] != signal.get(key):
            raise ValueError("signal_replay_mismatch")


def evaluate(signal, db, *, provider_mode="free", as_of=None):
    validate_archive(signal)
    as_of = as_of or datetime.now(timezone.utc)
    current = timestamp(as_of.isoformat())
    if current < timestamp(signal["sealed_at"]):
        raise ValueError("evaluation_before_signal")
    path = Path(db).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    engine = create_engine("sqlite://", creator=lambda: connection)
    reader = engine.connect()
    reader.exec_driver_sql("BEGIN")
    ids = [r["instrument_id"] for r in signal["rankings"]]
    day = date.fromisoformat(signal["signal_date"])
    candidate = ids[:POLICY["top_k"]]
    baseline = (signal["baseline_order"] or [])[:POLICY["top_k"]]
    horizons = []
    try:
        cache = MarketDataCacheRepository(sessionmaker(bind=reader))
        for horizon in FACTOR_SHADOW_HORIZONS:
            entry, end = factor_shadow_outcome_dates(day, horizon)
            item = {"horizon_sessions": horizon, "entry_date": str(entry), "outcome_date": str(end),
                    "expected": len(ids), "completed": 0, "labels": [],
                    "candidate_selected": candidate, "baseline_selected": baseline,
                    "baseline_status": signal["baseline_status"], "paired_complete": False,
                    "candidate_net_excess_pct": None, "baseline_net_excess_pct": None,
                    "lift_pct": None}
            if current.date() < end or (current.date() == end and current.time() < time(15, 30)):
                item["status"] = "waiting_for_maturity"
                horizons.append(item)
                continue
            bars = _load_cached_bars(cache, provider_mode, [*ids, POLICY["benchmark_id"]], entry, end)
            needed = bars[bars["trade_date"].isin([entry, end])]
            # Retain normalized source values for deterministic replay of the cached snapshot.
            records = json.loads(needed.to_json(orient="records", date_format="iso", double_precision=15))
            item["price_evidence"] = records
            item["price_evidence_digest"] = digest(records)
            def price(key, when, field):
                value = _adjusted_price(bars, key, when, field)
                rows = bars[(bars["instrument_id"] == key) & (bars["trade_date"] == when)]
                reason = None if value is not None else "missing_price_row" if rows.empty else "invalid_adjusted_price"
                return value, reason
            be, ber = price(POLICY["benchmark_id"], entry, "adjusted_open")
            bx, bxr = price(POLICY["benchmark_id"], end, "adjusted_close")
            item["benchmark_reasons"] = sorted({r for r in (ber, bxr) if r})
            for key in ids:
                pe, er = price(key, entry, "adjusted_open")
                px, xr = price(key, end, "adjusted_close")
                reasons = sorted({r for r in (er, xr) if r})
                if item["benchmark_reasons"]:
                    reasons.append("benchmark_unavailable")
                label = {"instrument_id": key, "status": "unresolved" if reasons else "computed",
                         "reasons": reasons, "instrument_return_pct": None,
                         "benchmark_return_pct": None, "net_excess_return_pct": None,
                         "entry_adjusted_open": pe, "exit_adjusted_close": px,
                         "benchmark_entry_adjusted_open": be, "benchmark_exit_adjusted_close": bx}
                if not reasons:
                    ir, br = _return_pct(pe, px), _return_pct(be, bx)
                    label.update(instrument_return_pct=ir, benchmark_return_pct=br,
                                 net_excess_return_pct=ir-br-POLICY["round_trip_cost_bps"]/100)
                item["labels"].append(label)
            known = {r["instrument_id"]: r["net_excess_return_pct"] for r in item["labels"]
                     if r["status"] == "computed"}
            item.update(completed=len(known), status="complete" if len(known) == len(ids) else "partial")
            item["candidate_completed"] = sum(k in known for k in candidate)
            item["baseline_completed"] = sum(k in known for k in baseline)
            if all(k in known for k in candidate):
                item["candidate_net_excess_pct"] = sum(known[k] for k in candidate)/len(candidate)
            if baseline and all(k in known for k in baseline):
                item["baseline_net_excess_pct"] = sum(known[k] for k in baseline)/len(baseline)
            if item["candidate_net_excess_pct"] is not None and item["baseline_net_excess_pct"] is not None:
                item.update(paired_complete=True, lift_pct=item["candidate_net_excess_pct"]-item["baseline_net_excess_pct"])
            horizons.append(item)
    finally:
        reader.close()
        engine.dispose()
        connection.close()
    result = {"protocol": "financial-rule-forward-evaluation-v1", "signal_digest": signal["result_digest"],
              "policy_digest": signal["policy_digest"], "signal_date": str(day), "as_of": current.isoformat(),
              "provider_mode": provider_mode, "horizons": horizons,
              "runtime_identity": evaluation_runtime_identity(),
              "decision_weight": False, "activation_allowed": False,
              "limitations": ["Research fixed-horizon labels, not executable fills or account returns.",
                              "G2 baseline is a research model, never the supplied observation order.",
                              "Digests prove integrity, not timestamp authenticity or historical PIT.",
                              "No promotion; overlapping daily windows are not independent samples."]}
    result["result_digest"] = digest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("seal")
    capture.add_argument("--daily", type=Path, required=True)
    capture.add_argument("--baseline-g2", type=Path)
    capture.add_argument("--output-dir", type=Path, required=True)
    evaluation = sub.add_parser("evaluate")
    evaluation.add_argument("--signal", type=Path, required=True)
    evaluation.add_argument("--db", type=Path, required=True)
    evaluation.add_argument("--provider-mode", default="free")
    evaluation.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "seal":
            result = seal(json.loads(args.daily.read_text()),
                          json.loads(args.baseline_g2.read_text()) if args.baseline_g2 else None)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            target = args.output_dir / (result["signal_date"] + ".json")
        else:
            result = evaluate(json.loads(args.signal.read_text()), args.db, provider_mode=args.provider_mode)
            target = args.output
        publish(target, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "blocked", "error": type(exc).__name__}), file=sys.stderr)
        return 2
    print(json.dumps({"status": "archived", "result_digest": result["result_digest"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
