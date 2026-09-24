import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("daily_dependencies", SCRIPTS / "upgrade_financial_daily_dependencies.py")
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)
sys.path.pop(0)


def test_schedule_arguments_and_historical_helpers_unchanged():
    engine = upgrade.configured_engine()
    checksum = engine.previous.base.checksum
    assert checksum(engine.old_daily_cron()) == engine.DAILY_OLD_CRON_SHA
    assert checksum(engine.old_forward_cron()) == engine.FORWARD_OLD_CRON_SHA
    before = engine.old_daily_cron(), engine.old_forward_cron()
    assert str(engine.DAILY_OLD_BUNDLE).endswith("20260918-v7")
    assert str(engine.FORWARD_OLD_BUNDLE).endswith("20260918-v9")
    assert str(engine.DAILY_NEW_BUNDLE).endswith("20260922-v9")
    assert str(engine.FORWARD_NEW_BUNDLE).endswith("20260922-v11")
    assert engine.DAILY_OLD_CRON_SHA == "04e0023e784aad93a006011a16f6e7061f2bd1adc6bf8c1e78594542c4827e5f"
    assert engine.FORWARD_OLD_CRON_SHA == "f03ae7a5e252d95191967d85d79b956aed6d00d0bcc973e09e8f48ce8ad7b6ef"
    assert engine.DAILY_OLD_MANIFEST_SHA == "3edf4fa5b867bfe2cd452f2f4a8504bec47fe458bfa697abf14154a971daf793"
    assert engine.FORWARD_OLD_MANIFEST_SHA == "b09e7e639493eb8cda5e9a2da321956dded47bbbb7a202c4342c93de149dd097"
    for raw, old_raw, count in ((engine.forward_cron(), before[1], 4),):
        lines = [s for s in raw.decode().splitlines() if "run_financial_forward_research.py" in s]
        old_lines = [s for s in old_raw.decode().splitlines() if "run_financial_forward_research.py" in s]
        assert len(lines) == count
        assert [s.split(None, 6)[:6] for s in lines] == [s.split(None, 6)[:6] for s in old_lines]
        for line in lines:
            assert line.count(upgrade.BASELINE_ARGUMENTS) == 1
            assert "--budget-seconds 300" in line
            assert subprocess.run(["/bin/sh", "-n", "-c", line.split(None, 6)[6]]).returncode == 0
    daily_lines = [s for s in engine.daily_cron().decode().splitlines()
                   if "run_daily_financial_same_day.py" in s]
    old_daily_lines = [s for s in before[0].decode().splitlines()
                       if "collect_daily_documented_research.py" in s]
    assert len(daily_lines) == len(old_daily_lines) == 13
    for line, old_line in zip(daily_lines, old_daily_lines):
        assert line.split(None, 6)[:6] == old_line.split(None, 6)[:6]
        assert len(line) < 1000
        assert "&&" not in line
        assert "/daily-financial-20260922-v9/scripts/run_daily_financial_same_day.py" in line
        assert subprocess.run(["/bin/sh", "-n", "-c", line.split(None, 6)[6]]).returncode == 0
    assert all(len(line) < 1000 for line in engine.daily_cron().decode().splitlines())
    assert engine.forward_cron().decode().count("--daily-frozen-industry") == 0
    assert upgrade._v7_v9_crons() == before
    assert upgrade.baseline.DAILY_REQUIRED < upgrade.DAILY_REQUIRED
    with pytest.raises(ValueError, match="unexpected_upgrade_baseline"):
        engine._validate_manifests("0" * 64, "0" * 64, "0" * 64, "0" * 64)


@pytest.mark.parametrize("entry_index", [0, 1])
@pytest.mark.parametrize("module_name", ["upgrade_financial_daily_dependencies", "upgrade_financial_global_control"])
def test_isolated_bundles_preview_consumer_first_resume_and_rollback(tmp_path, entry_index, module_name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        upgrade = __import__(module_name)
    finally:
        sys.path.pop(0)
    packages = upgrade.package_bundles(tmp_path / "packages")
    repeated = upgrade.package_bundles(tmp_path / "repeat")
    assert [p["tar_sha256"] for p in packages] == [p["tar_sha256"] for p in repeated]
    bundles = []
    for index, package in enumerate(packages):
        bundle = tmp_path / f"bundle-{index}"
        bundle.mkdir()
        with tarfile.open(package["tar"]) as archive:
            assert set(archive.getnames()) == upgrade.DAILY_REQUIRED | {"manifest.json"}
            archive.extractall(bundle, filter="data")
        bundles.append(bundle)
    code = f'''
import sys, json
from pathlib import Path
sys.path.insert(0, {str(bundles[entry_index] / "scripts")!r})
import {module_name} as u
e = u.configured_engine()
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
e.DAILY_NEW_BUNDLE = Path({str(bundles[0])!r})
e.FORWARD_NEW_BUNDLE = Path({str(bundles[1])!r})
e.DAILY_OLD_MANIFEST_SHA = {packages[0]["manifest_sha256"]!r}
e.FORWARD_OLD_MANIFEST_SHA = {packages[1]["manifest_sha256"]!r}
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
original = e.DAILY_CRON.read_bytes(), e.FORWARD_CRON.read_bytes()
assert e.install(*args)["status"] == "planned"
assert original == (e.DAILY_CRON.read_bytes(), e.FORWARD_CRON.read_bytes())
writes = []
write = e._write_atomic
def record(path, raw):
    writes.append(path)
    return write(path, raw)
e._write_atomic = record
receipt = e.install(*args, execute=True)
assert receipt["status"] == "upgraded" and not receipt["started_job"]
assert writes == [e.FORWARD_CRON, e.DAILY_CRON]
assert e.install(*args, execute=True)["status"] == "already_installed"
writes.clear()
assert e.rollback(receipt["backup"], *args, execute=True)["status"] == "rolled_back"
assert writes == [e.DAILY_CRON, e.FORWARD_CRON]
assert original == (e.DAILY_CRON.read_bytes(), e.FORWARD_CRON.read_bytes())
# A interrupted upgrade has only old producer / new consumer and can resume.
e.FORWARD_CRON.write_bytes(e.forward_cron())
assert e.install(*args)["status"] == "resume_planned"
resumed = e.install(*args, execute=True)
assert resumed["status"] == "resumed_upgrade" and not resumed["started_job"]
assert e.rollback(resumed["backup"], *args, execute=True)["status"] == "rolled_back"
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
