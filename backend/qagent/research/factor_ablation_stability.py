"""Descriptive paired diagnostics on an already exposed, fixed test cohort.

Only the offline ablation calls this module. Model strings stay in memory;
no model registration, database access, inference activation, or tuning occurs.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from qagent.research.factor_experiments import _model_metrics


def date_outcomes(frame: pd.DataFrame, predictions: np.ndarray, top_fraction: float) -> list[dict]:
    scored = frame.assign(prediction=predictions)
    rows = []
    previous = None
    for day, group in scored.groupby("signal_date", sort=True):
        if len(group) < 5 or not np.isfinite(group["prediction"]).all():
            raise ValueError("stability requires every test row to have a finite prediction")
        selected = group.nlargest(max(1, math.ceil(len(group) * top_fraction)), "prediction")
        holdings = set(selected["instrument_id"].astype(str))
        rank_ic = group["prediction"].rank().corr(group["target_excess_return_pct"].rank())
        rows.append({
            "signal_date": str(day), "sample_rows": len(group), "top_count": len(selected),
            "rank_ic": float(rank_ic) if pd.notna(rank_ic) else None,
            "gross_top_bucket_excess_return_pct": float(selected["target_excess_return_pct"].mean()),
            "turnover_from_previous": None if previous is None else
                1 - len(previous & holdings) / max(len(previous), len(holdings), 1),
        })
        previous = holdings
    return rows


def collect_stability(frame, config, artifacts, models, expected_metrics) -> dict:
    import lightgbm as lgb

    split = artifacts["split"]
    test = frame.loc[frame["signal_date"].between(
        pd.Timestamp(split["test_start"]).date(), pd.Timestamp(split["test_end"]).date()
    )].copy()
    if len(test) != split["test_rows"]:
        raise RuntimeError("stability test cohort differs from comparator")
    if [model["seed"] for model in models] != list(config.seeds):
        raise RuntimeError("stability seed identities differ from comparator")
    predictions, seeds = [], []
    for model in models:
        booster = lgb.Booster(model_str=model["model_text"])
        values = booster.predict(test[list(config.selected_feature_columns)], num_threads=2)
        predictions.append(values)
        seeds.append({
            "seed": model["seed"], "model_digest": model["model_digest"],
            "metrics": _model_metrics(test.assign(prediction=values), "prediction",
                                      config.top_fraction, config.round_trip_cost_bps),
            "dates": date_outcomes(test, values, config.top_fraction),
        })
    ensemble = np.mean(np.vstack(predictions), axis=0)
    metrics = _model_metrics(test.assign(prediction=ensemble), "prediction",
                             config.top_fraction, config.round_trip_cost_bps)
    if metrics != expected_metrics:
        raise RuntimeError("reconstructed ensemble metrics differ from comparator")
    return {"seeds": seeds, "ensemble_dates": date_outcomes(test, ensemble, config.top_fraction)}


def _paired_dates(reference: list[dict], challenger: list[dict]) -> dict:
    dates = [row["signal_date"] for row in reference]
    if dates != sorted(set(dates)) or dates != [row["signal_date"] for row in challenger]:
        raise ValueError("paired date identities must match and be unique and chronological")
    if len(dates) < 2:
        raise ValueError("paired diagnostics require at least two dates")
    output = {"test_dates": len(dates), "date_deltas": []}
    for left, right in zip(reference, challenger):
        if (left["sample_rows"], left["top_count"]) != (right["sample_rows"], right["top_count"]):
            raise ValueError("paired cohorts and bucket sizes must match")
        output["date_deltas"].append({"signal_date": left["signal_date"], **{
            metric: None if left[metric] is None or right[metric] is None
            else right[metric] - left[metric]
            for metric in ("gross_top_bucket_excess_return_pct", "rank_ic")
        }})
    for metric in ("gross_top_bucket_excess_return_pct", "rank_ic"):
        deltas = output["date_deltas"]
        def summary(rows):
            values = [row[metric] for row in rows if row[metric] is not None]
            return {"observed_pairs": len(values), "mean_delta": float(np.mean(values)) if values else None,
                    "positive_fraction": sum(value > 0 for value in values) / len(values) if values else None}
        midpoint = len(dates) // 2
        leaveouts = [summary(deltas[:i] + deltas[i + 1:])["mean_delta"] for i in range(len(dates))]
        valid = [value for value in leaveouts if value is not None]
        output[metric] = {
            "all_dates": summary(deltas),
            "chronological_halves": [
                {"start": block[0]["signal_date"], "end": block[-1]["signal_date"], **summary(block)}
                for block in (deltas[:midpoint], deltas[midpoint:])
            ],
            "leave_one_date_out_mean_range": [min(valid), max(valid)] if valid else None,
        }
    return output


def paired_stability(results: list[dict]) -> dict:
    reference = results[0]
    if reference["variant"] != "full_features":
        raise ValueError("full_features must be the paired reference")
    comparisons = []
    for result in results[1:]:
        left, right = reference["stability"], result["stability"]
        if [row["seed"] for row in left["seeds"]] != [row["seed"] for row in right["seeds"]]:
            raise ValueError("paired seed identities must match")
        comparisons.append({
            "variant": result["variant"],
            "ensemble": _paired_dates(left["ensemble_dates"], right["ensemble_dates"]),
            "seeds": [{"seed": a["seed"], **_paired_dates(a["dates"], b["dates"])}
                      for a, b in zip(left["seeds"], right["seeds"])],
        })
    return {
        "reference": "full_features", "comparisons": comparisons,
        "interpretation": "Previously exposed retrospective test. Overlapping horizon labels and shared "
            "training data mean dates and seeds are not independent replicates. Halves and leave-one-date-out "
            "ranges describe sensitivity, not confidence intervals, significance, or future validation.",
        "cost_policy": "Paired date diagnostics use gross excess only. Per-seed aggregate metrics retain "
            "the comparator average-turnover heuristic; no per-date net return is invented.",
    }
