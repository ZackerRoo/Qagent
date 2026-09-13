"""Synthetic selection checks; none of these fixtures constitute forward evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("rank_g2_consensus", SCRIPTS / "rank_g2_consensus.py")
ranking = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ranking)
sys.path.pop(0)


def fixture(order=(5, 2, 3, 4, 1)):
    rows = [{
        "instrument_id": f"SYNTHETIC:{index}", "industry": None,
        "full_features": {"rank": index + 1, "score": float(5 - index)},
        "without_risk": {"rank": rank, "score": float(6 - rank)},
    } for index, rank in enumerate(order)]
    return seal({
        "protocol": "g2-risk-feature-forward-v1", "status": "ready", "signal_date": "2026-09-11",
        "reasons": [], "decision_weight": False, "activation_allowed": False,
        "coverage": {"scored_rows": 5}, "predictions": rows,
    })


def seal(value):
    value.pop("result_digest", None)
    value["result_digest"] = ranking.digest(value)
    return value


def test_jointly_strong_beats_single_model_extreme_and_input_unchanged():
    source = fixture()
    before = copy.deepcopy(source)
    result = ranking.rank_consensus(source)
    assert [r["instrument_id"] for r in result["rankings"]] == [
        "SYNTHETIC:1", "SYNTHETIC:2", "SYNTHETIC:3", "SYNTHETIC:0", "SYNTHETIC:4",
    ]
    assert result["rankings"][-1]["rank_disagreement"] == 4
    assert result["rankings"][0]["rank_improvement"] == {"full_features": 1, "without_risk": 1}
    assert source == before
    assert result["activation_allowed"] is False
    assert result["forward_metrics"] is None


def test_mean_breaks_equal_worst_rank_then_identity_breaks_equal_mean():
    result = ranking.rank_consensus(fixture((5, 2, 3, 1, 4)))
    # Both have worst rank 5; ranks (1,5) outrank (5,4).
    last = result["rankings"][-2:]
    assert [r["instrument_id"] for r in last] == ["SYNTHETIC:0", "SYNTHETIC:4"]
    symmetric = ranking.rank_consensus(fixture((5, 4, 3, 2, 1)))
    assert [r["instrument_id"] for r in symmetric["rankings"]][-2:] == ["SYNTHETIC:0", "SYNTHETIC:4"]


def test_input_order_invariance_and_outcomes_not_consumed():
    source = fixture()
    original = ranking.rank_consensus(source)
    source["predictions"].reverse()
    source["forward_metrics"] = {"invented_return": 999}
    changed = ranking.rank_consensus(seal(source))
    assert original["rankings"] == changed["rankings"]
    assert original["selections"] == changed["selections"]


def test_identical_rankings_reproduce_baseline_and_small_cohort_counts():
    result = ranking.rank_consensus(fixture((1, 2, 3, 4, 5)))
    assert [r["consensus_rank"] for r in result["rankings"]] == [1, 2, 3, 4, 5]
    assert result["selections"]["top10"]["effective_k"] == 5
    assert result["selections"]["top10pct"]["effective_k"] == 1
    assert result["selections"]["top5"]["industry_distribution"]["unknown_fraction"] == 1
    for comparison in result["selections"]["top5"]["comparisons"].values():
        assert comparison["overlap_fraction"] == 1
        assert comparison["entered"] == comparison["exited"] == []


@pytest.mark.parametrize("damage", [
    lambda s: s["predictions"][0].pop("without_risk"),
    lambda s: s["predictions"][0].update(instrument_id="SYNTHETIC:1"),
    lambda s: s["predictions"][0]["full_features"].update(score=True),
    lambda s: s.update(activation_allowed=True),
    lambda s: s.update(status="not_ready"),
])
def test_invalid_input_rejected(damage):
    source = fixture()
    damage(source)
    with pytest.raises(ValueError):
        ranking.rank_consensus(seal(source))


def test_damaged_digest_and_nonfinite_input_rejected():
    source = fixture()
    source["signal_date"] = "2026-09-12"
    with pytest.raises(ValueError, match="digest"):
        ranking.rank_consensus(source)
    source = fixture()
    source["predictions"][0]["full_features"]["score"] = float("nan")
    with pytest.raises(ValueError):
        ranking.rank_consensus(source)


def test_cli_atomic_no_overwrite_and_no_inference_dependencies(tmp_path):
    source, output = tmp_path / "source.json", tmp_path / "output.json"
    source.write_text(json.dumps(fixture()))
    source_before = source.read_bytes()
    command = [sys.executable, "-S", str(SCRIPTS / "rank_g2_consensus.py"),
               "--signal", str(source), "--output", str(output)]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    before = output.read_bytes()
    result = json.loads(before)
    expected_digest = result.pop("result_digest")
    assert ranking.digest(result) == expected_digest
    assert len(result["implementation_sha256"]) == 2
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 2
    assert output.read_bytes() == before
    assert source.read_bytes() == source_before
    assert not list(tmp_path.glob(".g2-consensus-*"))
