from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import qagent.api.routes as routes
from qagent.app import create_app
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardConflictError,
    RankingV3ForwardStateError,
)
from qagent.backtesting.ranking_v3_protocol import build_ranking_v3_protocol
from qagent.jobs.automation_scheduler import AutoProcessingSettings
from qagent.market.calendars import trading_day_offset


def test_forward_state_endpoint_reports_idle_without_eligible_run(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'forward-state-idle.db'}",
    )
    client = TestClient(create_app())

    response = client.get("/api/ranking-v3/forward/state")

    assert response.status_code == 200
    assert response.json() == {
        "state": "idle",
        "status": "idle",
        "reason": "no eligible successful Ranking V3 validation run",
        "message": "no eligible successful Ranking V3 validation run",
        "validation_run_id": None,
        "protocol": None,
        "protocol_id": None,
        "model_version": None,
        "required_sessions": 0,
        "required_completed_trades": 0,
        "maximum_sessions": None,
        "phase": "idle",
        "collection_target_sessions": 0,
        "latest_session_date": None,
        "blocked_date": None,
        "blocked_code": None,
        "last_attempt_at": None,
        "error": None,
        "metrics": None,
        "evaluation": None,
        "release_proof_available": False,
        "release_proof_digest": None,
    }


def test_forward_context_does_not_silently_fall_back_to_an_older_run(monkeypatch):
    calls = []
    latest = SimpleNamespace(status="succeeded", payload={})
    repo = SimpleNamespace(
        session_factory=object(),
        list_walk_forward_runs=lambda **kwargs: (
            calls.append(kwargs) or [latest]
        ),
    )
    monkeypatch.setattr(
        routes,
        "RankingV3ForwardRepository",
        lambda _: SimpleNamespace(load_snapshot=lambda __: None),
    )

    assert routes._ranking_v3_forward_context(repo) is None
    assert calls == [{"provider": "free", "limit": 1}]


def test_forward_run_once_uses_server_context_and_returns_processed_dates(
    monkeypatch,
):
    context = (SimpleNamespace(run_id="run-v3"), {}, SimpleNamespace())
    result = SimpleNamespace(session_date=date(2026, 7, 27))
    monkeypatch.setattr(routes, "_repo", lambda: SimpleNamespace())
    monkeypatch.setattr(
        routes,
        "_ranking_v3_forward_context",
        lambda repo, run_id=None: context,
    )
    monkeypatch.setattr(
        routes,
        "_run_ranking_v3_forward_catch_up",
        lambda repo, provider, current, through_date: [result],
    )
    monkeypatch.setattr(
        routes,
        "_ranking_v3_forward_state_payload",
        lambda repo, current: {
            "state": "shadow_running",
            "validation_run_id": "run-v3",
        },
    )
    monkeypatch.setattr(routes, "build_market_data_provider", lambda _: object())

    payload = routes.run_ranking_v3_forward_once(
        run_id=None,
        session_date=date(2026, 7, 27),
    )

    assert payload["state"] == "shadow_running"
    assert payload["processed_session_count"] == 1
    assert payload["processed_session_dates"] == ["2026-07-27"]


def test_production_state_reports_current_immutable_batch(monkeypatch):
    target = date(2026, 7, 27)
    identity = SimpleNamespace(identity_digest="a" * 64)
    batch = SimpleNamespace(
        session_date=target,
        selected_count=5,
        fact_digest="b" * 64,
    )
    monkeypatch.setattr(routes, "_a_share_today", lambda: target)
    monkeypatch.setattr(
        routes.RankingV3ProductionIdentity,
        "from_release_proof",
        lambda *_args, **_kwargs: identity,
    )
    monkeypatch.setattr(
        routes,
        "RankingV3ProductionRepository",
        lambda _factory: SimpleNamespace(list_batches=lambda *_args, **_kwargs: (batch,)),
    )

    payload = routes._ranking_v3_production_state_payload(
        SimpleNamespace(session_factory=object()),
        validation_run_id="walk-forward-v3",
        evaluation=SimpleNamespace(status="approved", release_proof=object()),
    )

    assert payload["state"] == "recorded"
    assert payload["paper_admission_enforced"] is True
    assert payload["target_session_date"] == target.isoformat()
    assert payload["latest_session_date"] == target.isoformat()
    assert payload["selected_count"] == 5
    assert payload["batch_fact_digest"] == "b" * 64
    assert payload["identity_digest"] == "a" * 64


