import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from qagent.providers.datahubco import DatahubcoClient, DatahubcoError, DOCUMENTED_APIS, READ_APIS


def make_client(body=None, status=200, allow=True):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "datahubco.com"
        assert request.url.scheme == "http"
        assert request.method == "GET"
        assert request.headers["X-API-Key"] == "synthetic-secret"
        assert "synthetic-secret" not in str(request.url)
        return httpx.Response(status, json=body if body is not None else {
            "code": 0, "data": {"fields": ["close", "ts_code"], "items": [[12, "000001.SZ"]]}},
            headers={"Location": "http://evil.invalid"})

    return DatahubcoClient("synthetic-secret", allow_insecure_http=allow,
                          transport=httpx.MockTransport(handler)), calls


def test_catalogue_exactly_matches_document():
    # Reviewed names from the supplied 2026-08-27 overview; no private file dependency.
    expected = set("""daily weekly monthly pro_bar daily_basic new_share top_list top_inst
        pledge_detail pledge_stat margin margin_detail repurchase share_float block_trade
        stk_holdernumber moneyflow stk_holdertrade stk_limit hk_hold income balancesheet
        cashflow forecast express dividend fina_indicator fina_audit fina_mainbz disclosure_date
        fund_basic fund_company fund_nav fund_daily fund_div fund_portfolio fund_adj fut_basic
        trade_cal fut_daily fut_holding fut_wsr fut_settle index_daily opt_basic opt_daily cb_basic
        cb_issue cb_daily fx_obasic fx_daily index_basic index_weekly index_monthly index_weight
        index_dailybasic index_classify index_member_all hk_basic shibor shibor_quote shibor_lpr
        libor hibor wz_index gz_index tmt_twincome tmt_twincomedetail bo_monthly bo_weekly bo_daily
        bo_cinema film_record teleplay_record report_rc cyq_perf cyq_chips stk_rewards stk_factor_pro
        stk_nineturn""".split())
    assert DOCUMENTED_APIS == expected | {"stock_basic"}
    assert len(DOCUMENTED_APIS) == 81 and len(READ_APIS) == 80


def test_header_identity_dynamic_table_and_zero_empty():
    client, calls = make_client()
    table = client.query("daily", ts_code="000001.SZ", trade_date="20260911", limit=2)
    assert table.rows == ({"close": 12, "ts_code": "000001.SZ"},)
    assert table.source == "datahubco"
    assert calls[0].url.path == "/app-api/openapi/v1/tushare/daily"
    assert "synthetic-secret" not in repr(client)
    client, _ = make_client({"code": 0, "data": {"fields": [], "items": []}})
    assert client.query("daily", limit=0).rows == ()


def test_http_gate_prevents_any_request():
    client, calls = make_client(allow=False)
    with pytest.raises(DatahubcoError, match="insecure_http_not_authorized"):
        client.query("daily")
    assert calls == []


@pytest.mark.parametrize("api", ["pro_bar", "stock-basic", "../daily", "p_save", "delete", "https://evil"])
def test_noncallable_routes_fail_before_request(api):
    client, calls = make_client()
    with pytest.raises(DatahubcoError):
        client.query(api)
    assert calls == []


def test_stock_basic_uses_documented_hyphen_endpoint_and_bounded_query():
    client, calls = make_client({"code": 0, "data": {
        "fields": ["ts_code", "industry"], "items": [["000001.SZ", "银行"]]}})
    table = client.query("stock_basic", ts_code="000001.SZ", fields="ts_code,industry", limit=2, offset=0)
    assert table.rows == ({"ts_code": "000001.SZ", "industry": "银行"},)
    assert calls[0].url.path.endswith("/stock-basic")
    assert dict(calls[0].url.params) == {
        "ts_code": "000001.SZ", "fields": "ts_code,industry", "limit": "2", "offset": "0"}


@pytest.mark.parametrize("params", [
    {"offset": 1}, {"offset": -1, "limit": 1}, {"limit": 5001}, {"limit": True},
    {"start_date": "20260101"}, {"end_date": "20260911"},
    {"start_date": "20260101", "end_date": "20260911", "trade_date": "20260911"},
    {"start_date": "20260911", "end_date": "20260101"}, {"trade_date": "2026-09-11"},
    {"trade_date": "20260230"}, {"fields": ["close"]}, {"fields": "close,close"},
    {"fields": "close, ts_code"}, {"fields": "trade-date"}, {"token": "something"},
    {"ts_code": "synthetic-secret"}, {"ts_code": ["000001.SZ"]}, {"value": float("nan")},
])
def test_invalid_params_are_local(params):
    client, calls = make_client()
    with pytest.raises(DatahubcoError):
        client.query("daily", **params)
    assert not calls


def test_month_and_timestamp_exceptions():
    client, calls = make_client()
    client.query("teleplay_record", start_date="202601", end_date="202609")
    client.query("stk_nineturn", start_date="2026-09-11 09:30:00", end_date="2026-09-11 15:00:00")
    assert len(calls) == 2


def test_later_pagination_offset():
    client, calls = make_client()
    client.query("daily", limit=5000, offset=10000)
    assert calls[0].url.params["offset"] == "10000"


@pytest.mark.parametrize("body", [
    {"code": False, "msg": "synthetic-secret"}, {"code": 1, "msg": "synthetic-secret"},
    {"code": 0, "data": {"fields": ["a", "a"], "items": []}},
    {"code": 0, "data": {"fields": ["a"], "items": [[1, 2]]}},
    {"code": 0, "data": {"fields": ["a"], "items": [["synthetic-secret"]]}},
])
def test_bad_schema_and_secret_echo_fail_safely(body):
    client, _ = make_client(body)
    with pytest.raises(DatahubcoError) as caught:
        client.query("daily")
    assert "synthetic-secret" not in str(caught.value)


@pytest.mark.parametrize("status", [301, 302, 403, 502])
def test_no_redirect_retry_or_remote_error_echo(status):
    client, calls = make_client({"msg": "synthetic-secret"}, status)
    with pytest.raises(DatahubcoError) as caught:
        client.query("daily")
    assert caught.value.status_code == status
    assert len(calls) == 1 and "synthetic-secret" not in str(caught.value)


def test_environment_proxy_is_used(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    routes = []

    class Client(httpx.Client):
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 30 and kwargs["follow_redirects"] is False
            super().__init__(**kwargs)

        def _init_transport(self, **kwargs):
            return httpx.MockTransport(lambda _: pytest.fail("unexpected direct route"))

        def _init_proxy_transport(self, proxy, **kwargs):
            routes.append(str(proxy.url))
            return httpx.MockTransport(lambda _: httpx.Response(200, json={
                "code": 0, "data": {"fields": [], "items": []}}))

    monkeypatch.setattr(httpx, "Client", Client)
    assert DatahubcoClient("synthetic-secret", allow_insecure_http=True).query("daily").rows == ()
    assert routes == ["http://proxy.invalid:8080"]


def test_research_artifact_and_cli_gate(tmp_path):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("collect_datahubco_research", scripts / "collect_datahubco_research.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    client, _ = make_client()
    report = module.collect(client, "daily", {"limit": 5})
    assert report["query_params"] == {"limit": 5}
    assert report["source"] == "datahubco" and not report["activation_allowed"]
    target = tmp_path / "out.json"
    module.publish(target, json.dumps(report))
    with pytest.raises(FileExistsError):
        module.publish(target, "replacement")
    result = subprocess.run([sys.executable, str(scripts / "collect_datahubco_research.py"),
                             "--api", "daily"], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"] == "insecure_http_not_authorized"
