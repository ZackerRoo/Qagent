from types import SimpleNamespace

from fastapi.testclient import TestClient

from qagent.api import routes
from qagent.app import create_app
from qagent.storage.tables import HistoricalDataRevisionRow


def _reference_health(
    *,
    index_ready: int = 4,
    index_expected: int = 4,
    request_status: str = "succeeded",
) -> dict[str, str]:
    return {
        "historical_benchmark_price_ready": "4/4",
        "historical_benchmark_ready": str(index_ready),
        "historical_index_expected_snapshots": str(index_expected),
        "historical_reference_request_status": request_status,
    }


def _ready_instrument(*, industry_rows: int = 1, asset_type: str = "stock"):
    start = routes.date(2025, 1, 2)
    return SimpleNamespace(
        asset_type=asset_type,
        bar_coverage_ratio=1.0,
        adjustment_coverage_ratio=1.0,
        tradability_coverage_ratio=1.0,
        universe_snapshot_rows=4,
        first_universe_date=start,
        profile_rows=1,
        fundamental_rows=4 if asset_type == "stock" else 0,
        first_fundamental_date=(
            routes.date(2024, 12, 31) if asset_type == "stock" else None
        ),
        industry_rows=industry_rows if asset_type == "stock" else 0,
    )


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
        first_universe_date=routes.date(2025, 1, 2),
        profile_rows=1,
        fundamental_rows=4,
        first_fundamental_date=routes.date(2024, 12, 31),
        industry_rows=1,
    )
    result = SimpleNamespace(
        job=job,
        manifest=SimpleNamespace(
            instruments=[item],
            data_health=_reference_health(),
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
    assert walk_jobs[0].rebalance_step_sessions == 10
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
        first_universe_date=None,
        profile_rows=1,
        fundamental_rows=0,
        first_fundamental_date=None,
    )
    result = SimpleNamespace(
        job=job,
        manifest=SimpleNamespace(
            instruments=[item],
            data_health={
                **_reference_health(index_ready=2),
                "historical_benchmark_price_ready": "2/4",
            },
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


def test_validation_readiness_accepts_listing_aware_universe_history():
    start = routes.date(2025, 1, 2)
    item = SimpleNamespace(
        asset_type="stock",
        bar_coverage_ratio=1.0,
        adjustment_coverage_ratio=1.0,
        tradability_coverage_ratio=1.0,
        universe_snapshot_rows=3,
        first_universe_date=routes.date(2025, 2, 10),
        profile_rows=1,
        listing_date=routes.date(2025, 2, 7),
        fundamental_rows=2,
        first_fundamental_date=routes.date(2025, 2, 7),
        industry_rows=1,
    )

    readiness = routes._historical_validation_readiness(
        SimpleNamespace(
            instruments=[item],
            data_health=_reference_health(),
        ),
        start=start,
    )

    assert readiness["validation_pipeline_gate"] == "ready"
    assert readiness["validation_pipeline_universe_coverage"] == "1.0000"


def test_completed_full_market_backfill_blocks_recent_only_history(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-recent-only.db'}",
    )
    repo = routes._repo()
    start = routes.date(2025, 1, 2)
    job = repo.create_historical_backfill_job(
        "free",
        ["CN:000001"],
        start=start,
        end=routes.date(2025, 3, 31),
        data_health={
            "backfill_scope": "full-a-share",
            "backfill_auto_validate": "true",
        },
    )
    job = repo.update_historical_backfill_job(job.job_id, status="succeeded")
    item = SimpleNamespace(
        asset_type="stock",
        bar_coverage_ratio=1.0,
        adjustment_coverage_ratio=1.0,
        tradability_coverage_ratio=1.0,
        universe_snapshot_rows=4,
        first_universe_date=routes.date(2025, 2, 1),
        profile_rows=1,
        fundamental_rows=4,
        first_fundamental_date=routes.date(2025, 2, 1),
        industry_rows=1,
    )
    result = SimpleNamespace(
        job=job,
        manifest=SimpleNamespace(
            instruments=[item],
            data_health=_reference_health(),
        ),
    )

    state = routes._continue_validation_pipeline(result)
    stored = repo.get_historical_backfill_job(job.job_id)

    assert state == "blocked_data_coverage"
    assert "universe<90%" in stored.data_health["validation_pipeline_blockers"]
    assert "fundamental<80%" in stored.data_health["validation_pipeline_blockers"]
    assert repo.list_walk_forward_jobs(provider="free", limit=5) == []


def test_validation_readiness_accepts_post_start_listing_fundamentals():
    start = routes.date(2023, 1, 3)
    item = SimpleNamespace(
        asset_type="stock",
        bar_coverage_ratio=1.0,
        adjustment_coverage_ratio=1.0,
        tradability_coverage_ratio=1.0,
        universe_snapshot_rows=4,
        first_universe_date=start,
        profile_rows=1,
        listing_date=routes.date(2024, 2, 1),
        fundamental_rows=3,
        first_fundamental_date=routes.date(2024, 4, 30),
        industry_rows=1,
    )
    manifest = SimpleNamespace(
        instruments=[item],
        data_health=_reference_health(),
    )

    readiness = routes._historical_validation_readiness(manifest, start=start)

    assert readiness["validation_pipeline_gate"] == "ready"
    assert readiness["validation_pipeline_fundamental_coverage"] == "1.0000"


def test_validation_readiness_fails_closed_on_reference_timeout():
    readiness = routes._historical_validation_readiness(
        SimpleNamespace(
            instruments=[_ready_instrument()],
            data_health=_reference_health(),
        ),
        start=routes.date(2025, 1, 2),
        backfill_status="succeeded_with_errors",
        backfill_errors=("baostock reference login: request timed out",),
    )

    assert readiness["validation_pipeline_gate"] == "insufficient"
    assert "critical_reference_errors" in readiness["validation_pipeline_blockers"]
    assert readiness["validation_pipeline_critical_reference_error_count"] == "1"
    assert readiness["validation_pipeline_bars_gate"] == "ready"
    assert readiness["validation_pipeline_index_gate"] == "ready"


def test_validation_readiness_fails_closed_on_empty_reference_bundle():
    readiness = routes._historical_validation_readiness(
        SimpleNamespace(
            instruments=[_ready_instrument(industry_rows=0)],
            data_health=_reference_health(
                index_ready=0,
                request_status="empty",
            ),
        ),
        start=routes.date(2025, 1, 2),
    )

    assert readiness["validation_pipeline_gate"] == "insufficient"
    assert "industry<90%" in readiness["validation_pipeline_blockers"]
    assert "index<100%" in readiness["validation_pipeline_blockers"]
    assert "reference_request=empty" in readiness["validation_pipeline_blockers"]
    assert readiness["validation_pipeline_industry_ready"] == "0/1"
    assert readiness["validation_pipeline_index_ready"] == "0/4"


def test_validation_readiness_uses_declared_scope_for_low_reference_coverage():
    instruments = [
        _ready_instrument(industry_rows=1 if index < 8 else 0)
        for index in range(10)
    ]
    readiness = routes._historical_validation_readiness(
        SimpleNamespace(
            instruments=instruments,
            data_health=_reference_health(
                index_ready=9,
                index_expected=10,
            ),
        ),
        start=routes.date(2025, 1, 2),
    )

    assert readiness["validation_pipeline_gate"] == "insufficient"
    assert readiness["validation_pipeline_industry_coverage"] == "0.8000"
    assert readiness["validation_pipeline_industry_ready"] == "8/10"
    assert readiness["validation_pipeline_index_coverage"] == "0.9000"
    assert readiness["validation_pipeline_index_ready"] == "9/10"


def test_validation_readiness_accepts_healthy_scoped_reference_coverage():
    instruments = [
        _ready_instrument(industry_rows=1 if index < 9 else 0)
        for index in range(10)
    ]
    instruments.append(_ready_instrument(asset_type="etf"))
    readiness = routes._historical_validation_readiness(
        SimpleNamespace(
            instruments=instruments,
            data_health=_reference_health(
                index_ready=10,
                index_expected=10,
            ),
        ),
        start=routes.date(2025, 1, 2),
    )

    assert readiness["validation_pipeline_gate"] == "ready"
    assert readiness["validation_pipeline_bars_gate"] == "ready"
    assert readiness["validation_pipeline_bars_ready"] == "11/11"
    assert readiness["validation_pipeline_profile_gate"] == "ready"
    assert readiness["validation_pipeline_profile_ready"] == "11/11"
    assert readiness["validation_pipeline_fundamental_gate"] == "ready"
    assert readiness["validation_pipeline_fundamental_ready"] == "10/10"
    assert readiness["validation_pipeline_industry_gate"] == "ready"
    assert readiness["validation_pipeline_industry_ready"] == "9/10"
    assert readiness["validation_pipeline_index_gate"] == "ready"
    assert readiness["validation_pipeline_index_ready"] == "10/10"
    assert readiness["validation_pipeline_etf_constituent_gate"] == "not_required"
    assert readiness["validation_pipeline_etf_constituent_scope"] == "1"


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


def test_failed_historical_backfill_can_resume_from_saved_checkpoint(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-retry-api.db'}",
    )
    repo = routes._repo()
    job = repo.create_historical_backfill_job(
        "free",
        ["CN:000001", "CN:000002"],
        start=routes.date(2026, 1, 1),
        end=routes.date(2026, 1, 9),
        data_health={"backfill_scope": "symbols", "backfill_phase": "failed"},
    )
    repo.update_historical_backfill_job(
        job.job_id,
        status="failed",
        processed_symbols=1,
        succeeded_symbols=1,
        failed_symbols=1,
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_history_task_executor", FakeExecutor())
    routes._submitted_historical_jobs.clear()
    client = TestClient(create_app())

    response = client.post(f"/api/historical-data/backfill/{job.job_id}/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["processed_symbols"] == 1
    assert body["succeeded_symbols"] == 1
    assert body["failed_symbols"] == 0
    assert body["data_health"]["backfill_resume_requested"] == "true"
    assert body["data_health"]["backfill_resume_count"] == "1"
    assert submitted[0][1] == (job.job_id,)


def test_incomplete_historical_backfill_can_retry_blocked_validation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'historical-retry-blocked-api.db'}",
    )
    repo = routes._repo()
    job = repo.create_historical_backfill_job(
        "free",
        ["CN:000001"],
        start=routes.date(2026, 1, 1),
        end=routes.date(2026, 1, 9),
        data_health={
            "backfill_scope": "full-a-share",
            "validation_pipeline_state": "blocked_data_coverage",
        },
    )
    repo.update_historical_backfill_job(
        job.job_id,
        status="succeeded_with_errors",
        processed_symbols=1,
        succeeded_symbols=0,
        failed_symbols=1,
    )
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    monkeypatch.setattr(routes, "_history_task_executor", FakeExecutor())
    routes._submitted_historical_jobs.clear()
    client = TestClient(create_app())

    response = client.post(f"/api/historical-data/backfill/{job.job_id}/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["data_health"]["backfill_resume_count"] == "1"
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
