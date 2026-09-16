from copy import deepcopy
from datetime import date, datetime
import fcntl
import importlib.util
import json
from pathlib import Path
import stat
import sys
import time

import pytest
from sqlalchemy import create_engine

from qagent.storage.tables import MarketBarCacheRow

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import collect_daily_documented_research as batch  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "financial_forward_runner", SCRIPTS / "run_financial_forward_research.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
sys.path.pop(0)

SYMBOLS = ["600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH", "600006.SH"]
CLOSE = datetime.fromisoformat("2026-09-11T16:40:00+08:00")
RUN = datetime.fromisoformat("2026-09-11T19:30:00+08:00")


def _daily(monkeypatch, symbols=SYMBOLS):
    monkeypatch.setattr(batch, "now", lambda: CLOSE.isoformat())

    def query(base_url, **request):
        symbol = request["params"]["ts_code"]
        row = {"ts_code": symbol, "end_date": "20260630", "ann_date": "20260801",
               "report_type": "1", "comp_type": "1", "n_cashflow_act": 30,
               "n_income": 10, "total_revenue": 100, "trade_date": "20260911",
               "pe": 10, "total_assets": 1000, "total_liab": 300, "roe": 10,
               "netprofit_margin": 20, "type": "预增", "p_change_min": 1,
               "p_change_max": 2}
        return {"source": "datahubco", "status": "observed", "rows": [row],
                "decision_weight": False, "activation_allowed": False}

    return batch.run_batch(symbols, "20260630", "20260911", query=query)


def _db(path, *, mature=False, missing=False):
    engine = create_engine("sqlite:///" + str(path))
    MarketBarCacheRow.__table__.create(engine)
    if mature:
        rows = []
        for day in (date(2026, 9, 14), date(2026, 9, 18)):
            for index, key in enumerate(["CN:" + item[:6] for item in SYMBOLS]
                                        + ["CN:000300.IDX"]):
                if missing and day.day == 18 and key == "CN:600001":
                    continue
                close = 100 if day.day == 14 else 102 if key.endswith("IDX") else 110 + index
                rows.append({"provider_mode": "free", "instrument_id": key,
                             "trade_date": day, "source_provider": "fixture", "open": 100,
                             "high": max(100, close), "low": 100, "close": close, "volume": 1,
                             "adjusted_open": 100, "adjusted_close": close,
                             "adjusted_high": max(100, close), "adjusted_low": 100,
                             "adjustment_factor": 1, "adjustment_type": "qfq"})
        with engine.begin() as connection:
            connection.execute(MarketBarCacheRow.__table__.insert(), rows)
    engine.dispose()
    return path


@pytest.fixture
def paths(tmp_path):
    values = [tmp_path / name for name in ("daily", "signals", "evaluations", "runs")]
    values[0].mkdir()
    return (*values, _db(tmp_path / "qagent.db"))


def test_first_legal_daily_is_sealed_and_existing_signal_is_idempotent(paths, monkeypatch):
    daily_dir, signal_dir, evaluations, runs, db = paths
    document = _daily(monkeypatch)
    (daily_dir / "a-broken.json").write_text("{")
    (daily_dir / "b-first.json").write_text(json.dumps(document))
    (daily_dir / "c-second.json").write_text(json.dumps(deepcopy(document)))

    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=RUN)
    assert code == 0 and report["seal"]["status"] == "sealed"
    assert report["seal"]["daily"].endswith("b-first.json")
    target = signal_dir / "2026-09-11.json"
    before = target.read_bytes()
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=RUN)
    assert code == 0 and report["seal"]["status"] == "already_sealed"
    assert target.read_bytes() == before
    assert len(list(runs.glob("*.json"))) == 2


