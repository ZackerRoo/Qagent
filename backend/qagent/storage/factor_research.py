from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.storage.tables import (
    FactorResearchExperimentRow,
    FactorResearchModelArtifactRow,
    FactorShadowScoreRow,
)


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


class FactorResearchModelArtifact(BaseModel):
    experiment_id: str
    seed: int
    feature_set_version: str
    feature_contract_digest: str
    model_digest: str
    model_text: str


class FactorResearchModelBundle(BaseModel):
    experiment: FactorResearchExperiment
    models: list[FactorResearchModelArtifact]
    aggregate_model_digest: str


class FactorShadowScore(BaseModel):
    instrument_id: str
    baseline_score: float
    challenger_score: float
    baseline_rank: int
    challenger_rank: int
    feature_coverage: float = Field(ge=0.0, le=1.0)
    industry: str | None = None


class FactorShadowRun(BaseModel):
    experiment_id: str
    scan_job_id: str
    signal_date: date
    dataset_revision: int
    model_digest: str
    scored_instruments: int
    mean_feature_coverage: float
    top_scores: list[FactorShadowScore] = Field(default_factory=list)
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
        model_artifacts: list[dict[str, Any]] | None = None,
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
            for artifact in model_artifacts or []:
                model_text = str(artifact["model_text"])
                model_digest = sha256(model_text.encode("utf-8")).hexdigest()
                declared_digest = str(artifact.get("model_digest") or model_digest)
                if declared_digest != model_digest:
                    raise ValueError("factor research model digest mismatch")
                session.add(
                    FactorResearchModelArtifactRow(
                        experiment_id=experiment_id,
                        seed=int(artifact["seed"]),
                        feature_set_version=str(artifact["feature_set_version"]),
                        feature_contract_digest=str(artifact["feature_contract_digest"]),
                        model_digest=model_digest,
                        model_text=model_text,
                    )
                )
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

    def latest_model_bundle(self, provider_mode: str) -> FactorResearchModelBundle | None:
        with self.session_factory() as session:
            experiments = (
                session.query(FactorResearchExperimentRow)
                .filter(
                    FactorResearchExperimentRow.provider_mode == provider_mode.strip().lower(),
                    FactorResearchExperimentRow.status == "succeeded",
                )
                .order_by(FactorResearchExperimentRow.completed_at.desc())
                .all()
            )
            for experiment in experiments:
                rows = (
                    session.query(FactorResearchModelArtifactRow)
                    .filter(
                        FactorResearchModelArtifactRow.experiment_id == experiment.experiment_id
                    )
                    .order_by(FactorResearchModelArtifactRow.seed.asc())
                    .all()
                )
                if not rows:
                    continue
                models = [_model_artifact_from_row(row) for row in rows]
                return FactorResearchModelBundle(
                    experiment=_from_row(experiment),
                    models=models,
                    aggregate_model_digest=_aggregate_model_digest(models),
                )
        return None

    def record_shadow_scores(
        self,
        *,
        experiment_id: str,
        scan_job_id: str,
        signal_date: date,
        dataset_revision: int,
        model_digest: str,
        scores: list[FactorShadowScore],
    ) -> FactorShadowRun:
        if not scores:
            raise ValueError("factor shadow scoring requires at least one instrument")
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            existing = (
                session.query(FactorShadowScoreRow)
                .filter(
                    FactorShadowScoreRow.experiment_id == experiment_id,
                    FactorShadowScoreRow.scan_job_id == scan_job_id,
                )
                .order_by(FactorShadowScoreRow.challenger_rank.asc())
                .all()
            )
            if existing:
                first = existing[0]
                if (
                    first.signal_date != signal_date
                    or first.dataset_revision != dataset_revision
                    or first.model_digest != model_digest
                ):
                    raise ValueError("factor shadow score retry identity does not match immutable rows")
                stored = [_shadow_score_from_row(row) for row in existing]
                if [item.model_dump() for item in stored] != [item.model_dump() for item in scores]:
                    raise ValueError("factor shadow score retry does not match immutable rows")
                return _shadow_run_from_rows(existing)
            for item in scores:
                session.add(
                    FactorShadowScoreRow(
                        experiment_id=experiment_id,
                        scan_job_id=scan_job_id,
                        instrument_id=item.instrument_id,
                        signal_date=signal_date,
                        baseline_score=Decimal(str(item.baseline_score)),
                        challenger_score=Decimal(str(item.challenger_score)),
                        baseline_rank=item.baseline_rank,
                        challenger_rank=item.challenger_rank,
                        feature_coverage=Decimal(str(item.feature_coverage)),
                        industry=item.industry,
                        dataset_revision=dataset_revision,
                        model_digest=model_digest,
                        created_at=now,
                    )
                )
            session.commit()
            rows = (
                session.query(FactorShadowScoreRow)
                .filter(
                    FactorShadowScoreRow.experiment_id == experiment_id,
                    FactorShadowScoreRow.scan_job_id == scan_job_id,
                )
                .order_by(FactorShadowScoreRow.challenger_rank.asc())
                .all()
            )
            return _shadow_run_from_rows(rows)

    def latest_shadow_run(
        self,
        provider_mode: str,
        *,
        top_limit: int = 20,
    ) -> FactorShadowRun | None:
        with self.session_factory() as session:
            latest = (
                session.query(FactorShadowScoreRow)
                .join(
                    FactorResearchExperimentRow,
                    FactorResearchExperimentRow.experiment_id == FactorShadowScoreRow.experiment_id,
                )
                .filter(FactorResearchExperimentRow.provider_mode == provider_mode.strip().lower())
                .order_by(FactorShadowScoreRow.created_at.desc())
                .first()
            )
            if latest is None:
                return None
            rows = (
                session.query(FactorShadowScoreRow)
                .filter(
                    FactorShadowScoreRow.experiment_id == latest.experiment_id,
                    FactorShadowScoreRow.scan_job_id == latest.scan_job_id,
                )
                .order_by(FactorShadowScoreRow.challenger_rank.asc())
                .all()
            )
            return _shadow_run_from_rows(rows, top_limit=top_limit)


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


