import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "upgrade_financial_dependency_retry", SCRIPTS / "upgrade_financial_dependency_retry.py")
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)
sys.path.pop(0)


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


def copy_bundle(bundle, required, schema):
    files = {}
    for name in required:
        source = ROOT / name
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files[name] = upgrade.base.checksum(target.read_bytes())
    raw = json.dumps({"schema": schema, "files": files}).encode()
    (bundle / "manifest.json").write_bytes(raw)
    return upgrade.base.checksum(raw)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.os, "geteuid", lambda: 0)
    monkeypatch.setattr(upgrade.os, "fchown", lambda *_args: None)

    def regular(path):
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
            raise ValueError("unsafe_file")
        return path.stat()

    def directory(path, *, private=False):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("unsafe_directory")
        if path.stat().st_mode & (0o077 if private else 0o022):
            raise ValueError("unsafe_directory_permissions")

    monkeypatch.setattr(upgrade.base, "regular", regular)
    monkeypatch.setattr(upgrade.base, "directory", directory)
    monkeypatch.setattr(upgrade.forward_base, "_regular", regular)
    monkeypatch.setattr(upgrade.forward_base, "_directory", directory)
    paths = {
        "DAILY_OLD_BUNDLE": tmp_path / "daily-v3",
        "DAILY_NEW_BUNDLE": tmp_path / "daily-v4",
        "FORWARD_OLD_BUNDLE": tmp_path / "forward-v5",
        "FORWARD_NEW_BUNDLE": tmp_path / "forward-v6",
        "DAILY_CRON": tmp_path / "cron.d/daily",
        "FORWARD_CRON": tmp_path / "cron.d/forward",
        "BACKUPS": tmp_path / "backups",
        "TIMEZONE": tmp_path / "timezone",
    }
    for name, value in paths.items():
        monkeypatch.setattr(upgrade, name, value)
    upgrade.DAILY_CRON.parent.mkdir()
    upgrade.TIMEZONE.write_text("UTC")

    old_daily = (f"# old\nSHELL=/bin/sh\nPATH=/usr/bin:/bin\n40 8 * * 1-5 user python "
                 f"{upgrade.DAILY_OLD_BUNDLE}/scripts/collect_daily_documented_research.py "
                 "--candidate-pool --period 20260630 --today-close --output-dir /daily\n").encode()
    old_forward = (f"# old\nSHELL=/bin/sh\nPATH=/usr/bin:/bin\n37 11 * * 1-5 user python "
                   f"{upgrade.FORWARD_OLD_BUNDLE}/scripts/run_financial_forward_research.py "
                   "--daily-dir /daily --signal-dir /signals\n").encode()
    monkeypatch.setattr(upgrade.daily_v3, "upgraded_cron", lambda: old_daily)
    monkeypatch.setattr(upgrade.forward_v5, "upgraded_cron", lambda: old_forward)
    monkeypatch.setattr(upgrade, "DAILY_OLD_CRON_SHA", upgrade.base.checksum(old_daily))
    monkeypatch.setattr(upgrade, "FORWARD_OLD_CRON_SHA", upgrade.base.checksum(old_forward))
    upgrade.DAILY_CRON.write_bytes(old_daily)
    upgrade.FORWARD_CRON.write_bytes(old_forward)

    daily_old = make_bundle(upgrade.DAILY_OLD_BUNDLE, upgrade.daily_v3.NEW_REQUIRED,
                            "daily-financial-bundle-v1", upgrade.base.checksum)
    daily_new = make_bundle(upgrade.DAILY_NEW_BUNDLE, upgrade.DAILY_REQUIRED,
                            "daily-financial-bundle-v1", upgrade.base.checksum)
    forward_old = make_bundle(upgrade.FORWARD_OLD_BUNDLE, upgrade.forward_v5.NEW_REQUIRED,
                              "financial-forward-bundle-v1", upgrade.base.checksum)
    forward_new = make_bundle(upgrade.FORWARD_NEW_BUNDLE, upgrade.FORWARD_REQUIRED,
                              "financial-forward-bundle-v1", upgrade.base.checksum)
    monkeypatch.setattr(upgrade, "DAILY_OLD_MANIFEST_SHA", daily_old)
    monkeypatch.setattr(upgrade, "FORWARD_OLD_MANIFEST_SHA", forward_old)
    return (daily_old, daily_new, forward_old, forward_new), old_daily, old_forward


def test_templates_are_bounded_and_keep_one_chain(deployment):
    daily = upgrade.daily_cron().decode()
    forward = upgrade.forward_cron().decode()
    assert daily.count("collect_daily_documented_research.py") == 6
    assert "40 8 * * 1-5" in daily and "10 11 * * 1-5" in daily
    assert daily.count("* * 1-5 user python") == 6
    assert daily.count("--candidate-pool") == 6
    assert forward.count("run_financial_forward_research.py") == 3
    assert "37 11 * * 1-5" in forward and "37 12 * * 1-5" in forward
    assert forward.count("* * 1-5 user python") == 3
    assert str(upgrade.DAILY_NEW_BUNDLE) in daily
    assert str(upgrade.FORWARD_NEW_BUNDLE) in forward


