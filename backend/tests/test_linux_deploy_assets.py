from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
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
    assert (
        (ROOT / "deploy/runit/qagent-backup.cron.in")
        .read_text()
        .startswith("CRON_TZ=Asia/Shanghai")
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
    assert "merge_proxy_environment.py" in installer
    assert 'python3.11 "$APP_DIR/scripts/merge_proxy_environment.py"' in installer
    assert "\nsource /etc/environment" not in installer
    assert "\n. /etc/environment" not in installer
    assert "8#$CURRENT_ENV_MODE & 8#640" in installer


def test_proxy_environment_merge_is_safe_idempotent_and_preserves_secrets(
    tmp_path: Path,
):
    source = tmp_path / "environment"
    target = tmp_path / "qagent.env"
    source.write_text(
        "UNRELATED=ignore-me\n"
        "http_proxy=http://proxy.example:8080\n"
        "HTTPS_PROXY='http://user:secret@proxy.example:8080'\n"
        'NO_PROXY="localhost,127.0.0.1"\n'
        "MALICIOUS=$(touch /tmp/qagent-must-not-run)\n"
    )
    target.write_text(
        "QAGENT_SECRET=keep-this\n"
        "http_proxy=stale\n"
        "no_proxy=existing.internal\n"
        "CUSTOM_SETTING=keep-too\n"
    )
    command = [
        sys.executable,
        str(ROOT / "scripts/merge_proxy_environment.py"),
        "--source",
        str(source),
        "--target",
        str(target),
    ]

    first = subprocess.run(command, check=True, text=True, capture_output=True)
    first_content = target.read_text()
    second = subprocess.run(command, check=True, text=True, capture_output=True)

    assert target.read_text() == first_content
    assert "QAGENT_SECRET=keep-this" in first_content
    assert "CUSTOM_SETTING=keep-too" in first_content
    assert "UNRELATED" not in first_content
    assert "MALICIOUS" not in first_content
    assert first_content.count("http_proxy=") == 1
    assert "http_proxy=stale" in first_content
    assert "HTTPS_PROXY=" in first_content
    assert "NO_PROXY=" in first_content
    assert "no_proxy=existing.internal,localhost,127.0.0.1,::1" in first_content
    assert "NO_PROXY=localhost,127.0.0.1,::1" in first_content
    assert "secret" not in first.stdout
    assert "secret" not in second.stdout


def test_proxy_environment_malformed_value_is_atomic_and_redacted(tmp_path: Path):
    source = tmp_path / "environment"
    target = tmp_path / "qagent.env"
    sensitive_value = "SENSITIVE-PROXY-VALUE"
    source.write_text(f'HTTPS_PROXY="unterminated-{sensitive_value}\n')
    original = b"QAGENT_SECRET=preserve-exactly\nCUSTOM=value\n"
    target.write_bytes(original)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/merge_proxy_environment.py"),
            "--source",
            str(source),
            "--target",
            str(target),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert target.read_bytes() == original
    assert sensitive_value not in result.stdout
    assert sensitive_value not in result.stderr


def test_enable_waits_for_runsv_before_removing_down_files():
    enabler = (ROOT / "scripts/enable_linux_runit.sh").read_text()
    validation_position = enabler.index("QAGENT_RUNSV_READY_TIMEOUT must be a positive integer")
    link_position = enabler.index("ln -s /etc/sv/qagent-backend")
    wait_position = enabler.index('wait_for_runsv "$name"')
    remove_down_position = enabler.index("rm -f /etc/sv/qagent-backend/down")

    assert validation_position < link_position
    assert wait_position < remove_down_position
    assert "QAGENT_RUNSV_READY_TIMEOUT" in enabler
    assert "runsvdir watches /etc/service" in enabler


def test_enable_readiness_failure_removes_links_while_down_files_remain():
    enabler = (ROOT / "scripts/enable_linux_runit.sh").read_text()
    readiness_failure_position = enabler.index('if ! wait_for_runsv "$name"')
    unlink_position = enabler.index(
        'unlink "/etc/service/$linked_name"', readiness_failure_position
    )
    clear_marker_position = enabler.index(
        'rm -f "$STATE_DIR/.single-writer-approved"', unlink_position
    )
    remove_down_position = enabler.index("rm -f /etc/sv/qagent-backend/down")

    assert readiness_failure_position < unlink_position < clear_marker_position
    assert clear_marker_position < remove_down_position
    assert "service links were removed, backup cron remains disabled" in enabler
    assert "any retained service link remains disabled by its down file" in enabler


def test_enable_failure_restores_disabled_state_before_reporting_failure():
    enabler = (ROOT / "scripts/enable_linux_runit.sh").read_text()
    up_position = enabler.index("if ! sv up")
    restore_down_position = enabler.index('touch "/etc/sv/$name/down"', up_position)
    disable_cron_position = enabler.index(
        "mv /etc/cron.d/qagent-backup /etc/cron.d/qagent-backup.disabled",
        restore_down_position,
    )
    clear_marker_position = enabler.index(
        'rm -f "$STATE_DIR/.single-writer-approved"', disable_cron_position
    )
    bounded_down_position = enabler.index(
        'sv -w "$RUNSV_READY_TIMEOUT" down', clear_marker_position
    )
    confirmed_down_condition = enabler.index(
        "if (( shutdown_failed == 0 )); then", bounded_down_position
    )
    unlink_position = enabler.index('unlink "/etc/service/$name"', confirmed_down_condition)
    success_cron_position = enabler.index(
        "mv /etc/cron.d/qagent-backup.disabled /etc/cron.d/qagent-backup",
        unlink_position,
    )
    success_marker_position = enabler.index(
        'touch "$STATE_DIR/.single-writer-approved"', success_cron_position
    )

    assert (
        up_position
        < restore_down_position
        < disable_cron_position
        < clear_marker_position
        < bounded_down_position
        < confirmed_down_condition
        < unlink_position
        < success_cron_position
        < success_marker_position
    )
    assert "service links remain managed by runit" in enabler
    assert "no approval marker was created" in enabler
    assert "service links were removed" in enabler


def test_deployment_verifier_requires_root_without_relaxing_supervise_permissions():
    verifier = (ROOT / "scripts/verify_linux_deployment.sh").read_text()
    assert "root access is required to inspect runit supervise state" in verifier
    assert "rerun with sudo" in verifier
    assert "exec sudo" not in verifier
    assert 'cat "/etc/service/$service/supervise/pid"' in verifier
    assert "chmod" not in verifier


def test_disable_keeps_links_until_processes_and_ports_are_quiescent():
    disabler = (ROOT / "scripts/disable_linux_runit.sh").read_text()
    down_intent_loop_position = disabler.index('touch "/etc/sv/$name/down"')
    disable_cron_position = disabler.index(
        "mv /etc/cron.d/qagent-backup /etc/cron.d/qagent-backup.disabled",
        down_intent_loop_position,
    )
    clear_marker_position = disabler.index(
        "rm -f /var/lib/qagent/.single-writer-approved", disable_cron_position
    )
    down_position = disabler.index('sv -w "$DISABLE_TIMEOUT" down')
    port_position = disabler.index('ss -H -ltn "sport = :$port"')
    unlink_position = disabler.index('unlink "/etc/service/$name"')

    assert (
        down_intent_loop_position
        < disable_cron_position
        < clear_marker_position
        < down_position
        < port_position
        < unlink_position
    )
    assert (
        "for name in qagent-frontend qagent-backend; do\n"
        '  touch "/etc/sv/$name/down"\n'
        "done\n"
        "if [[ -f /etc/cron.d/qagent-backup ]]"
    ) in disabler
    assert disabler.count("mv /etc/cron.d/qagent-backup ") == 1
    assert disabler.count("rm -f /var/lib/qagent/.single-writer-approved") == 1
    assert "QAGENT_DISABLE_TIMEOUT" in disabler
    assert "kill -0" in disabler
    assert "collect_process_tree" in disabler
    assert "service links were retained under runit" in disabler
    assert "sv down" not in disabler
    assert "|| true\n    unlink" not in disabler


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
        db.execute("CREATE TABLE automation_scheduler_state (enabled BOOLEAN, settings_json TEXT)")
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
        db.execute("CREATE TABLE automation_scheduler_state (enabled BOOLEAN, settings_json TEXT)")
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
