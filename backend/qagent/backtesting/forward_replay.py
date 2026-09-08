"""Explicit offline execution comparison of prospectively saved plans.

python -m qagent.backtesting.forward_replay facts.json isolated.sqlite report.json
No scanner, scheduler, database migration, lease or paper account is invoked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from qagent.backtesting.execution import VersionedAshareExecutionResolver
from qagent.backtesting.matched_control import digest
from qagent.backtesting.portfolio import DEFAULT_EXECUTION_PROFILE, run_signal_portfolio_backtest
from qagent.backtesting.replay_provider import ReplayMarketDataProvider
from qagent.recommendations.forward_alignment import (
    PROTOCOL, SOURCE, _valid_saved_fact, frozen_selection_signals,
)
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.recommendations.alignment_identity import IDENTITY_KEY, identity_is_valid


def run_forward_replay(source, repository):
    start, end = date.fromisoformat(source["start_date"]), date.fromisoformat(source["end_date"])
    if start > end or (end - start).days > 366 or end > date.today():
        raise ValueError("a past bounded replay window of at most 366 days is required")
    revision = int(source["dataset_revision"])
    if revision <= 0 or repository.current_revision() != revision:
        raise ValueError("isolated dataset revision differs from requested frozen revision")
    if source["provider_mode"] != repository.provider_mode:
        raise ValueError("provider mismatch")
    cohorts = source["cohorts"]
    if not cohorts:
        raise ValueError("saved prospective cohorts are required")
    days = set()
    cohort_identity = None
    signals = {5: [], 10: []}
    for row in cohorts:
        fact = row["fact"]
        if (not _valid_saved_fact(fact) or fact.get("protocol") != PROTOCOL
                or fact.get("source") != SOURCE or fact["blockers"]
                or not fact["source_complete"] or fact["benchmark_gate_status"] != "captured"):
            raise ValueError("complete frozen prospective fact and captured benchmark gate required")
        if type(fact.get("benchmark_entry_allowed")) is not bool or (
                fact["benchmark_entry_allowed"] is False and fact["top10"]):
            raise ValueError("frozen selection contradicts benchmark gate")
        day = date.fromisoformat(fact["decision_date"])
        recorded = datetime.fromisoformat(fact["recorded_at"])
        if recorded.tzinfo is None or recorded.astimezone(ZoneInfo("Asia/Shanghai")).date() != day:
            raise ValueError("cohort must have been recorded on its signal date")
        identity = fact.get(IDENTITY_KEY)
        identity = json.loads(identity) if isinstance(identity, str) else identity
        if not identity_is_valid(identity) or identity["source"] != SOURCE:
            raise ValueError("valid frozen source identity required")
        # Frozen adaptive-policy inputs may evolve; policy implementation/config
        # must stay fixed. Unknown model artifacts remain explicitly unknown.
        stable_identity = digest({key: value for key, value in identity.items()
                                  if key not in {"manifest_digest", "decision_inputs"}})
        if cohort_identity is not None and cohort_identity != stable_identity:
            raise ValueError("mixed cohort model/config identities require separate reports")
        cohort_identity = stable_identity
        if not start <= day <= end or day in days:
            raise ValueError("cohort outside window or duplicate decision date")
        days.add(day)
        if len({item["instrument_id"] for item in fact["top10"]}) != len(fact["top10"]):
            raise ValueError("duplicate selected instrument")
        for size in signals:
            signals[size].extend(frozen_selection_signals(fact, run_id=row["run_id"], size=size))
    symbols = sorted({signal.instrument_id for signal in signals[10]})
    config = dict(start=start, end=end, initial_capital=Decimal("100000"),
                  risk_per_trade_pct=Decimal("1"), max_positions=10,
                  transaction_cost_bps=Decimal("5"), slippage_bps=Decimal("5"),
                  fee_multiplier=Decimal("1"), max_entry_wait_days=5, max_holding_days=20)
    config_identity = {**config, "execution_profile": DEFAULT_EXECUTION_PROFILE.key,
                       "dataset_revision": revision, "provider": repository.provider_mode,
                       "instrument_ids": symbols, "execution_rules": "VersionedAshareExecutionResolver"}
    arms = {}
    for size in signals:
        audit = []
        provider = ReplayMarketDataProvider(repository, revision)
        result = run_signal_portfolio_backtest(
            signals=signals[size], instrument_ids=symbols, provider=provider,
            execution_rule_resolver=VersionedAshareExecutionResolver(repository, dataset_revision=revision),
            execution_profile=DEFAULT_EXECUTION_PROFILE, audit_sink=audit, **config)
        arms[f"top_{size}"] = {"config_digest": digest(config_identity),
                              "signals_digest": digest([s.model_dump(mode="json") for s in signals[size]]),
                              "signal_count": len(signals[size]), "audit": audit,
                              "provider_errors": list(provider.last_errors),
                              "portfolio": result.model_dump(mode="json")}
    return {"schema": "frozen-prospective-top5-top10-offline-v1",
            "source_digest": digest(source), "config": json.loads(json.dumps(config_identity, default=str)),
            "stable_model_config_digest": cohort_identity,
            "execution_implementation_sha256": {
                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ("forward_replay.py", "portfolio.py", "execution.py", "replay_provider.py")},
            "cohort_identity_manifests": [{"run_id": row["run_id"], "identity": row["fact"][IDENTITY_KEY]}
                                           for row in cohorts],
            "configs_identical": True, "arms": arms,
            "limitations": ["Offline execution of saved prospective selections; no historical model equivalence is asserted.",
                            "Both arms use ten slots and equity/10 sizing; cash and equity paths can differ.",
                            "Revision equality is not a cryptographic data fingerprint; missing execution evidence stays unknown."]}


def run_files(source_path: Path, isolated_db: Path, output: Path):
    source_path, isolated_db, output = (path.resolve() for path in (source_path, isolated_db, output))
    if isolated_db.name == "qagent.db" or "data" in isolated_db.parts:
        raise ValueError("use an explicit isolated database copy outside data directories")
    if not isolated_db.is_file() or output.exists() or output in {source_path, isolated_db}:
        raise ValueError("existing isolated database and new separate output required")
    raw = source_path.read_bytes()
    source = json.loads(raw)
    def connect():
        connection = sqlite3.connect(isolated_db.as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection
    engine = create_engine("sqlite://", creator=connect)
    try:
        repository = ReplayEvidenceRepository(sessionmaker(bind=engine), source["provider_mode"])
        report = run_forward_replay(source, repository)
    finally:
        engine.dispose()
    if source_path.read_bytes() != raw:
        raise RuntimeError("source changed during replay")
    report["database_access"] = "sqlite mode=ro; PRAGMA query_only=ON"
    with output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("isolated_db", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run_files(args.source, args.isolated_db, args.output)
