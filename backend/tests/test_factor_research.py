from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import numpy as np
import pandas as pd
import pytest

from qagent.db import create_session_factory, initialize_database
from qagent.factors.models import FactorExposure, FactorRanking
from qagent.factors.research_contract import FACTOR_RESEARCH_VERSION
from qagent.research.factor_experiments import (
    FEATURE_COLUMNS,
    FactorResearchConfig,
    _attach_excess_labels,
    _prepare_instrument_rows,
    compare_baseline_and_lightgbm,
    factor_research_feature_contract_digest,
    neutralize_research_features,
)
from qagent.research.factor_shadow import score_factor_shadow_run
from qagent.storage.factor_research import FactorResearchRepository
from qagent.storage.replay_evidence import ReplayFactorBarReadRow


def test_factor_research_recorder_persists_terminal_result(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'factor-research.db'}"
    initialize_database(database_url)
    store = FactorResearchRepository(create_session_factory(database_url))
    experiment = store.create(
        experiment_name="fixture experiment",
        provider_mode="fixture",
        model_family="baseline+lightgbm",
        benchmark_id="CN:000300.IDX",
        dataset_revision=7,
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        code_revision="a" * 40,
        config={"horizon_sessions": 20},
    )

    running = store.mark_running(experiment.experiment_id)
    completed = store.complete(
        experiment.experiment_id,
        metrics={"activation_allowed": False},
        data_health={"calendar": "XSHG"},
        artifacts={"paper_model_unchanged": True},
    )

    assert running.status == "running"
    assert completed.status == "succeeded"
    assert completed.metrics == {"activation_allowed": False}
    assert store.list()[0].experiment_id == experiment.experiment_id
    with pytest.raises(ValueError, match="not running"):
        store.complete(
            experiment.experiment_id,
            metrics={},
            data_health={},
            artifacts={},
        )


def test_excess_label_prefers_sufficient_point_in_time_industry_group():
    rows = []
    signal_date = date(2025, 1, 2)
    for index in range(6):
        rows.append(
            {
                "signal_date": signal_date,
                "instrument_id": f"CN:{index:06d}",
                "industry": "银行",
                "raw_forward_return_pct": float(index),
                "benchmark_return_pct": 1.0,
            }
        )
    rows.append(
        {
            "signal_date": signal_date,
            "instrument_id": "CN:999999",
            "industry": "稀疏行业",
            "raw_forward_return_pct": 3.0,
            "benchmark_return_pct": 1.0,
        }
    )

    labeled = _attach_excess_labels(pd.DataFrame(rows))

    bank = labeled[labeled["industry"] == "银行"]
    sparse = labeled[labeled["industry"] == "稀疏行业"].iloc[0]
    assert set(bank["label_scope"]) == {"industry_excess"}
    assert bank["target_excess_return_pct"].median() == pytest.approx(0.0)
    assert sparse["label_scope"] == "benchmark_excess"
    assert sparse["target_excess_return_pct"] == pytest.approx(2.0)


def test_neutralization_removes_cross_sectional_size_loading():
    rng = np.random.default_rng(42)
    rows = []
    signal_date = date(2025, 1, 2)
    for index in range(120):
        size = 18 + index / 25
        row = {
            "signal_date": signal_date,
            "instrument_id": f"CN:{index:06d}",
            "industry": "行业A" if index % 2 else "行业B",
            "log_market_cap": size,
        }
        for feature in FEATURE_COLUMNS:
            row[feature] = size * 2 + rng.normal(0, 0.2)
        rows.append(row)

    normalized = neutralize_research_features(pd.DataFrame(rows))

    correlation = normalized["momentum_20"].corr(normalized["log_market_cap"])
    assert abs(correlation) < 1e-8
    assert normalized["momentum_20"].std() == pytest.approx(1.0, rel=0.02)


def test_replay_factor_rows_use_volume_price_turnover_proxy():
    rows = [
        ReplayFactorBarReadRow(
            instrument_id="CN:000001",
            trade_date=date(2024, 1, 1) + timedelta(days=index),
            raw_close=Decimal("10"),
            adjusted_open=Decimal("10"),
            adjusted_high=Decimal("10"),
            adjusted_low=Decimal("10"),
            adjusted_close=Decimal("10"),
            volume=Decimal("100"),
            adjustment_mode="forward",
        )
        for index in range(61)
    ]

    prepared = _prepare_instrument_rows(rows)

    assert prepared is not None
    assert prepared[3][-1] == pytest.approx(1_000)


