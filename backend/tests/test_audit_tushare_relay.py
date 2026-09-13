import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

SPEC = importlib.util.spec_from_file_location("audit_relay", Path(__file__).resolve().parents[2] / "scripts/audit_tushare_relay.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
SECRET = "test-private-key"


def response(body, status=200, headers=None):
    return Mock(status_code=status, headers=headers or {}, json=Mock(return_value=body))


def entry(name="daily", **kwargs):
    return {"name": name, "enabled": True, "methods": ["GET"], "required": [], "required_any": [], **kwargs}


@pytest.fixture(autouse=True)
def network(monkeypatch):
    getter = Mock(side_effect=AssertionError("network must be mocked"))
    monkeypatch.setattr(audit.requests, "get", getter)
    monkeypatch.setattr(audit.time, "sleep", Mock())
    monkeypatch.setenv("TUSHARE_RELAY_KEY", SECRET)
    return getter


def test_observed_catalogue_shape_and_exclusions():
    entries = audit.catalogue({"count": 6, "probe": {}, "interfaces": [
        entry(), entry("p_get"), entry("stk_account"), entry("fund_portfolio"),
        entry("disabled", enabled=False), entry("mutation", methods=["GET", "POST"])]})
    assert len(entries) == 6
    assert [e["api"] for e in entries if audit.safe_get(e)] == ["daily", "fund_portfolio"]


def test_only_exact_public_fund_portfolio_exception():
    examples = [entry("fund_portfolio"), entry("fund_portfolio_save"),
                entry("p_get"), entry("user_portfolio"),
                entry("fund_portfolio", enabled=False), entry("fund_portfolio", methods=["POST"])]
    assert [audit.safe_get(audit.catalogue([e])[0]) for e in examples] == [True, False, False, False, False, False]


def test_probe_and_business_are_separate(network, capsys):
    body = {"code": 0, "data": {"fields": ["ts_code"], "items": []}, "message": SECRET}
    network.side_effect = [response({"interfaces": [entry()]}), response(body)]
    assert audit.main(["--catalog", "--probe-all"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["checks"][0]["status"] == "no_rows"
    assert report["checks"][0]["mode"] == "local_probe"
    assert network.call_args.kwargs["params"] == {"__probe": 1, "limit": 5}
    assert SECRET not in json.dumps(report)
    assert audit.time.sleep.call_args.args[0] <= 1.2


def test_retry_after_and_secure_transport(network):
    network.side_effect = [response({"error": "rate_limited", "message": SECRET}, 429, {"Retry-After": "7"}), response({"interfaces": [entry()]})]
    result, _ = audit.Client(SECRET).get("/capabilities")
    assert result["attempts"] == 2
    assert any(c.args == (7.0,) for c in audit.time.sleep.call_args_list)
    for call in network.call_args_list:
        assert call.kwargs["verify"] is True
        assert call.kwargs["allow_redirects"] is False
        assert call.kwargs["timeout"] == 30


@pytest.mark.parametrize("status,error", [(503, "data_source_unavailable"), (302, SECRET), (401, SECRET)])
def test_no_retry_permanent_errors_no_leak(network, status, error):
    network.side_effect = None
    network.return_value = response({"error": error, "message": SECRET}, status)
    result, body = audit.Client(SECRET).get("/capabilities")
    assert network.call_count == 1
    assert body is None
    assert SECRET not in json.dumps(result)


def test_defer_long_retry_after(network):
    network.side_effect = None
    network.return_value = response({}, 429, {"Retry-After": "120"})
    result, _ = audit.Client(SECRET).get("/capabilities")
    assert result["retry"] == "deferred_retry_after"
    assert network.call_count == 1


def test_transport_retry_bounded(network):
    network.side_effect = requests.RequestException(SECRET)
    result, _ = audit.Client(SECRET).get("/capabilities")
    assert network.call_count == 3
    assert result == {"status": "transport_error", "attempts": 3}


def test_pool_exhausted_whitelisted_without_raw_message(network):
    network.side_effect = None
    network.return_value = response({"error": "upstream_pool_exhausted", "message": SECRET}, 503)
    result, body = audit.Client(SECRET).get("/pro/a_share_mins")
    assert result["error"] == "upstream_pool_exhausted"
    assert result["status"] == "http_error"
    assert result["attempts"] == 3
    assert body is None
    assert SECRET not in json.dumps(result)


def test_requirements_and_documented_minute_parameters():
    e = audit.catalogue([entry(required=["ts_code"], required_any=[["start_date", "end_date"], ["trade_date"]])])[0]
    suite = audit.formal_suite("000001.SZ", "20260911", "20251231")
    assert audit.requirements_met(e, suite["daily"])
    assert not audit.requirements_met(e, {"ts_code": "000001.SZ", "start_date": "20260911"})
    assert suite["a_share_mins"]["start_date"] == "2026-09-11 09:30:00"
    assert suite["a_share_mins"]["__probe"] == 0
    assert "start_date" not in suite["rt_min"]


@pytest.mark.parametrize("body,expected", [
    ({"code": 0, "data": {"fields": ["x"], "items": [[1]]}}, "valid_shape"),
    ({"code": 0, "data": {"fields": ["x"], "items": []}}, "no_rows"),
    ({"code": 0, "data": {"fields": ["x"], "items": [[1, 2]]}}, "invalid_rows"),
    ({"code": True}, "upstream_error"),
])
def test_shape_does_not_claim_business_verification(body, expected):
    assert audit.shape(body)["status"] == expected


def test_formal_price_validation_and_disabled_gate(network, capsys):
    body = {"code": 0, "data": {"fields": ["ts_code", "trade_date", "open", "high", "low", "close"],
                                "items": [["000001.SZ", "20260911", 10, 12, 9, 11]]}}
    network.side_effect = [response({"interfaces": [entry(), entry("adj_factor", enabled=False)]}), response(body)]
    assert audit.main(["--formal", "--date", "20260911"]) == 1
    checks = json.loads(capsys.readouterr().out)["checks"]
    assert checks[0]["status"] == "business_sample_valid"
    assert checks[1]["status"] == "catalogue_gate_blocked"
    assert network.call_args.kwargs["params"]["__probe"] == 0
    assert network.call_count == 2


@pytest.mark.parametrize("path", ["//other.example", "/pro/../capabilities", "/pro/p_get?token=x", "http://example.com"])
def test_invalid_paths_never_send_credentials(network, path):
    result, _ = audit.Client(SECRET).get(path)
    assert result["status"] == "invalid_path"
    network.assert_not_called()
