from datetime import datetime, timedelta, timezone

from qagent.db import Base, create_db_engine, create_session_factory
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import ScanResultCacheRow


def _repo(tmp_path) -> QagentRepository:
    database_url = f"sqlite:///{tmp_path / 'storage-governance.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    return QagentRepository(create_session_factory(database_url))


def _checkpoint(
    repo: QagentRepository,
    *,
    job_id: str,
    batch_index: int,
    created_at: datetime,
) -> str:
    record = repo.save_scan_result_cache(
        cache_key=f"full_market_batch_checkpoint:{job_id}:{batch_index}",
        provider="free",
        mode="full_market_batch_checkpoint",
        symbols=["CN:600000"],
        payload={"job_id": job_id, "batch_index": batch_index, "payload": "x" * 100},
    )
    with repo.session_factory.begin() as session:
        row = session.get(ScanResultCacheRow, record.cache_id)
        assert row is not None
        row.created_at = created_at
    return record.cache_id


def test_checkpoint_maintenance_protects_active_recent_and_unknown_rows(tmp_path):
    repo = _repo(tmp_path)
    now = datetime(2026, 8, 9, 8, tzinfo=timezone.utc)
    old = now - timedelta(days=20)
    recent = now - timedelta(days=2)

    succeeded = repo.create_full_market_scan_job("free", ["CN:600000"], 1, True, False)
    repo.update_full_market_scan_job(succeeded.job_id, status="succeeded")
    failed = repo.create_full_market_scan_job("free", ["CN:600003"], 1, True, False)
    repo.update_full_market_scan_job(failed.job_id, status="failed")
    active = repo.create_full_market_scan_job("free", ["CN:600001"], 1, True, False)
    eligible_id = _checkpoint(
        repo,
        job_id=succeeded.job_id,
        batch_index=0,
        created_at=old,
    )
    recent_id = _checkpoint(
        repo,
        job_id=failed.job_id,
        batch_index=0,
        created_at=recent,
    )
    active_id = _checkpoint(
        repo,
        job_id=active.job_id,
        batch_index=0,
        created_at=old,
    )
    unknown = repo.save_scan_result_cache(
        cache_key="full_market_batch_checkpoint:missing-job:0",
        provider="free",
        mode="full_market_batch_checkpoint",
        symbols=["CN:600002"],
        payload={"payload": "unknown"},
    )
    with repo.session_factory.begin() as session:
        row = session.get(ScanResultCacheRow, unknown.cache_id)
        assert row is not None
        row.created_at = old

    preview = repo.maintain_full_market_scan_checkpoints(
        retention_days=14,
        dry_run=True,
        now=now,
    )

    assert preview.total_checkpoint_rows == 4
    assert preview.eligible_rows == 1
    assert preview.eligible_succeeded_rows == 1
    assert preview.eligible_expired_terminal_rows == 0
    assert preview.deleted_rows == 0
    assert preview.protected_active_rows == 1
    assert preview.protected_recent_rows == 1
    assert preview.protected_unrecognized_rows == 1
    assert preview.active_job_ids == [active.job_id]

    applied = repo.maintain_full_market_scan_checkpoints(
        retention_days=14,
        dry_run=False,
        now=now,
    )

    assert applied.deleted_rows == 1
    assert applied.deleted_payload_bytes > 0
    with repo.session_factory() as session:
        assert session.get(ScanResultCacheRow, eligible_id) is None
        assert session.get(ScanResultCacheRow, recent_id) is not None
        assert session.get(ScanResultCacheRow, active_id) is not None
        assert session.get(ScanResultCacheRow, unknown.cache_id) is not None


def test_succeeded_job_checkpoint_cleanup_requires_succeeded_status(tmp_path):
    repo = _repo(tmp_path)
    job = repo.create_full_market_scan_job("free", ["CN:600000"], 1, True, False)
    checkpoint_id = _checkpoint(
        repo,
        job_id=job.job_id,
        batch_index=0,
        created_at=datetime.now(timezone.utc),
    )

    assert repo.delete_succeeded_full_market_scan_checkpoints(job.job_id) == 0
    repo.update_full_market_scan_job(job.job_id, status="succeeded")
    assert repo.delete_succeeded_full_market_scan_checkpoints(job.job_id) == 1
    with repo.session_factory() as session:
        assert session.get(ScanResultCacheRow, checkpoint_id) is None
