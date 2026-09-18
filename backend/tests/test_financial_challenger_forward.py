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
               "pe": 10, "total_mv": 100000 + int(symbol[:6]),
               "total_assets": 1000, "total_liab": 300, "roe": 10,
               "netprofit_margin": 20, "type": "预增", "p_change_min": 1, "p_change_max": 2}
        return {"source": "datahubco", "status": "observed", "rows": [row],
                "decision_weight": False, "activation_allowed": False}
    return batch.run_batch(SYMBOLS, "20260630", "20260911", query=query)


@pytest.fixture
def dynamic_daily(daily):
    items = [{"instrument_id": "CN:159146", "asset_type": "etf",
              "signal_date": "2026-09-11", "signal_date_fresh": True}]
    items.extend({"instrument_id": "CN:" + symbol[:6], "asset_type": "stock",
                  "industry": "test", "exposure_group": "test",
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


def matched_inputs(monkeypatch, *, industries=None, total_mvs=None):
    symbols = [f"600{i:03}.SH" for i in range(1, 11)]
    industries = industries or ["test"] * len(symbols)
    total_mvs = total_mvs or [100 + i for i in range(len(symbols))]
    by_symbol = dict(zip(symbols, total_mvs))
    monkeypatch.setattr(batch, "now", lambda: "2026-09-11T16:40:00+08:00")

    def query(base_url, **request):
        symbol = request["params"]["ts_code"]
        row = {"ts_code": symbol, "end_date": "20260630", "ann_date": "20260801",
               "report_type": "1", "comp_type": "1", "n_cashflow_act": 30,
               "n_income": 10, "total_revenue": 100, "trade_date": "20260911",
               "pe": 10, "total_mv": by_symbol[symbol], "total_assets": 1000,
               "total_liab": 300, "roe": 10, "netprofit_margin": 20, "type": "预增",
               "p_change_min": 1, "p_change_max": 2}
        return {"source": "datahubco", "status": "observed", "rows": [row],
                "decision_weight": False, "activation_allowed": False}

    items = [{"instrument_id": "CN:" + symbol[:6], "asset_type": "stock",
              "industry": industry, "exposure_group": industry,
              "signal_date": "2026-09-11", "signal_date_fresh": True}
             for symbol, industry in zip(symbols, industries)]
    payload = {
        "items": items,
        "summary": {"shown_candidates": len(items), "total_candidates": len(items)},
        "data_health": {
            "paper_candidate_pool_endpoint": "true", "paper_candidate_pool_limit": "100",
            "paper_candidate_pool_total": str(len(items)), "paper_candidate_freshness_gate": "fresh",
            "paper_candidate_expected_signal_date": "2026-09-11",
            "paper_candidate_signal_date_mismatch": "0",
        },
    }
    _, universe = batch.candidate_pool_universe(payload, "20260911")
    document = batch.run_batch(
        symbols, "20260630", "20260911", query=query, universe=universe)
    g2 = signed({
        "protocol": "g2-risk-feature-forward-v1", "status": "ready", "reasons": [],
        "signal_date": "2026-09-11", "coverage": {"scored_rows": len(symbols)},
        "collection_started_at_utc": "2026-09-11T08:00:00+00:00",
        "collected_at_utc": "2026-09-11T08:01:00+00:00",
        "decision_weight": False, "activation_allowed": False,
        "predictions": [
            {"instrument_id": "CN:" + symbol[:6], "industry": industry,
             "full_features": {"rank": index + 1, "score": len(symbols) - index},
             "without_risk": {"rank": index + 1, "score": len(symbols) - index}}
            for index, (symbol, industry) in enumerate(zip(symbols, industries))
        ],
    })
    return symbols, document, g2


def database(tmp_path, *, missing=None, unsafe=None, benchmark_missing=False, symbols=None):
    symbols = symbols or SYMBOLS
    path = tmp_path / "cache.db"
    engine = create_engine("sqlite:///" + str(path))
    MarketBarCacheRow.__table__.create(engine)
    rows = []
    for day in (date(2026, 9, 14), date(2026, 9, 18)):
        for i, key in enumerate(["CN:" + s[:6] for s in symbols] + ["CN:000300.IDX"]):
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


@pytest.mark.parametrize("baseline_case", ["absent", "wrong_day", "missing_cohort", "invalid"])
def test_extended_signal_missing_valid_g2_remains_v2(monkeypatch, tmp_path, baseline_case):
    symbols, document, g2 = matched_inputs(monkeypatch)
    if baseline_case == "absent":
        g2 = None
    elif baseline_case == "wrong_day":
        g2["signal_date"] = "2026-09-10"
        signed(g2)
    elif baseline_case == "missing_cohort":
        g2["predictions"].pop()
        g2["coverage"]["scored_rows"] -= 1
        signed(g2)
    else:
        g2 = {"order": symbols}
    signal = forward.seal(document, g2, now=NOW)
    assert signal["protocol"] == "financial-rule-forward-v2"
    assert signal["baseline_status"] == "baseline_unavailable"
    assert signal["control_status"] == "control_unavailable"
    assert signal["control_reasons"] == ["g2_rank_incomplete"]
    assert signal["matched_control_pairs"] == []
    assert signal["control_order"] == []
    forward.validate_archive(signal)
    result = forward.evaluate(signal, database(tmp_path, symbols=symbols),
                              as_of=datetime.fromisoformat("2026-09-18T16:00:00+08:00"))
    assert result["protocol"] == "financial-rule-forward-evaluation-v2"
    assert result["horizons"][0]["paired_complete"] is False
    assert result["horizons"][0]["lift_pct"] is None


def test_pre_fix_extended_v1_without_baseline_replays_unchanged(monkeypatch, tmp_path):
    symbols, document, _ = matched_inputs(monkeypatch)
    # Reproduce the prior artifact schema independently of the new replay switch.
    old = forward.seal(document, now=NOW)
    for key in ("control_status", "control_discriminative", "control_reasons",
                "matched_control_pairs", "matched_control_pairs_digest", "control_order"):
        old.pop(key)
    old.update(protocol=forward.POLICY["protocol"], policy=forward.POLICY,
               policy_digest=forward.digest(forward.POLICY), implementation_sha256="0" * 64)
    signed(old)
    before = deepcopy(old)
    forward.validate_archive(old)
    result = forward.evaluate(old, database(tmp_path, symbols=symbols),
                              as_of=datetime.fromisoformat("2026-09-18T16:00:00+08:00"))
    assert old == before
    assert result["protocol"] == "financial-rule-forward-evaluation-v1"
    assert result["horizons"][0]["lift_pct"] is None


def test_old_candidate_pool_v1_archive_replay_and_evaluation_compatibility(dynamic_daily, tmp_path):
    old = deepcopy(dynamic_daily)
    for item in old["universe"]["selection_order"]:
        item.pop("industry")
        item.pop("exposure_group")
    for index, report in enumerate(old["enrichment_reports"]):
        raw = deepcopy(report["raw_evidence"])
        raw.pop("matched_control_evidence_version", None)
        old["enrichment_reports"][index] = batch.analyze(raw)
    signed(old)
    old["financial_candidate"] = batch.rank_candidate([old], old["symbols"], top_k=5)
    signal = forward.seal(signed(old), baseline(), now=NOW)
    assert signal["protocol"] == "financial-rule-forward-v1"
    assert "control_status" not in signal
    forward.validate_archive(signal)
    result = forward.evaluate(signal, database(tmp_path),
                              as_of=datetime.fromisoformat("2026-09-18T16:00:00+08:00"))
    assert result["protocol"] == "financial-rule-forward-evaluation-v1"
    assert "control_selected" not in result["horizons"][0]


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
        set_value(["selection_order", 0, "exposure_group"], "different"),
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


def test_v2_matched_control_success_is_same_industry_unique_and_deterministic(monkeypatch, tmp_path):
    symbols, document, g2 = matched_inputs(monkeypatch)
    first = forward.seal(document, g2, now=NOW)
    second = forward.seal(deepcopy(document), deepcopy(g2), now=NOW)
    assert first == second
    assert first["protocol"] == "financial-rule-forward-v2"
    assert first["control_status"] == "available" and first["control_discriminative"] is True
    pairs = first["matched_control_pairs"]
    assert len(pairs) == 5
    assert len({pair["control_instrument_id"] for pair in pairs}) == 5
    assert all(pair["industry"] == "test" for pair in pairs)
    assert all(pair["control_is_candidate"] is False for pair in pairs)
    assert all(pair["evidence_complete"] is True and pair["discriminative"] is True
               for pair in pairs)
    assert all(pair["pair_digest"] == forward.digest(
        {key: value for key, value in pair.items() if key != "pair_digest"}) for pair in pairs)
    assert first["policy"]["total_mv_semantics"] == (
        "provider_source_unit_unchanged_current_observation_not_historical_pit")
    assert first["matched_control_pairs_digest"] == forward.digest(pairs)
    forward.validate_archive(first)

    outcome = forward.evaluate(
        first, database(tmp_path, symbols=symbols),
        as_of=datetime.fromisoformat("2026-09-18T16:00:00+08:00"),
    )["horizons"][0]
    assert outcome["candidate_completed"] == outcome["control_completed"] == 5
    assert outcome["baseline_net_excess_pct"] is None
    assert outcome["paired_complete"] is True
    assert outcome["lift_pct"] == pytest.approx(
        outcome["candidate_net_excess_pct"] - outcome["control_net_excess_pct"]
    )


def test_v2_equal_market_cap_distance_uses_g2_rank_before_instrument_id(monkeypatch):
    _, document, g2 = matched_inputs(monkeypatch, total_mvs=[100] * 10)
    rows = g2["predictions"]
    rows[5]["full_features"], rows[6]["full_features"] = (
        rows[6]["full_features"], rows[5]["full_features"])
    signed(g2)
    signal = forward.seal(document, g2, now=NOW)
    first = signal["matched_control_pairs"][0]
    assert first["absolute_log_total_mv_distance"] == 0
    assert first["control_instrument_id"] == "CN:600007"
    assert first["control_g2_rank"] == 6


def test_v2_industry_shortage_is_control_unavailable(monkeypatch):
    industries = ["A", "A", "A", "A", "B", "C", "C", "C", "C", "C"]
    _, document, g2 = matched_inputs(monkeypatch, industries=industries)
    signal = forward.seal(document, g2, now=NOW)
    assert signal["control_status"] == "control_unavailable"
    assert signal["control_discriminative"] is False
    assert signal["matched_control_pairs"] == []
    assert signal["control_reasons"] == ["same_industry_control_unavailable"]


def test_v2_missing_market_cap_is_control_unavailable_without_imputation(monkeypatch):
    values = [None, *range(101, 110)]
    _, document, g2 = matched_inputs(monkeypatch, total_mvs=values)
    signal = forward.seal(document, g2, now=NOW)
    assert signal["control_status"] == "control_unavailable"
    assert signal["control_reasons"] == ["total_mv_incomplete"]
    assert signal["matched_control_pairs"] == []


def test_v2_conflicting_market_cap_does_not_change_financial_rank(monkeypatch):
    _, document, g2 = matched_inputs(monkeypatch)
    original_order = [row["stock_id"] for row in document["financial_candidate"]["rankings"]]
    raw = deepcopy(document["enrichment_reports"][0]["raw_evidence"])
    rows = raw["instruments"][document["symbols"][0]]["daily_basic"]["rows"]
    rows.append({**rows[0], "total_mv": rows[0]["total_mv"] + 1})
    document["system_evidence"][document["symbols"][0]]["daily_basic"]["response"]["rows"] = deepcopy(rows)
    document["system_evidence"][document["symbols"][0]]["daily_basic"]["coverage"]["received_rows"] = 2
    document["enrichment_reports"][0] = batch.analyze(raw)
    signed(document)
    document["financial_candidate"] = batch.rank_candidate(
        [document], document["symbols"], top_k=5)
    signed(document)
    assert [row["stock_id"] for row in document["financial_candidate"]["rankings"]] == original_order
    signal = forward.seal(document, g2, now=NOW)
    assert signal["control_status"] == "control_unavailable"
    assert signal["control_reasons"] == ["total_mv_incomplete"]


def test_v2_daily_basic_without_derived_values_is_control_unavailable(monkeypatch):
    _, document, g2 = matched_inputs(monkeypatch)
    symbol = document["symbols"][-1]
    raw = deepcopy(document["enrichment_reports"][-1]["raw_evidence"])
    raw["instruments"][symbol]["daily_basic"] = {"status": "no_rows", "rows": []}
    evidence = document["system_evidence"][symbol]["daily_basic"]
    evidence["response"].update(status="no_rows", rows=[])
    evidence["classification"] = "no_rows"
    evidence["coverage"] = {"received_rows": 0, "page_limit_reached": False}
    document["enrichment_reports"][-1] = batch.analyze(raw)
    signed(document)
    document["financial_candidate"] = batch.rank_candidate(
        [document], document["symbols"], top_k=5)
    signed(document)
    assert document["financial_candidate"]["coverage"]["eligible_count"] == 9
    signal = forward.seal(document, g2, now=NOW)
    assert signal["control_status"] == "control_unavailable"
    assert signal["control_reasons"] == ["total_mv_incomplete"]


def test_v2_missing_industry_is_control_unavailable_without_cross_industry_match(monkeypatch):
    industries = [None, *(["A"] * 9)]
    _, document, g2 = matched_inputs(monkeypatch, industries=industries)
    signal = forward.seal(document, g2, now=NOW)
    assert signal["control_status"] == "control_unavailable"
    assert signal["control_reasons"] == ["industry_incomplete"]
    assert signal["matched_control_pairs"] == []


def test_v2_fewer_than_two_external_controls_is_not_discriminative(monkeypatch):
    industries = ["A", "A", "A", "A", "A", "A", "B", "B", "B", "B"]
    _, document, g2 = matched_inputs(monkeypatch, industries=industries)
    signal = forward.seal(document, g2, now=NOW)
    assert signal["control_status"] == "control_not_discriminative"
    assert signal["control_discriminative"] is False
    assert len(signal["matched_control_pairs"]) == 5
    assert sum(not pair["control_is_candidate"] for pair in signal["matched_control_pairs"]) == 1


@pytest.mark.parametrize("missing_side", ["candidate", "control"])
def test_v2_price_incomplete_requires_candidate_and_control_five_of_five(
        monkeypatch, tmp_path, missing_side):
    symbols, document, g2 = matched_inputs(monkeypatch)
    signal = forward.seal(document, g2, now=NOW)
    missing = (signal["rankings"][0]["instrument_id"] if missing_side == "candidate"
               else signal["control_order"][0])
    outcome = forward.evaluate(
        signal, database(tmp_path, symbols=symbols, missing=missing),
        as_of=datetime.fromisoformat("2026-09-18T16:00:00+08:00"),
    )["horizons"][0]
    assert outcome["candidate_completed"] == (4 if missing_side == "candidate" else 5)
    assert outcome["control_completed"] == (5 if missing_side == "candidate" else 4)
    assert (outcome["candidate_net_excess_pct"] if missing_side == "candidate"
            else outcome["control_net_excess_pct"]) is None
    assert outcome["paired_complete"] is False and outcome["lift_pct"] is None
    assert any(not pair["pair_complete"] for pair in outcome["matched_control_pair_results"])
    assert outcome["matched_control_pair_results_digest"] == forward.digest(
        outcome["matched_control_pair_results"])
    assert all(pair["pair_result_digest"] == forward.digest(
        {key: value for key, value in pair.items() if key != "pair_result_digest"})
        for pair in outcome["matched_control_pair_results"])


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
