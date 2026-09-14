from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

from qagent.config import Settings
from qagent.providers.status import build_provider_status
from qagent.providers.tushare_relay import RelayError, TushareRelayClient
from qagent.strategy_data.providers import build_strategy_data_provider
from qagent.providers.tushare_relay_research import (
    TushareRelayStrategyDataProvider,
    _today,
    build_tushare_relay_research_provider,
)


def make_client(data=None, *, entries=None, handler=None, retries=0):
    calls = []
    entries = entries or [dict(api_name=name, enabled=True, methods=["GET"],
                              required=[], required_any=["ts_code", "trade_date"])
                          for name in ["daily", "adj_factor", "daily_basic", "fina_indicator"]]

    def route(request):
        calls.append(request)
        assert request.url.scheme == "https"
        assert request.url.host == "pcd.mobcvb.cn"
        assert request.headers["X-API-Key"] == "secret-relay-key"
        assert "secret-relay-key" not in str(request.url)
        if request.url.path.endswith("capabilities"):
            return httpx.Response(200, json={"count": len(entries), "interfaces": entries})
        if handler:
            return handler(request)
        return httpx.Response(200, json=data or {"code": 0, "data": {"fields": [], "items": []}})

    return TushareRelayClient("secret-relay-key", transport=httpx.MockTransport(route),
                              sleep=lambda _: None, retries=retries), calls


def test_dynamic_fields_and_business_mode():
    client, calls = make_client({"code": 0, "data": {
        "fields": ["close", "ts_code"], "items": [[12, "000001.SZ"]]}})
    result = client.query("daily", ts_code="000001.SZ", limit=5)
    assert result.rows == ({"close": 12, "ts_code": "000001.SZ"},)
    assert calls[-1].url.params["__probe"] == "0"
    assert "secret-relay-key" not in repr(client)
    assert len(calls) == 2
    catalogue = client.capabilities()
    catalogue["daily"]["enabled"] = False
    assert client.capabilities()["daily"]["enabled"] is True


