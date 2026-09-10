#!/usr/bin/env python3
"""Archive one read-only execution observation; never install or change cron."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def code_identity() -> dict:
    """Include dirty/untracked Python implementation, not just a Git revision."""
    files = sorted((ROOT / "backend/qagent").rglob("*.py")) + [Path(__file__).resolve()]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
        timeout=10, check=False,
    )
    return {"git_head": revision.stdout.strip() or None,
            "python_source_sha256": digest.hexdigest(),
            "scope": "backend/qagent/**/*.py and archive wrapper"}


def archive(db: Path, output: Path, timeout: float = 300) -> tuple[int, Path | None]:
    db, output = db.resolve(), output.resolve()
    # Keep artifacts away from the ledger file itself; no ledger connection here.
    if output == db or db.is_relative_to(output):
        raise ValueError("artifact directory must be separate from the database")
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".observation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75, None
        started = datetime.now(timezone.utc)
        command = [sys.executable, "-B", "-m", "qagent.execution.paper_observation",
                   "--db", str(db)]
        result = {"schema_version": "paper-observation-run-v1",
                  "started_at": started.isoformat(), "database_path": str(db),
                  "command": command, "automatic_promotion": False}
        try:
            result["code"] = code_identity()
            completed = subprocess.run(command, cwd=ROOT / "backend", capture_output=True,
                                       text=True, timeout=timeout, check=False)
            code = completed.returncode
            result.update(stdout=completed.stdout, stderr=completed.stderr)
            try:
                report = json.loads(completed.stdout)
            except (ValueError, TypeError):
                report = None
            result["observation"] = report
            result["source"] = ({key: report.get(key) for key in (
                "source_digest", "source_high_water", "source_event_count",
                "observation_digest", "sample_count")}
                if isinstance(report, dict) else None)
        except Exception as exc:
            code = 1
            result.update(error=type(exc).__name__, detail=str(exc))
        result.update(exit_code=code, finished_at=datetime.now(timezone.utc).isoformat())
        filename = started.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex + ".json"
        final = output / filename
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=output)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fchmod(stream.fileno(), 0o444)
                os.fsync(stream.fileno())
            # link is atomic and refuses to replace an existing run.
            os.link(temporary, final)
            os.unlink(temporary)
            directory_fd = os.open(output, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return (code if code >= 0 else 1), final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        code, path = archive(args.db, args.output_dir, args.timeout)
    except (OSError, ValueError) as exc:
        print(f"observation archive failed: {exc}", file=sys.stderr)
        return 1
    print(str(path) if path else "observation already running", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
