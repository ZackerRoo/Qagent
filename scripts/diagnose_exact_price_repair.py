#!/usr/bin/env python3
"""Read bounded saved shadow repair telemetry; never call providers or scheduler.

Local: python scripts/diagnose_exact_price_repair.py --database data/qagent.db
Remote: ssh HOST python3 - --database /var/lib/qagent/qagent.db < this_script
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
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


def summarize(stages: list[dict]) -> dict:
    """Summarize saved counters without treating candidate sums as unique gaps."""
    grouped = defaultdict(list)
    for stage in stages:
        grouped[stage["stage_key"]].append(stage)
    summaries = {}
    for key, group in grouped.items():
        observations = []
        seen_keys = set()
        all_scopes = set()
        for stage in sorted(group, key=lambda item: item["updated_at"]):
            health = stage["exact_price"].get("data_health", {})
            prefix = key + "_exact_price_"
            metrics = {}
            for field in ("requested", "cache_hits", "provider_requested", "provider_batches",
                          "repaired", "deferred_by_budget", "unresolved", "retryable"):
                try:
                    raw = health.get(prefix + field)
                    metrics[field] = int(raw) if raw is not None else None
                except (TypeError, ValueError):
                    metrics[field] = None
            attempted = set()
            trace_entries = 0
            scopes = set()
            first_index = None
            for token in str(health.get(prefix + "batch_trace", "")).split(" | "):
                parts = token.split(":", 3)
                if len(parts) != 4:
                    continue
                scope, position, day, instruments = parts
                try:
                    index, count = map(int, position.split("/"))
                    datetime.strptime(day, "%Y-%m-%d")
                    if not 0 <= index < count:
                        continue
                except ValueError:
                    continue
                scopes.add(scope)
                trace_entries += 1
                if first_index is None:
                    first_index = index
                attempted.update((day, symbol) for symbol in instruments.split(",")
                                 if re.fullmatch(r"CN:\d{6}", symbol))
            observations.append({
                "updated_at": stage["updated_at"],
                "status": stage.get("status", "not_recorded"), **metrics,
                "aggregation": health.get(prefix + "aggregation", "not_recorded"),
                "budget_reason": health.get(prefix + "budget_exhausted_reason", "not_recorded"),
                "reason_mix": health.get(prefix + "reason_mix", "not_recorded"),
                "trace_entries": trace_entries,
                "trace_unique_instrument_dates_lower_bound": len(attempted),
                "trace_repeated_instrument_dates_from_earlier_observations": len(attempted & seen_keys),
                "trace_scopes": sorted(scopes), "trace_first_batch_index": first_index,
            })
            seen_keys.update(attempted)
            all_scopes.update(scopes)
        def timestamp(value):
            parsed = datetime.fromisoformat(value)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        hours = (timestamp(observations[-1]["updated_at"]) -
                 timestamp(observations[0]["updated_at"])).total_seconds() / 3600
        repaired = [item["repaired"] for item in observations[1:]]
        summaries[key] = {
            "observations": observations,
            "unique_pending_requirements": None,
            "unique_pending_requirements_reason": "saved counters lack requirement identities",
            "trace_unique_instrument_dates_lower_bound": len(seen_keys),
            "trace_distinct_scope_prefixes": len(all_scopes),
            "budget_reason_counts": dict(Counter(item["budget_reason"] for item in observations)),
            "elapsed_observation_hours": round(hours, 4),
            "reported_repaired_fields_per_elapsed_hour": (
                round(sum(repaired) / hours, 2)
                if hours > 0 and all(item is not None for item in repaired) else None
            ),
            "rate_caveat": "excludes oldest observation; saved field counts are not unique provider "
                           "repairs, and observation intervals are not provider execution time",
        }
    return summaries


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
    stages = [
            {"cycle_slot": row[0], "stage_key": row[1], "status": row[2],
             "attempt_count": row[3], "updated_at": row[4],
             "exact_price": exact_price_fields(json.loads(row[5]))}
            for row in rows
        ]
    return {
        "read_only": True, "provider_calls": 0, "limit": limit,
        "stages": stages, "throughput": summarize(stages),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(diagnose(args.database, args.limit), ensure_ascii=False, indent=2))
