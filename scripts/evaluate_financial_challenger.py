#!/usr/bin/env python3
"""Seal same-day financial rule signals, then evaluate from read-only adjusted bars."""
import argparse
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
import math
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
from research_cashflow_quality import number
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
POLICY_MATCHED_CONTROL = {
    **POLICY,
    "protocol": "financial-rule-forward-v2",
    "control": (
        "one same-industry control per candidate; prefer non-candidates, then absolute log total_mv "
        "distance, G2 full_features rank, instrument_id; controls are unique across pairs"
    ),
    "control_minimum_outside_financial_top5": 2,
    "total_mv_semantics": "provider_source_unit_unchanged_current_observation_not_historical_pit",
    "paired_lift": "candidate_top5_vs_matched_control_top5_only_when_both_price_complete_5_of_5",
}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
PROSPECTIVE_CONTRACT = "financial-daily-frozen-industry-v1"
POLICY_DAILY = {**POLICY_MATCHED_CONTROL, "protocol": "financial-rule-forward-v3",
                "baseline": "isolated_daily_frozen_full_features_ranks",
                "industry": "single_provider_stock_basic_current_observation"}
POLICY_DAILY_V4 = {
    **POLICY_DAILY,
    "protocol": "financial-rule-forward-v4",
    "control": (
        "one same-industry control per candidate; globally minimize candidate-controls, then "
        "total absolute log total_mv distance, G2 full_features rank, instrument_id; "
        "controls are unique across pairs"
    ),
}


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
        has_industry = "industry" in item
        has_exposure = "exposure_group" in item
        industry, exposure = item.get("industry"), item.get("exposure_group")
        if (has_industry != has_exposure
                or (has_industry
                    and not ((industry is None and exposure is None)
                             or (isinstance(industry, str) and industry == industry.strip() and industry
                                 and isinstance(exposure, str) and exposure == exposure.strip()
                                 and exposure == industry)))):
            raise ValueError("candidate_pool_selection_exposure_invalid")
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


def _extended_candidate_pool_universe(universe):
    """New collector artifacts carry explicit exposure keys; old v1 archives do not."""
    if not isinstance(universe, dict) or universe.get("kind") != "paper_candidate_pool_order":
        return False
    selection = universe.get("selection_order")
    return isinstance(selection, list) and bool(selection) and all(
        isinstance(item, dict) and "industry" in item and "exposure_group" in item
        for item in selection
    )


def _global_control_assignment(candidate, ids, metadata, g2_ranks):
    """Return the deterministic minimum-cost complete assignment, or ``None``.

    The candidate set is capped at five and the eligible set at twenty, so an
    exhaustive assignment is both simpler to audit and bounded (at most 20P5).
    Tuple ordering makes every tie deterministic without changing membership.
    """
    candidate_set = set(candidate)
    choices = []
    for candidate_id in candidate:
        candidate_mv = float(metadata[candidate_id]["total_mv"])
        alternatives = [key for key in ids if key != candidate_id
                        and metadata[key]["industry"] == metadata[candidate_id]["industry"]]
        if not alternatives:
            return None
        choices.append(sorted(
            ((key, key in candidate_set,
              abs(math.log(float(metadata[key]["total_mv"])) - math.log(candidate_mv)),
              g2_ranks[key]) for key in alternatives),
            key=lambda value: (value[1], value[2], value[3], value[0]),
        ))

    best = None

    def visit(position, used, selected, candidate_controls, distance, rank_sum):
        nonlocal best
        if position == len(candidate):
            # Preserve the existing preference order globally.  The ordered
            # control IDs resolve otherwise identical aggregate costs.
            value = (candidate_controls, distance, rank_sum,
                     tuple(item[0] for item in selected), tuple(selected))
            if best is None or value[:4] < best[:4]:
                best = value
            return
        for choice in choices[position]:
            control_id, is_candidate, log_distance, control_rank = choice
            if control_id in used:
                continue
            visit(position + 1, used | {control_id}, selected + [choice],
                  candidate_controls + int(is_candidate), distance + log_distance,
                  rank_sum + control_rank)

    visit(0, frozenset(), [], 0, 0.0, 0)
    return None if best is None else best[4]