@pytest.mark.parametrize("bypass", [False, True])
def test_environment_https_proxy_and_no_proxy_are_respected(monkeypatch, bypass):
    """Exercise HTTPX environment routing with in-memory transports, never a socket."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    if bypass:
        monkeypatch.setenv("NO_PROXY", "pcd.mobcvb.cn")
    routes = []

    def transport(route):
        def respond(request):
            routes.append(route)
            assert request.url.scheme == "https"
            assert request.url.host == "pcd.mobcvb.cn"
            assert request.headers["X-API-Key"] == "secret-relay-key"
            assert "secret-relay-key" not in str(request.url)
            return httpx.Response(200, json={"interfaces": []})
        return httpx.MockTransport(respond)

    class RoutedClient(httpx.Client):
        def __init__(self, **kwargs):
            assert kwargs["verify"] is True
            assert kwargs["follow_redirects"] is False
            assert kwargs["timeout"] == 5
            super().__init__(**kwargs)

        def _init_transport(self, **kwargs):
            return transport("direct")

        def _init_proxy_transport(self, proxy, **kwargs):
            assert str(proxy.url) == "http://proxy.invalid:8080"
            assert kwargs["verify"] is True
            return transport("proxy")

    monkeypatch.setattr(httpx, "Client", RoutedClient)
    client = TushareRelayClient("secret-relay-key", timeout_seconds=5, retries=0,
                               sleep=lambda _: None)
    assert client.capabilities() == {}
    assert routes == ["direct" if bypass else "proxy"]


def test_explicit_mock_transport_remains_isolated_with_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    client, calls = make_client()
    assert client.query("daily", ts_code="000001.SZ").rows == ()
    assert len(calls) == 2


@pytest.mark.parametrize("api", ["p_list", "p_get", "p_save", "p_delete", "../daily",
                                 "https://evil.com", "order", "order_create"])
def test_forbidden_routes_never_send_auth(api):
    client, calls = make_client()
    with pytest.raises(RelayError):
        client.query(api, ts_code="000001.SZ")
    assert not calls


@pytest.mark.parametrize("params", [{"token": "x"}, {"api_key": "x"},
    {"ts_code": "secret-relay-key"}, {"limit": 0}, {"limit": 5001},
    {"offset": -1}, {"fields": "close,close"}, {"fields": "bad-field"},
    {"ts_code": ["000001.SZ"]}, {"ts_code": ""}])
def test_invalid_query_is_local(params):
    client, calls = make_client()
    with pytest.raises(RelayError):
        client.query("daily", **params)
    assert not calls


@pytest.mark.parametrize("body", [
    {"code": 0, "data": {"fields": ["x", "x"], "items": [[1, 2]]}},
    {"code": 0, "data": {"fields": ["x"], "items": [[1, 2]]}},
    {"code": 0, "data": {"fields": ["x"], "items": "bad"}},
    {"data": {"fields": [], "items": []}},
    {"code": False, "data": {"fields": [], "items": []}},
    {"code": 2002, "msg": "secret-relay-key"},
])
def test_schema_or_upstream_errors_redact(body):
    client, _ = make_client(body)
    with pytest.raises(RelayError) as exc:
        client.query("daily", ts_code="000001.SZ")
    assert "secret-relay-key" not in str(exc.value)


def test_empty_is_success_not_missing_auth():
    client, _ = make_client()
    assert client.query("daily", ts_code="000001.SZ").rows == ()


def test_public_fund_holdings_are_not_customer_portfolio_management():
    client, calls = make_client(entries=[dict(name="fund_portfolio", enabled=True,
        methods=["GET"], required_any=[["ts_code"], ["period"]])])
    assert client.query("fund_portfolio", ts_code="110011.OF", period="20260630").rows == ()
    assert calls[-1].url.path == "/tushare/pro/fund_portfolio"


@pytest.mark.parametrize("status", [202, 301, 302, 401, 403, 404, 500])
def test_http_states_fail_without_redirects(status):
    client, calls = make_client(handler=lambda r: httpx.Response(
        status, headers={"Location": "https://evil.com"}, text="secret-relay-key"))
    with pytest.raises(RelayError) as exc:
        client.query("daily", ts_code="000001.SZ")
    assert len(calls) == 2
    assert exc.value.status_code == status
    assert "secret-relay-key" not in str(exc.value)


def test_bounded_retry_and_server_defer():
    client, calls = make_client(retries=2, handler=lambda r: httpx.Response(503, json={}))
    with pytest.raises(RelayError):
        client.query("daily", ts_code="000001.SZ")
    assert len(calls) == 4
    client, calls = make_client(retries=2, handler=lambda r: httpx.Response(
        429, headers={"Retry-After": "60"}, json={}))
    with pytest.raises(RelayError, match="retry_deferred"):
        client.query("daily", ts_code="000001.SZ")
    assert len(calls) == 2


def test_disabled_source_does_not_retry():
    client, calls = make_client(retries=2, handler=lambda r: httpx.Response(
        503, json={"error": "data_source_unavailable"}))
    with pytest.raises(RelayError, match="data_source_unavailable"):
        client.query("daily", ts_code="000001.SZ")
    assert len(calls) == 2


def test_upstream_pool_exhausted_retains_safe_kind_after_bounded_retry():
    client, calls = make_client(retries=1, handler=lambda r: httpx.Response(
        503, json={"error": "upstream_pool_exhausted", "message": "secret-relay-key"}))
    with pytest.raises(RelayError) as caught:
        client.query("daily", ts_code="000001.SZ")
    assert caught.value.kind == "upstream_pool_exhausted"
    assert caught.value.status_code == 503
    assert "secret-relay-key" not in str(caught.value)
    assert len(calls) == 3


@pytest.mark.parametrize("entry,kind", [
    ({"api_name": "daily", "enabled": False, "methods": ["GET"]}, "disabled_api"),
    ({"api_name": "daily", "enabled": True, "methods": ["POST"]}, "forbidden_api"),
    ({"api_name": "daily", "enabled": True, "methods": ["GET"],
      "required": ["trade_date"]}, "missing_params"),
    ({"api_name": "daily", "enabled": True, "methods": ["GET"],
      "required_any": [["start_date", "end_date"]]}, "missing_params"),
])
def test_catalogue_gates(entry, kind):
    client, calls = make_client(entries=[entry])
    with pytest.raises(RelayError, match=kind):
        client.query("daily", ts_code="000001.SZ")
    assert len(calls) == 1


def test_catalogue_refreshes_after_ttl():
    client, calls = make_client()
    now = [1000.0]
    client.clock = lambda: now[0]
    client.capabilities()
    now[0] += 301
    client.capabilities()
    assert len(calls) == 2


def test_default_factory_and_secret_isolation():
    settings = Settings(_env_file=None, tushare_token="official-only")
    with pytest.raises(RelayError, match="research_disabled"):
        build_tushare_relay_research_provider(settings)
    settings.tushare_relay_research_enabled = True
    with pytest.raises(RelayError, match="missing_config"):
        build_tushare_relay_research_provider(settings)
    settings = Settings(_env_file=None, tushare_relay_key="secret-relay-key",
                        tushare_relay_research_enabled=True)
    assert "secret-relay-key" not in repr(settings)
    assert "secret-relay-key" not in settings.model_dump_json()
    default = build_strategy_data_provider("free", settings)
    assert not any(isinstance(p, TushareRelayStrategyDataProvider) for p in default.providers)
    explicit = build_tushare_relay_research_provider(settings)
    assert isinstance(explicit, TushareRelayStrategyDataProvider)
    status = {p.provider_id: p for p in build_provider_status(settings)}
    assert status["tushare_relay_promax"].status == "configured"


def table(rows):
    return {"code": 0, "data": {"fields": list(rows[0]), "items": [list(r.values()) for r in rows]}}


def test_current_fundamentals_units_and_no_historical_pit():
    today = _today()
    ds = today.strftime("%Y%m%d")
    def route(r):
        if r.url.path.endswith("daily_basic"):
            return httpx.Response(200, json=table([dict(ts_code="000001.SZ", trade_date=ds,
                total_mv=123, pe_ttm=0, ps_ttm=2)]))
        return httpx.Response(200, json=table([dict(ts_code="000001.SZ", ann_date=ds,
            end_date="20251231", tr_yoy=5, netprofit_yoy=-2, roe=0)]))
    client, calls = make_client(handler=route)
    provider = TushareRelayStrategyDataProvider(client)
    assert provider.get_fundamentals(["CN:000001"], today-timedelta(days=3),
                                     today-timedelta(days=1)) == []
    assert not calls
    rows = provider.get_fundamentals(["CN:000001"], today-timedelta(days=3), today)
    assert rows[0].market_cap == Decimal(1230000)
    assert rows[0].pe_ratio == 0
    assert len(rows) == 1
    assert rows[0].return_on_equity_pct == 0
    assert rows[0].as_of_date == today
    assert rows[0].valuation_date == today
    assert not provider.last_errors


def price_provider(*, missing_factor=False, bad_price=False):
    def route(r):
        if r.url.path.endswith("adj_factor"):
            rows = [dict(ts_code="000001.SZ", trade_date="20260910", adj_factor=2)]
            if not missing_factor:
                rows.append(dict(ts_code="000001.SZ", trade_date="20260911", adj_factor=4))
        else:
            rows = [dict(ts_code="000001.SZ", trade_date=d, open=10, high=12, low=9,
                         close="NaN" if bad_price else 11) for d in ["20260910", "20260911"]]
        return httpx.Response(200, json=table(rows))
    client, _ = make_client(handler=route)
    return TushareRelayStrategyDataProvider(client)


def test_adjusted_research_prices_explicit_anchor():
    result = price_provider().get_research_daily_bars("CN:000001", date(2026, 9, 10),
        date(2026, 9, 11), adjustment_anchor=date(2026, 9, 11))
    assert result.rows[0]["raw_close"] == Decimal(11)
    assert result.rows[0]["adjusted_close"] == Decimal("5.5")
    assert result.rows[1]["adjusted_close"] == Decimal(11)
    assert result.source == "tushare_relay_promax"


@pytest.mark.parametrize("kwargs", [{"missing_factor": True}, {"bad_price": True}])
def test_adjusted_prices_fail_closed(kwargs):
    with pytest.raises(RelayError):
        price_provider(**kwargs).get_research_daily_bars("CN:000001", date(2026, 9, 10),
            date(2026, 9, 11), adjustment_anchor=date(2026, 9, 11))


@pytest.mark.parametrize("change", ["symbol", "future", "duplicate", "negative", "ohlc"])
def test_research_price_identity_and_quality(change):
    def route(r):
        if r.url.path.endswith("adj_factor"):
            row = dict(ts_code="000001.SZ", trade_date="20260911", adj_factor=2)
            if change == "negative":
                row["adj_factor"] = -2
        else:
            row = dict(ts_code="000001.SZ", trade_date="20260911", open=10,
                       high=12, low=9, close=11)
            if change == "symbol":
                row["ts_code"] = "600000.SH"
            if change == "future":
                row["trade_date"] = "20260912"
            if change == "ohlc":
                row["high"] = 8
        rows = [row, row] if change == "duplicate" else [row]
        return httpx.Response(200, json=table(rows))
    client, _ = make_client(handler=route)
    provider = TushareRelayStrategyDataProvider(client)
    with pytest.raises(RelayError):
        provider.get_research_daily_bars("CN:000001", date(2026, 9, 11),
            date(2026, 9, 11), adjustment_anchor=date(2026, 9, 11))


def test_current_combines_stale_valuation_with_explicit_source_dates():
    today = _today()
    prior = today - timedelta(days=2)
    def route(r):
        if r.url.path.endswith("daily_basic"):
            row = dict(ts_code="000001.SZ", trade_date=prior.strftime("%Y%m%d"), pe_ttm=10)
        else:
            row = dict(ts_code="000001.SZ", ann_date="20260801", end_date="20260630", roe=12)
        return httpx.Response(200, json=table([row]))
    client, _ = make_client(handler=route)
    rows = TushareRelayStrategyDataProvider(client).get_fundamentals(
        ["CN:000001"], prior, today)
    assert len(rows) == 1
    assert rows[0].pe_ratio == 10 and rows[0].return_on_equity_pct == 12
    assert rows[0].valuation_date == prior
    assert rows[0].financial_period == date(2026, 6, 30)
    assert rows[0].as_of_date == today


def test_duplicate_finance_does_not_guess_revision():
    today = _today()
    def route(r):
        if r.url.path.endswith("daily_basic"):
            return httpx.Response(200, json={"code": 0, "data": {"fields": [], "items": []}})
        row = dict(ts_code="000001.SZ", ann_date="20260801", end_date="20260630", roe=12)
        return httpx.Response(200, json=table([row, {**row, "roe": 13}]))
    client, _ = make_client(handler=route)
    provider = TushareRelayStrategyDataProvider(client)
    assert provider.get_fundamentals(["CN:000001"], today, today) == []
    assert provider.last_errors == ["tushare_relay:ambiguous_financial_revision"]


@pytest.mark.parametrize("failure", ["ambiguous", "transport", "empty"])
def test_finance_failure_preserves_valuation(failure):
    today = _today()
    def route(r):
        if r.url.path.endswith("daily_basic"):
            return httpx.Response(200, json=table([dict(ts_code="000001.SZ",
                trade_date=today.strftime("%Y%m%d"), pe_ttm=10)]))
        if failure == "transport":
            raise httpx.ConnectError("unavailable")
        if failure == "empty":
            return httpx.Response(200, json={"code": 0, "data": {"fields": [], "items": []}})
        row = dict(ts_code="000001.SZ", ann_date="20260801", end_date="20260630", roe=12)
        return httpx.Response(200, json=table([row, {**row, "roe": 13}]))
    client, _ = make_client(handler=route)
    provider = TushareRelayStrategyDataProvider(client)
    rows = provider.get_fundamentals(["CN:000001"], today, today)
    assert len(rows) == 1 and rows[0].pe_ratio == 10
    assert rows[0].return_on_equity_pct is None
    assert rows[0].financial_period is None
    assert rows[0].valuation_date == today
    assert provider.last_errors


def test_latest_finance_accepts_exact_duplicates_and_ignores_old_conflicts():
    today = _today()
    def route(r):
        if r.url.path.endswith("daily_basic"):
            return httpx.Response(200, json={"code": 0, "data": {"fields": [], "items": []}})
        old = dict(ts_code="000001.SZ", ann_date="20260401", end_date="20251231", roe=12)
        latest = dict(ts_code="000001.SZ", ann_date="20260801", end_date="20260630", roe=15)
        return httpx.Response(200, json=table([old, {**old, "roe": 13}, latest, latest]))
    client, _ = make_client(handler=route)
    provider = TushareRelayStrategyDataProvider(client)
    rows = provider.get_fundamentals(["CN:000001"], today, today)
    assert len(rows) == 1 and rows[0].return_on_equity_pct == 15
    assert rows[0].financial_period == date(2026, 6, 30)
    assert not provider.last_errors


def test_finance_unused_revision_difference_keeps_identical_normalized_inputs():
    today = _today()
    def route(r):
        if r.url.path.endswith("daily_basic"):
            return httpx.Response(200, json={"code": 0, "data": {"fields": [], "items": []}})
        row = dict(ts_code="000001.SZ", ann_date="20260815", end_date="20260630",
                   roe=4.6746, tr_yoy=1.7756, bps=10)
        return httpx.Response(200, json=table([row, {**row, "roe": "4.674600", "bps": 11}]))
    client, _ = make_client(handler=route)
    provider = TushareRelayStrategyDataProvider(client)
    rows = provider.get_fundamentals(["CN:000001"], today, today)
    assert len(rows) == 1
    assert rows[0].return_on_equity_pct == Decimal("4.6746")
    assert rows[0].revenue_growth_pct == Decimal("1.7756")
    assert provider.last_errors == ["tushare_relay:unused_field_revision_difference"]


def test_transport_failure_does_not_expose_credentials():
    def route(request):
        raise httpx.ConnectError("secret-relay-key", request=request)
    client, calls = make_client(handler=route)
    with pytest.raises(RelayError, match="transport_error") as caught:
        client.query("daily", ts_code="000001.SZ")
    assert "secret-relay-key" not in str(caught.value)
    assert len(calls) == 2
