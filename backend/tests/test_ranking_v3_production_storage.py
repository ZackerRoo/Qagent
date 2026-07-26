from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardIdentity,
    stable_digest,
)
from qagent.backtesting.ranking_v3_production import (
    LEGACY_PRODUCTION_BATCH_SCHEMA_VERSION,
    PRODUCTION_BATCH_ATTESTATION_KIND,
    RankingV3ProductionBatch,
    RankingV3ProductionBatchInput,
    RankingV3ProductionAuthorizationError,
    RankingV3ProductionConflictError,
    RankingV3ProductionIdentity,
    RankingV3ProductionIntegrityError,
    RankingV3ProductionSelectionItem,
    production_batch_fact_digest,
    require_current_ranking_v3_production_batch,
)
from qagent.db import create_session_factory, initialize_database
from qagent.security.ranking_v3_attestation import RankingV3Attestor
from qagent.storage.ranking_v3_production import RankingV3ProductionRepository
from qagent.storage.tables import (
    OpportunitySnapshotRow,
    RankingV3ProductionBatchRow,
    ScanRunRow,
)


RECORDED_AT = datetime(2026, 7, 29, 8, 30, tzinfo=timezone.utc)
ATTESTOR = RankingV3Attestor(b"k" * 32)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _identity() -> RankingV3ProductionIdentity:
    return RankingV3ProductionIdentity.create(
        release_proof_digest="a" * 64,
        validation_run_id="validation-run-42",
        data_revision="data-revision-20260726",
        protocol_identity=RankingV3ForwardIdentity(
            protocol_id="QAGENT-RANK-V3.2-20260726",
            protocol_digest="b" * 64,
            model_version="point-in-time-net-excess-v3.2",
        ),
    )


