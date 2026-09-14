import copy
import fcntl
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SPEC = importlib.util.spec_from_file_location(
    "deploy_relay_runtime", Path(__file__).resolve().parents[2] / "scripts/deploy_relay_runtime.py"
)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def test_environment_preserves_g2_and_quotes_credential():
    original = b"# header\nQAGENT_G2_CAPTURE_DIR=/research\nexport QAGENT_TUSHARE_RELAY_KEY=old\nQAGENT_TUSHARE_RELAY_MARKET_ENABLED=false\nOTHER=yes"
    key = "key ' $(touch /not-executed)"
    result = deploy.relay_environment(original, key)
    assert result.startswith(b"# header\nQAGENT_G2_CAPTURE_DIR=/research\nOTHER=yes\n")
    assert result.count(b"QAGENT_TUSHARE_RELAY_KEY=") == 1
    assert result.count(b"QAGENT_TUSHARE_RELAY_MARKET_ENABLED=true") == 1
    assert shlex.split(result.decode().splitlines()[-1])[0] == "QAGENT_TUSHARE_RELAY_KEY=" + key


@pytest.mark.parametrize("key", ["", "a\nb", "a\x00b", "a\rb", "x" * 4097])
def test_bad_credential_rejected_without_exposing_value(key):
    with pytest.raises(ValueError, match="^invalid Relay credential$"):
        deploy.relay_environment(b"", key)


def test_dual_credentials_replace_only_approved_keys():
    original = b"KEEP=yes\nQAGENT_DATAHUBCO_ENABLED=false\nexport QAGENT_DATAHUBCO_KEY=old\n"
    basic = "basic ' $(not-executed)"
    payload = json.dumps({"relay_key": "relay-test", "datahubco_key": basic})
    result = deploy.credential_environment(original, io.StringIO(payload), enable_datahubco=True)
    assert result.startswith(b"KEEP=yes\n")
    assert result.count(b"QAGENT_DATAHUBCO_KEY=") == 1
    assert b"QAGENT_DATAHUBCO_ALLOW_INSECURE_HTTP=true\n" in result
    assert shlex.split(result.decode().splitlines()[-1])[0] == "QAGENT_DATAHUBCO_KEY=" + basic
    assert deploy.credential_environment(b"", io.StringIO("relay-test\n")) == deploy.relay_environment(b"", "relay-test")


@pytest.mark.parametrize("payload", ["bad-json", "[]", '{"relay_key":"secret"}',
    json.dumps({"relay_key": "secret", "datahubco_key": "bad\nkey"}),
    json.dumps({"relay_key": None, "datahubco_key": "secret"}),
    json.dumps({"relay_key": "secret", "datahubco_key": "非ASCII"}),
])
def test_dual_credentials_fail_safely(payload):
    with pytest.raises(ValueError, match="^invalid deployment credentials$"):
        deploy.credential_environment(b"KEEP=yes", io.StringIO(payload), enable_datahubco=True)


def test_atomic_environment_preserves_mode_and_rejects_concurrent_update(tmp_path, monkeypatch):
    env = tmp_path / "env"
    env.write_bytes(b"old")
    env.chmod(0o640)
    monkeypatch.setattr(deploy, "ENV", env)
    deploy.atomic_environment(b"old", b"new")
    assert env.read_bytes() == b"new"
    assert env.stat().st_mode & 0o777 == 0o640
    with pytest.raises(AssertionError, match="concurrently"):
        deploy.atomic_environment(b"old", b"wrong")
    assert env.read_bytes() == b"new"
    assert list(tmp_path.iterdir()) == [env]


@pytest.mark.parametrize("active", ["job", "lease", "in_flight", None])
def test_idle_checks_jobs_leases_and_runtime(tmp_path, monkeypatch, active):
    db = tmp_path / "qagent.db"
    with sqlite3.connect(db) as connection:
        for table in deploy.JOBS:
            connection.execute(f"CREATE TABLE {table}(status TEXT)")
        for table, column in [
            ("runtime_leases", "expires_at"),
            ("historical_dataset_leases", "lease_expires_at"),
        ]:
            connection.execute(f"CREATE TABLE {table}({column} TEXT)")
        if active == "job":
            connection.execute("INSERT INTO full_market_scan_jobs VALUES ('queued')")
        if active == "lease":
            connection.execute("INSERT INTO runtime_leases VALUES ('2999-01-01')")
    monkeypatch.setattr(deploy, "DB", str(db))
    monkeypatch.setattr(
        deploy, "state", lambda: {"payload": {"runtime": {"in_flight": active == "in_flight"}}}
    )
    if active:
        with pytest.raises(AssertionError):
            deploy.idle()
    else:
        deploy.idle()


