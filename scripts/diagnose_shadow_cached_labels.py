#!/usr/bin/env python3
"""Read one candidate's mature, missing 5-day labels without repairing prices.

Run with the deployed backend on PYTHONPATH; compatible with ``python -``.
Only SELECTs are issued inside one mode=ro / query_only snapshot.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

import pandas as pd

from qagent.research.factor_shadow_outcomes import factor_shadow_outcome_dates
from qagent.research.shadow_price_repair import _unsafe_exact_row


PRICE_FIELDS = (
    "source_provider", "adjusted_source_provider", "adjustment_type", "close",
    "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close",
    "adjustment_factor",
)


def classify_price(row: dict | None, field: str) -> str:
    if row is None:
        return "missing"
    try:
        value = float(row[field])
    except (TypeError, ValueError, KeyError):
        return "nonpositive"
    if not math.isfinite(value) or value <= 0:
        return "nonpositive"
    mapped = {**row, "provider": row.get("source_provider")}
    return "unsafe" if _unsafe_exact_row(pd.Series(mapped), field) else "ready_cached"


def _combined(states: list[str]) -> str:
    for state in ("missing", "nonpositive", "unsafe"):
        if state in states:
            return state
    return "ready_cached"


def diagnose(connection: sqlite3.Connection, *, experiment_id: str, as_of: date) -> dict:
    connection.row_factory = sqlite3.Row
    experiment = connection.execute(
        "SELECT provider_mode, benchmark_id FROM factor_research_experiments "
        "WHERE experiment_id=?", (experiment_id,),
    ).fetchone()
    if experiment is None:
        raise ValueError("experiment not found")
    mode, benchmark = experiment["provider_mode"], experiment["benchmark_id"]
    runs = connection.execute(
        "WITH runs AS (SELECT scan_job_id, signal_date, MIN(created_at) t, COUNT(*) n "
        "FROM factor_shadow_scores WHERE experiment_id=? GROUP BY 1,2), "
        "ranked AS (SELECT *, ROW_NUMBER() OVER(PARTITION BY signal_date "
        "ORDER BY t,scan_job_id) rn FROM runs) SELECT * FROM ranked WHERE rn=1 "
        "ORDER BY signal_date", (experiment_id,),
    ).fetchall()
    total: Counter = Counter()
    batches = []
    for run in runs:
        entry, exit_ = factor_shadow_outcome_dates(date.fromisoformat(run["signal_date"]), 5)
        if exit_ > as_of:
            continue
        pending = [row[0] for row in connection.execute(
            "SELECT s.instrument_id FROM factor_shadow_scores s "
            "LEFT JOIN factor_shadow_outcomes o ON o.experiment_id=s.experiment_id "
            "AND o.scan_job_id=s.scan_job_id AND o.instrument_id=s.instrument_id "
            "AND o.horizon_sessions=5 WHERE s.experiment_id=? AND s.scan_job_id=? "
            "AND o.instrument_id IS NULL ORDER BY s.instrument_id",
            (experiment_id, run["scan_job_id"]),
        )]
        prices = {}
        ids = sorted(set(pending) | {benchmark})
        for offset in range(0, len(ids), 400):
            batch_ids = ids[offset:offset + 400]
            placeholders = ",".join("?" for _ in batch_ids)
            rows = connection.execute(
                "SELECT instrument_id,trade_date," + ",".join(PRICE_FIELDS)
                + " FROM market_bar_cache WHERE provider_mode=? "
                + f"AND instrument_id IN ({placeholders}) AND trade_date IN (?,?)",
                (mode, *batch_ids, entry.isoformat(), exit_.isoformat()),
            )
            for row in rows:
                prices[(row["instrument_id"], row["trade_date"])] = dict(row)
        def pair(instrument_id):
            return [prices.get((instrument_id, day.isoformat())) for day in (entry, exit_)]
        def states(rows):
            return [classify_price(row, field) for row, field in
                    zip(rows, ("adjusted_open", "adjusted_close"))]
        benchmark_rows = pair(benchmark)
        benchmark_states = states(benchmark_rows)
        counts: Counter = Counter()
        stock_counts: Counter = Counter()
        examples = defaultdict(list)
        for instrument_id in pending:
            stock_rows = pair(instrument_id)
            stock_states = states(stock_rows)
            stock_counts[_combined(stock_states)] += 1
            category = _combined(stock_states + benchmark_states)
            counts[category] += 1
            if len(examples[category]) < 3:
                examples[category].append({"instrument_id": instrument_id,
                                           "stock_states": stock_states,
                                           "entry": stock_rows[0], "exit": stock_rows[1]})
        total.update(counts)
        batches.append({"signal_date": run["signal_date"], "scan_job_id": run["scan_job_id"],
                        "entry_date": entry.isoformat(), "outcome_date": exit_.isoformat(),
                        "expected": run["n"], "uncomputed": len(pending),
                        "counts_with_benchmark": dict(counts), "stock_counts": dict(stock_counts),
                        "benchmark_states": benchmark_states, "benchmark_prices": benchmark_rows,
                        "examples": dict(examples)})
    return {"experiment_id": experiment_id, "provider_mode": mode, "as_of_date": as_of.isoformat(),
            "horizon_sessions": 5, "counts_with_benchmark": dict(total), "batches": batches}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 10))
    args = parser.parse_args()
    uri = Path(args.db).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        started = datetime.now(timezone.utc).isoformat()
        report = diagnose(connection, experiment_id=args.experiment_id, as_of=args.as_of)
        report["snapshot_started_at_utc"] = started
        report["read_only"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
