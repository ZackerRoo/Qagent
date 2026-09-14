"""Synthetic current observations, never historical or forward performance evidence."""
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
SPEC = importlib.util.spec_from_file_location("research_cashflow_quality", SCRIPTS / "research_cashflow_quality.py")
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
sys.path.pop(0)


def fixture():
    row = {"ts_code": "600519.SH", "end_date": "20260630", "ann_date": "20260815",
           "f_ann_date": "20260816", "report_type": "1", "comp_type": "1"}
    return {"retrieved_at": "2026-09-13T20:00:00+08:00", "instruments": {
        "600519.SH": {"cashflow": [{**row, "n_cashflow_act": 150}],
                      "income": [{**row, "n_income": 100, "n_income_attr_p": 80,
                                  "total_revenue": 300}]}}}


def result(payload):
    return research.analyze(payload)["instruments"]["600519.SH"]


def test_ratios_total_profit_not_parent_and_digest():
    payload = fixture()
    report = research.analyze(payload)
    row = result(payload)["rows"][0]
    assert row["ratios"] == {"operating_cashflow_to_netprofit": "1.5",
                             "operating_cashflow_to_total_revenue": "0.5"}
    assert report == research.analyze(deepcopy(payload))
    assert not report["decision_weight"] and not report["activation_allowed"]
    assert row["announcement_dates"]["income"] == ["2026-08-15", "2026-08-16"]


@pytest.mark.parametrize("value", [None, "NaN", "Infinity", 0, -10, True])
def test_invalid_profit_is_not_ratio(value):
    payload = fixture()
    payload["instruments"]["600519.SH"]["income"][0]["n_income"] = value
    row = result(payload)["rows"][0]
    assert row["ratios"]["operating_cashflow_to_netprofit"] is None
    assert row["ratio_exclusions"]["operating_cashflow_to_netprofit"]


@pytest.mark.parametrize("field,value", [("report_type", "2"), ("comp_type", "2"),
                                         ("f_ann_date", "20260914")])
def test_noncomparable_or_future_excluded(field, value):
    payload = fixture()
    payload["instruments"]["600519.SH"]["income"][0][field] = value
    assert result(payload)["rows"] == []


def test_ambiguous_consumed_revision_rejected_but_unused_is_allowed():
    payload = fixture()
    rows = payload["instruments"]["600519.SH"]["income"]
    rows.append({**rows[0], "unused": "different"})
    assert len(result(payload)["rows"]) == 1
    rows[1]["n_income"] = 101
    assert result(payload)["rows"] == []
    assert result(payload)["coverage"]["income"]["ambiguous_periods"] == ["2026-06-30"]


def test_periods_never_mixed_and_negative_cashflow_preserved():
    payload = fixture()
    payload["instruments"]["600519.SH"]["cashflow"][0]["n_cashflow_act"] = -50
    assert result(payload)["rows"][0]["ratios"]["operating_cashflow_to_netprofit"] == "-0.5"
    payload["instruments"]["600519.SH"]["income"][0]["end_date"] = "20260331"
    assert result(payload)["rows"] == []


def test_symbol_mismatch_and_page_budget():
    payload = fixture()
    rows = payload["instruments"]["600519.SH"]["cashflow"]
    rows[0]["ts_code"] = "000001.SZ"
    with pytest.raises(ValueError):
        result(payload)
    rows[:] = [rows[0]] * 13
    with pytest.raises(ValueError):
        result(payload)


def test_fetch_is_bounded_and_validates_before_requests():
    calls = []

    class Client:
        def query(self, api, **kwargs):
            calls.append((api, kwargs))
            return type("Table", (), {"rows": []})()

    now = datetime.fromisoformat("2026-09-13T20:00:00+08:00")
    research.fetch(Client(), ["600519.SH", "000001.SZ"], now)
    assert len(calls) == 4
    assert all(kwargs["limit"] == 12 for _, kwargs in calls)
    assert all(kwargs["report_type"] == "1" for _, kwargs in calls)
    with pytest.raises(ValueError):
        research.fetch(Client(), ["600519.SH", "000001.SZ", "300001.SZ"], now)
    assert len(calls) == 4


def test_cli_fixture_and_nonoverwrite_and_safe_errors(tmp_path):
    source, target = tmp_path / "fixture.json", tmp_path / "out.json"
    source.write_text(json.dumps(fixture()))
    command = [sys.executable, str(SCRIPTS / "research_cashflow_quality.py"),
               "--fixture", str(source), "--output", str(target)]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0
    original = target.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 1 and target.read_bytes() == original
    assert json.loads(second.stdout)["error"] == "cashflow_research_failed"
    source.write_text('{"secret": "NEVER_ECHO_THIS"}')
    failure = subprocess.run(command, capture_output=True, text=True)
    assert "NEVER_ECHO_THIS" not in failure.stdout + failure.stderr
