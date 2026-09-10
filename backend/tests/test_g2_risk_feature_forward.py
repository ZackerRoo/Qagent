from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from qagent.factors.models import FactorRanking
from qagent.factors.research_contract import FEATURE_COLUMNS
from qagent.research import g2_forward_source as capture
from qagent.research import g2_risk_feature_forward as forward
from qagent.research.factor_ablation import prepare_features

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "data/archives/g2-risk-feature-ablation-20260910-frozen-v1"


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)


def ranking(index):
    values = {key: (index + 1) ** (1 + position % 3) for position, key in enumerate(FEATURE_COLUMNS)}
    return FactorRanking(
        instrument_id=f"CN:{index:06d}", factor_score=.5, factor_rank=index + 1,
        percentile=.5, momentum_score=.5, trend_quality_score=.5, liquidity_score=.5,
        low_risk_score=.5, reversal_score=.5, execution_penalty=0, data_completeness=1,
        factor_exposures=[], research_features=values,
    )


def make_source(tmp_path):
    rankings = [ranking(index) for index in range(7)]
    source = {
        "protocol": capture.SOURCE_PROTOCOL, "stage": "ranking_finalized_before_job_completion",
        "provider": "free", "scan_job_id": "synthetic-not-operational-evidence", "signal_date": "2026-09-11",
        "capture_started_at_utc": "2026-09-11T07:40:00+00:00", "captured_at_utc": "2026-09-11T07:41:00+00:00",
        "rankings": [item.model_dump(mode="json") for item in rankings],
        "research_universe": sorted(item.instrument_id for item in rankings),
        "stock_ids": sorted(item.instrument_id for item in rankings),
        "items": [{"instrument_id": item.instrument_id, "latest_trade_date": "2026-09-11"} for item in rankings],
        "industries": {}, "revision": {"revision": 3, "updated_at": "2026-09-11 07:30:00"},
        "source_sha256": {name: "0" * 64 for name in (
            "research/g2_forward_source.py", "jobs/full_market.py", "jobs/daily_scan.py", "factors/engine.py")},
        "decision_weight": False, "activation_allowed": False,
    }
    return write_source(tmp_path, source)


def write_source(tmp_path, source):
    source.pop("source_digest", None)
    source["source_digest"] = capture.digest(source)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source))
    return path


@pytest.fixture
def frozen_models():
    if not FROZEN.exists():
        pytest.skip("local frozen G2 models not distributed in Git")
    return forward.load_frozen(FROZEN)


def test_real_models_exact_paired_preprocessing_and_idempotency(tmp_path, monkeypatch, frozen_models):
    monkeypatch.setattr(forward, "datetime", Clock)
    path = make_source(tmp_path)
    result = forward.collect(path, FROZEN, tmp_path / "output")
    assert result["status"] == "ready"
    rows, coverage = forward.source_rows(json.loads(path.read_text()))
    prepared = prepare_features(pd.DataFrame(rows), feature_stage="raw")
    for name, features in forward.VARIANTS.items():
        actual = [row[name]["score"] for row in result["predictions"]]
        expected = np.mean([model.predict(prepared[list(features)].astype("float64"))
                            for model in frozen_models[1][name]], axis=0)
        np.testing.assert_array_equal(actual, expected)
    assert coverage["scored_rows"] == 7
    target = tmp_path / "output/signals/2026-09-11.json"
    before = target.read_bytes()
    assert forward.collect(path, FROZEN, tmp_path / "output") == result
    assert target.read_bytes() == before
    assert target.stat().st_mode & 0o222 == 0


@pytest.mark.parametrize("change,reason", [
    ({"signal_date": "2026-09-10"}, "outside_frozen_10_session_sampling_schedule"),
    ({"capture_started_at_utc": "2026-09-11T06:00:00+00:00"}, "capture_before_completed_market_session"),
    ({"captured_at_utc": "2026-09-12T08:00:00+00:00"}, "capture_not_after_freeze_or_future_timestamp"),
])
def test_invalid_time_is_diagnostic_only(tmp_path, monkeypatch, frozen_models, change, reason):
    monkeypatch.setattr(forward, "datetime", Clock)
    path = make_source(tmp_path)
    source = json.loads(path.read_text())
    source.update(change)
    write_source(tmp_path, source)
    result = forward.collect(path, FROZEN, tmp_path / "output")
    assert result["status"] == "not_ready" and reason in result["reasons"]
    assert not result["predictions"]
    assert not (tmp_path / "output/signals").exists()


