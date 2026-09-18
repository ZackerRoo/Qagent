from hashlib import sha256
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from qagent.api import documented_research_routes as routes
from qagent.config import Settings
from qagent.providers import documented_research as research
from qagent.providers.tushare_relay import RelayError, RelayTable


def settings(**overrides):
    return Settings(_env_file=None, datahubco_key="synthetic-basic-key",
                    tushare_relay_key="synthetic-promax-key", **overrides)


def enabled():
    return settings(datahubco_enabled=True, datahubco_allow_insecure_http=True,
                    tushare_relay_research_enabled=True)


class Client:
    def __init__(self, *, rows=None, failure=None):
        self.calls = []
        self.rows = [] if rows is None else rows
        self.failure = failure

    def query(self, api, **params):
        self.calls.append((api, params))
        if self.failure:
            raise self.failure
        return RelayTable(api, ("close",), tuple(self.rows), params["limit"], source="synthetic")

    def capabilities(self):
        if self.failure:
            raise self.failure
        return {
            "daily": {"enabled": True, "methods": ["GET"], "description": "DO_NOT_RETURN_REMOTE_TEXT"},
            "p_save": {"enabled": True, "methods": ["GET", "POST"]},
            "disabled": {"enabled": False, "methods": ["GET"]},
        }


def test_catalogue_separates_configuration_capability_and_mutations(monkeypatch):
    service = research.DocumentedResearch(enabled())
    monkeypatch.setattr(service, "_client", lambda source: Client())
    report = service.catalogue()
    assert report["sources"]["datahubco"]["count"] == 81
    assert report["sources"]["datahubco"]["callable_count"] == 80
    entries = {entry["api"]: entry for entry in report["sources"]["promax"]["entries"]}
    assert entries["daily"]["callable"] is True
    assert not entries["p_save"]["read_only"] and not entries["disabled"]["callable"]
    assert "DO_NOT_RETURN_REMOTE_TEXT" not in json.dumps(report)
    assert report["research_only"] and not report["activation_allowed"]


def test_disabled_sources_do_not_construct_network_clients(monkeypatch):
    monkeypatch.setattr(research, "TushareRelayClient", lambda *a, **k: pytest.fail("network construction"))
    monkeypatch.setattr(research, "DatahubcoClient", lambda *a, **k: pytest.fail("network construction"))
    service = research.DocumentedResearch(settings(tushare_relay_market_enabled=True))
    report = service.catalogue()
    assert all(section["error"] == "source_disabled" for section in report["sources"].values())
    assert service.query("promax", "daily")["error"] == "source_disabled"


def test_catalogue_failure_is_independent_and_redacted(monkeypatch):
    service = research.DocumentedResearch(enabled())
    monkeypatch.setattr(service, "_client", lambda source: Client(
        failure=RuntimeError("synthetic-promax-key") if source == "promax" else None))
    report = service.catalogue()
    assert report["sources"]["datahubco"]["status"] == "configured"
    assert report["sources"]["promax"]["status"] == "error"
    assert report["status"] == "incomplete"
    assert "synthetic-promax-key" not in json.dumps(report)


@pytest.mark.parametrize("source", ["datahubco", "promax"])
@pytest.mark.parametrize("rows,status", [([], "no_rows"), ([{"close": 12}], "observed")])
def test_query_preserves_request_and_digest(monkeypatch, source, rows, status):
    service, client = research.DocumentedResearch(enabled()), Client(rows=rows)
    monkeypatch.setattr(service, "_client", lambda source: client)
    report = service.query(source, "daily", params={"ts_code": "000001.SZ"}, limit=12, fields="close")
    assert report["status"] == status and report["rows"] == rows
    assert report["request"]["params"] == {"ts_code": "000001.SZ"}
    expected = report.pop("result_digest")
    assert expected == sha256(json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert client.calls == [("daily", {"ts_code": "000001.SZ", "limit": 12, "offset": 0, "fields": "close"})]


@pytest.mark.parametrize("param", ["url", "base_url", "baseurl", "proxies", "host", "headers", "auth", "token", "password", "limit"])
def test_route_and_credentials_params_rejected_before_network(monkeypatch, param):
    service = research.DocumentedResearch(enabled())
    monkeypatch.setattr(service, "_client", lambda source: pytest.fail("network construction"))
    report = service.query("datahubco", "daily", params={param: "not-a-key"})
    assert report["status"] == "error" and report["request"] is None


@pytest.mark.parametrize("api", ["p_list", "p_save", "order_create", "delete", "../daily"])
def test_operations_never_reach_network(monkeypatch, api):
    service = research.DocumentedResearch(enabled())
    monkeypatch.setattr(service, "_client", lambda source: pytest.fail("network construction"))
    assert service.query("promax", api)["status"] == "error"


def test_key_in_request_or_response_is_not_returned(monkeypatch):
    service = research.DocumentedResearch(enabled())
    monkeypatch.setattr(service, "_client", lambda source: Client(rows=[{"close": "synthetic-promax-key"}]))
    for report in (service.query("datahubco", "daily", params={"ts_code": "synthetic-promax-key"}),
                   service.query("datahubco", "daily")):
        assert report["status"] == "error" and report["rows"] == []
        assert "synthetic-promax-key" not in json.dumps(report)


def test_clients_keep_fixed_budgets_and_separate_optins(monkeypatch):
    calls = []
    monkeypatch.setattr(research, "DatahubcoClient", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(research, "TushareRelayClient", lambda *a, **kw: calls.append(kw))
    service = research.DocumentedResearch(enabled())
    service._client("datahubco")
    service._client("promax")
    assert calls == [{"allow_insecure_http": True, "timeout_seconds": 30}, {"timeout_seconds": 30, "retries": 0}]
    disabled = research.DocumentedResearch(settings(datahubco_enabled=True))
    assert disabled.query("datahubco", "daily")["error"] == "insecure_http_not_authorized"


def test_http_routes_validation_redaction_and_no_lifespan(monkeypatch):
    monkeypatch.setattr(routes, "get_settings", enabled)
    monkeypatch.setattr(research.DocumentedResearch, "_client", lambda self, source: Client())
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)
    response = client.post("/api/documented-research/query", json={"source": "promax", "api": "daily"})
    assert response.status_code == 200 and response.json()["status"] == "no_rows"
    for bad in ({"source": "promax", "api": "daily", "limit": 5001, "key": "DO_NOT_ECHO"},
                {"source": "unknown", "api": "DO_NOT_ECHO"}):
        response = client.post("/api/documented-research/query", json=bad)
        assert response.status_code == 422 and "DO_NOT_ECHO" not in response.text
    assert client.get("/api/documented-research/catalogue").status_code == 200


def test_known_transport_error_is_safe(monkeypatch):
    service = research.DocumentedResearch(enabled())
    monkeypatch.setattr(service, "_client", lambda source: Client(failure=RelayError("transport_error")))
    assert service.query("promax", "daily")["error"] == "transport_error"


def test_router_registered_without_starting_application(monkeypatch):
    from qagent.app import create_app
    monkeypatch.setattr(routes, "get_settings", settings)
    client = TestClient(create_app())  # no context manager, so no scheduler lifespan
    response = client.post("/api/documented-research/query", json={"source": "promax", "api": "daily"})
    assert response.status_code == 200 and response.json()["error"] == "source_disabled"
    assert client.get("/api/documented-research/catalogue").status_code == 200
