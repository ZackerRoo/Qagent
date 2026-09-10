import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess

from test_paper_observation import _database, _note


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/archive_paper_observation.py"
spec = importlib.util.spec_from_file_location("observation_archive", SCRIPT)
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


def test_success_immutable_archive_and_database_unchanged(tmp_path):
    db = _database(tmp_path, [_note(evidence_version="v2")])
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    output = tmp_path / "observations"
    code, first = wrapper.archive(db, output)
    assert code == 0
    original = first.read_bytes()
    record = json.loads(original)
    assert record["observation"]["samples"][0]["verdict"] == "matched"
    assert record["exit_code"] == 0
    assert record["started_at"] <= record["finished_at"]
    assert len(record["code"]["python_source_sha256"]) == 64
    assert record["source"]["source_digest"]
    assert stat.S_IMODE(first.stat().st_mode) == 0o444
    _, second = wrapper.archive(db, output)
    assert first != second
    assert first.read_bytes() == original
    assert json.loads(second.read_text())["source"] == record["source"]
    assert len(list(output.glob("*.json"))) == 2
    assert not list(output.glob(".pending-*"))
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_blocked_report_is_retained(tmp_path):
    db = _database(tmp_path, ["[paper_replay_evidence:v2]{bad}"])
    code, artifact = wrapper.archive(db, tmp_path / "observations")
    assert code == 2
    result = json.loads(artifact.read_text())
    assert result["exit_code"] == 2
    assert result["observation"]["summary"]["gate"] == "blocked"


def test_missing_database_error_is_archived_without_creating_database(tmp_path):
    db = tmp_path / "missing.db"
    code, artifact = wrapper.archive(db, tmp_path / "observations")
    assert code == 1
    result = json.loads(artifact.read_text())
    assert result["exit_code"] == 1
    assert "FileNotFoundError" in result["stderr"]
    assert not db.exists()


def test_lock_skips_overlapping_execution(tmp_path):
    output = tmp_path / "observations"
    output.mkdir()
    with (output / ".observation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert wrapper.archive(tmp_path / "missing.db", output) == (75, None)
    assert not list(output.glob("*.json"))


def test_unexpected_failure_is_archived(tmp_path, monkeypatch):
    def fail():
        raise RuntimeError("fixture failure")
    monkeypatch.setattr(wrapper, "code_identity", fail)
    code, artifact = wrapper.archive(tmp_path / "missing.db", tmp_path / "observations")
    assert code == 1
    assert json.loads(artifact.read_text())["error"] == "RuntimeError"


def test_timeout_is_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(wrapper, "code_identity", lambda: {"git_head": "fixture"})
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("fixture", 1)
    monkeypatch.setattr(wrapper.subprocess, "run", timeout)
    code, artifact = wrapper.archive(tmp_path / "missing.db", tmp_path / "observations")
    assert code == 1
    assert json.loads(artifact.read_text())["error"] == "TimeoutExpired"
