import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("collect_documented_research", SCRIPTS / "collect_documented_research.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
sys.path.pop(0)


@pytest.mark.parametrize("url", ["http://example.org", "http://127.0.0.1.evil", "http://10.0.0.1",
    "http://user:secret@127.0.0.1", "http://127.0.0.1/other", "http://127.0.0.1?secret=yes",
    "http://127.0.0.1#fragment", "file:///tmp/a"])
def test_external_and_credential_origins_rejected(url):
    with pytest.raises((ValueError, TypeError)):
        cli.local_origin(url)


def test_loopback_normalization():
    assert cli.local_origin("http://localhost:8000/") == "http://127.0.0.1:8000"
    assert cli.local_origin("http://[::1]:8000") == "http://[::1]:8000"


def test_incomplete_catalogue_keeps_evidence_and_returns_failure(monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect", lambda *a, **kw: {"status": "incomplete", "sources": {}})
    monkeypatch.setattr(sys, "argv", ["collect", "--catalogue"])
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "incomplete"


class Opener:
    def __init__(self, payload):
        self.payload, self.requests = payload, []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        response = io.BytesIO(json.dumps(self.payload).encode())
        response.status = 200
        return response


def test_exact_query_contract_and_catalogue_no_extra_calls():
    opener = Opener({"status": "observed", "rows": [{"close": 10}]})
    result = cli.collect("http://127.0.0.1:8000", source="datahubco", api="daily",
                         params={"ts_code": "000001.SZ"}, limit=2, offset=3, fields="close", opener=opener)
    request, timeout = opener.requests[0]
    assert result == opener.payload
    assert request.full_url == "http://127.0.0.1:8000/api/documented-research/query"
    assert request.method == "POST" and timeout == 70
    assert json.loads(request.data) == {"source": "datahubco", "api": "daily",
        "params": {"ts_code": "000001.SZ"}, "limit": 2, "offset": 3, "fields": "close"}
    cli.collect("http://127.0.0.1:8000", catalogue=True, opener=opener)
    assert opener.requests[1][0].method == "GET"
    assert opener.requests[1][0].full_url.endswith("/catalogue")
    assert len(opener.requests) == 2


@pytest.mark.parametrize("kwargs", [{"limit": 5001}, {"offset": -1}, {"params": {"limit": 3}}, {"params": []}])
def test_invalid_query_rejected_before_network(kwargs):
    with pytest.raises(ValueError):
        cli.collect("http://127.0.0.1:8000", source="datahubco", api="daily", **kwargs)


def test_redirect_blocked():
    with pytest.raises(ValueError, match="redirect_blocked"):
        cli.NoRedirect().redirect_request(None, None, 302, None, None, "http://evil")


def test_cli_artifact_exclusive_errors_safe(tmp_path, monkeypatch, capsys):
    output = tmp_path / "result.json"
    report = {"status": "no_rows", "rows": [], "result_digest": "source-digest"}
    monkeypatch.setattr(cli, "collect", lambda *a, **kw: report)
    monkeypatch.setattr(sys, "argv", ["collect", "--source", "datahubco", "--api", "daily", "--output", str(output)])
    assert cli.main() == 0
    before = output.read_bytes()
    assert json.loads(before) == report
    assert cli.main() == 1
    assert output.read_bytes() == before
    assert "documented_research_collection_failed" in capsys.readouterr().out
    def fail(*a, **kw):
        raise ValueError("private-secret")
    monkeypatch.setattr(cli, "collect", fail)
    assert cli.main() == 1
    assert "private-secret" not in capsys.readouterr().out


def test_cli_real_fastapi_route_and_service_contract(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from qagent.api import documented_research_routes as routes
    from qagent.config import Settings
    from qagent.providers.documented_research import DocumentedResearch
    from qagent.providers.tushare_relay import RelayTable
    settings = Settings(_env_file=None, datahubco_enabled=True,
                        datahubco_allow_insecure_http=True, datahubco_key="synthetic")
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    calls = []
    class Provider:
        def query(self, api, **params):
            calls.append((api, params))
            return RelayTable(api, ("close",), ({"close": 11.74},), params["limit"], source="datahubco")
    monkeypatch.setattr(DocumentedResearch, "_client", lambda self, source: Provider())
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    with TestClient(app) as test_client:
        class Adapter:
            def open(self, request, timeout):
                response = test_client.request(request.method, request.full_url,
                    content=request.data, headers=dict(request.header_items()))
                result = io.BytesIO(response.content)
                result.status = response.status_code
                return result
        report = cli.collect("http://127.0.0.1:8000", source="datahubco", api="daily",
                             params={"ts_code": "000001.SZ"}, limit=2, offset=4,
                             fields="close", opener=Adapter())
        assert report["status"] == "observed" and report["rows"] == [{"close": 11.74}]
        assert report["research_only"] and not report["decision_weight"]
        assert calls == [("daily", {"ts_code": "000001.SZ", "limit": 2, "offset": 4, "fields": "close"})]
        catalogue = cli.collect("http://127.0.0.1:8000", catalogue=True, opener=Adapter())
        assert catalogue["sources"]["datahubco"]["count"] == 81
