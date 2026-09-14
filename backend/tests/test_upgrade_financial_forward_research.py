import importlib.util
import json
from pathlib import Path
import stat
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import install_daily_financial_research as daily  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "financial_forward_upgrade", SCRIPTS / "upgrade_financial_forward_research.py")
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.os, "geteuid", lambda: 0)
    monkeypatch.setattr(upgrade.os, "fchown", lambda *args: None)

    def regular(path):
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
            raise ValueError("unsafe_file")
        return path.stat()

    def directory(path, *, private=False):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("unsafe_directory")
        if path.stat().st_mode & (0o077 if private else 0o022):
            raise ValueError("unsafe_directory_permissions")

    monkeypatch.setattr(upgrade, "_regular", regular)
    monkeypatch.setattr(upgrade, "_directory", directory)
    monkeypatch.setattr(daily, "regular", regular)
    monkeypatch.setattr(daily, "directory", directory)
    for name, relative in (("DAILY_BUNDLE", "daily"), ("FORWARD_BUNDLE", "forward"),
                           ("DAILY_CRON", "cron.d/daily"),
                           ("FORWARD_CRON", "cron.d/forward"),
                           ("BACKUPS", "backups"), ("TIMEZONE", "timezone")):
        monkeypatch.setattr(upgrade, name, tmp_path / relative)
    upgrade.DAILY_CRON.parent.mkdir()
    upgrade.BACKUPS.mkdir(mode=0o700)
    upgrade.TIMEZONE.write_text("UTC")

    daily_required = daily.REQUIRED | {"scripts/upgrade_daily_financial_research.py"}
    files = {}
    for name in daily_required:
        path = upgrade.DAILY_BUNDLE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# reviewed\n")
        files[name] = daily.checksum(path.read_bytes())
    raw = json.dumps({"schema": "daily-financial-bundle-v1", "files": files}).encode()
    (upgrade.DAILY_BUNDLE / "manifest.json").write_bytes(raw)
    daily_manifest = daily.checksum(raw)
    monkeypatch.setattr(upgrade, "DAILY_MANIFEST_SHA", daily_manifest)

    files = {}
    for name in upgrade.REQUIRED:
        path = upgrade.FORWARD_BUNDLE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# reviewed\n")
        files[name] = upgrade.checksum(path.read_bytes())
    raw = json.dumps({"schema": "financial-forward-bundle-v1", "files": files}).encode()
    (upgrade.FORWARD_BUNDLE / "manifest.json").write_bytes(raw)
    forward_manifest = upgrade.checksum(raw)

    upgrade.DAILY_CRON.write_text("reviewed daily cron\n")
    daily_cron = upgrade.checksum(upgrade.DAILY_CRON.read_bytes())
    monkeypatch.setattr(upgrade, "DAILY_CRON_SHA", daily_cron)
    return daily_cron, daily_manifest, forward_manifest


def test_cron_is_isolated_at_1937_shanghai_with_fixed_budget():
    cron = upgrade.cron_bytes()
    assert b"37 11 * * 1-5 luozhenkun" in cron
    assert b"--budget-seconds 300" in cron
    assert b"run_financial_forward_research.py" in cron
    assert b"daily-financial" in cron
    assert b"PYTHONPATH=/opt/qagent/current/backend" in cron


def prepared_crash_receipt(deployment, *, matching_inode=True, install_id="1" * 32):
    wanted = upgrade.cron_bytes()
    pending = upgrade.FORWARD_CRON.parent / ("pending-" + install_id)
    pending.write_bytes(wanted)
    pending.chmod(0o644)
    pending_meta = pending.stat()
    if matching_inode:
        upgrade.FORWARD_CRON.hardlink_to(pending)
    else:
        upgrade.FORWARD_CRON.write_bytes(wanted)
        upgrade.FORWARD_CRON.chmod(0o644)
    receipt = {
        "schema": "financial-forward-install-receipt-v1", "status": "prepared",
        "install_id": install_id, "pending_device": pending_meta.st_dev,
        "pending_inode": pending_meta.st_ino, "cron_path": str(upgrade.FORWARD_CRON),
        "previously_absent": True, "installed_sha256": upgrade.checksum(wanted),
        "forward_manifest_sha256": deployment[2], "daily_cron_sha256": deployment[0],
        "daily_manifest_sha256": deployment[1],
    }
    path = upgrade.BACKUPS / ("before-install-" + install_id + ".json")
    path.write_text(json.dumps(receipt))
    path.chmod(0o600)
    pending.unlink()
    return path


def test_preview_validates_manifests_without_mutation(deployment):
    result = upgrade.install(*deployment)
    assert result["status"] == "planned" and result["started_job"] is False
    assert not upgrade.FORWARD_CRON.exists()
    assert list(upgrade.BACKUPS.iterdir()) == []


def test_atomic_install_idempotence_and_recoverable_rollback(deployment):
    result = upgrade.install(*deployment, execute=True)
    assert result["status"] == "installed" and result["daily_cron_unchanged"] is True
    receipt = Path(result["receipt"])
    assert stat.S_IMODE(upgrade.FORWARD_CRON.stat().st_mode) == 0o644
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    before = upgrade.FORWARD_CRON.read_bytes()
    assert upgrade.install(*deployment, execute=True)["status"] == "already_installed"
    preview = upgrade.rollback(receipt, deployment[2])
    assert preview["status"] == "rollback_planned" and upgrade.FORWARD_CRON.read_bytes() == before
    rolled = upgrade.rollback(receipt, deployment[2], execute=True)
    assert rolled["status"] == "rolled_back" and not upgrade.FORWARD_CRON.exists()
    assert Path(rolled["recovered_cron"]).read_bytes() == before


