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


def test_factor_candidate_queue_api_is_shadow_only_and_has_no_paper_effect():
    client = TestClient(create_app())

    response = client.get("/api/research/factor-candidate-queue")

    assert response.status_code == 200
    body = response.json()
    states = [candidate["state"] for candidate in body["candidates"]]
    assert states.count("contract_available_for_shadow_design") == 5
    assert states.count("future_capability") == 1
    assert body["scope"] == "research_shadow"
    assert body["decision_weight"] is False
    assert body["production_ranking_effect"] == "none"
    assert body["paper_order_effect"] == "none"
    assert body["data_health"]["factor_candidate_queue_data_coverage_status"] == "unverified"
    assert body["data_health"]["factor_candidate_queue_experiment_start_allowed"] == "false"
    assert body["data_health"]["factor_candidate_queue_gate_policy_completeness"] == "partial"
    assert body["data_health"]["factor_candidate_queue_paper_isolation"] == "true"
    assert all(candidate["decision_weight"] is False for candidate in body["candidates"])
    assert all(candidate["paper_order_effect"] == "none" for candidate in body["candidates"])
    assert all(candidate["data_coverage_status"] == "unverified" for candidate in body["candidates"])
    assert all(candidate["experiment_start_allowed"] is False for candidate in body["candidates"])


def _factor_candidate_request(
    candidate_id: str,
    provider_mode: str = "free",
) -> dict[str, object]:
    return {"candidate_id": candidate_id, "provider_mode": provider_mode}


def test_factor_candidate_api_rejects_unknown_and_future_without_recording(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'factor-candidate-reject.db'}",
    )
    client = TestClient(create_app())

    unknown = client.post(
        "/api/factor-research/experiments",
        json=_factor_candidate_request("unknown-candidate"),
    )
    future = client.post(
        "/api/factor-research/experiments",
        json=_factor_candidate_request("point-in-time-catalyst-v1"),
    )
    experiments = client.get("/api/factor-research/experiments")

    assert unknown.status_code == 400
    assert future.status_code == 400
    assert experiments.json()["experiments"] == []


def test_factor_candidate_api_rejects_caller_supplied_experiment_parameters():
    client = TestClient(create_app())

    response = client.post(
        "/api/factor-research/experiments",
        json={
            "candidate_id": "trend-health-composite-v1",
            "provider_mode": "free",
            "selected_feature_columns": ["momentum_20"],
            "dataset_revision": 7,
            "model_recipe": "balanced_v1",
        },
    )

    assert response.status_code == 422
    assert {item["loc"][-1] for item in response.json()["detail"]} == {
        "selected_feature_columns",
        "dataset_revision",
        "model_recipe",
    }


def test_factor_candidate_api_requires_provider_mode():
    client = TestClient(create_app())

    response = client.post(
        "/api/factor-research/experiments",
        json={"candidate_id": "trend-health-composite-v1"},
    )

    assert response.status_code == 422
    assert {item["loc"][-1] for item in response.json()["detail"]} == {"provider_mode"}


def test_factor_candidate_api_propagates_explicit_provider_without_recording(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'factor-candidate-provider.db'}",
    )
    monkeypatch.setattr(
        routes,
        "current_factor_source_identity",
        lambda: {
            "code_revision": "f" * 40,
            "source_digest": "a" * 64,
            "dirty_worktree_digest": "b" * 64,
        },
    )
    monkeypatch.setattr(
        routes,
        "resolved_config",
        lambda _session_factory, config: config.model_copy(
            update={"dataset_revision": 7}
        ),
    )

    def reject_after_provider_check(_session_factory, config):
        assert config.provider_mode == "fixture"
        raise ValueError("provider propagation verified")

    monkeypatch.setattr(routes, "preflight_factor_candidate_experiment", reject_after_provider_check)
    client = TestClient(create_app())

    response = client.post(
        "/api/factor-research/experiments",
        json=_factor_candidate_request("trend-health-composite-v1", "fixture"),
    )
    experiments = client.get("/api/factor-research/experiments")

    assert response.status_code == 400
    assert response.json()["detail"] == "provider propagation verified"
    assert experiments.json()["experiments"] == []
    assert (
        experiments.json()["data_health"]["factor_research_start_contract"]
        == "breaking_v2_candidate_id_plus_provider_mode_extra_forbid"
    )


def test_factor_candidate_api_rejects_low_coverage_without_recording(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'factor-candidate-low-coverage.db'}",
    )
    monkeypatch.setattr(
        routes,
        "current_factor_source_identity",
        lambda: {
            "code_revision": "f" * 40,
            "source_digest": "a" * 64,
            "dirty_worktree_digest": "b" * 64,
        },
    )
    monkeypatch.setattr(
        routes,
        "resolved_config",
        lambda _session_factory, config: config.model_copy(
            update={"dataset_revision": 7}
        ),
    )
    monkeypatch.setattr(
        routes,
        "preflight_factor_candidate_experiment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("factor candidate joint feature coverage 0.940000 is below 0.95")
        ),
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/factor-research/experiments",
        json=_factor_candidate_request("trend-health-composite-v1"),
    )
    experiments = client.get("/api/factor-research/experiments")

    assert response.status_code == 400
    assert "below 0.95" in response.json()["detail"]
    assert experiments.json()["experiments"] == []