def test_production_state_waits_for_full_market_scan_without_batch(monkeypatch):
    target = date(2026, 7, 27)
    identity = SimpleNamespace(identity_digest="c" * 64)
    monkeypatch.setattr(routes, "_a_share_today", lambda: target)
    monkeypatch.setattr(
        routes.RankingV3ProductionIdentity,
        "from_release_proof",
        lambda *_args, **_kwargs: identity,
    )
    monkeypatch.setattr(
        routes,
        "RankingV3ProductionRepository",
        lambda _factory: SimpleNamespace(list_batches=lambda *_args, **_kwargs: ()),
    )

    payload = routes._ranking_v3_production_state_payload(
        SimpleNamespace(session_factory=object()),
        validation_run_id="walk-forward-v3",
        evaluation=SimpleNamespace(status="approved", release_proof=object()),
    )

    assert payload["state"] == "awaiting_full_market_scan"
    assert payload["paper_admission_enforced"] is True
    assert payload["selected_count"] == 0
    assert payload["latest_session_date"] is None
    assert payload["identity_digest"] == "c" * 64


def test_forward_catch_up_reports_missing_daily_snapshot_as_waiting(
    monkeypatch,
):
    protocol = build_ranking_v3_protocol()
    blocked_date = protocol.prospective_shadow_start
    today = trading_day_offset(blocked_date, 1)
    repo = SimpleNamespace(
        session_factory=object(),
        list_top_daily_opportunity_snapshots=lambda **_: [],
    )
    store = SimpleNamespace(load_snapshot=lambda _: None)
    context = (SimpleNamespace(run_id="run-v3-waiting"), {}, protocol)
    monkeypatch.setattr(routes, "_a_share_today", lambda: today)
    monkeypatch.setattr(
        routes,
        "RankingV3ForwardRepository",
        lambda _: store,
    )

    routes._clear_ranking_v3_forward_runtime_status(protocol)
    try:
        processed = routes._run_ranking_v3_forward_catch_up(
            repo,
            object(),
            context,
            through_date=today,
        )
        payload = routes._ranking_v3_forward_state_payload(repo, context)
    finally:
        routes._clear_ranking_v3_forward_runtime_status(protocol)

    assert processed == []
    assert payload["state"] == "waiting_snapshot"
    assert payload["phase"] == "waiting_snapshot"
    assert payload["blocked_date"] == blocked_date.isoformat()
    assert payload["blocked_code"] == "daily_opportunity_snapshot_missing"
    assert payload["last_attempt_at"]
    assert "no session was fabricated" in str(payload["message"])


def test_forward_catch_up_clears_waiting_state_after_snapshot_recovers(
    monkeypatch,
):
    protocol = build_ranking_v3_protocol()
    session_date = protocol.prospective_shadow_start
    today = trading_day_offset(session_date, 1)
    repo = SimpleNamespace(
        session_factory=object(),
        list_top_daily_opportunity_snapshots=lambda **_: [object()],
    )
    store = SimpleNamespace(load_snapshot=lambda _: None)
    context = (SimpleNamespace(run_id="run-v3-recovered"), {}, protocol)
    result = SimpleNamespace(session_date=session_date, ledger_status="pending")
    monkeypatch.setattr(routes, "_a_share_today", lambda: today)
    monkeypatch.setattr(routes, "RankingV3ForwardRepository", lambda _: store)
    monkeypatch.setattr(
        routes,
        "run_ranking_v3_forward_day",
        lambda *_args, **_kwargs: result,
    )

    routes._set_ranking_v3_forward_waiting_snapshot(protocol, session_date)
    try:
        processed = routes._run_ranking_v3_forward_catch_up(
            repo,
            object(),
            context,
            through_date=today,
        )
        runtime_status = routes._ranking_v3_forward_runtime_status_for(protocol)
    finally:
        routes._clear_ranking_v3_forward_runtime_status(protocol)

    assert processed == [result]
    assert runtime_status is None


