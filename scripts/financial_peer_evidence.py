"""Bounded, replayable external Financial controls; never changes ranking inputs."""
from collections import Counter
from datetime import datetime, time as daytime
import math
import time
from zoneinfo import ZoneInfo

from financial_industry_evidence import validate_industry_evidence
from rank_financial_candidate import rank_candidate, selected_metrics, verify_digest
from research_financial_enrichment import SYMBOL, analyze, digest, number

POLICY = {"protocol": "financial-peer-control-evidence-v1", "pages_per_api": 1,
          "page_size": 5000, "maximum_peers": 10, "peers_per_top5": 2,
          "selection": "within_captured_first_page_intersection_per_industry_minimum_absolute_log_market_cap_distance_then_symbol",
          "qualification": "unchanged_financial_candidate_v2_metrics",
          "anchor_source": "verified_original_individual_industry_and_daily_basic; bulk_agreement_when_present",
          "temporal_semantics": "current_observation_not_historical_pit",
          "catalogue_scope": "bounded_first_page_intersection_completeness_unknown",
          "pagination": "disabled_provider_offset_semantics_unverified"}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _stamp(value, document):
    value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError("peer_timezone_required")
    local = value.astimezone(SHANGHAI)
    if local.strftime("%Y%m%d") != document["trade_date"] or local.time() < daytime(15, 30):
        raise ValueError("peer_capture_day_invalid")
    return value


def _request(document, api, offset=0):
    return {"source": document["source"], "api": api,
            "params": {} if api == "stock_basic" else {"trade_date": document["trade_date"]},
            "limit": 5000, "offset": offset,
            "fields": "ts_code,industry,list_status" if api == "stock_basic"
            else "ts_code,trade_date,total_mv"}


def _response(entry, request, document):
    if entry["request"] != request:
        raise ValueError("peer_request_mismatch")
    response = entry["response"]
    verify_digest(response)
    received = _stamp(entry["received_at"], document)
    fetched = _stamp(response["fetched_at"], document)
    started = _stamp(document["started_at"], document)
    finished = _stamp(document["finished_at"], document)
    if not started <= fetched <= received <= finished:
        raise ValueError("peer_capture_time_order")
    if (response.get("source") != document["source"]
            or response.get("data_source") != ("datahubco" if document["source"] == "datahubco"
                                                else "tushare_relay_promax")
            or response.get("request") != {k: v for k, v in request.items() if k != "source"}
            or response.get("research_only") is not True
            or response.get("decision_weight") is not False
            or response.get("activation_allowed") is not False):
        raise ValueError("peer_response_identity_invalid")
    rows = response.get("rows")
    coverage = response.get("coverage", {})
    fields = response.get("fields")
    if (not isinstance(fields, list) or len(fields) != len(set(fields))
            or any(not isinstance(field, str) for field in fields)
            or (request["fields"] and not set(request["fields"].split(",")).issubset(fields))):
        raise ValueError("peer_response_fields_invalid")
    if (response.get("status") not in {"observed", "no_rows"} or not isinstance(rows, list)
            or type(coverage.get("rows")) is not int or coverage.get("rows") != len(rows)
            or type(coverage.get("row_limit")) is not int or coverage.get("row_limit") != request["limit"]
            or coverage.get("page_limit_reached") is not (len(rows) == request["limit"])
            or len(rows) > request["limit"] or any(not isinstance(row, dict) for row in rows)):
        raise ValueError("peer_response_coverage_invalid")
    if (response["status"] == "observed") != bool(rows):
        raise ValueError("peer_response_status_invalid")
    return rows


def _pages(raw, api, document):
    if not isinstance(raw, list) or len(raw) != 1:
        raise ValueError("peer_first_page_required")
    result = {}
    for index, entry in enumerate(raw):
        rows = _response(entry, _request(document, api, index * 5000), document)
        for row in rows:
            symbol = row.get("ts_code")
            if not isinstance(symbol, str) or symbol in result:
                raise ValueError("peer_duplicate_or_invalid_identity")
            if api == "daily_basic" and row.get("trade_date") != document["trade_date"]:
                raise ValueError("peer_market_cap_day_mismatch")
            result[symbol] = row
    return result


def _selection(document, raw):
    industry = validate_industry_evidence(document["industry_evidence"], document["symbols"],
                                          document["trade_date"], source=document["source"])
    if industry["status"] != "available":
        raise ValueError("peer_original_industry_unavailable")
    ranked = rank_candidate(document["enrichment_reports"], document["symbols"], top_k=5)
    if ranked["rankings"] != document["financial_candidate"]["rankings"]:
        raise ValueError("peer_original_ranking_mismatch")
    top = [row["stock_id"] for row in ranked["rankings"][:5]]
    if len(top) != 5:
        raise ValueError("peer_top5_unavailable")
    industries = {row["symbol"]: row["industry"] for row in industry["rows"]}
    catalogue = _pages(raw["catalogue"], "stock_basic", document)
    caps = _pages(raw["market_caps"], "daily_basic", document)
    original_caps = {}
    for report in document["enrichment_reports"]:
        for symbol, details in report["instruments"].items():
            original_caps[symbol] = number(details["sections"]["daily_basic"]["derived"]["values"].get("total_mv"))
    for symbol in top:
        cap = original_caps.get(symbol)
        if cap is None or cap <= 0 or not math.isfinite(float(cap)) or float(cap) == 0:
            raise ValueError("peer_original_market_cap_invalid")
        if symbol in caps and number(caps[symbol].get("total_mv")) != cap:
            raise ValueError("peer_original_market_cap_mismatch")
        if symbol in catalogue:
            observed_industry = catalogue[symbol].get("industry")
            if not isinstance(observed_industry, str) or observed_industry.strip() != industries[symbol]:
                raise ValueError("peer_original_industry_mismatch")
    selected = []
    for industry_name, count in sorted(Counter(industries[s] for s in top).items()):
        choices = []
        anchors = [float(original_caps[s]) for s in top if industries[s] == industry_name]
        for symbol, row in catalogue.items():
            cap = number(caps.get(symbol, {}).get("total_mv"))
            if (SYMBOL.fullmatch(symbol) and symbol not in document["symbols"]
                    and row.get("list_status") == "L" and isinstance(row.get("industry"), str)
                    and row["industry"].strip() == industry_name and cap is not None and cap > 0
                    and math.isfinite(float(cap)) and float(cap) > 0):
                distance = min(abs(math.log(float(cap)) - math.log(anchor)) for anchor in anchors)
                choices.append((distance, symbol))
        selected.extend(symbol for _, symbol in sorted(choices)[:2 * count])
    return top, selected, catalogue, caps


