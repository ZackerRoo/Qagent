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
    "upgrade_financial_matched_control",
    SCRIPTS / "upgrade_financial_matched_control.py",
)
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
        files[name] = upgrade.previous.base.checksum(target.read_bytes())
    raw = json.dumps({"schema": schema, "files": files}).encode()
    (bundle / "manifest.json").write_bytes(raw)
    return upgrade.previous.base.checksum(raw)


def current_daily_cron(bundle):
    command = (
        f"luozhenkun python -B {bundle}/scripts/collect_daily_documented_research.py "
        "--candidate-pool --period 20260630 --today-close --output-dir /daily"
    )
    schedules = ("40 8", "10 9", "40 9", "10 10", "40 10", "10 11")
    return (
        "# daily\nSHELL=/bin/sh\nPATH=/usr/bin:/bin\n"
        + "".join(f"{slot} * * 1-5 {command}\n" for slot in schedules)
    ).encode()


def current_forward_cron(bundle):
    command = (
        f"luozhenkun python -B {bundle}/scripts/run_financial_forward_research.py "
        "--daily-dir /daily --signal-dir /signals"
    )
    schedules = ("37 11", "7 12", "37 12")
    return (
        "# forward\nSHELL=/bin/sh\nPATH=/usr/bin:/bin\n"
        + "".join(f"{slot} * * 1-5 {command}\n" for slot in schedules)
    ).encode()


def test_reviewed_production_baselines_and_target_templates_are_pinned():
    assert upgrade.previous.base.checksum(upgrade.old_daily_cron()) == upgrade.DAILY_OLD_CRON_SHA
    assert upgrade.previous.base.checksum(upgrade.old_forward_cron()) == upgrade.FORWARD_OLD_CRON_SHA
    assert upgrade.previous.base.checksum(upgrade.daily_cron()) == (
        "45ee481081dd3e3ba06f6df02bbd55f07944d1b896ff54e08f8ffcff10ff62f8"
    )
    assert upgrade.previous.base.checksum(upgrade.forward_cron()) == (
        "1305eb71acc82ecb3730433044f218815c40ec23aff004a602db3f622cc9dec3"
    )


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.os, "geteuid", lambda: 0)
    monkeypatch.setattr(upgrade.os, "fchown", lambda *_args: None)

    def regular(path):
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
            raise ValueError("unsafe_file")
        return path.stat()

    def directory(path, *, private=False):
        path = Path(path)
        if path.is_symlink() or not path.is_dir():
            raise ValueError("unsafe_directory")
        if path.stat().st_mode & (0o077 if private else 0o022):
            raise ValueError("unsafe_directory_permissions")

    monkeypatch.setattr(upgrade.previous.base, "regular", regular)
    monkeypatch.setattr(upgrade.previous.base, "directory", directory)
    monkeypatch.setattr(upgrade.previous.forward_base, "_regular", regular)
    monkeypatch.setattr(upgrade.previous.forward_base, "_directory", directory)
    paths = {
        "DAILY_OLD_BUNDLE": tmp_path / "daily-v4",
        "DAILY_NEW_BUNDLE": tmp_path / "daily-v5",
        "FORWARD_OLD_BUNDLE": tmp_path / "forward-v6",
        "FORWARD_NEW_BUNDLE": tmp_path / "forward-v7",
        "DAILY_CRON": tmp_path / "cron.d/daily",
        "FORWARD_CRON": tmp_path / "cron.d/forward",
        "BACKUPS": tmp_path / "backups",
        "TIMEZONE": tmp_path / "timezone",
    }
    for name, value in paths.items():
        monkeypatch.setattr(upgrade, name, value)
    upgrade.DAILY_CRON.parent.mkdir()
    upgrade.TIMEZONE.write_text("UTC\n")
    old_daily = current_daily_cron(upgrade.DAILY_OLD_BUNDLE)
    old_forward = current_forward_cron(upgrade.FORWARD_OLD_BUNDLE)
    monkeypatch.setattr(upgrade.previous, "daily_cron", lambda: old_daily)
    monkeypatch.setattr(upgrade.previous, "forward_cron", lambda: old_forward)
    monkeypatch.setattr(upgrade, "DAILY_OLD_CRON_SHA", upgrade.previous.base.checksum(old_daily))
    monkeypatch.setattr(upgrade, "FORWARD_OLD_CRON_SHA", upgrade.previous.base.checksum(old_forward))
    upgrade.DAILY_CRON.write_bytes(old_daily)
    upgrade.FORWARD_CRON.write_bytes(old_forward)

    daily_old = make_bundle(
        upgrade.DAILY_OLD_BUNDLE,
        upgrade.previous.DAILY_REQUIRED,
        "daily-financial-bundle-v1",
        upgrade.previous.base.checksum,
    )
    daily_new = make_bundle(
        upgrade.DAILY_NEW_BUNDLE,
        upgrade.DAILY_REQUIRED,
        "daily-financial-bundle-v1",
        upgrade.previous.base.checksum,
    )
    forward_old = make_bundle(
        upgrade.FORWARD_OLD_BUNDLE,
        upgrade.previous.FORWARD_REQUIRED,
        "financial-forward-bundle-v1",
        upgrade.previous.base.checksum,
    )
    forward_new = make_bundle(
        upgrade.FORWARD_NEW_BUNDLE,
        upgrade.FORWARD_REQUIRED,
        "financial-forward-bundle-v1",
        upgrade.previous.base.checksum,
    )
    monkeypatch.setattr(upgrade, "DAILY_OLD_MANIFEST_SHA", daily_old)
    monkeypatch.setattr(upgrade, "FORWARD_OLD_MANIFEST_SHA", forward_old)
    return (daily_old, daily_new, forward_old, forward_new), old_daily, old_forward


