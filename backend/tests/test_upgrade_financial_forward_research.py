import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest
from sqlalchemy import create_engine

from qagent.storage.tables import MarketBarCacheRow

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import install_daily_financial_research as daily  # noqa: E402
import collect_daily_documented_research as batch  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "financial_forward_upgrade", SCRIPTS / "upgrade_financial_forward_research.py")
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.os, "geteuid", lambda: 0)
    monkeypatch.setattr(upgrade.os, "fchown", lambda *args: None)
    monkeypatch.setattr(upgrade, "_service_identity", lambda: (upgrade.os.getuid(), upgrade.os.getgid()))

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
    for name, relative in (("SIGNAL_DIR", "state/signals"),
                           ("EVALUATION_DIR", "state/evaluations"),
                           ("RUN_DIR", "state/runs")):
        monkeypatch.setattr(upgrade, name, tmp_path / relative)
    upgrade.DAILY_CRON.parent.mkdir()
    upgrade.BACKUPS.mkdir(mode=0o700)
    upgrade.SIGNAL_DIR.mkdir(parents=True, mode=0o700)
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
    assert result["research_directories"][str(upgrade.SIGNAL_DIR)] == "verified"
    assert result["research_directories"][str(upgrade.EVALUATION_DIR)] == "planned"
    assert not upgrade.EVALUATION_DIR.exists() and not upgrade.RUN_DIR.exists()
    assert list(upgrade.BACKUPS.iterdir()) == []


def test_atomic_install_idempotence_and_recoverable_rollback(deployment):
    result = upgrade.install(*deployment, execute=True)
    json.dumps(result)
    assert result["status"] == "installed" and result["daily_cron_unchanged"] is True
    receipt = Path(result["receipt"])
    assert stat.S_IMODE(upgrade.FORWARD_CRON.stat().st_mode) == 0o644
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert result["research_directories"][str(upgrade.EVALUATION_DIR)] == "created"
    for path in (upgrade.SIGNAL_DIR, upgrade.EVALUATION_DIR, upgrade.RUN_DIR):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    before = upgrade.FORWARD_CRON.read_bytes()
    repeated = upgrade.install(*deployment, execute=True)
    assert repeated["status"] == "already_installed"
    json.dumps(repeated)
    preview = upgrade.rollback(receipt, deployment[2])
    assert preview["status"] == "rollback_planned" and upgrade.FORWARD_CRON.read_bytes() == before
    rolled = upgrade.rollback(receipt, deployment[2], execute=True)
    json.dumps(rolled)
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
    json.dumps(result)
    assert Path(result["receipt"]) == receipt
    assert json.loads(receipt.read_text())["status"] == "installed"
    rolled = upgrade.rollback(receipt, deployment[2], execute=True)
    json.dumps(rolled)
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