def test_failed_attempt_does_not_reserve_signal_date(tmp_path, monkeypatch, frozen_models):
    monkeypatch.setattr(forward, "datetime", Clock)
    path = make_source(tmp_path)
    source = json.loads(path.read_text())
    for item in source["items"]:
        item["latest_trade_date"] = "2026-09-10"
    write_source(tmp_path, source)
    assert forward.collect(path, FROZEN, tmp_path / "output")["status"] == "not_ready"
    path = make_source(tmp_path)
    assert forward.collect(path, FROZEN, tmp_path / "output")["status"] == "ready"


def test_stale_rows_excluded_from_both_variants(tmp_path, monkeypatch, frozen_models):
    monkeypatch.setattr(forward, "datetime", Clock)
    path = make_source(tmp_path)
    source = json.loads(path.read_text())
    source["items"][0]["latest_trade_date"] = "2026-09-10"
    write_source(tmp_path, source)
    result = forward.collect(path, FROZEN, tmp_path / "output")
    assert result["status"] == "ready"
    assert result["coverage"]["stale_or_missing_trade_dates"] == ["CN:000000"]
    assert len(result["predictions"]) == 6
    assert all(row["instrument_id"] != "CN:000000" for row in result["predictions"])


def test_source_corruption_rejected(tmp_path):
    path = make_source(tmp_path)
    source = json.loads(path.read_text())
    source["signal_date"] = "2026-09-12"
    with pytest.raises(ValueError, match="digest"):
        forward.source_rows(source)


def test_model_corruption_rejected(tmp_path, frozen_models):
    manifest = frozen_models[0]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    for variant in manifest["variants"].values():
        for model in variant["models"]:
            (tmp_path / model["file"]).write_bytes((FROZEN / model["file"]).read_bytes())
    (tmp_path / manifest["variants"]["full_features"]["models"][0]["file"]).write_text("changed")
    with pytest.raises(ValueError, match="model hash"):
        forward.load_frozen(tmp_path)


def test_readonly_snapshot_filters_future_industries_and_preserves_db(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE historical_data_revisions(provider_mode TEXT,revision INTEGER,updated_at TEXT);
        INSERT INTO historical_data_revisions VALUES('free',3,'2026-09-11 07:30:00');
        CREATE TABLE historical_industry_snapshots(provider_mode TEXT,instrument_id TEXT,
          snapshot_date TEXT,dataset_revision INTEGER,source_provider TEXT,industry TEXT,fetched_at TEXT);
        INSERT INTO historical_industry_snapshots VALUES('free','CN:000001','2026-09-11',2,'a','old','2026-09-11 07:00:00');
        INSERT INTO historical_industry_snapshots VALUES('free','CN:000001','2026-09-11',3,'a','future','2026-09-11 09:00:00');
        """)
    before = sha256(path.read_bytes()).hexdigest()
    result = capture.read_industries(path, "free", "2026-09-11", Clock.now())
    assert result["industries"]["CN:000001"]["industry"] == "old"
    assert sha256(path.read_bytes()).hexdigest() == before
    with pytest.raises(sqlite3.OperationalError):
        capture.read_industries(tmp_path / "absent.db", "free", "2026-09-11", Clock.now())
    assert not (tmp_path / "absent.db").exists()


def test_disabled_capture_and_failure_are_nonblocking(tmp_path, monkeypatch):
    monkeypatch.delenv("QAGENT_G2_CAPTURE_DIR", raising=False)
    monkeypatch.setattr(capture, "capture_source", lambda **kw: pytest.fail("must not capture"))
    assert capture.capture_if_enabled() is None
    monkeypatch.setenv("QAGENT_G2_CAPTURE_DIR", str(tmp_path))
    def fail(**kw):
        raise RuntimeError("do not log raw sensitive exception")
    monkeypatch.setattr(capture, "capture_source", fail)
    assert capture.capture_if_enabled() is None
