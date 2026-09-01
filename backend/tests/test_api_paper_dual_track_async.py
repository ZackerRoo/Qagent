from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from qagent.api import routes
from qagent.app import create_app
from qagent.db import initialize_database
from qagent.storage.tables import PaperDualTrackJobRow


class _PendingFuture:
    def add_done_callback(self, _callback):
        return None


class _RecordingExecutor:
    def __init__(self):
        self.submissions = []

    def submit(self, function, *args, **kwargs):
        self.submissions.append((function, args, kwargs))
        return _PendingFuture()


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{tmp_path / name}"


def test_dual_track_get_is_cache_only_and_post_is_single_flight(
    tmp_path,
    monkeypatch,
):
    database_url = _database_url(tmp_path, "dual-track-api.db")
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    executor = _RecordingExecutor()
    monkeypatch.setattr(routes, "_paper_dual_track_task_executor", executor)
    routes._submitted_paper_dual_track_jobs.clear()
    monkeypatch.setattr(
        routes,
        "_build_paper_dual_track_report",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("GET started computation")),
    )
    client = TestClient(create_app())

    empty = client.get(
        "/api/paper-trades/dual-track"
        "?provider=free&reporting_scope=current_model_cohort&days=180&top_n=5"
    )
    first = client.post(
        "/api/paper-trades/dual-track/jobs"
        "?provider=free&reporting_scope=current_model_cohort&days=180&top_n=5"
    )
    repeated = client.post(
        "/api/paper-trades/dual-track/jobs"
        "?provider=free&reporting_scope=current_model_cohort&days=180&top_n=5"
    )

    assert empty.status_code == 200
    assert empty.json()["status"] == "missing"
    assert empty.json()["report"] is None
    assert first.status_code == 200
    assert first.json()["status"] == "queued"
    assert repeated.json()["job"]["job_id"] == first.json()["job"]["job_id"]
    assert len(executor.submissions) == 1
    routes._submitted_paper_dual_track_jobs.clear()


def test_dual_track_repository_identity_and_concurrent_idempotency(
    tmp_path,
    monkeypatch,
):
    database_url = _database_url(tmp_path, "dual-track-concurrency.db")
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    engine = initialize_database(database_url)
    assert {
        "paper_dual_track_jobs",
        "paper_dual_track_snapshots",
    }.issubset(set(inspect(engine).get_table_names()))
    repo = routes._repo()
    identity = "paper-dual-track-v1:free:all_history:90:10"

    def create(index: int):
        return repo.create_or_get_paper_dual_track_job(
            job_id=f"dual-track-{index}",
            cache_identity=identity,
            provider="free",
            reporting_scope="all_history",
            days=90,
            top_n=10,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))

    assert sum(created for _, created in results) == 1
    assert len({job.job_id for job, _ in results}) == 1
    job = results[0][0]
    assert job.cache_identity == identity
    assert (job.provider, job.reporting_scope, job.days, job.top_n) == (
        "free",
        "all_history",
        90,
        10,
    )


def test_dual_track_failed_refresh_preserves_latest_successful_snapshot(
    tmp_path,
    monkeypatch,
):
    database_url = _database_url(tmp_path, "dual-track-failure.db")
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    repo = routes._repo()
    identity = "paper-dual-track-v1:free:current_model_cohort:180:5"
    first, created = repo.create_or_get_paper_dual_track_job(
        job_id="dual-track-success",
        cache_identity=identity,
        provider="free",
        reporting_scope="current_model_cohort",
        days=180,
        top_n=5,
    )
    assert created is True
    repo.mark_paper_dual_track_job_running(first.job_id)
    old_report = {"as_of": "2026-08-31", "summary": {"recommendations": 12}}
    repo.complete_paper_dual_track_job(
        first.job_id,
        payload=old_report,
        generated_at=datetime.now(timezone.utc),
    )
    second, created = repo.create_or_get_paper_dual_track_job(
        job_id="dual-track-failure",
        cache_identity=identity,
        provider="free",
        reporting_scope="current_model_cohort",
        days=180,
        top_n=5,
    )
    assert created is True
    monkeypatch.setattr(routes, "_repo", lambda: repo)
    monkeypatch.setattr(
        routes,
        "_build_paper_dual_track_report",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )

    routes._run_submitted_paper_dual_track_job(second.job_id)

    failed = repo.get_paper_dual_track_job(second.job_id)
    snapshot = repo.get_paper_dual_track_snapshot(identity)
    envelope = routes._paper_dual_track_cache_payload(repo, cache_identity=identity)
    assert failed is not None and failed.status == "failed"
    assert "synthetic failure" in (failed.error or "")
    assert snapshot is not None and snapshot.source_job_id == first.job_id
    assert snapshot.payload == old_report
    assert envelope["freshness"] == "stale"
    assert envelope["report"] == old_report


def test_dual_track_cache_identity_separates_scope_days_and_top_n(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        _database_url(tmp_path, "dual-track-identity.db"),
    )
    client = TestClient(create_app())
    cohort = client.get(
        "/api/paper-trades/dual-track"
        "?provider=free&reporting_scope=current_model_cohort&days=180&top_n=5"
    ).json()
    history = client.get(
        "/api/paper-trades/dual-track"
        "?provider=free&reporting_scope=all_history&days=180&top_n=5"
    ).json()
    shorter = client.get(
        "/api/paper-trades/dual-track"
        "?provider=free&reporting_scope=current_model_cohort&days=90&top_n=10"
    ).json()

    assert len(
        {
            cohort["cache_identity"],
            history["cache_identity"],
            shorter["cache_identity"],
        }
    ) == 3


def test_dual_track_expired_job_is_timed_out_before_new_refresh(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        _database_url(tmp_path, "dual-track-timeout.db"),
    )
    repo = routes._repo()
    identity = "paper-dual-track-v1:free:current_model_cohort:180:5"
    expired, _ = repo.create_or_get_paper_dual_track_job(
        job_id="dual-track-expired",
        cache_identity=identity,
        provider="free",
        reporting_scope="current_model_cohort",
        days=180,
        top_n=5,
    )
    old_time = datetime.now(timezone.utc) - timedelta(minutes=11)
    with repo.session_factory() as session:
        row = session.get(PaperDualTrackJobRow, expired.job_id)
        assert row is not None
        row.created_at = old_time
        row.updated_at = old_time
        session.commit()
    executor = _RecordingExecutor()
    monkeypatch.setattr(routes, "_paper_dual_track_task_executor", executor)
    routes._submitted_paper_dual_track_jobs.clear()
    client = TestClient(create_app())

    timed_out = client.get(
        "/api/paper-trades/dual-track"
        "?provider=free&reporting_scope=current_model_cohort&days=180&top_n=5"
    )
    refreshed = client.post(
        "/api/paper-trades/dual-track/jobs"
        "?provider=free&reporting_scope=current_model_cohort&days=180&top_n=5"
    )

    assert timed_out.json()["status"] == "timed_out"
    assert refreshed.json()["status"] == "queued"
    assert refreshed.json()["job"]["job_id"] != expired.job_id
    assert repo.get_paper_dual_track_job(expired.job_id).status == "timed_out"
    assert len(executor.submissions) == 1
    routes._submitted_paper_dual_track_jobs.clear()
