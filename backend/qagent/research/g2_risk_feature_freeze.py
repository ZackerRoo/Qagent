"""Freeze one offline feature-ablation pair; no database or registration path."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from qagent.factors.research_contract import FEATURE_COLUMNS
from qagent.research.factor_ablation import FACTOR_GROUPS, prepare_features, validate_input
from qagent.research.factor_experiments import (
    compare_baseline_and_lightgbm,
    current_code_revision,
    factor_research_feature_contract_digest,
)
from qagent.research.factor_shadow import factor_shadow_scorer_identity

INPUT_SHA256 = "6621a9bfa984f24a6e9e34b9c15c7ec56aa5f9d85b38d9f93c9eefbe6ec5eff8"
PROTOCOL = "g2-risk-feature-freeze-v1"
VARIANTS = {
    "full_features": FEATURE_COLUMNS,
    "without_risk": tuple(f for f in FEATURE_COLUMNS if f not in FACTOR_GROUPS["risk"]),
}


def digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_frozen_config(config: dict) -> None:
    if config.get("protocol") != PROTOCOL:
        raise ValueError("wrong protocol")
    if config.get("input_sha256") != INPUT_SHA256:
        raise ValueError("only the declared historical archive is allowed")
    if config.get("variants") != {k: list(v) for k, v in VARIANTS.items()}:
        raise ValueError("variant feature contract changed")
    if config.get("activation_allowed") is not False or config.get("decision_weight") is not False:
        raise ValueError("offline isolation flags changed")
    expected = {
        "dataset_revision": 8947, "start_date": "2021-11-01", "end_date": "2025-12-31",
        "seeds": [7, 19, 42], "model_recipe": "balanced_v1",
        "rebalance_step_sessions": 10, "horizon_sessions": 20,
        "round_trip_cost_bps": 10.0, "top_fraction": 0.1,
        "benchmark_id": "CN:000300.IDX", "minimum_history_sessions": 120,
        "max_instruments": None, "candidate_id": None, "provider_mode": "free",
        "scope": "research_shadow", "decision_weight": False, "activation_allowed": False,
    }
    if config.get("training_config") != expected:
        raise ValueError("frozen training configuration changed")


def freeze(input_path: Path, config: dict, output: Path) -> dict:
    validate_frozen_config(config)
    if output.exists():
        raise ValueError("output must be a new directory")
    output.mkdir()
    manifest = {
        "protocol": PROTOCOL, "status": "started", "config": config,
        "config_sha256": digest(config), "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "activation_allowed": False, "decision_weight": False, "candidate_registered": False,
    }
    try:
        raw = input_path.read_bytes()
        if sha256(raw).hexdigest() != INPUT_SHA256:
            raise ValueError("frozen input hash mismatch")
        payload = json.loads(raw)
        del raw
        if payload["config"] != config["training_config"]:
            raise ValueError("input training configuration changed")
        manifest["input_path"] = str(input_path.resolve())
        manifest["production_registry_written"] = False
        manifest["local_models_persisted"] = True
        frame, training = validate_input(payload)
        manifest["cohort_sha256"] = sha256(frame.to_json(orient="records", date_format="iso").encode()).hexdigest()
        prepared = prepare_features(frame, feature_stage=payload["feature_stage"])
        manifest["feature_stage"] = payload["feature_stage"]
        manifest["code_revision"] = current_code_revision()
        module_root = Path(__file__).resolve().parents[1]
        manifest["source_sha256"] = {
            name: sha256((module_root / name).read_bytes()).hexdigest()
            for name in ("research/g2_risk_feature_freeze.py", "research/factor_ablation.py",
                         "research/factor_experiments.py", "research/factor_shadow.py",
                         "factors/research_contract.py")
        }
        manifest["variants"] = {}
        split = reference = None
        for name, features in VARIANTS.items():
            print(f"Training fixed {name}: three seeds", flush=True)
            metrics, artifacts, models = compare_baseline_and_lightgbm(
                prepared, training.model_copy(update={"selected_feature_columns": features})
            )
            if artifacts["selected_feature_columns"] != list(features):
                raise RuntimeError("trainer ignored feature contract")
            if split is not None and (split != artifacts["split"] or reference != metrics["baseline"]):
                raise RuntimeError("paired split or full linear reference changed")
            split, reference = artifacts["split"], metrics["baseline"]
            entries = []
            for model in models:
                model_text = model.pop("model_text")
                if sha256(model_text.encode()).hexdigest() != model["model_digest"]:
                    raise RuntimeError("model digest mismatch")
                model_path = f"{name}-seed-{model['seed']}.txt"
                (output / model_path).write_text(model_text)
                entries.append({**model, "file": model_path})
            manifest["variants"][name] = {
                "models": entries, "feature_contract_digest": factor_research_feature_contract_digest(features),
                "scorer_identity": factor_shadow_scorer_identity(features).model_dump(),
                "best_iterations": artifacts["best_iterations"],
                "exposed_historical_test_metrics_descriptive_only": metrics,
            }
        manifest["split"] = split
        manifest["status"] = "models_frozen_forward_unvalidated"
        manifest["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["forward_observations"] = 0
        manifest["forward_metrics"] = None
        manifest["forward_window_start_policy"] = "first_XSHG_session_after_frozen_at_utc_Asia_Shanghai_date"
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        manifest["local_models_persisted"] = any(output.glob("*.txt"))
        manifest["manifest_sha256"] = digest(manifest)
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = freeze(args.input, json.loads(args.config.read_text()), args.output)
    except Exception as error:
        print(f"Freeze failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps({"status": result["status"], "manifest_sha256": result["manifest_sha256"]}))


if __name__ == "__main__":
    main()
