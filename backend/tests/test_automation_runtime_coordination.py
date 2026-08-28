from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, inspect, text

from qagent.db import create_session_factory, initialize_database
from qagent import db as db_module
from qagent.jobs.automation_retry import classify_automation_error
from qagent.jobs.automation_scheduler import (
    AutoProcessingCycleResult,
    AutoProcessingSettings,
    AutomationScheduler,
)
from qagent.providers.failure_state import (
    FailureCategory,
    FailureKey,
    ProviderFailureStateRegistry,
    provider_failure_state_data_health,
)
from qagent.storage.automation_runtime import (
    AUTOMATION_LEASE_KEY,
    AutomationCycleBusyError,
    AutomationCycleConflictError,
    AutomationLeaseLostError,
    AutomationRuntimeRepository,
    RuntimeLeaseGuard,
    manual_cycle_slot,
    scheduled_cycle_slot,
)
from qagent.storage.paper import PaperTradeEventMetadata, PaperTradingRepository
from qagent.storage.repository import (
    BriefRunRecord,
    DeliveryIdempotencyConflictError,
    QagentRepository,
)
from qagent.storage.tables import (
    AutomationCircuitBreakerRow,
    AutomationCycleRow,
    AutomationCycleStageRow,
)


def _runtime(database_url: str) -> AutomationRuntimeRepository:
    initialize_database(database_url)
    return AutomationRuntimeRepository(create_session_factory(database_url))


@pytest.mark.parametrize(
    ("health", "expected_kind", "expected_retryable"),
    [
        (
            {
                "provider_error_kind": "unsupported",
                "provider_error_code": "NO_ENDPOINT",
                "provider_error_retryable": "false",
            },
            "unsupported",
            False,
        ),
        (
            {
                "free_provider_error_kind": "timeout",
                "free_provider_error_retryable": "true",
            },
            "timeout",
            True,
        ),
        (
            {
                "provider_error_kind": "auth",
                "provider_error_retryable": "true",
            },
            "permanent_configuration/auth",
            False,
        ),
        (
            {
                "provider_error_kind": "timeout",
                "provider_error_retryable": "false",
            },
            "timeout",
            False,
        ),
    ],
)
def test_automation_error_classification_prefers_structured_provider_health(
    health,
    expected_kind,
    expected_retryable,
):
    classified = classify_automation_error(
        "paper_update",
        "free",
        "contract violation",
        health,
    )

    assert classified.error_kind == expected_kind
    assert classified.retryable is expected_retryable
    assert len(classified.fingerprint) == 64


def test_automation_error_classification_falls_back_when_health_is_absent():
    retryable = classify_automation_error(
        "scan",
        "free",
        "provider timeout request_id=volatile",
    )
    permanent = classify_automation_error(
        "paper_update",
        "free",
        "telemetry is missing or invalid",
    )
    auth = classify_automation_error(
        "paper_update",
        "free",
        "HTTP 401 unauthorized",
    )

    assert retryable.retryable is True
    assert retryable.error_kind == "provider_or_coverage"
    assert permanent.retryable is False
    assert permanent.error_kind == "permanent_contract"
    assert auth.retryable is False
    assert auth.error_kind == "permanent_configuration/auth"


def test_provider_auth_health_is_permanent_across_provider_and_automation_layers():
    registry = ProviderFailureStateRegistry(jitter_ratio=0)
    key = FailureKey(provider="free", origin="example", capability="daily")
    registry.failure(key, FailureCategory.AUTH, error_code=401)
    provider = type("ProviderWithRegistry", (), {"failure_registry": registry})()

    health = provider_failure_state_data_health(provider)
    classified = classify_automation_error(
        "paper_update",
        "free",
        "provider authentication failed",
        health,
    )

    assert health["provider_error_kind"] == "auth"
    assert health["provider_error_retryable"] == "false"
    assert classified.retryable is False
    assert classified.error_kind == "permanent_configuration/auth"


