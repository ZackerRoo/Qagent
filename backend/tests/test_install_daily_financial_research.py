import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest

SPEC = importlib.util.spec_from_file_location(
    "daily_installer", Path(__file__).resolve().parents[2] /
    "scripts/install_daily_financial_research.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # Test only temporary paths. Simulate root ownership without altering host ownership.
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    monkeypatch.setattr(installer.os, "fchown", lambda *args: None)
    original_regular, original_directory = installer.regular, installer.directory
    def regular(path):
        if not path.exists():
            raise ValueError("unsafe_path")
        meta = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(meta.st_mode):
            raise ValueError("unsafe_path")
        if meta.st_mode & 0o022:
            raise ValueError("unsafe_file_permissions")
        return meta
    def directory(path, *, private=False):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("unsafe_directory")
        if path.stat().st_mode & (0o077 if private else 0o022):
            raise ValueError("unsafe_directory_permissions")
    monkeypatch.setattr(installer, "regular", regular)
    monkeypatch.setattr(installer, "directory", directory)
    for name, target in [("BUNDLE", "bundle"), ("CRON", "cron.d/new-financial"),
                         ("BACKUPS", "backups/private"), ("TIMEZONE", "timezone")]:
        monkeypatch.setattr(installer, name, tmp_path / target)
    installer.BUNDLE.mkdir()
    installer.CRON.parent.mkdir()
    installer.BACKUPS.parent.mkdir()
    installer.TIMEZONE.write_text("UTC\n")
    files = {}
    for name in installer.REQUIRED:
        path = installer.BUNDLE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# reviewed\n")
        files[name] = installer.checksum(path.read_bytes())
    raw = json.dumps({"schema": "daily-financial-bundle-v1", "files": files}).encode()
    (installer.BUNDLE / installer.MANIFEST).write_bytes(raw)
    return installer.checksum(raw), original_regular, original_directory


def test_preview_is_read_only(setup):
    digest, *_ = setup
    report = installer.install(digest)
    assert report["status"] == "planned"
    assert report["started_job"] is False
    assert not installer.CRON.exists()
    assert not installer.BACKUPS.exists()
    assert "40 8 * * 1-5 luozhenkun" in report["cron"]
    assert report["cron"].count("--symbol ") == 8
    assert "--today-close --budget-seconds 600" in report["cron"]
    assert "qagent.env" not in report["cron"]
    assert "--period 20260630" in report["cron"]


def test_install_atomic_private_receipt_and_idempotent(setup):
    digest, *_ = setup
    old_cron = installer.CRON.parent / "qagent-relay-research"
    old_cron.write_text("old 16:30 observation\n")
    result = installer.install(digest, execute=True)
    assert result["status"] == "installed"
    assert stat.S_IMODE(installer.CRON.stat().st_mode) == 0o644
    assert stat.S_IMODE(Path(result["backup"]).stat().st_mode) == 0o600
    assert json.loads(Path(result["backup"]).read_text())["previously_absent"] is True
    assert old_cron.read_text() == "old 16:30 observation\n"
    before = list(installer.BACKUPS.iterdir())
    assert installer.install(digest, execute=True)["status"] == "already_installed"
    assert list(installer.BACKUPS.iterdir()) == before


@pytest.mark.parametrize("kind", ["manifest", "file", "extra", "missing", "symlink"])
def test_bundle_change_rejected_before_mutation(setup, kind):
    digest, *_ = setup
    target = installer.BUNDLE / sorted(installer.REQUIRED)[0]
    if kind == "manifest":
        (installer.BUNDLE / installer.MANIFEST).write_text("{}")
    elif kind == "file":
        target.write_text("changed")
    elif kind == "extra":
        (installer.BUNDLE / "extra.py").write_text("extra")
    elif kind == "missing":
        target.unlink()
    else:
        target.unlink()
        target.symlink_to(installer.BUNDLE / installer.MANIFEST)
    with pytest.raises(ValueError):
        installer.install(digest, execute=True)
    assert not installer.CRON.exists()
    assert not installer.BACKUPS.exists()


def test_conflicting_existing_cron_not_overwritten(setup):
    digest, *_ = setup
    installer.CRON.write_text("operator-managed\n")
    with pytest.raises(ValueError, match="existing_cron_mismatch"):
        installer.install(digest, execute=True)
    assert installer.CRON.read_text() == "operator-managed\n"


def test_timezone_and_root_guards(setup, monkeypatch):
    digest, *_ = setup
    installer.TIMEZONE.write_text("Asia/Shanghai")
    with pytest.raises(ValueError, match="utc_required"):
        installer.install(digest, execute=True)
    installer.TIMEZONE.write_text("UTC")
    monkeypatch.setattr(installer.os, "geteuid", lambda: 123)
    with pytest.raises(ValueError, match="root_required"):
        installer.install(digest, execute=True)


def test_concurrent_target_creation_cannot_be_overwritten(setup, monkeypatch):
    digest, *_ = setup
    original_link = os.link
    def conflict(source, destination, **kwargs):
        destination.write_text("operator-race")
        return original_link(source, destination, **kwargs)
    monkeypatch.setattr(installer.os, "link", conflict)
    with pytest.raises(FileExistsError):
        installer.install(digest, execute=True)
    assert installer.CRON.read_text() == "operator-race"
    assert not list(installer.CRON.parent.glob(".daily-financial-*"))


def test_production_symlink_guard(tmp_path):
    target = tmp_path / "file"
    target.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="unsafe_path"):
        installer.regular(link)
