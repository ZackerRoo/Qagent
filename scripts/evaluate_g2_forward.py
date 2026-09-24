#!/usr/bin/env python3
"""Evaluate frozen G2 close-to-close labels from a read-only cache, without inference."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from compare_g2_selections import VARIANTS, digest, industry_distribution, validate_signal
from rank_g2_consensus import publish
from qagent.market.calendars import trading_day_offset, trading_sessions_in_range
import qagent.market.calendars as calendar_runtime
from qagent.research import factor_experiments as comparator
import qagent.research.factor_shadow_outcomes as price_runtime
from qagent.research.factor_shadow_outcomes import _adjusted_price, _load_cached_bars
from qagent.storage.market_cache import MarketDataCacheRepository

MANIFEST_DIGEST = "b6bbb9a44dbb61fb4ec8879034ef6232e2cd32f07356a1407ffad71c69c9e77b"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "docs/research/g2-risk-feature-freeze-20260910-manifest.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(SHANGHAI)


def validate_inputs(signals: list[dict], manifest: dict, as_of: datetime) -> list[dict]:
    unsigned = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    if digest(unsigned) != MANIFEST_DIGEST or manifest.get("manifest_sha256") != MANIFEST_DIGEST:
        raise ValueError("frozen manifest mismatch")
    if digest(manifest["config"]) != manifest["config_sha256"]:
        raise ValueError("frozen config mismatch")
    if not signals:
        raise ValueError("at least one signal required")
    dates = set()
    schedule = set(trading_sessions_in_range(date(2026, 9, 11), date(2026, 12, 31))[::10])
    for signal in signals:
        validate_signal(signal)
        day = date.fromisoformat(signal["signal_date"])
        if day in dates or day not in schedule:
            raise ValueError("duplicate or unscheduled signal date")
        dates.add(day)
        for key, expected in (
            ("frozen_manifest_sha256", MANIFEST_DIGEST),
            ("config_sha256", manifest["config_sha256"]),
            ("models", {v: manifest["variants"][v]["models"] for v in VARIANTS}),
            ("scorers", {v: manifest["variants"][v]["scorer_identity"] for v in VARIANTS}),
        ):
            if signal.get(key) != expected:
                raise ValueError(f"frozen signal identity mismatch: {key}")
        started = timestamp(signal["collection_started_at_utc"])
        collected = timestamp(signal["collected_at_utc"])
        if not timestamp(manifest["frozen_at_utc"]) < started <= collected <= as_of:
            raise ValueError("invalid signal chronology")
        if started.date() != day or collected.date() != day or started.time() < time(15, 30):
            raise ValueError("noncontemporaneous signal")
    return sorted(signals, key=lambda s: s["signal_date"])


def evaluate(signals: list[dict], db: Path, manifest: dict, *, as_of: datetime | None = None) -> dict:
    current = timestamp((as_of or datetime.now(timezone.utc)).isoformat())
    signals = validate_inputs(signals, manifest, current)
    config = manifest["config"]["training_config"]
    windows = []
    for signal in signals:
        day = date.fromisoformat(signal["signal_date"])
        end = trading_day_offset(day, config["horizon_sessions"])
        mature = current.date() > end or (current.date() == end and current.time() >= time(15, 30))
        windows.append({"signal_date": str(day), "outcome_date": str(end),
                        "signal_digest": signal["result_digest"], "expected": len(signal["predictions"]),
                        "completed": 0, "status": "pending_prices" if mature else "waiting_for_maturity",
                        "labels": []})
    result = {"protocol": "g2-frozen-forward-outcomes-v1", "as_of": current.isoformat(),
              "frozen_manifest_sha256": MANIFEST_DIGEST, "windows": windows,
              "metrics": None, "net_difference_pct": None,
              "activation_allowed": False, "decision_weight": False,
              "implementation_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
              "comparator_sha256": sha256(Path(comparator.__file__).read_bytes()).hexdigest(),
              "price_helper_sha256": sha256(Path(price_runtime.__file__).read_bytes()).hexdigest(),
              "calendar_sha256": sha256(Path(calendar_runtime.__file__).read_bytes()).hexdigest(),
              "limitations": [
                  "Cached snapshot, not historical point-in-time cache replay or executable fills.",
                  "Explicit as_of is a caller-supplied cutoff, not evidence of natural evaluation time.",
                  "One cross-section has no observed turnover and therefore no estimated net metric.",
                  "Overlapping horizon compounding is diagnostic, not a tradable portfolio drawdown.",
                  "Incomplete or immature input windows suppress the aggregate comparison."]}
    frames = []
    if any(w["status"] == "pending_prices" for w in windows):
        connection = sqlite3.connect(Path(db).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        engine = create_engine("sqlite://", creator=lambda: connection)
        try:
            with engine.connect() as reader:
                reader.exec_driver_sql("BEGIN")
                cache = MarketDataCacheRepository(sessionmaker(bind=reader))
                for signal, window in zip(signals, windows):
                    if window["status"] != "pending_prices":
                        continue
                    day, end = date.fromisoformat(window["signal_date"]), date.fromisoformat(window["outcome_date"])
                    rows = sorted(signal["predictions"], key=lambda r: r["instrument_id"])
                    ids = [r["instrument_id"] for r in rows]
                    bars = _load_cached_bars(cache, config["provider_mode"], [*ids, config["benchmark_id"]], day, end)
                    needed = bars[bars["trade_date"].isin([day, end])]
                    evidence = json.loads(needed.to_json(orient="records", date_format="iso", double_precision=15))
                    window.update(price_evidence=evidence, price_evidence_digest=digest(evidence))

                    def price(key, when):
                        return _adjusted_price(bars, key, when, "adjusted_close")

                    be, bx = price(config["benchmark_id"], day), price(config["benchmark_id"], end)
                    for row in rows:
                        pe, px = price(row["instrument_id"], day), price(row["instrument_id"], end)
                        reasons = []
                        for name, value in (("signal_close", pe), ("outcome_close", px),
                                            ("benchmark_signal_close", be), ("benchmark_outcome_close", bx)):
                            if value is None:
                                reasons.append(name + "_missing_or_invalid")
                        label = {"instrument_id": row["instrument_id"], "industry": row["industry"],
                                 "signal_date": str(day), "reasons": reasons,
                                 "signal_adjusted_close": pe, "outcome_adjusted_close": px,
                                 **{v: row[v]["score"] for v in VARIANTS}}
                        if not reasons:
                            label.update(raw_forward_return_pct=(px / pe - 1) * 100,
                                         benchmark_return_pct=(bx / be - 1) * 100)
                            window["completed"] += 1
                        window["labels"].append(label)
                    window["status"] = "complete" if window["completed"] == window["expected"] else "partial"
                    if window["status"] == "complete":
                        frame = comparator._attach_excess_labels(pd.DataFrame(window["labels"]))
                        window["labels"] = json.loads(frame.to_json(orient="records", double_precision=15))
                        window["selections"] = {}
                        by_id = {r["instrument_id"]: r for r in rows}
                        for variant in VARIANTS:
                            selected = frame.nlargest(max(1, math.ceil(len(frame) * config["top_fraction"])), variant)
                            window["selections"][variant] = {
                                "instrument_ids": selected["instrument_id"].tolist(),
                                "industry_distribution": industry_distribution(
                                    [by_id[key] for key in selected["instrument_id"]])}
                        frames.append(frame)
        finally:
            engine.dispose()
            connection.close()
    if all(w["status"] == "complete" for w in windows):
        frame = pd.concat(frames, ignore_index=True)
        result["metrics"] = {v: comparator._model_metrics(frame, v, config["top_fraction"],
                                                         config["round_trip_cost_bps"]) for v in VARIANTS}
        full, candidate = [result["metrics"][v]["net_top_bucket_excess_return_pct"] for v in VARIANTS]
        if full is not None and candidate is not None:
            result["net_difference_pct"] = candidate - full
        result["status"] = "complete"
    else:
        result["status"] = "partial" if any(w["status"] == "partial" for w in windows) else "waiting_for_maturity"
    result["result_digest"] = digest(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal", type=Path, action="append", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--as-of", type=timestamp)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate([json.loads(p.read_text()) for p in args.signal], args.db,
                      json.loads(args.manifest.read_text()), as_of=args.as_of)
    publish(args.output, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": result["status"], "result_digest": result["result_digest"]}))


if __name__ == "__main__":
    main()