def test_atomic_upgrade_idempotence_and_rollback(deployment):
    args, old_daily, old_forward = deployment
    assert upgrade.install(*args)["status"] == "planned"
    result = upgrade.install(*args, execute=True)
    assert result["status"] == "upgraded" and result["started_job"] is False
    assert stat.S_IMODE(Path(result["backup"]).stat().st_mode) == 0o600
    assert upgrade.install(*args, execute=True)["status"] == "already_installed"
    assert upgrade.rollback(result["backup"], *args)["status"] == "rollback_planned"
    assert upgrade.rollback(result["backup"], *args, execute=True)["status"] == "rolled_back"
    assert upgrade.DAILY_CRON.read_bytes() == old_daily
    assert upgrade.FORWARD_CRON.read_bytes() == old_forward


def test_changed_cron_fails_closed(deployment):
    args, _, old_forward = deployment
    upgrade.DAILY_CRON.write_text("operator changed\n")
    with pytest.raises(ValueError, match="existing_cron_mismatch"):
        upgrade.install(*args, execute=True)
    assert upgrade.FORWARD_CRON.read_bytes() == old_forward


def test_upgrade_mixed_state_is_compatible_and_retry_resumes_daily(deployment, monkeypatch):
    args, old_daily, _ = deployment
    original_write = upgrade._write_atomic
    failed = False

    def fail_daily_once(path, raw):
        nonlocal failed
        if path == upgrade.DAILY_CRON and not failed:
            failed = True
            raise OSError("simulated daily write interruption")
        original_write(path, raw)

    monkeypatch.setattr(upgrade, "_write_atomic", fail_daily_once)
    with pytest.raises(OSError, match="daily write interruption"):
        upgrade.install(*args, execute=True)
    assert upgrade.DAILY_CRON.read_bytes() == old_daily
    assert upgrade.FORWARD_CRON.read_bytes() == upgrade.forward_cron()
    assert upgrade.inspect(*args)[2] == upgrade.STATE_UPGRADE_PARTIAL

    monkeypatch.setattr(upgrade, "_write_atomic", original_write)
    preview = upgrade.install(*args)
    assert preview["status"] == "resume_planned"
    result = upgrade.install(*args, execute=True)
    assert result["status"] == "resumed_upgrade"
    assert upgrade.inspect(*args)[2] == upgrade.STATE_NEW
    receipt = json.loads(Path(result["backup"]).read_text())
    assert receipt["daily_rollback_sha256"] == upgrade.DAILY_OLD_CRON_SHA
    assert receipt["forward_rollback_sha256"] == upgrade.FORWARD_OLD_CRON_SHA
    assert upgrade.rollback(result["backup"], *args)["status"] == "rollback_planned"


def test_rollback_second_write_failure_leaves_compatible_state_and_retry_resumes(
        deployment, monkeypatch):
    args, old_daily, old_forward = deployment
    installed = upgrade.install(*args, execute=True)
    original_write = upgrade._write_atomic
    failed = False

    def fail_forward_once(path, raw):
        nonlocal failed
        if path == upgrade.FORWARD_CRON and not failed:
            failed = True
            raise OSError("simulated forward rollback interruption")
        original_write(path, raw)

    monkeypatch.setattr(upgrade, "_write_atomic", fail_forward_once)
    with pytest.raises(OSError, match="forward rollback interruption"):
        upgrade.rollback(installed["backup"], *args, execute=True)
    assert upgrade.DAILY_CRON.read_bytes() == old_daily
    assert upgrade.FORWARD_CRON.read_bytes() == upgrade.forward_cron()
    assert upgrade.inspect(*args)[2] == upgrade.STATE_UPGRADE_PARTIAL

    monkeypatch.setattr(upgrade, "_write_atomic", original_write)
    preview = upgrade.rollback(installed["backup"], *args)
    assert preview["status"] == "rollback_resume_planned"
    result = upgrade.rollback(installed["backup"], *args, execute=True)
    assert result["status"] == "resumed_rollback"
    assert upgrade.DAILY_CRON.read_bytes() == old_daily
    assert upgrade.FORWARD_CRON.read_bytes() == old_forward
    assert upgrade.inspect(*args)[2] == upgrade.STATE_OLD


def test_daily_new_forward_old_is_rejected(deployment):
    args, _, old_forward = deployment
    upgrade.DAILY_CRON.write_bytes(upgrade.daily_cron())
    upgrade.FORWARD_CRON.write_bytes(old_forward)
    with pytest.raises(ValueError, match="unsafe_daily_new_forward_old_state"):
        upgrade.install(*args)


