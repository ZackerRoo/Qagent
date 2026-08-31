from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from qagent.factors.research_contract import (
    EXPLICIT_SHADOW_CANDIDATE_IDS,
    FACTOR_CANDIDATE_SHADOW_PROTOCOL,
)
from qagent.storage.tables import (
    FactorResearchExperimentRow,
    FactorResearchModelArtifactRow,
    FactorShadowOutcomeRow,
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


class FactorShadowRunRef(BaseModel):
    experiment_id: str
    scan_job_id: str
    signal_date: date
    dataset_revision: int
    model_digest: str
    scored_instruments: int
    created_at: datetime


class FactorShadowOutcome(BaseModel):
    experiment_id: str
    scan_job_id: str
    instrument_id: str
    horizon_sessions: int = Field(gt=0)
    signal_date: date
    entry_date: date
    outcome_date: date
    benchmark_id: str
    instrument_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    net_excess_return_pct: float
    round_trip_cost_bps: float = Field(ge=0)
    signal_dataset_revision: int
    model_digest: str
    source_digest: str
    created_at: datetime | None = None


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
        bundles = self.model_bundles(provider_mode, limit=1)
        return bundles[0] if bundles else None

    def model_bundle(self, experiment_id: str) -> FactorResearchModelBundle | None:
        with self.session_factory() as session:
            experiment = session.get(FactorResearchExperimentRow, experiment_id)
            if experiment is None or experiment.status != "succeeded":
                return None
            rows = (
                session.query(FactorResearchModelArtifactRow)
                .filter(FactorResearchModelArtifactRow.experiment_id == experiment.experiment_id)
                .order_by(FactorResearchModelArtifactRow.seed.asc())
                .all()
            )
            if rows:
                models = [_model_artifact_from_row(row) for row in rows]
                return FactorResearchModelBundle(
                    experiment=_from_row(experiment),
                    models=models,
                    aggregate_model_digest=_aggregate_model_digest(models),
                )
        return None

    def model_bundles(
        self,
        provider_mode: str,
        *,
        limit: int = 3,
    ) -> list[FactorResearchModelBundle]:
        """Return explicitly registered candidates plus one named legacy lane.

        A candidate keeps only its newest successful frozen lane, even when a
        retry changed revision or source identity. Historical experiments
        without candidate identity are grandfathered only as the single newest
        legacy lane and are never interpreted as one of the named candidates.
        """

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
            explicit_bundles: list[FactorResearchModelBundle] = []
            legacy_bundle: FactorResearchModelBundle | None = None
            seen_candidates: set[str] = set()
            for experiment in experiments:
                config = json.loads(experiment.config_json or "{}")
                lane_kind = _shadow_lane_kind(config)
                if lane_kind is None or (lane_kind == "legacy" and legacy_bundle is not None):
                    continue
                candidate_id = str(config.get("candidate_id") or "")
                if lane_kind == "explicit" and candidate_id in seen_candidates:
                    continue
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
                bundle = FactorResearchModelBundle(
                    experiment=_from_row(experiment),
                    models=models,
                    aggregate_model_digest=_aggregate_model_digest(models),
                )
                if lane_kind == "legacy":
                    legacy_bundle = bundle
                else:
                    seen_candidates.add(candidate_id)
                    explicit_bundles.append(bundle)
        bounded_limit = max(1, limit)
        bundles = explicit_bundles[:bounded_limit]
        if legacy_bundle is not None and len(bundles) < bounded_limit:
            bundles.append(legacy_bundle)
        return bundles

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

    def latest_model_shadow_runs(self, provider_mode: str) -> list[FactorShadowRunRef]:
        bundle = self.latest_model_bundle(provider_mode)
        if bundle is None:
            return []
        return self.shadow_runs(bundle.experiment.experiment_id)

    def shadow_runs(self, experiment_id: str) -> list[FactorShadowRunRef]:
        with self.session_factory() as session:
            rows = (
                session.query(
                    FactorShadowScoreRow.experiment_id,
                    FactorShadowScoreRow.scan_job_id,
                    FactorShadowScoreRow.signal_date,
                    FactorShadowScoreRow.dataset_revision,
                    FactorShadowScoreRow.model_digest,
                    func.count(FactorShadowScoreRow.instrument_id),
                    func.max(FactorShadowScoreRow.created_at),
                )
                .filter(
                    FactorShadowScoreRow.experiment_id == experiment_id
                )
                .group_by(
                    FactorShadowScoreRow.experiment_id,
                    FactorShadowScoreRow.scan_job_id,
                    FactorShadowScoreRow.signal_date,
                    FactorShadowScoreRow.dataset_revision,
                    FactorShadowScoreRow.model_digest,
                )
                .order_by(FactorShadowScoreRow.signal_date.asc())
                .all()
            )
        return [
            FactorShadowRunRef(
                experiment_id=row[0],
                scan_job_id=row[1],
                signal_date=row[2],
                dataset_revision=row[3],
                model_digest=row[4],
                scored_instruments=int(row[5]),
                created_at=row[6],
            )
            for row in rows
        ]

    def shadow_scores(
        self,
        experiment_id: str,
        scan_job_id: str,
    ) -> list[FactorShadowScore]:
        with self.session_factory() as session:
            rows = (
                session.query(FactorShadowScoreRow)
                .filter(
                    FactorShadowScoreRow.experiment_id == experiment_id,
                    FactorShadowScoreRow.scan_job_id == scan_job_id,
                )
                .order_by(FactorShadowScoreRow.challenger_rank.asc())
                .all()
            )
        return [_shadow_score_from_row(row) for row in rows]

    def record_shadow_outcomes(
        self,
        outcomes: list[FactorShadowOutcome],
    ) -> int:
        if not outcomes:
            return 0
        inserted = 0
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            for item in outcomes:
                values = {
                    "experiment_id": item.experiment_id,
                    "scan_job_id": item.scan_job_id,
                    "instrument_id": item.instrument_id,
                    "horizon_sessions": item.horizon_sessions,
                    "signal_date": item.signal_date,
                    "entry_date": item.entry_date,
                    "outcome_date": item.outcome_date,
                    "benchmark_id": item.benchmark_id,
                    "instrument_return_pct": Decimal(str(item.instrument_return_pct)),
                    "benchmark_return_pct": Decimal(str(item.benchmark_return_pct)),
                    "excess_return_pct": Decimal(str(item.excess_return_pct)),
                    "net_excess_return_pct": Decimal(str(item.net_excess_return_pct)),
                    "round_trip_cost_bps": Decimal(str(item.round_trip_cost_bps)),
                    "signal_dataset_revision": item.signal_dataset_revision,
                    "model_digest": item.model_digest,
                    "source_digest": item.source_digest,
                    "created_at": item.created_at or now,
                }
                statement = sqlite_insert(FactorShadowOutcomeRow).values(**values)
                statement = statement.on_conflict_do_nothing(
                    index_elements=[
                        FactorShadowOutcomeRow.experiment_id,
                        FactorShadowOutcomeRow.scan_job_id,
                        FactorShadowOutcomeRow.instrument_id,
                        FactorShadowOutcomeRow.horizon_sessions,
                    ]
                )
                result = session.execute(statement)
                if int(result.rowcount or 0) > 0:
                    inserted += 1
                    continue
                existing = session.get(
                    FactorShadowOutcomeRow,
                    (
                        item.experiment_id,
                        item.scan_job_id,
                        item.instrument_id,
                        item.horizon_sessions,
                    ),
                )
                if existing is None:
                    raise RuntimeError("factor shadow outcome conflict was not readable")
                if _outcome_identity_payload(_outcome_from_row(existing)) != (
                    _outcome_identity_payload(item)
                ):
                    raise ValueError("factor shadow outcome retry does not match immutable row")
            session.commit()
        return inserted

    def shadow_outcomes(
        self,
        experiment_id: str,
        *,
        scan_job_id: str | None = None,
        horizon_sessions: int | None = None,
    ) -> list[FactorShadowOutcome]:
        with self.session_factory() as session:
            query = session.query(FactorShadowOutcomeRow).filter(
                FactorShadowOutcomeRow.experiment_id == experiment_id
            )
            if scan_job_id is not None:
                query = query.filter(
                    FactorShadowOutcomeRow.scan_job_id == scan_job_id
                )
            if horizon_sessions is not None:
                query = query.filter(
                    FactorShadowOutcomeRow.horizon_sessions == horizon_sessions
                )
            rows = query.order_by(
                FactorShadowOutcomeRow.signal_date,
                FactorShadowOutcomeRow.horizon_sessions,
                FactorShadowOutcomeRow.instrument_id,
            ).all()
        return [_outcome_from_row(row) for row in rows]


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


def _outcome_from_row(row: FactorShadowOutcomeRow) -> FactorShadowOutcome:
    return FactorShadowOutcome(
        experiment_id=row.experiment_id,
        scan_job_id=row.scan_job_id,
        instrument_id=row.instrument_id,
        horizon_sessions=row.horizon_sessions,
        signal_date=row.signal_date,
        entry_date=row.entry_date,
        outcome_date=row.outcome_date,
        benchmark_id=row.benchmark_id,
        instrument_return_pct=float(row.instrument_return_pct),
        benchmark_return_pct=float(row.benchmark_return_pct),
        excess_return_pct=float(row.excess_return_pct),
        net_excess_return_pct=float(row.net_excess_return_pct),
        round_trip_cost_bps=float(row.round_trip_cost_bps),
        signal_dataset_revision=row.signal_dataset_revision,
        model_digest=row.model_digest,
        source_digest=row.source_digest,
        created_at=row.created_at,
    )


def _outcome_identity_payload(item: FactorShadowOutcome) -> dict[str, Any]:
    return item.model_dump(mode="json", exclude={"created_at"})


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _shadow_lane_kind(config: dict[str, Any]) -> str | None:
    candidate_id = config.get("candidate_id")
    if candidate_id is None:
        registration = config.get("shadow_registration")
        return "legacy" if registration in {None, "legacy_grandfather"} else None
    if (
        candidate_id in EXPLICIT_SHADOW_CANDIDATE_IDS
        and config.get("candidate_protocol_version") == FACTOR_CANDIDATE_SHADOW_PROTOCOL
        and config.get("shadow_registration") == "explicit_manual"
        and config.get("scope") == "research_shadow"
        and config.get("decision_weight") is False
        and config.get("activation_allowed") is False
    ):
        return "explicit"
    return None
