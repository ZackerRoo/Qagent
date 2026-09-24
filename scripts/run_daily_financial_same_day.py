#!/usr/bin/env python3
"""Run one bounded daily financial attempt and then its forward consumer.

This is deliberately a versioned bundle entrypoint rather than a shell
snippet in cron.  It verifies both immutable research bundles before starting
work, records a private attempt receipt for every outcome, and never starts
the forward consumer unless the same-day daily artifact is observed (or was
already completed for that day).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from uuid import uuid4


PROTOCOL = "financial-daily-same-day-wrapper-v1"
MAX_OUTPUT_BYTES = 64 * 1024
DAILY_BUNDLE = Path("/opt/qagent-research/daily-financial-20260922-v9")
FORWARD_BUNDLE = Path("/opt/qagent-research/financial-forward-20260922-v11")
ATTEMPT_DIR = Path("/var/lib/qagent-research/daily-financial/wrapper-attempts")
BACKEND = Path("/opt/qagent/current/backend")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_bundle(bundle: Path, schema: str) -> str:
    manifest_path = bundle / "manifest.json"
    if bundle.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("unsafe_bundle")
    manifest = json.loads(manifest_path.read_text())
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if manifest.get("schema") != schema or not isinstance(files, dict) or not files:
        raise ValueError("invalid_bundle_manifest")
    entries = list(bundle.rglob("*"))
    if any(path.is_symlink() for path in entries):
        raise ValueError("bundle_symlink_rejected")
    actual = {str(path.relative_to(bundle)): path for path in entries if path.is_file()}
    if set(actual) != set(files) | {"manifest.json"}:
        raise ValueError("bundle_file_set_mismatch")
    for name, expected in files.items():
        path = bundle / name
        if (not isinstance(name, str) or not isinstance(expected, str)
                or path.is_symlink() or not path.is_file()
                or stat.S_IMODE(path.stat().st_mode) & 0o022 or _digest(path) != expected):
            raise ValueError("bundle_file_mismatch")
    return _digest(manifest_path)


def _output(value: str | bytes | None) -> str:
    raw = (value or "").encode() if isinstance(value, str) else (value or b"")
    return raw[-MAX_OUTPUT_BYTES:].decode(errors="replace")


def _result(value: str) -> dict | None:
    for line in reversed(value.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _publish(directory: Path, report: dict) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise ValueError("unsafe_attempt_directory")
    target = directory / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S.%fZ}-{uuid4().hex}.json"
    raw = (json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n").encode()
    fd, pending = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            os.fchmod(stream.fileno(), 0o400)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(pending, target, follow_symlinks=False)
        parent = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    return target


def _command(script: Path, arguments: tuple[str, ...]) -> list[str]:
    return [sys.executable, "-B", str(script), *arguments]


def run(*, daily_bundle: Path = DAILY_BUNDLE, forward_bundle: Path = FORWARD_BUNDLE,
        attempt_dir: Path = ATTEMPT_DIR, backend: Path = BACKEND) -> tuple[int, dict]:
    report = {"protocol": PROTOCOL, "started_at": datetime.now(timezone.utc).isoformat(),
              "daily": None, "forward": None}
    try:
        report["daily_manifest_sha256"] = _validate_bundle(daily_bundle, "daily-financial-bundle-v1")
        report["forward_manifest_sha256"] = _validate_bundle(forward_bundle, "financial-forward-bundle-v1")
        daily = subprocess.run(_command(daily_bundle / "scripts/collect_daily_documented_research.py", (
            "--base-url", "http://127.0.0.1:8000", "--source", "datahubco", "--candidate-pool",
            "--period", "20260630", "--today-close", "--budget-seconds", "600", "--output-dir",
            "/var/lib/qagent-research/daily-financial", "--bounded-same-day", "--daily-frozen-industry")),
            capture_output=True, text=True, check=False)
        daily_result = _result(daily.stdout)
        report["daily"] = {"returncode": daily.returncode, "result": daily_result,
                           "stdout": _output(daily.stdout), "stderr": _output(daily.stderr)}
        if daily.returncode != 0 or not daily_result or daily_result.get("status") not in {
                "observed", "already_completed"}:
            report["status"] = "daily_not_successful"
            return daily.returncode or 2, report
        env = os.environ.copy()
        env["PYTHONPATH"] = str(backend) + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        forward = subprocess.run(_command(forward_bundle / "scripts/run_financial_forward_research.py", (
            "--daily-dir", "/var/lib/qagent-research/daily-financial", "--baseline-dir",
            "/var/lib/qagent-research/g2-forward-results/signals", "--signal-dir",
            "/var/lib/qagent-research/financial-forward-signals", "--evaluation-dir",
            "/var/lib/qagent-research/financial-forward-evaluations", "--run-dir",
            "/var/lib/qagent-research/financial-forward-runs", "--db", "/var/lib/qagent/qagent.db",
            "--provider-mode", "free", "--budget-seconds", "300", "--daily-baseline-source-dir",
            "/var/lib/qagent-research/g2-forward-sources", "--daily-baseline-frozen-dir",
            "/var/lib/qagent-research/g2-frozen-v1", "--daily-baseline-rank-dir",
            "/var/lib/qagent-research/financial-daily-ranks")), capture_output=True, text=True,
            check=False, env=env)
        report["forward"] = {"returncode": forward.returncode, "result": _result(forward.stdout),
                             "stdout": _output(forward.stdout), "stderr": _output(forward.stderr)}
        report["status"] = "completed" if forward.returncode == 0 else "forward_failed"
        return forward.returncode, report
    except Exception as error:
        report["status"] = "wrapper_error"
        report["error"] = type(error).__name__
        return 2, report
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            report["attempt"] = str(_publish(attempt_dir, report))
        except Exception as error:
            report["attempt_error"] = type(error).__name__


def main(**options) -> int:
    code, report = run(**options)
    print(json.dumps({"status": report["status"], "attempt": report.get("attempt"),
                      "attempt_error": report.get("attempt_error"), "daily": report["daily"],
                      "forward": report["forward"]}, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
