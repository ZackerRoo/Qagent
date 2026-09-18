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
    previous = upgrade.baseline.configured_engine()
    before = previous.daily_cron(), previous.forward_cron()
    engine = upgrade.configured_engine()
    checksum = engine.previous.base.checksum
    assert checksum(engine.old_daily_cron()) == engine.DAILY_OLD_CRON_SHA
    assert checksum(engine.old_forward_cron()) == engine.FORWARD_OLD_CRON_SHA
    assert str(engine.DAILY_OLD_BUNDLE).endswith("20260917-v6")
    assert str(engine.FORWARD_OLD_BUNDLE).endswith("20260917-v8")
    assert str(engine.DAILY_NEW_BUNDLE).endswith("20260918-v7")
    assert str(engine.FORWARD_NEW_BUNDLE).endswith("20260918-v9")
    assert engine.DAILY_OLD_MANIFEST_SHA == "1e942320d572f2a61c5e0f155c3c2dcb2a4e0e1589b7c77f7af854f275cb8d55"
    assert engine.FORWARD_OLD_MANIFEST_SHA == "92717c3ff89a50efe27a627a40d2549a6ab585989330c9111b045dfc45e6838b"
    for raw, old_raw, count in ((engine.daily_cron(), before[0], 13), (engine.forward_cron(), before[1], 4)):
        lines = [s for s in raw.decode().splitlines() if "run_financial_forward_research.py" in s]
        old_lines = [s for s in old_raw.decode().splitlines() if "run_financial_forward_research.py" in s]
        assert len(lines) == count
        assert [s.split(None, 6)[:6] for s in lines] == [s.split(None, 6)[:6] for s in old_lines]
        for line in lines:
            assert line.count(upgrade.BASELINE_ARGUMENTS) == 1
            assert "--budget-seconds 300" in line
            assert subprocess.run(["/bin/sh", "-n", "-c", line.split(None, 6)[6]]).returncode == 0
    assert engine.daily_cron().decode().count("--daily-frozen-industry &&") == 13
    assert engine.forward_cron().decode().count("--daily-frozen-industry") == 0
    assert (previous.daily_cron(), previous.forward_cron()) == before
    assert upgrade.baseline.DAILY_REQUIRED < upgrade.DAILY_REQUIRED
    with pytest.raises(ValueError, match="unexpected_upgrade_baseline"):
        engine._validate_manifests("0" * 64, "0" * 64, "0" * 64, "0" * 64)


@pytest.mark.parametrize("entry_index", [0, 1])
def test_isolated_bundles_preview_consumer_first_resume_and_rollback(tmp_path, entry_index):
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
import upgrade_financial_daily_dependencies as u
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
