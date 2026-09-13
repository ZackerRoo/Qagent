"""Synthetic complete-case diagnostics never constitute forward evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("audit_g2_complete_cohort", SCRIPTS / "audit_g2_complete_cohort.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
sys.path.pop(0)


def seal(signal):
    source = signal["source"]
    source.pop("source_digest", None)
    source["source_digest"] = audit.digest(source)
    signal["source_digest"] = source["source_digest"]
    signal.pop("result_digest", None)
    signal["result_digest"] = audit.digest(signal)
    return signal


def fixture(incomplete=(0,)):
    config = json.loads(audit.CONFIG.read_text())
    features = config["variants"]
    keys = [f"SYNTHETIC:{index}" for index in range(5)]
    rankings = [{"instrument_id": key, "research_features": {
        feature: None if index in incomplete and feature == "momentum_20" else 1.0
        for feature in features["full_features"]}} for index, key in enumerate(keys)]
    rows = []
    for index, key in enumerate(keys):
        row = {"instrument_id": key, "industry": None}
        for name, rank in (("full_features", index + 1), ("without_risk", (5, 2, 3, 1, 4)[index])):
            row[name] = {"rank": rank, "score": float(6 - rank),
                         "feature_coverage": (len(features[name]) - (index in incomplete)) / len(features[name])}
        rows.append(row)
    return seal({
        "protocol": "g2-risk-feature-forward-v1", "status": "ready", "reasons": [],
        "decision_weight": False, "activation_allowed": False, "signal_date": "2026-09-11",
        "config_sha256": audit.digest(config), "scan_job_id": "synthetic", "predictions": rows,
        "source": {"protocol": "g2-forward-source-v1", "stage": "ranking_finalized_before_job_completion",
                   "provider": "free", "signal_date": "2026-09-11", "scan_job_id": "synthetic",
                   "decision_weight": False, "activation_allowed": False, "rankings": rankings,
                   "industries": {},
                   "stock_ids": keys, "research_universe": keys,
                   "items": [{"instrument_id": key, "latest_trade_date": "2026-09-11"} for key in keys]},
        "coverage": {"source_rows": 5, "scored_rows": 5, "eligible_fraction": 1.0,
                     "industry_nonmissing": 0,
                     "excluded_all_features_missing": [], "stale_or_missing_trade_dates": [],
                     "variant_joint_complete": {name: 5 - len(incomplete) for name in features},
                     "feature_nonmissing": {feature: 5 - len(incomplete) if feature == "momentum_20" else 5
                                            for feature in features["full_features"]}},
    })


def test_complete_case_same_set_preserves_full_consensus_and_input():
    signal = fixture()
    before = copy.deepcopy(signal)
    original = audit.rank_consensus(signal)
    result = audit.audit(signal)
    expected = [row["instrument_id"] for row in original["rankings"] if row["instrument_id"] != "SYNTHETIC:0"]
    assert result["filtered_orders"]["consensus"] == expected
    assert result["complete_rows"] == 4
    assert result["excluded_from_diagnostic"] == [{"instrument_id": "SYNTHETIC:0", "missing_features": {
        "full_features": ["momentum_20"], "without_risk": ["momentum_20"]}}]
    for order in result["filtered_orders"].values():
        assert set(order) == set(result["complete_instrument_ids"])
    original_ranks = {row["instrument_id"]: row["consensus_rank"] for row in original["rankings"]}
    assert result["selections"]["top5"]["lanes"]["consensus"]["original_ranks"] == {
        key: original_ranks[key] for key in expected}
    assert signal == before
    assert result["activation_allowed"] is False
    assert result["forward_metrics"] is None
    assert result["policy"]["changes_frozen_eligibility"] is False


def test_zero_complete_rows_is_explicit_not_error_or_original_fallback():
    result = audit.audit(fixture(incomplete=tuple(range(5))))
    assert result["status"] == "no_complete_rows"
    assert result["complete_rows"] == 0
    for selection in result["selections"].values():
        assert selection["effective_k"] == 0
        assert all(lane["instrument_ids"] == [] and lane["industry_distribution"] is None
                   for lane in selection["lanes"].values())


@pytest.mark.parametrize("damage", [
    lambda s: s["predictions"][0]["full_features"].pop("feature_coverage"),
    lambda s: s["predictions"][0]["full_features"].update(feature_coverage=True),
    lambda s: s["predictions"][0]["full_features"].update(feature_coverage=1.0),
    lambda s: s["coverage"]["variant_joint_complete"].update(full_features=5),
    lambda s: s["source"]["rankings"][0].update(instrument_id="SYNTHETIC:1"),
    lambda s: s["source"]["rankings"][0].update(research_features=[]),
    lambda s: s["source"]["items"][0].update(latest_trade_date="2026-09-10"),
    lambda s: s["source"].update(activation_allowed=True),
    lambda s: s.update(config_sha256="wrong"),
    lambda s: s["source"].update(stock_ids=[]),
    lambda s: s["predictions"][0].update(industry="different"),
    lambda s: s["coverage"].update(industry_nonmissing=1),
])
def test_conflicts_and_malformed_metadata_block(damage):
    signal = fixture()
    damage(signal)
    with pytest.raises(ValueError):
        audit.audit(seal(signal))


def test_source_digest_must_match_both_layers():
    signal = fixture()
    signal["source"]["provider"] = "different"
    signal.pop("result_digest")
    signal["result_digest"] = audit.digest(signal)
    with pytest.raises(ValueError, match="source digest"):
        audit.audit(signal)
    signal = fixture()
    signal["source_digest"] = "wrong"
    signal.pop("result_digest")
    signal["result_digest"] = audit.digest(signal)
    with pytest.raises(ValueError, match="source digest"):
        audit.audit(signal)


def test_numeric_string_semantics_matches_collector_and_order_invariant():
    signal = fixture()
    baseline = audit.audit(signal)
    signal["source"]["rankings"][1]["research_features"]["momentum_20"] = "1.0"
    signal["source"]["rankings"].reverse()
    signal["predictions"].reverse()
    changed = audit.audit(seal(signal))
    assert changed["filtered_orders"] == baseline["filtered_orders"]
    assert changed["complete_universe_digest"] == baseline["complete_universe_digest"]


def test_cli_no_inference_atomic_and_nonoverwrite(tmp_path):
    source, output = tmp_path / "signal.json", tmp_path / "audit.json"
    source.write_text(json.dumps(fixture()))
    before = source.read_bytes()
    command = [sys.executable, "-S", str(SCRIPTS / "audit_g2_complete_cohort.py"),
               "--signal", str(source), "--output", str(output)]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    encoded = output.read_bytes()
    result = json.loads(encoded)
    claimed = result.pop("result_digest")
    assert claimed == audit.digest(result)
    assert result["source_file_sha256"] == audit.sha256(before).hexdigest()
    assert len(result["implementation_sha256"]) == 3
    assert subprocess.run(command, capture_output=True).returncode == 2
    assert output.read_bytes() == encoded
    assert source.read_bytes() == before
    assert not list(tmp_path.glob(".g2-consensus-*"))
