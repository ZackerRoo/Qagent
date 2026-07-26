from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from multiprocessing import get_context

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardConflictError,
    RankingV3ForwardGateEvidence,
    RankingV3ForwardIdentity,
    RankingV3ForwardMetrics,
    RankingV3ForwardReleaseProof,
    RankingV3ForwardSessionInput,
    RankingV3ShadowCandidateInput,
    stable_digest,
    stable_release_proof_digest,
)
from qagent.db import create_db_engine, create_session_factory, initialize_database
from qagent.security.ranking_v3_attestation import RankingV3Attestor
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository


IDENTITY = RankingV3ForwardIdentity(
    protocol_id="QAGENT-RANK-V3-CONCURRENCY",
    protocol_digest="a" * 64,
    model_version="ranking-v3-concurrency",
)
DATA_REVISION = "concurrency-data-revision"
SESSION_DATE = date(2026, 7, 27)
NOW = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
ATTESTOR = RankingV3Attestor(b"k" * 32)


def _initialize_database_process(database_url: str) -> tuple[tuple[str, ...], int, int]:
    engine = initialize_database(database_url)
    columns = tuple(
        sorted(
            column["name"] for column in inspect(engine).get_columns("ranking_v3_forward_ledgers")
        )
    )
    with engine.connect() as connection:
        trigger_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name LIKE 'trg_ranking_v3_forward_%'"
            )
        ).scalar_one()
        version = connection.execute(
            text(
                "SELECT version FROM qagent_schema_components "
                "WHERE component = 'ranking_v3_forward_triggers'"
            )
        ).scalar_one()
    return columns, trigger_count, version


def _repository(tmp_path, name: str):
    database_url = f"sqlite:///{tmp_path / f'{name}.db'}"
    initialize_database(database_url)
    return (
        RankingV3ForwardRepository(create_session_factory(database_url)),
        database_url,
    )


def _session() -> RankingV3ForwardSessionInput:
    return RankingV3ForwardSessionInput(
        session_date=SESSION_DATE,
        benchmark_id="CN:000300.IDX",
        benchmark_return_pct=Decimal("0.1"),
        portfolio_equity=Decimal("100"),
        stress_portfolio_equity=Decimal("99.9"),
        benchmark_equity=Decimal("100.1"),
        data_revision=DATA_REVISION,
    )


def _candidate() -> RankingV3ShadowCandidateInput:
    return RankingV3ShadowCandidateInput(
        candidate_id="candidate-concurrent",
        source_snapshot_id="snapshot-concurrent",
        session_date=SESSION_DATE,
        maturity_session_date=date(2026, 7, 28),
        instrument_id="CN:000001",
        strategy_id="ranking-v3",
        rank=1,
        score=Decimal("0.8"),
        benchmark_id="CN:000300.IDX",
        data_revision=DATA_REVISION,
        selection_digest="b" * 64,
    )


def _evidence(index: int) -> RankingV3ForwardGateEvidence:
    payload = {"index": index, "source": "authoritative"}
    return RankingV3ForwardGateEvidence(
        identity=IDENTITY,
        evidence_kind="historical_gates",
        evidence_digest=stable_digest(payload),
        data_revision=DATA_REVISION,
        passed=True,
        payload=payload,
        idempotency_key=f"evidence-{index}",
        recorded_at=NOW,
    )


def _proof(revision: int) -> RankingV3ForwardReleaseProof:
    unsigned = RankingV3ForwardReleaseProof(
        proof_digest="0" * 64,
        identity=IDENTITY,
        data_revision=DATA_REVISION,
        generated_at=NOW,
        ledger_revision=revision,
        ledger_evidence_digest="c" * 64,
        metrics=RankingV3ForwardMetrics(
            session_count=0,
            completed_trade_count=0,
            candidate_count=0,
            mature_candidate_count=0,
            valid_outcome_count=0,
            invalid_outcome_count=0,
            pending_mature_outcome_count=0,
            pending_candidate_count=0,
        ),
        gates=[],
        historical_gates_evidence_digest="d" * 64,
        pbo_evidence_digest="e" * 64,
        portfolio_evidence_digest="f" * 64,
        attestation=ATTESTOR.sign("ranking-v3-release-proof", "0" * 64),
    )
    proof_digest = stable_release_proof_digest(unsigned)
    return unsigned.model_copy(
        update={
            "proof_digest": proof_digest,
            "attestation": ATTESTOR.sign("ranking-v3-release-proof", proof_digest),
        }
    )


