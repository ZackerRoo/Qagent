import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("late_scan", SCRIPTS / "upgrade_financial_late_scan.py")
late = importlib.util.module_from_spec(spec)
spec.loader.exec_module(late)
sys.path.pop(0)


def test_schedule_and_frozen_baselines():
    engine = late.configured_engine()
    checksum = engine.previous.base.checksum
    assert checksum(engine.old_daily_cron()) == engine.DAILY_OLD_CRON_SHA
    assert checksum(engine.old_forward_cron()) == engine.FORWARD_OLD_CRON_SHA
    daily = engine.daily_cron().decode()
    lines = [line for line in daily.splitlines() if "--bounded-same-day" in line]
    assert len(lines) == 13
    assert lines[0].startswith("40 8 ") and lines[-1].startswith("40 14 ")
    assert any(line.startswith("40 13 ") for line in lines)  # 21:39 repair caught at21:40
    for line in lines:
        command = line.split(None, 6)[6]
        assert "--bounded-same-day && PYTHONPATH=" in command
        assert subprocess.run(["/bin/sh", "-n", "-c", command]).returncode == 0
    forward = engine.forward_cron().decode()
    assert forward.count("run_financial_forward_research.py") == 4
    assert "7 15 * * 1-5" in forward  # 23:07 recovery, same calendar day
    assert checksum(late.baseline.daily_cron()) == engine.DAILY_OLD_CRON_SHA


@pytest.mark.parametrize("entry_index", [0, 1])
def test_isolated_bundle_preview_install_resume_rollback(tmp_path, entry_index):
    packages = late.package_bundles(tmp_path / "packages")
    repeated = late.package_bundles(tmp_path / "repeat")
    assert [p["tar_sha256"] for p in packages] == [p["tar_sha256"] for p in repeated]
    bundles = []
    for index, package in enumerate(packages):
        bundle = tmp_path / f"bundle-{index}"
        bundle.mkdir()
        with tarfile.open(package["tar"]) as archive:
            assert set(archive.getnames()) == late.DAILY_REQUIRED | {"manifest.json"}
            archive.extractall(bundle, filter="data")
        bundles.append(bundle)
    code = f'''
import sys, json, os
from pathlib import Path
sys.path.insert(0, {str(bundles[entry_index] / "scripts")!r})
import upgrade_financial_late_scan as late
e = late.configured_engine()
root = Path({str(tmp_path)!r})
def regular(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
        raise ValueError("unsafe_file")
    return path.stat()
def directory(path, private=False):
    if path.is_symlink() or not path.is_dir():
        raise ValueError("unsafe_directory")
e.previous.base.regular = e.previous.forward_base._regular = regular
e.previous.base.directory = e.previous.forward_base._directory = directory
e.os.geteuid = lambda: 0
e.os.fchown = lambda *args: None
e.DAILY_OLD_BUNDLE = e.DAILY_NEW_BUNDLE = Path({str(bundles[0])!r})
e.FORWARD_OLD_BUNDLE = e.FORWARD_NEW_BUNDLE = Path({str(bundles[1])!r})
e.DAILY_OLD_MANIFEST_SHA = {packages[0]["manifest_sha256"]!r}
e.FORWARD_OLD_MANIFEST_SHA = {packages[1]["manifest_sha256"]!r}
# Baseline text remains pinned. Bundle paths in desired templates are remapped
# only after extracting the commands from the actual immutable baseline.
e.DAILY_OLD_BUNDLE = late.baseline.DAILY_NEW_BUNDLE
e.FORWARD_OLD_BUNDLE = late.baseline.FORWARD_NEW_BUNDLE
validate = e._validate_manifests
def validate_local(*args):
    old = e.DAILY_OLD_BUNDLE, e.FORWARD_OLD_BUNDLE
    e.DAILY_OLD_BUNDLE, e.FORWARD_OLD_BUNDLE = e.DAILY_NEW_BUNDLE, e.FORWARD_NEW_BUNDLE
    try:
        validate(*args)
    finally:
        e.DAILY_OLD_BUNDLE, e.FORWARD_OLD_BUNDLE = old
e._validate_manifests = validate_local
e.DAILY_CRON, e.FORWARD_CRON = root / "daily.cron", root / "forward.cron"
e.TIMEZONE, e.BACKUPS = root / "timezone", root / "backups"
e.TIMEZONE.write_text("UTC")
e.DAILY_CRON.write_bytes(e.old_daily_cron())
e.FORWARD_CRON.write_bytes(e.old_forward_cron())
e.DAILY_CRON.chmod(0o644)
e.FORWARD_CRON.chmod(0o644)
args = (e.DAILY_OLD_MANIFEST_SHA, e.DAILY_OLD_MANIFEST_SHA,
        e.FORWARD_OLD_MANIFEST_SHA, e.FORWARD_OLD_MANIFEST_SHA)
assert e.install(*args)["status"] == "planned"
# Simulate interruption after consumer write; recover without starting jobs.
e.FORWARD_CRON.write_bytes(e.forward_cron())
assert e.install(*args)["status"] == "resume_planned"
receipt = e.install(*args, execute=True)
assert receipt["status"] == "resumed_upgrade" and not receipt["started_job"]
assert e.install(*args, execute=True)["status"] == "already_installed"
assert e.rollback(receipt["backup"], *args, execute=True)["status"] == "rolled_back"
assert e.DAILY_CRON.read_bytes() == e.old_daily_cron()
assert e.FORWARD_CRON.read_bytes() == e.old_forward_cron()
e.DAILY_CRON.write_bytes(e.daily_cron())
try:
    e.install(*args)
except ValueError as error:
    assert str(error) == "unsafe_daily_new_forward_old_state"
else:
    raise AssertionError("incompatible state accepted")
print(json.dumps({{"status": "verified"}}))
'''
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["status"] == "verified"
    assert not any(list(bundle.rglob("__pycache__")) for bundle in bundles)