def test_templates_only_move_existing_chain_to_immutable_target_bundles(deployment):
    daily = upgrade.daily_cron().decode()
    forward = upgrade.forward_cron().decode()
    assert daily.count("collect_daily_documented_research.py") == 6
    assert forward.count("run_financial_forward_research.py") == 3
    assert str(upgrade.DAILY_NEW_BUNDLE) in daily
    assert str(upgrade.FORWARD_NEW_BUNDLE) in forward
    assert str(upgrade.DAILY_OLD_BUNDLE) not in daily
    assert str(upgrade.FORWARD_OLD_BUNDLE) not in forward
    assert "40 8 * * 1-5" in daily and "10 11 * * 1-5" in daily
    assert "37 11 * * 1-5" in forward and "37 12 * * 1-5" in forward


def test_preview_execute_idempotence_and_rollback(deployment):
    args, old_daily, old_forward = deployment
    preview = upgrade.install(*args)
    assert preview["status"] == "planned" and preview["started_job"] is False
    assert upgrade.DAILY_CRON.read_bytes() == old_daily
    assert upgrade.FORWARD_CRON.read_bytes() == old_forward
    result = upgrade.install(*args, execute=True)
    assert result["status"] == "upgraded" and result["started_job"] is False
    assert stat.S_IMODE(Path(result["backup"]).stat().st_mode) == 0o600
    assert upgrade.install(*args, execute=True)["status"] == "already_installed"
    assert upgrade.rollback(result["backup"], *args)["status"] == "rollback_planned"
    rolled_back = upgrade.rollback(result["backup"], *args, execute=True)
    assert rolled_back["status"] == "rolled_back" and rolled_back["started_job"] is False
    assert upgrade.DAILY_CRON.read_bytes() == old_daily
    assert upgrade.FORWARD_CRON.read_bytes() == old_forward


def test_upgrade_interruption_is_consumer_first_and_resumable(deployment, monkeypatch):
    args, old_daily, _ = deployment
    original_write = upgrade._write_atomic
    failed = False

    def fail_daily_once(path, raw):
        nonlocal failed
        if path == upgrade.DAILY_CRON and not failed:
            failed = True
            raise OSError("daily interruption")
        original_write(path, raw)

    monkeypatch.setattr(upgrade, "_write_atomic", fail_daily_once)
    with pytest.raises(OSError, match="daily interruption"):
        upgrade.install(*args, execute=True)
    assert upgrade.DAILY_CRON.read_bytes() == old_daily
    assert upgrade.FORWARD_CRON.read_bytes() == upgrade.forward_cron()
    assert upgrade.inspect(*args)[2] == upgrade.STATE_UPGRADE_PARTIAL
    monkeypatch.setattr(upgrade, "_write_atomic", original_write)
    assert upgrade.install(*args)["status"] == "resume_planned"
    assert upgrade.install(*args, execute=True)["status"] == "resumed_upgrade"


def test_rollback_interruption_is_producer_first_and_resumable(deployment, monkeypatch):
    args, old_daily, old_forward = deployment
    installed = upgrade.install(*args, execute=True)
    original_write = upgrade._write_atomic
    failed = False

    def fail_forward_once(path, raw):
        nonlocal failed
        if path == upgrade.FORWARD_CRON and not failed:
            failed = True
            raise OSError("forward interruption")
        original_write(path, raw)

    monkeypatch.setattr(upgrade, "_write_atomic", fail_forward_once)
    with pytest.raises(OSError, match="forward interruption"):
        upgrade.rollback(installed["backup"], *args, execute=True)
    assert upgrade.DAILY_CRON.read_bytes() == old_daily
    assert upgrade.FORWARD_CRON.read_bytes() == upgrade.forward_cron()
    monkeypatch.setattr(upgrade, "_write_atomic", original_write)
    assert upgrade.rollback(installed["backup"], *args)["status"] == "rollback_resume_planned"
    assert upgrade.rollback(installed["backup"], *args, execute=True)["status"] == "resumed_rollback"
    assert upgrade.DAILY_CRON.read_bytes() == old_daily
    assert upgrade.FORWARD_CRON.read_bytes() == old_forward