def test_concurrent_exact_replays_are_atomic_and_increment_once(tmp_path):
    repository, database_url = _repository(tmp_path, "exact-replays")

    with ThreadPoolExecutor(max_workers=16) as executor:
        ledgers = list(
            executor.map(
                lambda _: repository.ensure_ledger(IDENTITY, DATA_REVISION),
                range(32),
            )
        )
    assert {ledger.revision for ledger in ledgers} == {0}

    session_item = _session()
    session_digest = stable_digest(session_item)
    with ThreadPoolExecutor(max_workers=16) as executor:
        sessions = list(
            executor.map(
                lambda _: repository.record_session(
                    IDENTITY,
                    session_item,
                    idempotency_key="session-concurrent",
                    fact_digest=session_digest,
                ),
                range(32),
            )
        )
    assert len({item.fact_digest for item in sessions}) == 1

    candidate_item = _candidate()
    candidate_digest = stable_digest(candidate_item)
    with ThreadPoolExecutor(max_workers=16) as executor:
        candidates = list(
            executor.map(
                lambda _: repository.record_candidate(
                    IDENTITY,
                    candidate_item,
                    idempotency_key="candidate-concurrent",
                    fact_digest=candidate_digest,
                ),
                range(32),
            )
        )
    assert len({item.fact_digest for item in candidates}) == 1

    snapshot = repository.load_snapshot(IDENTITY)
    assert snapshot is not None
    assert len(snapshot.sessions) == 1
    assert len(snapshot.candidates) == 1
    assert snapshot.ledger.revision == 2

    with create_db_engine(database_url).connect() as connection:
        assert (
            connection.execute(text("SELECT COUNT(*) FROM ranking_v3_forward_ledgers")).scalar_one()
            == 1
        )


def test_cross_process_database_initialization_serializes_schema_migrations(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'cross-process-migration.db'}"
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE market_bar_cache ("
                "provider_mode VARCHAR(32) NOT NULL, "
                "instrument_id VARCHAR(32) NOT NULL, "
                "trade_date DATE NOT NULL, "
                "source_provider VARCHAR(64) NOT NULL DEFAULT '', "
                "open NUMERIC(18, 6) NOT NULL, "
                "high NUMERIC(18, 6) NOT NULL, "
                "low NUMERIC(18, 6) NOT NULL, "
                "close NUMERIC(18, 6) NOT NULL, "
                "volume NUMERIC(24, 4) NOT NULL, "
                "cached_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL, "
                "PRIMARY KEY (provider_mode, instrument_id, trade_date)"
                ")"
            )
        )

    with ProcessPoolExecutor(
        max_workers=4,
        mp_context=get_context("spawn"),
    ) as executor:
        results = list(
            executor.map(
                _initialize_database_process,
                [database_url] * 8,
                chunksize=1,
            )
        )

    assert len(set(results)) == 1
    columns, trigger_count, version = results[0]
    assert {"integrity_status", "quarantine_reason"}.issubset(columns)
    assert trigger_count >= 17
    assert version == 2


def test_concurrent_distinct_evidence_gets_gapless_sequences(tmp_path):
    repository, _database_url = _repository(tmp_path, "evidence-sequences")
    repository.ensure_ledger(IDENTITY, DATA_REVISION)
    items = [_evidence(index) for index in range(24)]

    with ThreadPoolExecutor(max_workers=16) as executor:
        persisted = list(executor.map(repository.record_evidence, items))

    assert sorted(item.sequence for item in persisted) == list(range(1, 25))
    snapshot = repository.load_snapshot(IDENTITY)
    assert snapshot is not None
    assert snapshot.ledger.revision == 24
    assert len(snapshot.evidence) == 24

    with ThreadPoolExecutor(max_workers=16) as executor:
        replayed = list(executor.map(repository.record_evidence, items))
    assert sorted(item.sequence for item in replayed) == list(range(1, 25))
    assert repository.load_snapshot(IDENTITY).ledger.revision == 24


