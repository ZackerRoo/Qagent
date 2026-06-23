from fastapi.testclient import TestClient

from qagent.app import create_app


def test_data_cache_api_reports_and_clears_cached_market_data(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'data-cache.db'}")
    client = TestClient(create_app())

    scan_response = client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    summary_response = client.get("/api/data-cache")
    clear_response = client.delete("/api/data-cache?provider=fixture")
    empty_response = client.get("/api/data-cache")

    assert scan_response.status_code == 200
    assert scan_response.json()["data_health"]["market_cache"] == "enabled"
    assert summary_response.status_code == 200
    summaries = summary_response.json()["summaries"]
    assert summaries
    assert summaries[0]["provider_mode"] == "fixture"
    assert summaries[0]["instrument_id"] == "US:TEST"
    assert summaries[0]["rows"] > 0
    assert clear_response.status_code == 200
    assert clear_response.json()["deleted"] == summaries[0]["rows"]
    assert empty_response.json()["summaries"] == []
