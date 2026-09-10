from copy import deepcopy
import json
from pathlib import Path

import pytest

from qagent.research.g2_risk_feature_freeze import digest, freeze, validate_frozen_config
from qagent.research.factor_experiments import FactorResearchConfig


@pytest.fixture
def config():
    return json.loads((Path(__file__).resolve().parents[2] / "docs/research/g2-risk-feature-freeze-20260910-config.json").read_text())


def test_config_reuses_legacy_training_but_does_not_register_subset(config):
    validate_frozen_config(config)
    FactorResearchConfig.model_validate(config["training_config"])
    with pytest.raises(ValueError, match="full feature contract"):
        FactorResearchConfig.model_validate({**config["training_config"], "selected_feature_columns": config["variants"]["without_risk"]})


@pytest.mark.parametrize("change", ["seed", "features", "activation", "input", "cost"])
def test_protocol_rejects_unfrozen_changes(config, change):
    altered = deepcopy(config)
    if change == "seed":
        altered["training_config"]["seeds"] = [8]
    elif change == "features":
        altered["variants"]["without_risk"].append("volatility_20")
    elif change == "activation":
        altered["activation_allowed"] = True
    elif change == "input":
        altered["input_sha256"] = "0" * 64
    else:
        altered["training_config"]["round_trip_cost_bps"] = 0
    with pytest.raises(ValueError):
        validate_frozen_config(altered)


def test_corrupt_input_archives_failure_and_never_overwrites(tmp_path, config):
    source = tmp_path / "input.json"
    source.write_text("{}")
    output = tmp_path / "artifact"
    with pytest.raises(ValueError, match="hash mismatch"):
        freeze(source, config, output)
    manifest = json.loads((output / "manifest.json").read_text())
    reported = manifest.pop("manifest_sha256")
    assert digest(manifest) == reported
    assert manifest["status"] == "failed"
    assert manifest["candidate_registered"] is False
    assert manifest["local_models_persisted"] is False
    assert not list(output.glob("*.txt"))
    with pytest.raises(ValueError, match="new directory"):
        freeze(source, config, output)
    assert json.loads((output / "manifest.json").read_text())["status"] == "failed"


def test_missing_input_keeps_failed_manifest(tmp_path, config):
    output = tmp_path / "artifact"
    with pytest.raises(FileNotFoundError):
        freeze(tmp_path / "absent.json", config, output)
    assert json.loads((output / "manifest.json").read_text())["error"]["type"] == "FileNotFoundError"
