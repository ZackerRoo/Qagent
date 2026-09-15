import importlib.util
import fcntl
import json
from pathlib import Path
import stat
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daily = load("upgrade_daily_financial_research_v3")
forward = load("upgrade_financial_forward_research_v5")
chain = load("upgrade_financial_candidate_pool_chain")


def validators(monkeypatch, regular_modules, directory_modules):
    def regular(path):
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
            raise ValueError("unsafe_file")
        return path.stat()

    def directory(path, *, private=False):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("unsafe_directory")
        if path.stat().st_mode & (0o077 if private else 0o022):
            raise ValueError("unsafe_directory_permissions")

    for module, name in regular_modules:
        monkeypatch.setattr(module, name, regular)
    for module, name in directory_modules:
        monkeypatch.setattr(module, name, directory)


def make_bundle(bundle, required, schema, checksum):
    files = {}
    for name in required:
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# reviewed\n")
        files[name] = checksum(path.read_bytes())
    raw = json.dumps({"schema": schema, "files": files}).encode()
    (bundle / "manifest.json").write_bytes(raw)
    return checksum(raw)


@pytest.fixture
def daily_deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(daily.os, "geteuid", lambda: 0)
    monkeypatch.setattr(daily.os, "fchown", lambda *_: None)
    validators(monkeypatch, [(daily.base, "regular")], [(daily.base, "directory")])
    for name, value in (("OLD_BUNDLE", tmp_path / "daily-v2"),
                        ("NEW_BUNDLE", tmp_path / "daily-v3"),
                        ("CRON", tmp_path / "cron.d/daily"),
                        ("BACKUPS", tmp_path / "daily-backups"),
                        ("TIMEZONE", tmp_path / "timezone")):
        monkeypatch.setattr(daily, name, value)
    daily.CRON.parent.mkdir(exist_ok=True)
    daily.BACKUPS.mkdir(mode=0o700)
    daily.TIMEZONE.write_text("UTC")
    old_symbols = " ".join(f"--symbol {symbol}" for symbol in daily.v2.NEW_SYMBOLS)
    old = (f"40 8 * * 1-5 luozhenkun python -B {daily.OLD_BUNDLE}/scripts/"
           f"collect_daily_documented_research.py --base-url http://127.0.0.1:8000 "
           f"--source datahubco {old_symbols} --period 20260630 --today-close "
           f"--budget-seconds 600 --output-dir /var/lib/qagent-research/daily-financial\n").encode()
    monkeypatch.setattr(daily.v2, "upgraded_cron", lambda: old)
    monkeypatch.setattr(daily, "OLD_CRON_SHA", daily.base.checksum(old))
    daily.CRON.write_bytes(old)
    old_manifest = make_bundle(daily.OLD_BUNDLE, daily.v2.NEW_REQUIRED,
                               "daily-financial-bundle-v1", daily.base.checksum)
    new_manifest = make_bundle(daily.NEW_BUNDLE, daily.NEW_REQUIRED,
                               "daily-financial-bundle-v1", daily.base.checksum)
    monkeypatch.setattr(daily, "OLD_MANIFEST_SHA", old_manifest)
    return (daily.OLD_CRON_SHA, old_manifest, new_manifest), old


@pytest.fixture
def forward_deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(forward.os, "geteuid", lambda: 0)
    monkeypatch.setattr(forward.os, "fchown", lambda *_: None)
    validators(monkeypatch, [(forward.v4, "_regular")], [(forward.v4, "_directory")])
    for name, value in (("OLD_BUNDLE", tmp_path / "forward-v4"),
                        ("NEW_BUNDLE", tmp_path / "forward-v5"),
                        ("CRON", tmp_path / "cron.d/forward"),
                        ("BACKUPS", tmp_path / "forward-backups"),
                        ("TIMEZONE", tmp_path / "timezone")):
        monkeypatch.setattr(forward, name, value)
    forward.CRON.parent.mkdir(exist_ok=True)
    forward.BACKUPS.mkdir(mode=0o700)
    forward.TIMEZONE.write_text("UTC")
    old = (f"37 11 * * 1-5 luozhenkun python -B {forward.OLD_BUNDLE}/scripts/"
           "run_financial_forward_research.py --daily-dir /var/lib/qagent-research/daily-financial "
           "--baseline-dir /var/lib/qagent-research/g2-forward-results/signals "
           "--signal-dir /var/lib/qagent-research/financial-forward-signals "
           "--evaluation-dir /var/lib/qagent-research/financial-forward-evaluations "
           "--run-dir /var/lib/qagent-research/financial-forward-runs "
           "--db /var/lib/qagent/qagent.db --provider-mode free --budget-seconds 300\n").encode()
    monkeypatch.setattr(forward.v4, "cron_bytes", lambda: old)
    monkeypatch.setattr(forward, "OLD_CRON_SHA", forward.v4.checksum(old))
    forward.CRON.write_bytes(old)
    old_manifest = make_bundle(forward.OLD_BUNDLE, forward.v4.REQUIRED,
                               "financial-forward-bundle-v1", forward.v4.checksum)
    new_manifest = make_bundle(forward.NEW_BUNDLE, forward.NEW_REQUIRED,
                               "financial-forward-bundle-v1", forward.v4.checksum)
    monkeypatch.setattr(forward, "OLD_MANIFEST_SHA", old_manifest)
    return (forward.OLD_CRON_SHA, old_manifest, new_manifest), old


