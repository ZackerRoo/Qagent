import importlib.util
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("collect_daily_documented_research", SCRIPTS / "collect_daily_documented_research.py")
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)
sys.path.pop(0)


def response(api, symbol):
    row = {"ts_code": symbol}
    if api in ("cashflow", "income"):
        row.update(end_date="20260630", ann_date="20260815", report_type="1", comp_type="1",
                   n_cashflow_act=30, n_income=10, total_revenue=100)
    elif api == "daily_basic":
        row.update(trade_date="20260911", pe=10, pe_ttm=10, pb=1, turnover_rate=1, volume_ratio=1)
    elif api == "forecast":
        row.update(end_date="20261231", ann_date="20260901", type="预增",
                   p_change_min=1, p_change_max=2, net_profit_min=3, net_profit_max=4)
    elif api == "moneyflow":
        row["trade_date"] = "20260911"
    elif api == "fina_indicator":
        row.update(end_date="20260630", ann_date="20260815", roe=10, netprofit_margin=20)
    elif api == "balancesheet":
        row.update(end_date="20260630", ann_date="20260815", report_type="1", comp_type="1",
                   total_assets=1000, total_liab=300)
    return {"source": "datahubco", "status": "observed", "rows": [row],
            "fetched_at": "2026-09-14T08:00:00+00:00", "decision_weight": False, "activation_allowed": False}


def query(base_url, **request):
    return response(request["api"], request["params"]["ts_code"])


def test_seven_api_batch_replay_and_digest():
    calls = []
    def record(base_url, **request):
        calls.append(request)
        return query(base_url, **request)
    report = batch.run_batch(["600519.SH", "603259.SH"], "20260630", "20260911", query=record)
    assert report["status"] == "observed" and len(calls) == 14
    assert {r["api"] for r in calls} == set(batch.BATCH_APIS)
    assert all(r["limit"] == 12 and "limit" not in r["params"] for r in calls)
    assert report["system_evidence"]["600519.SH"]["moneyflow"]["request"]["params"]["trade_date"] == "20260911"
    claimed = report.pop("result_digest")
    assert claimed == batch.digest(report)
    for enrichment in report["enrichment_reports"]:
        assert enrichment["raw_evidence"]["analysis_version"] == 2
        assert batch.analyze(enrichment["raw_evidence"]) == enrichment
        assert enrichment["retrieved_at"] == report["finished_at"]
        assert next(iter(enrichment["instruments"].values()))["financial_ratios"]["cashflow_to_netprofit"]["value"] == "3"


def test_errors_and_no_rows_independent_no_retry():
    calls = []
    def faulty(base_url, **request):
        calls.append(request["api"])
        if request["api"] == "forecast":
            raise ValueError("do-not-echo-secret")
        result = query(base_url, **request)
        if request["api"] == "moneyflow":
            result.update(status="no_rows", rows=[])
        return result
    report = batch.run_batch(["600519.SH"], "20260630", "20260911", query=faulty)
    assert report["status"] == "incomplete" and len(calls) == 7
    assert "do-not-echo" not in json.dumps(report)
    assert report["enrichment_reports"][0]["instruments"]["600519.SH"]["financial_ratios"]["cashflow_to_netprofit"]["value"] == "3"
    assert report["system_evidence"]["600519.SH"]["moneyflow"]["response"]["status"] == "no_rows"


def test_budget_records_unrequested_sections(monkeypatch):
    monkeypatch.setattr(batch.time, "monotonic", iter([0] + [2] * 7).__next__)
    report = batch.run_batch(["600519.SH"], "20260630", "20260911", budget_seconds=70,
                             query=lambda *a, **kw: pytest.fail("budget request"))
    assert all(item["response"]["error"] == "batch_budget_exhausted"
               for item in report["system_evidence"]["600519.SH"].values())


@pytest.mark.parametrize("symbols", [[], ["600519.SH"] * 2, [f"600{i:03}.SH" for i in range(21)], ["bad"]])
def test_invalid_universe_no_network(symbols):
    with pytest.raises(ValueError):
        batch.run_batch(symbols, "20260630", "20260911", query=lambda *a, **kw: pytest.fail("network"))


