"""Execution-time identities. Digests describe provenance, never attest performance."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

IDENTITY_KEY = "recommendation_alignment_identity"
SCHEMA = "recommendation-alignment-identity-v2"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def selection_digest():
    from qagent.recommendations import selection
    return hashlib.sha256(inspect.getsource(selection).encode()).hexdigest()


def build_alignment_identity(*, source, effective_config, missing_components=(), decision_inputs=None):
    """Snapshot actual code, registry, dependencies and effective settings at execution.

    The whole package is deliberately conservative: an unrelated code change can
    prevent equality. Secrets and machine paths are excluded from settings.
    Data observations are distinct from model identity and are not compared here.
    """
    from qagent.backtesting.experiment import _runtime_dependency_digest
    from qagent.config import get_settings
    from qagent.strategies.registry import default_strategy_registry

    root = Path(__file__).resolve().parents[1]
    files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(root.rglob("*.py"))}
    settings = get_settings().model_dump(mode="json")
    settings = {key: settings[key] for key in (
        "a_share_enhanced_data_enabled", "a_share_enhanced_max_cards",
        "a_share_enhanced_min_interval_seconds", "a_share_enhanced_timeout_seconds",
        "a_share_enhanced_cache_ttl_hours")}
    registry = sorted((item.model_dump(mode="json") for item in default_strategy_registry().all()),
                      key=lambda item: item["strategy_id"])
    payload = {
        "schema": SCHEMA, "source": source,
        "selection_implementation_digest": selection_digest(),
        "model_identity": {"package_source_digest": digest(files),
                           "strategy_registry_digest": digest(registry),
                           "runtime_dependency_digest": _runtime_dependency_digest()},
        "effective_config": {**effective_config, "runtime_settings": settings},
        "decision_inputs": decision_inputs or {},
        "missing_components": sorted(set(missing_components)),
    }
    payload["manifest_digest"] = digest(payload)
    return payload


def capture_live_identity(job, *, top_cards_limit, governance_context, feedback_center):
    """Research provenance must never stop the operational scan on capture errors."""
    try:
        return build_alignment_identity(
            source="live_scan_cards_order",
            effective_config={"provider_mode": job.provider,
                              "batch_size": job.batch_size, "include_etfs": job.include_etfs,
                              "top_cards_limit": top_cards_limit, "max_per_strategy": 2, "top_n": 10,
                              "universe": "live_tradable_catalog",
                              "adaptive_context_policy": "final_policy_inputs_and_recent_recommendation_feedback"},
            decision_inputs={"governance_context_digest": digest(governance_context.model_dump(mode="json") if governance_context else None),
                             "feedback_digest": digest(feedback_center.model_dump(mode="json") if feedback_center else None)},
            missing_components=["live_enrichment_and_calibration_artifacts_not_fully_frozen"],
        )
    except Exception:
        return {"schema": SCHEMA, "source": "live_scan_cards_order",
                "missing_components": ["identity_capture_error"]}


def identity_is_valid(value):
    return (isinstance(value, dict) and value.get("schema") == SCHEMA
            and isinstance(value.get("source"), str) and bool(value["source"])
            and isinstance(value.get("effective_config"), dict) and bool(value["effective_config"])
            and isinstance(value.get("missing_components"), list)
            and isinstance(value.get("model_identity"), dict)
            and all(isinstance(value["model_identity"].get(key), str) and bool(value["model_identity"][key])
                    for key in ("package_source_digest", "strategy_registry_digest", "runtime_dependency_digest"))
            and bool(value.get("selection_implementation_digest"))
            and value.get("manifest_digest") == digest({k: v for k, v in value.items() if k != "manifest_digest"}))


def compare_identities(actual, expected):
    differences = []
    for label, value in (("prospective", actual), ("historical", expected)):
        if not identity_is_valid(value):
            differences.append(f"{label}_identity_manifest_missing_or_invalid")
        elif value["missing_components"]:
            differences.extend(f"{label}_identity_incomplete:{item}" for item in value["missing_components"])
    if identity_is_valid(actual) and identity_is_valid(expected):
        def compare(left, right, prefix=""):
            for key in sorted(set(left) | set(right)):
                if key in {"manifest_digest", "missing_components"}:
                    continue
                path = f"{prefix}{key}"
                a, b = left.get(key), right.get(key)
                if isinstance(a, dict) and isinstance(b, dict):
                    compare(a, b, path + ":")
                elif a != b:
                    differences.append(path)
        compare(actual, expected)
    return differences