def _matched_control_plan(document, rankings, baseline_rows, *, global_assignment=False):
    selection = document["universe"]["selection_order"]
    if document.get("prospective_contract") == PROSPECTIVE_CONTRACT:
        from financial_industry_evidence import validate_industry_evidence
        section = validate_industry_evidence(document["industry_evidence"], document["symbols"],
                                            document["trade_date"], source=document["source"])
        day = datetime.strptime(document["trade_date"], "%Y%m%d").date()
        for entry in section["raw_evidence"].values():
            received = after_close(entry["received_at"], day)
            if not timestamp(document["started_at"]) <= received <= timestamp(document["finished_at"]):
                raise ValueError("industry_capture_time_order")
            if entry["response"].get("fetched_at"):
                fetched = after_close(entry["response"]["fetched_at"], day)
                if not timestamp(document["started_at"]) <= fetched <= received:
                    raise ValueError("industry_capture_time_order")
        selection = section["rows"] if section["status"] == "available" else [
            {"symbol": symbol, "industry": None, "exposure_group": None}
            for symbol in document["symbols"]]
    metadata = {}
    reasons = set()
    for item in selection:
        industry, exposure = item.get("industry"), item.get("exposure_group")
        if not ((industry is None and exposure is None)
                or (isinstance(industry, str) and industry == industry.strip() and industry
                    and isinstance(exposure, str) and exposure == exposure.strip()
                    and exposure == industry)):
            raise ValueError("candidate_pool_exposure_invalid")
        key = "CN:" + item["symbol"].split(".")[0]
        metadata[key] = {"industry": industry, "exposure_group": exposure, "total_mv": None}
        if industry is None:
            reasons.add("industry_incomplete")

    for report in document["enrichment_reports"]:
        for symbol, details in report["instruments"].items():
            key = "CN:" + symbol.split(".")[0]
            if key not in metadata:
                continue
            value = (details.get("sections", {}).get("daily_basic", {}).get("derived", {})
                     .get("values", {}).get("total_mv"))
            parsed = number(value)
            if parsed is not None and parsed > 0:
                metadata[key]["total_mv"] = str(parsed)
            else:
                reasons.add("total_mv_incomplete")

    ids = [row["instrument_id"] for row in rankings]
    if any(key not in metadata for key in ids):
        reasons.add("universe_metadata_incomplete")
    g2_ranks = {row["instrument_id"]: row["full_features"]["rank"] for row in baseline_rows}
    if any(key not in g2_ranks for key in ids):
        reasons.add("g2_rank_incomplete")
    candidate = ids[:POLICY_MATCHED_CONTROL["top_k"]]
    pairs = []
    if not reasons:
        candidate_set = set(candidate)
        if global_assignment:
            assignments = _global_control_assignment(candidate, ids, metadata, g2_ranks)
            if assignments is None:
                reasons.add("same_industry_control_unavailable")
        else:
            used_controls = set()
            assignments = []
            for candidate_id in candidate:
                candidate_meta = metadata[candidate_id]
                alternatives = [key for key in ids if key != candidate_id and key not in used_controls
                                and metadata[key]["industry"] == candidate_meta["industry"]]
                if not alternatives:
                    reasons.add("same_industry_control_unavailable")
                    break
                candidate_mv = float(candidate_meta["total_mv"])
                control_id = min(
                    alternatives,
                    key=lambda key: (
                        key in candidate_set,
                        abs(math.log(float(metadata[key]["total_mv"])) - math.log(candidate_mv)),
                        g2_ranks[key],
                        key,
                    ),
                )
                used_controls.add(control_id)
                assignments.append((control_id, control_id in candidate_set,
                                    abs(math.log(float(metadata[control_id]["total_mv"]))
                                        - math.log(candidate_mv)), g2_ranks[control_id]))
        if not reasons:
            for candidate_id, (control_id, _, _, _) in zip(candidate, assignments):
                candidate_meta = metadata[candidate_id]
                control_meta = metadata[control_id]
                candidate_mv = float(candidate_meta["total_mv"])
                pair = {
                    "position": len(pairs) + 1,
                    "candidate_instrument_id": candidate_id,
                    "control_instrument_id": control_id,
                    "industry": candidate_meta["industry"],
                    "candidate_total_mv": candidate_meta["total_mv"],
                    "control_total_mv": control_meta["total_mv"],
                    "absolute_log_total_mv_distance": abs(
                        math.log(float(control_meta["total_mv"])) - math.log(candidate_mv)
                    ),
                    "control_is_candidate": control_id in candidate_set,
                    "evidence_complete": True,
                    "discriminative": control_id not in candidate_set,
                    "candidate_g2_rank": g2_ranks[candidate_id],
                    "control_g2_rank": g2_ranks[control_id],
                }
                pair["pair_digest"] = digest(pair)
                pairs.append(pair)
    if reasons:
        status, discriminative = "control_unavailable", False
        pairs = []
    elif sum(pair["control_instrument_id"] not in candidate_set for pair in pairs) < 2:
        status, discriminative = "control_not_discriminative", False
        reasons.add("fewer_than_two_controls_outside_financial_top5")
    else:
        status, discriminative = "available", True
    return {
        "control_status": status,
        "control_discriminative": discriminative,
        "control_reasons": sorted(reasons),
        "matched_control_pairs": pairs,
        "matched_control_pairs_digest": digest(pairs),
        "control_order": [pair["control_instrument_id"] for pair in pairs],
    }


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
    """Seal new evidence; prospective inputs use global matched-control v4."""
    return _seal(document, baseline, now=now)


