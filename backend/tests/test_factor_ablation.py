from datetime import date
import importlib.util
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from qagent.factors.research_contract import FEATURE_COLUMNS
from qagent.market.calendars import trading_sessions_in_range
from qagent.research import factor_ablation as ablation


def payload():
    rng = np.random.default_rng(7)
    rows = []
    dates = trading_sessions_in_range(date(2024, 1, 1), date(2025, 1, 1))[::10][:20]
    for day in range(20):
        for stock in range(12):
            rows.append({
                "signal_date": str(dates[day]),
                "instrument_id": str(stock), "industry": "test",
                "log_market_cap": float(rng.normal()),
                "target_excess_return_pct": float(rng.normal()),
                **{feature: float(rng.normal()) for feature in FEATURE_COLUMNS},
            })
    return {
        "protocol": ablation.PROTOCOL, "feature_stage": "raw", "provenance": "synthetic test only",
        "config": {"dataset_revision": 7, "start_date": "2024-01-01", "end_date": "2025-01-01",
                   "seeds": [7], "model_recipe": "balanced_v1", "rebalance_step_sessions": 10,
                   "horizon_sessions": 20, "round_trip_cost_bps": 10, "top_fraction": 0.1},
        "rows": rows,
    }


def test_predeclared_groups_partition_contract():
    flattened = [f for group in ablation.FACTOR_GROUPS.values() for f in group]
    assert len(ablation.FACTOR_GROUPS) == 4
    assert len(flattened) == len(set(flattened)) == len(FEATURE_COLUMNS)
    assert set(flattened) == set(FEATURE_COLUMNS)


@pytest.mark.parametrize("failure", ["duplicate", "short", "target", "revision", "stage"])
def test_bad_input_fails_closed(failure):
    sample = payload()
    if failure == "duplicate":
        sample["rows"].append(sample["rows"][0])
    elif failure == "short":
        sample["rows"] = sample["rows"][:24]
    elif failure == "target":
        sample["rows"][0]["target_excess_return_pct"] = None
    elif failure == "revision":
        sample["config"]["dataset_revision"] = 0
    else:
        sample["feature_stage"] = "unknown"
    with pytest.raises(ValueError):
        ablation.validate_input(sample)


@pytest.mark.parametrize("boundary", ["training/validation", "validation/test"])
def test_external_dates_cannot_under_purge_labels(boundary):
    sample = payload()
    sessions = trading_sessions_in_range(date(2024, 1, 1), date(2025, 1, 1))
    indexes = (list(range(20)) if boundary == "training/validation" else
               [day * 10 for day in range(14)] + list(range(131, 137)))
    for index, row in enumerate(sample["rows"]):
        row["signal_date"] = str(sessions[indexes[index // 12]])
    with pytest.raises(ValueError, match=boundary):
        ablation.validate_input(sample)


def test_label_maturity_equal_to_next_split_is_rejected():
    sample = payload()
    # One purged cross-section leaves exactly 20 sessions between these splits.
    sample["config"]["rebalance_step_sessions"] = 20
    with pytest.raises(ValueError, match="label maturity overlaps"):
        ablation.validate_input(sample)


def test_labels_cannot_extend_past_config_end():
    sample = payload()
    sample["config"]["end_date"] = sample["rows"][-1]["signal_date"]
    with pytest.raises(ValueError, match="last label matures"):
        ablation.validate_input(sample)


def test_non_session_dates_are_rejected():
    sample = payload()
    for row in sample["rows"][:12]:
        row["signal_date"] = "2024-01-01"
    with pytest.raises(ValueError, match="XSHG trading sessions"):
        ablation.validate_input(sample)


def test_missing_cross_sections_are_allowed_with_safe_label_boundaries():
    sample = payload()
    del sample["rows"][60:72]
    frame, _ = ablation.validate_input(sample)
    assert frame["signal_date"].nunique() == 19


def test_normalization_is_once_and_reference_is_fixed(monkeypatch):
    calls = []
    original = ablation.neutralize_research_features
    def neutralize(frame):
        calls.append("normalize")
        return original(frame)
    frames = []
    def compare(frame, config):
        frames.append(frame.copy(deep=True))
        features = list(config.selected_feature_columns)
        return {"baseline": {"value": 1}, "lightgbm_challenger": {}}, {
            "selected_feature_columns": features, "split": {"test_rows": 48},
            "best_iterations": [1], "feature_importance": [],
        }, []
    monkeypatch.setattr(ablation, "neutralize_research_features", neutralize)
    monkeypatch.setattr(ablation, "compare_baseline_and_lightgbm", compare)
    report = ablation.run_factor_ablation(payload())
    assert calls == ["normalize"]
    assert len(report["results"]) == 5
    for frame in frames[1:]:
        pd.testing.assert_frame_equal(frame, frames[0])
    assert report["candidate_registered"] is False
    assert report["feature_non_null_counts"]["momentum_20"] == 240


def test_neutralized_input_is_not_transformed_again(monkeypatch):
    frame, _ = ablation.validate_input(payload())
    def unexpected(*args):
        pytest.fail("neutralization called twice")
    monkeypatch.setattr(ablation, "neutralize_research_features", unexpected)
    pd.testing.assert_frame_equal(ablation.prepare_features(frame, feature_stage="neutralized"), frame)


def test_real_trainer_receives_subset_for_training_validation_and_prediction(monkeypatch):
    lgb = pytest.importorskip("lightgbm")
    datasets, predictions = [], []
    original_dataset, original_predict = lgb.Dataset, lgb.Booster.predict
    def dataset(data, *args, **kwargs):
        datasets.append(tuple(data.columns))
        return original_dataset(data, *args, **kwargs)
    def predict(self, data, *args, **kwargs):
        predictions.append(tuple(data.columns))
        return original_predict(self, data, *args, **kwargs)
    monkeypatch.setattr(lgb, "Dataset", dataset)
    monkeypatch.setattr(lgb.Booster, "predict", predict)
    report = ablation.run_factor_ablation(payload())
    expected = [tuple(row["selected_feature_columns"]) for row in report["results"]]
    assert datasets == [features for features in expected for _ in range(2)]
    assert predictions == expected
    assert all(row["full_linear_reference"] == report["results"][0]["full_linear_reference"]
               for row in report["results"])


def test_database_mode_enforces_read_only(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/run_factor_ablation.py"
    spec = importlib.util.spec_from_file_location("offline_ablation_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / "source.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sentinel (value INTEGER)")
    connection.commit()
    connection.close()
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from qagent.research import factor_experiments
    def build(factory, config):
        with factory() as session:
            assert session.execute(text("PRAGMA query_only")).scalar() == 1
            with pytest.raises(OperationalError, match="readonly"):
                session.execute(text("INSERT INTO sentinel VALUES (1)"))
        return pd.DataFrame({"signal_date": [date(2024, 1, 1)]}), {}
    monkeypatch.setattr(factor_experiments, "build_factor_research_dataset", build)
    result = module.dataset_from_database(database, payload()["config"])
    assert result["feature_stage"] == "neutralized"