def test_same_day_dynamic_candidate_pool_daily_is_sealed(paths, monkeypatch):
    daily_dir, signal_dir, evaluations, runs, db = paths
    document = _daily(monkeypatch)
    items = [{"instrument_id": "CN:159146", "asset_type": "etf",
              "signal_date": "2026-09-11", "signal_date_fresh": True}]
    items.extend({"instrument_id": "CN:" + symbol[:6], "asset_type": "stock",
                  "industry": "test", "exposure_group": "test",
                  "signal_date": "2026-09-11", "signal_date_fresh": True}
                 for symbol in SYMBOLS)
    payload = {
        "items": items, "summary": {"shown_candidates": 7, "total_candidates": 7},
        "data_health": {"paper_candidate_pool_endpoint": "true",
                        "paper_candidate_pool_limit": "100",
                        "paper_candidate_pool_total": "7",
                        "paper_candidate_freshness_gate": "fresh",
                        "paper_candidate_expected_signal_date": "2026-09-11",
                        "paper_candidate_signal_date_mismatch": "0"},
    }
    _, document["universe"] = batch.candidate_pool_universe(payload, "20260911")
    document.pop("result_digest")
    document["result_digest"] = batch.digest(document)
    (daily_dir / "dynamic.json").write_text(json.dumps(document))
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=RUN)
    assert code == 0 and report["seal"]["status"] == "sealed"
    signal = json.loads((signal_dir / "2026-09-11.json").read_text())
    assert signal["source"]["universe"]["kind"] == "paper_candidate_pool_order"
    runner.forward.validate_archive(signal)


def test_existing_signal_must_match_first_legal_daily(paths, monkeypatch):
    daily_dir, signal_dir, evaluations, runs, db = paths
    document = _daily(monkeypatch)
    changed = _daily(monkeypatch, list(reversed(SYMBOLS)))
    (daily_dir / "first.json").write_text(json.dumps(document))
    signal_dir.mkdir()
    signal = runner.forward.seal(changed, now=RUN)
    runner._publish(signal_dir / "2026-09-11.json", signal)
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=RUN)
    assert code == 1 and report["seal"]["status"] == "blocked"


def test_waiting_maturity_is_only_in_run_evidence(paths, monkeypatch):
    daily_dir, signal_dir, evaluations, runs, db = paths
    (daily_dir / "daily.json").write_text(json.dumps(_daily(monkeypatch)))
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=RUN)
    assert code == 0
    assert {h["archive_status"] for h in report["evaluations"][0]["horizons"]} == {
        "waiting_for_maturity"}
    assert not evaluations.exists()


def test_mature_horizon_archived_once_and_database_unchanged(paths, monkeypatch):
    daily_dir, signal_dir, evaluations, runs, db = paths
    document = _daily(monkeypatch)
    signal_dir.mkdir()
    runner._publish(signal_dir / "2026-09-11.json", runner.forward.seal(document, now=RUN))
    db.unlink()
    _db(db, mature=True)
    before_db = db.read_bytes()
    mature = datetime.fromisoformat("2026-09-18T19:30:00+08:00")
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=mature)
    assert code == 0
    horizon = evaluations / "2026-09-11/5.json"
    before = horizon.read_bytes()
    assert json.loads(before)["evaluation"]["status"] == "complete"
    assert json.loads(before)["runtime_identity"]["factor_shadow_outcomes_sha256"]
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=mature)
    assert code == 0
    assert report["evaluations"][0]["horizons"][0]["archive_status"] == "already_archived"
    assert horizon.read_bytes() == before and db.read_bytes() == before_db


def test_partial_stays_in_run_then_complete_prices_publish_final(paths, monkeypatch):
    daily_dir, signal_dir, evaluations, runs, db = paths
    signal_dir.mkdir()
    runner._publish(signal_dir / "2026-09-11.json",
                    runner.forward.seal(_daily(monkeypatch), now=RUN))
    db.unlink()
    _db(db, mature=True, missing=True)
    mature = datetime.fromisoformat("2026-09-18T19:30:00+08:00")
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=mature)
    five = report["evaluations"][0]["horizons"][0]
    assert code == 0 and five["status"] == "partial"
    assert five["archive_status"] == "retryable_partial"
    assert five["snapshot"]["labels"][0]["reasons"] == ["missing_price_row"]
    assert not (evaluations / "2026-09-11/5.json").exists()

    engine = create_engine("sqlite:///" + str(db))
    with engine.begin() as connection:
        connection.execute(MarketBarCacheRow.__table__.insert(), [{
            "provider_mode": "free", "instrument_id": "CN:600001",
            "trade_date": date(2026, 9, 18), "source_provider": "fixture",
            "open": 100, "high": 110, "low": 100, "close": 110, "volume": 1,
            "adjusted_open": 100, "adjusted_close": 110, "adjusted_high": 110,
            "adjusted_low": 100, "adjustment_factor": 1, "adjustment_type": "qfq"}])
    engine.dispose()
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=mature)
    assert code == 0
    assert report["evaluations"][0]["horizons"][0]["archive_status"] == "archived"
    assert json.loads((evaluations / "2026-09-11/5.json").read_text())["evaluation"]["status"] == "complete"