def test_lightgbm_challenger_uses_purged_time_split():
    pytest.importorskip("lightgbm")
    rng = np.random.default_rng(7)
    rows = []
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for date_index in range(30):
        signal_date = (start + timedelta(days=date_index * 10)).date()
        for instrument_index in range(60):
            row = {
                "signal_date": signal_date,
                "instrument_id": f"CN:{instrument_index:06d}",
                "industry": f"industry-{instrument_index % 6}",
                "log_market_cap": rng.normal(22, 1),
            }
            for feature in FEATURE_COLUMNS:
                row[feature] = rng.normal()
            row["target_excess_return_pct"] = (
                1.5 * row["momentum_20"]
                - 0.8 * row["volatility_20"] ** 2
                + rng.normal(0, 0.3)
            )
            rows.append(row)
    frame = pd.DataFrame(rows)
    config = FactorResearchConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        dataset_revision=7,
        seeds=[7],
    )

    metrics, artifacts, model_artifacts = compare_baseline_and_lightgbm(frame, config)

    assert metrics["activation_allowed"] is False
    assert metrics["lightgbm_challenger"]["cross_sections"] >= 2
    assert artifacts["split"]["purge_cross_sections"] == 2
    assert artifacts["paper_model_unchanged"] is True
    assert artifacts["feature_importance"]
    assert artifacts["shadow_model_persisted"] is True
    assert len(model_artifacts) == 1
    assert model_artifacts[0]["model_digest"]
    assert "tree" in model_artifacts[0]["model_text"]


def test_lightgbm_shadow_scores_are_persisted_without_paper_activation(tmp_path):
    lightgbm = pytest.importorskip("lightgbm")
    database_url = f"sqlite:///{tmp_path / 'factor-shadow.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    store = FactorResearchRepository(session_factory)
    rng = np.random.default_rng(17)
    training = pd.DataFrame(
        rng.normal(size=(120, len(FEATURE_COLUMNS))),
        columns=list(FEATURE_COLUMNS),
    )
    label = 1.2 * training["momentum_20"] - 0.7 * training["volatility_20"]
    model = lightgbm.train(
        {
            "objective": "regression_l1",
            "verbosity": -1,
            "num_threads": 1,
            "seed": 17,
        },
        lightgbm.Dataset(training, label=label),
        num_boost_round=12,
    )
    model_text = model.model_to_string()
    experiment = store.create(
        experiment_name="shadow fixture",
        provider_mode="fixture",
        model_family="baseline+lightgbm",
        benchmark_id="CN:000300.IDX",
        dataset_revision=0,
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
        code_revision="b" * 40,
        config={"paper_model_unchanged": True},
    )
    store.mark_running(experiment.experiment_id)
    store.complete(
        experiment.experiment_id,
        metrics={"activation_allowed": False},
        data_health={},
        artifacts={"paper_model_unchanged": True},
        model_artifacts=[
            {
                "seed": 17,
                "feature_set_version": FACTOR_RESEARCH_VERSION,
                "feature_contract_digest": factor_research_feature_contract_digest(),
                "model_digest": sha256(model_text.encode("utf-8")).hexdigest(),
                "model_text": model_text,
            }
        ],
    )
    rankings = [
        FactorRanking(
            instrument_id=f"CN:{index:06d}",
            factor_score=0.5,
            factor_rank=index + 1,
            percentile=0.5,
            momentum_score=0.5,
            trend_quality_score=0.5,
            liquidity_score=0.5,
            low_risk_score=0.5,
            reversal_score=0.5,
            execution_penalty=0.0,
            data_completeness=1.0,
            factor_exposures=[
                FactorExposure(
                    factor_id="size",
                    label="size",
                    raw_value=float(10_000_000_000 + index * 1_000_000),
                    score=0.5,
                    weight=0.0,
                    explanation="fixture",
                )
            ],
            research_features={
                feature: float(rng.normal()) for feature in FEATURE_COLUMNS
            },
        )
        for index in range(25)
    ]

    result = score_factor_shadow_run(
        session_factory,
        provider_mode="fixture",
        scan_job_id="scan-fixture-1",
        signal_date=date(2026, 8, 10),
        rankings=rankings,
        stock_ids={item.instrument_id for item in rankings},
    )
    retried = score_factor_shadow_run(
        session_factory,
        provider_mode="fixture",
        scan_job_id="scan-fixture-1",
        signal_date=date(2026, 8, 10),
        rankings=rankings,
        stock_ids={item.instrument_id for item in rankings},
    )

    assert result.status == "recorded"
    assert result.run is not None
    assert result.run.scored_instruments == 25
    assert len(result.run.top_scores) == 20
    assert result.data_health["factor_shadow_paper_isolation"] == "true"
    assert retried.run == result.run
    assert store.latest_shadow_run("fixture") == result.run
    with pytest.raises(ValueError, match="retry identity"):
        store.record_shadow_scores(
            experiment_id=experiment.experiment_id,
            scan_job_id="scan-fixture-1",
            signal_date=date(2026, 8, 11),
            dataset_revision=0,
            model_digest=result.run.model_digest,
            scores=result.run.top_scores,
        )