def test_forward_state_maps_repository_conflict_to_http_409(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'forward-state-conflict.db'}",
    )
    monkeypatch.setattr(
        routes,
        "_ranking_v3_forward_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RankingV3ForwardConflictError("forward revision conflict")
        ),
    )
    client = TestClient(create_app())

    response = client.get("/api/ranking-v3/forward/state")

    assert response.status_code == 409
    assert response.json()["detail"] == "forward revision conflict"


def test_forward_state_maps_sql_repository_conflict_to_http_409(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'forward-state-sql-conflict.db'}",
    )
    monkeypatch.setattr(
        routes,
        "_ranking_v3_forward_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IntegrityError("insert forward ledger", {}, Exception("unique conflict"))
        ),
    )
    client = TestClient(create_app())

    response = client.get("/api/ranking-v3/forward/state")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Ranking V3 forward ledger repository conflict"
    )


def test_forward_run_once_maps_state_conflict_to_http_409(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'forward-run-state-conflict.db'}",
    )
    context = (SimpleNamespace(run_id="run-v3"), {}, SimpleNamespace())
    monkeypatch.setattr(
        routes,
        "_ranking_v3_forward_context",
        lambda *_args, **_kwargs: context,
    )
    monkeypatch.setattr(routes, "build_market_data_provider", lambda _: object())
    monkeypatch.setattr(
        routes,
        "_run_ranking_v3_forward_catch_up",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RankingV3ForwardStateError("approved forward ledger is immutable")
        ),
    )
    client = TestClient(create_app())

    response = client.post("/api/ranking-v3/forward/run-once")

    assert response.status_code == 409
    assert response.json()["detail"] == "approved forward ledger is immutable"


def test_free_automation_cycle_runs_forward_shadow_without_seeding_paper(
    monkeypatch,
):
    repo = SimpleNamespace()
    paper_repo = SimpleNamespace(list_trades=lambda **_: [])
    context = (SimpleNamespace(run_id="run-v3"), {}, SimpleNamespace())
    result = SimpleNamespace(session_date=date(2026, 7, 27))
    monkeypatch.setattr(routes, "_repo", lambda: repo)
    monkeypatch.setattr(routes, "_paper_repo", lambda: paper_repo)
    monkeypatch.setattr(
        routes,
        "_paper_seed_risk_gate",
        lambda *_: (
            True,
            {
                "paper_risk_gate_action": "allow_new_entries",
                "paper_risk_gate_reason": "test",
            },
        ),
    )
    monkeypatch.setattr(
        routes,
        "_ranking_v3_forward_context",
        lambda current_repo, run_id=None: context,
    )
    monkeypatch.setattr(
        routes,
        "_run_ranking_v3_forward_catch_up",
        lambda current_repo, provider, current, through_date: [result],
    )
    monkeypatch.setattr(
        routes,
        "_ranking_v3_forward_state_payload",
        lambda current_repo, current: {
            "state": "shadow_running",
            "validation_run_id": "run-v3",
            "evaluation": {
                "metrics": {
                    "session_count": 1,
                    "completed_trade_count": 0,
                }
            },
            "release_proof_available": False,
        },
    )
    monkeypatch.setattr(routes, "build_market_data_provider", lambda _: object())

    cycle = routes._run_auto_processing_cycle(
        AutoProcessingSettings(
            provider="free",
            run_scan=False,
            seed_paper=False,
            update_paper=False,
            run_alerts=False,
        )
    )

    assert cycle.paper_created == 0
    assert cycle.data_health["ranking_v3_forward_state"] == "shadow_running"
    assert cycle.data_health["ranking_v3_forward_processed_sessions"] == "1"
    assert cycle.data_health["ranking_v3_forward_release_proof"] == "false"