def test_lock_excludes_another_process(tmp_path):
    with batch.batch_lock(tmp_path):
        code = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path / ".daily-research.lock")], capture_output=True)
        assert result.returncode != 0
    with batch.batch_lock(tmp_path):
        pass


def test_lock_symlink_rejected(tmp_path):
    target = tmp_path / "unrelated"
    target.write_text("preserve")
    (tmp_path / ".daily-research.lock").symlink_to(target)
    with pytest.raises(OSError):
        with batch.batch_lock(tmp_path):
            pytest.fail("symlink acquired")
    assert target.read_text() == "preserve"


def test_moneyflow_invalid_identity_preserves_raw():
    def wrong(base_url, **request):
        result = query(base_url, **request)
        if request["api"] == "moneyflow":
            result["rows"][0]["ts_code"] = "600000.SH"
        return result
    report = batch.run_batch(["600519.SH"], "20260630", "20260911", query=wrong)
    evidence = report["system_evidence"]["600519.SH"]["moneyflow"]
    assert evidence["classification"] == "invalid_data"
    assert evidence["response"]["rows"][0]["ts_code"] == "600000.SH"
    assert report["status"] == "incomplete"
    assert report["financial_candidate"]["decision_weight"] is False


@pytest.mark.parametrize("api", ["fina_indicator", "balancesheet"])
@pytest.mark.parametrize("change", [{"ts_code": "600000.SH"}, {"end_date": "20260331"}])
def test_added_financial_rows_require_same_identity_period(api, change):
    def changed(base_url, **request):
        result = query(base_url, **request)
        if request["api"] == api:
            result["rows"][0].update(change)
        return result
    report = batch.run_batch(["600519.SH"], "20260630", "20260911", query=changed)
    evidence = report["system_evidence"]["600519.SH"][api]
    assert evidence["classification"] == "invalid_data"
    assert all(evidence["response"]["rows"][0][k] == v for k, v in change.items())
    assert report["status"] == "incomplete"


@pytest.mark.parametrize("count,classification", [(12, "observed"), (13, "invalid_data")])
def test_added_financial_page_limit_retains_evidence(count, classification):
    def paged(base_url, **request):
        result = query(base_url, **request)
        if request["api"] == "balancesheet":
            result["rows"] *= count
        return result
    report = batch.run_batch(["600519.SH"], "20260630", "20260911", query=paged)
    evidence = report["system_evidence"]["600519.SH"]["balancesheet"]
    assert evidence["classification"] == classification
    assert evidence["coverage"] == {"received_rows": count, "page_limit_reached": count == 12}
    assert len(evidence["response"]["rows"]) == count


def test_added_api_failure_does_not_cancel_other_requests():
    calls = []
    def failing(base_url, **request):
        calls.append(request["api"])
        if request["api"] == "fina_indicator":
            raise ValueError("private")
        return query(base_url, **request)
    report = batch.run_batch(["600519.SH"], "20260630", "20260911", query=failing)
    assert len(calls) == 7 and calls[-1] == "balancesheet"
    assert report["system_evidence"]["600519.SH"]["fina_indicator"]["classification"] == "error"
    assert report["system_evidence"]["600519.SH"]["balancesheet"]["classification"] == "observed"


def test_ranking_failure_does_not_discard_collection(monkeypatch):
    def fail(*a, **kw):
        raise ValueError("private-internal-error")
    monkeypatch.setattr(batch, "rank_candidate", fail)
    report = batch.run_batch(["600519.SH"], "20260630", "20260911", query=query)
    assert len(report["system_evidence"]["600519.SH"]) == 7
    assert report["financial_candidate"]["error"] == "candidate_failed"
    assert "private-internal-error" not in json.dumps(report)
    assert report["status"] == "incomplete"


def test_nonindustrial_company_retains_raw_but_not_candidate():
    def financial_company(base_url, **request):
        result = query(base_url, **request)
        if request["api"] in {"cashflow", "income", "balancesheet"}:
            result["rows"][0]["comp_type"] = "2"
        return result
    report = batch.run_batch(["600918.SH"], "20260630", "20260911", query=financial_company)
    assert report["system_evidence"]["600918.SH"]["balancesheet"]["response"]["rows"][0]["comp_type"] == "2"
    assert report["financial_candidate"]["status"] == "no_eligible_stocks"
    assert "600918.SH" in json.dumps(report["financial_candidate"])


