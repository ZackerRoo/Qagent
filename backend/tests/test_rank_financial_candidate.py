from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("rank_financial_candidate", SCRIPTS / "rank_financial_candidate.py")
ranking = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ranking)
sys.path.pop(0)

STOCKS = ["600519.SH", "603259.SH", "600000.SH"]


def report(symbol, cash=150, profit=100, revenue=300, pe=20):
    row = {"ts_code": symbol, "end_date": "20260630", "ann_date": "20260815",
           "f_ann_date": "20260816", "report_type": "1", "comp_type": "1"}
    raw = {"cashflow": [{**row, "n_cashflow_act": cash}],
           "income": [{**row, "n_income": profit, "total_revenue": revenue}],
           "daily_basic": [{"ts_code": symbol, "trade_date": "20260911", "pe": pe}],
           "forecast": []}
    return ranking.analyze({"source": "datahubco", "retrieved_at": "2026-09-14T12:00:00+08:00",
                            "period": "20260630", "trade_date": "20260911", "instruments": {
                                symbol: {api: {"status": "observed" if rows else "no_rows", "rows": rows}
                                         for api, rows in raw.items()}}})


def seal(value):
    value.pop("result_digest", None)
    value["result_digest"] = ranking.digest(value)
    return value


def test_fixed_equal_percentiles_and_same_cohort_overlap():
    reports = [report(STOCKS[0], cash=50, pe=40), report(STOCKS[1], cash=150, pe=20),
               report(STOCKS[2], cash=200, pe=10)]
    result = ranking.rank_candidate(reports, STOCKS, top_k=2)
    assert [row["stock_id"] for row in result["rankings"]] == STOCKS[::-1]
    assert [row["score"] for row in result["rankings"]] == [1, 0.5, 0]
    assert result["comparison"]["overlap_fraction"] == 0.5
    assert result["same_cohort_baseline_order"] == STOCKS
    assert result["decision_weight"] is False and result["activation_allowed"] is False
    assert result == ranking.rank_candidate(deepcopy(reports), list(STOCKS), top_k=2)


def test_ties_average_percentile_then_symbol_and_singleton():
    result = ranking.rank_candidate([report(s) for s in STOCKS], STOCKS)
    assert [row["stock_id"] for row in result["rankings"]] == sorted(STOCKS)
    assert all(row["score"] == 0.5 for row in result["rankings"])
    single = ranking.rank_candidate([report(STOCKS[0])], [STOCKS[0]])
    assert single["rankings"][0]["score_exact"] == "1/2"


@pytest.mark.parametrize("kwargs", [{"profit": 0}, {"profit": -1}, {"profit": None},
                                    {"pe": -1}, {"pe": "NaN"}, {"cash": "Infinity"},
                                    {"revenue": 0}])
def test_missing_invalid_inputs_excluded_from_both_orders(kwargs):
    result = ranking.rank_candidate([report(STOCKS[0], **kwargs), report(STOCKS[1])], STOCKS)
    assert result["same_cohort_baseline_order"] == [STOCKS[1]]
    assert result["coverage"]["eligible_count"] == 1
    assert {item["stock_id"] for item in result["coverage"]["excluded"]} == {STOCKS[0], STOCKS[2]}
    assert result["comparison"]["effective_top_k"] == 1


def test_negative_cashflow_is_valid_numeric_not_missing():
    result = ranking.rank_candidate([report(STOCKS[0], cash=-50), report(STOCKS[1])], STOCKS[:2])
    assert result["coverage"]["eligible_count"] == 2
    assert result["rankings"][-1]["values"]["cashflow_to_netprofit"] == "-0.5"


def test_unrelated_forecasts_do_not_gate_and_no_eligible_is_explicit():
    document = report(STOCKS[0], pe=0)
    result = ranking.rank_candidate([document], [STOCKS[0]])
    assert result["status"] == "no_eligible_stocks" and result["rankings"] == []
    assert result["comparison"]["overlap_fraction"] is None


def test_tampered_digest_or_resealed_derived_values_rejected():
    document = report(STOCKS[0])
    document["instruments"][STOCKS[0]]["financial_ratios"]["cashflow_to_netprofit"]["value"] = "999"
    with pytest.raises(ValueError, match="digest_mismatch"):
        ranking.rank_candidate([document], [STOCKS[0]])
    seal(document)
    with pytest.raises(ValueError, match="replay_mismatch"):
        ranking.rank_candidate([document], [STOCKS[0]])


def test_duplicate_or_cross_day_cohorts_rejected():
    first, second = report(STOCKS[0]), report(STOCKS[1])
    with pytest.raises(ValueError, match="duplicate_stock_observation"):
        ranking.rank_candidate([first, first], STOCKS[:2])
    second["raw_evidence"]["retrieved_at"] = "2026-09-15T12:00:00+08:00"
    second = ranking.analyze(second["raw_evidence"])
    with pytest.raises(ValueError, match="incompatible_observation_cohort"):
        ranking.rank_candidate([first, second], STOCKS[:2])


