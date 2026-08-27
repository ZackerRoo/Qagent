from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from qagent.api import routes
from qagent.app import create_app
from qagent.strategy_data.models import FundamentalSnapshot
from qagent.strategy_data.providers import EmptyStrategyDataProvider


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


def test_factor_backtest_uses_stored_point_in_time_fundamentals(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'factor-fundamental-history.db'}",
    )
    client = TestClient(create_app())
    stored = [
        FundamentalSnapshot(
            instrument_id=instrument_id,
            as_of_date=date(2026, 1, 10),
            pe_ratio=pe_ratio,
            market_cap=market_cap,
            return_on_equity_pct=Decimal("18"),
            gross_margin_pct=Decimal("35"),
            revenue_growth_pct=Decimal("12"),
            provider="stored_test",
        )
        for instrument_id, pe_ratio, market_cap in [
            ("US:TEST", Decimal("12"), Decimal("50000000000")),
            ("CN:000001", Decimal("8"), Decimal("90000000000")),
        ]
    ]
    routes._repo().upsert_fundamental_snapshots("fixture", stored)
    monkeypatch.setattr(
        routes,
        "build_strategy_data_provider",
        lambda _mode: EmptyStrategyDataProvider(),
    )

    response = client.get(
        "/api/factors/backtest?provider=fixture&forward_days=10&step_days=20&top_n=1"
    )

    assert response.status_code == 200
    health = response.json()["data_health"]
    assert health["fundamental_live_rows"] == "0"
    assert health["fundamental_stored_rows"] == "2"
    assert health["historical_fundamentals"] == "2"
    assert health["fundamental_mode"] == "point_in_time"


def test_factor_research_experiment_api_starts_empty_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'factor-research-api.db'}",
    )
    client = TestClient(create_app())

    response = client.get("/api/factor-research/experiments")
    library = client.get("/api/research/experiment-library?provider=fixture")
    shadow = client.get("/api/factor-research/shadow/latest")
    evaluation = client.get("/api/factor-research/shadow/evaluation?provider=fixture")
    roster = client.get("/api/factor-research/shadow/roster?provider=fixture")
    resolution = client.post(
        "/api/factor-research/shadow/outcomes/resolve?provider=fixture"
    )
    missing = client.get("/api/factor-research/experiments/not-found")

    assert response.status_code == 200
    assert response.json()["experiments"] == []
    assert response.json()["data_health"]["paper_model_isolation"] == "unchanged"
    assert library.status_code == 200
    assert library.json()["artifacts"] == []
    assert library.json()["data_health"]["experiment_library_changes_paper_execution"] == "false"
    assert shadow.status_code == 200
    assert shadow.json()["run"] is None
    assert shadow.json()["data_health"]["paper_order_effect"] == "none"
    assert evaluation.status_code == 200
    assert evaluation.json()["evaluation"]["status"] == "not_started"
    assert roster.status_code == 200
    assert roster.json()["roster"]["status"] == "not_started"
    assert roster.json()["data_health"]["factor_shadow_roster_paper_isolation"] == "true"
    assert resolution.status_code == 200
    assert resolution.json()["resolution"]["status"] == "not_started"
    assert resolution.json()["evaluation"]["status"] == "not_started"
    assert missing.status_code == 404
