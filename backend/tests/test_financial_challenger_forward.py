from copy import deepcopy
from datetime import date, datetime
import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest
from sqlalchemy import create_engine

from qagent.storage.tables import MarketBarCacheRow

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
batch = __import__("collect_daily_documented_research")

SPEC = importlib.util.spec_from_file_location("financial_forward", SCRIPTS / "evaluate_financial_challenger.py")
forward = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(forward)
sys.path.pop(0)

SYMBOLS = ["600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH", "600006.SH"]
NOW = datetime.fromisoformat("2026-09-11T17:00:00+08:00")


def signed(value):
    value.pop("result_digest", None)
    value["result_digest"] = forward.digest(value)
    return value


@pytest.fixture
def daily(monkeypatch):
    monkeypatch.setattr(batch, "now", lambda: "2026-09-11T16:40:00+08:00")
    def query(base_url, **request):
        symbol = request["params"]["ts_code"]
        row = {"ts_code": symbol, "end_date": "20260630", "ann_date": "20260801",
               "report_type": "1", "comp_type": "1", "n_cashflow_act": 30,
               "n_income": 10, "total_revenue": 100, "trade_date": "20260911",
               "pe": 10, "total_assets": 1000, "total_liab": 300, "roe": 10,
               "netprofit_margin": 20, "type": "预增", "p_change_min": 1, "p_change_max": 2}
        return {"source": "datahubco", "status": "observed", "rows": [row],
                "decision_weight": False, "activation_allowed": False}
    return batch.run_batch(SYMBOLS, "20260630", "20260911", query=query)


@pytest.fixture
def dynamic_daily(daily):
    items = [{"instrument_id": "CN:159146", "asset_type": "etf",
              "signal_date": "2026-09-11", "signal_date_fresh": True}]
    items.extend({"instrument_id": "CN:" + symbol[:6], "asset_type": "stock",
                  "signal_date": "2026-09-11", "signal_date_fresh": True}
                 for symbol in SYMBOLS)
    payload = {
        "items": items,
        "summary": {"shown_candidates": 7, "total_candidates": 7},
        "data_health": {
            "paper_candidate_pool_endpoint": "true",
            "paper_candidate_pool_limit": "100",
            "paper_candidate_pool_total": "7",
            "paper_candidate_freshness_gate": "fresh",
            "paper_candidate_expected_signal_date": "2026-09-11",
            "paper_candidate_signal_date_mismatch": "0",
        },
    }
    symbols, universe = batch.candidate_pool_universe(payload, "20260911")
    assert symbols == SYMBOLS
    value = deepcopy(daily)
    value["universe"] = universe
    return signed(value)


def baseline():
    return signed({"protocol": "g2-risk-feature-forward-v1", "status": "ready", "reasons": [],
                   "signal_date": "2026-09-11", "coverage": {"scored_rows": 6},
                   "collection_started_at_utc": "2026-09-11T08:00:00+00:00",
                   "collected_at_utc": "2026-09-11T08:01:00+00:00",
                   "decision_weight": False, "activation_allowed": False,
                   "predictions": [{"instrument_id": "CN:" + s[:6], "industry": "test",
                                    **{v: {"rank": 6-i, "score": i} for v in ("full_features", "without_risk")}}
                                   for i, s in enumerate(SYMBOLS)]})


def database(tmp_path, *, missing=None, unsafe=None, benchmark_missing=False):
    path = tmp_path / "cache.db"
    engine = create_engine("sqlite:///" + str(path))
    MarketBarCacheRow.__table__.create(engine)
    rows = []
    for day in (date(2026, 9, 14), date(2026, 9, 18)):
        for i, key in enumerate(["CN:" + s[:6] for s in SYMBOLS] + ["CN:000300.IDX"]):
            if (key == missing and day.day == 18) or (benchmark_missing and key.endswith("IDX")):
                continue
            close = 100 if day.day == 14 else 102 if key.endswith("IDX") else 110+i
            rows.append({"provider_mode": "free", "instrument_id": key, "trade_date": day,
                         "source_provider": "fixture", "open": 100, "high": max(100, close), "low": 100,
                         "close": close, "volume": 1, "adjusted_open": 100, "adjusted_close": close,
                         "adjusted_high": max(100, close), "adjusted_low": 100, "adjustment_factor": 1,
                         "adjustment_type": "snapshot_qfq_anchor" if key == unsafe else "qfq"})
    with engine.begin() as conn:
        conn.execute(MarketBarCacheRow.__table__.insert(), rows)
    engine.dispose()
    return path


