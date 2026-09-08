"""Bounded offline factor ablations; never creates database or candidate records."""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qagent.factors.research_contract import FEATURE_COLUMNS
from qagent.market.calendars import trading_day_offset, trading_sessions_in_range
from qagent.research.factor_experiments import (
    FactorResearchConfig,
    compare_baseline_and_lightgbm,
    neutralize_research_features,
)

PROTOCOL = "offline-factor-group-ablation-v1"
FACTOR_GROUPS = {
    "trend_reversal": (
        "momentum_20", "momentum_60", "momentum_120", "return_5",
        "trend_slope_60", "trend_r2_60", "distance_ma20",
    ),
    "risk": ("volatility_20", "downside_risk_60", "max_drawdown_60"),
    "liquidity": ("turnover_log_20", "volume_ratio_5_20"),
    "fundamentals": (
        "earnings_yield", "return_on_equity", "gross_margin",
        "revenue_growth", "earnings_growth",
    ),
}


def validate_input(payload: dict[str, Any]) -> tuple[pd.DataFrame, FactorResearchConfig]:
    if payload.get("protocol") != PROTOCOL:
        raise ValueError(f"input protocol must be {PROTOCOL}")
    if payload.get("feature_stage") not in {"raw", "neutralized"}:
        raise ValueError("feature_stage must explicitly be raw or neutralized")
    if not isinstance(payload.get("provenance"), str) or not payload["provenance"].strip():
        raise ValueError("provenance must identify the frozen dataset source")
    settings = payload.get("config", {})
    required_settings = {
        "dataset_revision", "start_date", "end_date", "seeds", "model_recipe",
        "rebalance_step_sessions", "horizon_sessions", "round_trip_cost_bps",
        "top_fraction",
    }
    if missing := required_settings - settings.keys():
        raise ValueError(f"explicit config fields missing: {sorted(missing)}")
    config = FactorResearchConfig.model_validate(settings)
    if config.candidate_id is not None:
        raise ValueError("offline ablations must not name a registered candidate")
    if config.dataset_revision is None or config.dataset_revision <= 0:
        raise ValueError("a frozen positive dataset_revision is required")
    if len(set(config.seeds)) != len(config.seeds):
        raise ValueError("seeds must be distinct")
    frame = pd.DataFrame(payload.get("rows", []))
    required = set(FEATURE_COLUMNS) | {
        "signal_date", "instrument_id", "industry", "log_market_cap",
        "target_excess_return_pct",
    }
    if missing := required - set(frame.columns):
        raise ValueError(f"dataset columns missing: {sorted(missing)}")
    if frame.empty:
        raise ValueError("dataset is empty")
    if frame["signal_date"].isna().any() or frame["instrument_id"].isna().any():
        raise ValueError("row identities must be non-null")
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="raise").dt.date
    frame["instrument_id"] = frame["instrument_id"].astype(str)
    if frame["instrument_id"].str.strip().eq("").any():
        raise ValueError("instrument_id must be non-empty")
    if frame.duplicated(["signal_date", "instrument_id"]).any():
        raise ValueError("duplicate signal_date/instrument_id rows")
    if not frame["signal_date"].between(config.start_date, config.end_date).all():
        raise ValueError("signal dates outside declared config window")
    if frame["signal_date"].nunique() < 15:
        raise ValueError("at least 15 cross-sections are required")
    if frame.groupby("signal_date").size().min() < 5:
        raise ValueError("at least 5 instruments in every cross-section are required")
    _validate_label_boundaries(frame, config)
    numeric = [*FEATURE_COLUMNS, "log_market_cap", "target_excess_return_pct"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
        if np.isinf(frame[column]).any():
            raise ValueError(f"infinite values in {column}")
    if frame["target_excess_return_pct"].isna().any():
        raise ValueError("targets must be fully observed; cohort is never filtered per variant")
    return frame.sort_values(["signal_date", "instrument_id"]).reset_index(drop=True), config


def _validate_label_boundaries(frame: pd.DataFrame, config: FactorResearchConfig) -> None:
    """Verify actual label maturity, not caller-declared sampling frequency."""
    dates = sorted(frame["signal_date"].unique())
    sessions = set(trading_sessions_in_range(config.start_date, config.end_date))
    if not set(dates).issubset(sessions):
        raise ValueError("signal dates must be XSHG trading sessions")
    if trading_day_offset(dates[-1], config.horizon_sessions) > config.end_date:
        raise ValueError("last label matures after declared config end_date")
    # Match the existing comparator's fixed split; gaps in the cohort are legal.
    # Its cross-section purge alone cannot guarantee separation for external JSON.
    purge = max(1, math.ceil(config.horizon_sessions / config.rebalance_step_sessions))
    train_boundary = max(3, int(len(dates) * 0.60))
    valid_boundary = max(train_boundary + purge + 2, int(len(dates) * 0.80))
    train_dates = dates[: max(1, train_boundary - purge)]
    valid_dates = dates[train_boundary : max(train_boundary + 1, valid_boundary - purge)]
    test_dates = dates[valid_boundary:]
    if min(len(train_dates), len(valid_dates), len(test_dates)) < 2:
        raise ValueError("purged train/valid/test geometry is too small")
    for name, earlier, later in (
        ("training/validation", train_dates, valid_dates),
        ("validation/test", valid_dates, test_dates),
    ):
        if trading_day_offset(earlier[-1], config.horizon_sessions) >= later[0]:
            raise ValueError(f"{name} label maturity overlaps the next split")


def prepare_features(frame: pd.DataFrame, *, feature_stage: str) -> pd.DataFrame:
    prepared = frame.copy(deep=True)
    # Neutralization regresses each factor independently against the same size /
    # industry controls. Prepare once, preserving the fixed linear reference.
    if feature_stage == "raw":
        prepared = neutralize_research_features(prepared)
    elif feature_stage != "neutralized":
        raise ValueError("unknown feature_stage")
    return prepared


def run_factor_ablation(payload: dict[str, Any]) -> dict[str, Any]:
    frame, config = validate_input(payload)
    variants = {"full_features": FEATURE_COLUMNS}
    variants.update({
        f"without_{group}": tuple(f for f in FEATURE_COLUMNS if f not in omitted)
        for group, omitted in FACTOR_GROUPS.items()
    })
    manifest = {
        "protocol": PROTOCOL,
        "feature_stage": payload["feature_stage"],
        "provenance": payload["provenance"],
        "source_sha256": {
            str(path.relative_to(Path(__file__).resolve().parents[1])): sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__).resolve(),
                Path(__file__).resolve().with_name("factor_experiments.py"),
                Path(__file__).resolve().parents[1] / "factors/research_contract.py",
            )
        },
        "config": config.model_dump(mode="json"),
        "variants": {name: list(features) for name, features in variants.items()},
        "held_out_policy": "fixed_retrospective_previously_exposed_test_descriptive_only_no_tuning",
    }
    manifest_digest = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    cohort_digest = sha256(frame.to_json(orient="records", date_format="iso").encode()).hexdigest()
    prepared = prepare_features(frame, feature_stage=payload["feature_stage"])
    results = []
    expected_split = None
    expected_reference = None
    for name, features in variants.items():
        usable = prepared.groupby("signal_date")[list(features)].count()
        if (usable.max(axis=1) < 5).any():
            raise ValueError("insufficient usable selected factors in a cross-section")
        # Local research-only copy: the public config validator intentionally
        # disallows arbitrary candidate subsets. Never pass this to persistence.
        variant_config = config.model_copy(update={"selected_feature_columns": features})
        metrics, artifacts, _models = compare_baseline_and_lightgbm(prepared, variant_config)
        if artifacts["selected_feature_columns"] != list(features):
            raise RuntimeError("trainer did not honor selected feature columns")
        if expected_split is None:
            expected_split = artifacts["split"]
        elif artifacts["split"] != expected_split:
            raise RuntimeError("ablation cohort or date split changed")
        if expected_reference is None:
            expected_reference = metrics["baseline"]
        elif expected_reference != metrics["baseline"]:
            raise RuntimeError("full-feature linear reference changed")
        # Comparator dispositions describe candidate registration workflows;
        # offline ablations report measurements only, not a winner or promotion.
        results.append({
            "variant": name,
            "selected_feature_columns": list(features),
            "lightgbm": metrics["lightgbm_challenger"],
            "full_linear_reference": metrics["baseline"],
            "best_iterations": artifacts["best_iterations"],
            "feature_importance": artifacts["feature_importance"],
            "model_digests": [model["model_digest"] for model in _models],
        })
    return {
        "status": "completed_offline_measurement",
        "manifest": manifest,
        "manifest_sha256": manifest_digest,
        "cohort_sha256": cohort_digest,
        "cohort_rows": len(frame),
        "feature_non_null_counts": {f: int(prepared[f].notna().sum()) for f in FEATURE_COLUMNS},
        "split": expected_split,
        "results": results,
        "activation_allowed": False,
        "decision_weight": False,
        "model_persisted": False,
        "candidate_registered": False,
        "metric_semantics": {
            "net_top_bucket_excess_return_pct": (
                "Mean cross-sectional top-bucket horizon excess return in percentage points "
                "minus heuristic average-turnover cost; not cumulative or annualized portfolio return."
            ),
            "top_bucket_max_drawdown_pct": (
                "Diagnostic compounding of horizon labels at each rebalance; labels overlap when "
                "horizon_sessions exceeds rebalance_step_sessions (20 versus 10 in preregistration). "
                "Not drawdown of a tradable portfolio."
            ),
            "variant_selection": "No automatic best variant or promotion; descriptive measurements only.",
        },
        "dataset_preparation_caveat": "frozen_replay_revision_with_current_local_historical_rules_not_version_frozen",
    }