@pytest.mark.parametrize("entry_bundle_name", ["daily-v4", "forward-v6"])
def test_helper_preview_runs_from_each_isolated_manifest_bundle(
        tmp_path, entry_bundle_name):
    daily_old_bundle = tmp_path / "daily-v3"
    daily_new_bundle = tmp_path / "daily-v4"
    forward_old_bundle = tmp_path / "forward-v5"
    forward_new_bundle = tmp_path / "forward-v6"
    daily_old_manifest = copy_bundle(
        daily_old_bundle, upgrade.daily_v3.NEW_REQUIRED, "daily-financial-bundle-v1")
    daily_new_manifest = copy_bundle(
        daily_new_bundle, upgrade.DAILY_REQUIRED, "daily-financial-bundle-v1")
    forward_old_manifest = copy_bundle(
        forward_old_bundle, upgrade.forward_v5.NEW_REQUIRED, "financial-forward-bundle-v1")
    forward_new_manifest = copy_bundle(
        forward_new_bundle, upgrade.FORWARD_REQUIRED, "financial-forward-bundle-v1")
    cron_dir = tmp_path / "cron.d"
    cron_dir.mkdir()
    daily_cron = cron_dir / "daily"
    forward_cron = cron_dir / "forward"
    timezone = tmp_path / "timezone"
    timezone.write_text("UTC\n")
    backups = tmp_path / "backups"
    old_daily = (f"# old\nSHELL=/bin/sh\nPATH=/usr/bin:/bin\n40 8 * * 1-5 user python "
                 f"{daily_old_bundle}/scripts/collect_daily_documented_research.py "
                 "--candidate-pool --period 20260630 --today-close --output-dir /daily\n").encode()
    old_forward = (f"# old\nSHELL=/bin/sh\nPATH=/usr/bin:/bin\n37 11 * * 1-5 user python "
                   f"{forward_old_bundle}/scripts/run_financial_forward_research.py "
                   "--daily-dir /daily --signal-dir /signals\n").encode()
    daily_cron.write_bytes(old_daily)
    forward_cron.write_bytes(old_forward)
    entry = tmp_path / entry_bundle_name
    outside = tmp_path / "outside"
    outside.mkdir()
    code = f"""
import json
from pathlib import Path
import stat
import sys
sys.path.insert(0, {str(entry / 'scripts')!r})
import upgrade_financial_dependency_retry as helper
forbidden = {{{str(ROOT)!r}, {str(SCRIPTS)!r}, {str(ROOT / 'backend')!r}}}
assert not forbidden.intersection(sys.path)
def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
        raise ValueError('unsafe_file')
    return path.stat()
def directory(path, *, private=False):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError('unsafe_directory')
    if path.stat().st_mode & (0o077 if private else 0o022):
        raise ValueError('unsafe_directory_permissions')
helper.base.regular = regular
helper.base.directory = directory
helper.forward_base._regular = regular
helper.forward_base._directory = directory
helper.DAILY_OLD_BUNDLE = Path({str(daily_old_bundle)!r})
helper.DAILY_NEW_BUNDLE = Path({str(daily_new_bundle)!r})
helper.FORWARD_OLD_BUNDLE = Path({str(forward_old_bundle)!r})
helper.FORWARD_NEW_BUNDLE = Path({str(forward_new_bundle)!r})
helper.DAILY_CRON = Path({str(daily_cron)!r})
helper.FORWARD_CRON = Path({str(forward_cron)!r})
helper.BACKUPS = Path({str(backups)!r})
helper.TIMEZONE = Path({str(timezone)!r})
old_daily = {old_daily!r}
old_forward = {old_forward!r}
helper.daily_v3.upgraded_cron = lambda: old_daily
helper.forward_v5.upgraded_cron = lambda: old_forward
helper.DAILY_OLD_CRON_SHA = helper.base.checksum(old_daily)
helper.FORWARD_OLD_CRON_SHA = helper.base.checksum(old_forward)
helper.DAILY_OLD_MANIFEST_SHA = {daily_old_manifest!r}
helper.FORWARD_OLD_MANIFEST_SHA = {forward_old_manifest!r}
sys.argv = ['helper',
    '--expected-daily-new-manifest-sha256', {daily_new_manifest!r},
    '--expected-forward-new-manifest-sha256', {forward_new_manifest!r}]
raise SystemExit(helper.main())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code], cwd=outside,
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout)["status"] == "planned"
    assert daily_cron.read_bytes() == old_daily
    assert forward_cron.read_bytes() == old_forward
    assert not list(entry.rglob("__pycache__"))


def test_new_bundle_manifest_still_rejects_extra_files(deployment):
    args, _, _ = deployment
    (upgrade.DAILY_NEW_BUNDLE / "extra.py").write_text("# not manifested\n")
    with pytest.raises(ValueError, match="unmanifested_bundle_files"):
        upgrade.install(*args)
