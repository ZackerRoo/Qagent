from copy import deepcopy
from datetime import date, datetime
import importlib
import json
from pathlib import Path
import socket
import sqlite3
import sys

import pandas as pd
import pytest
from sqlalchemy import create_engine

from qagent.storage.tables import MarketBarCacheRow

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
forward = importlib.import_module("evaluate_g2_forward")
sys.path.pop(0)


@pytest.fixture
def manifest():
    return json.loads(forward.DEFAULT_MANIFEST.read_text())


def sign(value):
    value.pop("result_digest", None)
    value["result_digest"] = forward.digest(value)
    return value


def signal(manifest, day="2026-09-11"):
    rows = [{"instrument_id": f"CN:{600001+i}", "industry": "A" if i < 5 else None,
             "full_features": {"score": 10-i, "rank": i+1},
             "without_risk": {"score": i, "rank": 10-i}} for i in range(10)]
    return sign({"protocol": "g2-risk-feature-forward-v1", "status": "ready", "reasons": [],
                 "decision_weight": False, "activation_allowed": False, "signal_date": day,
                 "coverage": {"scored_rows": 10}, "predictions": rows,
                 "collection_started_at_utc": day + "T16:00:00+08:00",
                 "collected_at_utc": day + "T16:01:00+08:00",
                 "frozen_manifest_sha256": forward.MANIFEST_DIGEST,
                 "config_sha256": manifest["config_sha256"],
                 "models": {v: manifest["variants"][v]["models"] for v in forward.VARIANTS},
                 "scorers": {v: manifest["variants"][v]["scorer_identity"] for v in forward.VARIANTS}})


def database(tmp_path, signals, *, missing=None, unsafe=None):
    path = tmp_path / "cache.db"
    engine = create_engine("sqlite:///" + str(path))
    MarketBarCacheRow.__table__.create(engine)
    rows = []
    for s in signals:
        day = date.fromisoformat(s["signal_date"])
        end = forward.trading_day_offset(day, 20)
        for when in (day, end):
            for i, key in enumerate([r["instrument_id"] for r in s["predictions"]] + ["CN:000300.IDX"]):
                if key == missing and when == end:
                    continue
                close = 100 if when == day else 102 if key.endswith("IDX") else 105+i
                rows.append({"provider_mode": "free", "instrument_id": key, "trade_date": when,
                             "source_provider": "fixture", "open": 100, "high": max(100, close),
                             "low": 100, "close": close, "volume": 1,
                             "adjusted_open": 100, "adjusted_close": close,
                             "adjusted_high": max(100, close), "adjusted_low": 100,
                             "adjustment_factor": 1,
                             "adjustment_type": "snapshot_qfq_anchor" if key == unsafe else "qfq"})
    with engine.begin() as conn:
        conn.execute(MarketBarCacheRow.__table__.insert(), rows)
    engine.dispose()
    return path


@pytest.mark.parametrize("clock", ["2026-09-24T16:00:00+08:00", "2026-10-19T15:29:59+08:00"])
def test_waiting_never_opens_database(manifest, monkeypatch, tmp_path, clock):
    def forbidden(*args, **kwargs):
        pytest.fail("database/network access before maturity")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = forward.evaluate([signal(manifest)], tmp_path / "absent.db", manifest,
                              as_of=datetime.fromisoformat(clock))
    assert result["status"] == "waiting_for_maturity"
    assert result["windows"][0]["outcome_date"] == "2026-10-19"
    assert result["metrics"] is None


@pytest.mark.parametrize("kind", ["missing", "unsafe"])
def test_incomplete_never_reselects_or_compares(manifest, tmp_path, kind):
    s = signal(manifest)
    path = database(tmp_path, [s], **{kind: "CN:600001"})
    result = forward.evaluate([s], path, manifest, as_of=datetime.fromisoformat("2026-10-19T16:00:00+08:00"))
    w = result["windows"][0]
    assert result["status"] == "partial" and result["metrics"] is None
    assert w["expected"] == 10 and w["completed"] == 9
    assert len(w["labels"]) == 10 and "selections" not in w
    assert w["labels"][0]["reasons"]


