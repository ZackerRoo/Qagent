"""Focused safeguards for the one-shot G2 financial PIT evidence pilot."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import collect_g2_financial_pit_pilot as pilot  # noqa: E402
from qagent.factors.models import FactorRanking  # noqa: E402
from qagent.factors.research_contract import FEATURE_COLUMNS  # noqa: E402

DAY = "20261002"
PERIOD = "20260630"
REGISTERED = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
SYMBOLS = ["000001.SZ", "600519.SH"]


def setup(tmp_path, symbols=SYMBOLS, *, registered=REGISTERED):
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"kind": "scan_request_list", "signal_date": DAY,
                                    "symbols": symbols}))
    directory = tmp_path / "pilot"
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    pilot.register(directory, universe, DAY, PERIOD, source_dir, clock=lambda: registered)
    return directory


def response(request, fetched_at, *, status="observed", rows=None, error=None):
    if rows is None:
        symbol = request["params"]["ts_code"]
        if request["api"] == "fina_indicator":
            rows = [{"ts_code": symbol, "end_date": PERIOD, "ann_date": "20260930",
                     "tr_yoy": "18", "netprofit_yoy": "15", "grossprofit_margin": "35",
                     "netprofit_margin": "8", "roe": "12.0"}]
        else:
            rows = [{"ts_code": symbol, "trade_date": DAY, "total_mv": "100",
                     "pe": "10", "pe_ttm": "11", "pb": "2", "turnover_rate": "1",
                     "volume_ratio": "1.2"}]
    result = {"source": request["source"], "status": status, "rows": rows,
              "fetched_at": fetched_at.isoformat(), "decision_weight": False,
              "activation_allowed": False, "coverage": {"page_limit_reached": False},
              "request": {key: value for key, value in request.items() if key != "source"}}
    if error:
        result["error"] = error
    return pilot._sealed(result)


def source(tmp_path, *, cutoff=CUTOFF, universe=SYMBOLS):
    identities = sorted("CN:" + symbol[:6] for symbol in universe)
    rankings = [FactorRanking(
        instrument_id=identity, factor_score=.5, factor_rank=index + 1,
        percentile=.5, momentum_score=.5, trend_quality_score=.5,
        liquidity_score=.5, low_risk_score=.5, reversal_score=.5,
        execution_penalty=0, data_completeness=1,
        factor_exposures=[], research_features={feature: 1.0 for feature in FEATURE_COLUMNS},
    ).model_dump(mode="json") for index, identity in enumerate(identities)]
    job_id = "synthetic-source-for-pilot-tests"
    document = {"protocol": "g2-forward-source-v2",
                "stage": "ranking_finalized_before_job_completion", "provider": "free",
                "scan_job_id": job_id, "signal_date": "2026-10-02",
                "capture_started_at_utc": cutoff.isoformat(),
                "captured_at_utc": (cutoff + timedelta(minutes=1)).isoformat(),
                "research_universe": identities, "stock_ids": identities,
                "rankings": rankings,
                "items": [{"instrument_id": identity, "latest_trade_date": "2026-10-02"}
                          for identity in identities],
                "revision": None, "industries": {},
                "industry_evidence": {"source": "historical_industry_snapshots",
                                      "status": "revision_unavailable", "nonmissing_count": 0,
                                      "missing_count": len(identities),
                                      "missing_instrument_ids": identities},
                "source_sha256": {name: "0" * 64 for name in (
                    "research/g2_forward_source.py", "jobs/full_market.py",
                    "jobs/daily_scan.py", "factors/engine.py")},
                "decision_weight": False, "activation_allowed": False}
    document["source_digest"] = pilot.digest(document)
    path = tmp_path / "sources" / f"2026-10-02-{sha256(job_id.encode()).hexdigest()}.json"
    path.write_text(json.dumps(document))
    return path


def test_identity_and_budget_rejected_before_network(tmp_path):
    universe = tmp_path / "bad.json"
    universe.write_text(json.dumps({"kind": "scan_request_list", "signal_date": DAY,
                                    "symbols": ["000001.SZ"] * 21}))
    with pytest.raises(ValueError, match="invalid_cohort_size"):
        pilot.register(tmp_path / "pilot", universe, DAY, PERIOD, tmp_path,
                       clock=lambda: REGISTERED)
    universe.write_text(json.dumps({"kind": "scan_request_list", "signal_date": DAY,
                                    "symbols": ["159915.SZ"]}))
    with pytest.raises(ValueError, match="invalid_stock_id"):
        pilot.register(tmp_path / "pilot", universe, DAY, PERIOD, tmp_path,
                       clock=lambda: REGISTERED)
    assert not (tmp_path / "pilot").exists()


def test_bounded_requests_resume_and_checkpoints(tmp_path):
    symbols = [f"{i:06d}.SZ" for i in range(1, 21)]
    directory = setup(tmp_path, symbols)
    calls = []

    def query(_base, **request):
        calls.append(request)
        return response(request, REGISTERED)

    first = pilot.collect_pilot(directory, query=query, clock=lambda: REGISTERED)
    second = pilot.collect_pilot(directory, query=query, clock=lambda: REGISTERED)
    assert len(calls) == pilot.MAX_REQUESTS == 40
    assert first["result_digest"] == second["result_digest"]
    assert len(list(directory.glob("checkpoint-*.json"))) == 20
    assert all(pilot._check_seal(json.loads(path.read_text()))
               for path in directory.glob("checkpoint-*.json"))


def test_partial_503_has_no_retry_and_keeps_raw_error(tmp_path):
    directory = setup(tmp_path)
    calls = []

    def query(_base, **request):
        calls.append(request)
        if request["api"] == "daily_basic":
            return response(request, REGISTERED, status="error", rows=[],
                            error="upstream_pool_exhausted")
        return response(request, REGISTERED)

    pilot.collect_pilot(directory, query=query, clock=lambda: REGISTERED)
    result = pilot.bind_source(directory, source(tmp_path), clock=lambda: CUTOFF + timedelta(minutes=2))
    assert len(calls) == 4
    assert result["coverage"]["requested"] == 4
    assert result["coverage"]["valid_slots"] == 2
    assert result["classified"]["000001.SZ"]["daily_basic"]["reason"] == "upstream_pool_exhausted"
    assert result["entries"]["000001.SZ:daily_basic"]["response"]["status"] == "error"
    assert result["status"] == "partial"


def test_pit_cutoff_and_announcement_require_evidence(tmp_path):
    directory = setup(tmp_path)

    def query(_base, **request):
        if request["api"] == "daily_basic":
            return response(request, CUTOFF.replace(minute=1))
        answer = response(request, REGISTERED)
        answer["rows"][0]["ann_date"] = "20261002"  # same-day time unknown
        answer.pop("result_digest")
        return pilot._sealed(answer)

    pilot.collect_pilot(directory, query=query, clock=lambda: REGISTERED)
    result = pilot.bind_source(directory, source(tmp_path), clock=lambda: CUTOFF + timedelta(minutes=2))
    assert result["coverage"]["valid_slots"] == 0
    assert result["classified"]["000001.SZ"]["fina_indicator"]["reason"] == "announcement_outside_cutoff"
    assert result["classified"]["000001.SZ"]["daily_basic"]["reason"] == "fetched_after_capture_started"


def test_response_digest_tamper_fails_closed(tmp_path):
    directory = setup(tmp_path)
    pilot.collect_pilot(directory, query=lambda _base, **request: response(request, REGISTERED),
                        clock=lambda: REGISTERED)
    state = json.loads((directory / "state.json").read_text())
    state["entries"]["000001.SZ:fina_indicator"]["response"]["rows"][0]["roe"] = "999"
    state.pop("result_digest")
    os.chmod(directory / "state.json", 0o600)
    (directory / "state.json").write_text(json.dumps(pilot._sealed(state)))
    result = pilot.bind_source(directory, source(tmp_path), clock=lambda: CUTOFF + timedelta(minutes=2))
    assert result["classified"]["000001.SZ"]["fina_indicator"]["reason"] == "invalid_response_digest"
    assert pilot._check_seal(json.loads((directory / "bound-source.json").read_text()))


def test_registration_must_precede_natural_capture(tmp_path):
    directory = setup(tmp_path, registered=CUTOFF + timedelta(minutes=1))
    early_source = source(tmp_path)
    with pytest.raises(ValueError, match="registration_after_capture_started"):
        pilot.bind_source(directory, early_source, clock=lambda: CUTOFF + timedelta(minutes=2))


def test_reuses_matching_financial_archive_and_only_queries_missing_source(tmp_path):
    directory = setup(tmp_path)
    archive = {"protocol": "daily-documented-research-v2", "trade_date": DAY,
               "period": PERIOD, "started_at": REGISTERED.isoformat(),
               "system_evidence": {}}
    for symbol in SYMBOLS:
        request = pilot._request(symbol, "fina_indicator", pilot._check_seal(
            json.loads((directory / "registration.json").read_text())))
        archive["system_evidence"][symbol] = {"fina_indicator": {
            "request": request, "response": response(request, REGISTERED)}}
    archive = pilot._sealed(archive)
    path = tmp_path / "financial.json"
    path.write_text(json.dumps(archive))
    calls = []

    def query(_base, **request):
        calls.append(request)
        return response(request, REGISTERED)

    state = pilot.collect_pilot(directory, archive=path, query=query, clock=lambda: REGISTERED)
    assert len(calls) == 2
    assert all(item["api"] == "daily_basic" for item in calls)
    assert sum(entry["mode"] == "reused" for entry in state["entries"].values()) == 2
    result = pilot.bind_source(directory, source(tmp_path), clock=lambda: CUTOFF + timedelta(minutes=2))
    assert result["coverage"]["valid_slots"] == 4
    assert result["coverage"]["reused"] == 2


def test_time_budget_seals_unattempted_slots(tmp_path):
    directory = setup(tmp_path)
    now = [REGISTERED]
    calls = []

    def query(_base, **request):
        calls.append(request)
        now[0] = CUTOFF
        return response(request, REGISTERED)

    state = pilot.collect_pilot(directory, query=query, clock=lambda: now[0])
    assert len(calls) == 1
    assert sum(entry["mode"] == "excluded" for entry in state["entries"].values()) == 3
    result = pilot.bind_source(directory, source(tmp_path), clock=lambda: CUTOFF + timedelta(minutes=2))
    assert result["coverage"]["expected_slots"] == result["coverage"]["closed_slots"] == 4
    assert result["status"] == "partial"


def test_rejects_digest_valid_but_incomplete_g2_source(tmp_path):
    directory = setup(tmp_path)
    path = source(tmp_path)
    document = json.loads(path.read_text())
    document["rankings"] = []
    document.pop("source_digest")
    document["source_digest"] = pilot.digest(document)
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="source cohort mismatch"):
        pilot.bind_source(directory, path, clock=lambda: CUTOFF + timedelta(minutes=2))
    assert not (directory / "bound-source.json").exists()


def test_rejects_capture_order_and_unregistered_source_path(tmp_path):
    directory = setup(tmp_path)
    path = source(tmp_path)
    document = json.loads(path.read_text())
    document["captured_at_utc"] = (CUTOFF - timedelta(minutes=1)).isoformat()
    document.pop("source_digest")
    document["source_digest"] = pilot.digest(document)
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="source_capture_time_invalid"):
        pilot.bind_source(directory, path, clock=lambda: CUTOFF + timedelta(minutes=2))
    outside = tmp_path / path.name
    outside.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="source_path_mismatch"):
        pilot.bind_source(directory, outside, clock=lambda: CUTOFF + timedelta(minutes=2))


def test_register_requires_timezone_aware_clock(tmp_path):
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"kind": "scan_request_list", "signal_date": DAY,
                                    "symbols": SYMBOLS}))
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    with pytest.raises(ValueError, match="timezone_required"):
        pilot.register(tmp_path / "pilot", universe, DAY, PERIOD, source_dir,
                       clock=lambda: datetime(2026, 10, 1, 8))
    assert not (tmp_path / "pilot").exists()


def test_g2_five_feature_partial_and_marketcap_joint_coverage(tmp_path):
    directory = setup(tmp_path)

    def query(_base, **request):
        result = response(request, REGISTERED)
        if request["params"]["ts_code"] == "000001.SZ" and request["api"] == "fina_indicator":
            result["rows"][0]["netprofit_yoy"] = None
            result["rows"][0]["grossprofit_margin"] = None
        result.pop("result_digest")
        return pilot._sealed(result)

    pilot.collect_pilot(directory, query=query, clock=lambda: REGISTERED)
    result = pilot.bind_source(directory, source(tmp_path), clock=lambda: CUTOFF + timedelta(minutes=2))
    first = result["g2_feature_availability"]["000001.SZ"]
    assert sum(first.values()) == 3
    assert first == {"earnings_yield": True, "return_on_equity": True,
                     "gross_margin": False, "revenue_growth": True,
                     "earnings_growth": False}
    assert result["coverage"]["g2_feature_counts"] == {
        "earnings_yield": 2, "return_on_equity": 2, "gross_margin": 1,
        "revenue_growth": 2, "earnings_growth": 1}
    assert result["coverage"]["financial_and_marketcap_stocks"] == ["600519.SH"]
    assert result["g2_feature_map"]["earnings_yield"]["field"] == "pe_ttm"
    assert result["marketcap_map"]["transform"] == "positive total_mv * 10000 CNY"


def test_positive_static_pe_does_not_substitute_nonpositive_pe_ttm(tmp_path):
    directory = setup(tmp_path)

    def query(_base, **request):
        result = response(request, REGISTERED)
        if request["params"]["ts_code"] == "000001.SZ" and request["api"] == "daily_basic":
            result["rows"][0].update(pe="15", pe_ttm="-2")
        result.pop("result_digest")
        return pilot._sealed(result)

    pilot.collect_pilot(directory, query=query, clock=lambda: REGISTERED)
    result = pilot.bind_source(directory, source(tmp_path), clock=lambda: CUTOFF + timedelta(minutes=2))
    assert result["g2_feature_availability"]["000001.SZ"]["earnings_yield"] is False
    assert result["coverage"]["g2_feature_counts"]["earnings_yield"] == 1
    assert result["coverage"]["financial_and_marketcap_stocks"] == ["600519.SH"]