def test_pinned_templates_change_only_authorized_arguments():
    assert daily.OLD_CRON_SHA == "4a80db491109badc90190fe9bdd45bf39160118ebe17733a1e934a8cf9b88b9d"
    changed = daily.upgraded_cron()
    assert changed.count(b"--candidate-pool") == 1 and b"--symbol " not in changed
    assert b"40 8 * * 1-5 luozhenkun" in changed
    assert b"--period 20260630 --today-close --budget-seconds 600" in changed
    assert forward.OLD_CRON_SHA == "a570099924da2e4540a58aa7fe3952c689fc609ce821a13df21de569fe29d81f"
    forward_changed = forward.upgraded_cron()
    assert b"37 11 * * 1-5 luozhenkun" in forward_changed
    assert b"--db /var/lib/qagent/qagent.db --provider-mode free --budget-seconds 300" in forward_changed


def test_daily_v3_atomic_idempotent_and_rollback(daily_deployment):
    args, old = daily_deployment
    assert daily.install(*args)["status"] == "planned"
    result = daily.install(*args, execute=True)
    assert result["status"] == "upgraded" and result["started_job"] is False
    assert b"--candidate-pool" in daily.CRON.read_bytes()
    backup = Path(result["backup"])
    assert backup.read_bytes() == old and stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert daily.install(*args, execute=True)["status"] == "already_installed"
    assert daily.rollback(backup, args[1], args[2])["status"] == "rollback_planned"
    assert daily.rollback(backup, args[1], args[2], execute=True)["status"] == "rolled_back_to_v2"
    assert daily.CRON.read_bytes() == old
    assert daily.install(*args, execute=True)["status"] == "upgraded"
    assert daily.rollback(None, args[1], args[2], execute=True)["status"] == "rolled_back_to_v2"
    assert daily.CRON.read_bytes() == old


def test_forward_v5_atomic_idempotent_and_rollback(forward_deployment):
    args, old = forward_deployment
    assert forward.install(*args)["status"] == "planned"
    result = forward.install(*args, execute=True)
    assert result["status"] == "upgraded" and result["started_job"] is False
    assert str(forward.NEW_BUNDLE).encode() in forward.CRON.read_bytes()
    backup = Path(result["backup"])
    assert backup.read_bytes() == old and stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert forward.install(*args, execute=True)["status"] == "already_installed"
    assert forward.rollback(backup, args[1], args[2], execute=True)["status"] == "rolled_back_to_v4"
    assert forward.CRON.read_bytes() == old
    assert forward.install(*args, execute=True)["status"] == "upgraded"
    assert forward.rollback(None, args[1], args[2], execute=True)["status"] == "rolled_back_to_v4"
    assert forward.CRON.read_bytes() == old


def test_changed_crons_fail_closed(daily_deployment, forward_deployment):
    daily_args, _ = daily_deployment
    forward_args, _ = forward_deployment
    daily.CRON.write_text("operator changed\n")
    forward.CRON.write_text("operator changed\n")
    with pytest.raises(ValueError):
        daily.install(*daily_args, execute=True)
    with pytest.raises(ValueError):
        forward.install(*forward_args, execute=True)