def test_invalid_daily_leaves_failure_run(paths):
    daily_dir, signal_dir, evaluations, runs, db = paths
    (daily_dir / "invalid.json").write_text(json.dumps({
        "status": "observed", "trade_date": "20260911"}))
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=RUN)
    assert code == 1 and report["seal"]["status"] == "blocked_no_legal_daily"
    archived = json.loads(next(runs.glob("*.json")).read_text())
    assert archived["status"] == "incomplete" and archived["errors"]


def test_missing_same_day_daily_is_retryable_and_audited(paths):
    daily_dir, signal_dir, evaluations, runs, db = paths
    code, report = runner.run(daily_dir, signal_dir, evaluations, runs, db, now=RUN)
    assert code == 75
    assert report["status"] == "waiting_for_daily"
    assert report["seal"]["status"] == "waiting_for_daily"
    archived = json.loads(next(runs.glob("*.json")).read_text())
    assert archived["status"] == "waiting_for_daily"


def test_independent_lock_returns_temporary_failure(paths):
    daily, signals, evaluations, runs, db = paths
    runs.mkdir()
    with (runs / ".financial-forward.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code, report = runner.run(daily, signals, evaluations, runs, db, now=RUN)
    assert code == 75 and report["reason"] == "financial_forward_locked"
    archived = list(runs.glob("*.json"))
    assert len(archived) == 1
    evidence = json.loads(archived[0].read_text())
    assert evidence["reason"] == "financial_forward_locked"
    assert evidence["result_digest"] == report["result_digest"]
    assert stat.S_IMODE(archived[0].stat().st_mode) == 0o400


def test_single_evaluation_hard_timeout_and_run_evidence(paths, monkeypatch):
    daily, signals, evaluations, runs, db = paths

    def slow(*args, **kwargs):
        time.sleep(1)

    monkeypatch.setattr(runner.forward, "evaluate", slow)
    started = time.monotonic()
    with pytest.raises(runner.RunBudgetExceeded, match="run_budget_exhausted"):
        runner._evaluate_with_timeout({}, db, "free", RUN, 0.02)
    assert time.monotonic() - started < 0.5

    monkeypatch.setattr(runner.forward, "validate_archive", lambda value: None)
    signals.mkdir()
    signal_value = {"result_digest": "a" * 64, "signal_date": "2026-09-11"}
    (signals / "2026-09-11.json").write_text(json.dumps(signal_value))
    monkeypatch.setattr(
        runner, "_evaluate_with_timeout",
        lambda *args, **kwargs: (_ for _ in ()).throw(runner.RunBudgetExceeded()))
    code, report = runner.run(daily, signals, evaluations, runs, db, now=RUN)
    assert code == 1
    assert report["errors"][-1]["reason"] == "run_budget_exhausted"
    assert report["evaluations"][0]["error"] == "run_budget_exhausted"


def test_missing_database_is_preserved_in_run_evidence(paths):
    daily, signals, evaluations, runs, db = paths
    db.unlink()
    code, report = runner.run(daily, signals, evaluations, runs, db, now=RUN)
    assert code == 1 and report["errors"] == [{"stage": "database", "reason": "FileNotFoundError"}]
    archived = json.loads(next(runs.glob("*.json")).read_text())
    assert archived["errors"] == report["errors"]


def test_budget_and_corrupt_existing_evaluation_fail_closed(paths, monkeypatch):
    daily_dir, signal_dir, evaluations, runs, db = paths
    with pytest.raises(ValueError, match="invalid_budget"):
        runner.run(daily_dir, signal_dir, evaluations, runs, db, budget_seconds=0, now=RUN)
    document = _daily(monkeypatch)
    signal_dir.mkdir()
    runner._publish(signal_dir / "2026-09-11.json", runner.forward.seal(document, now=RUN))
    db.unlink()
    _db(db, mature=True)
    target = evaluations / "2026-09-11/5.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    code, report = runner.run(
        daily_dir, signal_dir, evaluations, runs, db,
        now=datetime.fromisoformat("2026-09-18T19:30:00+08:00"))
    assert code == 1 and report["evaluations"][0]["error"] == "ValueError"
    assert target.read_text() == "{}"
