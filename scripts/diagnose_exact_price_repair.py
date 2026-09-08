#!/usr/bin/env python3
"""Read bounded saved shadow repair telemetry; never call providers or scheduler.

Local: python scripts/diagnose_exact_price_repair.py --database data/qagent.db
Remote: ssh HOST python3 - --database /var/lib/qagent/qagent.db < this_script
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time


def exact_price_fields(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if "exact_price" in key:
            result[key] = item
        elif isinstance(item, dict):
            nested = exact_price_fields(item)
            if nested:
                result[key] = nested
    return result


def diagnose(database: Path, limit: int = 20) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    deadline = time.monotonic() + 10
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        rows = conn.execute(
            "SELECT cycle_slot, stage_key, status, attempt_count, updated_at, output_json "
            "FROM automation_cycle_stages WHERE stage_key LIKE '%shadow%' "
            "ORDER BY updated_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return {
        "read_only": True, "provider_calls": 0, "limit": limit,
        "stages": [
            {"cycle_slot": row[0], "stage_key": row[1], "status": row[2],
             "attempt_count": row[3], "updated_at": row[4],
             "exact_price": exact_price_fields(json.loads(row[5]))}
            for row in rows
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(diagnose(args.database, args.limit), ensure_ascii=False, indent=2))