def test_additive_migration_records_duplicate_audit_without_mutation(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    initialize_database(database_url)
    engine = create_engine(database_url)
    with engine.connect() as connection:
        payload = connection.execute(
            text(
                "SELECT payload_json FROM automation_migration_audits "
                "WHERE audit_key = 'automation-runtime-v1'"
            )
        ).scalar_one()
    audit = json.loads(payload)
    assert audit["active_paper_duplicate_groups"] == 0
    assert audit["action"] == "audit_only_no_historical_deletion"


def test_additive_migration_has_bounded_retry_schema(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'retry-schema.db'}"
    initialize_database(database_url)
    engine = create_engine(database_url)
    inspector = inspect(engine)

    cycle_columns = {item["name"] for item in inspector.get_columns("automation_cycles")}
    stage_columns = {
        item["name"] for item in inspector.get_columns("automation_cycle_stages")
    }

    assert {
        "attempt_count",
        "retry_budget",
        "next_retry_at",
        "last_error_fingerprint",
        "terminal_reason",
    } <= cycle_columns
    assert {
        "attempt_count",
        "retry_scope",
        "next_retry_at",
        "last_error_retryable",
    } <= stage_columns
    assert {"automation_circuit_breakers", "automation_incidents"} <= set(
        inspector.get_table_names()
    )
    breaker_columns = {
        item["name"] for item in inspector.get_columns("automation_circuit_breakers")
    }
    assert "probe_expires_at" in breaker_columns


def test_legacy_partial_cycle_migrates_and_first_new_failure_is_attempt_one(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-partial-retry.db'}"
    initialize_database(database_url)
    engine = create_engine(database_url)
    now = datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE automation_cycle_stages"))
        connection.execute(text("DROP TABLE automation_cycles"))
        connection.execute(text("DROP TABLE automation_circuit_breakers"))
        connection.execute(
            text(
                "CREATE TABLE automation_cycles ("
                "cycle_slot VARCHAR(160) PRIMARY KEY, cycle_kind VARCHAR(16), "
                "settings_digest VARCHAR(64), idempotency_key VARCHAR(160), "
                "due_at DATETIME, status VARCHAR(32), owner_token VARCHAR(128), "
                "fencing_token INTEGER, result_json TEXT, error_json TEXT, "
                "started_at DATETIME, finalized_at DATETIME, created_at DATETIME, "
                "updated_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE automation_cycle_stages ("
                "cycle_slot VARCHAR(160), stage_key VARCHAR(64), status VARCHAR(32), "
                "owner_token VARCHAR(128), fencing_token INTEGER, output_digest VARCHAR(64), "
                "output_json TEXT, error_text TEXT, started_at DATETIME, "
                "completed_at DATETIME, updated_at DATETIME, "
                "PRIMARY KEY(cycle_slot, stage_key))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE automation_circuit_breakers ("
                "scope_key VARCHAR(160) PRIMARY KEY, state VARCHAR(32), "
                "failure_count INTEGER, open_count INTEGER, next_probe_at DATETIME, "
                "last_error_fingerprint VARCHAR(64), last_error_text TEXT, "
                "half_open_cycle_slot VARCHAR(160), revision INTEGER, "
                "created_at DATETIME, updated_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO automation_circuit_breakers VALUES "
                "('scan:free','open',1,1,:now,NULL,'legacy',NULL,1,:now,:now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO automation_cycles VALUES "
                "('manual:legacy','manual',:digest,:key,NULL,'partial_retry_same_slot',"
                "'old-owner',1,'{}','[\"old failure\"]',:now,:now,:now,:now)"
            ),
            {"digest": "7" * 64, "key": "automation-manual:legacy", "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO automation_cycle_stages VALUES "
                "('manual:legacy','scan','error','old-owner',1,NULL,'{}','old failure',"
                ":now,:now,:now)"
            ),
            {"now": now},
        )

    db_module._apply_additive_migrations(engine)
    with engine.connect() as connection:
        legacy_breaker = connection.execute(
            text(
                "SELECT state,probe_expires_at FROM automation_circuit_breakers "
                "WHERE scope_key='scan:free'"
            )
        ).one()
    assert legacy_breaker.state == "open"
    assert legacy_breaker.probe_expires_at is None
    runtime = AutomationRuntimeRepository(create_session_factory(database_url))
    started = runtime.begin_cycle(
        cycle_slot="manual:legacy",
        cycle_kind="manual",
        settings_digest="7" * 64,
        due_at=None,
        idempotency_key="automation-manual:legacy",
        owner_token="new-owner",
        now=now + timedelta(minutes=5),
        process_fence_held=True,
    )
    assert started.attempt_count == 0
    assert started.grant is not None
    assert runtime.begin_stage(started.grant, "scan", now=now + timedelta(minutes=5)) is None
    runtime.fail_stage(
        started.grant,
        "scan",
        "provider timeout",
        error_fingerprint="7" * 64,
        error_kind="timeout",
        retryable=True,
        now=now + timedelta(minutes=5),
    )
    assert runtime.finalize_cycle(
        started.grant,
        result={},
        errors=["scan: provider timeout"],
        issues=[],
        required_stages={"scan"},
        now=now + timedelta(minutes=5),
    ) == "partial_retry_same_slot"
    assert runtime.cycle_retry_state("manual:legacy").attempt_count == 1


def test_legacy_outbox_migration_preserves_rows_and_adds_idempotency_columns(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-outbox.db'}"
    initialize_database(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE delivery_outbox"))
        connection.execute(
            text(
                "CREATE TABLE delivery_outbox ("
                "delivery_id VARCHAR(64) PRIMARY KEY, brief_id VARCHAR(64), "
                "channel VARCHAR(32), recipient VARCHAR(255), subject TEXT, "
                "markdown TEXT, payload_json TEXT, status VARCHAR(32), "
                "created_at DATETIME, updated_at DATETIME, sent_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO delivery_outbox "
                "(delivery_id, brief_id, channel, subject, markdown, payload_json, status) "
                "VALUES ('legacy-delivery', NULL, 'markdown', 'legacy', 'body', '{}', 'queued')"
            )
        )
    db_module._apply_additive_migrations(engine)
    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(delivery_outbox)"))
        }
        row = connection.execute(
            text(
                "SELECT delivery_id, idempotency_key, payload_digest "
                "FROM delivery_outbox WHERE delivery_id = 'legacy-delivery'"
            )
        ).one()
        audit = json.loads(
            connection.execute(
                text(
                    "SELECT payload_json FROM automation_migration_audits "
                    "WHERE audit_key = 'automation-runtime-v1'"
                )
            ).scalar_one()
        )
    assert {"idempotency_key", "payload_digest"} <= columns
    assert row.delivery_id == "legacy-delivery"
    assert row.idempotency_key is None
    assert row.payload_digest is None
    assert audit["legacy_outbox_without_idempotency"] == 1


def test_legacy_unlinked_outbox_rows_are_repointed_to_stable_sentinel(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-unlinked-outbox.db'}"
    initialize_database(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE delivery_outbox"))
        connection.execute(
            text(
                "CREATE TABLE delivery_outbox ("
                "delivery_id VARCHAR(64) PRIMARY KEY, brief_id VARCHAR(64), "
                "channel VARCHAR(32), recipient VARCHAR(255), subject TEXT, markdown TEXT, "
                "payload_json TEXT, status VARCHAR(32), created_at DATETIME, "
                "updated_at DATETIME, sent_at DATETIME, "
                "FOREIGN KEY(brief_id) REFERENCES brief_runs(brief_id))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO delivery_outbox VALUES "
                "('legacy-empty','', 'markdown','a@example.com','empty','body-a',:payload_a,"
                "'sent','2026-08-01 01:00:00','2026-08-01 02:00:00','2026-08-01 03:00:00'),"
                "('legacy-null',NULL,'webhook',NULL,'null','body-b',:payload_b,"
                "'failed','2026-08-02 01:00:00','2026-08-02 02:00:00',NULL)"
            ),
            {"payload_a": '{"a":1}', "payload_b": '{"b":2}'},
        )
        before = connection.execute(
            text(
                "SELECT delivery_id,channel,recipient,subject,markdown,payload_json,status,"
                "created_at,updated_at,sent_at FROM delivery_outbox ORDER BY delivery_id"
            )
        ).all()

    db_module._apply_additive_migrations(engine)
    with engine.connect() as connection:
        after = connection.execute(
            text(
                "SELECT delivery_id,channel,recipient,subject,markdown,payload_json,status,"
                "created_at,updated_at,sent_at FROM delivery_outbox ORDER BY delivery_id"
            )
        ).all()
        brief_ids = connection.execute(
            text("SELECT DISTINCT brief_id FROM delivery_outbox")
        ).scalars().all()
        sentinel_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM brief_runs "
                "WHERE brief_id='brief-legacy-unlinked-delivery'"
            )
        ).scalar_one()
        audit = json.loads(
            connection.execute(
                text(
                    "SELECT payload_json FROM automation_migration_audits "
                    "WHERE audit_key='automation-runtime-v1'"
                )
            ).scalar_one()
        )
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        foreign_key_issues = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    assert after == before
    assert brief_ids == ["brief-legacy-unlinked-delivery"]
    assert sentinel_count == 1
    assert audit["legacy_unlinked_outbox_repaired_this_run"] == 2
    assert audit["legacy_unlinked_outbox_repaired_total"] == 2
    assert audit["legacy_unlinked_outbox_remaining"] == 0
    assert foreign_key_issues == []

    db_module._apply_additive_migrations(engine)
    with engine.connect() as connection:
        repeated_audit = json.loads(
            connection.execute(
                text(
                    "SELECT payload_json FROM automation_migration_audits "
                    "WHERE audit_key='automation-runtime-v1'"
                )
            ).scalar_one()
        )
        assert connection.execute(text("SELECT COUNT(*) FROM delivery_outbox")).scalar_one() == 2
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM brief_runs "
                "WHERE brief_id='brief-legacy-unlinked-delivery'"
            )
        ).scalar_one() == 1
    assert repeated_audit["legacy_unlinked_outbox_repaired_this_run"] == 0
    assert repeated_audit["legacy_unlinked_outbox_repaired_total"] == 2


def test_two_engines_have_one_lease_winner(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'lease-race.db'}"
    first = _runtime(database_url)
    second = AutomationRuntimeRepository(create_session_factory(database_url))
    barrier = Barrier(2)

    def acquire(repo: AutomationRuntimeRepository, owner: str):
        barrier.wait()
        return repo.acquire(
            lease_key=AUTOMATION_LEASE_KEY,
            owner_token=owner,
            cycle_slot="scheduled:slot",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        grants = list(executor.map(acquire, (first, second), ("owner-a", "owner-b")))
    assert sum(grant is not None for grant in grants) == 1


def test_process_fence_blocks_live_holder_and_recovers_after_process_death(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'process-fence.db'}"
    backend_dir = Path(__file__).resolve().parents[1]
    child = r"""
import sys
import time
from datetime import timedelta

from qagent.db import create_session_factory, initialize_database
from qagent.storage.automation_runtime import AutomationRuntimeRepository

database_url, mode = sys.argv[1:3]
initialize_database(database_url)
runtime = AutomationRuntimeRepository(create_session_factory(database_url))
fence = runtime.acquire_process_fence()
if fence is None:
    print("busy", flush=True)
    raise SystemExit(0)
try:
    started = runtime.begin_cycle(
        cycle_slot="manual:process-fence",
        cycle_kind="manual",
        settings_digest="f" * 64,
        due_at=None,
        idempotency_key="automation-manual:process-fence",
        owner_token=mode,
        ttl=timedelta(milliseconds=100),
        process_fence_held=True,
    )
    assert started.grant is not None
    print(f"acquired:{started.grant.fencing_token}", flush=True)
    if mode == "holder":
        time.sleep(30)
    else:
        runtime.release(started.grant)
finally:
    fence.release()
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", child, database_url, "holder"],
        cwd=backend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "acquired:1"
        # Let the database lease expire while the process remains alive. The
        # OS-owned fence must still prevent a second stage owner.
        time.sleep(0.2)
        contender = subprocess.run(
            [sys.executable, "-c", child, database_url, "contender"],
            cwd=backend_dir,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert contender.stdout.strip() == "busy"

        holder.kill()
        holder.wait(timeout=10)
        takeover = subprocess.run(
            [sys.executable, "-c", child, database_url, "takeover"],
            cwd=backend_dir,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert takeover.stdout.strip() == "acquired:2"
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)


def test_expiry_fences_old_owner_and_finalize_requires_current_lease(tmp_path):
    runtime = _runtime(f"sqlite:///{tmp_path / 'fence.db'}")
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    old = runtime.acquire(
        lease_key=AUTOMATION_LEASE_KEY,
        owner_token="old",
        cycle_slot="slot-old",
        now=now,
        ttl=timedelta(seconds=1),
    )
    assert old is not None
    new = runtime.acquire(
        lease_key=AUTOMATION_LEASE_KEY,
        owner_token="new",
        cycle_slot="slot-new",
        now=now + timedelta(seconds=2),
    )
    assert new is not None
    assert new.fencing_token == old.fencing_token + 1
    with pytest.raises(AutomationLeaseLostError):
        runtime.heartbeat(old, now=now + timedelta(seconds=2))


def test_heartbeat_prevents_takeover_and_old_owner_cannot_complete_or_finalize(tmp_path):
    runtime = _runtime(f"sqlite:///{tmp_path / 'heartbeat.db'}")
    digest = "d" * 64
    slot, key = manual_cycle_slot("heartbeat-owner")
    started = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest=digest,
        due_at=None,
        idempotency_key=key,
        owner_token="old-owner",
        ttl=timedelta(milliseconds=150),
    )
    assert started.grant is not None
    runtime.begin_stage(started.grant, "scan")
    guard = RuntimeLeaseGuard(
        runtime,
        started.grant,
        ttl=timedelta(milliseconds=150),
        heartbeat_interval=timedelta(milliseconds=30),
    ).start()
    time.sleep(0.22)
    assert runtime.acquire(
        lease_key=AUTOMATION_LEASE_KEY,
        owner_token="blocked-owner",
        cycle_slot="blocked-slot",
        ttl=timedelta(milliseconds=150),
    ) is None
    takeover = runtime.acquire(
        lease_key=AUTOMATION_LEASE_KEY,
        owner_token="takeover-owner",
        cycle_slot="takeover-slot",
        now=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    assert takeover is not None
    deadline = time.monotonic() + 0.5
    while not guard.lost and time.monotonic() < deadline:
        time.sleep(0.01)
    assert guard.lost is True
    with pytest.raises(AutomationLeaseLostError):
        guard.assert_current()
    guard.stop()
    with pytest.raises(AutomationLeaseLostError):
        runtime.complete_stage(started.grant, "scan", {"artifact": "stale"})
    with pytest.raises(AutomationLeaseLostError):
        runtime.finalize_cycle(
            started.grant,
            result={"status": "stale"},
            errors=[],
            issues=[],
            required_stages={"scan"},
        )


def test_same_slot_replays_completed_result_and_manual_conflicts_with_scheduled(tmp_path):
    runtime = _runtime(f"sqlite:///{tmp_path / 'cycle.db'}")
    due_at = datetime(2026, 8, 28, 1, tzinfo=timezone.utc)
    digest = "a" * 64
    slot = scheduled_cycle_slot(due_at, digest)
    started = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="scheduled",
        settings_digest=digest,
        due_at=due_at,
        idempotency_key=None,
        owner_token="winner",
    )
    assert started.grant is not None
    runtime.begin_stage(started.grant, "scan")
    runtime.complete_stage(started.grant, "scan", {"scan_status": "completed"})
    result = {
        "provider": "free",
        "started_at": due_at.isoformat(),
        "finished_at": due_at.isoformat(),
        "scan_status": "completed",
    }
    assert runtime.finalize_cycle(
        started.grant,
        result=result,
        errors=[],
        issues=[],
        required_stages={"scan"},
    ) == "succeeded"
    replay = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="scheduled",
        settings_digest=digest,
        due_at=due_at,
        idempotency_key=None,
        owner_token="replayer",
    )
    assert replay.replay_result == result

    active = runtime.acquire(
        lease_key=AUTOMATION_LEASE_KEY,
        owner_token="scheduled-owner",
        cycle_slot="other-scheduled-slot",
    )
    assert active is not None
    manual, key = manual_cycle_slot("request-1")
    with pytest.raises(AutomationCycleBusyError):
        runtime.begin_cycle(
            cycle_slot=manual,
            cycle_kind="manual",
            settings_digest=digest,
            due_at=None,
            idempotency_key=key,
            owner_token="manual-owner",
        )


def test_deferred_stage_is_terminal_and_replays_issue_status(tmp_path):
    runtime = _runtime(f"sqlite:///{tmp_path / 'deferred-cycle.db'}")
    slot, idempotency_key = manual_cycle_slot("deferred-cycle")
    started = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest="e" * 64,
        due_at=None,
        idempotency_key=idempotency_key,
        owner_token="first-owner",
    )
    assert started.grant is not None
    runtime.begin_stage(started.grant, "factor_shadow")
    runtime.complete_stage(
        started.grant,
        "factor_shadow",
        {"status": "waiting_for_maturity"},
        status="deferred",
    )
    result = {"issues": ["factor_shadow: waiting_for_maturity"]}
    assert runtime.finalize_cycle(
        started.grant,
        result=result,
        errors=[],
        issues=result["issues"],
        required_stages={"factor_shadow"},
    ) == "completed_with_deferred_or_issues"

    replay = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest="e" * 64,
        due_at=None,
        idempotency_key=idempotency_key,
        owner_token="replayer",
    )
    assert replay.grant is None
    assert replay.replay_status == "completed_with_deferred_or_issues"
    assert replay.replay_result == result


def test_partial_cycle_reuses_successful_stage_and_recovers_failed_stage(tmp_path):
    runtime = _runtime(f"sqlite:///{tmp_path / 'stage-recovery.db'}")
    slot, idempotency_key = manual_cycle_slot("recover-1")
    started_at = datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)
    first = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest="b" * 64,
        due_at=None,
        idempotency_key=idempotency_key,
        owner_token="first-owner",
        now=started_at,
    )
    assert first.grant is not None
    runtime.begin_stage(first.grant, "scan", now=started_at)
    runtime.complete_stage(
        first.grant,
        "scan",
        {"artifact_id": "scan-1"},
        now=started_at,
    )
    runtime.begin_stage(first.grant, "paper_seed", now=started_at)
    runtime.fail_stage(
        first.grant,
        "paper_seed",
        "injected failure",
        now=started_at,
    )
    assert runtime.finalize_cycle(
        first.grant,
        result={"status": "partial"},
        errors=["paper_seed: injected failure"],
        issues=[],
        required_stages={"scan", "paper_seed"},
        now=started_at,
    ) == "partial_retry_same_slot"

    not_due = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest="b" * 64,
        due_at=None,
        idempotency_key=idempotency_key,
        owner_token="early-owner",
        now=started_at + timedelta(minutes=1),
    )
    assert not_due.grant is None
    assert not_due.retry_not_due_at == started_at + timedelta(minutes=5)

    retry = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest="b" * 64,
        due_at=None,
        idempotency_key=idempotency_key,
        owner_token="retry-owner",
        now=started_at + timedelta(minutes=5),
    )
    assert retry.grant is not None
    assert retry.grant.fencing_token > first.grant.fencing_token
    assert runtime.begin_stage(
        retry.grant,
        "scan",
        now=started_at + timedelta(minutes=5),
    ) == {"artifact_id": "scan-1"}
    assert runtime.begin_stage(
        retry.grant,
        "paper_seed",
        now=started_at + timedelta(minutes=5),
    ) is None
    runtime.complete_stage(
        retry.grant,
        "paper_seed",
        {"created": 1},
        now=started_at + timedelta(minutes=5),
    )
    assert runtime.finalize_cycle(
        retry.grant,
        result={"status": "succeeded"},
        errors=[],
        issues=[],
        required_stages={"scan", "paper_seed"},
        now=started_at + timedelta(minutes=5),
    ) == "succeeded"

    with pytest.raises(AutomationCycleConflictError, match="different facts"):
        runtime.begin_cycle(
            cycle_slot=slot,
            cycle_kind="manual",
            settings_digest="c" * 64,
            due_at=None,
            idempotency_key=idempotency_key,
            owner_token="conflicting-owner",
            now=started_at + timedelta(minutes=5),
        )


def test_partial_paper_stage_retry_does_not_duplicate_existing_event(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'partial-paper-event.db'}"
    runtime = _runtime(database_url)
    paper_repo = PaperTradingRepository(create_session_factory(database_url))
    trade = paper_repo.create_trade(
        source_snapshot_id="missing-legacy-snapshot",
        provider="free",
        instrument_id="CN:000001",
        strategy_id="test",
        signal_date=date(2026, 8, 28),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
    )
    now = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
    slot, key = manual_cycle_slot("paper-event-retry")
    first = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest="9" * 64,
        due_at=None,
        idempotency_key=key,
        owner_token="first-owner",
        now=now,
    )
    assert first.grant is not None
    runtime.begin_stage(first.grant, "paper_update", now=now)
    paper_repo.update_trade(
        trade.trade_id,
        status="open",
        entry_date=date(2026, 8, 29),
        entry_price=Decimal("10"),
        event_metadata=PaperTradeEventMetadata(
            idempotency_key="automation-paper-open-stable",
        ),
    )
    runtime.fail_stage(
        first.grant,
        "paper_update",
        "provider timeout after first persisted update",
        error_fingerprint="9" * 64,
        error_kind="timeout",
        retryable=True,
        now=now,
    )
    assert runtime.finalize_cycle(
        first.grant,
        result={},
        errors=["paper_update: provider timeout after first persisted update"],
        issues=[],
        required_stages={"paper_update"},
        now=now,
    ) == "partial_retry_same_slot"
    assert len(paper_repo.list_trade_events(trade.trade_id)) == 2

    retry_at = now + timedelta(minutes=5)
    retry = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest="9" * 64,
        due_at=None,
        idempotency_key=key,
        owner_token="retry-owner",
        now=retry_at,
    )
    assert retry.grant is not None
    assert runtime.begin_stage(retry.grant, "paper_update", now=retry_at) is None
    paper_repo.update_trade(
        trade.trade_id,
        status="open",
        entry_date=date(2026, 8, 29),
        entry_price=Decimal("10"),
        event_metadata=PaperTradeEventMetadata(
            idempotency_key="automation-paper-open-stable",
        ),
    )
    assert len(paper_repo.list_trade_events(trade.trade_id)) == 2
    runtime.complete_stage(
        retry.grant,
        "paper_update",
        {"paper_total": 1},
        now=retry_at,
    )
    assert runtime.finalize_cycle(
        retry.grant,
        result={},
        errors=[],
        issues=[],
        required_stages={"paper_update"},
        now=retry_at,
    ) == "succeeded"


def test_retry_budget_backoff_fingerprint_changes_and_alert_are_persisted(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'bounded-retry.db'}"
    runtime = _runtime(database_url)
    due = datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)
    slot = scheduled_cycle_slot(due, "d" * 64)
    current = due
    expected_delays = [300, 600, 1200]

    for attempt in range(1, 5):
        start = runtime.begin_cycle(
            cycle_slot=slot,
            cycle_kind="scheduled",
            settings_digest="d" * 64,
            due_at=due,
            idempotency_key=None,
            owner_token=f"owner-{attempt}",
            now=current,
        )
        assert start.grant is not None
        runtime.begin_stage(
            start.grant,
            "paper_update",
            retry_scope="paper_update:free",
            now=current,
        )
        runtime.fail_stage(
            start.grant,
            "paper_update",
            f"provider timeout request_id={attempt}",
            retry_scope="paper_update:free",
            error_fingerprint=f"{attempt % 2}" * 64,
            error_kind="provider_or_coverage",
            retryable=True,
            now=current,
        )
        status = runtime.finalize_cycle(
            start.grant,
            result={"attempt": attempt},
            errors=[f"paper_update: provider timeout request_id={attempt}"],
            issues=[],
            required_stages={"paper_update"},
            now=current,
        )
        retry = runtime.cycle_retry_state(slot)
        assert retry.attempt_count == attempt
        if attempt < 4:
            assert status == "partial_retry_same_slot"
            assert retry.retry_backoff_seconds == expected_delays[attempt - 1]
            assert retry.next_retry_at == current + timedelta(
                seconds=expected_delays[attempt - 1]
            )
            if attempt == 1:
                runtime = AutomationRuntimeRepository(
                    create_session_factory(database_url)
                )
            early = runtime.begin_cycle(
                cycle_slot=slot,
                cycle_kind="scheduled",
                settings_digest="d" * 64,
                due_at=due,
                idempotency_key=None,
                owner_token=f"early-{attempt}",
                now=current + timedelta(seconds=1),
            )
            assert early.grant is None
            assert early.retry_not_due_at == retry.next_retry_at
            current = retry.next_retry_at
            assert current is not None
        else:
            assert status == "deferred_with_alert"
            assert retry.next_retry_at is None
            assert retry.terminal_reason == "retry_budget_exhausted"

    with create_engine(database_url).connect() as connection:
        cycle = connection.execute(
            text(
                "SELECT attempt_count, status, last_error_fingerprint "
                "FROM automation_cycles WHERE cycle_slot=:slot"
            ),
            {"slot": slot},
        ).one()
        breaker = connection.execute(
            text(
                "SELECT state,open_count,next_probe_at FROM automation_circuit_breakers "
                "WHERE scope_key='paper_update:free'"
            )
        ).one()
        assert connection.execute(text("SELECT COUNT(*) FROM automation_incidents")).scalar_one() == 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM brief_runs WHERE brief_id LIKE 'automation-brief-%'")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM delivery_outbox WHERE idempotency_key LIKE 'automation-retry-alert:%'")
        ).scalar_one() == 1
    assert cycle.attempt_count == 4
    assert cycle.status == "deferred_with_alert"
    assert cycle.last_error_fingerprint
    assert breaker.state == "open"
    assert breaker.open_count == 1

    replay = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="scheduled",
        settings_digest="d" * 64,
        due_at=due,
        idempotency_key=None,
        owner_token="replay",
        now=current,
    )
    assert replay.replay_status == "deferred_with_alert"
    with create_engine(database_url).connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM automation_incidents")).scalar_one() == 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM brief_runs WHERE brief_id LIKE 'automation-brief-%'")
        ).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM delivery_outbox")).scalar_one() == 1

    with runtime._write_session() as session:
        cycle_row = session.get(AutomationCycleRow, slot)
        stage_row = session.get(AutomationCycleStageRow, (slot, "paper_update"))
        breaker_row = session.get(AutomationCircuitBreakerRow, "paper_update:free")
        assert cycle_row is not None
        assert stage_row is not None
        runtime._record_retry_incident(
            session,
            cycle_row,
            stage_row,
            breaker=breaker_row,
            current=current,
        )
    with create_engine(database_url).begin() as connection:
        connection.execute(
            text(
                "UPDATE brief_runs SET headline='tampered facts' "
                "WHERE brief_id LIKE 'automation-brief-%'"
            )
        )
    with pytest.raises(AutomationCycleConflictError, match="different facts"):
        with runtime._write_session() as session:
            cycle_row = session.get(AutomationCycleRow, slot)
            stage_row = session.get(AutomationCycleStageRow, (slot, "paper_update"))
            breaker_row = session.get(AutomationCircuitBreakerRow, "paper_update:free")
            assert cycle_row is not None
            assert stage_row is not None
            runtime._record_retry_incident(
                session,
                cycle_row,
                stage_row,
                breaker=breaker_row,
                current=current,
            )


def test_running_cycle_with_stage_attempt_four_finalizes_without_attempt_five(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'raw-attempt-four.db'}"
    runtime = _runtime(database_url)
    due = datetime(2026, 8, 29, 2, 0, tzinfo=timezone.utc)
    slot = scheduled_cycle_slot(due, "8" * 64)
    started = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="scheduled",
        settings_digest="8" * 64,
        due_at=due,
        idempotency_key=None,
        owner_token="crashed-owner",
        now=due,
    )
    assert started.grant is not None
    runtime.begin_stage(
        started.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=due,
    )
    runtime.fail_stage(
        started.grant,
        "paper_update",
        "provider timeout before terminal finalize",
        retry_scope="paper_update:free",
        error_fingerprint="8" * 64,
        error_kind="timeout",
        retryable=True,
        now=due,
    )
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE automation_cycles SET attempt_count=4,status='running' "
                "WHERE cycle_slot=:slot"
            ),
            {"slot": slot},
        )
        connection.execute(
            text(
                "UPDATE automation_cycle_stages SET attempt_count=4,status='error' "
                "WHERE cycle_slot=:slot AND stage_key='paper_update'"
            ),
            {"slot": slot},
        )

    recovered_at = due + timedelta(minutes=1)
    restarted = AutomationRuntimeRepository(create_session_factory(database_url))
    recovered = restarted.begin_cycle(
        cycle_slot=slot,
        cycle_kind="scheduled",
        settings_digest="8" * 64,
        due_at=due,
        idempotency_key=None,
        owner_token="recovery-owner",
        now=recovered_at,
        process_fence_held=True,
    )
    assert recovered.grant is not None
    checkpoint = restarted.begin_stage(
        recovered.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=recovered_at,
    )
    assert checkpoint is not None
    assert checkpoint["stage_terminal_error"] == (
        "provider timeout before terminal finalize"
    )
    assert restarted.finalize_cycle(
        recovered.grant,
        result={},
        errors=["paper_update: provider timeout before terminal finalize"],
        issues=[],
        required_stages={"paper_update"},
        now=recovered_at,
    ) == "deferred_with_alert"
    assert restarted.cycle_retry_state(slot).attempt_count == 4
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM automation_incidents")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM brief_runs WHERE brief_id LIKE 'automation-brief-%'")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM delivery_outbox")
        ).scalar_one() == 1


def test_find_recoverable_scheduled_cycle_filters_scope_and_active_lease(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'recoverable-cycle.db'}"
    runtime = _runtime(database_url)
    engine = create_engine(database_url)
    now = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        for slot, kind, digest, status, due_offset in (
            ("scheduled:old", "scheduled", "d" * 64, "running", -60),
            ("scheduled:new", "scheduled", "d" * 64, "partial_retry_same_slot", -30),
            ("scheduled:other", "scheduled", "e" * 64, "running", -10),
            ("manual:newest", "manual", "d" * 64, "running", -5),
            ("scheduled:terminal", "scheduled", "d" * 64, "succeeded", -1),
        ):
            due_at = now + timedelta(minutes=due_offset)
            connection.execute(
                text(
                    "INSERT INTO automation_cycles("
                    "cycle_slot,cycle_kind,settings_digest,idempotency_key,due_at,status,"
                    "owner_token,fencing_token,result_json,error_json,attempt_count,retry_budget,"
                    "next_retry_at,started_at,created_at,updated_at) VALUES ("
                    ":slot,:kind,:digest,NULL,:due,:status,'owner',1,'{}','[]',2,4,"
                    ":retry,:due,:due,:due)"
                ),
                {
                    "slot": slot,
                    "kind": kind,
                    "digest": digest,
                    "status": status,
                    "due": due_at,
                    "retry": due_at + timedelta(minutes=20),
                },
            )

    found = runtime.find_recoverable_scheduled_cycle("d" * 64, now=now)
    assert found is not None
    assert found.cycle_slot == "scheduled:new"
    assert found.due_at == now - timedelta(minutes=30)
    assert found.next_retry_at == now - timedelta(minutes=10)
    assert found.status == "partial_retry_same_slot"
    assert found.attempt_count == 2
    assert runtime.find_recoverable_scheduled_cycle("missing", now=now) is None

    lease = runtime.acquire(
        lease_key=AUTOMATION_LEASE_KEY,
        owner_token="foreign-owner",
        cycle_slot="scheduled:foreign",
        now=now,
    )
    assert lease is not None
    assert runtime.find_recoverable_scheduled_cycle("d" * 64, now=now) is None
    assert runtime.find_recoverable_scheduled_cycle(
        "d" * 64,
        now=now + timedelta(hours=4),
    ) is not None


def test_legacy_not_null_outbox_attempt_four_is_atomic_and_advances_scheduler(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-not-null-outbox.db'}"
    initialize_database(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE delivery_outbox"))
        connection.execute(
            text(
                "CREATE TABLE delivery_outbox ("
                "delivery_id VARCHAR(64) PRIMARY KEY, "
                "brief_id VARCHAR(64) NOT NULL, channel VARCHAR(32) NOT NULL, "
                "recipient VARCHAR(255), subject TEXT NOT NULL, markdown TEXT NOT NULL, "
                "payload_json TEXT NOT NULL DEFAULT '{}', status VARCHAR(32) NOT NULL, "
                "created_at DATETIME, updated_at DATETIME, sent_at DATETIME, "
                "FOREIGN KEY(brief_id) REFERENCES brief_runs(brief_id))"
            )
        )
    db_module._apply_additive_migrations(engine)
    inspector = inspect(engine)
    brief_id_column = next(
        item
        for item in inspector.get_columns("delivery_outbox")
        if item["name"] == "brief_id"
    )
    assert brief_id_column["nullable"] is False
    assert inspector.get_foreign_keys("delivery_outbox")[0]["referred_table"] == "brief_runs"

    runtime = AutomationRuntimeRepository(create_session_factory(database_url))
    due = datetime(2026, 8, 29, 4, 0, tzinfo=timezone.utc)
    slot = scheduled_cycle_slot(due, "b" * 64)
    current = due
    for attempt in range(1, 4):
        started = runtime.begin_cycle(
            cycle_slot=slot,
            cycle_kind="scheduled",
            settings_digest="b" * 64,
            due_at=due,
            idempotency_key=None,
            owner_token=f"legacy-owner-{attempt}",
            now=current,
        )
        assert started.grant is not None
        runtime.begin_stage(
            started.grant,
            "paper_update",
            retry_scope="paper_update:free",
            now=current,
        )
        runtime.fail_stage(
            started.grant,
            "paper_update",
            "provider timeout",
            retry_scope="paper_update:free",
            error_fingerprint="b" * 64,
            error_kind="timeout",
            retryable=True,
            now=current,
        )
        assert runtime.finalize_cycle(
            started.grant,
            result={"attempt": attempt},
            errors=["paper_update: provider timeout"],
            issues=[],
            required_stages={"paper_update"},
            now=current,
        ) == "partial_retry_same_slot"
        retry_at = runtime.cycle_retry_state(slot).next_retry_at
        assert retry_at is not None
        current = retry_at

    settings = AutoProcessingSettings(provider="free", interval_seconds=1800)
    scheduler = AutomationScheduler()
    with scheduler._lock:
        scheduler._enabled = True
        scheduler._generation = 11
        scheduler._settings = settings
        scheduler._cycle_due_at = due
        scheduler._next_run_at = current

    def fourth_attempt(_settings):
        started = runtime.begin_cycle(
            cycle_slot=slot,
            cycle_kind="scheduled",
            settings_digest="b" * 64,
            due_at=due,
            idempotency_key=None,
            owner_token="legacy-owner-4",
            now=current,
        )
        assert started.grant is not None
        runtime.begin_stage(
            started.grant,
            "paper_update",
            retry_scope="paper_update:free",
            now=current,
        )
        runtime.fail_stage(
            started.grant,
            "paper_update",
            "provider timeout",
            retry_scope="paper_update:free",
            error_fingerprint="b" * 64,
            error_kind="timeout",
            retryable=True,
            now=current,
        )
        status = runtime.finalize_cycle(
            started.grant,
            result={"attempt": 4},
            errors=["paper_update: provider timeout"],
            issues=[],
            required_stages={"paper_update"},
            now=current,
        )
        return AutoProcessingCycleResult(
            provider="free",
            started_at=current,
            finished_at=current,
            scan_status="failed",
            errors=["paper_update: provider timeout"],
            data_health={"automation_cycle_status": status},
        )

    assert scheduler._execute(settings, fourth_attempt, generation=11) is True
    state = scheduler.state()
    assert state.run_count == 1
    assert state.cycle_due_at == current + timedelta(minutes=30)
    assert state.next_run_at == current + timedelta(minutes=30)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT status FROM automation_cycles WHERE cycle_slot=:slot"),
            {"slot": slot},
        ).scalar_one() == "deferred_with_alert"
        assert connection.execute(
            text("SELECT COUNT(*) FROM automation_incidents")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM brief_runs WHERE brief_id LIKE 'automation-brief-%'")
        ).scalar_one() == 1
        delivery = connection.execute(
            text("SELECT COUNT(*),MIN(brief_id) FROM delivery_outbox")
        ).one()
    assert delivery[0] == 1
    assert delivery[1].startswith("automation-brief-")


def test_unkeyed_manual_failure_is_one_shot_and_keyed_manual_is_retryable(tmp_path):
    runtime = _runtime(f"sqlite:///{tmp_path / 'manual-retry-contract.db'}")
    now = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)

    unkeyed_slot, unkeyed_id = manual_cycle_slot()
    unkeyed = runtime.begin_cycle(
        cycle_slot=unkeyed_slot,
        cycle_kind="manual",
        settings_digest="1" * 64,
        due_at=None,
        idempotency_key=unkeyed_id,
        owner_token="unkeyed-owner",
        now=now,
    )
    assert unkeyed.grant is not None
    runtime.begin_stage(
        unkeyed.grant,
        "scan",
        retry_scope="scan:free",
        now=now,
    )
    runtime.fail_stage(
        unkeyed.grant,
        "scan",
        "provider timeout",
        retry_scope="scan:free",
        error_fingerprint="1" * 64,
        error_kind="timeout",
        retryable=True,
        now=now,
    )
    assert runtime.finalize_cycle(
        unkeyed.grant,
        result={},
        errors=["scan: provider timeout"],
        issues=[],
        required_stages={"scan"},
        now=now,
    ) == "deferred_with_alert"
    assert runtime.cycle_retry_state(unkeyed_slot).terminal_reason == "manual_one_shot"

    keyed_slot, keyed_id = manual_cycle_slot("stable-key")
    keyed = runtime.begin_cycle(
        cycle_slot=keyed_slot,
        cycle_kind="manual",
        settings_digest="2" * 64,
        due_at=None,
        idempotency_key=keyed_id,
        owner_token="keyed-owner",
        now=now,
    )
    assert keyed.grant is not None
    runtime.begin_stage(keyed.grant, "scan", now=now)
    runtime.fail_stage(
        keyed.grant,
        "scan",
        "provider timeout",
        error_fingerprint="2" * 64,
        error_kind="timeout",
        retryable=True,
        now=now,
    )
    assert runtime.finalize_cycle(
        keyed.grant,
        result={},
        errors=["scan: provider timeout"],
        issues=[],
        required_stages={"scan"},
        now=now,
    ) == "partial_retry_same_slot"

    early = runtime.begin_cycle(
        cycle_slot=keyed_slot,
        cycle_kind="manual",
        settings_digest="2" * 64,
        due_at=None,
        idempotency_key=keyed_id,
        owner_token="early-owner",
        now=now + timedelta(minutes=1),
    )
    assert early.grant is None
    assert early.retry_not_due_at == now + timedelta(minutes=5)


def test_auth_failure_is_first_attempt_terminal_without_automation_breaker(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'auth-permanent.db'}"
    runtime = _runtime(database_url)
    now = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
    due = now
    slot = scheduled_cycle_slot(due, "a" * 64)
    started = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="scheduled",
        settings_digest="a" * 64,
        due_at=due,
        idempotency_key=None,
        owner_token="auth-owner",
        now=now,
    )
    assert started.grant is not None
    runtime.begin_stage(
        started.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=now,
    )
    classified = classify_automation_error(
        "paper_update",
        "free",
        "HTTP 401 unauthorized",
        {
            "provider_error_kind": "auth",
            "provider_error_code": "401",
            "provider_error_retryable": "false",
        },
    )
    runtime.fail_stage(
        started.grant,
        "paper_update",
        "HTTP 401 unauthorized",
        retry_scope="paper_update:free",
        error_fingerprint=classified.fingerprint,
        error_kind=classified.error_kind,
        retryable=classified.retryable,
        now=now,
    )
    assert runtime.finalize_cycle(
        started.grant,
        result={},
        errors=["paper_update: HTTP 401 unauthorized"],
        issues=[],
        required_stages={"paper_update"},
        now=now,
    ) == "deferred_with_alert"
    retry = runtime.cycle_retry_state(slot)
    assert retry.attempt_count == 1
    assert retry.terminal_reason == "permanent_error"
    with create_engine(database_url).connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM automation_circuit_breakers")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM automation_incidents")
        ).scalar_one() == 1


def test_open_breaker_skips_then_allows_one_half_open_probe_and_recovers(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'half-open.db'}"
    runtime = _runtime(database_url)
    engine = create_engine(database_url)
    base = datetime(2026, 8, 29, 2, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO automation_circuit_breakers "
                "(scope_key,state,failure_count,open_count,next_probe_at,revision,created_at,updated_at) "
                "VALUES ('paper_update:free','open',1,1,:probe,1,:now,:now)"
            ),
            {"probe": base + timedelta(minutes=30), "now": base},
        )

    skipped_slot = scheduled_cycle_slot(base, "e" * 64)
    skipped = runtime.begin_cycle(
        cycle_slot=skipped_slot,
        cycle_kind="scheduled",
        settings_digest="e" * 64,
        due_at=base,
        idempotency_key=None,
        owner_token="skip-owner",
        now=base,
    )
    assert skipped.grant is not None
    checkpoint = runtime.begin_stage(
        skipped.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=base,
    )
    assert checkpoint is not None
    assert "circuit open" in checkpoint["stage_issue"]
    assert runtime.finalize_cycle(
        skipped.grant,
        result={},
        errors=[],
        issues=[checkpoint["stage_issue"]],
        required_stages={"paper_update"},
        now=base,
    ) == "deferred_with_alert"

    first_probe_at = base + timedelta(minutes=30)
    probe_slot = scheduled_cycle_slot(first_probe_at, "e" * 64)
    probe = runtime.begin_cycle(
        cycle_slot=probe_slot,
        cycle_kind="scheduled",
        settings_digest="e" * 64,
        due_at=first_probe_at,
        idempotency_key=None,
        owner_token="probe-owner",
        now=first_probe_at,
    )
    assert probe.grant is not None
    assert runtime.begin_stage(
        probe.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=first_probe_at,
    ) is None
    with pytest.raises(AutomationCycleBusyError):
        runtime.begin_cycle(
            cycle_slot=scheduled_cycle_slot(first_probe_at, "f" * 64),
            cycle_kind="scheduled",
            settings_digest="f" * 64,
            due_at=first_probe_at,
            idempotency_key=None,
            owner_token="second-probe-owner",
            now=first_probe_at,
        )
    runtime.fail_stage(
        probe.grant,
        "paper_update",
        "provider timeout",
        retry_scope="paper_update:free",
        error_fingerprint="f" * 64,
        error_kind="provider_or_coverage",
        retryable=True,
        now=first_probe_at,
    )
    assert runtime.finalize_cycle(
        probe.grant,
        result={},
        errors=["paper_update: provider timeout"],
        issues=[],
        required_stages={"paper_update"},
        now=first_probe_at,
    ) == "deferred_with_alert"
    with engine.connect() as connection:
        breaker = connection.execute(
            text(
                "SELECT state,open_count,next_probe_at FROM automation_circuit_breakers "
                "WHERE scope_key='paper_update:free'"
            )
        ).one()
    assert breaker.state == "open"
    assert breaker.open_count == 2

    second_probe_at = first_probe_at + timedelta(hours=1)
    recovered_slot = scheduled_cycle_slot(second_probe_at, "e" * 64)
    recovered = runtime.begin_cycle(
        cycle_slot=recovered_slot,
        cycle_kind="scheduled",
        settings_digest="e" * 64,
        due_at=second_probe_at,
        idempotency_key=None,
        owner_token="recovered-owner",
        now=second_probe_at,
    )
    assert recovered.grant is not None
    assert runtime.begin_stage(
        recovered.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=second_probe_at,
    ) is None
    runtime.complete_stage(
        recovered.grant,
        "paper_update",
        {"paper_total": 1},
        retry_scope="paper_update:free",
        now=second_probe_at,
    )
    assert runtime.finalize_cycle(
        recovered.grant,
        result={},
        errors=[],
        issues=[],
        required_stages={"paper_update"},
        now=second_probe_at,
    ) == "succeeded"
    with engine.connect() as connection:
        state = connection.execute(
            text(
                "SELECT state,open_count FROM automation_circuit_breakers "
                "WHERE scope_key='paper_update:free'"
            )
        ).one()
    assert state.state == "closed"
    assert state.open_count == 0


@pytest.mark.parametrize("terminal_status", ["deferred", "skipped"])
def test_half_open_deferred_or_skipped_releases_probe_back_to_open(
    tmp_path,
    terminal_status,
):
    database_url = f"sqlite:///{tmp_path / f'probe-{terminal_status}.db'}"
    runtime = _runtime(database_url)
    engine = create_engine(database_url)
    now = datetime(2026, 8, 29, 5, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO automation_circuit_breakers "
                "(scope_key,state,failure_count,open_count,next_probe_at,revision,created_at,updated_at) "
                "VALUES ('paper_update:free','open',1,1,:now,1,:now,:now)"
            ),
            {"now": now},
        )
    slot = scheduled_cycle_slot(now, "3" * 64)
    started = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="scheduled",
        settings_digest="3" * 64,
        due_at=now,
        idempotency_key=None,
        owner_token="probe-owner",
        now=now,
    )
    assert started.grant is not None
    assert runtime.begin_stage(
        started.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=now,
    ) is None

    heartbeat_at = now + timedelta(hours=1)
    runtime.heartbeat(started.grant, now=heartbeat_at)
    with engine.connect() as connection:
        extended = connection.execute(
            text(
                "SELECT probe_expires_at FROM automation_circuit_breakers "
                "WHERE scope_key='paper_update:free'"
            )
        ).scalar_one()
    assert datetime.fromisoformat(str(extended)).replace(tzinfo=timezone.utc) == (
        heartbeat_at + timedelta(hours=3)
    )

    runtime.complete_stage(
        started.grant,
        "paper_update",
        {"stage_issue": "natural deferral"},
        status=terminal_status,
        retry_scope="paper_update:free",
        now=heartbeat_at,
    )
    with engine.connect() as connection:
        breaker = connection.execute(
            text(
                "SELECT state,half_open_cycle_slot,probe_expires_at,next_probe_at "
                "FROM automation_circuit_breakers WHERE scope_key='paper_update:free'"
            )
        ).one()
    assert breaker.state == "open"
    assert breaker.half_open_cycle_slot is None
    assert breaker.probe_expires_at is None
    assert datetime.fromisoformat(str(breaker.next_probe_at)).replace(
        tzinfo=timezone.utc
    ) == heartbeat_at + timedelta(minutes=30)


def test_active_half_open_probe_cannot_be_stolen_but_expired_probe_is_reclaimed(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'probe-reclaim.db'}"
    runtime = _runtime(database_url)
    engine = create_engine(database_url)
    now = datetime(2026, 8, 29, 6, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO automation_circuit_breakers "
                "(scope_key,state,failure_count,open_count,next_probe_at,revision,created_at,updated_at) "
                "VALUES ('paper_update:free','open',1,1,:now,1,:now,:now)"
            ),
            {"now": now},
        )

    first_slot = scheduled_cycle_slot(now, "4" * 64)
    first = runtime.begin_cycle(
        cycle_slot=first_slot,
        cycle_kind="scheduled",
        settings_digest="4" * 64,
        due_at=now,
        idempotency_key=None,
        owner_token="first-probe",
        now=now,
    )
    assert first.grant is not None
    assert runtime.begin_stage(
        first.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=now,
    ) is None

    contender_at = now + timedelta(minutes=1)
    contender_slot = scheduled_cycle_slot(contender_at, "5" * 64)
    contender = runtime.begin_cycle(
        cycle_slot=contender_slot,
        cycle_kind="scheduled",
        settings_digest="5" * 64,
        due_at=contender_at,
        idempotency_key=None,
        owner_token="contender",
        now=contender_at,
        process_fence_held=True,
    )
    assert contender.grant is not None
    checkpoint = runtime.begin_stage(
        contender.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=contender_at,
    )
    assert checkpoint is not None
    assert checkpoint["data_health"]["automation_paper_update_circuit_state"] == "half_open"

    reclaim_at = now + timedelta(hours=3, seconds=1)
    reclaim_slot = scheduled_cycle_slot(reclaim_at, "6" * 64)
    reclaim = runtime.begin_cycle(
        cycle_slot=reclaim_slot,
        cycle_kind="scheduled",
        settings_digest="6" * 64,
        due_at=reclaim_at,
        idempotency_key=None,
        owner_token="reclaimer",
        now=reclaim_at,
        process_fence_held=True,
    )
    assert reclaim.grant is not None
    assert runtime.begin_stage(
        reclaim.grant,
        "paper_update",
        retry_scope="paper_update:free",
        now=reclaim_at,
    ) is None
    with engine.connect() as connection:
        breaker = connection.execute(
            text(
                "SELECT state,half_open_cycle_slot,probe_expires_at "
                "FROM automation_circuit_breakers WHERE scope_key='paper_update:free'"
            )
        ).one()
    assert breaker.state == "half_open"
    assert breaker.half_open_cycle_slot == reclaim_slot
    assert datetime.fromisoformat(str(breaker.probe_expires_at)).replace(
        tzinfo=timezone.utc
    ) == reclaim_at + timedelta(hours=3)


def test_outbox_same_facts_reuses_id_and_different_facts_conflict(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'outbox.db'}"
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))
    first = repo.enqueue_delivery(
        subject="same",
        markdown="facts",
        payload={"count": 1},
        idempotency_key="cycle-alert-1",
    )
    replay = repo.enqueue_delivery(
        subject="same",
        markdown="facts",
        payload={"count": 1},
        idempotency_key="cycle-alert-1",
    )
    assert replay.delivery_id == first.delivery_id
    with pytest.raises(DeliveryIdempotencyConflictError):
        repo.enqueue_delivery(
            subject="changed",
            markdown="facts",
            payload={"count": 2},
            idempotency_key="cycle-alert-1",
        )
    brief = BriefRunRecord(
        brief_id="brief-recipient-test",
        provider="fixture",
        symbols=["US:TEST"],
        headline="brief",
        opportunity_count=1,
        entry_watch_count=0,
        risk_alert_count=0,
        catalyst_count=0,
        validation_count=0,
        data_health={},
        payload={},
        created_at=datetime.now(timezone.utc),
    )
    first_recipient = repo.enqueue_brief_delivery(
        brief,
        recipient="first@example.com",
        markdown="same brief",
    )
    second_recipient = repo.enqueue_brief_delivery(
        brief,
        recipient="second@example.com",
        markdown="same brief",
    )
    assert first_recipient.delivery_id != second_recipient.delivery_id
    assert first_recipient.idempotency_key is None
    assert second_recipient.idempotency_key is None


def test_scheduler_state_revision_rejects_stale_runtime_but_merges_control_plane(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'scheduler-cas.db'}"
    initialize_database(database_url)
    first = QagentRepository(create_session_factory(database_url))
    second = QagentRepository(create_session_factory(database_url))
    initial = first.save_automation_scheduler_state(
        enabled=True,
        settings={"provider": "free"},
        runtime={"run_count": 1, "last_error": None},
        expected_revision=0,
    )
    stale = second.get_automation_scheduler_state()
    assert stale is not None and stale.revision == initial.revision
    winner = first.save_automation_scheduler_state(
        enabled=True,
        settings={"provider": "free"},
        runtime={"run_count": 2, "last_error": None, "winner": "yes"},
        expected_revision=initial.revision,
    )
    rejected = second.save_automation_scheduler_state(
        enabled=True,
        settings={"provider": "stale"},
        runtime={"run_count": 1, "last_error": "stale loser"},
        expected_revision=stale.revision,
    )
    assert rejected.revision == winner.revision
    assert rejected.runtime["run_count"] == 2
    assert rejected.runtime["winner"] == "yes"

    stopped = second.save_automation_scheduler_state(
        enabled=False,
        settings={"provider": "free"},
        runtime={"run_count": 1, "last_error": "stale loser"},
        expected_revision=stale.revision,
        control_plane=True,
    )
    assert stopped.enabled is False
    assert stopped.runtime["run_count"] == 2
    assert stopped.runtime["winner"] == "yes"
    assert stopped.revision == winner.revision + 1


def test_two_paper_repositories_do_not_exceed_capacity_or_double_open(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'paper-race.db'}"
    initialize_database(database_url)
    first = PaperTradingRepository(create_session_factory(database_url))
    second = PaperTradingRepository(create_session_factory(database_url))
    barrier = Barrier(2)

    def create(repo: PaperTradingRepository, snapshot: str):
        barrier.wait()
        return repo.create_trade_if_capacity(
            source_snapshot_id=snapshot,
            provider="free",
            instrument_id="CN:600000",
            strategy_id="strategy",
            signal_date=date(2026, 8, 28),
            trigger_price=Decimal("10"),
            initial_stop=Decimal("9"),
            target_1=Decimal("12"),
            max_active_trades=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        created = list(executor.map(create, (first, second), ("snapshot-a", "snapshot-b")))
    assert sum(item is not None for item in created) == 1
    trades = first.list_trades(limit=10)
    assert len(trades) == 1
    canonical = hashlib.sha256(
        json.dumps(
            [trade.model_dump(mode="json") for trade in trades],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    replay = first.create_trade_if_capacity(
        source_snapshot_id=trades[0].source_snapshot_id,
        provider="free",
        instrument_id="CN:600000",
        strategy_id="strategy",
        signal_date=date(2026, 8, 28),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        max_active_trades=1,
    )
    assert replay is None
    assert canonical == hashlib.sha256(
        json.dumps(
            [trade.model_dump(mode="json") for trade in first.list_trades(limit=10)],
            sort_keys=True,
        ).encode()
    ).hexdigest()
