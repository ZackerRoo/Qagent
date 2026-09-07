"""Offline matched-capital replay of already saved walk-forward selections."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from qagent.backtesting.execution import VersionedAshareExecutionResolver
from qagent.backtesting.portfolio import DEFAULT_EXECUTION_PROFILE, run_signal_portfolio_backtest
from qagent.backtesting.replay_provider import ReplayMarketDataProvider
from qagent.backtesting.walk_forward import WalkForwardSnapshot, _signals
from qagent.storage.replay_evidence import ReplayEvidenceRepository


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


def run_matched_control(source: dict, repository: ReplayEvidenceRepository) -> dict:
    """No scans, writes, leases, refreshes or paper-trading service calls."""
    payload = source["payload"]
    revision = int(payload["dataset_revision"])
    if revision <= 0 or repository.current_revision() != revision:
        raise ValueError("isolated database current revision must equal frozen source revision")
    for field in ("dataset_revision", "start_date", "end_date"):
        if field in source and str(source[field]) != str(payload[field]):
            raise ValueError(f"source and payload disagree on {field}")
    snapshots = [WalkForwardSnapshot.model_validate(item) for item in payload["snapshots"]]
    if not snapshots:
        raise ValueError("saved snapshots are required")
    start, end = date.fromisoformat(payload["start_date"]), date.fromisoformat(payload["end_date"])
    if any(not start <= item.decision_date <= end for item in snapshots):
        raise ValueError("snapshot outside replay window")
    if len({item.decision_date for item in snapshots}) != len(snapshots):
        raise ValueError("duplicate snapshot decision dates")
    # Fail closed if this is not the nested Top 5/Top 10 experiment requested.
    for snapshot in snapshots:
        if snapshot.top_5 != snapshot.top_10[:5]:
            raise ValueError("saved Top 5 is not the exact Top 10 prefix")
    signals_by_size = {size: _signals(snapshots, size=size) for size in (5, 10)}
    symbols = sorted({signal.instrument_id for signals in signals_by_size.values() for signal in signals})
    config = dict(start=start, end=end, initial_capital=Decimal("100000"),
                  risk_per_trade_pct=Decimal("1"), max_positions=10,
                  transaction_cost_bps=Decimal("5"), slippage_bps=Decimal("5"),
                  fee_multiplier=Decimal("1"), max_entry_wait_days=5, max_holding_days=20)
    config_manifest = {**config, "instrument_ids": symbols, "dataset_revision": revision,
                       "execution_profile": DEFAULT_EXECUTION_PROFILE.key,
                       "provider": repository.provider_mode,
                       "execution_rules": "VersionedAshareExecutionResolver"}
    arms = {}
    for size, signals in signals_by_size.items():
        audit: list[dict[str, object]] = []
        provider = ReplayMarketDataProvider(repository, revision)
        result = run_signal_portfolio_backtest(
            signals=signals, instrument_ids=symbols, provider=provider,
            execution_rule_resolver=VersionedAshareExecutionResolver(repository, dataset_revision=revision),
            execution_profile=DEFAULT_EXECUTION_PROFILE, audit_sink=audit, **config,
        )
        utilization = [float(point.market_value / point.equity * 100)
                       for point in result.equity_curve if point.equity > 0]
        arms[f"top_{size}"] = {
            "config_digest": digest(config_manifest),
            "signal_count": len(signals),
            "signals_digest": digest([signal.model_dump(mode="json") for signal in signals]),
            "signals": [signal.model_dump(mode="json") for signal in signals],
            "summary": result.summary.model_dump(mode="json"),
            "average_capital_utilization_pct": sum(utilization) / len(utilization) if utilization else None,
            "capital_utilization_basis": "equal_weight_end_of_day_market_value_divided_by_equity",
            "closed_trade_costs": str(sum((trade.costs for trade in result.trades), Decimal(0))),
            "executed_entry_costs": str(sum((Decimal(str(row["entry_costs"])) for row in audit
                                            if row["reason"] == "executed"), Decimal(0))),
            "audit_reason_counts": dict(Counter(str(row["reason"]) for row in audit)),
            "audit": audit,
            "provider_errors": list(provider.last_errors),
            "portfolio": result.model_dump(mode="json"),
        }
    return {
        "schema": "matched-top5-top10-offline-v1",
        "source_run_id": source.get("run_id"),
        "source_reproducibility_digest": payload.get("reproducibility_digest"),
        "source_snapshots_digest": digest(payload["snapshots"]),
        "config": json.loads(json.dumps(config_manifest, default=str)),
        "configs_identical": arms["top_5"]["config_digest"] == arms["top_10"]["config_digest"],
        "implementation_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("matched_control.py", "portfolio.py", "replay_provider.py", "execution.py", "walk_forward.py")
        },
        "arms": arms,
        "top10_minus_top5_return_percentage_points": (
            arms["top_10"]["summary"]["total_return_pct"] - arms["top_5"]["summary"]["total_return_pct"]),
        "limitations": [
            "Only saved signal membership differs; both arms use ten slots and equity/10 sizing budget.",
            "Identical sizing rules do not imply identical trade amounts: equity and cash paths diverge between arms.",
            "This is a retrospective isolated replay, not prospective validation or a production allocation recommendation.",
            "Non-candidates are unknown: missing data, invalid plan, absent trigger and unfillable orders are not distinguished.",
            "size_zero does not establish cash causality; cash_insufficient records only the explicit outlay check.",
            "Audit reasons record the first failing simulation check, not every potentially binding constraint.",
            "executed means entry accepted; exit-liquidity-censored positions may remain open and not enter closed trade costs.",
            "Utilization uses replay equity-curve observations including its initial point; closed_trade_costs exclude open-position exits.",
            "Historical rule tables use the isolated database contents; revision equality alone is not a cryptographic dataset fingerprint.",
        ],
    }


def run_files(source_path: Path, isolated_db: Path, output: Path) -> dict:
    source_path, isolated_db, output = (path.resolve() for path in (source_path, isolated_db, output))
    if isolated_db.name == "qagent.db" or "data" in isolated_db.parts:
        raise ValueError("refusing default/production-looking database path; use an explicit isolated copy")
    if not isolated_db.is_file() or output in {source_path, isolated_db}:
        raise ValueError("existing isolated database and separate output required")
    if output.exists():
        raise ValueError("output must not exist")
    raw = source_path.read_bytes()
    source = json.loads(raw)
    def connect():
        connection = sqlite3.connect(isolated_db.as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection
    engine = create_engine("sqlite://", creator=connect)
    try:
        repository = ReplayEvidenceRepository(sessionmaker(bind=engine), source["payload"]["provider_mode"])
        report = run_matched_control(source, repository)
    finally:
        engine.dispose()
    if source_path.read_bytes() != raw:
        raise RuntimeError("source changed during replay")
    report["source_file_sha256"] = hashlib.sha256(raw).hexdigest()
    report["isolated_database"] = str(isolated_db)
    report["database_access"] = "sqlite mode=ro; PRAGMA query_only=ON; no migrations or leases"
    with output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report
