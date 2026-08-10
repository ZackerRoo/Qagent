from __future__ import annotations

from datetime import date
from typing import Any, Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.factors.models import FactorRanking
from qagent.factors.research_contract import FACTOR_RESEARCH_VERSION, FEATURE_COLUMNS
from qagent.research.factor_experiments import (
    _baseline_prediction,
    factor_research_feature_contract_digest,
    neutralize_research_features,
)
from qagent.storage.factor_research import (
    FactorResearchRepository,
    FactorShadowRun,
    FactorShadowScore,
)
from qagent.storage.replay_evidence import ReplayEvidenceRepository


class FactorShadowScoringResult(BaseModel):
    status: str
    run: FactorShadowRun | None = None
    data_health: dict[str, str] = Field(default_factory=dict)


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
    expected_digest = factor_research_feature_contract_digest()
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
        available = sum(value is not None for value in features.values())
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
                "feature_coverage": available / len(FEATURE_COLUMNS),
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
    predictions = [
        lgb.Booster(model_str=model.model_text).predict(frame[list(FEATURE_COLUMNS)])
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
        model_digest=bundle.aggregate_model_digest,
        scores=scores,
    )
    industry_count = sum(item.industry is not None for item in scores)
    return FactorShadowScoringResult(
        status="recorded",
        run=run,
        data_health={
            "factor_shadow_status": "recorded",
            "factor_shadow_experiment_id": bundle.experiment.experiment_id,
            "factor_shadow_rows": str(len(scores)),
            "factor_shadow_industry_rows": str(industry_count),
            "factor_shadow_dataset_revision": str(dataset_revision),
            "factor_shadow_model_digest": bundle.aggregate_model_digest,
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
