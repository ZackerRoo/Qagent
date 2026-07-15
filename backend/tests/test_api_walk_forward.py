import json
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from qagent.api import routes
from qagent.app import create_app
from qagent.storage.tables import HistoricalDataRevisionRow, WalkForwardRunRow


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
        "&step_sessions=5&lookback_days=400"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["phase"] == "queued"
    assert body["dataset_revision"] == 7
    assert body["progress"] == 0
    assert body["total_snapshots"] > 0
    assert body["experiment_manifest"]["strategy_registry_digest"]
    assert body["experiment_manifest"]["execution_rule_set_version"]
    assert submitted[0][1] == (body["job_id"],)

    detail = client.get(f"/api/walk-forward/jobs/{body['job_id']}")
    latest = client.get("/api/walk-forward/jobs/latest?provider=free")

    assert detail.status_code == 200
    assert detail.json()["checkpoint_count"] == 0
    assert latest.status_code == 200
    assert latest.json()["job_id"] == body["job_id"]


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
            "experiment_manifest": {
                "experiment_digest": manifest.experiment_digest,
            }
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
    ).model_dump(mode="json")
    manifest["experiment_digest"] = "stale-definition"
    job = repo.create_walk_forward_job(
        job_id="walk-forward-stale-definition",
        provider="free",
        start=date(2025, 1, 2),
        end=date(2025, 3, 31),
        dataset_revision=7,
        rebalance_step_sessions=5,
        lookback_days=400,
        total_snapshots=12,
        experiment_manifest=manifest,
    )
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("stale experiment must not run")

    monkeypatch.setattr(routes, "run_full_market_walk_forward_selection", fail_if_called)

    routes._run_walk_forward_job_safely(job.job_id)

    stored = repo.get_walk_forward_job(job.job_id)
    assert stored.status == "failed"
    assert "experiment definition changed" in stored.error
    assert called is False
