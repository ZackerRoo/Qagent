import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from qagent.api import routes
from qagent.app import create_app
from qagent.backtesting import experiment
from qagent.backtesting.walk_forward import WalkForwardSnapshot
from qagent.storage.tables import (
    HistoricalDataRevisionRow,
    WalkForwardJobRow,
    WalkForwardRunRow,
)


def test_walk_forward_run_queries_return_latest_and_complete_payload(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-api.db'}",
    )
    now = datetime.now(timezone.utc)
    payload = {
        "owner_run_id": "api-walk-forward-1",
        "snapshots": [{"decision_date": "2025-01-02"}],
        "cost_sensitivity": [{"key": "stress"}],
    }
    data_health = {
        "walk_forward_top_5_oos_gate": "insufficient",
        "walk_forward_equal_weight_benchmark": "ready",
    }
    with routes._repo().session_factory() as session:
        session.add(
            WalkForwardRunRow(
                run_id="api-walk-forward-1",
                provider="free",
                status="succeeded",
                start_date=date(2024, 1, 2),
                end_date=date(2025, 1, 2),
                dataset_revision=9,
                rebalance_step_sessions=5,
                lookback_days=400,
                snapshot_count=52,
                top_5_trade_count=24,
                top_10_trade_count=48,
                top_5_return_pct=Decimal("8.25"),
                top_10_return_pct=Decimal("7.10"),
                top_5_oos_trades=12,
                top_10_oos_trades=18,
                top_5_oos_gate="insufficient",
                top_10_oos_gate="insufficient",
                reproducibility_digest="digest-1",
                payload_json=json.dumps(payload),
                data_health=json.dumps(data_health),
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    client = TestClient(create_app())
    listed = client.get("/api/walk-forward/runs?provider=free&limit=5")
    latest = client.get("/api/walk-forward/runs/latest?provider=free")
    detail = client.get("/api/walk-forward/runs/api-walk-forward-1")
    missing = client.get("/api/walk-forward/runs/missing")

    assert listed.status_code == 200
    assert listed.json()["runs"][0]["run_id"] == "api-walk-forward-1"
    assert latest.status_code == 200
    assert latest.json()["top_5_return_pct"] == 8.25
    assert detail.status_code == 200
    assert detail.json()["payload"]["cost_sensitivity"][0]["key"] == "stress"
    assert detail.json()["data_health"]["walk_forward_equal_weight_benchmark"] == "ready"
    assert missing.status_code == 404


def test_walk_forward_job_is_persisted_and_submitted_in_background(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-job-api.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=7))
        session.commit()

    client = TestClient(create_app())
    response = client.post(
        "/api/walk-forward/jobs"
        "?provider=free&start=2025-01-02&end=2025-03-31"
        "&step_sessions=10&lookback_days=400"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["phase"] == "queued"
    assert body["dataset_revision"] == 7
    assert body["progress"] == 0
    assert body["lease_maintenance_count"] == 0
    assert body["lease_recovery_count"] == 0
    assert body["last_lease_heartbeat_at"] is None
    assert body["total_snapshots"] > 0
    assert body["experiment_manifest"]["strategy_registry_digest"]
    assert body["experiment_manifest"]["execution_rule_set_version"]
    assert submitted[0][1] == (body["job_id"],)

    detail = client.get(f"/api/walk-forward/jobs/{body['job_id']}")
    latest = client.get("/api/walk-forward/jobs/latest?provider=free")

    assert detail.status_code == 200
    assert detail.json()["checkpoint_count"] == 0
    assert detail.json()["lease_maintenance_count"] == 0
    assert latest.status_code == 200
    assert latest.json()["job_id"] == body["job_id"]


def test_walk_forward_rejects_step_that_breaks_v3_dependence_model(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-step.db'}",
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/walk-forward/jobs"
        "?provider=free&start=2025-01-02&end=2025-03-31"
        "&step_sessions=5&lookback_days=400"
    )

    assert response.status_code == 400
    assert "step_sessions=10" in response.json()["detail"]


def test_walk_forward_restore_resubmits_persisted_job(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-restore-api.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()
    repo = routes._repo()
    job = repo.create_walk_forward_job(
        job_id="walk-forward-resume",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=9,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest={"dataset_revision": 9},
    )

    restored = routes.restore_walk_forward_job_from_storage()

    assert restored == job.job_id
    assert submitted[0][1] == (job.job_id,)


def test_walk_forward_submission_is_released_when_worker_finishes(monkeypatch):
    callbacks = []

    class FakeFuture:
        def add_done_callback(self, callback):
            callbacks.append(callback)

    class FakeExecutor:
        def submit(self, _fn, *_args, **_kwargs):
            return FakeFuture()

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()

    routes._submit_walk_forward_job("walk-forward-process-test")

    assert "walk-forward-process-test" in routes._submitted_walk_forward_jobs
    assert len(callbacks) == 1
    callbacks[0](None)
    assert "walk-forward-process-test" not in routes._submitted_walk_forward_jobs


def test_walk_forward_reconciles_cached_policy_without_refreshing_market_age(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-cache-reconcile.db'}",
    )
    repo = routes._repo()
    cached = repo.save_scan_result_cache(
        cache_key=routes.full_market_batch_cache_key("free", True),
        provider="free",
        mode="full_market_batch",
        symbols=["CN:000001"],
        payload={
            "cards": [{"card_id": "card-old"}],
            "strategy_governance": [],
            "data_health": {"scan_market_data": "ready"},
        },
    )

    card = SimpleNamespace(card_id="card-new")
    audit = SimpleNamespace(
        card_id="card-new",
        model_dump=lambda mode: {
            "card_id": "card-new",
            "gate_decision": {
                "action": "disable",
                "paper_candidate_eligible": False,
            },
        },
    )
    final_policy = SimpleNamespace(
        cards=[card],
        audits=[audit],
        data_health={"walk_forward_feedback_gate": "rejected"},
    )
    monkeypatch.setattr(routes, "_cards_from_payload", lambda _cards: [card])
    monkeypatch.setattr(
        routes,
        "apply_final_recommendation_policy",
        lambda *_args, **_kwargs: final_policy,
    )
    monkeypatch.setattr(routes, "sort_recommendation_cards", lambda cards: cards)
    monkeypatch.setattr(
        routes,
        "governed_card_payloads",
        lambda _cards, _audits: [
            {
                "card_id": "card-new",
                "strategy_governance": audit.model_dump(mode="json"),
            }
        ],
    )
    monkeypatch.setattr(
        routes,
        "load_latest_walk_forward_validation",
        lambda *_args, **_kwargs: SimpleNamespace(status="rejected"),
    )
    monkeypatch.setattr(
        routes,
        "load_strategy_governance_context",
        lambda *_args, **_kwargs: SimpleNamespace(strategies={}),
    )

    reconciled = routes._reconcile_full_market_caches_after_walk_forward(
        repo,
        provider="free",
        run_id="walk-forward-new",
    )
    updated = repo.get_recent_scan_result_cache(
        routes.full_market_batch_cache_key("free", True),
        max_age=timedelta(days=1),
    )

    assert reconciled == 1
    assert updated is not None
    assert updated.cache_id == cached.cache_id
    assert updated.created_at == cached.created_at
    assert updated.payload["cards"][0]["card_id"] == "card-new"
    assert updated.payload["data_health"]["scan_market_data"] == "ready"
    assert updated.payload["data_health"]["walk_forward_feedback_gate"] == "rejected"
    assert (
        updated.payload["data_health"]["walk_forward_cache_reconciled_run_id"] == "walk-forward-new"
    )
    assert (
        updated.payload["data_health"]["walk_forward_cache_market_data_created_at"]
        == cached.created_at.isoformat()
    )


def test_failed_walk_forward_job_retries_from_persisted_checkpoints(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-retry-api.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=9))
        session.commit()
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=9,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 3, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    job = repo.create_walk_forward_job(
        job_id="walk-forward-retry",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=9,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest=manifest.model_dump(mode="json"),
    )
    checkpoint = {"decision_date": "2025-01-02"}
    repo.update_walk_forward_job(
        job.job_id,
        status="failed",
        phase="failed",
        processed_snapshots=1,
        checkpoints=[checkpoint],
        error="lease expired",
        finished_at=datetime.now(timezone.utc),
    )
    client = TestClient(create_app())

    response = client.post(f"/api/walk-forward/jobs/{job.job_id}/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["checkpoint_count"] == 1
    assert response.json()["error"] is None
    assert response.json()["finished_at"] is None
    assert submitted[0][1] == (job.job_id,)


def test_current_walk_forward_checkpoint_envelope_rejects_tampering(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-checkpoint-integrity.db'}",
    )
    repo = routes._repo()
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=9,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 3, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    job = repo.create_walk_forward_job(
        job_id="walk-forward-checkpoint-integrity",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=9,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest=manifest.model_dump(mode="json"),
    )
    repo.update_walk_forward_job(
        job.job_id,
        status="failed",
        checkpoints=[{"decision_date": "2025-01-02"}],
    )
    with repo.session_factory() as session:
        row = session.get(WalkForwardJobRow, job.job_id)
        original = row.checkpoints_json

    def change_checkpoint(envelope):
        envelope["checkpoints"][0]["decision_date"] = "2025-01-03"

    def change_manifest_binding(envelope):
        envelope["experiment_digest"] = "0" * 64

    def change_execution_plan(envelope):
        envelope["execution_plan"]["lookback_days"] = 399

    for mutate, error in (
        (change_checkpoint, "checkpoint chain"),
        (change_manifest_binding, "checkpoint binding"),
        (change_execution_plan, "checkpoint binding"),
    ):
        envelope = json.loads(original)
        mutate(envelope)
        with repo.session_factory() as session:
            row = session.get(WalkForwardJobRow, job.job_id)
            row.checkpoints_json = json.dumps(envelope, sort_keys=True)
            session.commit()

        with pytest.raises(ValueError, match=error):
            repo.get_walk_forward_job(job.job_id)

        with repo.session_factory() as session:
            row = session.get(WalkForwardJobRow, job.job_id)
            row.checkpoints_json = original
            session.commit()

    with repo.session_factory() as session:
        row = session.get(WalkForwardJobRow, job.job_id)
        row.checkpoints_json = json.dumps([{"decision_date": "2025-01-02"}])
        session.commit()
    with pytest.raises(ValueError, match="lack integrity envelope"):
        repo.get_walk_forward_job(job.job_id)


def test_checkpoint_integrity_failure_stops_runner_and_marks_job_failed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-checkpoint-stop.db'}",
    )
    repo = routes._repo()
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=9,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 3, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    job = repo.create_walk_forward_job(
        job_id="walk-forward-checkpoint-stop",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=9,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest=manifest.model_dump(mode="json"),
    )
    repo.update_walk_forward_job(
        job.job_id,
        status="running",
        checkpoints=[{"decision_date": "2025-01-02"}],
    )
    with repo.session_factory() as session:
        row = session.get(WalkForwardJobRow, job.job_id)
        envelope = json.loads(row.checkpoints_json)
        envelope["checkpoints"][0]["decision_date"] = "2025-01-03"
        row.checkpoints_json = json.dumps(envelope, sort_keys=True)
        session.commit()

    monkeypatch.setattr(
        routes,
        "run_full_market_walk_forward_selection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("corrupt checkpoints must never reach the runner")
        ),
    )

    routes._run_walk_forward_job_safely(job.job_id)

    with repo.session_factory() as session:
        row = session.get(WalkForwardJobRow, job.job_id)
        assert row.status == "failed"
        assert row.phase == "failed"
        assert "checkpoint integrity validation failed" in row.error


def test_running_walk_forward_job_can_be_cancelled_without_losing_checkpoints(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-cancel-api.db'}",
    )
    repo = routes._repo()
    job = repo.create_walk_forward_job(
        job_id="walk-forward-cancel",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=9,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest={"dataset_revision": 9},
    )
    repo.update_walk_forward_job(
        job.job_id,
        status="running",
        phase="historical_replay",
        processed_snapshots=1,
        checkpoints=[{"decision_date": "2025-01-02"}],
    )

    client = TestClient(create_app())
    response = client.post(f"/api/walk-forward/jobs/{job.job_id}/cancel")
    repeated = client.post(f"/api/walk-forward/jobs/{job.job_id}/cancel")

    assert response.status_code == 200
    assert repeated.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["phase"] == "cancelled"
    assert response.json()["checkpoint_count"] == 1
    assert response.json()["result_run_id"] is None
    assert response.json()["finished_at"] is not None


def test_cancelled_walk_forward_runner_does_not_publish_or_overwrite_status(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-cancel-runner.db'}",
    )
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=9))
        session.commit()
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=9,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 3, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    job = repo.create_walk_forward_job(
        job_id="walk-forward-cancel-runner",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=9,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest=manifest.model_dump(mode="json"),
    )

    def cancel_during_progress(*_args, progress_callback, **_kwargs):
        repo.update_walk_forward_job(
            job.job_id,
            status="cancelled",
            phase="cancelled",
            error="superseded protocol",
            finished_at=datetime.now(timezone.utc),
        )
        progress_callback(
            routes.WalkForwardProgress(
                phase="historical_replay",
                processed_snapshots=1,
                total_snapshots=12,
                current_date=date(2025, 1, 2),
            )
        )
        raise AssertionError("cancelled progress must stop the validation")

    monkeypatch.setattr(
        routes,
        "run_full_market_walk_forward_selection",
        cancel_during_progress,
    )
    monkeypatch.setattr(
        repo,
        "save_walk_forward_run",
        lambda _result: (_ for _ in ()).throw(
            AssertionError("cancelled validation must not publish a result")
        ),
    )

    routes._run_walk_forward_job_safely(job.job_id)

    stored = repo.get_walk_forward_job(job.job_id)
    assert stored.status == "cancelled"
    assert stored.phase == "cancelled"
    assert stored.error == "superseded protocol"
    assert stored.result_run_id is None