def test_concurrent_conflicting_replays_raise_domain_conflict_not_integrity_error(
    tmp_path,
):
    repository, _database_url = _repository(tmp_path, "conflicting-replays")
    repository.ensure_ledger(IDENTITY, DATA_REVISION)
    first = _session()
    second = first.model_copy(update={"benchmark_return_pct": Decimal("0.2")})

    def append(item):
        try:
            repository.record_session(
                IDENTITY,
                item,
                idempotency_key="shared-session-key",
                fact_digest=stable_digest(item),
            )
        except RankingV3ForwardConflictError:
            return "conflict"
        return "persisted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, (first, second)))

    assert sorted(results) == ["conflict", "persisted"]
    snapshot = repository.load_snapshot(IDENTITY)
    assert snapshot is not None
    assert len(snapshot.sessions) == 1
    assert snapshot.ledger.revision == 1


def test_concurrent_release_proof_approval_is_idempotent_cas(tmp_path):
    repository, database_url = _repository(tmp_path, "approval-cas")
    repository.ensure_ledger(IDENTITY, DATA_REVISION)
    proof = _proof(0)

    with ThreadPoolExecutor(max_workers=8) as executor:
        persisted = list(
            executor.map(
                lambda _: repository.approve(proof, expected_revision=0),
                range(16),
            )
        )

    assert {item.proof_digest for item in persisted} == {proof.proof_digest}
    snapshot = repository.load_snapshot(IDENTITY)
    assert snapshot is not None
    assert snapshot.ledger.status == "approved"
    assert snapshot.ledger.revision == 1
    assert snapshot.release_proof == proof
    with create_db_engine(database_url).connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM ranking_v3_forward_release_proofs")
            ).scalar_one()
            == 1
        )


def test_competing_terminal_decisions_use_expected_revision_cas(tmp_path):
    repository, _database_url = _repository(tmp_path, "terminal-cas")
    repository.ensure_ledger(IDENTITY, DATA_REVISION)

    def reject(reason: str):
        try:
            repository.reject(IDENTITY, [reason], expected_revision=0)
        except RankingV3ForwardConflictError:
            return "conflict"
        return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reject, ("gate-a", "gate-b")))

    assert sorted(results) == ["conflict", "rejected"]
    snapshot = repository.load_snapshot(IDENTITY)
    assert snapshot is not None
    assert snapshot.ledger.status == "rejected"
    assert snapshot.ledger.revision == 1
    assert snapshot.ledger.rejection_reasons in (["gate-a"], ["gate-b"])


def test_expected_revision_and_database_state_machine_fail_closed(tmp_path):
    repository, database_url = _repository(tmp_path, "state-machine")
    repository.ensure_ledger(IDENTITY, DATA_REVISION)
    engine = create_db_engine(database_url)

    with pytest.raises(DBAPIError, match="revision must increment by one"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ranking_v3_forward_ledgers "
                    "SET revision = revision + 2 "
                    "WHERE protocol_id = :protocol_id"
                ),
                {"protocol_id": IDENTITY.protocol_id},
            )
    with pytest.raises(DBAPIError, match="transition is invalid"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ranking_v3_forward_ledgers "
                    "SET status = 'approved', revision = revision + 1 "
                    "WHERE protocol_id = :protocol_id"
                ),
                {"protocol_id": IDENTITY.protocol_id},
            )
    with pytest.raises(RankingV3ForwardConflictError, match="changed"):
        repository.reject(IDENTITY, ["stale"], expected_revision=1)

    rejected = repository.reject(IDENTITY, ["failed release gates"], expected_revision=0)
    assert rejected.status == "rejected"
    assert rejected.revision == 1

    with pytest.raises(DBAPIError, match="terminal ledger is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ranking_v3_forward_ledgers "
                    "SET revision = revision + 1 "
                    "WHERE protocol_id = :protocol_id"
                ),
                {"protocol_id": IDENTITY.protocol_id},
            )
    with pytest.raises(DBAPIError, match="cannot be deleted"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM ranking_v3_forward_ledgers WHERE protocol_id = :protocol_id"),
                {"protocol_id": IDENTITY.protocol_id},
            )