def test_full_cohort_exact_comparator_and_readonly(manifest, tmp_path, monkeypatch):
    s = signal(manifest)
    path = database(tmp_path, [s])
    original = deepcopy(s)
    before = path.read_bytes()
    connect = sqlite3.connect
    def checked(database_uri, **kwargs):
        assert database_uri.endswith("?mode=ro") and kwargs["uri"] is True
        conn = connect(database_uri, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE forbidden (id int)")
        return conn
    monkeypatch.setattr(sqlite3, "connect", checked)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network access"))
    result = forward.evaluate([s], path, manifest, as_of=datetime.fromisoformat("2026-10-19T15:30:00+08:00"))
    assert result["status"] == "complete"
    frame = forward.comparator._attach_excess_labels(pd.DataFrame([
        {"signal_date": "2026-09-11", "instrument_id": r["instrument_id"], "industry": r["industry"],
         "raw_forward_return_pct": ((105+i)/100-1)*100, "benchmark_return_pct": 2.0000000000000018,
         **{v: r[v]["score"] for v in forward.VARIANTS}}
        for i, r in enumerate(s["predictions"])]))
    for v in forward.VARIANTS:
        assert result["metrics"][v] == forward.comparator._model_metrics(frame, v, .1, 10)
        assert result["metrics"][v]["net_top_bucket_excess_return_pct"] is None
    assert result["windows"][0]["labels"][0]["label_scope"] == "industry_excess"
    assert result["windows"][0]["labels"][5]["label_scope"] == "benchmark_excess"
    assert result["net_difference_pct"] is None and original == s and before == path.read_bytes()


@pytest.mark.parametrize("kind", ["digest", "identity", "manifest", "future", "duplicate"])
def test_invalid_input_rejected_before_db(manifest, tmp_path, kind):
    s = signal(manifest)
    signals = [s]
    if kind == "digest":
        s["predictions"][0]["industry"] = "tampered"
    elif kind == "identity":
        s["config_sha256"] = "0" * 64
        sign(s)
    elif kind == "manifest":
        manifest["config"]["training_config"]["top_fraction"] = .2
    elif kind == "future":
        s["collected_at_utc"] = "2027-01-01T16:00:00+08:00"
        sign(s)
    else:
        signals.append(s)
    with pytest.raises(ValueError):
        forward.evaluate(signals, tmp_path / "absent.db", manifest,
                         as_of=datetime.fromisoformat("2026-09-24T16:00:00+08:00"))


def test_waiting_signal_suppresses_aggregate_and_publish_exclusive(manifest, tmp_path):
    days = forward.trading_sessions_in_range(date(2026, 9, 11), date(2026, 12, 31))
    signals = [signal(manifest), signal(manifest, str(days[10]))]
    db = database(tmp_path, signals[:1])
    result = forward.evaluate(signals, db, manifest, as_of=datetime.fromisoformat("2026-10-19T16:00:00+08:00"))
    assert result["metrics"] is None
    assert [w["status"] for w in result["windows"]] == ["complete", "waiting_for_maturity"]
    target = tmp_path / "result.json"
    forward.publish(target, json.dumps(result, allow_nan=False))
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        forward.publish(target, "replacement")
    assert before == target.read_bytes()


def test_multiple_mature_dates_turnover_and_tie_order(manifest, tmp_path):
    days = forward.trading_sessions_in_range(date(2026, 9, 11), date(2026, 12, 31))
    first, second = signal(manifest), signal(manifest, str(days[10]))
    for i, row in enumerate(second["predictions"]):
        row["without_risk"] = {"score": 10 if i < 2 else 10-i, "rank": i+1}
    second["predictions"].reverse()
    sign(second)
    db = database(tmp_path, [first, second])
    result = forward.evaluate([second, first], db, manifest,
                              as_of=datetime.fromisoformat("2026-11-30T16:00:00+08:00"))
    assert result["status"] == "complete"
    assert result["windows"][1]["selections"]["without_risk"]["instrument_ids"] == ["CN:600001"]
    metrics = result["metrics"]
    assert metrics["full_features"]["average_turnover_rate"] == 0
    assert metrics["without_risk"]["average_turnover_rate"] == 1
    assert metrics["without_risk"]["estimated_cost_drag_pct"] == .1
    assert result["net_difference_pct"] == (
        metrics["without_risk"]["net_top_bucket_excess_return_pct"]
        - metrics["full_features"]["net_top_bucket_excess_return_pct"])
