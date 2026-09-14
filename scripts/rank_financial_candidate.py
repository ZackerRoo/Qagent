#!/usr/bin/env python3
"""Rank a separate research candidate from verified current enrichment artifacts.

--input accepts enrichment reports or daily-documented-research-v1 wrappers and
may repeat. --baseline is a JSON list of explicit stock IDs in baseline order.
No outcomes, trading state, frozen G2 files, network or database are accessed.
"""
import argparse
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from research_cashflow_quality import digest, number
from research_financial_enrichment import analyze, SYMBOL
from rank_g2_consensus import publish

METRICS = ("cashflow_to_netprofit", "cashflow_to_total_revenue", "earnings_yield")
POLICY = {
    "protocol": "financial-candidate-equal-percentiles-v1",
    "metrics": list(METRICS), "direction": "higher_is_better",
    "weights": ["1/3", "1/3", "1/3"],
    "percentile": "(count_lower + (count_equal - 1)/2)/(N-1); singleton=1/2",
    "ties": "ascending_stock_id_after_exact_equal_weight_mean",
    "missing": "exclude_from_candidate_and_baseline_comparison_without_imputation",
    "outcomes_used": False, "decision_weight": False, "activation_allowed": False,
}
METRICS_V2 = METRICS + ("roe", "netprofit_margin", "liabilities_to_assets")
POLICY_V2 = {
    **POLICY, "protocol": "financial-candidate-equal-percentiles-v2",
    "metrics": list(METRICS_V2), "weights": ["1/6"] * 6,
    "direction": {metric: "lower_is_better" if metric == "liabilities_to_assets" else "higher_is_better"
                  for metric in METRICS_V2},
    "scope": "same-period general-industrial consolidated statements; no financial companies",
}


def verify_digest(document):
    if not isinstance(document, dict):
        raise ValueError("invalid_document")
    expected = document.get("result_digest")
    if not isinstance(expected, str) or digest({k: v for k, v in document.items() if k != "result_digest"}) != expected:
        raise ValueError("digest_mismatch")


def reports_from(document):
    verify_digest(document)
    if document.get("protocol") in ("financial-enrichment-current-v1", "financial-enrichment-current-v2"):
        return [document]
    if (document.get("protocol") not in ("daily-documented-research-v1", "daily-documented-research-v2")
            or document.get("decision_weight") is not False or document.get("activation_allowed") is not False
            or not isinstance(document.get("enrichment_reports"), list)):
        raise ValueError("unsupported_document")
    reports = document["enrichment_reports"]
    for report in reports:
        if any(report.get(key) != document.get(key) for key in ("source", "period", "trade_date")):
            raise ValueError("wrapper_identity_mismatch")
    return reports


def validate_report(report):
    verify_digest(report)
    if (report.get("protocol") not in ("financial-enrichment-current-v1", "financial-enrichment-current-v2")
            or report.get("decision_weight") is not False or report.get("activation_allowed") is not False
            or report.get("temporal_semantics") != "current_observation_not_historical_pit"):
        raise ValueError("invalid_report_protocol")
    raw = report["raw_evidence"]
    if report.get("input_digest") != digest(raw):
        raise ValueError("raw_digest_mismatch")
    # Reuse identity, date, revision and positive-denominator validation before ranking.
    replay = analyze(raw)
    if (any(report.get(key) != replay.get(key) for key in (
            "protocol", "source", "period", "trade_date", "retrieved_at", "instruments"))):
        raise ValueError("replay_mismatch")
    return replay