def _model_artifact_from_row(
    row: FactorResearchModelArtifactRow,
) -> FactorResearchModelArtifact:
    return FactorResearchModelArtifact(
        experiment_id=row.experiment_id,
        seed=row.seed,
        feature_set_version=row.feature_set_version,
        feature_contract_digest=row.feature_contract_digest,
        model_digest=row.model_digest,
        model_text=row.model_text,
    )


def _aggregate_model_digest(models: list[FactorResearchModelArtifact]) -> str:
    payload = "|".join(
        f"{item.seed}:{item.model_digest}:{item.feature_contract_digest}" for item in models
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _shadow_score_from_row(row: FactorShadowScoreRow) -> FactorShadowScore:
    return FactorShadowScore(
        instrument_id=row.instrument_id,
        baseline_score=float(row.baseline_score),
        challenger_score=float(row.challenger_score),
        baseline_rank=row.baseline_rank,
        challenger_rank=row.challenger_rank,
        feature_coverage=float(row.feature_coverage),
        industry=row.industry,
    )


def _shadow_run_from_rows(
    rows: list[FactorShadowScoreRow],
    *,
    top_limit: int = 20,
) -> FactorShadowRun:
    if not rows:
        raise ValueError("factor shadow run rows are empty")
    first = rows[0]
    coverage = sum(float(row.feature_coverage) for row in rows) / len(rows)
    return FactorShadowRun(
        experiment_id=first.experiment_id,
        scan_job_id=first.scan_job_id,
        signal_date=first.signal_date,
        dataset_revision=first.dataset_revision,
        model_digest=first.model_digest,
        scored_instruments=len(rows),
        mean_feature_coverage=round(coverage, 6),
        top_scores=[_shadow_score_from_row(row) for row in rows[: max(0, top_limit)]],
        created_at=max(row.created_at for row in rows),
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