def test_expected_financial_exclusion_does_not_fail_successful_collection(tmp_path, monkeypatch):
    def mixed(base_url, **request):
        result = query(base_url, **request)
        if request["params"]["ts_code"] == "600918.SH" and request["api"] in {"cashflow", "income", "balancesheet"}:
            result["rows"][0]["comp_type"] = "2"
        return result
    report = batch.run_batch(["600519.SH", "600918.SH"], "20260630", "20260911", query=mixed)
    assert report["status"] == "observed"
    assert report["financial_candidate"]["coverage"]["eligible_count"] == 1
    assert report["analysis_coverage"]["complete_reports"] == 1
    assert report["analysis_coverage"]["incomplete_reports"] == 1
    assert report["analysis_coverage"]["incomplete_instruments"] == ["600918.SH"]
    assert report["analysis_coverage"]["candidate_exclusions"][0]["stock_id"] == "600918.SH"
    assert report["enrichment_reports"][1]["status"] == "incomplete"
    monkeypatch.setattr(batch, "run_batch", lambda *a, **kw: report)
    monkeypatch.setattr(sys, "argv", ["batch", "--symbol", "600519.SH", "--symbol", "600918.SH",
        "--period", "20260630", "--trade-date", "20260911", "--output-dir", str(tmp_path)])
    assert batch.main() == 0


def test_explicit_twenty_stock_file_and_universe_digest(tmp_path, monkeypatch):
    symbols = [f"600{i:03}.SH" for i in range(20)]
    calls = []
    def record(base_url, **request):
        calls.append(request)
        return query(base_url, **request)
    report = batch.run_batch(symbols, "20260630", "20260911", query=record)
    assert len(calls) == 140
    assert report["protocol"] == "daily-documented-research-v2"
    assert batch.rank_candidate([report], symbols, top_k=5)["status"] == "ranked"
    assert len(report["system_evidence"]) == 20
    assert report["universe"]["digest"] == batch.digest({"symbols": symbols})
    stock_file = tmp_path / "stocks.json"
    stock_file.write_text(json.dumps(symbols))
    seen = []
    monkeypatch.setattr(batch, "run_batch", lambda *a, **kw: seen.append(a[0]) or report)
    monkeypatch.setattr(sys, "argv", ["batch", "--symbols-file", str(stock_file), "--period", "20260630",
                                     "--trade-date", "20260911", "--output-dir", str(tmp_path / "out")])
    assert batch.main() == 0 and seen == [symbols]


def test_main_immutable_private_archive(tmp_path, monkeypatch):
    report = batch.run_batch(["600519.SH"], "20260630", "20260911", query=query)
    monkeypatch.setattr(batch, "run_batch", lambda *a, **kw: report)
    monkeypatch.setattr(sys, "argv", ["batch", "--symbol", "600519.SH", "--period", "20260630",
                                     "--trade-date", "20260911", "--output-dir", str(tmp_path)])
    assert batch.main() == 0
    first = next(tmp_path.glob("*.json"))
    before = first.read_bytes()
    assert first.stat().st_mode & 0o777 == 0o400
    assert batch.main() == 0
    assert len(list(tmp_path.glob("*.json"))) == 2 and first.read_bytes() == before


@pytest.mark.parametrize("hour,expected", [(10, 2), (16, 1)])
def test_today_close_does_not_invent_previous_trading_day(tmp_path, monkeypatch, hour, expected):
    class Weekend(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 13, hour, tzinfo=tz)
    monkeypatch.setattr(batch, "datetime", Weekend)
    calls = []
    def collect(*args, **kwargs):
        calls.append(args)
        return {"status": "incomplete", "result_digest": "test"}
    monkeypatch.setattr(batch, "run_batch", collect)
    monkeypatch.setattr(sys, "argv", ["batch", "--symbol", "600519.SH", "--period", "20260630",
                                     "--today-close", "--output-dir", str(tmp_path)])
    assert batch.main() == expected
    if hour < 15:
        assert not calls
    else:
        assert calls[0][2] == "20260913"
