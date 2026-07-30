from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from qagent.backtesting.ranking_v4_forward_evidence import (
    RankingV4EvidenceConflictError,
    RankingV4EvidenceIntegrityError,
    RankingV4ProspectiveModelReturn,
    build_attempt_inventory_snapshot,
    build_common_date_return_record,
    build_prospective_definition,
)
from qagent.backtesting.ranking_v4_prospective_release import (
    build_prospective_execution_summary,
    build_prospective_release_policy,
)
from qagent.db import create_db_engine, create_session_factory, initialize_database
from qagent.security.ranking_v4_attestation import RankingV4EvidenceAttestor
from qagent.storage.ranking_v4_forward_evidence import RankingV4EvidenceRepository
from qagent.storage.ranking_v4_prospective_release import (
    RankingV4ProspectiveReleaseRepository,
)


ATTESTOR = RankingV4EvidenceAttestor(b"k" * 32)
FROZEN_AT = datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc)
START = date(2026, 7, 31)


def _repositories(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'ranking-v4-release.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    return (
        RankingV4EvidenceRepository(session_factory, attestor=ATTESTOR),
        RankingV4ProspectiveReleaseRepository(
            session_factory,
            attestor=ATTESTOR,
        ),
        database_url,
    )


def _definition():
    return build_prospective_definition(
        epoch_id="ranking-v45-forward-release-storage-test",
        code_revision="a" * 40,
        dataset_revision=8939,
        evidence_start_date=START,
        frozen_at=FROZEN_AT,
        attestor=ATTESTOR,
    )


def _freeze_with_first_return(evidence_repository, *, return_count: int = 1):
    definition = evidence_repository.freeze_definition(_definition())
    inventory = build_attempt_inventory_snapshot(
        definition=definition,
        sequence=1,
        as_of_date=FROZEN_AT.date(),
        pre_epoch_unverifiable_attempt_ids=("legacy-a",),
        prospective_attempts={
            definition.identity.epoch_id: definition.definition_digest,
        },
        previous_inventory_digest=None,
        recorded_at=FROZEN_AT,
        attestor=ATTESTOR,
    )
    evidence_repository.append_inventory(inventory)
    model_returns = tuple(
        RankingV4ProspectiveModelReturn(
            model_id=model_id,
            net_return_pct=Decimal("0.1"),
            stress_net_return_pct=Decimal("0.05"),
            source_snapshot_digest=f"{index:x}".rjust(64, "0"),
        )
        for index, model_id in enumerate(
            definition.registered_model_ids,
            start=1,
        )
    )
    previous_digest = None
    for index in range(return_count):
        rebalance_date = START + timedelta(days=14 * index)
        record = evidence_repository.append_return_record(
            build_common_date_return_record(
            definition=definition,
            sequence=index + 1,
            rebalance_date=rebalance_date,
            dataset_revision=8939,
            source_result_digest="v2:first-result",
            model_returns=model_returns,
            previous_record_digest=previous_digest,
            recorded_at=datetime.combine(
                rebalance_date + timedelta(days=30),
                datetime.min.time(),
                tzinfo=timezone.utc,
            ),
            attestor=ATTESTOR,
        )
        )
        previous_digest = record.record_digest
    return definition


def _policy(definition, *, registered_at=None):
    return build_prospective_release_policy(
        definition_digest=definition.definition_digest,
        model_protocol_digest=definition.identity.protocol_digest,
        experiment_registry_digest=(
            definition.identity.experiment_registry_digest
        ),
        registered_at=registered_at
        or datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc),
        attestor=ATTESTOR,
    )


def _summary(definition, policy, *, common_date_count=1):
    return build_prospective_execution_summary(
        definition_digest=definition.definition_digest,
        policy_digest=policy.policy_digest,
        sequence=1,
        source_result_digest="v2:first-result",
        dataset_revision=8939,
        execution_start_date=START,
        execution_end_date=date(2026, 8, 31),
        latest_mature_rebalance_date=START,
        common_date_count=common_date_count,
        completed_trade_count=1,
        valid_outcome_count=8,
        expected_outcome_count=8,
        maximum_drawdown_pct=Decimal("-0.25"),
        previous_summary_digest=None,
        recorded_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        attestor=ATTESTOR,
    )


