from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_linux_unattended_health.py"


class _HealthHandler(BaseHTTPRequestHandler):
    replay_blocked = False

    def do_GET(self):  # noqa: N802
        if self.path == "/api/health":
            self._json({"status": "ok"})
        elif self.path == "/api/paper-trades/execution-replay-readiness":
            self._json(
                {
                    "schema_version": "paper-execution-replay-readiness-v2",
                    "gate": "blocked" if self.replay_blocked else "collecting",
                    "buy": {"matched": 2, "target": 5},
                    "sell": {"matched": 1, "target": 3},
                    "unknown_count": 1 if self.replay_blocked else 0,
                    "audit_build_failures": 0,
                }
            )
        elif self.path == "/":
            body = b"<!doctype html><title>Qagent</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def _json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def _database(path: Path, *, enabled: bool = True, last_error: str | None = None) -> None:
    runtime = {
        "in_flight": False,
        "next_run_at": "2026-09-02T09:50:00+00:00",
        "last_error": last_error,
    }
    with sqlite3.connect(path) as database:
        database.execute(
            "CREATE TABLE automation_scheduler_state ("
            "state_id TEXT PRIMARY KEY, enabled BOOLEAN, settings_json TEXT, updated_at TEXT)"
        )
        database.execute(
            "INSERT INTO automation_scheduler_state VALUES ('default', ?, ?, ?)",
            (
                enabled,
                json.dumps(
                    {
                        "settings": {"interval_seconds": 1800},
                        "runtime": runtime,
                    }
                ),
                "2026-09-02T09:00:00+00:00",
            ),
        )


def _service_layout(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    service_dir = tmp_path / "service"
    sv_dir = tmp_path / "sv"
    service_dir.mkdir()
    for name in ("qagent-backend", "qagent-frontend"):
        definition = sv_dir / name
        definition.mkdir(parents=True)
        run = definition / "run"
        run.write_text("#!/bin/sh\nexit 0\n")
        run.chmod(0o755)
        (service_dir / name).symlink_to(definition)
    sv_command = tmp_path / "fake-sv"
    sv_command.write_text("#!/bin/sh\necho 'run: test: (pid 123) 10s'\n")
    sv_command.chmod(0o755)
    pgrep_command = tmp_path / "pgrep"
    pgrep_command.write_text("#!/bin/sh\necho 123\n")
    pgrep_command.chmod(0o755)
    cron = tmp_path / "qagent-backup"
    cron.write_text("30 19 * * * test backup\n")
    init_dir = tmp_path / "init.d"
    init_dir.mkdir()
    init_cron = init_dir / "cron"
    init_cron.write_text("#!/bin/sh\n")
    for runlevel in (2, 3, 4, 5):
        rc_dir = tmp_path / f"rc{runlevel}.d"
        rc_dir.mkdir()
        (rc_dir / "S01cron").symlink_to(init_cron)
    return service_dir, sv_dir, sv_command, pgrep_command, cron


def _run_check(
    tmp_path: Path,
    handler: type[_HealthHandler],
    *,
    enabled: bool = True,
    last_error: str | None = None,
    backup_disk_max_used_percent: str = "100",
    backup_min_free_bytes: str = "0",
) -> tuple[subprocess.CompletedProcess[str], bytes]:
    database = tmp_path / "qagent.db"
    _database(database, enabled=enabled, last_error=last_error)
    database_before = database.read_bytes()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup = backup_dir / "qagent-20260902T170000+0800.db"
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
    now_epoch = 1788339600  # 2026-09-02T09:40:00+00:00
    os.utime(backup, (now_epoch - 600, now_epoch - 600))
    service_dir, sv_dir, sv_command, pgrep_command, cron = _service_layout(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--database",
                str(database),
                "--backup-dir",
                str(backup_dir),
                "--backend-health-url",
                f"{base_url}/api/health",
                "--frontend-url",
                f"{base_url}/",
                "--replay-readiness-url",
                f"{base_url}/api/paper-trades/execution-replay-readiness",
                "--service-dir",
                str(service_dir),
                "--sv-dir",
                str(sv_dir),
                "--sv-command",
                str(sv_command),
                "--pgrep-command",
                str(pgrep_command),
                "--backup-cron",
                str(cron),
                "--backup-disk-max-used-percent",
                backup_disk_max_used_percent,
                "--backup-min-free-bytes",
                backup_min_free_bytes,
                "--sysv-rc-root",
                str(tmp_path),
                "--now",
                "2026-09-02T09:40:00+00:00",
            ],
            text=True,
            capture_output=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return result, database_before


def test_unattended_health_reports_healthy_without_mutating_database(tmp_path: Path):
    class HealthyHandler(_HealthHandler):
        replay_blocked = False

    result, database_before = _run_check(tmp_path, HealthyHandler)

    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "qagent-unattended-health-v1"
    assert payload["status"] == "ok"
    assert payload["read_only"] is True
    assert payload["failed_checks"] == 0
    assert {check["status"] for check in payload["checks"]} == {"pass"}
    assert {check["name"] for check in payload["checks"]} >= {
        "backup_disk_usage",
        "next_backup_capacity",
        "runit_supervisor",
        "cron_daemon",
        "cron_boot_enablement",
    }
    assert (tmp_path / "qagent.db").read_bytes() == database_before


def test_unattended_health_reports_next_backup_capacity_risk_without_writing(
    tmp_path: Path,
):
    class HealthyHandler(_HealthHandler):
        replay_blocked = False

    result, database_before = _run_check(
        tmp_path,
        HealthyHandler,
        backup_min_free_bytes="999999999999999999",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    failed = {check["name"]: check for check in payload["checks"] if check["status"] == "fail"}
    assert set(failed) == {"next_backup_capacity"}
    capacity = failed["next_backup_capacity"]
    assert capacity["required_bytes"] > capacity["available_bytes"]
    assert capacity["estimated_backup_bytes"] == (tmp_path / "qagent.db").stat().st_size
    assert (tmp_path / "qagent.db").read_bytes() == database_before


def test_unattended_health_fails_when_backup_filesystem_usage_exceeds_limit(
    tmp_path: Path,
):
    class HealthyHandler(_HealthHandler):
        replay_blocked = False

    result, database_before = _run_check(
        tmp_path,
        HealthyHandler,
        backup_disk_max_used_percent="0.000001",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    failed = {check["name"]: check for check in payload["checks"] if check["status"] == "fail"}
    assert set(failed) == {"backup_disk_usage"}
    assert failed["backup_disk_usage"]["used_percent"] > 0
    assert (tmp_path / "qagent.db").read_bytes() == database_before


def test_unattended_health_fails_for_scheduler_error_and_blocked_replay(tmp_path: Path):
    class BlockedHandler(_HealthHandler):
        replay_blocked = True

    result, database_before = _run_check(
        tmp_path,
        BlockedHandler,
        last_error="provider timeout",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "critical"
    failed = {check["name"]: check for check in payload["checks"] if check["status"] == "fail"}
    assert set(failed) == {"scheduler", "replay_readiness"}
    assert failed["scheduler"]["last_error"] == "provider timeout"
    assert failed["replay_readiness"]["unknown_count"] == 1
    assert (tmp_path / "qagent.db").read_bytes() == database_before