def _selection(
    rank: int,
    *,
    prefix: str = "",
) -> RankingV3ProductionSelectionItem:
    return RankingV3ProductionSelectionItem.create(
        candidate_id=f"{prefix}candidate-{rank}",
        instrument_id=f"CN:{rank:06d}",
        source_snapshot_id=f"{prefix}snapshot-{rank}",
        strategy_id="ranking-v3",
        rank=rank,
        score=Decimal("0.9") - Decimal(rank) / Decimal("100"),
        source_rank_score=Decimal("0.9") - Decimal(rank) / Decimal("100"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        allocation_multiplier=Decimal("1"),
    )


def _batch(
    identity: RankingV3ProductionIdentity,
    *,
    session_date: date = date(2026, 7, 29),
    prefix: str = "",
    idempotency_key: str = "production-20260729",
    selections: tuple[RankingV3ProductionSelectionItem, ...] | None = None,
) -> RankingV3ProductionBatch:
    resolved_selections = (
        selections
        if selections is not None
        else (_selection(1, prefix=prefix), _selection(2, prefix=prefix))
    )
    started_at = datetime.combine(session_date, time(10, 0), tzinfo=SHANGHAI)
    completed_at = datetime.combine(session_date, time(15, 30), tzinfo=SHANGHAI)
    scan_recorded_at = datetime.combine(session_date, time(15, 31), tzinfo=SHANGHAI)
    recorded_at = datetime.combine(session_date, time(16, 30), tzinfo=SHANGHAI)
    source = RankingV3ProductionBatchInput.create(
        session_date=session_date,
        candidate_snapshot_digest=("c" if not prefix else "d") * 64,
        selections=resolved_selections,
        source_scan_run_id=(
            f"run-{resolved_selections[0].source_snapshot_id}"
            if resolved_selections
            else f"run-empty-{session_date.isoformat()}"
        ),
        source_scan_started_at=started_at,
        source_scan_completed_at=completed_at,
        source_scan_recorded_at=scan_recorded_at,
        recorded_at=recorded_at,
    )
    return RankingV3ProductionBatch(
        **source.model_dump(mode="python"),
        identity=identity,
        fact_digest=production_batch_fact_digest(identity, source),
        attestation=ATTESTOR.sign(
            "ranking-v3-production-batch",
            production_batch_fact_digest(identity, source),
        ),
        idempotency_key=idempotency_key,
    )


def _store(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'production.db'}"
    engine = initialize_database(database_url)
    return (
        engine,
        RankingV3ProductionRepository(
            create_session_factory(database_url),
            attestor=ATTESTOR,
        ),
    )


def _insert_snapshots(
    engine,
    selections,
    *,
    signal_date: date = date(2026, 7, 29),
) -> None:
    selections = tuple(selections)
    run_id = f"run-{selections[0].source_snapshot_id}" if selections else "run-empty"
    with engine.begin() as connection:
        connection.execute(
            ScanRunRow.__table__.insert().values(
                run_id=run_id,
                provider="free",
                mode="full",
                symbols="",
                scanned=len(selections),
                cards=len(selections),
                data_health="{}",
                started_at=datetime.combine(signal_date, time(10, 0), tzinfo=SHANGHAI),
                completed_at=datetime.combine(signal_date, time(15, 30), tzinfo=SHANGHAI),
                created_at=datetime.combine(signal_date, time(15, 31), tzinfo=SHANGHAI),
            )
        )
        for item in selections:
            connection.execute(
                OpportunitySnapshotRow.__table__.insert().values(
                    snapshot_id=item.source_snapshot_id,
                    run_id=run_id,
                    card_id=f"card-{item.candidate_id}",
                    instrument_id=item.instrument_id,
                    market="CN",
                    status="ready",
                    signal_date=signal_date,
                    latest_close=Decimal("10"),
                    primary_strategy_id=item.strategy_id,
                    score=Decimal("0.8"),
                    strategy_score=Decimal("0.8"),
                    rank_score=item.score,
                    trigger_price=Decimal("10"),
                    initial_stop=Decimal("9"),
                    target_1=Decimal("12"),
                    card_json="{}",
                    created_at=RECORDED_AT,
                )
            )


def test_repository_persists_canonical_batch_selections_and_aliases(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    batch = _batch(identity)
    _insert_snapshots(engine, batch.selections)

    first = repository.append_batch(batch)
    replay = repository.append_batch(batch)
    alias_request = batch.model_copy(update={"idempotency_key": "production-alias"})
    aliased = repository.append_batch(alias_request)

    assert first == replay == aliased
    assert repository.get_batch_for_session(identity, batch.session_date) == first
    assert repository.get_batch_by_idempotency_key(identity, batch.idempotency_key) == first
    assert repository.get_batch_by_idempotency_key(identity, "production-alias") == first
    assert repository.get_batch_by_fact_digest(identity, batch.fact_digest) == first
    binding = repository.get_selection_by_source_snapshot(
        identity,
        batch.selections[0].source_snapshot_id,
    )
    assert binding is not None
    assert binding.selection_item_digest == batch.selections[0].item_digest
    assert binding.batch_fact_digest == batch.fact_digest
    assert binding.identity_digest == identity.identity_digest
    assert repository.list_batches(identity) == (first,)

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM ranking_v3_production_batches")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM ranking_v3_production_selections")
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM ranking_v3_production_idempotency_keys")
            ).scalar_one()
            == 2
        )
        payload = connection.execute(
            text(
                "SELECT payload_json FROM ranking_v3_production_batches "
                "WHERE fact_digest = :fact_digest"
            ),
            {"fact_digest": batch.fact_digest},
        ).scalar_one()
    assert payload == json.dumps(
        batch.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_32_concurrent_exact_replays_persist_one_batch(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    batch = _batch(identity)
    _insert_snapshots(engine, batch.selections)

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda _: repository.append_batch(batch), range(32)))

    assert {item.fact_digest for item in results} == {batch.fact_digest}
    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT "
                "(SELECT COUNT(*) FROM ranking_v3_production_batches), "
                "(SELECT COUNT(*) FROM ranking_v3_production_selections), "
                "(SELECT COUNT(*) FROM ranking_v3_production_idempotency_keys)"
            )
        ).one()
    assert counts == (1, 2, 1)


def test_same_session_drift_and_idempotency_key_drift_are_rejected(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    first = _batch(identity)
    same_day_drift = _batch(identity, prefix="drift-", idempotency_key="drift-key")
    next_day_same_key = _batch(
        identity,
        session_date=date(2026, 7, 30),
        prefix="next-",
        idempotency_key=first.idempotency_key,
    )
    _insert_snapshots(engine, first.selections)
    _insert_snapshots(engine, same_day_drift.selections)
    _insert_snapshots(
        engine,
        next_day_same_key.selections,
        signal_date=date(2026, 7, 30),
    )
    repository.append_batch(first)

    with pytest.raises(
        RankingV3ProductionConflictError,
        match="session already has a different",
    ):
        repository.append_batch(same_day_drift)
    with pytest.raises(
        RankingV3ProductionConflictError,
        match="idempotency key is already bound",
    ):
        repository.append_batch(next_day_same_key)

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM ranking_v3_production_batches")
            ).scalar_one()
            == 1
        )


