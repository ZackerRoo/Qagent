#!/usr/bin/env python3
"""Read-only health check for the unattended Linux Qagent deployment."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


EXIT_OK = 0
EXIT_UNHEALTHY = 1
EXIT_USAGE = 2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result(name: str, status: str, detail: str, **fields: object) -> dict[str, object]:
    return {"name": name, "status": status, "detail": detail, **fields}


def _http_json(url: str, timeout: float) -> object:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_reachable(url: str, timeout: float) -> tuple[int, int]:
    request = Request(url, headers={"Accept": "text/html"}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        body = response.read(1024)
        return response.status, len(body)


def _check_backend(url: str, timeout: float) -> dict[str, object]:
    try:
        payload = _http_json(url, timeout)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        return _result("backend_health", "fail", f"backend health unavailable: {exc}")
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return _result("backend_health", "fail", "backend health payload is not ok")
    return _result("backend_health", "pass", "backend health is ok")


def _check_frontend(url: str, timeout: float) -> dict[str, object]:
    try:
        status, body_bytes = _http_reachable(url, timeout)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return _result("frontend_reachability", "fail", f"frontend unavailable: {exc}")
    if not 200 <= status < 300 or body_bytes == 0:
        return _result(
            "frontend_reachability",
            "fail",
            "frontend returned an invalid response",
            http_status=status,
        )
    return _result(
        "frontend_reachability",
        "pass",
        "frontend is reachable",
        http_status=status,
    )


def _sqlite_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_sqlite_uri(path), uri=True, timeout=30)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _quick_check(name: str, path: Path) -> dict[str, object]:
    if not path.is_file():
        return _result(
            name, "fail", f"SQLite database is missing: {path}", path=str(path)
        )
    try:
        with _open_read_only(path) as database:
            row = database.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        return _result(
            name, "fail", f"SQLite quick_check failed: {exc}", path=str(path)
        )
    if row != ("ok",):
        return _result(
            name, "fail", f"SQLite quick_check returned {row!r}", path=str(path)
        )
    return _result(name, "pass", "SQLite quick_check is ok", path=str(path))


def _check_scheduler(
    database_path: Path,
    *,
    now: datetime,
    max_running_seconds: int,
    overdue_grace_seconds: int,
) -> dict[str, object]:
    try:
        with _open_read_only(database_path) as database:
            table = database.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='automation_scheduler_state'"
            ).fetchone()
            if table is None:
                return _result("scheduler", "fail", "scheduler state table is missing")
            row = database.execute(
                "SELECT enabled, settings_json, updated_at "
                "FROM automation_scheduler_state WHERE state_id = 'default'"
            ).fetchone()
    except sqlite3.Error as exc:
        return _result("scheduler", "fail", f"scheduler state cannot be read: {exc}")
    if row is None:
        return _result("scheduler", "fail", "scheduler state row is missing")

    enabled = bool(row[0])
    try:
        payload = json.loads(row[1] or "{}")
    except (TypeError, json.JSONDecodeError):
        return _result("scheduler", "fail", "scheduler settings_json is invalid")
    if not isinstance(payload, dict):
        return _result("scheduler", "fail", "scheduler state payload is invalid")
    settings = payload.get("settings", payload)
    runtime = payload.get("runtime", {})
    if not isinstance(settings, dict) or not isinstance(runtime, dict):
        return _result("scheduler", "fail", "scheduler settings/runtime is invalid")

    interval = settings.get("interval_seconds", 1800)
    try:
        interval_seconds = int(interval)
    except (TypeError, ValueError):
        return _result("scheduler", "fail", "scheduler interval is invalid")
    in_flight = bool(runtime.get("in_flight", False))
    last_started = _parse_datetime(runtime.get("last_started_at"))
    next_run = _parse_datetime(runtime.get("next_run_at"))
    last_error = runtime.get("last_error")
    last_error_text = str(last_error).strip() if last_error is not None else ""

    status = "idle"
    overdue_seconds = 0
    running_seconds = 0
    problems: list[str] = []
    if not enabled:
        problems.append("scheduler_disabled")
    if last_error_text:
        problems.append("last_error")
    if in_flight:
        status = "running"
        if last_started is None:
            problems.append("running_without_start_time")
        else:
            running_seconds = max(int((now - last_started).total_seconds()), 0)
            if running_seconds > max_running_seconds:
                problems.append("running_too_long")
    elif next_run is None:
        problems.append("next_run_missing")
    elif next_run < now:
        overdue_seconds = int((now - next_run).total_seconds())
        if overdue_seconds > overdue_grace_seconds:
            problems.append("scheduler_overdue")

    result_status = "fail" if problems else "pass"
    detail = "scheduler checkpoint is healthy" if not problems else ",".join(problems)
    return _result(
        "scheduler",
        result_status,
        detail,
        enabled=enabled,
        scheduler_status=status,
        interval_seconds=interval_seconds,
        overdue_seconds=overdue_seconds,
        running_seconds=running_seconds,
        last_error=last_error_text or None,
        checkpoint_updated_at=str(row[2]) if row[2] is not None else None,
    )


def _check_replay_readiness(url: str, timeout: float) -> dict[str, object]:
    try:
        payload = _http_json(url, timeout)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        return _result(
            "replay_readiness", "fail", f"replay readiness unavailable: {exc}"
        )
    if not isinstance(payload, dict):
        return _result(
            "replay_readiness", "fail", "replay readiness payload is invalid"
        )
    schema = payload.get("schema_version")
    gate = payload.get("gate")
    unknown_count = payload.get("unknown_count")
    audit_failures = payload.get("audit_build_failures")
    buy = payload.get("buy") if isinstance(payload.get("buy"), dict) else {}
    sell = payload.get("sell") if isinstance(payload.get("sell"), dict) else {}
    if schema != "paper-execution-replay-readiness-v2" or gate not in {
        "collecting",
        "blocked",
        "ready_for_shadow",
    }:
        return _result(
            "replay_readiness", "fail", "replay readiness contract is invalid"
        )
    blocked = gate == "blocked" or bool(unknown_count) or bool(audit_failures)
    return _result(
        "replay_readiness",
        "fail" if blocked else "pass",
        "replay evidence is blocked" if blocked else "replay readiness is visible",
        gate=gate,
        buy_matched=buy.get("matched"),
        buy_target=buy.get("target"),
        sell_matched=sell.get("matched"),
        sell_target=sell.get("target"),
        unknown_count=unknown_count,
        audit_build_failures=audit_failures,
    )


def _check_backup(
    backup_dir: Path,
    *,
    now: datetime,
    max_age_seconds: int,
) -> dict[str, object]:
    candidates = [path for path in backup_dir.glob("qagent-*.db") if path.is_file()]
    if not candidates:
        return _result("latest_backup", "fail", f"no backup found in {backup_dir}")
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    modified_at = datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc)
    age_seconds = max(int((now - modified_at).total_seconds()), 0)
    integrity = _quick_check("latest_backup", latest)
    integrity["age_seconds"] = age_seconds
    integrity["modified_at"] = modified_at.isoformat()
    if integrity["status"] == "pass" and age_seconds > max_age_seconds:
        integrity["status"] = "fail"
        integrity["detail"] = "latest backup is stale"
    return integrity


def _check_backup_cron(path: Path) -> dict[str, object]:
    if not path.is_file():
        return _result("backup_schedule", "fail", f"backup cron is not enabled: {path}")
    return _result("backup_schedule", "pass", "backup cron is enabled", path=str(path))


def _backup_capacity_checks(
    database_path: Path,
    backup_dir: Path,
    *,
    max_used_percent: float,
    minimum_free_bytes: int,
) -> list[dict[str, object]]:
    try:
        filesystem = os.statvfs(backup_dir)
    except OSError as exc:
        failure = _result(
            "backup_disk_usage",
            "fail",
            f"backup filesystem cannot be inspected: {exc}",
            path=str(backup_dir),
        )
        return [
            failure,
            _result(
                "next_backup_capacity",
                "fail",
                "next backup capacity cannot be estimated",
                path=str(backup_dir),
            ),
        ]

    total_bytes = filesystem.f_blocks * filesystem.f_frsize
    available_bytes = filesystem.f_bavail * filesystem.f_frsize
    used_percent = (
        ((total_bytes - available_bytes) * 100.0 / total_bytes)
        if total_bytes
        else 100.0
    )
    disk_status = "fail" if used_percent >= max_used_percent else "pass"
    disk = _result(
        "backup_disk_usage",
        disk_status,
        "backup filesystem usage is too high"
        if disk_status == "fail"
        else "backup filesystem usage is within limit",
        path=str(backup_dir),
        total_bytes=total_bytes,
        available_bytes=available_bytes,
        used_percent=round(used_percent, 2),
        max_used_percent=max_used_percent,
    )

    if not database_path.is_file():
        capacity = _result(
            "next_backup_capacity",
            "fail",
            "next backup size cannot be estimated because the database is missing",
            database_path=str(database_path),
        )
    else:
        try:
            with _open_read_only(database_path) as database:
                page_count = int(database.execute("PRAGMA page_count").fetchone()[0])
                page_size = int(database.execute("PRAGMA page_size").fetchone()[0])
            estimated_backup_bytes = max(
                database_path.stat().st_size, page_count * page_size
            )
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            capacity = _result(
                "next_backup_capacity",
                "fail",
                f"next backup size cannot be estimated: {exc}",
                database_path=str(database_path),
            )
        else:
            required_bytes = estimated_backup_bytes + minimum_free_bytes
            at_risk = available_bytes < required_bytes
            capacity = _result(
                "next_backup_capacity",
                "fail" if at_risk else "pass",
                "insufficient space for the next atomic backup"
                if at_risk
                else "space is available for the next atomic backup",
                database_path=str(database_path),
                backup_dir=str(backup_dir),
                available_bytes=available_bytes,
                estimated_backup_bytes=estimated_backup_bytes,
                minimum_free_after_backup_bytes=minimum_free_bytes,
                required_bytes=required_bytes,
            )
    return [disk, capacity]


def _check_process(
    name: str, process_names: tuple[str, ...], pgrep_command: str
) -> dict[str, object]:
    errors: list[str] = []
    for process_name in process_names:
        try:
            result = subprocess.run(
                [pgrep_command, "-x", process_name],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
            continue
        if result.returncode == 0 and result.stdout.strip():
            return _result(
                name,
                "pass",
                f"{process_name} daemon is running",
                process_name=process_name,
            )
    detail = "daemon is not running"
    if errors:
        detail = f"process inspection unavailable: {errors[-1]}"
    return _result(name, "fail", detail, process_names=list(process_names))


def _check_sysv_cron_enablement(rc_root: Path) -> dict[str, object]:
    missing: list[str] = []
    links: dict[str, str] = {}
    for runlevel in (2, 3, 4, 5):
        matches = sorted(
            path
            for path in (rc_root / f"rc{runlevel}.d").glob("S*cron")
            if path.is_symlink()
        )
        if not matches:
            missing.append(str(runlevel))
            continue
        links[str(runlevel)] = str(matches[0])
    if missing:
        return _result(
            "cron_boot_enablement",
            "fail",
            "cron is not enabled for required SysV runlevels",
            missing_runlevels=missing,
            enabled_links=links,
        )
    return _result(
        "cron_boot_enablement",
        "pass",
        "cron is enabled for SysV runlevels 2-5",
        enabled_links=links,
    )


def _check_service(
    name: str,
    *,
    service_dir: Path,
    sv_dir: Path,
    sv_command: str,
) -> dict[str, object]:
    active_path = service_dir / name
    definition_path = sv_dir / name
    run_path = definition_path / "run"
    down_path = definition_path / "down"
    problems: list[str] = []
    if not active_path.exists():
        problems.append("not_enabled")
    elif active_path.resolve() != definition_path.resolve():
        problems.append("unexpected_service_target")
    if not run_path.is_file() or not os.access(run_path, os.X_OK):
        problems.append("run_definition_missing")
    if down_path.exists():
        problems.append("down_policy_present")
    sv_output = ""
    if not problems:
        try:
            result = subprocess.run(
                [sv_command, "status", str(active_path)],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            sv_output = (result.stdout or result.stderr).strip()
            if result.returncode != 0 or not sv_output.startswith("run:"):
                problems.append("not_running")
        except (OSError, subprocess.TimeoutExpired):
            problems.append("sv_status_unavailable")
    return _result(
        f"service_{name}",
        "fail" if problems else "pass",
        ",".join(problems) if problems else "runit service is enabled and running",
        restart_policy="runit_always" if not problems else "unverified",
        sv_status=sv_output or None,
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _percentage(value: str) -> float:
    parsed = float(value)
    if not 0 < parsed <= 100:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 100")
    return parsed


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Qagent unattended Linux deployment health check."
    )
    parser.add_argument("--database", default="/var/lib/qagent/qagent.db")
    parser.add_argument("--backup-dir", default="/var/backups/qagent")
    parser.add_argument("--backup-max-age-seconds", type=_positive_int, default=129600)
    parser.add_argument(
        "--backup-disk-max-used-percent", type=_percentage, default=85.0
    )
    parser.add_argument(
        "--backup-min-free-bytes", type=_non_negative_int, default=10737418240
    )
    parser.add_argument(
        "--scheduler-max-running-seconds", type=_positive_int, default=21600
    )
    parser.add_argument(
        "--scheduler-overdue-grace-seconds", type=_positive_int, default=60
    )
    parser.add_argument(
        "--backend-health-url", default="http://127.0.0.1:8000/api/health"
    )
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5173/")
    parser.add_argument(
        "--replay-readiness-url",
        default="http://127.0.0.1:8000/api/paper-trades/execution-replay-readiness",
    )
    parser.add_argument("--http-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--service-dir", default="/etc/service")
    parser.add_argument("--sv-dir", default="/etc/sv")
    parser.add_argument("--sv-command", default="sv")
    parser.add_argument("--pgrep-command", default="pgrep")
    parser.add_argument("--sysv-rc-root", default="/etc")
    parser.add_argument("--backup-cron", default="/etc/cron.d/qagent-backup")
    parser.add_argument("--now", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv or sys.argv[1:])
    now = _parse_datetime(args.now) if args.now else _utc_now()
    if now is None:
        print(json.dumps({"status": "usage_error", "detail": "invalid --now"}))
        return EXIT_USAGE
    database_path = Path(args.database)
    backup_dir = Path(args.backup_dir)
    checks = [
        _check_backend(args.backend_health_url, args.http_timeout_seconds),
        _check_frontend(args.frontend_url, args.http_timeout_seconds),
        _quick_check("production_database", database_path),
        _check_scheduler(
            database_path,
            now=now,
            max_running_seconds=args.scheduler_max_running_seconds,
            overdue_grace_seconds=args.scheduler_overdue_grace_seconds,
        ),
        _check_replay_readiness(args.replay_readiness_url, args.http_timeout_seconds),
        _check_backup(
            backup_dir,
            now=now,
            max_age_seconds=args.backup_max_age_seconds,
        ),
        *_backup_capacity_checks(
            database_path,
            backup_dir,
            max_used_percent=args.backup_disk_max_used_percent,
            minimum_free_bytes=args.backup_min_free_bytes,
        ),
        _check_backup_cron(Path(args.backup_cron)),
        _check_process("runit_supervisor", ("runsvdir",), args.pgrep_command),
        _check_process("cron_daemon", ("cron", "crond"), args.pgrep_command),
        _check_sysv_cron_enablement(Path(args.sysv_rc_root)),
        _check_service(
            "qagent-backend",
            service_dir=Path(args.service_dir),
            sv_dir=Path(args.sv_dir),
            sv_command=args.sv_command,
        ),
        _check_service(
            "qagent-frontend",
            service_dir=Path(args.service_dir),
            sv_dir=Path(args.sv_dir),
            sv_command=args.sv_command,
        ),
    ]
    failures = sum(check["status"] == "fail" for check in checks)
    payload = {
        "schema_version": "qagent-unattended-health-v1",
        "status": "ok" if failures == 0 else "critical",
        "observed_at": now.isoformat(),
        "read_only": True,
        "failed_checks": failures,
        "checks": checks,
    }
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return EXIT_OK if failures == 0 else EXIT_UNHEALTHY


if __name__ == "__main__":
    raise SystemExit(main())
