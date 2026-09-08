#!/usr/bin/env python3
"""Run fixed offline ablations from a local frozen JSON dataset."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from qagent.research.factor_ablation import PROTOCOL, run_factor_ablation


def dataset_from_database(database: Path, settings: dict) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from qagent.research.factor_experiments import FactorResearchConfig, build_factor_research_dataset

    config = FactorResearchConfig.model_validate(settings)
    if config.candidate_id is not None or not config.dataset_revision or config.dataset_revision <= 0:
        raise ValueError("database mode requires positive frozen revision and no candidate")
    uri = database.resolve().as_uri() + "?mode=ro"

    def connect():
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection

    engine = create_engine("sqlite://", creator=connect)
    try:
        frame, health = build_factor_research_dataset(sessionmaker(bind=engine), config)
    finally:
        engine.dispose()
    print(f"Frozen cohort: {len(frame)} rows, {frame.signal_date.nunique()} dates", flush=True)
    return {
        "protocol": PROTOCOL,
        "feature_stage": "neutralized",
        "provenance": f"read-only {database.resolve()} revision {config.dataset_revision}",
        "config": settings,
        "data_health": health,
        "dataset_preparation_caveat": "frozen_replay_revision_with_current_local_historical_rules_not_version_frozen",
        "rows": json.loads(frame.to_json(orient="records", date_format="iso")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--database", type=Path)
    parser.add_argument("--config", type=Path, help="explicit config JSON required in database mode")
    parser.add_argument("--prepare-only", action="store_true", help="write the frozen dataset envelope without training")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; choose a new artifact path")
    if not args.output.parent.is_dir():
        parser.error("output parent directory must already exist")
    if args.database:
        if args.config is None:
            parser.error("--database requires --config")
        payload = dataset_from_database(args.database, json.loads(args.config.read_text()))
        raw = json.dumps(payload, sort_keys=True).encode()
    else:
        if args.config is not None:
            parser.error("--config is only accepted with --database")
        raw = args.input.read_bytes()
        payload = json.loads(raw)
    if args.prepare_only:
        from qagent.research.factor_ablation import validate_input
        validate_input(payload)
        report = payload
    else:
        report = run_factor_ablation(payload)
        report["input_sha256"] = sha256(raw).hexdigest()
        report["data_health"] = payload.get("data_health")
    encoded = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(f"{'Prepared dataset' if args.prepare_only else 'Completed 5 fixed offline variants'}: {args.output}")


if __name__ == "__main__":
    main()