def test_seal_replays_same_day_and_does_not_promote_observation_order(daily):
    signal = forward.seal(daily, now=NOW)
    assert signal["baseline_status"] == "baseline_unavailable"
    assert signal["baseline_order"] is None
    assert signal["rankings"][0]["instrument_id"] == "CN:600001"
    forward.validate_archive(signal)


def test_dynamic_candidate_pool_seals_and_archive_replays(dynamic_daily):
    signal = forward.seal(dynamic_daily, now=NOW)
    assert signal["status"] == "sealed"
    assert signal["source"]["universe"]["kind"] == "paper_candidate_pool_order"
    forward.validate_archive(signal)


def test_dynamic_candidate_pool_critical_tampering_rejected(dynamic_daily):
    def set_value(path, value):
        def mutate(document):
            target = document["universe"]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
        return mutate

    mutations = [
        set_value(["kind"], "explicit_observation_order"),
        set_value(["source"], "other"),
        set_value(["endpoint"], "/api/other"),
        set_value(["provider"], "fixture"),
        set_value(["include_etfs"], True),
        set_value(["requested_pool_limit"], 20),
        set_value(["selected_limit"], 19),
        set_value(["selected_count"], 5),
        set_value(["symbols"], list(reversed(SYMBOLS))),
        set_value(["selection_order", 0, "position"], 2),
        set_value(["selection_order", 0, "source_position"], 3),
        set_value(["selection_order", 0, "instrument_id"], "CN:600999"),
        set_value(["eligible_stock_count"], 7),
        set_value(["source_shown_count"], 8),
        set_value(["source_total_count"], 8),
        set_value(["excluded_count"], 0),
        set_value(["excluded_reasons", "explicit_fund_asset_type"], 2),
        set_value(["excluded_digest"], "0" * 64),
        set_value(["excluded_items", 0, "reason"], "selected_limit"),
        set_value(["response_summary", "shown_candidates"], 8),
        set_value(["response_data_health", "paper_candidate_pool_limit"], "20"),
        set_value(["response_data_health", "paper_candidate_expected_signal_date"], "2026-09-10"),
        set_value(["response_data_health", "paper_candidate_freshness_gate"], "filtered"),
        set_value(["response_data_health", "paper_candidate_signal_date_mismatch"], "1"),
        set_value(["response_digest"], "not-a-digest"),
        set_value(["digest"], "0" * 64),
    ]
    for mutate in mutations:
        changed = deepcopy(dynamic_daily)
        mutate(changed)
        with pytest.raises(ValueError):
            forward.seal(signed(changed), now=NOW)


def test_dynamic_candidate_pool_cannot_underselect_eligible_stocks(dynamic_daily):
    universe = deepcopy(dynamic_daily["universe"])
    omitted = universe["selection_order"].pop()
    universe["selected_count"] = 5
    universe["symbols"] = universe["symbols"][:5]
    universe["excluded_items"].append({**omitted, "reason": "selected_limit"})
    universe["excluded_items"].sort(key=lambda item: item["source_position"])
    universe["excluded_count"] = len(universe["excluded_items"])
    universe["excluded_reasons"] = {"explicit_fund_asset_type": 1, "selected_limit": 1}
    universe["excluded_digest"] = forward.digest(universe["excluded_items"])
    universe["digest"] = forward.digest({
        "symbols": universe["symbols"], "response_digest": universe["response_digest"]})
    with pytest.raises(ValueError, match="count_mismatch"):
        forward.validate_source_universe(universe, universe["symbols"], date(2026, 9, 11))


@pytest.mark.parametrize("clock", ["2026-09-14T17:00:00+08:00", "2026-09-11T14:59:00+08:00",
                                   "2026-09-11T17:00:00"])
def test_historical_backfill_before_close_and_naive_time_rejected(daily, clock):
    with pytest.raises(ValueError):
        forward.seal(daily, now=datetime.fromisoformat(clock))


def test_capture_cross_day_preclose_and_future_rejected(daily):
    for field, value in (("started_at", "2026-09-10T16:00:00+08:00"),
                         ("started_at", "2026-09-11T15:00:00+08:00"),
                         ("finished_at", "2026-09-11T18:00:00+08:00")):
        changed = deepcopy(daily)
        changed[field] = value
        with pytest.raises(ValueError):
            forward.seal(signed(changed), now=NOW)


