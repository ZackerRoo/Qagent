from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from qagent.db import create_session_factory, initialize_database
from qagent.research.factor_experiments import (
    FEATURE_COLUMNS,
    FactorResearchConfig,
    _attach_excess_labels,
    _prepare_instrument_rows,
    compare_baseline_and_lightgbm,
    neutralize_research_features,
)
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

    metrics, artifacts = compare_baseline_and_lightgbm(frame, config)

    assert metrics["activation_allowed"] is False
    assert metrics["lightgbm_challenger"]["cross_sections"] >= 2
    assert artifacts["split"]["purge_cross_sections"] == 2
    assert artifacts["paper_model_unchanged"] is True
    assert artifacts["feature_importance"]