def test_changed_bundles_and_concurrent_installs_fail_closed(
        daily_deployment, forward_deployment):
    daily_args, daily_old = daily_deployment
    forward_args, forward_old = forward_deployment
    next(path for path in daily.NEW_BUNDLE.rglob("*.py")).write_text("changed\n")
    next(path for path in forward.NEW_BUNDLE.rglob("*.py")).write_text("changed\n")
    with pytest.raises(ValueError):
        daily.install(*daily_args, execute=True)
    with pytest.raises(ValueError):
        forward.install(*forward_args, execute=True)
    assert daily.CRON.read_bytes() == daily_old and forward.CRON.read_bytes() == forward_old

    # Restore the files and manifests by rebuilding their isolated fixture bundles.
    daily_args = (daily_args[0], daily_args[1], make_bundle(
        daily.NEW_BUNDLE, daily.NEW_REQUIRED, "daily-financial-bundle-v1", daily.base.checksum))
    forward_args = (forward_args[0], forward_args[1], make_bundle(
        forward.NEW_BUNDLE, forward.NEW_REQUIRED, "financial-forward-bundle-v1", forward.v4.checksum))
    with (daily.BACKUPS / ".install.lock").open("a+b") as daily_lock:
        fcntl.flock(daily_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            daily.install(*daily_args, execute=True)
    with (forward.BACKUPS / ".upgrade.lock").open("a+b") as forward_lock:
        fcntl.flock(forward_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            forward.install(*forward_args, execute=True)


def configure_chain(tmp_path, monkeypatch):
    calls = []
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    monkeypatch.setattr(chain.daily, "BACKUPS", backups)
    monkeypatch.setattr(chain.base, "directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chain.daily, "OLD_CRON_SHA", "a" * 64)
    monkeypatch.setattr(chain.daily, "OLD_MANIFEST_SHA", "b" * 64)
    monkeypatch.setattr(chain.forward, "OLD_CRON_SHA", "c" * 64)
    monkeypatch.setattr(chain.forward, "OLD_MANIFEST_SHA", "d" * 64)
    return calls, backups


def test_chain_preview_and_success_are_consumer_first(tmp_path, monkeypatch):
    calls, _ = configure_chain(tmp_path, monkeypatch)
    def forward_install(*_args, **kwargs):
        calls.append(("forward", kwargs.get("execute", False)))
        return {"status": "upgraded" if kwargs.get("execute") else "planned",
                "backup": "forward-backup"}

    def daily_install(*_args, **kwargs):
        calls.append(("daily", kwargs.get("execute", False)))
        return {"status": "upgraded" if kwargs.get("execute") else "planned"}

    monkeypatch.setattr(chain.forward, "install", forward_install)
    monkeypatch.setattr(chain.daily, "install", daily_install)
    preview = chain.upgrade("e" * 64, "f" * 64)
    assert preview["order"] == ["forward_v5", "daily_v3"]
    assert calls == [("forward", False), ("daily", False)]
    calls.clear()
    result = chain.upgrade("e" * 64, "f" * 64, execute=True)
    assert result["order"] == ["forward_v5", "daily_v3"]
    assert calls == [("forward", True), ("daily", True)]


def test_chain_forward_failure_never_calls_daily(tmp_path, monkeypatch):
    calls, _ = configure_chain(tmp_path, monkeypatch)
    def forward_install(*_args, **kwargs):
        calls.append(("forward", kwargs.get("execute", False)))
        raise ValueError("forward failed")

    monkeypatch.setattr(chain.forward, "install", forward_install)
    monkeypatch.setattr(chain.daily, "install", lambda *_args, **_kwargs: calls.append(("daily", True)))
    with pytest.raises(ValueError, match="forward failed"):
        chain.upgrade("e" * 64, "f" * 64, execute=True)
    assert calls == [("forward", True)]


@pytest.mark.parametrize("forward_status", ["upgraded", "already_installed"])
def test_chain_daily_failure_rolls_forward_back(
        tmp_path, monkeypatch, forward_deployment, forward_status):
    forward_args, old = forward_deployment
    calls, _ = configure_chain(tmp_path, monkeypatch)
    monkeypatch.setattr(chain.forward, "OLD_CRON_SHA", forward_args[0])
    monkeypatch.setattr(chain.forward, "OLD_MANIFEST_SHA", forward_args[1])
    if forward_status == "already_installed":
        assert forward.install(*forward_args, execute=True)["status"] == "upgraded"

    def forward_install(*_args, **kwargs):
        calls.append(("forward", kwargs.get("execute", False)))
        return forward.install(*forward_args, **kwargs)

    def daily_install(*_args, **kwargs):
        calls.append(("daily", kwargs.get("execute", False)))
        raise ValueError("daily failed")

    def forward_rollback(backup, *_args, **kwargs):
        calls.append(("rollback", backup, kwargs.get("execute", False)))
        return forward.rollback(backup, forward_args[1], forward_args[2], **kwargs)

    monkeypatch.setattr(chain.forward, "install", forward_install)
    monkeypatch.setattr(chain.daily, "install", daily_install)
    monkeypatch.setattr(chain.forward, "rollback", forward_rollback)
    with pytest.raises(ValueError, match="daily failed"):
        chain.upgrade("e" * 64, "f" * 64, execute=True)
    assert calls[:2] == [("forward", True), ("daily", True)]
    assert calls[2][0] == "rollback" and calls[2][2] is True
    assert (calls[2][1] is None) == (forward_status == "already_installed")
    assert forward.CRON.read_bytes() == old
