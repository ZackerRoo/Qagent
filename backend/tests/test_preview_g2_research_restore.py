from __future__ import annotations

import json
from pathlib import Path
import shutil

from scripts.preview_g2_research_restore import CANONICAL_MANIFEST, LOCAL_MODELS, preview, verify_models


def _copy_models(destination: Path) -> None:
    destination.mkdir(parents=True)
    for path in LOCAL_MODELS.glob("*.txt"):
        shutil.copyfile(path, destination / path.name)
    shutil.copyfile(LOCAL_MODELS / "manifest.json", destination / "manifest.json")


def test_local_frozen_models_match_pinned_manifest() -> None:
    report = verify_models(LOCAL_MODELS, CANONICAL_MANIFEST)
    assert report["valid"] is True
    assert len(report["files"]) == 6
    assert all(item["matches_manifest"] for item in report["files"].values())


def test_preview_reports_missing_research_paths_without_writes(tmp_path: Path) -> None:
    home = tmp_path / "qagent"
    cron = tmp_path / "qagent-g2-forward"
    report = preview(LOCAL_MODELS, CANONICAL_MANIFEST, home, cron)
    assert report["status"] == "rehydration_incomplete"
    assert report["checks"]["target_models_verified"] is False
    assert report["checks"]["capture_env_matches_source"] is False
    assert report["checks"]["cron_file_present"] is False
    assert report["checks"]["cron_matches_persistent_paths"] is False
    assert report["scheduler_activation_verified"] is False
    assert not home.exists()


def test_preview_requires_capture_and_cron_to_match_persistent_paths(tmp_path: Path) -> None:
    home = tmp_path / "qagent"
    research = home / "research-data"
    _copy_models(research / "g2-frozen-v1")
    (research / "g2-forward-sources").mkdir()
    (research / "g2-forward-results").mkdir()
    collector = home / "current/backend/qagent/research/g2_forward_schedule.py"
    collector.parent.mkdir(parents=True)
    collector.write_text("# existing collector\n")
    env = home / "config/qagent.env"
    env.parent.mkdir()
    env.write_text("QAGENT_G2_CAPTURE_DIR=/var/lib/qagent-research/g2-forward-sources\n")
    cron = tmp_path / "qagent-g2-forward"
    cron.write_text("# disabled\n")
    report = preview(LOCAL_MODELS, CANONICAL_MANIFEST, home, cron)
    assert report["target_models"]["valid"] is True
    assert report["checks"]["cron_file_present"] is True
    assert report["checks"]["capture_env_matches_source"] is False
    assert report["checks"]["cron_matches_persistent_paths"] is False

    source = research / "g2-forward-sources"
    output = research / "g2-forward-results"
    env.write_text(f"QAGENT_G2_CAPTURE_DIR={source}\n")
    cron.write_text("0,30 8-15 * * 1-5 user python -B -m qagent.research.g2_forward_schedule "
                    f"--source-dir {source} --frozen-dir {research / 'g2-frozen-v1'} "
                    f"--output-dir {output}\n")
    before = (research / "g2-frozen-v1/manifest.json").read_bytes()
    report = preview(LOCAL_MODELS, CANONICAL_MANIFEST, home, cron)
    assert report["status"] == "ready_for_opt_in_review"
    assert report["checks"]["cron_file_present"] is True
    assert report["scheduler_activation_verified"] is False
    assert report["natural_forward_validated"] is False
    assert (research / "g2-frozen-v1/manifest.json").read_bytes() == before


def test_tampered_model_and_manifest_are_rejected(tmp_path: Path) -> None:
    models = tmp_path / "models"
    _copy_models(models)
    model = models / "full_features-seed-7.txt"
    model.write_bytes(model.read_bytes() + b"\ncorrupt")
    report = verify_models(models, CANONICAL_MANIFEST)
    assert report["valid"] is False
    assert "model_digest_mismatch:full_features-seed-7.txt" in report["errors"]

    shutil.copyfile(LOCAL_MODELS / model.name, model)
    manifest = models / "manifest.json"
    payload = json.loads(manifest.read_text())
    payload["activation_allowed"] = True
    manifest.write_text(json.dumps(payload))
    report = verify_models(models, CANONICAL_MANIFEST)
    assert report["valid"] is False
    assert "research_isolation_flags_mismatch" in report["errors"]
