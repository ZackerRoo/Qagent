from datetime import date
import json
from pathlib import Path

import pandas as pd

from qagent.research.factor_candidate_coverage import (
    FactorCandidateCoverageManifest,
    FactorCoverageSource,
    audit_factor_candidate_coverage,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _source() -> FactorCoverageSource:
    return FactorCoverageSource(
        provider_mode="free",
        dataset_revision=8940,
        source_artifact_id="factor-research-example",
    )


def test_candidate_coverage_reports_field_and_joint_point_in_time_evidence():
    frame = pd.DataFrame(
        {
            "signal_date": [date(2025, 1, 2), date(2025, 1, 2), date(2025, 1, 3)],
            "return_on_equity": [1.0, None, 3.0],
            "gross_margin": [1.0, 2.0, None],
            "earnings_growth": [1.0, 2.0, 3.0],
            "revenue_growth": [1.0, None, 3.0],
            "turnover_log_20": [1.0, 2.0, 3.0],
            "volume_ratio_5_20": [1.0, 2.0, 3.0],
            "momentum_20": [1.0, 2.0, 3.0],
            "trend_slope_60": [1.0, 2.0, 3.0],
            "trend_r2_60": [1.0, 2.0, 3.0],
            "downside_risk_60": [1.0, 2.0, 3.0],
            "max_drawdown_60": [1.0, 2.0, 3.0],
            "earnings_yield": [1.0, 2.0, 3.0],
        }
    )

    manifest = audit_factor_candidate_coverage(frame, _source())
    by_id = {item.candidate_id: item for item in manifest.candidates}
    quality = by_id["quality-profitability-level-v1"]

    assert quality.coverage_status == "verified"
    assert quality.sample_count == 3
    assert quality.signal_sessions == 2
    assert quality.covered_signal_sessions == 1
    assert quality.joint_non_null_samples == 1
    assert quality.joint_non_null_rate == 0.333333
    assert quality.first_signal_date == date(2025, 1, 2)
    assert quality.last_signal_date == date(2025, 1, 3)
    assert quality.covered_first_signal_date == date(2025, 1, 2)
    assert {item.feature: item.non_null_rate for item in quality.field_coverage} == {
        "return_on_equity": 0.666667,
        "gross_margin": 0.666667,
    }
    assert manifest.source.dataset_revision == 8940
    assert manifest.experiment_start_allowed is False
    assert manifest.decision_weight is False


def test_candidate_coverage_fails_closed_for_missing_fields_and_empty_joint_data():
    frame = pd.DataFrame(
        {
            "signal_date": [date(2025, 1, 2)],
            "return_on_equity": [1.0],
            "earnings_growth": [None],
            "revenue_growth": [None],
        }
    )

    manifest = audit_factor_candidate_coverage(frame, _source())
    by_id = {item.candidate_id: item for item in manifest.candidates}

    assert by_id["quality-profitability-level-v1"].coverage_status == "missing_required_fields"
    assert by_id["quality-profitability-level-v1"].missing_features == ("gross_margin",)
    growth = by_id["profitability-growth-confirmation-v1"]
    assert growth.coverage_status == "no_samples"
    assert growth.joint_non_null_samples == 0
    assert growth.experiment_start_allowed is False


def test_catalyst_remains_future_even_if_a_column_is_present():
    frame = pd.DataFrame(
        {
            "signal_date": [date(2025, 1, 2)],
            "point_in_time_catalyst_event": [1.0],
        }
    )

    manifest = audit_factor_candidate_coverage(frame, _source())
    catalyst = next(item for item in manifest.candidates if item.candidate_id == "point-in-time-catalyst-v1")

    assert catalyst.coverage_status == "future_capability"
    assert catalyst.missing_features == ("point_in_time_catalyst_event",)
    assert catalyst.joint_non_null_samples == 0
    assert catalyst.experiment_start_allowed is False


def test_checked_in_real_coverage_evidence_is_the_pydantic_manifest_contract():
    path = (
        WORKSPACE_ROOT
        / "docs"
        / "research"
        / "factor-candidate-coverage-2026-08-31.json"
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    manifest = FactorCandidateCoverageManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    by_id = {item.candidate_id: item for item in manifest.candidates}

    assert manifest.model_dump(mode="json") == raw
    assert manifest.source.dataset_revision == 8940
    assert manifest.source.read_policy == "sqlite_mode_ro_immutable"
    assert manifest.source.inventory_stock_count == 5350
    assert by_id["quality-profitability-level-v1"].sample_count == 420381
    assert by_id["quality-profitability-level-v1"].signal_sessions == 88
    assert by_id["valuation-growth-fit-v1"].joint_non_null_samples == 298951
    assert by_id["valuation-growth-fit-v1"].joint_non_null_rate == 0.711143
    assert by_id["point-in-time-catalyst-v1"].coverage_status == "future_capability"
    assert manifest.experiment_start_allowed is False