def selected_metrics(item, version=1):
    sections, ratios = item["sections"], item["financial_ratios"]
    reasons, values = [], {}
    required = ("cashflow", "income", "daily_basic") + (("balancesheet", "fina_indicator") if version == 2 else ())
    for api in required:
        if sections[api]["status"] != "observed":
            reasons.append(api + "_not_observed")
            if version == 2:
                excluded = sections[api].get("coverage", {}).get("excluded", {})
                if excluded.get("unsupported_company_type"):
                    reasons.append(api + "_unsupported_company_type")
                derived = sections[api].get("derived", {})
                if "unsupported_report_or_company_type" in derived.get("excluded_rows", []):
                    reasons.append(api + "_unsupported_report_or_company_type")
                if derived.get("exclusions", {}).get("section") == "ambiguous_consumed_revision":
                    reasons.append(api + "_ambiguous_consumed_revision")
    if reasons:
        return values, reasons
    for metric in METRICS[:2]:
        value = number(ratios[metric]["value"])
        if value is None or ratios[metric].get("reason") is not None:
            reasons.append(metric + "_unavailable")
        else:
            values[metric] = value
    valuation = sections["daily_basic"]["derived"]["values"]
    pe, earnings_yield = number(valuation.get("pe")), number(valuation.get("earnings_yield"))
    if pe is None or pe <= 0 or earnings_yield is None or earnings_yield <= 0:
        reasons.append("earnings_yield_unavailable")
    else:
        values["earnings_yield"] = earnings_yield
    if version == 2:
        for api, metrics in (("fina_indicator", ("roe", "netprofit_margin")),
                             ("balancesheet", ("liabilities_to_assets",))):
            derived = sections[api]["derived"]
            for metric in metrics:
                value = number(derived["values"].get(metric))
                if value is None or metric in derived["exclusions"]:
                    reasons.append(metric + "_unavailable")
                else:
                    values[metric] = value
        if sections["fina_indicator"]["derived"].get("scope_confirmed_by_matching_statements") is not True:
            reasons.append("indicator_scope_unconfirmed")
    return values, reasons