@pytest.fixture
def rollout(tmp_path, monkeypatch):
    releases = tmp_path / "releases"
    sha = "a" * 40
    release = releases / sha
    old = releases / ("b" * 40)
    (release / "backend/.venv/bin").mkdir(parents=True)
    (release / "backend/.venv/bin/python").touch()
    (release / "frontend/dist").mkdir(parents=True)
    (release / "frontend/dist/index.html").touch()
    current = tmp_path / "current"
    current.symlink_to(old)
    env = tmp_path / "env"
    original = b"QAGENT_G2_CAPTURE_DIR=/research\nOTHER=yes\n"
    env.write_bytes(original)
    models = tmp_path / "models"
    models.mkdir()
    records = []
    for index in range(6):
        name = str(index)
        (models / name).write_bytes(b"model")
        records.append({"file": name, "model_digest": hashlib.sha256(b"model").hexdigest()})
    manifest = json.dumps({"variants": {"all": {"models": records}}}).encode()
    (models / "manifest.json").write_bytes(manifest)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    settings = {str(index): index for index in range(16)}
    initial = {
        "enabled": True,
        "revision": 1,
        "payload": {"settings": settings, "runtime": {"in_flight": False, "run_count": 7}},
    }
    state = copy.deepcopy(initial)
    events = []
    monkeypatch.setattr(deploy, "CURRENT", current)
    monkeypatch.setattr(deploy, "ENV", env)
    monkeypatch.setattr(deploy, "MODELS", models)
    monkeypatch.setattr(deploy, "CAPTURE", str(models))
    monkeypatch.setattr(deploy, "MANIFEST_FILE_SHA256", hashlib.sha256(manifest).hexdigest())
    monkeypatch.setattr(
        deploy, "Path", lambda value: releases if value == "/opt/qagent/releases" else Path(value)
    )
    monkeypatch.setattr(
        deploy.sys, "argv", ["deploy", str(release), "--expected-sha", sha, "--execute"]
    )
    monkeypatch.setattr(deploy.sys, "stdin", io.StringIO("private-test-key\n"))
    monkeypatch.setattr(deploy.os, "geteuid", lambda: 0)
    monkeypatch.setattr(deploy.tempfile, "mkdtemp", lambda **kwargs: str(evidence))
    monkeypatch.setattr(
        deploy.subprocess,
        "check_output",
        lambda args, **kwargs: sha + "\n" if "rev-parse" in args else "",
    )
    monkeypatch.setattr(deploy, "state", lambda: copy.deepcopy(state))
    monkeypatch.setattr(deploy, "idle", lambda: events.append("idle"))
    monkeypatch.setattr(deploy, "paper_tick_quiescent", lambda: events.append("paper_idle"))
    monkeypatch.setattr(deploy, "ledger", lambda **kwargs: {"hash": "stable"})
    monkeypatch.setattr(deploy, "healthy", lambda: events.append("healthy"))
    monkeypatch.setattr(deploy, "RESUME_COMMITTED", False)

    def stop(request, **kwargs):
        events.append("pause")
        state["enabled"] = False
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def script(path, name, *args):
        events.append(name)
        if name == "switch_linux_release.sh":
            current.unlink()
            current.symlink_to(args[0])

    def restore(saved, actual_settings, ledger):
        assert actual_settings == initial["payload"]["settings"]
        assert ledger == {"hash": "stable"}
        events.append("resume")
        state.update(copy.deepcopy(saved))

    monkeypatch.setattr(deploy.urllib.request, "urlopen", stop)
    monkeypatch.setattr(deploy, "script", script)
    monkeypatch.setattr(deploy, "restore", restore)
    return SimpleNamespace(
        env=env,
        original=original,
        state=state,
        initial=initial,
        events=events,
        current=current,
        release=release,
        old=old,
        evidence=evidence,
    )


