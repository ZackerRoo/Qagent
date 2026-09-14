import importlib.util
import json
from pathlib import Path
import stat
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import install_daily_financial_research as base  # noqa: E402

SPEC = importlib.util.spec_from_file_location("financial_upgrade", SCRIPTS /
                                             "upgrade_daily_financial_research.py")
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.os, "geteuid", lambda: 0)
    monkeypatch.setattr(upgrade.os, "fchown", lambda *args: None)
    def regular(path):
        if path.is_symlink() or not path.is_file():
            raise ValueError("unsafe_path")
        meta = path.stat()
        if meta.st_mode & 0o022:
            raise ValueError("unsafe_file_permissions")
        return meta
    def directory(path, *, private=False):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("unsafe_directory")
        if path.stat().st_mode & (0o077 if private else 0o022):
            raise ValueError("unsafe_directory_permissions")
    for module in (upgrade, base):
        monkeypatch.setattr(module, "regular", regular)
        monkeypatch.setattr(module, "directory", directory)
    original_cron = base.cron_bytes()
    old_path = upgrade.OLD_BUNDLE
    for name, path in [("OLD_BUNDLE", "old"), ("NEW_BUNDLE", "new"),
                       ("BACKUPS", "backups"), ("TIMEZONE", "timezone"),
                       ("CRON", "cron.d/financial")]:
        monkeypatch.setattr(upgrade, name, tmp_path / path)
    old_cron = original_cron.replace(str(old_path).encode(), str(upgrade.OLD_BUNDLE).encode())
    monkeypatch.setattr(upgrade, "cron_bytes", lambda: old_cron)
    monkeypatch.setattr(upgrade, "OLD_CRON_SHA", base.checksum(old_cron))
    upgrade.CRON.parent.mkdir()
    upgrade.CRON.write_bytes(old_cron)
    upgrade.BACKUPS.mkdir(mode=0o700)
    upgrade.TIMEZONE.write_text("UTC")
    hashes = []
    for bundle, required in [(upgrade.OLD_BUNDLE, base.REQUIRED),
                             (upgrade.NEW_BUNDLE, upgrade.NEW_REQUIRED)]:
        files = {}
        for name in required:
            path = bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# reviewed\n")
            files[name] = base.checksum(path.read_bytes())
        raw = json.dumps({"schema": "daily-financial-bundle-v1", "files": files}).encode()
        (bundle / "manifest.json").write_bytes(raw)
        hashes.append(base.checksum(raw))
    monkeypatch.setattr(upgrade, "OLD_MANIFEST_SHA", hashes[0])
    return (upgrade.OLD_CRON_SHA, *hashes), old_cron


def test_pinned_original_template_and_twenty_symbol_upgrade():
    assert base.checksum(base.cron_bytes()) == upgrade.OLD_CRON_SHA
    wanted = upgrade.upgraded_cron()
    assert wanted.count(b"--symbol ") == 20
    assert b"40 8 * * 1-5 luozhenkun" in wanted
    assert b"--today-close --budget-seconds 600" in wanted
    assert b"--period 20260630" in wanted


def test_preview_does_not_mutate(deployment):
    args, old = deployment
    assert upgrade.install(*args)["status"] == "planned"
    assert upgrade.CRON.read_bytes() == old
    assert list(upgrade.BACKUPS.iterdir()) == []


def test_atomic_upgrade_backup_and_idempotence(deployment):
    args, old = deployment
    other = upgrade.CRON.parent / "qagent-relay-research"
    other.write_text("old 16:30\n")
    result = upgrade.install(*args, execute=True)
    assert result["status"] == "upgraded"
    assert result["started_job"] is False
    assert Path(result["backup"]).read_bytes() == old
    assert stat.S_IMODE(Path(result["backup"]).stat().st_mode) == 0o600
    assert stat.S_IMODE(upgrade.CRON.stat().st_mode) == 0o644
    assert other.read_text() == "old 16:30\n"
    backups = list(upgrade.BACKUPS.iterdir())
    assert upgrade.install(*args, execute=True)["status"] == "already_installed"
    assert list(upgrade.BACKUPS.iterdir()) == backups


@pytest.mark.parametrize("target", ["cron", "old_manifest", "old_file", "new_file", "extra"])
def test_changed_state_rejected_without_cron_write(deployment, target):
    args, old = deployment
    if target == "cron":
        upgrade.CRON.write_bytes(old + b"# changed")
    elif target == "old_manifest":
        (upgrade.OLD_BUNDLE / "manifest.json").write_text("{}")
    elif target == "extra":
        (upgrade.NEW_BUNDLE / "._metadata").write_text("metadata")
    else:
        bundle = upgrade.OLD_BUNDLE if target == "old_file" else upgrade.NEW_BUNDLE
        (bundle / "scripts/collect_daily_documented_research.py").write_text("changed")
    before = upgrade.CRON.read_bytes()
    with pytest.raises(ValueError):
        upgrade.install(*args, execute=True)
    assert upgrade.CRON.read_bytes() == before
    assert list(upgrade.BACKUPS.iterdir()) == []


def test_changed_cron_after_backup_is_rejected(deployment, monkeypatch):
    args, _ = deployment
    original = upgrade.inspect
    calls = []
    def inspect(*arguments):
        calls.append(True)
        if len(calls) == 3:
            upgrade.CRON.write_text("operator change")
        return original(*arguments)
    monkeypatch.setattr(upgrade, "inspect", inspect)
    with pytest.raises(ValueError):
        upgrade.install(*args, execute=True)
    assert upgrade.CRON.read_text() == "operator change"
    assert not list(upgrade.CRON.parent.glob(".daily-financial-v2-*"))


def test_explicit_baseline_timezone_root(deployment, monkeypatch):
    args, old = deployment
    with pytest.raises(ValueError, match="unexpected_upgrade_baseline"):
        upgrade.install("0" * 64, *args[1:], execute=True)
    upgrade.TIMEZONE.write_text("Asia/Shanghai")
    with pytest.raises(ValueError, match="utc_required"):
        upgrade.install(*args, execute=True)
    upgrade.TIMEZONE.write_text("UTC")
    monkeypatch.setattr(upgrade.os, "geteuid", lambda: 501)
    with pytest.raises(ValueError, match="root_required"):
        upgrade.install(*args, execute=True)
    assert upgrade.CRON.read_bytes() == old
