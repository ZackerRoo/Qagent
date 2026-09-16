#!/usr/bin/env python3
"""Safely close one scan breaker opened by the stale-candidate misclassification.

Preview is the default.  Execution changes only the ``scan:free`` breaker row,
records its before/after values in a private receipt, and never starts a scan or
the paper-trading scheduler.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Any
import uuid


SCOPE = "scan:free"
ALLOWED_LAST_ERRORS = frozenset(
    {
        "scan: scan status is candidate_data_partially_stale_filtered",
        "scan: scan status is candidate_data_stale_filtered",
        "scan: scan status is candidate_data_stale_after_retry",
    }
)
ROW_COLUMNS = (
    "scope_key",
    "state",
    "failure_count",
    "open_count",
    "next_probe_at",
    "last_error_fingerprint",
    "last_error_text",
    "half_open_cycle_slot",
    "probe_expires_at",
    "revision",
    "created_at",
    "updated_at",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("breaker_next_probe_missing")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _regular_database(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("database_must_be_regular_file")
    if resolved != path.absolute():
        raise ValueError("database_path_must_not_traverse_symlinks")
    return resolved


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            timeout=30,
        )
        connection.execute("PRAGMA query_only=ON")
    else:
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _load_row(connection: sqlite3.Connection) -> dict[str, Any] | None:
    columns = ",".join(ROW_COLUMNS)
    row = connection.execute(
        f"SELECT {columns} FROM automation_circuit_breakers WHERE scope_key=?",
        (SCOPE,),
    ).fetchone()
    return dict(row) if row is not None else None


def _already_recovered(row: dict[str, Any]) -> bool:
    return (
        row["state"] == "closed"
        and row["last_error_text"] in ALLOWED_LAST_ERRORS
        and int(row["failure_count"] or 0) == 0
        and int(row["open_count"] or 0) == 0
        and row["next_probe_at"] is None
        and row["half_open_cycle_slot"] is None
        and row["probe_expires_at"] is None
    )


def _validate_open_row(row: dict[str, Any] | None, *, now: datetime) -> None:
    if row is None:
        raise ValueError("scan_free_breaker_not_found")
    if _already_recovered(row):
        return
    if row["state"] != "open":
        raise ValueError("breaker_state_must_be_open")
    if row["last_error_text"] not in ALLOWED_LAST_ERRORS:
        raise ValueError("breaker_last_error_not_approved")
    if row["half_open_cycle_slot"] is not None or row["probe_expires_at"] is not None:
        raise ValueError("breaker_open_state_has_probe_ownership")
    if _as_utc(row["next_probe_at"]) <= now:
        raise ValueError("breaker_probe_is_already_due")


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    data = dict(payload)
    data["receipt_digest"] = _canonical_digest(payload)
    descriptor, pending_name = tempfile.mkstemp(prefix=".scan-breaker-", dir=path.parent)
    pending = Path(pending_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True, indent=2, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        pending.unlink(missing_ok=True)


def recover(
    database: Path,
    *,
    execute: bool = False,
    receipt_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or _utc_now()).astimezone(timezone.utc)
    database = _regular_database(database)
    with _connect(database, read_only=True) as connection:
        before = _load_row(connection)
    _validate_open_row(before, now=current)
    assert before is not None

    if _already_recovered(before):
        return {
            "schema": "scan-freshness-breaker-recovery-v1",
            "status": "already_recovered",
            "executed": False,
            "started_scan": False,
            "scope": SCOPE,
            "before": before,
            "after": before,
            "receipt": None,
        }
    if not execute:
        return {
            "schema": "scan-freshness-breaker-recovery-v1",
            "status": "planned",
            "executed": False,
            "started_scan": False,
            "scope": SCOPE,
            "before": before,
            "after": {
                **before,
                "state": "closed",
                "failure_count": 0,
                "open_count": 0,
                "next_probe_at": None,
                "half_open_cycle_slot": None,
                "probe_expires_at": None,
                "revision": int(before["revision"] or 0) + 1,
                "updated_at": current.isoformat(),
            },
            "receipt": None,
        }
    if receipt_dir is None:
        raise ValueError("receipt_dir_is_required_for_execute")
    if receipt_dir.exists():
        directory_metadata = receipt_dir.lstat()
        if stat.S_ISLNK(directory_metadata.st_mode) or not stat.S_ISDIR(
            directory_metadata.st_mode
        ):
            raise ValueError("receipt_dir_must_be_real_directory")
    else:
        receipt_dir.mkdir(mode=0o700, parents=False)
    if stat.S_IMODE(receipt_dir.stat().st_mode) & 0o077:
        raise ValueError("receipt_dir_must_not_be_group_or_world_accessible")

    receipt_path = receipt_dir / f"scan-breaker-recovery-{uuid.uuid4().hex}.json"
    prepared = {
        "schema": "scan-freshness-breaker-recovery-v1",
        "status": "prepared",
        "scope": SCOPE,
        "database": str(database),
        "prepared_at": current.isoformat(),
        "started_scan": False,
        "before": before,
    }
    _write_receipt(receipt_path, prepared)
    try:
        with _connect(database, read_only=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_row = _load_row(connection)
            transaction_now = current if now is not None else _utc_now()
            _validate_open_row(current_row, now=transaction_now)
            if current_row != before:
                raise ValueError("breaker_changed_since_preview")
            result = connection.execute(
                "UPDATE automation_circuit_breakers SET "
                "state='closed',failure_count=0,open_count=0,next_probe_at=NULL,"
                "half_open_cycle_slot=NULL,probe_expires_at=NULL,revision=revision+1,"
                "updated_at=? WHERE scope_key=? AND state='open' AND revision=? "
                "AND last_error_text=?",
                (
                    transaction_now.replace(tzinfo=None).isoformat(sep=" "),
                    SCOPE,
                    int(before["revision"] or 0),
                    before["last_error_text"],
                ),
            )
            if result.rowcount != 1:
                raise ValueError("breaker_compare_and_swap_failed")
            after = _load_row(connection)
            connection.commit()
    except Exception:
        failed = {
            **prepared,
            "status": "failed_before_verified_commit",
            "failed_at": _utc_now().isoformat(),
        }
        _write_receipt(receipt_path, failed)
        raise

    assert after is not None
    executed = {
        **prepared,
        "status": "executed",
        "executed_at": _utc_now().isoformat(),
        "after": after,
    }
    _write_receipt(receipt_path, executed)
    return {
        "schema": "scan-freshness-breaker-recovery-v1",
        "status": "recovered",
        "executed": True,
        "started_scan": False,
        "scope": SCOPE,
        "before": before,
        "after": after,
        "receipt": str(receipt_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        report = recover(
            args.database,
            execute=args.execute,
            receipt_dir=args.receipt_dir,
        )
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