def test_rollout_success_preserves_state_and_private_backup(rollout, capsys):
    deploy.main()
    assert rollout.current.resolve() == rollout.release
    assert rollout.state == rollout.initial
    assert rollout.events.index("idle") < rollout.events.index("pause")
    assert rollout.events[-1] == "resume"
    assert rollout.env.read_bytes().startswith(rollout.original)
    backup = rollout.evidence / "qagent.env.before"
    assert backup.read_bytes() == rollout.original
    assert backup.stat().st_mode & 0o777 == 0o600
    assert "private-test-key" not in capsys.readouterr().out


def test_failure_before_resume_rolls_back_code_and_env(rollout, monkeypatch):
    calls = []

    def health():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("health failure")

    monkeypatch.setattr(deploy, "healthy", health)
    with pytest.raises(RuntimeError, match="health failure"):
        deploy.main()
    assert rollout.current.resolve() == rollout.old
    assert rollout.env.read_bytes() == rollout.original
    assert rollout.state == rollout.initial
    assert (rollout.evidence / "rollback.json").exists()


@pytest.mark.parametrize("fail", [False, True])
def test_dual_service_rollout_and_rollback(rollout, monkeypatch, capsys, fail):
    monkeypatch.setattr(deploy.sys, "argv", deploy.sys.argv + ["--enable-datahubco"])
    monkeypatch.setattr(deploy.sys, "stdin", io.StringIO(json.dumps({
        "relay_key": "private-relay", "datahubco_key": "private-basic"})))
    calls = []
    def health():
        calls.append(1)
        if fail and len(calls) == 1:
            raise RuntimeError("health failure")
    monkeypatch.setattr(deploy, "healthy", health)
    if fail:
        with pytest.raises(RuntimeError, match="health failure"):
            deploy.main()
        assert rollout.env.read_bytes() == rollout.original
        assert rollout.current.resolve() == rollout.old
    else:
        deploy.main()
        assert b"QAGENT_DATAHUBCO_ENABLED=true" in rollout.env.read_bytes()
        assert b"QAGENT_DATAHUBCO_ALLOW_INSECURE_HTTP=true" in rollout.env.read_bytes()
        assert b"QAGENT_TUSHARE_RELAY_MARKET_ENABLED=true" in rollout.env.read_bytes()
    assert rollout.state == rollout.initial
    assert (rollout.evidence / "qagent.env.before").read_bytes() == rollout.original
    assert "private-" not in capsys.readouterr().out


@pytest.mark.parametrize("fail", [False, True])
def test_documented_research_flag_and_rollback(rollout, monkeypatch, fail):
    monkeypatch.setattr(deploy.sys, "argv", deploy.sys.argv + ["--enable-documented-research"])
    calls = []
    def health():
        calls.append(1)
        if fail and len(calls) == 1:
            raise RuntimeError("health failure")
    monkeypatch.setattr(deploy, "healthy", health)
    if fail:
        with pytest.raises(RuntimeError, match="health failure"):
            deploy.main()
        assert rollout.env.read_bytes() == rollout.original
    else:
        deploy.main()
        assert b"QAGENT_TUSHARE_RELAY_RESEARCH_ENABLED=true\n" in rollout.env.read_bytes()
    assert rollout.state == rollout.initial
    original = b"KEEP=yes\nexport QAGENT_TUSHARE_RELAY_RESEARCH_ENABLED=false\n"
    expected = b"KEEP=yes\nQAGENT_TUSHARE_RELAY_RESEARCH_ENABLED=true\n"
    assert deploy.documented_research_environment(original) == expected
    assert deploy.documented_research_environment(expected) == expected


def test_idle_failure_never_pauses(rollout, monkeypatch):
    def busy():
        raise AssertionError("busy")

    monkeypatch.setattr(deploy, "idle", busy)
    with pytest.raises(AssertionError, match="busy"):
        deploy.main()
    assert "pause" not in rollout.events
    assert rollout.env.read_bytes() == rollout.original


def test_no_rollback_after_scheduler_resume_committed(rollout, monkeypatch):
    def restore(*args):
        deploy.RESUME_COMMITTED = True
        raise RuntimeError("post-resume failure")

    monkeypatch.setattr(deploy, "restore", restore)
    with pytest.raises(RuntimeError, match="post-resume failure"):
        deploy.main()
    assert rollout.current.resolve() == rollout.release
    assert not (rollout.evidence / "rollback.json").exists()


