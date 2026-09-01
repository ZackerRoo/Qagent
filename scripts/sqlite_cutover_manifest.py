#!/usr/bin/env python3
"""Create stable ledger hashes and fail closed on resumable runtime state."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


LEDGER_TABLES = (
    "watchlist_items",
    "positions",
    "alert_rules",
    "universes",
    "paper_trades",
    "paper_trade_events",
    "paper_account_settings",
    "paper_research_baselines",
)
JOB_TABLES = (
    "full_market_scan_jobs",
    "historical_backfill_jobs",
    "walk_forward_jobs",
    "paper_dual_track_jobs",
)
NON_TERMINAL = ("queued", "running")
ACTIVE_CYCLE_STATUSES = ("running",)
ACTIVE_STAGE_STATUSES = ("running",)


def _tables(db: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _table_hash(db: sqlite3.Connection, table: str) -> dict[str, object]:
    columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
    order = ", ".join(f'"{name}"' for name in columns)
    digest = hashlib.sha256()
    count = 0
    for row in db.execute(f'SELECT * FROM "{table}" ORDER BY {order}'):
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)
        digest.update(encoded.encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    uri = f"file:{args.database}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise SystemExit("database quick_check failed")
        tables = _tables(db)
        manifest = {
            "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
            "ledger": {
                table: _table_hash(db, table)
                for table in LEDGER_TABLES
                if table in tables
            },
        }
        if args.preflight:
            if "automation_scheduler_state" in tables:
                scheduler_columns = {
                    row[1] for row in db.execute("PRAGMA table_info(automation_scheduler_state)")
                }
                selected = "enabled, settings_json" if "settings_json" in scheduler_columns else "enabled"
                for row in db.execute(f"SELECT {selected} FROM automation_scheduler_state"):
                    if row[0]:
                        raise SystemExit("cutover blocked: automation scheduler is enabled")
                    if len(row) == 1:
                        continue
                    try:
                        payload = json.loads(row[1] or "{}")
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise SystemExit(
                            "cutover blocked: scheduler settings_json is invalid"
                        ) from exc
                    runtime = payload.get("runtime") if isinstance(payload, dict) else None
                    if not isinstance(runtime, dict) or runtime.get("in_flight") is not False:
                        raise SystemExit(
                            "cutover blocked: scheduler runtime.in_flight is not explicitly false"
                        )
            for table in JOB_TABLES:
                if table not in tables:
                    continue
                placeholders = ",".join("?" for _ in NON_TERMINAL)
                count = db.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE status IN ({placeholders})',
                    NON_TERMINAL,
                ).fetchone()[0]
                if count:
                    raise SystemExit(f"cutover blocked: {table} has {count} queued/running jobs")
            for table, active in (
                ("automation_cycles", ACTIVE_CYCLE_STATUSES),
                ("automation_cycle_stages", ACTIVE_STAGE_STATUSES),
            ):
                if table not in tables:
                    continue
                placeholders = ",".join("?" for _ in active)
                count = db.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE status IN ({placeholders})',
                    active,
                ).fetchone()[0]
                if count:
                    raise SystemExit(f"cutover blocked: {table} has {count} in-flight rows")

    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
