"""Synthetic tool checks only; these fixtures are not observed G2 signals."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "compare_g2_selections.py"
SPEC = importlib.util.spec_from_file_location("compare_g2_selections", SCRIPT)
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


def seal(signal):
    signal.pop("result_digest", None)
    signal["result_digest"] = comparison.digest(signal)
    return signal


def signal(reverse=False, count=20):
    rows = []
    for index in range(count):
        rank = count - index if reverse else index + 1
        rows.append({
            "instrument_id": f"SYNTHETIC:{index:02d}", "industry": None if index % 2 else "synthetic-sector",
            "full_features": {"rank": index + 1, "score": float(count - index)},
            "without_risk": {"rank": rank, "score": float(count - rank + 1)},
        })
    return seal({
        "protocol": "g2-risk-feature-forward-v1", "status": "ready", "signal_date": "2026-09-11",
        "reasons": [], "decision_weight": False, "activation_allowed": False,
        "coverage": {"scored_rows": count}, "predictions": rows,
    })


def test_identical_rankings_and_unknown_industry():
    report = comparison.compare(signal())
    top = report["comparisons"]["top5"]
    assert (top["intersection_count"], top["overlap_fraction"], top["jaccard"]) == (5, 1, 1)
    assert top["entered_candidate"] == top["exited_candidate"] == []
    distribution = top["industry_distributions"]["full_features"]
    assert distribution["unknown_fraction"] == 2 / 5
    assert sum(bucket["count"] for bucket in distribution["industries"]) == 5
    assert report["comparisons"]["top10pct"]["effective_k"] == 2


def test_disjoint_top_ten_preserves_both_ranks():
    report = comparison.compare(signal(reverse=True))
    top = report["comparisons"]["top10"]
    assert top["intersection_count"] == top["overlap_fraction"] == top["jaccard"] == 0
    assert len(top["entered_candidate"]) == len(top["exited_candidate"]) == 10
    first = top["entered_candidate"][0]
    assert first["instrument_id"] == "SYNTHETIC:19"
    assert first["full_features"]["rank"] == 20
    assert first["without_risk"]["rank"] == 1
    assert first["candidate_rank_improvement"] == 19


def test_cohort_smaller_than_top_ten_and_absent_industries():
    payload = signal(count=7)
    for row in payload["predictions"]:
        row["industry"] = " "
    top = comparison.compare(seal(payload))["comparisons"]["top10"]
    assert (top["requested_k"], top["effective_k"]) == (10, 7)
    assert top["industry_distributions"]["without_risk"]["unknown_fraction"] == 1


def test_digest_damage_is_rejected():
    payload = signal()
    payload["predictions"][0]["industry"] = "changed"
    with pytest.raises(ValueError, match="digest"):
        comparison.compare(payload)


@pytest.mark.parametrize("damage,match", [
    (lambda p: p["predictions"][1].update(instrument_id="SYNTHETIC:00"), "duplicate"),
    (lambda p: p["predictions"][0]["full_features"].update(rank=2), "ranks must match"),
    (lambda p: p["predictions"][0]["full_features"].update(score=True), "finite numbers"),
    (lambda p: p["predictions"][0]["without_risk"].update(rank=1.0), "ranks must be integers"),
    (lambda p: p["predictions"][0].pop("without_risk"), "both variants"),
    (lambda p: p["coverage"].update(scored_rows=19), "coverage"),
    (lambda p: p.update(status="not_ready"), "ready G2"),
])
def test_invalid_paired_contract_is_rejected(damage, match):
    payload = signal()
    damage(payload)
    with pytest.raises(ValueError, match=match):
        comparison.compare(seal(payload))


def test_cli_runs_without_inference_dependencies_and_refuses_overwrite(tmp_path):
    source, output = tmp_path / "signal.json", tmp_path / "report.json"
    source.write_text(json.dumps(signal()))
    command = [sys.executable, "-S", str(SCRIPT), "--signal", str(source), "--output", str(output)]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert json.loads(output.read_text())["paired_rows"] == 20
    before = output.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 2
    assert output.read_bytes() == before
