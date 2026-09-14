from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("research_financial_enrichment", SCRIPTS / "research_financial_enrichment.py")
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
sys.path.pop(0)


def fixture():
    row = {"ts_code": "600519.SH", "end_date": "20260630", "ann_date": "20260815",
           "f_ann_date": "20260816", "report_type": "1", "comp_type": "1"}
    raw = {"cashflow": [{**row, "n_cashflow_act": 150}],
           "income": [{**row, "n_income": 100, "total_revenue": 300}],
           "daily_basic": [{"ts_code": "600519.SH", "trade_date": "20260911", "pe": 20,
                            "pe_ttm": 25, "pb": 3, "turnover_rate": 0.5, "volume_ratio": 1.25}],
           "forecast": [{"ts_code": "600519.SH", "end_date": "20241231", "ann_date": "20241230",
                         "type": "预增", "p_change_min": 10, "p_change_max": 20,
                         "net_profit_min": 100, "net_profit_max": 120}]}
    return {"source": "datahubco", "retrieved_at": "2026-09-14T12:00:00+08:00",
            "period": "20260630", "trade_date": "20260911", "instruments": {
                "600519.SH": {api: {"status": "observed", "rows": rows} for api, rows in raw.items()}}}


def sections(payload):
    return payload["instruments"]["600519.SH"]


def result(payload):
    return research.analyze(payload)["instruments"]["600519.SH"]


def test_metrics_raw_provenance_and_reproducibility():
    payload = fixture()
    report = research.analyze(payload)
    assert report == research.analyze(deepcopy(payload))
    assert report["raw_evidence"] == payload
    assert report["status"] == "observed" and report["activation_allowed"] is False
    got = result(payload)
    assert got["financial_ratios"]["cashflow_to_netprofit"]["value"] == "1.5"
    assert got["sections"]["daily_basic"]["derived"]["values"]["earnings_yield"] == "0.05"
    forecast = got["sections"]["forecast"]["derived"]["rows"][0]
    assert forecast["latest_announcement_date"] == "2024-12-30"
    assert forecast["announcement_age_days"] > 600
    assert "surprise" in report["metric_definitions"]["forecast"]


@pytest.mark.parametrize("value", [None, 0, -3, "NaN", "Infinity"])
def test_nonpositive_or_missing_does_not_become_zero(value):
    payload = fixture()
    sections(payload)["income"]["rows"][0]["n_income"] = value
    sections(payload)["daily_basic"]["rows"][0]["pe"] = value
    got = result(payload)
    assert got["financial_ratios"]["cashflow_to_netprofit"]["value"] is None
    assert got["sections"]["daily_basic"]["derived"]["values"]["earnings_yield"] is None


@pytest.mark.parametrize("api,field,value", [
    ("cashflow", "ts_code", "000001.SZ"), ("income", "end_date", "20260331"),
    ("daily_basic", "trade_date", "20260910"), ("cashflow", "f_ann_date", "20260915"),
])
def test_bad_identity_or_future_dates_do_not_contaminate_other_apis(api, field, value):
    payload = fixture()
    sections(payload)[api]["rows"][0][field] = value
    got = result(payload)
    assert got["sections"][api]["status"] in ("invalid_data", "no_usable_rows")
    assert got["sections"]["forecast"]["status"] == "observed"


@pytest.mark.parametrize("api,field", [("cashflow", "n_cashflow_act"), ("daily_basic", "pe"),
                                       ("forecast", "p_change_min")])
def test_consumed_revision_conflicts_are_rejected(api, field):
    payload = fixture()
    rows = sections(payload)[api]["rows"]
    rows.append({**rows[0], field: 999})
    got = result(payload)["sections"][api]
    assert got["status"] in ("invalid_data", "no_usable_rows")


def test_forecast_future_ann_and_reversed_range():
    payload = fixture()
    rows = sections(payload)["forecast"]["rows"]
    rows[0]["p_change_min"] = 50
    derived = result(payload)["sections"]["forecast"]["derived"]
    assert derived["rows"][0]["ranges"]["p_change_min"] is None
    rows[0]["ann_date"] = "20260915"
    assert result(payload)["sections"]["forecast"]["status"] == "no_usable_rows"


def test_equivalent_report_revisions_and_missing_features_remain_explicit():
    payload = fixture()
    rows = sections(payload)["income"]["rows"]
    rows.append({**rows[0], "unused": "not_consumed"})
    assert result(payload)["financial_ratios"]["cashflow_to_netprofit"]["value"] == "1.5"
    sections(payload)["daily_basic"]["rows"][0]["volume_ratio"] = None
    got = result(payload)["sections"]["daily_basic"]
    assert got["status"] == "observed"
    assert got["derived"]["values"]["volume_ratio"] is None
    assert got["derived"]["exclusions"]["volume_ratio"] == "missing_or_nonfinite"


def test_current_unclosed_session_rejected():
    payload = fixture()
    payload["trade_date"] = "20260914"
    with pytest.raises(ValueError, match="current_session_not_closed"):
        research.analyze(payload)


def test_fetch_independent_failure_and_request_budget():
    calls = []

    class Client:
        def query(self, api, **kwargs):
            calls.append((api, kwargs))
            if api == "income":
                raise RuntimeError("DO_NOT_ECHO_SECRET")
            return type("Table", (), {"rows": []})()

    payload = research.fetch(Client(), "promax", ["600519.SH", "603259.SH"], "20260630", "20260911",
                             observed=datetime.fromisoformat("2026-09-14T12:00:00+08:00"))
    assert len(calls) == 8 and all(p["limit"] == 12 for _, p in calls)
    assert calls[0][1]["period"] == "20260630" and calls[2][1]["trade_date"] == "20260911"
    assert calls[3][1] == {"ts_code": "600519.SH", "limit": 12}
    assert sections(payload)["income"]["status"] == "error"
    assert sections(payload)["cashflow"]["status"] == "no_rows"
    assert "DO_NOT_ECHO_SECRET" not in json.dumps(payload)
    with pytest.raises(ValueError):
        research.fetch(Client(), "promax", ["600519.SH"] * 3, "20260630", "20260911")
    assert len(calls) == 8


def test_cli_fixture_nonoverwrite_and_live_http_optin(tmp_path):
    source, output = tmp_path / "fixture.json", tmp_path / "output.json"
    source.write_text(json.dumps(fixture()))
    command = [sys.executable, str(SCRIPTS / "research_financial_enrichment.py"),
               "--source", "datahubco", "--fixture", str(source), "--output", str(output)]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0
    original = output.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode == 2
    assert output.read_bytes() == original
    live = subprocess.run([sys.executable, str(SCRIPTS / "research_financial_enrichment.py"),
                           "--source", "datahubco", "--live"], capture_output=True, text=True)
    assert live.returncode == 2 and json.loads(live.stdout)["status"] == "error"
