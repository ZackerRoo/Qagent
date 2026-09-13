#!/usr/bin/env python3
"""Opt-in bounded daily research archive; no database or account access.

Defaults to the Shanghai calendar date, without assuming it is a trading day.
Missing/partial upstream data is archived and returns nonzero, never backfilled.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts/collect_tushare_research.py"


def reject_nonfinite(_value):
    raise ValueError("invalid_json_number")


def redact(value, secrets):
    if isinstance(value, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return re.sub(r"https?://[^\s]+", "[REDACTED_URL]", value)
    return value


def atomic_archive(output, result):
    final = output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ-")
                      + uuid4().hex + ".json")
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=output)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o400)
            os.fsync(stream.fileno())
        os.link(temporary, final)
        directory_fd = os.open(output, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.unlink(temporary)
    return final


def archive(output: Path, symbols: list[str], requested_date: date, *,
            limit: int = 2, timeout: float = 180, minute_api: str = "rt_min"):
    if not 1 <= limit <= 5 or not 1 <= len(symbols) <= limit:
        raise ValueError("symbol_limit")
    if len(set(symbols)) != len(symbols) or any(
        not re.fullmatch(r"CN:(?:[03648]\d{5}|92\d{4})", s) for s in symbols
    ):
        raise ValueError("invalid_symbols")
    if not math.isfinite(timeout) or not 0 < timeout <= 240:
        raise ValueError("invalid_timeout")
    if minute_api not in {"rt_min", "stk_mins", "a_share_mins"}:
        raise ValueError("invalid_minute_api")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".research.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75, None
        secrets = [v for k, v in os.environ.items() if v and
                   re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I)]
        result = {"schema_version": "tushare-research-run-v1", "research_only": True,
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "requested_date": requested_date.isoformat(), "symbols": symbols,
                  "minute_api": minute_api, "timeout_per_symbol_seconds": timeout,
                  "runs": []}
        digest = hashlib.sha256()
        for path in [Path(__file__), COLLECTOR,
                     *sorted((ROOT / "backend/qagent/providers").glob("tushare_relay*.py"))]:
            digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
        result["source_sha256"] = digest.hexdigest()
        for symbol in symbols:
            run = {"instrument_id": symbol, "status": "error", "exit_code": 1}
            command = [sys.executable, "-B", str(COLLECTOR), "--symbol", symbol,
                       "--date", requested_date.isoformat(), "--fundamentals",
                       "--minute-api", minute_api]
            try:
                completed = subprocess.run(command, cwd=ROOT, capture_output=True,
                                           text=True, timeout=timeout, check=False)
                run["exit_code"] = completed.returncode
                # Raw stdout/stderr and arbitrary exceptions are never archived.
                report = json.loads(completed.stdout, parse_constant=reject_nonfinite)
                if not isinstance(report, dict):
                    raise ValueError("invalid_report")
                run["report"] = redact(report, secrets)
                sections = report.get("sections", {})
                valid = (report.get("instrument_id") == symbol
                         and report.get("requested_date") == requested_date.isoformat()
                         and report.get("research_only") is True
                         and isinstance(sections, dict)
                         and all(isinstance(sections.get(name), dict)
                                 and sections[name].get("status") == "ok"
                                 for name in ("daily", "fundamentals", "minutes")))
                run["status"] = ("ok" if completed.returncode == 0
                                 and report.get("status") == "ok" and valid else "incomplete")
            except subprocess.TimeoutExpired:
                run["error"] = "collector_timeout"
            except Exception:
                run["error"] = "collector_failed"
            result["runs"].append(run)
        code = 0 if all(r["status"] == "ok" for r in result["runs"]) else 1
        result.update(status="ok" if code == 0 else "incomplete", exit_code=code,
                      finished_at=datetime.now(timezone.utc).isoformat())
        return code, atomic_archive(output, result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--symbols", default="CN:000001,CN:600519")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--date", type=date.fromisoformat,
                        default=datetime.now(ZoneInfo("Asia/Shanghai")).date())
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--minute-api", choices=("rt_min", "stk_mins", "a_share_mins"),
                        default="rt_min")
    args = parser.parse_args(argv)
    try:
        code, path = archive(args.output_dir, args.symbols.split(","), args.date,
                             limit=args.limit, timeout=args.timeout, minute_api=args.minute_api)
    except Exception:
        print("research archive failed", file=sys.stderr)
        return 1
    print(json.dumps({"exit_code": code, "archive": str(path) if path else None,
                      "status": "locked" if code == 75 else "ok" if code == 0 else "incomplete"}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
