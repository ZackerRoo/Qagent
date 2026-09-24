import hashlib
import importlib.util
import json
from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
spec = importlib.util.spec_from_file_location("daily_wrapper", SCRIPTS / "run_daily_financial_same_day.py")
wrapper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = wrapper
spec.loader.exec_module(wrapper)


def _bundle(root, schema, file_name):
    path = root
    script = path / "scripts" / file_name
    script.parent.mkdir(parents=True)
    script.write_text("# immutable bundle file\n")
    script.chmod(0o444)
    manifest = {"schema": schema, "files": {str(script.relative_to(path)): hashlib.sha256(
        script.read_bytes()).hexdigest()}}
    (path / "manifest.json").write_text(json.dumps(manifest))
    (path / "manifest.json").chmod(0o444)
    return path


def test_observed_daily_runs_forward_and_publishes_private_attempt(tmp_path, monkeypatch):
    daily = _bundle(tmp_path / "daily", "daily-financial-bundle-v1", "collect_daily_documented_research.py")
    forward = _bundle(tmp_path / "forward", "financial-forward-bundle-v1", "run_financial_forward_research.py")
    calls = []

    class Result:
        def __init__(self, code, body):
            self.returncode, self.stdout, self.stderr = code, body, "diagnostic"

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return Result(0, json.dumps({"status": "observed" if len(calls) == 1 else "complete"}))

    monkeypatch.setattr(wrapper.subprocess, "run", run)
    code, report = wrapper.run(daily_bundle=daily, forward_bundle=forward,
                               attempt_dir=tmp_path / "attempts", backend=tmp_path / "backend")
    assert code == 0
    assert report["status"] == "completed"
    assert len(calls) == 2
    assert "run_financial_forward_research.py" in calls[1][0][2]
    assert calls[1][1]["env"]["PYTHONPATH"].startswith(str(tmp_path / "backend"))
    attempt = Path(report["attempt"])
    assert attempt.stat().st_mode & 0o777 == 0o400
    saved = json.loads(attempt.read_text())
    assert saved["daily"]["result"]["status"] == "observed"
    assert saved["forward"]["result"]["status"] == "complete"


def test_unsuccessful_daily_never_runs_forward_and_leaves_evidence(tmp_path, monkeypatch):
    daily = _bundle(tmp_path / "daily", "daily-financial-bundle-v1", "collect_daily_documented_research.py")
    forward = _bundle(tmp_path / "forward", "financial-forward-bundle-v1", "run_financial_forward_research.py")
    calls = []

    class Result:
        returncode = 75
        stdout = json.dumps({"status": "waiting_for_candidate_pool"})
        stderr = "waiting diagnosis"

    monkeypatch.setattr(wrapper.subprocess, "run", lambda *args, **kwargs: calls.append(args) or Result())
    code, report = wrapper.run(daily_bundle=daily, forward_bundle=forward,
                               attempt_dir=tmp_path / "attempts")
    assert code == 75
    assert report["status"] == "daily_not_successful"
    assert len(calls) == 1
    saved = json.loads(Path(report["attempt"]).read_text())
    assert saved["forward"] is None
    assert saved["daily"]["stderr"] == "waiting diagnosis"


def test_manifest_mismatch_prevents_daily_execution_and_is_recorded(tmp_path, monkeypatch):
    daily = _bundle(tmp_path / "daily", "daily-financial-bundle-v1", "collect_daily_documented_research.py")
    forward = _bundle(tmp_path / "forward", "financial-forward-bundle-v1", "run_financial_forward_research.py")
    (daily / "scripts/collect_daily_documented_research.py").chmod(0o644)
    (daily / "scripts/collect_daily_documented_research.py").write_text("tampered\n")
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    code, report = wrapper.run(daily_bundle=daily, forward_bundle=forward,
                               attempt_dir=tmp_path / "attempts")
    assert code == 2
    assert report["status"] == "wrapper_error"
    assert report["error"] == "ValueError"
    assert Path(report["attempt"]).is_file()


def test_peer_control_requires_explicit_wrapper_opt_in(tmp_path, monkeypatch):
    daily = _bundle(tmp_path / "daily", "daily-financial-bundle-v1", "collect_daily_documented_research.py")
    forward = _bundle(tmp_path / "forward", "financial-forward-bundle-v1", "run_financial_forward_research.py")
    commands = []

    class Result:
        returncode = 75
        stdout = json.dumps({"status": "waiting_for_candidate_pool"})
        stderr = ""

    monkeypatch.setattr(wrapper.subprocess, "run", lambda command, **kwargs: commands.append(command) or Result())
    for enabled in (False, True):
        code, _ = wrapper.run(daily_bundle=daily, forward_bundle=forward,
                              attempt_dir=tmp_path / "attempts", peer_controls=enabled)
        assert code == 75
    assert "--peer-controls" not in commands[0]
    assert commands[1].count("--peer-controls") == 1
    for command in commands:
        assert command[command.index("--budget-seconds") + 1] == "600"
        assert "--bounded-same-day" in command
        assert "--daily-frozen-industry" in command