def _build(document, raw):
    from collect_daily_documented_research import BATCH_APIS, FINANCIAL_APIS, batch_params
    result = {"protocol": POLICY["protocol"], "policy": POLICY, "source": document["source"],
              "trade_date": document["trade_date"], "period": document["period"],
              "status": "unavailable", "reasons": [], "top5_symbols": [], "selected_symbols": [],
              "rows": [], "excluded": [], "raw_evidence": raw, "enrichment_reports": [],
              "catalogue_completeness": "unknown", "market_caps_completeness": "unknown",
              "decision_weight": False, "activation_allowed": False}
    try:
        top, selected, catalogue, caps = _selection(document, raw)
        result.update(top5_symbols=top, selected_symbols=selected)
        if set(raw["financial"]) != set(selected):
            raise ValueError("peer_financial_universe_mismatch")
        for symbol in selected:
            entries = raw["financial"][symbol]
            if set(entries) != set(BATCH_APIS):
                raise ValueError("peer_financial_api_mismatch")
            sections = {}
            for api in BATCH_APIS:
                params = batch_params(api, symbol, document["period"], document["trade_date"])
                request = {"source": document["source"], "api": api, "limit": params.pop("limit"),
                           "params": params, "offset": 0, "fields": None}
                rows = _response(entries[api], request, document)
                if any(row.get("ts_code") != symbol for row in rows):
                    raise ValueError("peer_financial_symbol_mismatch")
                if api in {"daily_basic", "moneyflow"} and any(
                        row.get("trade_date") != document["trade_date"] for row in rows):
                    raise ValueError("peer_financial_trade_date_mismatch")
                if api in FINANCIAL_APIS:
                    sections[api] = {"status": entries[api]["response"]["status"], "rows": rows}
            observed = max((entries[api]["received_at"] for api in BATCH_APIS),
                           key=lambda value: _stamp(value, document))
            report = analyze({"analysis_version": 2, "matched_control_evidence_version": 1,
                              "source": document["source"], "period": document["period"],
                              "trade_date": document["trade_date"], "retrieved_at": observed,
                              "instruments": {symbol: sections}})
            result["enrichment_reports"].append(report)
            details = report["instruments"][symbol]
            _, reasons = selected_metrics(details, 2)
            cap = number(details["sections"]["daily_basic"]["derived"]["values"].get("total_mv"))
            if cap is None or cap <= 0 or cap != number(caps[symbol]["total_mv"]):
                reasons.append("peer_market_cap_mismatch")
            if reasons:
                result["excluded"].append({"symbol": symbol, "reasons": sorted(set(reasons))})
            else:
                industry = catalogue[symbol]["industry"].strip()
                result["rows"].append({"symbol": symbol, "instrument_id": "CN:" + symbol.split(".")[0],
                                       "industry": industry, "exposure_group": industry, "total_mv": str(cap)})
        result["status"] = "available"
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result["rows"] = []
        result["reasons"] = [str(exc) if isinstance(exc, ValueError) else "peer_evidence_invalid"]
    result["result_digest"] = digest(result)
    return result


def collect_peer_evidence(document, *, base_url, query, deadline_monotonic):
    from collect_daily_documented_research import BATCH_APIS, batch_params
    raw = {"catalogue": [], "market_caps": [], "financial": {}}

    def fetch(request):
        if deadline_monotonic - time.monotonic() < 70:
            raise ValueError("peer_budget_exhausted")
        response = query(base_url, **request)
        return {"request": request, "response": response,
                "received_at": datetime.now(SHANGHAI).isoformat()}

    working = dict(document)
    try:
        for key, api in (("catalogue", "stock_basic"), ("market_caps", "daily_basic")):
            for index in range(1):
                entry = fetch(_request(document, api, index * 5000))
                raw[key].append(entry)
                working["finished_at"] = datetime.now(SHANGHAI).isoformat()
                rows = _response(entry, _request(document, api, index * 5000), working)
                if len(rows) < 5000:
                    break
        _, selected, _, _ = _selection(working, raw)
        for symbol in selected:
            raw["financial"][symbol] = {}
            for api in BATCH_APIS:
                params = batch_params(api, symbol, document["period"], document["trade_date"])
                request = {"source": document["source"], "api": api, "limit": params.pop("limit"),
                           "params": params, "offset": 0, "fields": None}
                raw["financial"][symbol][api] = fetch(request)
    except Exception:
        # Raw partial evidence, not fabricated provider responses, explains failure on replay.
        pass
    working["finished_at"] = datetime.now(SHANGHAI).isoformat()
    return _build(working, raw)


def validate_peer_evidence(document):
    section = document["peer_control_evidence"]
    verify_digest(section)
    replay = _build(document, section["raw_evidence"])
    if replay != section:
        raise ValueError("peer_evidence_replay_mismatch")
    return replay