def test_daily_wrapper_metadata_and_baseline_timing_are_not_preregistration():
    reports = [report(s) for s in STOCKS[:2]]
    wrapper = seal({"protocol": "daily-documented-research-v1", "source": "datahubco",
                    "period": "20260630", "trade_date": "20260911", "enrichment_reports": reports,
                    "decision_weight": False, "activation_allowed": False})
    result = ranking.rank_candidate([wrapper], {"order": STOCKS[:2], "label": "observation smoke",
                                               "recorded_at": "2026-09-14T10:00:00+08:00"})
    assert result["baseline_metadata"]["timestamp_precedes_collection"] is True
    assert result["baseline_metadata"]["production_baseline_verified"] is False
    wrapper["source"] = "promax"
    seal(wrapper)
    with pytest.raises(ValueError, match="wrapper_identity_mismatch"):
        ranking.rank_candidate([wrapper], STOCKS[:2])


def test_invalid_baseline_and_top_k():
    for baseline in ([], [STOCKS[0]] * 2, ["G2:fake"]):
        with pytest.raises(ValueError):
            ranking.rank_candidate([report(STOCKS[0])], baseline)
    with pytest.raises(ValueError):
        ranking.rank_candidate([report(STOCKS[0])], [STOCKS[0]], top_k=0)


def test_cli_nonoverwrite_and_input_unchanged(tmp_path):
    source, baseline, output = tmp_path / "source.json", tmp_path / "baseline.json", tmp_path / "out.json"
    source.write_text(json.dumps(report(STOCKS[0])))
    baseline.write_text(json.dumps([STOCKS[0]]))
    original = source.read_bytes()
    command = [sys.executable, str(SCRIPTS / "rank_financial_candidate.py"), "--input", str(source),
               "--baseline", str(baseline), "--output", str(output)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    saved = output.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode == 2
    assert saved == output.read_bytes() and original == source.read_bytes()


def report_v2(symbol, liabilities=300, roe=10, margin=20, comp_type="1"):
    raw = report(symbol)["raw_evidence"]
    raw["analysis_version"] = 2
    base = {"ts_code": symbol, "end_date": "20260630", "ann_date": "20260815"}
    raw["instruments"][symbol].update({
        "balancesheet": {"status": "observed", "rows": [{**base, "report_type": "1", "comp_type": comp_type,
                                                         "total_assets": 1000, "total_liab": liabilities}]},
        "fina_indicator": {"status": "observed", "rows": [{**base, "roe": roe, "netprofit_margin": margin}]},
    })
    return ranking.analyze(raw)


def test_v2_six_equal_weights_reverse_leverage_direction():
    documents = [report_v2(STOCKS[0], liabilities=600), report_v2(STOCKS[1], liabilities=200)]
    result = ranking.rank_candidate(documents, STOCKS[:2])
    assert result["protocol"] == "financial-candidate-equal-percentiles-v2"
    assert result["policy"]["weights"] == ["1/6"] * 6
    assert result["rankings"][0]["stock_id"] == STOCKS[1]
    assert result["rankings"][0]["percentiles"]["liabilities_to_assets"] == 1
    assert result["rankings"][0]["score_exact"] == "7/12"


@pytest.mark.parametrize("api,field,formatted", [
    ("daily_basic", "pe", "20.00"),
    ("fina_indicator", "roe", "10.0"),
    ("balancesheet", "total_assets", "1000.00"),
])
def test_v2_candidate_accepts_equivalent_precision_but_excludes_real_revision(api, field, formatted):
    documents = [report_v2(symbol) for symbol in STOCKS[:2]]
    expected = ranking.rank_candidate(documents, STOCKS[:2])
    raw = documents[0]["raw_evidence"]
    rows = raw["instruments"][STOCKS[0]][api]["rows"]
    rows.append({**rows[0], field: formatted})
    documents[0] = ranking.analyze(raw)
    equivalent = ranking.rank_candidate(documents, STOCKS[:2])
    assert equivalent["rankings"] == expected["rankings"]
    assert equivalent["coverage"]["excluded"] == []
    rows[1][field] = formatted + "1"
    documents[0] = ranking.analyze(raw)
    conflict = ranking.rank_candidate(documents, STOCKS[:2])
    assert conflict["same_cohort_baseline_order"] == [STOCKS[1]]
    assert conflict["coverage"]["excluded"][0]["stock_id"] == STOCKS[0]


@pytest.mark.parametrize("kwargs", [{"roe": None}, {"margin": "Infinity"}, {"liabilities": -1},
                                    {"comp_type": "2"}, {"comp_type": "4"}, {"comp_type": "7"}])
def test_v2_missing_metric_and_financial_companies_excluded(kwargs):
    result = ranking.rank_candidate([report_v2(STOCKS[0], **kwargs), report_v2(STOCKS[1])], STOCKS[:2])
    assert result["same_cohort_baseline_order"] == [STOCKS[1]]
    assert result["coverage"]["excluded"][0]["stock_id"] == STOCKS[0]


def test_v1_still_validates_but_mixed_v1_v2_rejected():
    legacy = report(STOCKS[0])
    assert ranking.validate_report(legacy)["protocol"] == "financial-enrichment-current-v1"
    assert ranking.rank_candidate([legacy], [STOCKS[0]])["policy"]["weights"] == ["1/3"] * 3
    with pytest.raises(ValueError, match="mixed_candidate_versions"):
        ranking.rank_candidate([legacy, report_v2(STOCKS[1])], STOCKS[:2])


def test_protocol_downgrade_cannot_bypass_new_data_validation():
    document = report_v2(STOCKS[0])
    document["protocol"] = "financial-enrichment-current-v1"
    seal(document)
    with pytest.raises(ValueError, match="replay_mismatch"):
        ranking.validate_report(document)