@pytest.mark.parametrize("kind", ["mode", "owner", "symlink"])
def test_research_directory_contract_rejected_before_cron_install(deployment, monkeypatch, kind):
    if kind == "mode":
        upgrade.SIGNAL_DIR.chmod(0o755)
    elif kind == "owner":
        monkeypatch.setattr(upgrade, "_service_identity", lambda: (
            upgrade.os.getuid() + 1, upgrade.os.getgid()))
    else:
        upgrade.SIGNAL_DIR.rmdir()
        upgrade.SIGNAL_DIR.symlink_to(upgrade.SIGNAL_DIR.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_research_directory"):
        upgrade.install(*deployment, execute=True)
    assert not upgrade.FORWARD_CRON.exists()


def test_isolated_manifest_bundle_replays_seal_and_evaluate(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    bundle = tmp_path / "isolated-forward-v3"
    files = {}
    for name in upgrade.REQUIRED:
        source = root / name
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files[name] = upgrade.checksum(target.read_bytes())
    manifest = json.dumps({"schema": "financial-forward-bundle-v1", "files": files}).encode()
    (bundle / "manifest.json").write_bytes(manifest)
    monkeypatch.setattr(upgrade, "FORWARD_BUNDLE", bundle)
    monkeypatch.setattr(upgrade, "_regular", lambda path: path.stat())
    monkeypatch.setattr(upgrade, "_directory", lambda path, private=False: None)
    upgrade.validate_forward_bundle(upgrade.checksum(manifest))
    assert {"backend/qagent/providers/datahubco.py",
            "backend/qagent/providers/tushare_relay.py"} <= upgrade.REQUIRED

    monkeypatch.setattr(batch, "now", lambda: "2026-09-11T16:40:00+08:00")
    symbols = [f"60000{index}.SH" for index in range(1, 7)]

    def query(base_url, **request):
        symbol = request["params"]["ts_code"]
        row = {"ts_code": symbol, "end_date": "20260630", "ann_date": "20260801",
               "report_type": "1", "comp_type": "1", "n_cashflow_act": 30,
               "n_income": 10, "total_revenue": 100, "trade_date": "20260911",
               "pe": 10, "total_assets": 1000, "total_liab": 300, "roe": 10,
               "netprofit_margin": 20, "type": "预增", "p_change_min": 1,
               "p_change_max": 2}
        return {"source": "datahubco", "status": "observed", "rows": [row],
                "decision_weight": False, "activation_allowed": False}

    daily_path = tmp_path / "daily.json"
    daily_path.write_text(json.dumps(
        batch.run_batch(symbols, "20260630", "20260911", query=query)))
    db = tmp_path / "qagent.db"
    engine = create_engine("sqlite:///" + str(db))
    MarketBarCacheRow.__table__.create(engine)
    engine.dispose()
    code = """
import json, pathlib, sys
from datetime import datetime
bundle, daily, db = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(bundle / 'scripts'))
import evaluate_financial_challenger as forward
document = json.loads(daily.read_text())
signal = forward.seal(document, now=datetime.fromisoformat('2026-09-11T19:30:00+08:00'))
result = forward.evaluate(signal, db, as_of=datetime.fromisoformat('2026-09-11T19:31:00+08:00'))
assert signal['status'] == 'sealed'
assert all(item['status'] == 'waiting_for_maturity' for item in result['horizons'])
print(result['protocol'])
"""
    environment = dict(os.environ, PYTHONPATH=str(root / "backend"))
    completed = subprocess.run(
        [sys.executable, "-c", code, str(bundle), str(daily_path), str(db)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "financial-rule-forward-evaluation-v1"


def test_absent_cron_ignores_historical_v2_receipt_for_v4_install(deployment):
    old = upgrade.BACKUPS / "before-install-v2-history.json"
    old.write_text(json.dumps({
        "schema": "financial-forward-install-receipt-v1", "status": "installed",
        "cron_path": str(upgrade.FORWARD_CRON), "forward_manifest_sha256": "2" * 64,
        "installed_sha256": "3" * 64}))
    old.chmod(0o600)
    result = upgrade.install(*deployment, execute=True)
    assert result["status"] == "installed"
    assert upgrade.FORWARD_CRON.read_bytes() == upgrade.cron_bytes()
    assert old.exists()


def test_main_outputs_json_for_install_idempotence_and_rollback(deployment, capsys):
    arguments = ["--expected-daily-cron-sha256", deployment[0],
                 "--expected-daily-manifest-sha256", deployment[1],
                 "--expected-forward-manifest-sha256", deployment[2], "--execute"]
    assert upgrade.main(arguments) == 0
    installed = json.loads(capsys.readouterr().out)
    assert installed["status"] == "installed"
    assert upgrade.main(arguments) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["status"] == "already_installed"
    rollback = ["--expected-forward-manifest-sha256", deployment[2],
                "--rollback-receipt", installed["receipt"], "--execute"]
    assert upgrade.main(rollback) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "rolled_back"


def test_v2_installed_history_does_not_block_linked_v4_prepared_recovery(deployment):
    current = prepared_crash_receipt(deployment, install_id="3" * 32)
    historical = json.loads(current.read_text())
    historical.update(status="installed", install_id="2" * 32,
                      forward_manifest_sha256="2" * 64,
                      installed_sha256="4" * 64,
                      pending_inode=historical["pending_inode"] + 10)
    old = upgrade.BACKUPS / "before-install-v2-installed.json"
    old.write_text(json.dumps(historical))
    old.chmod(0o600)
    result = upgrade.install(*deployment, execute=True)
    assert result["status"] == "recovered_installed"
    assert Path(result["receipt"]) == current
    assert json.loads(old.read_text())["status"] == "installed"
    assert upgrade.rollback(current, deployment[2], execute=True)["status"] == "rolled_back"