def test_tampering_even_resealed_candidate_rejected(daily):
    changed = deepcopy(daily)
    changed["financial_candidate"]["rankings"][0]["rank"] = 99
    signed(changed["financial_candidate"])
    with pytest.raises(ValueError, match="candidate_replay"):
        forward.seal(signed(changed), now=NOW)


def test_raw_response_disagreement_rejected(daily):
    daily["system_evidence"][SYMBOLS[0]]["cashflow"]["response"]["rows"] = []
    with pytest.raises(ValueError, match="raw_system"):
        forward.seal(signed(daily), now=NOW)


def test_baseline_date_missing_cohort_and_input_order_never_accepted(daily):
    for b in ({"order": SYMBOLS}, {**baseline(), "signal_date": "2026-09-10"}):
        signal = forward.seal(daily, signed(b), now=NOW)
        assert signal["baseline_status"] == "baseline_unavailable"
    good = forward.seal(daily, baseline(), now=NOW)
    assert good["baseline_order"] == ["CN:" + s[:6] for s in reversed(SYMBOLS)]
    good["baseline_order"].reverse()
    with pytest.raises(ValueError, match="signal_replay"):
        forward.validate_archive(signed(good))


def test_baseline_valid_but_missing_one_eligible_instrument(daily):
    b = baseline()
    b["predictions"][0]["instrument_id"] = "CN:600999"
    assert forward.seal(daily, signed(b), now=NOW)["baseline_status"] == "baseline_unavailable"


def test_non_session_signal_rejected(daily):
    daily["trade_date"] = "20260912"
    with pytest.raises(ValueError, match="not_exchange_session"):
        forward.seal(signed(daily), now=datetime.fromisoformat("2026-09-12T17:00:00+08:00"))


def test_evaluate_exact_math_calendar_complete_pair_and_readonly(daily, tmp_path):
    db = database(tmp_path)
    before = db.read_bytes()
    signal = forward.seal(daily, baseline(), now=NOW)
    result = forward.evaluate(signal, db, as_of=datetime.fromisoformat("2026-09-18T16:00:00+08:00"))
    five, ten, twenty = result["horizons"]
    assert five["entry_date"] == "2026-09-14" and five["outcome_date"] == "2026-09-18"
    assert five["completed"] == 6 and five["paired_complete"] is True
    assert five["candidate_net_excess_pct"] == pytest.approx(9.9)
    assert five["baseline_net_excess_pct"] == pytest.approx(10.9)
    assert five["lift_pct"] == pytest.approx(-1)
    assert result["runtime_identity"]["evaluator_sha256"]
    assert result["runtime_identity"]["factor_shadow_outcomes_sha256"]
    assert Path(result["runtime_identity"]["backend_root_resolved"]).name == "backend"
    assert ten["status"] == twenty["status"] == "waiting_for_maturity"
    assert db.read_bytes() == before
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("market_bar_cache",)]


def test_before_maturity_close_no_prices_or_early_returns(daily, tmp_path):
    db = database(tmp_path)
    result = forward.evaluate(forward.seal(daily, now=NOW), db,
                              as_of=datetime.fromisoformat("2026-09-18T15:00:00+08:00"))
    assert all(h["status"] == "waiting_for_maturity" and h["labels"] == [] for h in result["horizons"])


@pytest.mark.parametrize("options,reason", [({"missing": "CN:600001"}, "missing_price_row"),
                                           ({"unsafe": "CN:600001"}, "invalid_adjusted_price"),
                                           ({"benchmark_missing": True}, "benchmark_unavailable")])
def test_unresolved_no_backfill_no_partial_portfolio(daily, tmp_path, options, reason):
    signal = forward.seal(daily, baseline(), now=NOW)
    result = forward.evaluate(signal, database(tmp_path, **options),
                              as_of=datetime.fromisoformat("2026-09-18T16:00:00+08:00"))["horizons"][0]
    assert reason in result["labels"][0]["reasons"]
    assert result["paired_complete"] is False and result["lift_pct"] is None
    assert result["candidate_net_excess_pct"] is None
    assert result["candidate_selected"] == ["CN:" + s[:6] for s in SYMBOLS[:5]]


def test_signal_publication_exclusive_and_original_immutable(daily, tmp_path):
    path = tmp_path / "2026-09-11.json"
    forward.publish(path, "first")
    with pytest.raises(FileExistsError):
        forward.publish(path, "second")
    assert path.read_text() == "first"
