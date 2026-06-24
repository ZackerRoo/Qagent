from fastapi.testclient import TestClient

from qagent.app import create_app


def test_factors_endpoint_returns_ranked_factor_scores():
    client = TestClient(create_app())

    response = client.get("/api/factors?provider=fixture")

    assert response.status_code == 200
    body = response.json()
    assert body["rankings"]
    first = body["rankings"][0]
    assert first["instrument_id"]
    assert first["factor_score"] >= 0
    assert first["factor_rank"] == 1
    assert first["factor_exposures"]
    assert body["data_health"]["factor_rankings"] == str(len(body["rankings"]))


def test_factor_backtest_endpoint_returns_validation_samples():
    client = TestClient(create_app())

    response = client.get("/api/factors/backtest?provider=fixture&forward_days=10&step_days=20&top_n=1")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["sample_count"] > 0
    assert body["signals"]
    assert body["data_health"]["factor_backtest"] in {"ok", "no_bars"}
    assert "min_history_days" in body["data_health"]