@pytest.mark.parametrize("changed", ["daily_cron", "daily_file", "forward_file", "extra"])
def test_changed_inputs_fail_before_install(deployment, changed):
    if changed == "daily_cron":
        upgrade.DAILY_CRON.write_text("operator changed\n")
    elif changed == "daily_file":
        next(path for path in upgrade.DAILY_BUNDLE.rglob("*.py")).write_text("changed\n")
    elif changed == "forward_file":
        next(path for path in upgrade.FORWARD_BUNDLE.rglob("*.py")).write_text("changed\n")
    else:
        (upgrade.FORWARD_BUNDLE / "._metadata").write_text("unexpected")
    with pytest.raises(ValueError):
        upgrade.install(*deployment, execute=True)
    assert not upgrade.FORWARD_CRON.exists()


def test_rollback_rejects_modified_cron(deployment):
    result = upgrade.install(*deployment, execute=True)
    receipt = Path(result["receipt"])
    upgrade.FORWARD_CRON.write_text("operator changed\n")
    with pytest.raises(ValueError, match="forward_cron_changed"):
        upgrade.rollback(receipt, deployment[2], execute=True)
    assert upgrade.FORWARD_CRON.read_text() == "operator changed\n"


def test_publication_failure_leaves_only_nonrollback_receipt(deployment, monkeypatch):
    monkeypatch.setattr(upgrade.os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("publication failed")))
    with pytest.raises(OSError, match="publication failed"):
        upgrade.install(*deployment, execute=True)
    assert not upgrade.FORWARD_CRON.exists()
    receipts = list(upgrade.BACKUPS.glob("before-install-*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["status"] == "prepared"
    with pytest.raises(ValueError, match="invalid_rollback_receipt"):
        upgrade.rollback(receipts[0], deployment[2], execute=True)


def test_receipt_promotion_failure_withdraws_only_owned_cron(deployment, monkeypatch):
    monkeypatch.setattr(upgrade.os, "replace", lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("receipt promotion failed")))
    with pytest.raises(OSError, match="receipt promotion failed"):
        upgrade.install(*deployment, execute=True)
    assert not upgrade.FORWARD_CRON.exists()
    receipt = next(upgrade.BACKUPS.glob("before-install-*.json"))
    assert json.loads(receipt.read_text())["status"] == "prepared"
    assert not list(upgrade.BACKUPS.glob(".installed-receipt-*"))


def test_receipt_promotion_failure_never_removes_replacement(deployment, monkeypatch):
    original_replace = upgrade.os.replace
    replacement = upgrade.FORWARD_CRON.parent / "operator-replacement"
    replacement.write_text("operator cron\n")

    def fail_after_operator_replace(source, target):
        original_replace(replacement, upgrade.FORWARD_CRON)
        raise OSError("receipt promotion failed")

    monkeypatch.setattr(upgrade.os, "replace", fail_after_operator_replace)
    with pytest.raises(OSError, match="receipt promotion failed"):
        upgrade.install(*deployment, execute=True)
    assert upgrade.FORWARD_CRON.read_text() == "operator cron\n"
    receipt = next(upgrade.BACKUPS.glob("before-install-*.json"))
    assert json.loads(receipt.read_text())["status"] == "prepared"


def test_execute_recovers_crash_prepared_receipt_and_can_rollback(deployment):
    receipt = prepared_crash_receipt(deployment)
    result = upgrade.install(*deployment, execute=True)
    assert result["status"] == "recovered_installed"
    assert Path(result["receipt"]) == receipt
    assert json.loads(receipt.read_text())["status"] == "installed"
    rolled = upgrade.rollback(receipt, deployment[2], execute=True)
    assert rolled["status"] == "rolled_back" and not upgrade.FORWARD_CRON.exists()


def test_recovery_rejects_same_bytes_from_different_inode(deployment):
    prepared_crash_receipt(deployment, matching_inode=False)
    before = upgrade.FORWARD_CRON.stat()
    with pytest.raises(ValueError, match="ambiguous_or_missing_install_receipt"):
        upgrade.install(*deployment, execute=True)
    after = upgrade.FORWARD_CRON.stat()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert upgrade.FORWARD_CRON.read_bytes() == upgrade.cron_bytes()


def test_recovery_rejects_multiple_matching_prepared_receipts(deployment):
    first = prepared_crash_receipt(deployment, install_id="1" * 32)
    second = upgrade.BACKUPS / ("before-install-" + "2" * 32 + ".json")
    value = json.loads(first.read_text())
    value["install_id"] = "2" * 32
    second.write_text(json.dumps(value))
    second.chmod(0o600)
    with pytest.raises(ValueError, match="ambiguous_or_missing_install_receipt"):
        upgrade.install(*deployment, execute=True)
    assert json.loads(first.read_text())["status"] == "prepared"
    assert json.loads(second.read_text())["status"] == "prepared"