def test_new_producer_with_old_consumer_is_rejected(deployment):
    args, _, old_forward = deployment
    upgrade.DAILY_CRON.write_bytes(upgrade.daily_cron())
    upgrade.FORWARD_CRON.write_bytes(old_forward)
    with pytest.raises(ValueError, match="unsafe_daily_new_forward_old_state"):
        upgrade.install(*args)


def test_operator_changed_cron_fails_closed(deployment):
    args, _, old_forward = deployment
    upgrade.DAILY_CRON.write_text("operator changed\n")
    with pytest.raises(ValueError, match="existing_cron_mismatch"):
        upgrade.install(*args, execute=True)
    assert upgrade.FORWARD_CRON.read_bytes() == old_forward


@pytest.mark.parametrize("entry_bundle_name", ["daily-v5", "forward-v7"])
def test_preview_runs_from_each_isolated_target_bundle(tmp_path, entry_bundle_name):
    daily_old_bundle = tmp_path / "daily-v4"
    daily_new_bundle = tmp_path / "daily-v5"
    forward_old_bundle = tmp_path / "forward-v6"
    forward_new_bundle = tmp_path / "forward-v7"
    daily_old_manifest = copy_bundle(
        daily_old_bundle, upgrade.previous.DAILY_REQUIRED, "daily-financial-bundle-v1")
    daily_new_manifest = copy_bundle(
        daily_new_bundle, upgrade.DAILY_REQUIRED, "daily-financial-bundle-v1")
    forward_old_manifest = copy_bundle(
        forward_old_bundle, upgrade.previous.FORWARD_REQUIRED, "financial-forward-bundle-v1")
    forward_new_manifest = copy_bundle(
        forward_new_bundle, upgrade.FORWARD_REQUIRED, "financial-forward-bundle-v1")
    cron_dir = tmp_path / "cron.d"
    cron_dir.mkdir()
    daily_cron = cron_dir / "daily"
    forward_cron = cron_dir / "forward"
    timezone = tmp_path / "timezone"
    timezone.write_text("UTC\n")
    backups = tmp_path / "backups"
    old_daily = current_daily_cron(daily_old_bundle)
    old_forward = current_forward_cron(forward_old_bundle)
    daily_cron.write_bytes(old_daily)
    forward_cron.write_bytes(old_forward)
    entry = tmp_path / entry_bundle_name
    outside = tmp_path / "outside"
    outside.mkdir()
    code = f"""
from pathlib import Path
import sys
sys.path.insert(0, {str(entry / 'scripts')!r})
import upgrade_financial_matched_control as helper
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
helper.previous.base.regular = regular
helper.previous.base.directory = directory
helper.previous.forward_base._regular = regular
helper.previous.forward_base._directory = directory
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
helper.previous.daily_cron = lambda: old_daily
helper.previous.forward_cron = lambda: old_forward
helper.DAILY_OLD_CRON_SHA = helper.previous.base.checksum(old_daily)
helper.FORWARD_OLD_CRON_SHA = helper.previous.base.checksum(old_forward)
helper.DAILY_OLD_MANIFEST_SHA = {daily_old_manifest!r}
helper.FORWARD_OLD_MANIFEST_SHA = {forward_old_manifest!r}
sys.argv = ['helper',
    '--expected-daily-new-manifest-sha256', {daily_new_manifest!r},
    '--expected-forward-new-manifest-sha256', {forward_new_manifest!r}]
raise SystemExit(helper.main())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=outside,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout)["status"] == "planned"
    assert daily_cron.read_bytes() == old_daily
    assert forward_cron.read_bytes() == old_forward
    assert not list(entry.rglob("__pycache__"))


@pytest.mark.parametrize("bundle_name", ["DAILY_NEW_BUNDLE", "FORWARD_NEW_BUNDLE"])
@pytest.mark.parametrize("violation", ["extra", "symlink", "writable"])
def test_target_manifest_rejects_unreviewed_or_unsafe_files(deployment, bundle_name, violation):
    args, _, _ = deployment
    bundle = getattr(upgrade, bundle_name)
    if violation == "extra":
        (bundle / "extra.py").write_text("# not manifested\n")
        error = "unmanifested_bundle_files"
    elif violation == "symlink":
        (bundle / "extra.py").symlink_to(next(bundle.rglob("*.py")))
        error = "unsafe_file"
    else:
        target = next(bundle.rglob("*.py"))
        target.chmod(0o666)
        error = "unsafe_file"
    with pytest.raises(ValueError, match=error):
        upgrade.install(*args)


def test_cli_error_is_fail_closed_and_does_not_leak_details(deployment, capsys):
    args, _, _ = deployment
    result = upgrade.main([
        "--expected-daily-new-manifest-sha256", args[1],
        "--expected-forward-new-manifest-sha256", "bad",
    ])
    assert result == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "error": "financial_matched_control_upgrade_failed",
    }