def test_failed_walk_forward_retry_rejects_changed_experiment(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-retry-stale-api.db'}",
    )
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=9))
        session.commit()
    job = repo.create_walk_forward_job(
        job_id="walk-forward-retry-stale",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=9,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest={"experiment_digest": "old-definition"},
    )
    repo.update_walk_forward_job(job.job_id, status="failed", phase="failed")
    client = TestClient(create_app())

    response = client.post(f"/api/walk-forward/jobs/{job.job_id}/retry")

    assert response.status_code == 409
    assert "experiment definition changed" in response.json()["detail"]


def test_failed_walk_forward_retry_reuses_checkpoints_after_execution_rule_upgrade(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-execution-upgrade-api.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=9))
        session.commit()
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"revision":"old"}', encoding="utf-8")
    monkeypatch.setattr(experiment, "RULES_PATH", rules_path)
    stored_manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=9,
        start_date=date(2021, 11, 1),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=10,
        lookback_days=400,
    )
    rules_path.write_text('{"revision":"new"}', encoding="utf-8")
    job = repo.create_walk_forward_job(
        job_id="walk-forward-execution-upgrade",
        provider="free",
        start=date(2021, 11, 1),
        end=date(2025, 12, 31),
        dataset_revision=9,
        rebalance_step_sessions=10,
        lookback_days=400,
        total_snapshots=102,
        experiment_manifest=stored_manifest.model_dump(mode="json"),
    )
    repo.update_walk_forward_job(
        job.job_id,
        status="failed",
        phase="failed",
        processed_snapshots=102,
        checkpoints=[{"decision_date": "2021-11-01"}],
        error="old execution metadata missing",
    )
    client = TestClient(create_app())

    response = client.post(f"/api/walk-forward/jobs/{job.job_id}/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["checkpoint_count"] == 1
    assert (
        response.json()["experiment_manifest"]["execution_rules_digest"]
        != stored_manifest.execution_rules_digest
    )
    assert submitted[0][1] == (job.job_id,)


def test_walk_forward_restore_resubmits_every_persisted_job(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-restore-all-api.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()
    repo = routes._repo()
    first = repo.create_walk_forward_job(
        job_id="walk-forward-resume-first",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=9,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest={"dataset_revision": 9},
    )
    second = repo.create_walk_forward_job(
        job_id="walk-forward-resume-second",
        provider="free",
        start=date(2025, 4, 1),
        end=date(2025, 6, 30),
        dataset_revision=9,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest={"dataset_revision": 9},
    )

    restored = routes.restore_walk_forward_job_from_storage()

    assert restored == first.job_id
    assert [item[1] for item in submitted] == [(first.job_id,), (second.job_id,)]


def test_walk_forward_job_only_reuses_identical_active_experiment(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-active-identity-api.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=7))
        session.commit()

    first = routes._create_or_get_walk_forward_job(
        repo=repo,
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        step_sessions=5,
        lookback_days=400,
    )
    reused = routes._create_or_get_walk_forward_job(
        repo=repo,
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        step_sessions=5,
        lookback_days=400,
    )
    second = routes._create_or_get_walk_forward_job(
        repo=repo,
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 6, 30),
        step_sessions=5,
        lookback_days=400,
    )

    assert reused.job_id == first.job_id
    assert second.job_id != first.job_id
    assert len(repo.list_walk_forward_jobs(provider="free", limit=10)) == 2
    assert [item[1] for item in submitted] == [(first.job_id,), (second.job_id,)]