def _seal(document, baseline=None, *, now=None, legacy_v1_fallback=False, archive_protocol=None):
    """No prices or database are consulted when fixing the eligible set."""
    now = now or datetime.now(timezone.utc)
    verify_digest(document)
    prospective = document.get("prospective_contract")
    if prospective is not None and prospective != PROSPECTIVE_CONTRACT:
        raise ValueError("unknown_prospective_contract")
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
    baseline_order, baseline_rows, reasons = None, None, ["baseline_not_supplied"]
    if baseline is not None:
        try:
            if prospective:
                from financial_daily_baseline import validate_archive as validate_daily_baseline
                rows = validate_daily_baseline(baseline, signal_date=day, eligible_ids=ids)
            else:
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
            baseline_rows = rows
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
    # Missing tie-break evidence makes a new matched-control signal unavailable;
    # it must not silently select the retired v1 performance protocol.
    if (_extended_candidate_pool_universe(document.get("universe"))
            and not (legacy_v1_fallback and baseline_rows is None)):
        result.update(
            protocol=POLICY_MATCHED_CONTROL["protocol"],
            policy=POLICY_MATCHED_CONTROL,
            policy_digest=digest(POLICY_MATCHED_CONTROL),
            **_matched_control_plan(
                document, rankings, baseline_rows or [],
                global_assignment=prospective and archive_protocol != POLICY_DAILY["protocol"],
            ),
        )
    if prospective:
        if not _extended_candidate_pool_universe(document.get("universe")):
            raise ValueError("prospective_candidate_pool_required")
        policy = POLICY_DAILY if archive_protocol == POLICY_DAILY["protocol"] else POLICY_DAILY_V4
        result.update(protocol=policy["protocol"], policy=policy,
                      policy_digest=digest(policy), prospective_contract=prospective,
                      baseline_kind="isolated_daily_frozen_full_features_research")
    result["result_digest"] = digest(result)
    return result


