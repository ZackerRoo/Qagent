"""Collect paired frozen G2 predictions from an opt-in, contemporaneous source capture."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from qagent.factors.models import FactorRanking
from qagent.factors.research_contract import FEATURE_COLUMNS
from qagent.market.calendars import trading_sessions_in_range
from qagent.research.factor_ablation import prepare_features
from qagent.research.factor_shadow import _finite_or_none, _log_market_cap, factor_shadow_scorer_identity
from qagent.research.g2_forward_source import SOURCE_PROTOCOL, atomic_archive, digest
from qagent.research.g2_risk_feature_freeze import VARIANTS, validate_frozen_config

MANIFEST_DIGEST = "b6bbb9a44dbb61fb4ec8879034ef6232e2cd32f07356a1407ffad71c69c9e77b"
START = date(2026, 9, 11)
END = date(2026, 12, 31)


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("source timestamps require explicit timezone")
    return result.astimezone(timezone.utc)


def load_frozen(directory: Path) -> tuple[dict, dict]:
    import lightgbm as lgb

    manifest = json.loads((directory / "manifest.json").read_text())
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if digest(unsigned) != MANIFEST_DIGEST or manifest["manifest_sha256"] != MANIFEST_DIGEST:
        raise ValueError("frozen manifest hash mismatch")
    validate_frozen_config(manifest["config"])
    if digest(manifest["config"]) != manifest["config_sha256"]:
        raise ValueError("frozen config hash mismatch")
    models = {}
    for variant, features in VARIANTS.items():
        entry = manifest["variants"][variant]
        if factor_shadow_scorer_identity(features).model_dump() != entry["scorer_identity"]:
            raise ValueError("frozen scorer implementation changed")
        models[variant] = []
        if [record["seed"] for record in entry["models"]] != [7, 19, 42]:
            raise ValueError("frozen seed set mismatch")
        for record in entry["models"]:
            raw = (directory / record["file"]).read_bytes()
            if sha256(raw).hexdigest() != record["model_digest"]:
                raise ValueError("frozen model hash mismatch")
            model = lgb.Booster(model_str=raw.decode())
            if model.feature_name() != list(features):
                raise ValueError("frozen model feature order mismatch")
            models[variant].append(model)
    return manifest, models


def source_rows(source: dict) -> tuple[list[dict], dict]:
    if source.get("protocol") != SOURCE_PROTOCOL or source.get("stage") != "ranking_finalized_before_job_completion":
        raise ValueError("unsupported source contract")
    if source.get("provider") != "free":
        raise ValueError("source provider differs from frozen protocol")
    if source.get("decision_weight") is not False or source.get("activation_allowed") is not False:
        raise ValueError("source isolation flags changed")
    unsigned = {key: value for key, value in source.items() if key != "source_digest"}
    if digest(unsigned) != source.get("source_digest"):
        raise ValueError("source digest mismatch")
    required_sources = {"research/g2_forward_source.py", "jobs/full_market.py", "jobs/daily_scan.py", "factors/engine.py"}
    hashes = source.get("source_sha256", {})
    if set(hashes) != required_sources or any(
        not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
        for value in hashes.values()
    ):
        raise ValueError("source implementation hashes missing or malformed")
    if not isinstance(source.get("revision", {}).get("revision"), int) or source["revision"]["revision"] <= 0:
        raise ValueError("source dataset revision missing")
    cutoff = timestamp(source["capture_started_at_utc"])
    for industry in source["industries"].values():
        fetched = datetime.fromisoformat(industry["fetched_at"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if (fetched > cutoff or industry["snapshot_date"] > source["signal_date"]
                or industry["dataset_revision"] > source["revision"]["revision"]):
            raise ValueError("industry source is not point in time")
    rankings = [FactorRanking.model_validate(item) for item in source["rankings"]]
    identities = [item.instrument_id for item in rankings]
    if len(identities) != len(set(identities)) or sorted(identities) != source["research_universe"]:
        raise ValueError("source cohort mismatch")
    if not set(identities).issubset(source["stock_ids"]):
        raise ValueError("source includes non-stock identities")
    rows, excluded, stale = [], [], []
    item_dates = {}
    for item in source["items"]:
        item_dates.setdefault(item["instrument_id"], set()).add(item.get("latest_trade_date"))
    for ranking in rankings:
        key = ranking.instrument_id
        features = {feature: _finite_or_none(ranking.research_features.get(feature)) for feature in FEATURE_COLUMNS}
        if item_dates.get(key) != {source["signal_date"]}:
            stale.append(key)
            continue
        if not any(value is not None for value in features.values()):
            excluded.append(key)
            continue
        industry = source["industries"].get(key, {}).get("industry")
        rows.append({"instrument_id": key, "signal_date": source["signal_date"],
                     "industry": industry, "log_market_cap": _log_market_cap(ranking), **features})
    # Stable identity ordering also fixes tie handling across retries.
    rows.sort(key=lambda row: row["instrument_id"])
    coverage = {
        "source_rows": len(rankings), "scored_rows": len(rows), "excluded_all_features_missing": excluded,
        "eligible_fraction": len(rows) / len(rankings) if rankings else 0.0,
        "stale_or_missing_trade_dates": stale,
        "feature_nonmissing": {feature: sum(row[feature] is not None for row in rows) for feature in FEATURE_COLUMNS},
        "industry_nonmissing": sum(row["industry"] is not None for row in rows),
        "size_nonmissing": sum(row["log_market_cap"] is not None for row in rows),
        "variant_joint_complete": {name: sum(all(row[f] is not None for f in features) for row in rows)
                                   for name, features in VARIANTS.items()},
    }
    return rows, coverage


def collect(source_path: Path, frozen_dir: Path, output: Path) -> dict:
    started = datetime.now(timezone.utc)
    manifest, models = load_frozen(frozen_dir)
    raw = source_path.read_bytes()
    source = json.loads(raw)
    rows, coverage = source_rows(source)
    day = date.fromisoformat(source["signal_date"])
    captured = timestamp(source["captured_at_utc"])
    capture_started = timestamp(source["capture_started_at_utc"])
    frozen = timestamp(manifest["frozen_at_utc"])
    local = started.astimezone(ZoneInfo("Asia/Shanghai"))
    capture_local = capture_started.astimezone(ZoneInfo("Asia/Shanghai"))
    sessions = trading_sessions_in_range(START, END)
    reasons = []
    if day not in sessions[::10]:
        reasons.append("outside_frozen_10_session_sampling_schedule")
    if not frozen < capture_started <= captured <= started:
        reasons.append("capture_not_after_freeze_or_future_timestamp")
    if day != local.date() or day != capture_local.date():
        reasons.append("not_contemporaneous_signal_date")
    if capture_local.time() < time(15, 30):
        reasons.append("capture_before_completed_market_session")
    if len(rows) < 5:
        reasons.append("fewer_than_five_feature_rows")
    result = {
        "protocol": "g2-risk-feature-forward-v1", "status": "not_ready" if reasons else "ready",
        "reasons": reasons, "signal_date": str(day), "scan_job_id": source["scan_job_id"],
        "collection_started_at_utc": started.isoformat(), "source_digest": source["source_digest"],
        "source_file_sha256": sha256(raw).hexdigest(), "source": source,
        "frozen_manifest_sha256": MANIFEST_DIGEST, "config_sha256": manifest["config_sha256"],
        "models": {name: entry["models"] for name, entry in manifest["variants"].items()},
        "scorers": {name: entry["scorer_identity"] for name, entry in manifest["variants"].items()},
        "collector_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "preprocessing_module_sha256": sha256((Path(__file__).parent / "factor_ablation.py").read_bytes()).hexdigest(),
        "coverage": coverage, "predictions": [], "decision_weight": False, "activation_allowed": False,
        "forward_metrics": None, "label_status": "not_collected",
    }
    if not reasons:
        prepared = prepare_features(pd.DataFrame(rows), feature_stage="raw")
        paired = [{"instrument_id": row["instrument_id"], "industry": row["industry"]} for row in rows]
        for name, features in VARIANTS.items():
            matrix = prepared.loc[:, list(features)].astype("float64")
            predictions = np.mean(np.vstack([model.predict(matrix) for model in models[name]]), axis=0)
            if not np.isfinite(predictions).all():
                raise ValueError("nonfinite model predictions")
            ranks = pd.Series(predictions).rank(method="first", ascending=False).astype(int)
            for index, prediction in enumerate(predictions):
                paired[index][name] = {"score": float(prediction), "rank": int(ranks.iloc[index]),
                    "feature_coverage": sum(rows[index][f] is not None for f in features) / len(features)}
        result["predictions"] = paired
    completed = datetime.now(timezone.utc)
    if not reasons and completed.astimezone(ZoneInfo("Asia/Shanghai")).date() != day:
        result.update(status="not_ready", reasons=["collection_crossed_signal_date"], predictions=[])
    result["collected_at_utc"] = completed.isoformat()
    result["result_digest"] = digest(result)
    # First successful collection per signal date wins; failed attempts do not occupy that slot.
    if result["status"] == "ready":
        target = output / "signals" / f"{day}.json"
    else:
        target = output / "diagnostics" / f"{day}-{source['source_digest']}.json"
    if not atomic_archive(target, result):
        existing = json.loads(target.read_text())
        unsigned = {key: value for key, value in existing.items() if key != "result_digest"}
        if digest(unsigned) != existing.get("result_digest"):
            raise ValueError("existing archive digest mismatch")
        return existing
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--frozen-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.source, args.frozen_dir, args.output)
    print(json.dumps({key: result[key] for key in ("status", "signal_date", "reasons", "result_digest")}))
    if result["status"] != "ready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
