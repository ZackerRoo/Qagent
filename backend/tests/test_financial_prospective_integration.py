from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from test_financial_challenger_forward import matched_inputs, signed, NOW, SCRIPTS, forward
from test_run_financial_forward_research import paths, runner, RUN  # noqa: F401


def prospective(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import financial_industry_evidence as industry
    symbols, document, baseline = matched_inputs(monkeypatch)
    raw = {}
    for symbol in symbols:
        request = industry.industry_request(symbol)
        response = signed({
            "source": "datahubco", "data_source": "datahubco", "status": "observed",
            "request": {k: v for k, v in request.items() if k != "source"},
            "fetched_at": document["started_at"], "fields": ["ts_code", "industry"],
            "rows": [{"ts_code": symbol, "industry": "vendor"}], "research_only": True,
            "decision_weight": False, "activation_allowed": False,
            "coverage": {"rows": 1, "row_limit": 2, "page_limit_reached": False}})
        raw[symbol] = {"request": request, "response": response, "received_at": document["finished_at"]}
    document["prospective_contract"] = forward.PROSPECTIVE_CONTRACT
    document["industry_evidence"] = industry.build_industry_evidence(symbols, raw, "20260911")
    return signed(document), baseline


def test_new_contract_uses_vendor_industry_and_only_isolated_ranks(monkeypatch):
    document, baseline = prospective(monkeypatch)
    assert forward.seal(document, baseline, now=NOW)["baseline_status"] == "baseline_unavailable"
    import financial_daily_baseline as daily
    calls = []
    def validate(value, **kwargs):
        calls.append(kwargs)
        return value["predictions"]
    monkeypatch.setattr(daily, "validate_archive", validate)
    signal = forward.seal(document, baseline, now=NOW)
    assert signal["protocol"] == "financial-rule-forward-v4"
    assert signal["control_status"] == "available"
    assert all(pair["industry"] == "vendor" for pair in signal["matched_control_pairs"])
    assert calls[0]["eligible_ids"] == {row["instrument_id"] for row in signal["rankings"]}
    forward.validate_archive(signal)
    legacy = forward._seal(
        document, baseline, now=NOW, archive_protocol="financial-rule-forward-v3")
    assert legacy["protocol"] == "financial-rule-forward-v3"
    forward.validate_archive(legacy)


def test_new_contract_cannot_fallback_to_candidate_pool_industry(monkeypatch):
    document, baseline = prospective(monkeypatch)
    import financial_industry_evidence as industry
    import financial_daily_baseline as daily
    monkeypatch.setattr(daily, "validate_archive", lambda value, **kwargs: value["predictions"])
    raw = deepcopy(document["industry_evidence"]["raw_evidence"])
    raw[document["symbols"][0]]["response"]["status"] = "no_rows"
    document["industry_evidence"] = industry.build_industry_evidence(document["symbols"], raw, "20260911")
    signal = forward.seal(signed(document), baseline, now=NOW)
    assert signal["control_status"] == "control_unavailable"
    assert "industry_incomplete" in signal["control_reasons"]


def test_v4_vendor_industry_singleton_still_has_zero_pairs(monkeypatch):
    document, baseline = prospective(monkeypatch)
    import financial_daily_baseline as daily
    import financial_industry_evidence as industry
    raw = deepcopy(document["industry_evidence"]["raw_evidence"])
    first = document["symbols"][0]
    raw[first]["response"]["rows"] = [{"ts_code": first, "industry": "singleton"}]
    signed(raw[first]["response"])
    document["industry_evidence"] = industry.build_industry_evidence(document["symbols"], raw, "20260911")
    monkeypatch.setattr(daily, "validate_archive", lambda value, **kwargs: value["predictions"])
    signal = forward.seal(signed(document), baseline, now=NOW)
    assert signal["protocol"] == "financial-rule-forward-v4"
    assert signal["control_status"] == "control_unavailable"
    assert signal["control_reasons"] == ["same_industry_control_unavailable"]
    assert signal["matched_control_pairs"] == []


def test_new_contract_rejects_industry_before_daily_capture(monkeypatch):
    document, _ = prospective(monkeypatch)
    import financial_industry_evidence as industry
    raw = document["industry_evidence"]["raw_evidence"]
    entry = raw[document["symbols"][0]]
    entry["response"]["fetched_at"] = "2026-09-11T16:00:00+08:00"
    signed(entry["response"])
    document["industry_evidence"] = industry.build_industry_evidence(document["symbols"], raw, "20260911")
    with pytest.raises(ValueError, match="industry_capture_time_order"):
        forward.seal(signed(document), now=NOW)


def test_collector_keeps_raw_universe_and_shares_request_budget(monkeypatch):
    document, _ = prospective(monkeypatch)
    import collect_daily_documented_research as batch
    original = deepcopy(document["universe"])
    calls = []
    def query(base_url, **request):
        calls.append(request)
        symbol = request["params"]["ts_code"]
        if request["api"] == "stock_basic":
            return document["industry_evidence"]["raw_evidence"][symbol]["response"]
        return document["system_evidence"][symbol][request["api"]]["response"]
    result = batch.run_batch(document["symbols"], "20260630", "20260911", query=query,
                             universe=original, daily_frozen_industry=True)
    assert len(calls) == len(document["symbols"]) * 8
    assert result["industry_evidence"]["status"] == "available"
    assert result["universe"] == original == document["universe"]
    def industry_failure(base_url, **request):
        if request["api"] == "stock_basic":
            return {"status": "error", "error": "transport_error", "rows": []}
        return query(base_url, **request)
    incomplete = batch.run_batch(document["symbols"], "20260630", "20260911", query=industry_failure,
                                 universe=original, daily_frozen_industry=True)
    assert incomplete["status"] == "incomplete"
    assert incomplete["industry_evidence"]["status"] == "unavailable"
    assert incomplete["financial_candidate"]["status"] == "ranked"
    calls.clear()
    clock = iter([0] + [531] * 100)
    monkeypatch.setattr(batch.time, "monotonic", lambda: next(clock))
    result = batch.run_batch(document["symbols"], "20260630", "20260911", query=query,
                             universe=original, daily_frozen_industry=True)
    assert not calls
    assert result["industry_evidence"]["status"] == "unavailable"
    assert result["status"] == "incomplete"


def test_waiting_baseline_does_not_seal_and_can_retry(paths, monkeypatch):  # noqa: F811
    document, baseline = prospective(monkeypatch)
    daily_dir, signal_dir, evaluations, runs, db = paths
    (daily_dir / "daily.json").write_text(json.dumps(document))
    import financial_daily_baseline as daily
    monkeypatch.setattr(daily, "collect", lambda *a, **kw: None)
    options = dict(daily_baseline_source_dir=daily_dir, daily_baseline_frozen_dir=daily_dir,
                   daily_baseline_rank_dir=runs / "ranks")
    code, report = runner.run(*paths, now=RUN, **options)
    assert code == 75 and report["seal"]["status"] == "waiting_for_baseline"
    assert not list(signal_dir.glob("*.json"))
    completed = RUN + timedelta(seconds=30)
    baseline["collected_at_utc"] = completed.isoformat()
    monkeypatch.setattr(daily, "collect", lambda *a, **kw: baseline)
    monkeypatch.setattr(daily, "validate_archive", lambda value, **kw: value["predictions"])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return completed.astimezone(tz)
    monkeypatch.setattr(runner, "datetime", Clock)
    code, report = runner.run(*paths, now=RUN, **options)
    assert code == 0 and report["seal"]["status"] == "sealed", report
    saved = (signal_dir / "2026-09-11.json").read_bytes()
    code, report = runner.run(*paths, now=RUN, **options)
    assert report["seal"]["status"] == "already_sealed"
    assert (signal_dir / "2026-09-11.json").read_bytes() == saved


def test_waiting_baseline_still_evaluates_archived_signals(paths, monkeypatch):  # noqa: F811
    document, _ = prospective(monkeypatch)
    daily_dir, signal_dir, evaluations, runs, db = paths
    legacy = deepcopy(document)
    legacy.pop("prospective_contract")
    legacy.pop("industry_evidence")
    old_signal = runner.forward.seal(signed(legacy), now=NOW)
    signal_dir.mkdir()
    (signal_dir / "archived.json").write_text(json.dumps(old_signal))
    (daily_dir / "daily.json").write_text(json.dumps(document))
    code, report = runner.run(*paths, now=RUN)
    assert code == 0
    assert report["seal"]["status"] == "waiting_for_baseline"
    assert len(report["evaluations"]) == 1
    assert len(report["evaluations"][0]["horizons"]) == 3
    assert not (signal_dir / "2026-09-11.json").exists()


def test_baseline_timeout_not_swallowed_as_input_error(paths, monkeypatch):  # noqa: F811
    document, _ = prospective(monkeypatch)
    (paths[0] / "daily.json").write_text(json.dumps(document))
    import financial_daily_baseline as daily
    def timed_out(*args, **kwargs):
        raise runner.RunBudgetExceeded("run_budget_exhausted")
    monkeypatch.setattr(daily, "collect", timed_out)
    with pytest.raises(runner.RunBudgetExceeded):
        runner.run(*paths, now=RUN, daily_baseline_source_dir=paths[0],
                   daily_baseline_frozen_dir=paths[0], daily_baseline_rank_dir=paths[3])
    assert not list(paths[1].glob("*.json"))


def test_synthetic_real_frozen_collection_to_v4_seal_and_replay(tmp_path, monkeypatch):
    """Actual frozen inference integration; synthetic inputs are not operating evidence."""
    document, _ = prospective(monkeypatch)
    import financial_daily_baseline as daily
    from test_g2_risk_feature_forward import FROZEN, make_source, ranking
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    initial = make_source(source_dir)
    source = json.loads(initial.read_text())
    ids = ["CN:" + symbol[:6] for symbol in document["symbols"]]
    rows = []
    for index, instrument_id in enumerate(ids):
        row = ranking(index).model_dump(mode="json")
        row["instrument_id"] = instrument_id
        rows.append(row)
    source.update(rankings=rows, research_universe=ids, stock_ids=ids,
                  items=[{"instrument_id": key, "latest_trade_date": "2026-09-11"} for key in ids])
    source.pop("source_digest")
    source["source_digest"] = daily.digest(source)
    (source_dir / "2026-09-11-synthetic.json").write_text(json.dumps(source))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(daily, "datetime", Clock)
    baseline = daily.collect(source_dir, FROZEN, tmp_path / "ranks",
                             signal_date=NOW.date(), eligible_ids=ids,
                             now=NOW - timedelta(minutes=5))
    assert baseline is not None
    assert baseline["source"]["scan_job_id"] == "synthetic-not-operational-evidence"
    assert len(daily.validate_archive(baseline, signal_date=NOW.date(), eligible_ids=ids)) == 10
    signal = forward.seal(document, baseline, now=NOW)
    assert signal["protocol"] == "financial-rule-forward-v4"
    assert signal["baseline_status"] == "available"
    assert signal["control_status"] == "available"
    assert len(signal["matched_control_pairs"]) == 5
    forward.validate_archive(signal)
    archived = json.loads((tmp_path / "ranks" / "2026-09-11.json").read_text())
    assert archived == signal["baseline_source"]
