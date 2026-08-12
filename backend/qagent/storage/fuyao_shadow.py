from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import FuyaoShadowOutcomeRow


class FuyaoShadowOutcome(BaseModel):
    outcome_id: str | None = None
    snapshot_id: str
    instrument_id: str
    signal_date: date
    horizon_sessions: int
    entry_date: date
    outcome_date: date
    signal_score: float
    entry_adjusted_open: float
    exit_adjusted_close: float
    benchmark_id: str
    benchmark_entry_adjusted_open: float
    benchmark_exit_adjusted_close: float
    instrument_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    net_excess_return_pct: float
    round_trip_cost_bps: float
    source_digest: str
    classification: str = "research_only"
    decision_weight_applied: bool = False


class FuyaoShadowRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def append_outcomes(self, outcomes: Iterable[FuyaoShadowOutcome]) -> int:
        rows = [_row_values(outcome) for outcome in outcomes]
        if not rows:
            return 0
        inserted = 0
        with self.session_factory() as session:
            for row in rows:
                statement = sqlite_insert(FuyaoShadowOutcomeRow).values(**row)
                statement = statement.on_conflict_do_nothing(
                    index_elements=[
                        FuyaoShadowOutcomeRow.snapshot_id,
                        FuyaoShadowOutcomeRow.instrument_id,
                        FuyaoShadowOutcomeRow.horizon_sessions,
                    ]
                )
                result = session.execute(statement)
                inserted += max(int(result.rowcount or 0), 0)
            session.commit()
        return inserted

    def list_outcomes(
        self,
        *,
        limit: int | None = None,
    ) -> list[FuyaoShadowOutcome]:
        if limit is not None and (limit <= 0 or limit > 100_000):
            raise ValueError("limit must be between 1 and 100000")
        with self.session_factory() as session:
            statement = (
                select(FuyaoShadowOutcomeRow)
                .order_by(
                    FuyaoShadowOutcomeRow.signal_date,
                    FuyaoShadowOutcomeRow.horizon_sessions,
                    FuyaoShadowOutcomeRow.instrument_id,
                )
            )
            if limit is not None:
                statement = statement.limit(limit)
            rows = session.scalars(statement).all()
            return [_from_row(row) for row in rows]


def _row_values(outcome: FuyaoShadowOutcome) -> dict[str, object]:
    payload = outcome.model_dump(mode="json", exclude={"outcome_id"})
    outcome_id = outcome.outcome_id or f"fuyao-shadow-{_digest(payload)}"
    return {
        "outcome_id": outcome_id,
        "snapshot_id": outcome.snapshot_id,
        "instrument_id": outcome.instrument_id,
        "signal_date": outcome.signal_date,
        "horizon_sessions": outcome.horizon_sessions,
        "entry_date": outcome.entry_date,
        "outcome_date": outcome.outcome_date,
        "signal_score": _decimal(outcome.signal_score),
        "entry_adjusted_open": _decimal(outcome.entry_adjusted_open),
        "exit_adjusted_close": _decimal(outcome.exit_adjusted_close),
        "benchmark_id": outcome.benchmark_id,
        "benchmark_entry_adjusted_open": _decimal(
            outcome.benchmark_entry_adjusted_open
        ),
        "benchmark_exit_adjusted_close": _decimal(
            outcome.benchmark_exit_adjusted_close
        ),
        "instrument_return_pct": _decimal(outcome.instrument_return_pct),
        "benchmark_return_pct": _decimal(outcome.benchmark_return_pct),
        "excess_return_pct": _decimal(outcome.excess_return_pct),
        "net_excess_return_pct": _decimal(outcome.net_excess_return_pct),
        "round_trip_cost_bps": _decimal(outcome.round_trip_cost_bps),
        "source_digest": outcome.source_digest,
        "classification": "research_only",
        "decision_weight_applied": False,
    }


def _from_row(row: FuyaoShadowOutcomeRow) -> FuyaoShadowOutcome:
    return FuyaoShadowOutcome(
        outcome_id=row.outcome_id,
        snapshot_id=row.snapshot_id,
        instrument_id=row.instrument_id,
        signal_date=row.signal_date,
        horizon_sessions=row.horizon_sessions,
        entry_date=row.entry_date,
        outcome_date=row.outcome_date,
        signal_score=float(row.signal_score),
        entry_adjusted_open=float(row.entry_adjusted_open),
        exit_adjusted_close=float(row.exit_adjusted_close),
        benchmark_id=row.benchmark_id,
        benchmark_entry_adjusted_open=float(row.benchmark_entry_adjusted_open),
        benchmark_exit_adjusted_close=float(row.benchmark_exit_adjusted_close),
        instrument_return_pct=float(row.instrument_return_pct),
        benchmark_return_pct=float(row.benchmark_return_pct),
        excess_return_pct=float(row.excess_return_pct),
        net_excess_return_pct=float(row.net_excess_return_pct),
        round_trip_cost_bps=float(row.round_trip_cost_bps),
        source_digest=row.source_digest,
        classification=row.classification,
        decision_weight_applied=row.decision_weight_applied,
    )


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(float(value), 10)))


def _digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
