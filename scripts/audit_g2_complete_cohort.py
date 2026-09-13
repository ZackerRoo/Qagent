#!/usr/bin/env python3
"""Audit complete raw features in a ready G2 signal; descriptive, never a gate."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path

from compare_g2_selections import VARIANTS, digest, industry_distribution, validate_signal
from rank_g2_consensus import publish, rank_consensus

CONFIG = Path(__file__).resolve().parents[1] / "docs/research/g2-risk-feature-freeze-20260910-config.json"
POLICY = {
    "version": "g2-complete-cohort-audit-v1",
    "cohort": "both_variants_all_raw_features_finite",
    "ordering": "filter_existing_full_cohort_orders_without_recomputing_minimax",
    "diagnostic_only": True,
    "changes_frozen_eligibility": False,
}


def finite(value: object) -> bool:
    """Mirror factor_shadow._finite_or_none without importing the inference stack."""
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def identities(values: object, label: str) -> list[str]:
    if not isinstance(values, list) or any(
        not isinstance(key, str) or not key or key.strip() != key for key in values
    ) or len(set(values)) != len(values):
        raise ValueError(f"invalid {label} identities")
    return values


def audit(signal: dict) -> dict:
    rows = validate_signal(signal)
    config = json.loads(CONFIG.read_text())
    if signal.get("config_sha256") != digest(config):
        raise ValueError("frozen config digest mismatch")
    features = config["variants"]
    source = signal.get("source")
    if not isinstance(source, dict):
        raise ValueError("embedded source required")
    if digest({key: value for key, value in source.items() if key != "source_digest"}) != source.get("source_digest"):
        raise ValueError("source digest mismatch")
    if signal.get("source_digest") != source["source_digest"]:
        raise ValueError("signal source digest mismatch")
    if (source.get("protocol") != "g2-forward-source-v1"
            or source.get("stage") != "ranking_finalized_before_job_completion"
            or source.get("provider") != "free"
            or source.get("decision_weight") is not False
            or source.get("activation_allowed") is not False
            or source.get("signal_date") != signal["signal_date"]
            or source.get("scan_job_id") != signal.get("scan_job_id")):
        raise ValueError("source contract mismatch")
    rankings = source.get("rankings")
    if not isinstance(rankings, list) or any(not isinstance(row, dict) for row in rankings):
        raise ValueError("source rankings required")
    keys = identities([row.get("instrument_id") for row in rankings], "ranking")
    key_set = set(keys)
    universe = identities(source.get("research_universe"), "research universe")
    stocks = identities(source.get("stock_ids"), "stock")
    if sorted(keys) != universe or not key_set.issubset(stocks):
        raise ValueError("source cohort mismatch")
    dates = {}
    items = source.get("items")
    if not isinstance(items, list):
        raise ValueError("source items required")
    for item in items:
        if not isinstance(item, dict) or item.get("instrument_id") not in key_set:
            raise ValueError("invalid source item identity")
        day = item.get("latest_trade_date")
        if day is not None and not isinstance(day, str):
            raise ValueError("invalid item date")
        dates.setdefault(item["instrument_id"], set()).add(day)
    paired, stale, empty, missing = {}, [], [], {}
    for row in rankings:
        key = row["instrument_id"]
        raw = row.get("research_features")
        if not isinstance(raw, dict):
            raise ValueError("raw research_features required")
        absent = {name: [feature for feature in columns if not finite(raw.get(feature))]
                  for name, columns in features.items()}
        if dates.get(key) != {signal["signal_date"]}:
            stale.append(key)
        elif len(absent["full_features"]) == len(features["full_features"]):
            empty.append(key)
        else:
            paired[key] = row
            missing[key] = absent
    if set(paired) != {row["instrument_id"] for row in rows}:
        raise ValueError("paired identities differ from source filtering")
    complete = {name: sum(not absent[name] for absent in missing.values()) for name in VARIANTS}
    industries = source.get("industries")
    if not isinstance(industries, dict) or not set(industries).issubset(key_set):
        raise ValueError("invalid source industries")
    for row in rows:
        industry_record = industries.get(row["instrument_id"], {})
        if not isinstance(industry_record, dict) or row.get("industry") != industry_record.get("industry"):
            raise ValueError("prediction industry differs from source")
    coverage = signal["coverage"]
    expected = {
        "source_rows": len(rankings), "scored_rows": len(rows),
        "eligible_fraction": len(rows) / len(rankings),
        "variant_joint_complete": complete,
        "industry_nonmissing": sum(row.get("industry") is not None for row in rows),
        "feature_nonmissing": {feature: sum(feature not in absent["full_features"] for absent in missing.values())
                               for feature in features["full_features"]},
    }
    for key, value in expected.items():
        if coverage.get(key) != value:
            raise ValueError(f"coverage {key} differs from raw source")
    for key, value in (("stale_or_missing_trade_dates", stale), ("excluded_all_features_missing", empty)):
        if sorted(identities(coverage.get(key), key)) != sorted(value):
            raise ValueError(f"coverage {key} differs from source identities")
    for row in rows:
        key = row["instrument_id"]
        for name in VARIANTS:
            value = row[name].get("feature_coverage")
            expected_fraction = (len(features[name]) - len(missing[key][name])) / len(features[name])
            if type(value) not in (float, int) or not math.isfinite(value) or value != expected_fraction:
                raise ValueError(f"feature_coverage mismatch for {key}/{name}")
    included = sorted(key for key, absent in missing.items() if all(not columns for columns in absent.values()))
    included_set = set(included)
    consensus = rank_consensus(signal)
    original = {"consensus": consensus["rankings"], **{
        name: sorted(rows, key=lambda row: row[name]["rank"]) for name in VARIANTS}}
    filtered = {name: [row for row in ordered if row["instrument_id"] in included_set]
                for name, ordered in original.items()}
    selections = {}
    for label, count in (("top5", 5), ("top10", 10), ("top10pct", math.ceil(len(included) * 0.1))):
        lanes = {}
        for name, ordered in filtered.items():
            selected = ordered[:count]
            lanes[name] = {
                "instrument_ids": [row["instrument_id"] for row in selected],
                "original_ranks": {row["instrument_id"]: row["consensus_rank"] if name == "consensus"
                                   else row[name]["rank"] for row in selected},
                "industry_distribution": industry_distribution(selected) if selected else None,
            }
        selections[label] = {"requested_k": count, "effective_k": min(count, len(included)), "lanes": lanes}
    return {
        "protocol": POLICY["version"], "policy": dict(POLICY), "policy_digest": digest(POLICY),
        "signal_date": signal["signal_date"], "source_result_digest": signal["result_digest"],
        "source_digest": source["source_digest"], "config_digest": digest(config),
        "paired_rows": len(rows), "complete_rows": len(included),
        "complete_fraction_of_paired": len(included) / len(rows),
        "complete_instrument_ids": included, "complete_universe_digest": digest({"instrument_ids": included}),
        "excluded_from_diagnostic": [{"instrument_id": key, "missing_features": missing[key]}
                                     for key in sorted(set(paired) - included_set)],
        "source_exclusions": {"stale_or_missing_trade_dates": sorted(stale), "all_features_missing": sorted(empty)},
        "filtered_orders": {name: [row["instrument_id"] for row in ordered] for name, ordered in filtered.items()},
        "removed_from_original_selections": {
            label: {name: [row["instrument_id"] for row in ordered[:count] if row["instrument_id"] not in included_set]
                    for name, ordered in original.items()}
            for label, count in (("top5", 5), ("top10", 10), ("top10pct", math.ceil(len(rows) * 0.1)))},
        "selections": selections, "status": "descriptive_complete_case" if included else "no_complete_rows",
        "decision_weight": False, "activation_allowed": False, "forward_metrics": None,
        "limitations": [
            "Diagnostic complete-case subset only; no new eligibility gate or exclusion from the frozen experiment.",
            "Complete-case selection can introduce selection bias; results do not represent the original full cohort.",
            "Missingness cannot be inferred to have caused ranking differences from this comparison.",
            "Existing full-cohort consensus order is filtered; minimax ranks are never recomputed on the subset.",
            "No returns, turnover, executable eligibility, or performance improvement is established.",
            "Digest checks establish internal consistency, not authenticity or independent forward eligibility.",
            "This audit does not revalidate the full source point-in-time contract or model provenance.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        raw = args.signal.read_bytes()
        result = audit(json.loads(raw))
        result["source_file_sha256"] = sha256(raw).hexdigest()
        result["implementation_sha256"] = {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("audit_g2_complete_cohort.py", "compare_g2_selections.py", "rank_g2_consensus.py")}
        result["result_digest"] = digest(result)
        encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            publish(args.output, encoded)
        else:
            print(encoded, end="")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"G2 complete-cohort audit blocked: {exc}\n")


if __name__ == "__main__":
    main()
