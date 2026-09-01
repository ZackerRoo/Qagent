from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runit_assets_enforce_loopback_user_restart_and_timezone():
    backend = (ROOT / "deploy/runit/backend.run.in").read_text()
    frontend = (ROOT / "deploy/runit/frontend.run.in").read_text()

    assert "chpst -u @SERVICE_USER@:@SERVICE_USER@" in backend
    assert "HOME=@SERVICE_HOME@" in backend
    assert "TZ=Asia/Shanghai" in backend
    assert "--host 127.0.0.1 --port 8000" in backend
    assert "chpst -u @SERVICE_USER@:@SERVICE_USER@" in frontend
    assert "HOME=@SERVICE_HOME@" in frontend
    assert "TZ=Asia/Shanghai" in frontend
    assert "--host 127.0.0.1 --port 5173 --strictPort" in frontend
    # runsv restarts an executable run script whenever it exits.
    assert (ROOT / "deploy/runit/qagent-backup.cron.in").read_text().startswith(
        "CRON_TZ=Asia/Shanghai"
    )


def test_installer_stages_services_without_enabling_them():
    installer = (ROOT / "scripts/install_linux_runit.sh").read_text()
    assert 'mv "$STAGING/$name" "$SV_DIR/$name"' in installer
    assert "/etc/service/qagent" not in installer
    assert "qagent-backup.disabled" in installer
    assert "verify_isolated_linux_install.py" in installer
    assert "env -u QAGENT_DATABASE_URL" in installer
    assert 'touch "$STAGING/qagent-$name/down"' in installer
    assert '"$UV_BIN" sync' in installer
    assert "--frozen --no-dev --python python3.11" in installer
    assert "pip install" not in installer


def test_backup_is_consistent_and_retention_argument_is_validated(tmp_path: Path):
    source = tmp_path / "source.db"
    destination = tmp_path / "backups"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        db.execute("INSERT INTO evidence VALUES ('preserved')")

    subprocess.run(
        [str(ROOT / "scripts/backup_sqlite.sh"), str(source), str(destination), "14"],
        check=True,
        text=True,
        capture_output=True,
    )
    backups = list(destination.glob("qagent-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert db.execute("SELECT value FROM evidence").fetchone() == ("preserved",)


def test_snapshot_install_refuses_implicit_replacement(tmp_path: Path):
    source = tmp_path / "snapshot.db"
    destination = tmp_path / "qagent.db"
    for path, value in ((source, "new"), (destination, "old")):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE marker (value TEXT)")
            db.execute("INSERT INTO marker VALUES (?)", (value,))

    result = subprocess.run(
        [str(ROOT / "scripts/install_sqlite_snapshot.sh"), str(source), str(destination)],
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )
    assert result.returncode != 0
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT value FROM marker").fetchone() == ("old",)

    installer = (ROOT / "scripts/install_sqlite_snapshot.sh").read_text()
    assert "supervise/pid" in installer
    assert 'ss -H -ltn "sport = :$port"' in installer
    assert "pgrep -u" in installer


def test_cutover_manifest_blocks_enabled_scheduler_and_running_jobs(tmp_path: Path):
    database = tmp_path / "cutover.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO positions VALUES (1, 'unchanged')")
        db.execute(
            "CREATE TABLE automation_scheduler_state (enabled BOOLEAN, settings_json TEXT)"
        )
        db.execute(
            "INSERT INTO automation_scheduler_state VALUES (1, ?)",
            ('{"runtime":{"in_flight":false}}',),
        )
        db.execute("CREATE TABLE full_market_scan_jobs (status TEXT)")
        db.execute("INSERT INTO full_market_scan_jobs VALUES ('running')")

    command = [
        str(ROOT / "scripts/sqlite_cutover_manifest.py"),
        "--preflight",
        str(database),
    ]
    blocked = subprocess.run(command, text=True, capture_output=True)
    assert blocked.returncode != 0
    assert "scheduler is enabled" in blocked.stderr

    with sqlite3.connect(database) as db:
        db.execute("UPDATE automation_scheduler_state SET enabled = 0")
    blocked = subprocess.run(command, text=True, capture_output=True)
    assert blocked.returncode != 0
    assert "queued/running jobs" in blocked.stderr

    with sqlite3.connect(database) as db:
        db.execute("UPDATE full_market_scan_jobs SET status = 'succeeded'")
        db.execute("CREATE TABLE automation_cycles (status TEXT)")
        db.execute("INSERT INTO automation_cycles VALUES ('running')")
    blocked = subprocess.run(command, text=True, capture_output=True)
    assert blocked.returncode != 0
    assert "automation_cycles has 1 in-flight rows" in blocked.stderr

    with sqlite3.connect(database) as db:
        db.execute("UPDATE automation_cycles SET status = 'succeeded'")
    ready = subprocess.run(command, text=True, capture_output=True)
    assert ready.returncode == 0
    assert '"positions"' in ready.stdout


def test_cutover_allows_historical_partial_cycle_and_error_stage(tmp_path: Path):
    database = tmp_path / "historical-runtime.db"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE automation_scheduler_state (enabled BOOLEAN, settings_json TEXT)"
        )
        db.execute(
            "INSERT INTO automation_scheduler_state VALUES (0, ?)",
            ('{"runtime":{"in_flight":false},"settings":{}}',),
        )
        db.execute("CREATE TABLE automation_cycles (status TEXT)")
        db.execute("INSERT INTO automation_cycles VALUES ('partial_retry_same_slot')")
        db.execute("CREATE TABLE automation_cycle_stages (status TEXT)")
        db.execute("INSERT INTO automation_cycle_stages VALUES ('error')")

    command = [
        str(ROOT / "scripts/sqlite_cutover_manifest.py"),
        "--preflight",
        str(database),
    ]
    ready = subprocess.run(command, text=True, capture_output=True)
    assert ready.returncode == 0, ready.stderr

    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE automation_scheduler_state SET settings_json = ?",
            ('{"runtime":{"in_flight":true},"settings":{}}',),
        )
    blocked = subprocess.run(command, text=True, capture_output=True)
    assert blocked.returncode != 0
    assert "runtime.in_flight" in blocked.stderr

    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE automation_scheduler_state SET settings_json = ?",
            ('{"runtime":{"in_flight":false},"settings":{}}',),
        )
        db.execute("INSERT INTO automation_cycle_stages VALUES ('running')")
    blocked = subprocess.run(command, text=True, capture_output=True)
    assert blocked.returncode != 0
    assert "automation_cycle_stages has 1 in-flight rows" in blocked.stderr


def test_manifest_includes_optional_user_state_tables(tmp_path: Path):
    database = tmp_path / "user-state.db"
    with sqlite3.connect(database) as db:
        for table in ("watchlist_items", "alert_rules", "universes"):
            db.execute(f'CREATE TABLE "{table}" (id TEXT PRIMARY KEY, value TEXT)')
            db.execute(f'INSERT INTO "{table}" VALUES (?, ?)', (table, "preserved"))

    result = subprocess.run(
        [str(ROOT / "scripts/sqlite_cutover_manifest.py"), str(database)],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    manifest = result.stdout
    assert '"watchlist_items"' in manifest
    assert '"alert_rules"' in manifest
    assert '"universes"' in manifest
