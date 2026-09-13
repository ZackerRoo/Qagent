import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

SPEC = importlib.util.spec_from_file_location(
    "probe_tushare_relay", Path(__file__).resolve().parents[2] / "scripts/probe_tushare_relay.py"
)
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)
SECRET = "private-credential-never-print"


def response(body=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = body
    return result


def payload(minute=False):
    if minute:
        return {"code": 0, "data": {"fields": ["ts_code", "trade_time", "open", "high", "low", "close"],
                                    "items": [["000001.SZ", "2026-09-11 09:31:00", 10, 12, 9, 11]]}}
    return {"code": 0, "data": {"fields": ["ts_code", "trade_date", "open", "high", "low", "close", "adj_factor"],
                                "items": [["000001.SZ", "20260911", 10, 12, 9, 11, 1.2]]}}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    getter = Mock(side_effect=AssertionError("unmocked request"))
    monkeypatch.setattr(relay.requests, "get", getter)
    return getter


def test_missing_key(monkeypatch, capsys, no_network):
    monkeypatch.delenv("TUSHARE_RELAY_KEY", raising=False)
    assert relay.main(["--date", "20260911"]) == 2
    assert "blocked_missing_key" in capsys.readouterr().out
    no_network.assert_not_called()


def test_four_bounded_sequential_calls(monkeypatch, capsys, no_network):
    monkeypatch.setenv("TUSHARE_RELAY_KEY", SECRET)
    no_network.side_effect = [response(payload()), response(payload()), response(payload(True)), response(payload(True))]
    relay.main(["--date", "20260911"])
    output = capsys.readouterr().out
    assert SECRET not in output
    results = [json.loads(line) for line in output.splitlines()]
    assert [r["api"] for r in results] == list(relay.APIS)
    assert results[0]["status"] == results[2]["status"] == "sample_valid"
    assert "cannot_verify_realtime" in results[-1]["caveat"]
    assert no_network.call_count == 4
    for call, api in zip(no_network.call_args_list, relay.APIS):
        assert call.args == (f"{relay.BASE}/{api}",)
        assert call.kwargs["verify"] is True
        assert call.kwargs["allow_redirects"] is False
        assert call.kwargs["timeout"] == 30
        assert call.kwargs["params"]["limit"] == 5
        assert call.kwargs["params"]["__probe"] == 0
    params = no_network.call_args_list[2].kwargs["params"]
    assert params["start_date"] == "2026-09-11 09:30:00"
    assert params["end_date"] == "2026-09-11 15:00:00"


@pytest.mark.parametrize("http,status", [(401, "auth_error"), (403, "auth_error"), (429, "rate_limited"), (302, "redirect_blocked"), (503, "http_error"), (202, "pending_unverified")])
def test_http_failures(http, status, no_network):
    no_network.side_effect = None
    no_network.return_value = response({"message": SECRET}, http)
    result = relay.probe("daily", SECRET, "000001.SZ", "20260911")
    assert result["status"] == status
    assert SECRET not in json.dumps(result)
    no_network.return_value.json.assert_not_called()


def test_non_json_and_transport(no_network):
    no_network.side_effect = None
    no_network.return_value = response()
    no_network.return_value.json.side_effect = ValueError(SECRET)
    assert relay.probe("daily", SECRET, "000001.SZ", "20260911")["status"] == "non_json"
    no_network.side_effect = requests.RequestException(SECRET)
    result = relay.probe("daily", SECRET, "000001.SZ", "20260911")
    assert result["status"] == "transport_error"
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -1, 0, 99, SECRET, True])
def test_invalid_ohlc(price, no_network):
    body = payload(True)
    body["data"]["items"][0][2] = price
    no_network.side_effect = None
    no_network.return_value = response(body)
    result = relay.probe("stk_mins", SECRET, "000001.SZ", "20260911")
    assert result["status"] == "invalid_ohlc"
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("change,status", [("date", "date_mismatch"), ("symbol", "symbol_mismatch"), ("duplicate", "duplicate_rows"), ("code", "upstream_error"), ("empty", "no_rows")])
def test_data_validation(change, status, no_network):
    body = payload()
    if change == "date":
        body["data"]["items"][0][1] = "20260910"
    elif change == "symbol":
        body["data"]["items"][0][0] = SECRET
    elif change == "duplicate":
        body["data"]["items"].append(list(body["data"]["items"][0]))
    elif change == "code":
        body["code"] = SECRET
    else:
        body["data"]["items"] = []
    body["message"] = SECRET
    body["data"]["fields"].append(SECRET)
    for row in body["data"]["items"]:
        row.append(SECRET)
    no_network.side_effect = None
    no_network.return_value = response(body)
    result = relay.probe("daily", SECRET, "000001.SZ", "20260911")
    assert result["status"] == status
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("api,column,value,status", [
    ("daily", "open", 99, "invalid_ohlc"),
    ("adj_factor", "adj_factor", 0, "invalid_factor"),
    ("adj_factor", "adj_factor", float("nan"), "invalid_factor"),
    ("stk_mins", "trade_time", "20260911", "invalid_rows"),
])
def test_additional_quality_checks(api, column, value, status, no_network):
    body = payload(api == "stk_mins")
    body["data"]["items"][0][body["data"]["fields"].index(column)] = value
    no_network.side_effect = None
    no_network.return_value = response(body)
    assert relay.probe(api, SECRET, "000001.SZ", "20260911")["status"] == status


def test_time_alias_and_weekend_caveat(monkeypatch, no_network):
    from datetime import datetime

    class Sunday(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 13, 10, tzinfo=tz)

    monkeypatch.setattr(relay, "datetime", Sunday)
    body = payload(True)
    body["data"]["fields"][1] = "time"
    no_network.side_effect = None
    no_network.return_value = response(body)
    result = relay.probe("rt_min_daily", SECRET, "000001.SZ", "20260911")
    assert result["status"] == "date_mismatch"
    assert result["caveat"] == "weekend_cannot_verify_realtime"
    assert result["max_date"] == "20260911"