def test_new_walk_forward_job_reuses_completed_selection_snapshots(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-reuse-run-api.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()
    repo = routes._repo()
    start = date(2025, 1, 2)
    end = date(2025, 1, 15)
    sessions = routes.trading_sessions_in_range(start, end)[::5]
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=start,
        end_date=end,
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    snapshots = [
        WalkForwardSnapshot(
            decision_date=decision_date,
            historical_universe_size=1,
            eligible_size=1,
            suspended_count=0,
            st_excluded_count=0,
            missing_tradability_count=0,
        ).model_dump(mode="json")
        for decision_date in sessions
    ]
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=7))
        session.commit()
    source = repo.create_walk_forward_job(
        job_id="walk-forward-base-v4",
        provider="free",
        start=start,
        end=end,
        dataset_revision=7,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=len(snapshots),
        experiment_manifest=manifest.model_dump(mode="json"),
    )
    repo.update_walk_forward_job(
        source.job_id,
        status="succeeded",
        processed_snapshots=len(snapshots),
        current_date=sessions[-1],
        checkpoints=snapshots,
    )

    job = routes._create_or_get_walk_forward_job(
        repo=repo,
        provider="free",
        start=start,
        end=end,
        step_sessions=5,
        lookback_days=400,
    )

    assert len(job.checkpoints) == len(sessions)
    assert job.processed_snapshots == len(sessions)
    assert job.current_date == sessions[-1]
    assert submitted[0][1] == (job.job_id,)


