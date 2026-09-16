import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import stat

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "recover_scan_freshness_breaker.py"
)
SPEC = importlib.util.spec_from_file_location("recover_scan_freshness_breaker", SCRIPT)
recovery = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(recovery)


def _database(tmp_path: Path, *, error: str, state: str = "open", probe_delta=timedelta(hours=1)):
    path = tmp_path / "qagent.db"
    now = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE automation_circuit_breakers ("
            "scope_key TEXT PRIMARY KEY,state TEXT NOT NULL,failure_count INTEGER NOT NULL,"
            "open_count INTEGER NOT NULL,next_probe_at TEXT,last_error_fingerprint TEXT,"
            "last_error_text TEXT,half_open_cycle_slot TEXT,probe_expires_at TEXT,"
            "revision INTEGER NOT NULL,created_at TEXT,updated_at TEXT)"
        )
        values = (
            state,
            6,
            6,
            (now + probe_delta).replace(tzinfo=None).isoformat(sep=" "),
            error,
            "2026-09-16 08:00:00",
        )
        connection.execute(
            "INSERT INTO automation_circuit_breakers VALUES "
            "('scan:free',?,?,?,?,'fingerprint',?,NULL,NULL,9,'2026-09-16 07:00:00',?)",
            values,
        )
        connection.execute(
            "INSERT INTO automation_circuit_breakers VALUES "
            "('paper_update:free','open',2,2,'2026-09-16 12:00:00','other',"
            "'paper_update: provider timeout',NULL,NULL,3,'2026-09-16 07:00:00',"
            "'2026-09-16 08:00:00')"
        )
    return path, now


def _rows(path: Path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT * FROM automation_circuit_breakers ORDER BY scope_key"
        ).fetchall()


def test_preview_is_default_and_does_not_mutate(tmp_path):
    database, now = _database(
        tmp_path,
        error="scan: scan status is candidate_data_partially_stale_filtered",
    )
    before = _rows(database)

    report = recovery.recover(database, now=now)

    assert report["status"] == "planned"
    assert report["executed"] is False
    assert report["started_scan"] is False
    assert report["scope"] == "scan:free"
    assert report["before"]["state"] == "open"
    assert report["after"]["state"] == "closed"
    assert _rows(database) == before
    assert list(tmp_path.glob("scan-breaker-recovery-*.json")) == []


def test_execute_only_closes_scan_breaker_records_receipt_and_is_idempotent(tmp_path):
    database, now = _database(
        tmp_path,
        error="scan: scan status is candidate_data_stale_filtered",
    )
    before_rows = _rows(database)
    receipt_dir = tmp_path / "receipts"

    report = recovery.recover(
        database,
        execute=True,
        receipt_dir=receipt_dir,
        now=now,
    )

    assert report["status"] == "recovered"
    assert report["executed"] is True
    assert report["started_scan"] is False
    assert report["after"]["state"] == "closed"
    assert report["after"]["failure_count"] == 0
    assert report["after"]["open_count"] == 0
    assert report["after"]["next_probe_at"] is None
    assert report["after"]["revision"] == 10
    after_rows = _rows(database)
    assert after_rows[0] == before_rows[0]
    receipt_path = Path(report["receipt"])
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "executed"
    assert receipt["scope"] == "scan:free"
    assert receipt["before"]["state"] == "open"
    assert receipt["after"]["state"] == "closed"
    assert receipt["started_scan"] is False
    assert len(receipt["receipt_digest"]) == 64
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600

    repeated = recovery.recover(
        database,
        execute=True,
        receipt_dir=receipt_dir,
        now=now,
    )
    assert repeated["status"] == "already_recovered"
    assert repeated["executed"] is False
    assert _rows(database) == after_rows
    assert len(list(receipt_dir.iterdir())) == 1


@pytest.mark.parametrize(
    ("error", "state", "probe_delta", "expected"),
    [
        ("scan: provider timeout", "open", timedelta(hours=1), "not_approved"),
        (
            "scan: scan status is candidate_data_stale_after_retry",
            "half_open",
            timedelta(hours=1),
            "state_must_be_open",
        ),
        (
            "scan: scan status is candidate_data_stale_after_retry",
            "open",
            timedelta(0),
            "already_due",
        ),
    ],
)
def test_recovery_refuses_unapproved_or_unsafe_breakers(
    tmp_path,
    error,
    state,
    probe_delta,
    expected,
):
    database, now = _database(
        tmp_path,
        error=error,
        state=state,
        probe_delta=probe_delta,
    )
    before = _rows(database)

    with pytest.raises(ValueError, match=expected):
        recovery.recover(
            database,
            execute=True,
            receipt_dir=tmp_path / "receipts",
            now=now,
        )

    assert _rows(database) == before
    assert not (tmp_path / "receipts").exists()
