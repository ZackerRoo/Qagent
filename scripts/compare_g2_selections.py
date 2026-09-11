#!/usr/bin/env python3
"""Describe paired G2 selections from an existing ready signal; never score or train."""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path


VARIANTS = ("full_features", "without_risk")


def digest(value: dict) -> str:
    # Match the existing G2 archive encoding without importing its inference stack.
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode()).hexdigest()


def validate_signal(signal: dict) -> list[dict]:
    if not isinstance(signal, dict):
        raise ValueError("signal must be an object")
    unsigned = {key: value for key, value in signal.items() if key != "result_digest"}
    if digest(unsigned) != signal.get("result_digest"):
        raise ValueError("result_digest mismatch")
    if signal.get("protocol") != "g2-risk-feature-forward-v1" or signal.get("status") != "ready":
        raise ValueError("requires an existing ready G2 forward signal")
    if signal.get("decision_weight") is not False or signal.get("activation_allowed") is not False:
        raise ValueError("signal isolation flags changed")
    if signal.get("reasons") != []:
        raise ValueError("ready signal must have no blocking reasons")
    try:
        day = signal["signal_date"]
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("noncanonical date")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid signal_date") from exc
    rows = signal.get("predictions")
    if not isinstance(rows, list) or len(rows) < 5:
        raise ValueError("requires at least five paired prediction rows")
    if not isinstance(signal.get("coverage"), dict) or signal["coverage"].get("scored_rows") != len(rows):
        raise ValueError("coverage scored_rows differs from paired predictions")
    identities = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("prediction row must be an object")
        key = row.get("instrument_id")
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise ValueError("invalid instrument_id")
        identities.append(key)
        industry = row.get("industry")
        if industry is not None and not isinstance(industry, str):
            raise ValueError("industry must be text or null")
        for variant in VARIANTS:
            values = row.get(variant)
            if not isinstance(values, dict):
                raise ValueError("each row requires both variants")
            score, rank = values.get("score"), values.get("rank")
            if type(score) not in (int, float) or not math.isfinite(score):
                raise ValueError("scores must be finite numbers")
            if type(rank) is not int or rank < 1 or rank > len(rows):
                raise ValueError("ranks must be integers within the paired cohort")
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate instrument_id")
    for variant in VARIANTS:
        ordered = sorted(rows, key=lambda row: (-row[variant]["score"], row["instrument_id"]))
        if [row[variant]["rank"] for row in ordered] != list(range(1, len(rows) + 1)):
            raise ValueError("ranks must match descending scores with instrument_id tie order")
    return rows


def describe_row(row: dict) -> dict:
    return {
        "instrument_id": row["instrument_id"],
        "industry": (row.get("industry") or "").strip() or "unknown",
        **{variant: {key: row[variant][key] for key in ("rank", "score")} for variant in VARIANTS},
        "candidate_rank_improvement": row["full_features"]["rank"] - row["without_risk"]["rank"],
    }


def industry_distribution(rows: list[dict]) -> dict:
    counts = {"unknown": 0}
    for row in rows:
        industry = (row.get("industry") or "").strip() or "unknown"
        counts[industry] = counts.get(industry, 0) + 1
    weights = {name: count / len(rows) for name, count in sorted(counts.items())}
    return {
        "industries": [{"industry": name, "count": counts[name], "weight": weight}
                       for name, weight in weights.items()],
        "unknown_fraction": weights["unknown"],
        "largest_bucket_weight": max(weights.values()),
        "hhi_including_unknown": sum(value * value for value in weights.values()),
    }


def compare(signal: dict) -> dict:
    rows = validate_signal(signal)
    by_id = {row["instrument_id"]: row for row in rows}
    ranked = {variant: sorted(rows, key=lambda row: row[variant]["rank"]) for variant in VARIANTS}
    comparisons = {}
    for label, requested in (("top5", 5), ("top10", 10), ("top10pct", math.ceil(len(rows) * 0.1))):
        count = min(requested, len(rows))
        selected = {variant: ranked[variant][:count] for variant in VARIANTS}
        baseline = {row["instrument_id"] for row in selected["full_features"]}
        candidate = {row["instrument_id"] for row in selected["without_risk"]}

        def details(identities: set[str], order: str) -> list[dict]:
            return [describe_row(by_id[key]) for key in sorted(identities, key=lambda key: by_id[key][order]["rank"])]

        comparisons[label] = {
            "requested_k": requested, "effective_k": count,
            "intersection_count": len(baseline & candidate),
            "overlap_fraction": len(baseline & candidate) / count,
            "jaccard": len(baseline & candidate) / len(baseline | candidate),
            "entered_candidate": details(candidate - baseline, "without_risk"),
            "exited_candidate": details(baseline - candidate, "full_features"),
            "shared": details(baseline & candidate, "without_risk"),
            "selections": {variant: [describe_row(row) for row in members] for variant, members in selected.items()},
            "industry_distributions": {variant: industry_distribution(members) for variant, members in selected.items()},
        }
    return {
        "protocol": "g2-selection-behavior-v1", "signal_date": signal["signal_date"],
        "source_result_digest": signal["result_digest"],
        "paired_rows": len(rows), "coverage": signal["coverage"],
        "comparisons": comparisons,
        "paired_rankings": [describe_row(row) for row in ranked["without_risk"]],
        "decision_weight": False, "activation_allowed": False,
        "limitations": [
            "Descriptive same-date model selections; not portfolio turnover, returns, or causal attribution.",
            "Input digest checks integrity, not authenticity or independent forward eligibility.",
            "Unknown industries are a separate bucket; HHI including unknown is a diagnostic.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="optional new JSON path; existing files are never overwritten")
    args = parser.parse_args()
    try:
        raw = args.signal.read_bytes()
        report = compare(json.loads(raw))
        report["source_file_sha256"] = sha256(raw).hexdigest()
        report["script_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
        encoded = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(encoded)
        else:
            print(encoded, end="")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"G2 selection comparison failed: {exc}\n")


if __name__ == "__main__":
    main()