def test_new_walk_forward_job_never_reuses_partial_prefix_from_failed_validation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-reuse-checkpoints-api.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()
    repo = routes._repo()
    start = date(2025, 1, 2)
    end = date(2025, 2, 28)
    sessions = routes.trading_sessions_in_range(start, end)[::5]
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=start,
        end_date=end,
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    checkpoints = [
        WalkForwardSnapshot(
            decision_date=decision_date,
            historical_universe_size=1,
            eligible_size=1,
            suspended_count=0,
            st_excluded_count=0,
            missing_tradability_count=0,
        ).model_dump(mode="json")
        for decision_date in sessions[:2]
    ]
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=7))
        session.commit()
    source = repo.create_walk_forward_job(
        job_id="walk-forward-failed-source",
        provider="free",
        start=start,
        end=end,
        dataset_revision=7,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=len(sessions),
        experiment_manifest=manifest.model_dump(mode="json"),
    )
    repo.update_walk_forward_job(
        source.job_id,
        status="failed",
        processed_snapshots=len(checkpoints),
        current_date=sessions[1],
        checkpoints=checkpoints,
        error="validation protocol superseded",
    )

    job = routes._create_or_get_walk_forward_job(
        repo=repo,
        provider="free",
        start=start,
        end=end,
        step_sessions=5,
        lookback_days=400,
    )

    assert job.job_id != source.job_id
    assert job.checkpoints == []
    assert job.processed_snapshots == 0
    assert job.current_date is None
    assert submitted[0][1] == (job.job_id,)


