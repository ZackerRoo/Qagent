#!/usr/bin/env python3
"""Generate an isolated minimax rank candidate from an existing ready G2 signal."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile

from compare_g2_selections import VARIANTS, digest, industry_distribution, validate_signal


POLICY = {
    "version": "g2-consensus-minimax-v1",
    "selection": "ascending_worst_rank_then_equal_weight_mean_rank_then_instrument_id",
    "variants": list(VARIANTS),
    "weights": [0.5, 0.5],
    "outcomes_used": False,
    "decision_weight": False,
    "activation_allowed": False,
}


def rank_consensus(signal: dict) -> dict:
    rows = validate_signal(signal)
    candidates = []
    for row in rows:
        ranks = [row[variant]["rank"] for variant in VARIANTS]
        candidates.append({
            "instrument_id": row["instrument_id"], "industry": row.get("industry"),
            **{variant: {key: row[variant][key] for key in ("rank", "score")} for variant in VARIANTS},
            "worst_rank": max(ranks), "mean_rank": sum(ranks) / 2,
            "rank_disagreement": abs(ranks[0] - ranks[1]),
        })
    candidates.sort(key=lambda row: (row["worst_rank"], row["mean_rank"], row["instrument_id"]))
    for position, row in enumerate(candidates, start=1):
        row["consensus_rank"] = position
        row["rank_improvement"] = {
            variant: row[variant]["rank"] - position for variant in VARIANTS
        }
    selections = {}
    for label, requested in (("top5", 5), ("top10", 10), ("top10pct", math.ceil(len(rows) * 0.1))):
        selected = candidates[:requested]
        selected_ids = {row["instrument_id"] for row in selected}
        comparisons = {}
        for variant in VARIANTS:
            baseline = sorted(candidates, key=lambda row: row[variant]["rank"])[:requested]
            baseline_ids = {row["instrument_id"] for row in baseline}
            comparisons[variant] = {
                "overlap_fraction": len(baseline_ids & selected_ids) / len(selected),
                "entered": [row["instrument_id"] for row in selected if row["instrument_id"] not in baseline_ids],
                "exited": [row["instrument_id"] for row in baseline if row["instrument_id"] not in selected_ids],
                "baseline_industry_distribution": industry_distribution(baseline),
            }
        selections[label] = {
            "requested_k": requested, "effective_k": len(selected), "candidates": selected,
            "industry_distribution": industry_distribution(selected), "comparisons": comparisons,
        }
    return {
        "protocol": "g2-consensus-ranking-candidate-v1", "policy": dict(POLICY),
        "policy_digest": digest(POLICY), "signal_date": signal["signal_date"],
        "source_result_digest": signal["result_digest"], "paired_rows": len(rows),
        "coverage": signal["coverage"], "rankings": candidates, "selections": selections,
        "decision_weight": False, "activation_allowed": False,
        "status": "offline_hypothesis", "forward_metrics": None,
        "limitations": [
            "A new research candidate; not a replacement for either frozen G2 variant or its preregistration.",
            "Worst rank measures agreement between related models, not calibrated uncertainty or return quality.",
            "No outcome is read or used for ranking; performance and execution eligibility remain unverified.",
            "Industry distributions are descriptive; there is no industry quota or neutralization change.",
            "Digest validation checks integrity, not authenticity or independent forward eligibility.",
        ],
    }


def publish(path: Path, encoded: str) -> None:
    """Publish complete output exclusively, without importing the inference stack."""
    fd, temporary = tempfile.mkstemp(prefix=".g2-consensus-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        raw = args.signal.read_bytes()
        report = rank_consensus(json.loads(raw))
        report["source_file_sha256"] = sha256(raw).hexdigest()
        report["implementation_sha256"] = {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("rank_g2_consensus.py", "compare_g2_selections.py")
        }
        report["result_digest"] = digest(report)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            publish(args.output, encoded)
        else:
            print(encoded, end="")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"G2 consensus ranking failed: {exc}\n")


if __name__ == "__main__":
    main()