def validate_archive(signal):
    verify_digest(signal)
    # Prior releases sealed extended inputs without a baseline as v1. Replay
    # that exact historical behavior without exposing it to new seal callers.
    replay = _seal(signal["source"], signal["baseline_source"],
                   now=timestamp(signal["sealed_at"]),
                   legacy_v1_fallback=signal.get("protocol") == POLICY["protocol"],
                   archive_protocol=signal.get("protocol"))
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
    is_v2 = signal["protocol"] in {POLICY_MATCHED_CONTROL["protocol"], POLICY_DAILY["protocol"]}
    policy = POLICY_MATCHED_CONTROL if is_v2 else POLICY
    candidate = ids[:policy["top_k"]]
    baseline = (signal["baseline_order"] or [])[:policy["top_k"]]
    controls = signal.get("control_order", []) if is_v2 else []
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
            if is_v2:
                pair_results = []
                for pair in signal["matched_control_pairs"]:
                    pair_result = {
                        "position": pair["position"],
                        "candidate_instrument_id": pair["candidate_instrument_id"],
                        "control_instrument_id": pair["control_instrument_id"],
                        "candidate_computed": False,
                        "control_computed": False,
                        "pair_complete": False,
                        "pair_lift_pct": None,
                    }
                    pair_result["pair_result_digest"] = digest(pair_result)
                    pair_results.append(pair_result)
                item.update(
                    control_selected=controls,
                    control_status=signal["control_status"],
                    control_discriminative=signal["control_discriminative"],
                    control_completed=0,
                    control_net_excess_pct=None,
                    matched_control_pairs=signal["matched_control_pairs"],
                    matched_control_pairs_digest=signal["matched_control_pairs_digest"],
                    matched_control_pair_results=pair_results,
                    matched_control_pair_results_digest=digest(pair_results),
                )
            if current.date() < end or (current.date() == end and current.time() < time(15, 30)):
                item["status"] = "waiting_for_maturity"
                horizons.append(item)
                continue
            bars = _load_cached_bars(cache, provider_mode, [*ids, policy["benchmark_id"]], entry, end)
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
            be, ber = price(policy["benchmark_id"], entry, "adjusted_open")
            bx, bxr = price(policy["benchmark_id"], end, "adjusted_close")
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
                                 net_excess_return_pct=ir-br-policy["round_trip_cost_bps"]/100)
                item["labels"].append(label)
            known = {r["instrument_id"]: r["net_excess_return_pct"] for r in item["labels"]
                     if r["status"] == "computed"}
            item.update(completed=len(known), status="complete" if len(known) == len(ids) else "partial")
            item["candidate_completed"] = sum(k in known for k in candidate)
            item["baseline_completed"] = sum(k in known for k in baseline)
            if all(k in known for k in candidate):
                item["candidate_net_excess_pct"] = sum(known[k] for k in candidate)/len(candidate)
            if not is_v2 and baseline and all(k in known for k in baseline):
                item["baseline_net_excess_pct"] = sum(known[k] for k in baseline)/len(baseline)
            if is_v2:
                pair_results = []
                for pair in signal["matched_control_pairs"]:
                    candidate_value = known.get(pair["candidate_instrument_id"])
                    control_value = known.get(pair["control_instrument_id"])
                    complete = candidate_value is not None and control_value is not None
                    pair_result = {
                        "position": pair["position"],
                        "candidate_instrument_id": pair["candidate_instrument_id"],
                        "control_instrument_id": pair["control_instrument_id"],
                        "candidate_computed": candidate_value is not None,
                        "control_computed": control_value is not None,
                        "pair_complete": complete,
                        "pair_lift_pct": candidate_value - control_value if complete else None,
                    }
                    pair_result["pair_result_digest"] = digest(pair_result)
                    pair_results.append(pair_result)
                item["matched_control_pair_results"] = pair_results
                item["matched_control_pair_results_digest"] = digest(pair_results)
                item["control_completed"] = sum(key in known for key in controls)
                if len(controls) == policy["top_k"] and all(key in known for key in controls):
                    item["control_net_excess_pct"] = sum(known[key] for key in controls) / len(controls)
                if (signal["control_status"] == "available"
                        and item["candidate_completed"] == policy["top_k"]
                        and item["control_completed"] == policy["top_k"]
                        and item["candidate_net_excess_pct"] is not None
                        and item["control_net_excess_pct"] is not None):
                    item.update(
                        paired_complete=True,
                        lift_pct=item["candidate_net_excess_pct"] - item["control_net_excess_pct"],
                    )
            elif item["candidate_net_excess_pct"] is not None and item["baseline_net_excess_pct"] is not None:
                item.update(paired_complete=True, lift_pct=item["candidate_net_excess_pct"]-item["baseline_net_excess_pct"])
            horizons.append(item)
    finally:
        reader.close()
        engine.dispose()
        connection.close()
    result = {"protocol": ("financial-rule-forward-evaluation-v2" if is_v2
                           else "financial-rule-forward-evaluation-v1"), "signal_digest": signal["result_digest"],
              "policy_digest": signal["policy_digest"], "signal_date": str(day), "as_of": current.isoformat(),
              "provider_mode": provider_mode, "horizons": horizons,
              "runtime_identity": evaluation_runtime_identity(),
              "decision_weight": False, "activation_allowed": False,
              "limitations": ["Research fixed-horizon labels, not executable fills or account returns.",
                              "G2 baseline is a research model, never the supplied observation order.",
                              "Digests prove integrity, not timestamp authenticity or historical PIT.",
                              "No promotion; overlapping daily windows are not independent samples."]}
    if is_v2:
        result["limitations"] = [
            "Research fixed-horizon labels, not executable fills or account returns.",
            "Matched controls never cross industry and never impute missing industry, market cap, or prices.",
            "G2 full_features ranks are tie-break evidence, not a second performance baseline.",
            "Digests prove integrity, not timestamp authenticity or historical PIT.",
            "No promotion; overlapping daily windows are not independent samples.",
        ]
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
