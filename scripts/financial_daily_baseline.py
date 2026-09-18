#!/usr/bin/env python3
"""Daily frozen ranks for Financial matched controls, separate from G2 sampling.

The caller owns the combined run timeout and serializes collection with its
existing Financial lock. No labels, database, training or production rank input.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from qagent.market.calendars import trading_sessions_in_range
from qagent.research.g2_forward_source import atomic_archive, digest
from qagent.research.g2_risk_feature_forward import (
    MANIFEST_DIGEST, load_frozen, prepare_features, source_rows, timestamp,
)

PROTOCOL = "financial-daily-frozen-rank-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_INPUT_FILES = 2000
POLICY = {
    "protocol": PROTOCOL,
    "consumer": "financial_matched_control_tie_break_only",
    "model_variant": "full_features",
    "seeds": [7, 19, 42],
    "source_selection": "first_valid_complete_cohort_by_captured_at_then_filename",
    "ranking": "mean_three_frozen_scores_descending_then_instrument_id",
    "timing": "same_exchange_day_after_1530_Shanghai_no_backfill",
    "decision_weight": False,
    "activation_allowed": False,
}


def _load(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("unsafe_or_oversized_baseline_input")
    raw = path.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("oversized_baseline_input")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("baseline_object_required")
    return value, sha256(raw).hexdigest()


def _ids(values):
    values = list(values)
    if (len(values) < 5 or len(set(values)) != len(values)
            or any(not isinstance(v, str) or not re.fullmatch(r"CN:[0-9]{6}", v) for v in values)):
        raise ValueError("invalid_financial_eligible_ids")
    return sorted(values)


def _local(value, day):
    parsed = timestamp(value).astimezone(SHANGHAI)
    if parsed.date() != day or parsed.time() < time(15, 30):
        raise ValueError("baseline_not_same_day_after_close")
    return parsed


def _manifest(manifest):
    unsigned = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    if digest(unsigned) != MANIFEST_DIGEST or manifest.get("manifest_sha256") != MANIFEST_DIGEST:
        raise ValueError("baseline_frozen_manifest_mismatch")


def _source(source, manifest, day, now, eligible_ids):
    if source.get("signal_date") != str(day):
        raise ValueError("baseline_source_date_mismatch")
    start = _local(source["capture_started_at_utc"], day)
    end = _local(source["captured_at_utc"], day)
    if not timestamp(manifest["frozen_at_utc"]) < start <= end <= now:
        raise ValueError("baseline_capture_time_order")
    rows, coverage = source_rows(source)
    if not set(eligible_ids) <= {r["instrument_id"] for r in rows}:
        raise ValueError("baseline_cohort_incomplete")
    return rows, coverage


def validate_archive(value, *, signal_date=None, eligible_ids=None):
    """Replay integrity without loading models or requiring today's code identity."""
    if digest({k: v for k, v in value.items() if k != "result_digest"}) != value.get("result_digest"):
        raise ValueError("baseline_digest_mismatch")
    if (value.get("protocol") != PROTOCOL or value.get("status") != "available"
            or value.get("policy") != POLICY or value.get("policy_digest") != digest(POLICY)
            or value.get("decision_weight") is not False or value.get("activation_allowed") is not False):
        raise ValueError("baseline_protocol_mismatch")
    day = date.fromisoformat(value["signal_date"])
    if str(day) != value["signal_date"] or not trading_sessions_in_range(day, day):
        raise ValueError("baseline_not_exchange_session")
    ids = _ids(value["eligible_ids"])
    if ids != value["eligible_ids"] or (eligible_ids is not None and ids != _ids(eligible_ids)):
        raise ValueError("baseline_eligible_cohort_mismatch")
    if signal_date is not None and str(signal_date) != str(day):
        raise ValueError("baseline_date_mismatch")
    start = _local(value["collection_started_at_utc"], day)
    end = _local(value["collected_at_utc"], day)
    if start > end:
        raise ValueError("baseline_collection_time_order")
    manifest = value["frozen_manifest"]
    _manifest(manifest)
    source = value["source"]
    rows, coverage = _source(source, manifest, day, start, ids)
    if (value.get("source_digest") != source["source_digest"]
            or value.get("coverage") != coverage
            or value.get("models") != manifest["variants"]["full_features"]["models"]
            or value.get("scorer") != manifest["variants"]["full_features"]["scorer_identity"]
            or value.get("preprocessing_module_sha256") != manifest["source_sha256"]["research/factor_ablation.py"]):
        raise ValueError("baseline_provenance_mismatch")
    for key in ("source_file_sha256", "collector_sha256"):
        if not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise ValueError("baseline_identity_malformed")
    predictions = value["predictions"]
    if [r["instrument_id"] for r in predictions] != [r["instrument_id"] for r in rows]:
        raise ValueError("baseline_prediction_cohort_mismatch")
    for row in predictions:
        score, rank = row["full_features"]["score"], row["full_features"]["rank"]
        if type(score) not in (int, float) or not math.isfinite(score) or type(rank) is not int:
            raise ValueError("baseline_invalid_score_or_rank")
    ordered = sorted(predictions, key=lambda r: (-r["full_features"]["score"], r["instrument_id"]))
    if [r["full_features"]["rank"] for r in ordered] != list(range(1, len(rows) + 1)):
        raise ValueError("baseline_rank_order_mismatch")
    return predictions


