from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.api import routes
from qagent.config import Settings
from qagent.providers.status import build_provider_status


def test_build_provider_status_marks_configured_vendor_keys():
    statuses = build_provider_status(
        Settings(
            alpha_vantage_api_key="alpha",
            fmp_api_key="fmp",
            finnhub_api_key="finnhub",
            tushare_token="tushare",
            fuyao_api_key="fuyao",
            sec_user_agent="qagent-test contact@example.com",
        )
    )

    by_id = {status.provider_id: status for status in statuses}
    assert by_id["fixture"].status == "ready"
    assert by_id["alpha_vantage"].status == "ready"
    assert by_id["fmp"].status == "ready"
    assert by_id["finnhub"].status == "ready"
    assert by_id["sec_edgar"].status == "ready"
    assert by_id["cninfo"].status == "configured"
    assert by_id["tushare"].status == "configured"
    assert by_id["tickflow_free"].status == "ready"
    assert by_id["fuyao"].status == "configured"
    assert "cn_snapshot_read_only" in by_id["fuyao"].capabilities
    assert "cn_daily_ohlcv_fallback" in by_id["tickflow_free"].capabilities
    assert "fundamentals" in by_id["alpha_vantage"].capabilities


def test_build_provider_status_marks_missing_vendor_keys():
    statuses = build_provider_status(Settings(_env_file=None))

    by_id = {status.provider_id: status for status in statuses}
    assert by_id["alpha_vantage"].status == "missing_config"
    assert by_id["fmp"].status == "missing_config"
    assert by_id["finnhub"].status == "missing_config"
    assert by_id["tushare"].status == "missing_config"
    assert by_id["yfinance"].status == "ready"
    assert by_id["akshare_baostock"].status == "ready"
    assert by_id["tickflow_free"].status == "ready"
    assert by_id["fuyao"].status == "missing_config"


def test_build_provider_status_uses_observed_runtime_health():
    statuses = build_provider_status(
        Settings(fuyao_api_key="fuyao", _env_file=None),
        data_health={
            "strategy_announcements": "12",
            "fuyao_telemetry": "partial",
        },
    )

    by_id = {status.provider_id: status for status in statuses}
    assert by_id["cninfo"].status == "ready"
    assert by_id["fuyao"].status == "partial"


def test_build_provider_status_does_not_claim_cninfo_ready_from_zero_rows():
    statuses = build_provider_status(
        Settings(_env_file=None),
        data_health={"strategy_announcements": "0"},
    )

    by_id = {status.provider_id: status for status in statuses}
    assert by_id["cninfo"].status == "configured"


def test_provider_status_api_returns_readiness(monkeypatch):
    monkeypatch.setenv("QAGENT_FMP_API_KEY", "fmp")
    client = TestClient(create_app())

    response = client.get("/api/provider-status")

    assert response.status_code == 200
    body = response.json()
    assert "providers" in body
    by_id = {status["provider_id"]: status for status in body["providers"]}
    assert by_id["fmp"]["status"] == "ready"


def test_provider_status_api_uses_latest_full_market_health(monkeypatch):
    monkeypatch.setenv("QAGENT_FUYAO_API_KEY", "fuyao")
    monkeypatch.setattr(
        routes,
        "_repo",
        lambda: type(
            "Repo",
            (),
            {
                "list_scan_runs": lambda self, limit: [
                    type(
                        "Run",
                        (),
                        {
                            "mode": "full_market_batch",
                            "data_health": {
                                "strategy_announcements": "8",
                                "fuyao_telemetry": "partial",
                            },
                        },
                    )()
                ]
            },
        )(),
    )
    client = TestClient(create_app())

    response = client.get("/api/provider-status")

    assert response.status_code == 200
    by_id = {status["provider_id"]: status for status in response.json()["providers"]}
    assert by_id["cninfo"]["status"] == "ready"
    assert by_id["fuyao"]["status"] == "partial"
