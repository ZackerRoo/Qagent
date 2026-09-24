import sys
from pathlib import Path
import subprocess
import tarfile

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import upgrade_financial_peer_control as upgrade
import run_daily_financial_v5 as wrapper
sys.path.pop(0)


def test_peer_control_upgrade_preserves_schedule_and_rewrites_only_bundle_entrypoints():
    old = upgrade.baseline.configured_engine()
    engine = upgrade.configured_engine()
    assert engine.old_daily_cron() == old.daily_cron()
    assert engine.old_forward_cron() == old.forward_cron()
    assert engine.DAILY_NEW_BUNDLE == wrapper.DAILY_BUNDLE
    assert engine.FORWARD_NEW_BUNDLE == wrapper.FORWARD_BUNDLE
    daily = engine.daily_cron().replace(str(engine.DAILY_NEW_BUNDLE).encode(),
                                       str(engine.DAILY_OLD_BUNDLE).encode())
    expected_daily = (old.daily_cron()
        .replace(b"/opt/qagent/current", str(upgrade.QAGENT_HOME / "current").encode())
        .replace(b"/var/lib/qagent-research", str(upgrade.QAGENT_HOME / "research-data").encode())
        .replace(b"/var/lib/qagent", str(upgrade.QAGENT_HOME / "state").encode()))
    assert daily.replace(b"run_daily_financial_v5.py", b"run_daily_financial_v4.py") == expected_daily
    forward = engine.forward_cron().replace(str(engine.FORWARD_NEW_BUNDLE).encode(),
                                           str(engine.FORWARD_OLD_BUNDLE).encode())
    expected_forward = (old.forward_cron()
        .replace(b"/opt/qagent/current", str(upgrade.QAGENT_HOME / "current").encode())
        .replace(b"/var/lib/qagent-research", str(upgrade.QAGENT_HOME / "research-data").encode())
        .replace(b"/var/lib/qagent", str(upgrade.QAGENT_HOME / "state").encode()))
    assert forward == expected_forward
    assert engine.daily_cron().count(b"run_daily_financial_v5.py") == 13
    assert engine.forward_cron().count(str(engine.FORWARD_NEW_BUNDLE).encode()) == 4
    assert b"/opt/qagent/current" not in forward
    assert b"/var/lib/qagent" not in forward
    assert "scripts/financial_peer_evidence.py" in upgrade.REQUIRED


@pytest.mark.parametrize("bundle_index", [0, 1])
def test_manifest_only_peer_bundles_import_and_show_help_from_isolated_cwd(tmp_path, bundle_index):
    packages = upgrade.package_bundles(tmp_path / "packages")
    bundle = tmp_path / f"bundle-{bundle_index}"
    bundle.mkdir()
    with tarfile.open(packages[bundle_index]["tar"]) as archive:
        assert set(archive.getnames()) == upgrade.REQUIRED | {"manifest.json"}
        archive.extractall(bundle, filter="data")
    scripts = bundle / "scripts"
    code = f'''
import importlib, io, os, runpy, sys
from contextlib import redirect_stdout
from pathlib import Path
root = Path({str(Path(__file__).resolve().parents[2])!r})
assert root not in map(Path, sys.path)
sys.path.insert(0, {str(scripts)!r})
for name in ("run_daily_financial_v5", "collect_daily_documented_research",
             "evaluate_financial_challenger", "upgrade_financial_peer_control"):
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to({str(bundle)!r})
for name in ("collect_daily_documented_research", "evaluate_financial_challenger",
             "upgrade_financial_peer_control"):
    sys.argv = [name, "--help"]
    with redirect_stdout(io.StringIO()) as out:
        try:
            runpy.run_path(str(Path({str(scripts)!r}) / (name + ".py")), run_name="__main__")
        except SystemExit as error:
            assert error.code == 0, (name, error.code)
    assert "usage:" in out.getvalue().lower(), name
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout
    assert not list(bundle.rglob("__pycache__"))