def collect(source_dir, frozen_dir, output_dir, *, signal_date, eligible_ids, now=None):
    """Return immutable daily rank evidence, or None while no valid source exists."""
    current = now or datetime.now(timezone.utc)
    day = date.fromisoformat(str(signal_date))
    _local(current.isoformat(), day)
    if not trading_sessions_in_range(day, day):
        raise ValueError("baseline_not_exchange_session")
    ids = _ids(eligible_ids)
    target = Path(output_dir) / f"{day}.json"
    if target.exists() or target.is_symlink():
        existing, _ = _load(target)
        validate_archive(existing, signal_date=day, eligible_ids=ids)
        if timestamp(existing["collected_at_utc"]) > current:
            raise ValueError("baseline_future_archive")
        return existing
    source_dir, frozen_dir = Path(source_dir), Path(frozen_dir)
    if not source_dir.exists():
        return None
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise ValueError("unsafe_baseline_source_directory")
    files = sorted(source_dir.glob(f"{day}-*.json"))
    if len(files) > MAX_INPUT_FILES:
        raise ValueError("baseline_source_file_budget_exceeded")
    if not files:
        return None
    # Verify pinned model/scorer identities before producing any rank evidence.
    manifest, models = load_frozen(frozen_dir)
    preprocessing = Path(prepare_features.__code__.co_filename)
    preprocessing_hash = sha256(preprocessing.read_bytes()).hexdigest()
    if preprocessing_hash != manifest["source_sha256"]["research/factor_ablation.py"]:
        raise ValueError("baseline_preprocessing_changed")
    candidates = []
    for path in files:
        try:
            source, file_hash = _load(path)
            _source(source, manifest, day, current, ids)
            candidates.append((timestamp(source["captured_at_utc"]), path.name, path, file_hash))
        except (ValueError, KeyError, TypeError, OSError):
            continue
    if not candidates:
        return None
    _, _, path, expected_hash = min(candidates)
    source, file_hash = _load(path)
    if file_hash != expected_hash:
        raise ValueError("baseline_source_changed_during_selection")
    rows, coverage = _source(source, manifest, day, current, ids)
    import numpy as np
    import pandas as pd
    from qagent.research.g2_risk_feature_freeze import VARIANTS

    prepared = prepare_features(pd.DataFrame(rows), feature_stage="raw")
    matrix = prepared.loc[:, list(VARIANTS["full_features"])].astype("float64")
    scores = np.mean(np.vstack([model.predict(matrix) for model in models["full_features"]]), axis=0)
    if not np.isfinite(scores).all():
        raise ValueError("baseline_nonfinite_predictions")
    ranks = pd.Series(scores).rank(method="first", ascending=False).astype(int)
    result = {
        "protocol": PROTOCOL, "status": "available", "policy": POLICY, "policy_digest": digest(POLICY),
        "signal_date": str(day), "eligible_ids": ids,
        "collection_started_at_utc": current.astimezone(timezone.utc).isoformat(),
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source, "source_digest": source["source_digest"], "source_file_sha256": file_hash,
        "source_filename": path.name, "frozen_manifest": manifest,
        "models": manifest["variants"]["full_features"]["models"],
        "scorer": manifest["variants"]["full_features"]["scorer_identity"],
        "preprocessing_module_sha256": preprocessing_hash,
        "collector_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "coverage": coverage,
        "predictions": [{"instrument_id": row["instrument_id"], "full_features": {
            "score": float(scores[i]), "rank": int(ranks.iloc[i])}} for i, row in enumerate(rows)],
        "decision_weight": False, "activation_allowed": False,
    }
    result["result_digest"] = digest(result)
    validate_archive(result, signal_date=day, eligible_ids=ids)
    if not atomic_archive(target, result):
        result, _ = _load(target)
        validate_archive(result, signal_date=day, eligible_ids=ids)
    return result