def test_production_rows_and_referenced_snapshot_are_database_immutable(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    batch = _batch(identity)
    _insert_snapshots(engine, batch.selections)
    repository.append_batch(batch)

    mutations = (
        (
            "UPDATE ranking_v3_production_batches SET selected_count = 99",
            "ranking_v3_production_batches rows are immutable",
        ),
        (
            "DELETE FROM ranking_v3_production_selections",
            "ranking_v3_production_selections rows are immutable",
        ),
        (
            "UPDATE ranking_v3_production_idempotency_keys "
            "SET batch_fact_digest = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'",
            "ranking_v3_production_idempotency_keys rows are immutable",
        ),
        (
            "UPDATE opportunity_snapshots SET status = 'changed' WHERE snapshot_id = 'snapshot-1'",
            "production selection is immutable",
        ),
        (
            "DELETE FROM opportunity_snapshots WHERE snapshot_id = 'snapshot-1'",
            "production selection is immutable",
        ),
        (
            "UPDATE scan_runs SET cards = 99 WHERE run_id = 'run-snapshot-1'",
            "scan run referenced by Ranking V3 production selection is immutable",
        ),
    )
    for statement, message in mutations:
        with pytest.raises(DBAPIError, match=message):
            with engine.begin() as connection:
                connection.execute(text(statement))


def test_selection_and_alias_must_reference_authoritative_rows(tmp_path):
    engine, _ = _store(tmp_path)
    orphan = RankingV3ProductionSelectionItem.create(
        candidate_id="orphan-candidate",
        instrument_id="CN:000001",
        source_snapshot_id="orphan-snapshot",
        strategy_id="ranking-v3",
        rank=1,
        score=Decimal("0.9"),
        source_rank_score=Decimal("0.9"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        allocation_multiplier=Decimal("1"),
    )
    _insert_snapshots(engine, (orphan,))
    with pytest.raises(DBAPIError, match="must reference"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ranking_v3_production_selections ("
                    "batch_fact_digest, item_digest, identity_digest, candidate_id, "
                    "instrument_id, source_snapshot_id, strategy_id, rank, score, "
                    "payload_json, recorded_at"
                    ") VALUES (:digest, :item, :identity, 'candidate', 'CN:000001', "
                    "'orphan-snapshot', 'ranking-v3', 1, 9000000000, '{}', :recorded)"
                ),
                {
                    "digest": "f" * 64,
                    "item": "e" * 64,
                    "identity": "d" * 64,
                    "recorded": RECORDED_AT,
                },
            )

    with pytest.raises(DBAPIError, match="alias must reference its batch"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ranking_v3_production_idempotency_keys ("
                    "identity_digest, idempotency_key, batch_fact_digest, payload_json, "
                    "recorded_at) VALUES (:identity, 'missing', :digest, '{}', :recorded)"
                ),
                {
                    "identity": "d" * 64,
                    "digest": "f" * 64,
                    "recorded": RECORDED_AT,
                },
            )


def test_selection_must_be_a_canonical_batch_member_and_match_snapshot(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    batch = _batch(identity)
    rogue = RankingV3ProductionSelectionItem.create(
        candidate_id="rogue-candidate",
        instrument_id="CN:000003",
        source_snapshot_id="rogue-snapshot",
        strategy_id="ranking-v3",
        rank=3,
        score=Decimal("0.7"),
        source_rank_score=Decimal("0.7"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        allocation_multiplier=Decimal("1"),
    )
    _insert_snapshots(engine, (*batch.selections, rogue))
    repository.append_batch(batch)

    with pytest.raises(DBAPIError, match="must reference"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ranking_v3_production_selections ("
                    "batch_fact_digest, item_digest, identity_digest, candidate_id, "
                    "instrument_id, source_snapshot_id, strategy_id, rank, score, "
                    "payload_json, recorded_at"
                    ") VALUES (:batch, :item, :identity, :candidate, :instrument, "
                    ":snapshot, :strategy, :rank, :score, :payload, :recorded)"
                ),
                {
                    "batch": batch.fact_digest,
                    "item": rogue.item_digest,
                    "identity": identity.identity_digest,
                    "candidate": rogue.candidate_id,
                    "instrument": rogue.instrument_id,
                    "snapshot": rogue.source_snapshot_id,
                    "strategy": rogue.strategy_id,
                    "rank": rogue.rank,
                    "score": 7000000000,
                    "payload": json.dumps(
                        rogue.model_dump(mode="json"),
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "recorded": RECORDED_AT,
                },
            )

    mismatched = RankingV3ProductionSelectionItem.create(
        candidate_id="mismatched-candidate",
        instrument_id="CN:999999",
        source_snapshot_id=batch.selections[0].source_snapshot_id,
        strategy_id="ranking-v3",
        rank=1,
        score=Decimal("0.8"),
        source_rank_score=Decimal("0.8"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        allocation_multiplier=Decimal("1"),
    )
    mismatched_batch = _batch(
        identity,
        session_date=date(2026, 7, 30),
        prefix="mismatched-",
        idempotency_key="mismatched",
        selections=(mismatched,),
    )
    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="scan timestamps do not match",
    ):
        repository.append_batch(mismatched_batch)


def test_repository_rejects_canonical_payload_tampering_on_read(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    batch = _batch(identity)
    _insert_snapshots(engine, batch.selections)
    repository.append_batch(batch)

    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_ranking_v3_production_batches_immutable_update"))
        connection.execute(
            text(
                "UPDATE ranking_v3_production_batches SET payload_json = '{}' "
                "WHERE fact_digest = :fact_digest"
            ),
            {"fact_digest": batch.fact_digest},
        )

    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="batch payload is invalid",
    ):
        repository.get_batch_for_session(identity, batch.session_date)


def test_repository_rejects_batch_signed_by_a_different_server_key(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    batch = _batch(identity)
    _insert_snapshots(engine, batch.selections)
    repository.append_batch(batch)
    wrong_key_repository = RankingV3ProductionRepository(
        repository.session_factory,
        attestor=RankingV3Attestor(b"x" * 32),
    )

    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="attestation is invalid",
    ):
        wrong_key_repository.get_batch_for_session(identity, batch.session_date)


def test_repository_reads_legacy_batch_but_formal_admission_fails_closed(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    unsigned_payload = {
        "schema_version": LEGACY_PRODUCTION_BATCH_SCHEMA_VERSION,
        "session_date": date(2026, 7, 28),
        "candidate_snapshot_digest": "d" * 64,
        "selected_count": 0,
        "selections": (),
    }
    source = RankingV3ProductionBatchInput(
        **unsigned_payload,
        selection_batch_digest=stable_digest(unsigned_payload),
    )
    fact_digest = production_batch_fact_digest(identity, source)
    legacy = RankingV3ProductionBatch(
        **(
            source.model_dump(mode="python")
            | {"recorded_at": RECORDED_AT}
        ),
        identity=identity,
        fact_digest=fact_digest,
        attestation=ATTESTOR.sign(
            PRODUCTION_BATCH_ATTESTATION_KIND,
            fact_digest,
        ),
        idempotency_key="legacy-read-only",
    )
    with engine.begin() as connection:
        connection.execute(
            RankingV3ProductionBatchRow.__table__.insert().values(
                fact_digest=legacy.fact_digest,
                identity_digest=identity.identity_digest,
                release_proof_digest=identity.release_proof_digest,
                validation_run_id=identity.validation_run_id,
                data_revision=identity.data_revision,
                protocol_id=identity.protocol_identity.protocol_id,
                protocol_digest=identity.protocol_identity.protocol_digest,
                model_version=identity.protocol_identity.model_version,
                session_date=legacy.session_date,
                candidate_snapshot_digest=legacy.candidate_snapshot_digest,
                selection_batch_digest=legacy.selection_batch_digest,
                selected_count=0,
                payload_json=json.dumps(
                    legacy.model_dump(mode="json"),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                recorded_at=legacy.recorded_at,
            )
        )

    persisted = repository.get_batch_by_fact_digest(identity, fact_digest)

    assert persisted == legacy
    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="legacy production batch is readable",
    ):
        require_current_ranking_v3_production_batch(persisted)


def test_repository_rejects_batch_when_signed_scan_times_do_not_match_source(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    batch = _batch(identity)
    _insert_snapshots(engine, batch.selections)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE scan_runs SET completed_at = :tampered "
                "WHERE run_id = :run_id"
            ),
            {
                "tampered": batch.source_scan_completed_at + timedelta(minutes=1),
                "run_id": batch.source_scan_run_id,
            },
        )

    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="scan timestamps do not match",
    ):
        repository.append_batch(batch)


def test_paper_trade_production_binding_must_match_exact_selection(tmp_path):
    engine, repository = _store(tmp_path)
    identity = _identity()
    batch = _batch(identity)
    selection = batch.selections[0]
    _insert_snapshots(engine, batch.selections)
    repository.append_batch(batch)

    base_values = {
        "source_snapshot_id": selection.source_snapshot_id,
        "instrument_id": selection.instrument_id,
        "strategy_id": selection.strategy_id,
        "signal_date": batch.session_date,
        "trigger_price": 10,
        "initial_stop": 9,
        "target_1": 12,
        "rank_score": float(selection.score),
        "allocation_multiplier": 1,
        "holding_days": 0,
        "notes": "",
        "created_at": RECORDED_AT,
        "updated_at": RECORDED_AT,
    }
    with pytest.raises(DBAPIError, match="production admission proof is invalid"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO paper_trades ("
                    "trade_id, source_snapshot_id, provider, instrument_id, strategy_id, "
                    "admission_source, status, signal_date, trigger_price, "
                    "allocation_multiplier, holding_days, notes, created_at, updated_at"
                    ") VALUES ('invalid-paper', :source_snapshot_id, 'free', "
                    ":instrument_id, :strategy_id, 'ranking_v3_production', 'pending', "
                    ":signal_date, :trigger_price, :allocation_multiplier, :holding_days, "
                    ":notes, :created_at, :updated_at)"
                ),
                base_values,
            )

    binding = {
        **base_values,
        "production_identity_digest": identity.identity_digest,
        "production_batch_fact_digest": batch.fact_digest,
        "production_selection_item_digest": selection.item_digest,
        "release_proof_digest": identity.release_proof_digest,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO paper_trades ("
                "trade_id, source_snapshot_id, provider, instrument_id, strategy_id, "
                "admission_source, production_identity_digest, "
                "production_batch_fact_digest, production_selection_item_digest, "
                "release_proof_digest, status, signal_date, trigger_price, initial_stop, "
                "target_1, rank_score, "
                "allocation_multiplier, holding_days, notes, created_at, updated_at"
                ") VALUES ('valid-paper', :source_snapshot_id, 'free', :instrument_id, "
                ":strategy_id, 'ranking_v3_production', :production_identity_digest, "
                ":production_batch_fact_digest, :production_selection_item_digest, "
                ":release_proof_digest, 'pending', :signal_date, :trigger_price, "
                ":initial_stop, :target_1, :rank_score, "
                ":allocation_multiplier, :holding_days, :notes, :created_at, :updated_at)"
            ),
            binding,
        )

    for field, value in (
        ("trigger_price", 11),
        ("initial_stop", 8),
        ("target_1", 13),
        ("rank_score", 0.5),
        ("allocation_multiplier", 0.5),
    ):
        with pytest.raises(DBAPIError, match="production admission proof is invalid"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO paper_trades ("
                        "trade_id, source_snapshot_id, provider, instrument_id, strategy_id, "
                        "admission_source, production_identity_digest, "
                        "production_batch_fact_digest, production_selection_item_digest, "
                        "release_proof_digest, status, signal_date, trigger_price, initial_stop, "
                        "target_1, rank_score, allocation_multiplier, holding_days, notes, "
                        "created_at, updated_at"
                        ") VALUES (:trade_id, :source_snapshot_id, 'free', :instrument_id, "
                        ":strategy_id, 'ranking_v3_production', :production_identity_digest, "
                        ":production_batch_fact_digest, :production_selection_item_digest, "
                        ":release_proof_digest, 'pending', :signal_date, :trigger_price, "
                        ":initial_stop, :target_1, :rank_score, :allocation_multiplier, "
                        ":holding_days, :notes, :created_at, :updated_at)"
                    ),
                    {
                        **binding,
                        "trade_id": f"tampered-{field}",
                        field: value,
                    },
                )

    with pytest.raises(DBAPIError, match="production plan is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE paper_trades SET instrument_id = 'CN:999999' "
                    "WHERE trade_id = 'valid-paper'"
                )
            )
    with pytest.raises(DBAPIError, match="production plan is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE paper_trades SET signal_date = '2026-07-30' "
                    "WHERE trade_id = 'valid-paper'"
                )
            )
    with pytest.raises(DBAPIError, match="production plan is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE paper_trades SET admission_source = 'legacy_unknown', "
                    "production_identity_digest = NULL, "
                    "production_batch_fact_digest = NULL, "
                    "production_selection_item_digest = NULL, "
                    "release_proof_digest = NULL "
                    "WHERE trade_id = 'valid-paper'"
                )
            )
    with pytest.raises(DBAPIError, match="production plan is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE paper_trades SET allocation_multiplier = 0.5 "
                    "WHERE trade_id = 'valid-paper'"
                )
            )