def test_paper_tick_environment_preserves_research_and_credentials():
    original = (
        b"QAGENT_TUSHARE_RELAY_KEY=private\nQAGENT_G2_CAPTURE_DIR=/research\n"
        b"export QAGENT_PAPER_UPDATE_SCHEDULER_ENABLED=false\n"
        b"QAGENT_PAPER_UPDATE_INTERVAL_SECONDS=1800\nOTHER=value"
    )
    updated = deploy.paper_tick_environment(original)
    assert updated.startswith(
        b"QAGENT_TUSHARE_RELAY_KEY=private\nQAGENT_G2_CAPTURE_DIR=/research\nOTHER=value\n"
    )
    assert updated.count(b"QAGENT_PAPER_UPDATE_SCHEDULER_ENABLED=true") == 1
    assert updated.count(b"QAGENT_PAPER_UPDATE_INTERVAL_SECONDS=600") == 1
    assert deploy.paper_tick_environment(updated) == updated


def test_rollout_opt_in_preserves_1800_settings(rollout, monkeypatch):
    rollout.state["payload"]["settings"].pop("0")
    rollout.state["payload"]["settings"]["interval_seconds"] = 1800
    rollout.initial["payload"]["settings"] = copy.deepcopy(rollout.state["payload"]["settings"])
    monkeypatch.setattr(deploy.sys, "argv", deploy.sys.argv + ["--enable-paper-tick"])
    deploy.main()
    assert b"QAGENT_PAPER_UPDATE_SCHEDULER_ENABLED=true" in rollout.env.read_bytes()
    assert rollout.state["payload"]["settings"]["interval_seconds"] == 1800


def test_rollout_opt_in_rejects_changed_research_interval_before_pause(rollout, monkeypatch):
    monkeypatch.setattr(deploy.sys, "argv", deploy.sys.argv + ["--enable-paper-tick"])
    with pytest.raises(AssertionError, match="research interval"):
        deploy.main()
    assert "pause" not in rollout.events


@pytest.mark.parametrize("status, enabled", [("running", True), ("stopping", False)])
def test_busy_tick_after_master_stop_never_kills_or_rolls_back(rollout, monkeypatch, status, enabled):
    def busy():
        raise RuntimeError("paper tick still active")

    monkeypatch.setattr(deploy, "paper_tick_quiescent", busy)
    original_script = deploy.script

    def guarded_script(path, name, *args):
        if name == "disable_linux_runit.sh":
            busy()
        original_script(path, name, *args)

    monkeypatch.setattr(deploy, "script", guarded_script)
    with pytest.raises(RuntimeError, match="paper tick still active"):
        deploy.main()
    assert "disable_linux_runit.sh" not in rollout.events
    assert "resume" not in rollout.events
    assert rollout.state["enabled"] is False
    assert rollout.env.read_bytes() == rollout.original


def test_kernel_writer_lock_is_nonblocking_and_held_until_shutdown_finishes(tmp_path, monkeypatch):
    db = tmp_path / "account.db"
    db.touch()
    monkeypatch.setattr(deploy, "DB", str(db))
    path = str(db) + ".paper-writer.lock"
    with deploy.idle_paper_writer():
        with pytest.raises(RuntimeError, match="writer busy"):
            with deploy.idle_paper_writer():
                pytest.fail("busy writer must not be acquired")
        with open(path, "rb") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with deploy.idle_paper_writer():
        pass


def test_fresh_lock_uses_db_ownership_and_existing_inode_is_preserved(tmp_path, monkeypatch):
    db = tmp_path / "account.db"
    db.touch()
    monkeypatch.setattr(deploy, "DB", str(db))
    chown = Mock(wraps=deploy.os.fchown)
    monkeypatch.setattr(deploy.os, "fchown", chown)
    path = Path(str(db) + ".paper-writer.lock")
    with deploy.idle_paper_writer():
        inode = path.stat().st_ino
        assert path.stat().st_uid == db.stat().st_uid
        assert path.stat().st_gid == db.stat().st_gid
    chown.assert_called_once()
    with deploy.idle_paper_writer():
        assert path.stat().st_ino == inode
    chown.assert_called_once()