def test_checkpoint_reuse_ignores_incompatible_prior_run_payloads():
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 3, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )

    class IncompatibleRunRepository:
        def list_walk_forward_jobs(self, **_kwargs):
            return []

        def list_walk_forward_runs(self, **_kwargs):
            raise ValueError("prior result digest does not match current schema")

    checkpoints = routes._reusable_walk_forward_checkpoints(
        IncompatibleRunRepository(),
        manifest=manifest,
        sessions=[date(2025, 1, 2)],
    )

    assert checkpoints == []


def test_walk_forward_completed_run_requires_matching_experiment_digest():
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 3, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    run = SimpleNamespace(
        dataset_revision=7,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 3, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
        payload={
            "experiment_manifest": manifest.model_dump(mode="json"),
        },
    )

    assert routes._walk_forward_run_matches_manifest(run, manifest) is True

    run.payload["experiment_manifest"]["experiment_digest"] = "stale-definition"

    assert routes._walk_forward_run_matches_manifest(run, manifest) is False


def test_walk_forward_runner_rejects_stale_experiment_definition(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-stale-definition-api.db'}",
    )
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=7))
        session.commit()
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 3, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    job = repo.create_walk_forward_job(
        job_id="walk-forward-stale-definition",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=7,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest=manifest.model_dump(mode="json"),
    )
    with repo.session_factory() as session:
        row = session.get(WalkForwardJobRow, job.job_id)
        payload = json.loads(row.experiment_manifest_json)
        payload["experiment_digest"] = "stale-definition"
        row.experiment_manifest_json = json.dumps(payload, sort_keys=True)
        session.commit()
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("stale experiment must not run")

    monkeypatch.setattr(routes, "run_full_market_walk_forward_selection", fail_if_called)

    routes._run_walk_forward_job_safely(job.job_id)

    with repo.session_factory() as session:
        stored = session.get(WalkForwardJobRow, job.job_id)
        assert stored.status == "failed"
        assert "integrity validation failed" in stored.error
    assert called is False


def test_walk_forward_runner_persists_live_lease_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'walk-forward-lease-telemetry-api.db'}",
    )
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=7))
        session.commit()
    manifest = routes.build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 3, 31),
        rebalance_step_sessions=5,
        lookback_days=400,
    )
    job = repo.create_walk_forward_job(
        job_id="walk-forward-live-lease-telemetry",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=7,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest=manifest.model_dump(mode="json"),
    )
    heartbeat_at = datetime.now(timezone.utc)

    def fake_walk_forward(*args, lease_maintenance_callback, **kwargs):
        lease_maintenance_callback(3, 1, heartbeat_at)
        raise RuntimeError("stop after telemetry")

    monkeypatch.setattr(
        routes,
        "run_full_market_walk_forward_selection",
        fake_walk_forward,
    )

    routes._run_walk_forward_job_safely(job.job_id)

    stored = repo.get_walk_forward_job(job.job_id)
    assert stored.status == "failed"
    assert stored.lease_maintenance_count == 3
    assert stored.lease_recovery_count == 1
    assert stored.last_lease_heartbeat_at == heartbeat_at
