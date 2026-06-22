from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.config import Settings
from qagent.providers.status import build_provider_status


def test_build_provider_status_marks_configured_vendor_keys():
    statuses = build_provider_status(
        Settings(
            alpha_vantage_api_key="alpha",
            fmp_api_key="fmp",
            finnhub_api_key="finnhub",
            tushare_token="tushare",
            sec_user_agent="qagent-test contact@example.com",
        )
    )

    by_id = {status.provider_id: status for status in statuses}
    assert by_id["fixture"].status == "ready"
    assert by_id["alpha_vantage"].status == "ready"
    assert by_id["fmp"].status == "ready"
    assert by_id["finnhub"].status == "ready"
    assert by_id["sec_edgar"].status == "ready"
    assert by_id["cninfo"].status == "ready"
    assert by_id["tushare"].status == "configured"
    assert "fundamentals" in by_id["alpha_vantage"].capabilities


def test_build_provider_status_marks_missing_vendor_keys():
    statuses = build_provider_status(Settings())

    by_id = {status.provider_id: status for status in statuses}
    assert by_id["alpha_vantage"].status == "missing_config"
    assert by_id["fmp"].status == "missing_config"
    assert by_id["finnhub"].status == "missing_config"
    assert by_id["tushare"].status == "missing_config"
    assert by_id["yfinance"].status == "ready"
    assert by_id["akshare_baostock"].status == "ready"


def test_provider_status_api_returns_readiness(monkeypatch):
    monkeypatch.setenv("QAGENT_FMP_API_KEY", "fmp")
    client = TestClient(create_app())

    response = client.get("/api/provider-status")

    assert response.status_code == 200
    body = response.json()
    assert "providers" in body
    by_id = {status["provider_id"]: status for status in body["providers"]}
    assert by_id["fmp"]["status"] == "ready"