@pytest.mark.parametrize("shutdown", ["backend", "services"])
def test_busy_manual_writer_blocks_actual_shutdown_entry(tmp_path, monkeypatch, shutdown):
    db = tmp_path / "account.db"
    db.touch()
    monkeypatch.setattr(deploy, "DB", str(db))
    monkeypatch.setattr(deploy, "paper_tick_quiescent", Mock())
    run = Mock()
    monkeypatch.setattr(deploy, "run", run)
    with deploy.idle_paper_writer():
        with pytest.raises(RuntimeError, match="writer busy"):
            if shutdown == "backend":
                deploy.backend_down()
            else:
                deploy.script(tmp_path, "disable_linux_runit.sh")
    run.assert_not_called()


@pytest.mark.parametrize("status, enabled, accepted", [
    ("stopped", False, True), ("stopping", False, False), ("running", True, False),
])
def test_paper_status_requires_positive_stopped_confirmation(tmp_path, monkeypatch, status, enabled, accepted):
    env = tmp_path / "env"
    env.write_bytes(b"QAGENT_PAPER_UPDATE_SCHEDULER_ENABLED=true\n")
    monkeypatch.setattr(deploy, "ENV", env)
    payload = {"configured": True, "state": {"status": status, "enabled": enabled}}
    monkeypatch.setattr(deploy.urllib.request, "urlopen", lambda *a, **kw: io.StringIO(json.dumps(payload)))
    if accepted:
        deploy.paper_tick_quiescent()
    else:
        with pytest.raises(AssertionError, match="still active"):
            deploy.paper_tick_quiescent()


@pytest.mark.parametrize("configured", [True, False])
def test_old_status_404_only_allowed_when_not_configured(tmp_path, monkeypatch, configured):
    env = tmp_path / "env"
    env.write_bytes(b"QAGENT_PAPER_UPDATE_SCHEDULER_ENABLED=true\n" if configured else b"")
    monkeypatch.setattr(deploy, "ENV", env)

    def missing(*args, **kwargs):
        raise deploy.urllib.error.HTTPError("url", 404, "not found", {}, None)

    monkeypatch.setattr(deploy.urllib.request, "urlopen", missing)
    if configured:
        with pytest.raises(RuntimeError, match="quiescence unavailable"):
            deploy.paper_tick_quiescent()
    else:
        deploy.paper_tick_quiescent()


@pytest.mark.parametrize("changed_ledger", [False, True])
def test_restore_real_scheduler_cas_and_ledger_guard(tmp_path, monkeypatch, changed_ledger):
    db = tmp_path / "state.db"
    settings = {"interval_seconds": 1800}
    stopped = {"settings": settings, "runtime": {"in_flight": False, "next_run_at": None}}
    saved = {
        "enabled": True,
        "payload": {
            "settings": settings,
            "runtime": {
                "in_flight": False,
                "run_count": 10,
                "next_run_at": "2026-09-14T01:30:00Z",
            },
        },
    }
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE automation_scheduler_state(state_id TEXT, enabled INTEGER, settings_json TEXT, revision INTEGER, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO automation_scheduler_state VALUES ('default', 0, ?, 42, '')",
            (json.dumps(stopped),),
        )
    monkeypatch.setattr(deploy, "DB", str(db))
    monkeypatch.setattr(deploy, "backend_down", Mock())
    monkeypatch.setattr(deploy, "idle", Mock())
    monkeypatch.setattr(deploy, "run", Mock())
    monkeypatch.setattr(deploy, "healthy", Mock())
    monkeypatch.setattr(deploy, "ledger", lambda: {"hash": "changed" if changed_ledger else "same"})
    monkeypatch.setattr(deploy, "RESUME_COMMITTED", False)
    if changed_ledger:
        with pytest.raises(AssertionError, match="ledger changed"):
            deploy.restore(saved, settings, {"hash": "same"})
        assert deploy.state()["enabled"] is False
        assert deploy.RESUME_COMMITTED is False
        deploy.run.assert_not_called()
    else:
        deploy.restore(saved, settings, {"hash": "same"})
        assert deploy.state() == {**saved, "revision": 43}
        assert deploy.RESUME_COMMITTED is True
        deploy.run.assert_called_once()
