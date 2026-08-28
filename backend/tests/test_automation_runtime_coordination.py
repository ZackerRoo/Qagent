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
from sqlalchemy import create_engine, text

from qagent.db import create_session_factory, initialize_database
from qagent import db as db_module
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
from qagent.storage.paper import PaperTradingRepository
from qagent.storage.repository import (
    BriefRunRecord,
    DeliveryIdempotencyConflictError,
    QagentRepository,
)


def _runtime(database_url: str) -> AutomationRuntimeRepository:
    initialize_database(database_url)
    return AutomationRuntimeRepository(create_session_factory(database_url))


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
    first = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest="b" * 64,
        due_at=None,
        idempotency_key=idempotency_key,
        owner_token="first-owner",
    )
    assert first.grant is not None
    runtime.begin_stage(first.grant, "scan")
    runtime.complete_stage(first.grant, "scan", {"artifact_id": "scan-1"})
    runtime.begin_stage(first.grant, "paper_seed")
    runtime.fail_stage(first.grant, "paper_seed", "injected failure")
    assert runtime.finalize_cycle(
        first.grant,
        result={"status": "partial"},
        errors=["paper_seed: injected failure"],
        issues=[],
        required_stages={"scan", "paper_seed"},
    ) == "partial_retry_same_slot"

    retry = runtime.begin_cycle(
        cycle_slot=slot,
        cycle_kind="manual",
        settings_digest="b" * 64,
        due_at=None,
        idempotency_key=idempotency_key,
        owner_token="retry-owner",
    )
    assert retry.grant is not None
    assert retry.grant.fencing_token > first.grant.fencing_token
    assert runtime.begin_stage(retry.grant, "scan") == {"artifact_id": "scan-1"}
    assert runtime.begin_stage(retry.grant, "paper_seed") is None
    runtime.complete_stage(retry.grant, "paper_seed", {"created": 1})
    assert runtime.finalize_cycle(
        retry.grant,
        result={"status": "succeeded"},
        errors=[],
        issues=[],
        required_stages={"scan", "paper_seed"},
    ) == "succeeded"

    with pytest.raises(AutomationCycleConflictError, match="different facts"):
        runtime.begin_cycle(
            cycle_slot=slot,
            cycle_kind="manual",
            settings_digest="c" * 64,
            due_at=None,
            idempotency_key=idempotency_key,
            owner_token="conflicting-owner",
        )


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
