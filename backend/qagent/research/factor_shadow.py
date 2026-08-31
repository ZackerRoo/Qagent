from __future__ import annotations

import inspect
import json
from datetime import date
from hashlib import sha256
from typing import Any, Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.factors.models import FactorRanking
from qagent.factors.research_contract import (
    BASELINE_SIGNS,
    FACTOR_RESEARCH_VERSION,
    FEATURE_COLUMNS,
)
from qagent.research.factor_experiments import (
    FactorResearchConfig,
    _baseline_prediction,
    factor_research_feature_contract_digest,
    neutralize_research_features,
)
from qagent.storage.factor_research import (
    FactorResearchModelBundle,
    FactorResearchRepository,
    FactorShadowRun,
    FactorShadowScore,
)
from qagent.storage.replay_evidence import ReplayEvidenceRepository


class FactorShadowScoringResult(BaseModel):
    status: str
    run: FactorShadowRun | None = None
    runs: list[FactorShadowRun] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


FACTOR_SHADOW_SCORER_PROTOCOL = "factor-shadow-scorer-v2-frozen-source"


class FactorShadowScorerIdentity(BaseModel):
    protocol_version: str = FACTOR_SHADOW_SCORER_PROTOCOL
    protocol_digest: str
    source_digest: str


def factor_shadow_scorer_identity(
    feature_columns: Sequence[str],
    *,
    implementation_source_digest: str | None = None,
) -> FactorShadowScorerIdentity:
    selected = tuple(feature_columns)
    source_digest = implementation_source_digest or sha256(
        "\0".join(
            inspect.getsource(function)
            for function in (
                neutralize_research_features,
                _baseline_prediction,
                factor_research_feature_contract_digest,
                factor_shadow_evidence_model_digest,
                factor_shadow_run_identity,
                score_factor_shadow_runs,
                _score_factor_shadow_bundle,
            )
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "protocol_version": FACTOR_SHADOW_SCORER_PROTOCOL,
        "feature_set_version": FACTOR_RESEARCH_VERSION,
        "feature_contract_digest": factor_research_feature_contract_digest(selected),
        "selected_feature_columns": list(selected),
        "neutralization": "cross_sectional_winsorize_then_size_and_industry_residual",
        "baseline_signs": {feature: BASELINE_SIGNS[feature] for feature in FEATURE_COLUMNS},
        "ensemble": "mean_seed_prediction",
        "rank_policy": "descending_method_first_then_instrument_id",
        "implementation_source_digest": source_digest,
    }
    protocol_digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return FactorShadowScorerIdentity(
        protocol_digest=protocol_digest,
        source_digest=source_digest,
    )


def factor_shadow_evidence_model_digest(
    aggregate_model_digest: str,
    scorer_protocol_digest: str,
) -> str:
    return sha256(
        f"{aggregate_model_digest}:{scorer_protocol_digest}".encode("utf-8")
    ).hexdigest()


def factor_shadow_run_identity(
    *,
    experiment_id: str,
    scan_job_id: str,
    signal_date: date,
    dataset_revision: int,
    evidence_model_digest: str,
) -> str:
    payload = {
        "experiment_id": experiment_id,
        "scan_job_id": scan_job_id,
        "signal_date": signal_date.isoformat(),
        "dataset_revision": dataset_revision,
        "evidence_model_digest": evidence_model_digest,
    }
    return "factor-shadow-run-" + sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def score_factor_shadow_run(
    session_factory: sessionmaker[Session],
    *,
    provider_mode: str,
    scan_job_id: str,
    signal_date: date,
    rankings: Sequence[FactorRanking],
    stock_ids: set[str],
) -> FactorShadowScoringResult:
    store = FactorResearchRepository(session_factory)
    bundle = store.latest_model_bundle(provider_mode)
    if bundle is None:
        return FactorShadowScoringResult(
            status="model_not_ready",
            data_health={
                "factor_shadow_status": "model_not_ready",
                "factor_shadow_paper_isolation": "true",
            },
        )
    return _score_factor_shadow_bundle(
        session_factory,
        store=store,
        bundle=bundle,
        provider_mode=provider_mode,
        scan_job_id=scan_job_id,
        signal_date=signal_date,
        rankings=rankings,
        stock_ids=stock_ids,
    )


def score_factor_shadow_runs(
    session_factory: sessionmaker[Session],
    *,
    provider_mode: str,
    scan_job_id: str,
    signal_date: date,
    rankings: Sequence[FactorRanking],
    stock_ids: set[str],
    max_challengers: int = 3,
) -> FactorShadowScoringResult:
    """Score each distinct frozen research configuration against one baseline.

    This is research-only: it writes append-only shadow scores and does not
    alter the opportunity ranking, paper orders, or position sizing.
    """

    store = FactorResearchRepository(session_factory)
    bundles = store.model_bundles(provider_mode, limit=max_challengers)
    if not bundles:
        return FactorShadowScoringResult(
            status="model_not_ready",
            data_health={
                "factor_shadow_status": "model_not_ready",
                "factor_shadow_paper_isolation": "true",
            },
        )

    results = [
        _score_factor_shadow_bundle(
            session_factory,
            store=store,
            bundle=bundle,
            provider_mode=provider_mode,
            scan_job_id=scan_job_id,
            signal_date=signal_date,
            rankings=rankings,
            stock_ids=stock_ids,
        )
        for bundle in bundles
    ]
    recorded = [result.run for result in results if result.run is not None]
    failed = [result for result in results if result.status != "recorded"]
    status = "recorded" if recorded and not failed else "partial" if recorded else failed[0].status
    health = {
        "factor_shadow_status": status,
        "factor_shadow_candidate_count": str(len(bundles)),
        "factor_shadow_recorded_candidates": str(len(recorded)),
        "factor_shadow_candidate_experiment_ids": ",".join(
            bundle.experiment.experiment_id for bundle in bundles
        ),
        "factor_shadow_paper_isolation": "true",
        "factor_shadow_order_effect": "none",
        "factor_shadow_run_ids": ",".join(
            result.data_health.get("factor_shadow_run_id", "") for result in results
        ),
        "factor_shadow_scorer_protocol_digests": ",".join(
            result.data_health.get("factor_shadow_scorer_protocol_digest", "")
            for result in results
        ),
        "factor_shadow_scorer_source_digests": ",".join(
            result.data_health.get("factor_shadow_scorer_source_digest", "")
            for result in results
        ),
    }
    if failed:
        health["factor_shadow_candidate_failures"] = ",".join(
            f"{result.run.experiment_id if result.run else 'unknown'}:{result.status}"
            for result in failed
        )
    return FactorShadowScoringResult(
        status=status,
        run=recorded[0] if recorded else None,
        runs=recorded,
        data_health=health,
    )


def _score_factor_shadow_bundle(
    session_factory: sessionmaker[Session],
    *,
    store: FactorResearchRepository,
    bundle: FactorResearchModelBundle,
    provider_mode: str,
    scan_job_id: str,
    signal_date: date,
    rankings: Sequence[FactorRanking],
    stock_ids: set[str],
) -> FactorShadowScoringResult:
    config = FactorResearchConfig.model_validate(bundle.experiment.config)
    feature_columns = config.selected_feature_columns
    expected_digest = factor_research_feature_contract_digest(feature_columns)
    scorer_identity = factor_shadow_scorer_identity(feature_columns)
    if config.candidate_id is not None and (
        config.shadow_scorer_protocol_digest != scorer_identity.protocol_digest
        or config.shadow_scorer_source_digest != scorer_identity.source_digest
    ):
        return FactorShadowScoringResult(
            status="scorer_contract_mismatch",
            data_health={
                "factor_shadow_status": "scorer_contract_mismatch",
                "factor_shadow_experiment_id": bundle.experiment.experiment_id,
                "factor_shadow_scorer_protocol_digest": scorer_identity.protocol_digest,
                "factor_shadow_scorer_source_digest": scorer_identity.source_digest,
                "factor_shadow_expected_scorer_protocol_digest": (
                    config.shadow_scorer_protocol_digest or "missing"
                ),
                "factor_shadow_expected_scorer_source_digest": (
                    config.shadow_scorer_source_digest or "missing"
                ),
                "factor_shadow_paper_isolation": "true",
                "factor_shadow_order_effect": "none",
            },
        )
    if any(
        model.feature_set_version != FACTOR_RESEARCH_VERSION
        or model.feature_contract_digest != expected_digest
        for model in bundle.models
    ):
        return FactorShadowScoringResult(
            status="feature_contract_mismatch",
            data_health={
                "factor_shadow_status": "feature_contract_mismatch",
                "factor_shadow_paper_isolation": "true",
            },
        )

    replay = ReplayEvidenceRepository(session_factory, provider_mode)
    dataset_revision = replay.current_revision()
    evidence_model_digest = factor_shadow_evidence_model_digest(
        bundle.aggregate_model_digest,
        scorer_identity.protocol_digest,
    )
    existing_evidence_digests = {
        run.model_digest for run in store.shadow_runs(bundle.experiment.experiment_id)
    }
    if existing_evidence_digests and existing_evidence_digests != {evidence_model_digest}:
        return FactorShadowScoringResult(
            status="scorer_evidence_lane_mismatch",
            data_health={
                "factor_shadow_status": "scorer_evidence_lane_mismatch",
                "factor_shadow_experiment_id": bundle.experiment.experiment_id,
                "factor_shadow_evidence_model_digest": evidence_model_digest,
                "factor_shadow_existing_evidence_model_digests": ",".join(
                    sorted(existing_evidence_digests)
                ),
                "factor_shadow_scorer_protocol_digest": scorer_identity.protocol_digest,
                "factor_shadow_scorer_source_digest": scorer_identity.source_digest,
                "factor_shadow_paper_isolation": "true",
                "factor_shadow_order_effect": "none",
            },
        )
    shadow_run_id = factor_shadow_run_identity(
        experiment_id=bundle.experiment.experiment_id,
        scan_job_id=scan_job_id,
        signal_date=signal_date,
        dataset_revision=dataset_revision,
        evidence_model_digest=evidence_model_digest,
    )
    selected = [item for item in rankings if item.instrument_id in stock_ids]
    industries = replay.industries_as_of(
        [item.instrument_id for item in selected],
        signal_date,
        dataset_revision,
    )
    rows: list[dict[str, Any]] = []
    for ranking in selected:
        features = {
            feature: _finite_or_none(ranking.research_features.get(feature))
            for feature in FEATURE_COLUMNS
        }
        available = sum(features[feature] is not None for feature in feature_columns)
        if available == 0:
            continue
        rows.append(
            {
                "signal_date": signal_date,
                "instrument_id": ranking.instrument_id,
                "industry": (
                    industries[ranking.instrument_id].industry
                    if ranking.instrument_id in industries
                    else None
                ),
                "log_market_cap": _log_market_cap(ranking),
                "feature_coverage": available / len(feature_columns),
                **features,
            }
        )
    if len(rows) < 5:
        return FactorShadowScoringResult(
            status="feature_coverage_insufficient",
            data_health={
                "factor_shadow_status": "feature_coverage_insufficient",
                "factor_shadow_rows": str(len(rows)),
                "factor_shadow_paper_isolation": "true",
            },
        )

    frame = neutralize_research_features(pd.DataFrame(rows))
    frame["baseline_score"] = _baseline_prediction(frame)
    try:
        import lightgbm as lgb
    except ImportError as error:  # pragma: no cover - dependency is exercised in integration.
        raise RuntimeError("lightgbm dependency is unavailable") from error
    model_frame = frame.loc[:, list(feature_columns)].astype("float64")
    predictions = [
        lgb.Booster(model_str=model.model_text).predict(model_frame)
        for model in bundle.models
    ]
    frame["challenger_score"] = np.mean(np.vstack(predictions), axis=0)
    frame["baseline_rank"] = (
        frame["baseline_score"].rank(method="first", ascending=False).astype(int)
    )
    frame["challenger_rank"] = (
        frame["challenger_score"].rank(method="first", ascending=False).astype(int)
    )
    frame = frame.sort_values(["challenger_rank", "instrument_id"])
    scores = [
        FactorShadowScore(
            instrument_id=str(row.instrument_id),
            baseline_score=round(float(row.baseline_score), 10),
            challenger_score=round(float(row.challenger_score), 10),
            baseline_rank=int(row.baseline_rank),
            challenger_rank=int(row.challenger_rank),
            feature_coverage=round(float(row.feature_coverage), 6),
            industry=str(row.industry) if pd.notna(row.industry) else None,
        )
        for row in frame.itertuples(index=False)
    ]
    run = store.record_shadow_scores(
        experiment_id=bundle.experiment.experiment_id,
        scan_job_id=scan_job_id,
        signal_date=signal_date,
        dataset_revision=dataset_revision,
        model_digest=evidence_model_digest,
        scores=scores,
    )
    industry_count = sum(item.industry is not None for item in scores)
    return FactorShadowScoringResult(
        status="recorded",
        run=run,
        runs=[run],
        data_health={
            "factor_shadow_status": "recorded",
            "factor_shadow_experiment_id": bundle.experiment.experiment_id,
            "factor_shadow_rows": str(len(scores)),
            "factor_shadow_industry_rows": str(industry_count),
            "factor_shadow_dataset_revision": str(dataset_revision),
            "factor_shadow_model_digest": bundle.aggregate_model_digest,
            "factor_shadow_evidence_model_digest": evidence_model_digest,
            "factor_shadow_run_id": shadow_run_id,
            "factor_shadow_scorer_protocol": scorer_identity.protocol_version,
            "factor_shadow_scorer_protocol_digest": scorer_identity.protocol_digest,
            "factor_shadow_scorer_source_digest": scorer_identity.source_digest,
            "factor_shadow_candidate_id": config.candidate_id or "legacy_grandfather",
            "factor_shadow_candidate_protocol": config.candidate_protocol_version,
            "factor_shadow_selected_features": ",".join(feature_columns),
            "factor_shadow_decision_weight": "false",
            "factor_shadow_activation_allowed": "false",
            "factor_shadow_paper_isolation": "true",
            "factor_shadow_order_effect": "none",
        },
    )


def _log_market_cap(ranking: FactorRanking) -> float | None:
    exposure = next(
        (item for item in ranking.factor_exposures if item.factor_id == "size"),
        None,
    )
    market_cap = _finite_or_none(exposure.raw_value if exposure is not None else None)
    if market_cap is None or market_cap <= 0:
        return None
    return float(np.log(market_cap))


def _finite_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None