def rank_candidate(documents, baseline, top_k=5):
    baseline_input = baseline
    baseline_metadata = {"kind": "supplied_observation_order", "production_baseline_verified": False}
    if isinstance(baseline, dict):
        if set(baseline) - {"order", "label", "recorded_at"}:
            raise ValueError("invalid_baseline_metadata")
        if "label" in baseline and not isinstance(baseline["label"], str):
            raise ValueError("invalid_baseline_metadata")
        baseline_metadata.update({k: baseline[k] for k in ("label", "recorded_at") if k in baseline})
        if "recorded_at" in baseline:
            timestamp = datetime.fromisoformat(baseline["recorded_at"])
            if timestamp.tzinfo is None:
                raise ValueError("baseline_timezone_required")
        baseline = baseline.get("order")
    if (not isinstance(baseline, list) or not baseline
            or any(not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in baseline)
            or len(baseline) != len(set(baseline))):
        raise ValueError("invalid_baseline")
    if type(top_k) is not int or top_k < 1:
        raise ValueError("invalid_top_k")
    if not isinstance(documents, list) or not documents:
        raise ValueError("missing_input")
    items, identities, observations, source_digests, protocols = {}, set(), [], [], set()
    for document in documents:
        for report in reports_from(document):
            replay = validate_report(report)
            protocols.add(replay["protocol"])
            observation = datetime.fromisoformat(replay["retrieved_at"])
            identities.add((replay["source"], replay["period"], replay["trade_date"],
                            observation.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()))
            observations.append(replay["retrieved_at"])
            source_digests.append(report["result_digest"])
            for symbol, item in replay["instruments"].items():
                if symbol in items:
                    raise ValueError("duplicate_stock_observation")
                items[symbol] = item
    if len(identities) != 1:
        raise ValueError("incompatible_observation_cohort")
    if len(protocols) != 1:
        raise ValueError("mixed_candidate_versions")
    version = 2 if next(iter(protocols)) == "financial-enrichment-current-v2" else 1
    policy = POLICY_V2 if version == 2 else POLICY
    values, excluded = {}, []
    for symbol in baseline:
        if symbol not in items:
            excluded.append({"stock_id": symbol, "reasons": ["missing_input"]})
            continue
        metrics, reasons = selected_metrics(items[symbol], version)
        if reasons:
            excluded.append({"stock_id": symbol, "reasons": reasons})
        else:
            values[symbol] = metrics
    count = len(values)
    rows = []
    for symbol, metrics in values.items():
        percentiles = {}
        for metric, value in metrics.items():
            lower = sum(v[metric] < value for v in values.values())
            equal = sum(v[metric] == value for v in values.values())
            percentiles[metric] = (Fraction(2 * lower + equal - 1, 2 * (count - 1))
                                   if count > 1 else Fraction(1, 2))
            if metric == "liabilities_to_assets":
                percentiles[metric] = 1 - percentiles[metric]
        rows.append({"stock_id": symbol, "values": {k: str(v) for k, v in metrics.items()},
                     "percentiles": {k: float(v) for k, v in percentiles.items()},
                     "score_fraction": sum(percentiles.values()) / len(policy["metrics"])})
    rows.sort(key=lambda row: (-row["score_fraction"], row["stock_id"]))
    for rank, row in enumerate(rows, 1):
        score = row.pop("score_fraction")
        row.update(rank=rank, score=float(score), score_exact=str(score))
    common_baseline = [s for s in baseline if s in values]
    effective_k = min(top_k, count)
    candidate_top = [row["stock_id"] for row in rows[:effective_k]]
    baseline_top = common_baseline[:effective_k]
    overlap = len(set(candidate_top) & set(baseline_top))
    source, period, trade_date, observed_day = next(iter(identities))
    baseline_metadata["timestamp_precedes_collection"] = (
        datetime.fromisoformat(baseline_metadata["recorded_at"]) <= min(
            datetime.fromisoformat(value) for value in observations)
        if baseline_metadata.get("recorded_at") else None)
    output = {
        "protocol": policy["protocol"], "policy": policy, "policy_digest": digest(policy),
        "source": source, "period": period, "trade_date": trade_date, "observation_day": observed_day,
        "source_observations": sorted(observations), "source_result_digests": sorted(source_digests),
        "input_document_digests": [doc["result_digest"] for doc in documents],
        "baseline_order": baseline, "baseline_metadata": baseline_metadata,
        "baseline_digest": digest(baseline_input),
        "same_cohort_baseline_order": common_baseline,
        "coverage": {"baseline_count": len(baseline), "input_stock_count": len(items),
                     "eligible_count": count, "excluded": excluded,
                     "outside_baseline": sorted(set(items) - set(baseline))},
        "rankings": rows, "comparison": {
            "requested_top_k": top_k, "effective_top_k": effective_k,
            "candidate_top": candidate_top, "baseline_top": baseline_top,
            "overlap_count": overlap, "overlap_fraction": overlap / effective_k if effective_k else None,
        }, "status": "ranked" if count else "no_eligible_stocks",
        "research_only": True, "decision_weight": False, "activation_allowed": False,
        "limitations": [
            "Current observations only; not historical PIT or a backfill of frozen G2 signals.",
            "Baseline order is explicitly supplied, not proven to have been registered before observation.",
            "Timestamp metadata does not prove an immutable preregistration or a production-selection baseline.",
            "Equal percentiles are a fixed research hypothesis; no return labels or tuning are used.",
            "Small cohorts and complete-case exclusions can bias comparisons; overlap is not performance.",
            "Profit forecasts and money flow are not ranking inputs; financial-sector exclusions are inherited.",
        ],
        "implementation_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    output["result_digest"] = digest(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        documents = [json.loads(path.read_text()) for path in args.input]
        report = rank_candidate(documents, json.loads(args.baseline.read_text()), args.top_k)
        encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            publish(args.output, encoded)
        else:
            print(encoded, end="")
        return 0 if report["status"] == "ranked" else 1
    except Exception:
        print(json.dumps({"status": "error", "error": "financial_candidate_failed",
                          "research_only": True, "decision_weight": False, "activation_allowed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
