#!/usr/bin/env python3
"""Read-only G2 research restore preflight for a persistent Qagent home.

This does not copy models, install cron, enable capture, or run a collector.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LOCAL_MODELS = ROOT / "data/archives/g2-risk-feature-ablation-20260910-frozen-v1"
CANONICAL_MANIFEST = ROOT / "docs/research/g2-risk-feature-freeze-20260910-manifest.json"
MANIFEST_DIGEST = "b6bbb9a44dbb61fb4ec8879034ef6232e2cd32f07356a1407ffad71c69c9e77b"
VARIANTS = {"full_features", "without_risk"}
SEEDS = {7, 19, 42}
CAPTURE_KEY = "QAGENT_G2_CAPTURE_DIR"


def _digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def verify_models(directory: Path, canonical_manifest: Path) -> dict:
    """Verify the six source/target model bytes against the pinned manifest."""
    errors: list[str] = []
    if directory.is_symlink() or not directory.is_dir():
        return {"valid": False, "errors": ["model_directory_missing_or_symlink"], "files": {}}
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return {"valid": False, "errors": ["manifest_missing_or_symlink"], "files": {}}
    try:
        raw = manifest_path.read_bytes()
        if raw != canonical_manifest.read_bytes():
            errors.append("manifest_differs_from_canonical_archive")
        manifest = json.loads(raw)
        unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        if manifest.get("manifest_sha256") != MANIFEST_DIGEST or _digest(unsigned) != MANIFEST_DIGEST:
            errors.append("manifest_digest_mismatch")
        if (manifest.get("status") != "models_frozen_forward_unvalidated"
                or manifest.get("activation_allowed") is not False
                or manifest.get("decision_weight") is not False):
            errors.append("research_isolation_flags_mismatch")
        variants = manifest.get("variants", {})
        if set(variants) != VARIANTS:
            errors.append("variant_set_mismatch")
        records = [(name, record) for name in sorted(VARIANTS)
                   for record in variants.get(name, {}).get("models", [])]
        if (len(records) != 6 or any(not isinstance(record, dict) for _, record in records)
                or any({record.get("seed") for variant, record in records if variant == name} != SEEDS
                       for name in VARIANTS)):
            errors.append("model_seed_set_mismatch")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"valid": False, "errors": [f"manifest_unreadable_or_invalid:{type(exc).__name__}"], "files": {}}
    files = {}
    seen: set[str] = set()
    for variant, record in records:
        if not isinstance(record, dict):
            errors.append("unsafe_or_duplicate_model_record")
            continue
        name = record.get("file")
        expected = record.get("model_digest")
        if (not isinstance(name, str) or name != f"{variant}-seed-{record.get('seed')}.txt"
                or name in seen or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            errors.append("unsafe_or_duplicate_model_record")
            continue
        seen.add(name)
        path = directory / name
        if path.is_symlink() or not path.is_file():
            errors.append(f"model_missing_or_symlink:{name}")
            continue
        try:
            actual = sha256(path.read_bytes()).hexdigest()
        except OSError:
            errors.append(f"model_unreadable:{name}")
            continue
        files[name] = {"sha256": actual, "matches_manifest": actual == expected}
        if actual != expected:
            errors.append(f"model_digest_mismatch:{name}")
    if len(seen) != 6:
        errors.append("six_distinct_models_required")
    return {"valid": not errors, "errors": errors, "files": files,
            "manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest()}


def _capture_value(env_file: Path) -> str | None:
    if env_file.is_symlink() or not env_file.is_file():
        return None
    values = []
    try:
        lines = env_file.read_text().splitlines()
    except (OSError, UnicodeError):
        return None
    for line in lines:
        match = re.fullmatch(r"\s*(?:export\s+)?QAGENT_G2_CAPTURE_DIR=(?:\"([^\"]*)\"|'([^']*)'|([^\s#]*))\s*", line)
        if match:
            values.append(next(value for value in match.groups() if value is not None))
    return values[0] if len(values) == 1 else None


def preview(source_dir: Path, canonical_manifest: Path, target_home: Path, cron_file: Path) -> dict:
    source = verify_models(source_dir, canonical_manifest)
    research = target_home / "research-data"
    target_models = research / "g2-frozen-v1"
    source_capture = research / "g2-forward-sources"
    output = research / "g2-forward-results"
    collector = target_home / "current/backend/qagent/research/g2_forward_schedule.py"
    env_file = target_home / "config/qagent.env"
    target = verify_models(target_models, canonical_manifest) if target_models.exists() else {
        "valid": False, "errors": ["model_directory_missing"], "files": {}}
    configured_capture = _capture_value(env_file)
    cron_file_present = cron_file.is_file() and not cron_file.is_symlink()
    try:
        cron_text = cron_file.read_text() if cron_file_present else ""
    except (OSError, UnicodeError):
        cron_text = ""
    cron_lines = [line for line in cron_text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    cron_ok = any("qagent.research.g2_forward_schedule" in line
                  and f"--source-dir {source_capture}" in line
                  and f"--frozen-dir {target_models}" in line
                  and f"--output-dir {output}" in line for line in cron_lines)
    checks = {
        "persistent_home": target_home.is_dir() and not target_home.is_symlink(),
        "target_models_verified": target["valid"],
        "source_directory_present": source_capture.is_dir() and not source_capture.is_symlink(),
        "output_directory_present": output.is_dir() and not output.is_symlink(),
        "collector_code_present": collector.is_file() and not collector.is_symlink(),
        "capture_env_matches_source": configured_capture == str(source_capture),
        "cron_file_present": cron_file_present,
        "cron_matches_persistent_paths": cron_ok,
    }
    captured_files = sum(1 for _ in source_capture.glob("*.json")) if checks["source_directory_present"] else 0
    return {
        "protocol": "g2-research-restore-preview-v1", "mode": "read_only",
        "source_models": source, "target_models": target,
        "expected_paths": {"home": str(target_home), "models": str(target_models),
                           "source_capture": str(source_capture), "output": str(output),
                           "capture_env": str(env_file), "collector_code": str(collector),
                           "cron": str(cron_file)},
        "checks": checks,
        "source_capture_files": captured_files,
        "source_capture_observed": captured_files > 0,
        "status": "invalid_local_models" if not source["valid"] else (
            "ready_for_opt_in_review" if all(checks.values()) else "rehydration_incomplete"),
        "natural_forward_validated": False,
        "scheduler_activation_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=LOCAL_MODELS)
    parser.add_argument("--canonical-manifest", type=Path, default=CANONICAL_MANIFEST)
    parser.add_argument("--target-home", type=Path, default=Path("/home/luozhenkun/qagent"))
    parser.add_argument("--cron-file", type=Path, default=Path("/etc/cron.d/qagent-g2-forward"))
    args = parser.parse_args()
    report = preview(args.source_dir, args.canonical_manifest, args.target_home, args.cron_file)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ready_for_opt_in_review" else (2 if report["status"] == "invalid_local_models" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