def test_policy_and_execution_summary_are_signed_append_only_records(tmp_path):
    evidence_repository, release_repository, _database_url = _repositories(tmp_path)
    definition = _freeze_with_first_return(evidence_repository)
    policy = release_repository.register_policy(_policy(definition))
    summary = release_repository.append_execution_summary(
        _summary(definition, policy)
    )

    assert release_repository.load_policy(definition.definition_digest) == policy
    assert release_repository.load_execution_summaries(
        definition.definition_digest
    ) == (summary,)
    assert release_repository.register_policy(policy) == policy
    assert release_repository.append_execution_summary(summary) == summary


def test_release_policy_is_unique_per_frozen_definition(tmp_path):
    evidence_repository, release_repository, _database_url = _repositories(tmp_path)
    definition = _freeze_with_first_return(evidence_repository)
    release_repository.register_policy(_policy(definition))

    with pytest.raises(RankingV4EvidenceConflictError, match="already bound"):
        release_repository.register_policy(
            _policy(
                definition,
                registered_at=datetime(2026, 7, 30, 3, 1, tzinfo=timezone.utc),
            )
        )


def test_summary_must_match_complete_return_chain(tmp_path):
    evidence_repository, release_repository, _database_url = _repositories(tmp_path)
    definition = _freeze_with_first_return(evidence_repository)
    policy = release_repository.register_policy(_policy(definition))

    with pytest.raises(RankingV4EvidenceIntegrityError, match="evidence chain"):
        release_repository.append_execution_summary(
            _summary(definition, policy, common_date_count=2)
        )


def test_sqlite_rejects_mutation_of_release_records(tmp_path):
    evidence_repository, release_repository, database_url = _repositories(tmp_path)
    definition = _freeze_with_first_return(evidence_repository)
    policy = release_repository.register_policy(_policy(definition))
    summary = release_repository.append_execution_summary(
        _summary(definition, policy)
    )
    engine = create_db_engine(database_url)

    with engine.begin() as connection:
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                text(
                    "UPDATE ranking_v4_prospective_execution_summaries "
                    "SET completed_trade_count = 99 "
                    "WHERE summary_digest = :digest"
                ),
                {"digest": summary.summary_digest},
            )
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                text(
                    "DELETE FROM ranking_v4_prospective_release_policies "
                    "WHERE policy_digest = :digest"
                ),
                {"digest": policy.policy_digest},
            )


def test_repository_recomputes_and_persists_checkpoint_release_proof(tmp_path):
    evidence_repository, release_repository, database_url = _repositories(tmp_path)
    definition = _freeze_with_first_return(
        evidence_repository,
        return_count=80,
    )
    latest_date = START + timedelta(days=14 * 79)
    evaluated_at = datetime.combine(
        latest_date + timedelta(days=31),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    evidence_repository.create_proof(
        definition.identity.epoch_id,
        generated_at=evaluated_at,
    )
    policy = release_repository.register_policy(_policy(definition))
    release_repository.append_execution_summary(
        build_prospective_execution_summary(
            definition_digest=definition.definition_digest,
            policy_digest=policy.policy_digest,
            sequence=1,
            source_result_digest="v2:first-result",
            dataset_revision=8939,
            execution_start_date=START,
            execution_end_date=latest_date + timedelta(days=30),
            latest_mature_rebalance_date=latest_date,
            common_date_count=80,
            completed_trade_count=1,
            valid_outcome_count=8,
            expected_outcome_count=8,
            maximum_drawdown_pct=Decimal("-0.25"),
            previous_summary_digest=None,
            recorded_at=evaluated_at,
            attestor=ATTESTOR,
        )
    )

    proof = release_repository.evaluate_checkpoint(
        definition.identity.epoch_id,
        evaluated_at=evaluated_at,
    )

    assert proof.evaluation_status == "continue_collecting"
    assert proof.release_scope == "shadow_only"
    assert proof.official_release_allowed is False
    assert release_repository.load_release_proofs(
        definition.definition_digest
    ) == (proof,)

    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                text(
                    "UPDATE ranking_v4_prospective_release_proofs "
                    "SET official_release_allowed = 1 "
                    "WHERE release_proof_digest = :digest"
                ),
                {"digest": proof.release_proof_digest},
            )
