from types import SimpleNamespace

from fastapi.testclient import TestClient

from qagent.api import routes
from qagent.app import create_app
from qagent.storage.tables import HistoricalDataRevisionRow


def test_historical_backfill_api_creates_background_job(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-api.db'}",
    )
    repo = routes._repo()
    repo.replace_tradable_instruments(
        [
            SimpleNamespace(
                instrument_id="CN:000001",
                symbol="000001",
                name="平安银行",
                label="平安银行 000001.SZ",
                asset_type="stock",
                exchange="SZ",
                source="test",
            )
        ]
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_history_task_executor", FakeExecutor())
    client = TestClient(create_app())

    response = client.post(
        "/api/historical-data/backfill"
        "?provider=free&symbols=CN:000001&start=2026-01-01&end=2026-01-09"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["phase"] == "queued"
    assert body["symbols"] == ["CN:000001"]
    assert body["progress"] == 0
    assert submitted
    assert submitted[0][1] == (body["job_id"],)

    detail = client.get(f"/api/historical-data/backfill/{body['job_id']}")
    latest = client.get("/api/historical-data/backfill/latest?provider=free")

    assert detail.status_code == 200
    assert detail.json()["job_id"] == body["job_id"]
    assert latest.status_code == 200
    assert latest.json()["job_id"] == body["job_id"]


def test_full_market_historical_backfill_persists_scope_without_eager_symbols(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-full-market-api.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_history_task_executor", FakeExecutor())
    client = TestClient(create_app())

    response = client.post(
        "/api/historical-data/backfill"
        "?provider=free&scope=full-a-share&batch_size=25"
        "&start=2023-01-03&end=2025-12-31"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["symbols"] == []
    assert body["total_symbols"] == 0
    assert body["data_health"]["backfill_scope"] == "full-a-share"
    assert body["data_health"]["backfill_batch_size"] == "25"
    assert body["data_health"]["backfill_auto_validate"] == "true"
    assert submitted[0][1] == (body["job_id"],)


def test_completed_full_market_backfill_queues_walk_forward_when_coverage_is_ready(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-validation-pipeline.db'}",
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_walk_forward_task_executor", FakeExecutor())
    routes._submitted_walk_forward_jobs.clear()
    repo = routes._repo()
    job = repo.create_historical_backfill_job(
        "free",
        ["CN:000001"],
        start=routes.date(2025, 1, 2),
        end=routes.date(2025, 3, 31),
        data_health={
            "backfill_scope": "full-a-share",
            "backfill_auto_validate": "true",
        },
    )
    job = repo.update_historical_backfill_job(job.job_id, status="succeeded")
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=11))
        session.commit()
    item = SimpleNamespace(
        asset_type="stock",
        bar_coverage_ratio=1.0,
        adjustment_coverage_ratio=1.0,
        tradability_coverage_ratio=1.0,
        universe_snapshot_rows=4,
        profile_rows=1,
        fundamental_rows=4,
    )
    result = SimpleNamespace(
        job=job,
        manifest=SimpleNamespace(
            instruments=[item],
            data_health={"historical_benchmark_price_ready": "4/4"},
        ),
    )

    state = routes._continue_validation_pipeline(result)
    stored = repo.get_historical_backfill_job(job.job_id)
    walk_jobs = repo.list_walk_forward_jobs(provider="free", limit=5)

    assert state == "walk_forward_queued"
    assert stored.data_health["validation_pipeline_gate"] == "ready"
    assert stored.data_health["validation_pipeline_state"] == "walk_forward_queued"
    assert stored.data_health["validation_pipeline_blockers"] == ""
    assert walk_jobs[0].dataset_revision == 11
    assert submitted[0][1] == (walk_jobs[0].job_id,)


def test_completed_full_market_backfill_blocks_validation_on_missing_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-validation-blocked.db'}",
    )
    repo = routes._repo()
    job = repo.create_historical_backfill_job(
        "free",
        ["CN:000001"],
        start=routes.date(2025, 1, 2),
        end=routes.date(2025, 3, 31),
        data_health={
            "backfill_scope": "full-a-share",
            "backfill_auto_validate": "true",
        },
    )
    job = repo.update_historical_backfill_job(job.job_id, status="succeeded_with_errors")
    item = SimpleNamespace(
        asset_type="stock",
        bar_coverage_ratio=1.0,
        adjustment_coverage_ratio=1.0,
        tradability_coverage_ratio=0.0,
        universe_snapshot_rows=0,
        profile_rows=1,
        fundamental_rows=0,
    )
    result = SimpleNamespace(
        job=job,
        manifest=SimpleNamespace(
            instruments=[item],
            data_health={"historical_benchmark_price_ready": "2/4"},
        ),
    )

    state = routes._continue_validation_pipeline(result)
    stored = repo.get_historical_backfill_job(job.job_id)

    assert state == "blocked_data_coverage"
    assert stored.data_health["validation_pipeline_gate"] == "insufficient"
    assert "tradability<90%" in stored.data_health["validation_pipeline_blockers"]
    assert "fundamental<80%" in stored.data_health["validation_pipeline_blockers"]
    assert "benchmarks<100%" in stored.data_health["validation_pipeline_blockers"]
    assert repo.list_walk_forward_jobs(provider="free", limit=5) == []


def test_full_market_historical_backfill_rejects_explicit_symbols(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-full-market-symbols.db'}",
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/historical-data/backfill"
        "?provider=free&scope=full-a-share&symbols=CN:000001"
        "&start=2023-01-03&end=2025-12-31"
    )

    assert response.status_code == 400
    assert "cannot be combined" in response.json()["detail"]


def test_historical_backfill_restore_resubmits_interrupted_job(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-restore-api.db'}",
    )
    job = routes._repo().create_historical_backfill_job(
        "free",
        ["CN:000001"],
        start=routes.date(2026, 1, 1),
        end=routes.date(2026, 1, 9),
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_history_task_executor", FakeExecutor())
    routes._submitted_historical_jobs.clear()

    restored = routes.restore_historical_backfill_from_storage()

    assert restored == job.job_id
    assert submitted
    assert submitted[0][1] == (job.job_id,)


def test_historical_coverage_api_reports_missing_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-coverage-api.db'}",
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/historical-data/coverage"
        "?provider=free&symbols=CN:000001&start=2026-01-01&end=2026-01-09"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_instruments"] == 1
    assert body["summary"]["missing_instruments"] == 1
    assert body["instruments"][0]["status"] == "missing"
    assert "bar_coverage_below_95pct" in body["instruments"][0]["issues"]
