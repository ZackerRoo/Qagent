from __future__ import annotations

import json
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import FactorResearchExperimentRow


TERMINAL_EXPERIMENT_STATUSES = {"succeeded", "failed"}


class FactorResearchExperiment(BaseModel):
    experiment_id: str
    experiment_name: str
    status: str
    provider_mode: str
    model_family: str
    benchmark_id: str
    dataset_revision: int
    start_date: date
    end_date: date
    code_revision: str
    config_digest: str
    config: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] | None = None
    data_health: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class FactorResearchRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create(
        self,
        *,
        experiment_name: str,
        provider_mode: str,
        model_family: str,
        benchmark_id: str,
        dataset_revision: int,
        start_date: date,
        end_date: date,
        code_revision: str,
        config: dict[str, Any],
    ) -> FactorResearchExperiment:
        config_json = _canonical_json(config)
        row = FactorResearchExperimentRow(
            experiment_id=f"factor-research-{uuid4().hex}",
            experiment_name=experiment_name,
            status="queued",
            provider_mode=provider_mode,
            model_family=model_family,
            benchmark_id=benchmark_id,
            dataset_revision=dataset_revision,
            start_date=start_date,
            end_date=end_date,
            code_revision=code_revision,
            config_digest=sha256(config_json.encode("utf-8")).hexdigest(),
            config_json=config_json,
            data_health_json="{}",
            artifacts_json="{}",
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return _from_row(row)

    def mark_running(self, experiment_id: str) -> FactorResearchExperiment:
        with self.session_factory() as session:
            row = _required_row(session, experiment_id)
            if row.status != "queued":
                raise ValueError(f"experiment {experiment_id} is not queued")
            row.status = "running"
            row.started_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return _from_row(row)

    def complete(
        self,
        experiment_id: str,
        *,
        metrics: dict[str, Any],
        data_health: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> FactorResearchExperiment:
        with self.session_factory() as session:
            row = _required_row(session, experiment_id)
            if row.status != "running":
                raise ValueError(f"experiment {experiment_id} is not running")
            row.status = "succeeded"
            row.metrics_json = _canonical_json(metrics)
            row.data_health_json = _canonical_json(data_health)
            row.artifacts_json = _canonical_json(artifacts)
            row.completed_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return _from_row(row)

    def fail(
        self,
        experiment_id: str,
        error: str,
        *,
        data_health: dict[str, Any] | None = None,
    ) -> FactorResearchExperiment:
        with self.session_factory() as session:
            row = _required_row(session, experiment_id)
            if row.status in TERMINAL_EXPERIMENT_STATUSES:
                return _from_row(row)
            row.status = "failed"
            row.error = error[:4000]
            row.data_health_json = _canonical_json(data_health or {})
            row.completed_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return _from_row(row)

    def get(self, experiment_id: str) -> FactorResearchExperiment | None:
        with self.session_factory() as session:
            row = session.get(FactorResearchExperimentRow, experiment_id)
            return _from_row(row) if row is not None else None

    def list(self, limit: int = 20) -> list[FactorResearchExperiment]:
        with self.session_factory() as session:
            rows = (
                session.query(FactorResearchExperimentRow)
                .order_by(FactorResearchExperimentRow.created_at.desc())
                .limit(max(0, limit))
                .all()
            )
            return [_from_row(row) for row in rows]

    def active(self) -> FactorResearchExperiment | None:
        with self.session_factory() as session:
            row = (
                session.query(FactorResearchExperimentRow)
                .filter(FactorResearchExperimentRow.status.in_(("queued", "running")))
                .order_by(FactorResearchExperimentRow.created_at.desc())
                .first()
            )
            return _from_row(row) if row is not None else None


def _required_row(session: Session, experiment_id: str) -> FactorResearchExperimentRow:
    row = session.get(FactorResearchExperimentRow, experiment_id)
    if row is None:
        raise LookupError(f"factor research experiment {experiment_id!r} does not exist")
    return row


def _from_row(row: FactorResearchExperimentRow) -> FactorResearchExperiment:
    return FactorResearchExperiment(
        experiment_id=row.experiment_id,
        experiment_name=row.experiment_name,
        status=row.status,
        provider_mode=row.provider_mode,
        model_family=row.model_family,
        benchmark_id=row.benchmark_id,
        dataset_revision=row.dataset_revision,
        start_date=row.start_date,
        end_date=row.end_date,
        code_revision=row.code_revision,
        config_digest=row.config_digest,
        config=json.loads(row.config_json),
        metrics=json.loads(row.metrics_json) if row.metrics_json else None,
        data_health=json.loads(row.data_health_json or "{}"),
        artifacts=json.loads(row.artifacts_json or "{}"),
        error=row.error,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
