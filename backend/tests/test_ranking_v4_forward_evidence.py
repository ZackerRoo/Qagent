from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from qagent.app import create_app
from qagent.backtesting.ranking_v4_forward_evidence import (
    RankingV4EvidenceConflictError,
    RankingV4EvidenceIntegrityError,
    RankingV4EvidenceStateError,
    RankingV4ProspectiveModelReturn,
    build_attempt_inventory_snapshot,
    build_common_date_return_record,
    build_prospective_definition,
)
from qagent.backtesting.ranking_v4_pbo import RankingV4DatedModelReturn
from qagent.backtesting.ranking_v4_validation import build_ranking_v4_trial_ledger
from qagent.db import create_db_engine, create_session_factory, initialize_database
from qagent.security.ranking_v4_attestation import RankingV4EvidenceAttestor
from qagent.storage.ranking_v4_forward_evidence import RankingV4EvidenceRepository


ATTESTOR = RankingV4EvidenceAttestor(b"k" * 32)
FROZEN_AT = datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc)
START = date(2026, 7, 31)
CODE_REVISION = "a" * 40


def _repository(tmp_path, name: str = "ranking-v4-evidence"):
    database_url = f"sqlite:///{tmp_path / f'{name}.db'}"
    initialize_database(database_url)
    return (
        RankingV4EvidenceRepository(
            create_session_factory(database_url),
            attestor=ATTESTOR,
        ),
        database_url,
    )


def _definition(epoch_id: str = "ranking-v45-forward-20260730"):
    return build_prospective_definition(
        epoch_id=epoch_id,
        code_revision=CODE_REVISION,
        dataset_revision=8939,
        evidence_start_date=START,
        frozen_at=FROZEN_AT,
        attestor=ATTESTOR,
    )


def _inventory(definition, *, sequence: int = 1, previous: str | None = None):
    return build_attempt_inventory_snapshot(
        definition=definition,
        sequence=sequence,
        as_of_date=FROZEN_AT.date(),
        pre_epoch_unverifiable_attempt_ids=(
            "walk-forward-legacy-a",
            "walk-forward-legacy-b",
        ),
        prospective_attempts={
            definition.identity.epoch_id: definition.definition_digest,
        },
        previous_inventory_digest=previous,
        recorded_at=FROZEN_AT,
        attestor=ATTESTOR,
    )


def _model_returns(definition, suffix: str = "1"):
    return tuple(
        RankingV4ProspectiveModelReturn(
            model_id=model_id,
            net_return_pct=Decimal("0.1"),
            stress_net_return_pct=Decimal("0.05"),
            source_snapshot_digest=(f"{index:x}" * 64)[:64],
        )
        for index, model_id in enumerate(definition.registered_model_ids, start=1)
    )


def _return_record(
    definition,
    *,
    sequence: int = 1,
    rebalance_date: date = START,
    previous: str | None = None,
):
    return build_common_date_return_record(
        definition=definition,
        sequence=sequence,
        rebalance_date=rebalance_date,
        dataset_revision=definition.identity.dataset_revision,
        source_result_digest="v2:test-result",
        model_returns=_model_returns(definition),
        previous_record_digest=previous,
        recorded_at=FROZEN_AT,
        attestor=ATTESTOR,
    )


def _trial_ledger(definition, dates: tuple[date, ...] = (START,)):
    return build_ranking_v4_trial_ledger(
        {
            model_id: tuple(
                RankingV4DatedModelReturn(
                    rebalance_date=rebalance_date,
                    net_return=float(index) / 100,
                )
                for rebalance_date in dates
            )
            for index, model_id in enumerate(
                definition.registered_model_ids,
                start=1,
            )
        },
        experiment_registry_digest=(
            definition.identity.experiment_registry_digest
        ),
        known_research_attempt_ids=(
            "walk-forward-legacy-a",
            "walk-forward-legacy-b",
        ),
    )


def test_definition_freeze_inventory_returns_and_proof_are_append_only(tmp_path):
    repository, _database_url = _repository(tmp_path)
    definition = repository.freeze_definition(_definition())
    inventory = repository.append_inventory(_inventory(definition))
    first = repository.append_return_record(_return_record(definition))
    second = repository.append_return_record(
        _return_record(
            definition,
            sequence=2,
            rebalance_date=date(2026, 8, 14),
            previous=first.record_digest,
        )
    )
    proof = repository.create_proof(
        definition.identity.epoch_id,
        generated_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    snapshot = repository.load_snapshot(definition.identity.epoch_id)
    assert snapshot is not None
    assert snapshot.definition == definition
    assert snapshot.inventories == (inventory,)
    assert snapshot.return_records == (first, second)
    assert snapshot.proofs == (proof,)
    assert proof.inventory_digest == inventory.inventory_digest
    assert proof.return_record_count == 2
    assert proof.official_release_allowed is False
    assert proof.release_scope == "shadow_only"

    assert repository.freeze_definition(definition) == definition
    assert repository.append_inventory(inventory) == inventory
    assert repository.append_return_record(first) == first
    assert repository.append_proof(proof) == proof
    assert repository.create_proof(
        definition.identity.epoch_id,
        generated_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
    ) == proof


def test_old_proof_remains_valid_after_inventory_extension(tmp_path):
    repository, _database_url = _repository(tmp_path, "proof-prefix")
    definition = repository.freeze_definition(_definition())
    first_inventory = repository.append_inventory(_inventory(definition))
    first_proof = repository.create_proof(
        definition.identity.epoch_id,
        generated_at=datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc),
    )
    second_inventory = repository.append_inventory(
        build_attempt_inventory_snapshot(
            definition=definition,
            sequence=2,
            as_of_date=FROZEN_AT.date(),
            pre_epoch_unverifiable_attempt_ids=(
                "walk-forward-legacy-a",
                "walk-forward-legacy-b",
                "walk-forward-legacy-c",
            ),
            prospective_attempts={
                definition.identity.epoch_id: definition.definition_digest,
                "ranking-v45-forward-secondary": definition.definition_digest,
            },
            previous_inventory_digest=first_inventory.inventory_digest,
            recorded_at=datetime(2026, 7, 30, 3, 5, tzinfo=timezone.utc),
            attestor=ATTESTOR,
        )
    )
    second_proof = repository.create_proof(
        definition.identity.epoch_id,
        generated_at=datetime(2026, 7, 30, 3, 10, tzinfo=timezone.utc),
    )

    snapshot = repository.load_snapshot(definition.identity.epoch_id)
    assert snapshot is not None
    assert snapshot.inventories == (first_inventory, second_inventory)
    assert snapshot.proofs == (first_proof, second_proof)
    assert second_proof.inventory_digest == second_inventory.inventory_digest
    assert first_proof.return_record_count == second_proof.return_record_count == 0


def test_evidence_start_uses_a_share_market_date():
    with pytest.raises(ValueError, match="strictly after"):
        build_prospective_definition(
            epoch_id="ranking-v45-forward-market-date",
            code_revision=CODE_REVISION,
            dataset_revision=8939,
            evidence_start_date=date(2026, 7, 31),
            frozen_at=datetime(2026, 7, 30, 17, 0, tzinfo=timezone.utc),
            attestor=ATTESTOR,
        )


def test_registered_trial_ledger_appends_only_new_common_dates(tmp_path):
    repository, _database_url = _repository(tmp_path, "trial-ledger")
    definition = repository.freeze_definition(_definition())
    repository.append_inventory(_inventory(definition))
    first_ledger = _trial_ledger(definition)
    first_proof = repository.append_trial_ledger(
        definition.identity.epoch_id,
        attempt_id=definition.identity.epoch_id,
        code_revision=definition.identity.code_revision,
        protocol_digest=definition.identity.protocol_digest,
        experiment_registry_digest=(
            definition.identity.experiment_registry_digest
        ),
        dataset_revision=definition.identity.dataset_revision,
        execution_start_date=START,
        source_result_digest="v2:first-result",
        trial_ledger=first_ledger,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    second_ledger = _trial_ledger(
        definition,
        dates=(START, date(2026, 8, 14)),
    )
    second_proof = repository.append_trial_ledger(
        definition.identity.epoch_id,
        attempt_id=definition.identity.epoch_id,
        code_revision=definition.identity.code_revision,
        protocol_digest=definition.identity.protocol_digest,
        experiment_registry_digest=(
            definition.identity.experiment_registry_digest
        ),
        dataset_revision=definition.identity.dataset_revision + 1,
        execution_start_date=START,
        source_result_digest="v2:second-result",
        trial_ledger=second_ledger,
        recorded_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    snapshot = repository.load_snapshot(definition.identity.epoch_id)
    assert snapshot is not None
    assert tuple(item.rebalance_date for item in snapshot.return_records) == (
        START,
        date(2026, 8, 14),
    )
    assert tuple(item.dataset_revision for item in snapshot.return_records) == (
        definition.identity.dataset_revision,
        definition.identity.dataset_revision + 1,
    )
    assert all(
        tuple(item.model_id for item in record.model_returns)
        == definition.registered_model_ids
        for record in snapshot.return_records
    )
    assert first_proof.return_record_count == 1
    assert second_proof.return_record_count == 2
    assert second_proof.official_release_allowed is False


def test_trial_ledger_identity_and_attempt_inventory_fail_closed(tmp_path):
    repository, _database_url = _repository(tmp_path, "trial-ledger-identity")
    definition = repository.freeze_definition(_definition())
    repository.append_inventory(_inventory(definition))
    ledger = _trial_ledger(definition)
    common = {
        "attempt_id": definition.identity.epoch_id,
        "code_revision": definition.identity.code_revision,
        "protocol_digest": definition.identity.protocol_digest,
        "experiment_registry_digest": (
            definition.identity.experiment_registry_digest
        ),
        "dataset_revision": definition.identity.dataset_revision,
        "execution_start_date": START,
        "source_result_digest": "v2:result",
        "trial_ledger": ledger,
        "recorded_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
    }
    with pytest.raises(RankingV4EvidenceIntegrityError, match="not registered"):
        repository.append_trial_ledger(
            definition.identity.epoch_id,
            **{**common, "attempt_id": "unregistered-attempt"},
        )
    with pytest.raises(RankingV4EvidenceIntegrityError, match="predates"):
        repository.append_trial_ledger(
            definition.identity.epoch_id,
            **{**common, "dataset_revision": 8938},
        )


def test_definition_rejects_same_epoch_with_different_code_revision(tmp_path):
    repository, _database_url = _repository(tmp_path, "definition-conflict")
    first = _definition()
    repository.freeze_definition(first)
    conflicting = build_prospective_definition(
        epoch_id=first.identity.epoch_id,
        code_revision="b" * 40,
        dataset_revision=8939,
        evidence_start_date=START,
        frozen_at=FROZEN_AT,
        attestor=ATTESTOR,
    )
    with pytest.raises(RankingV4EvidenceConflictError):
        repository.freeze_definition(conflicting)


def test_return_record_requires_inventory_and_complete_frozen_model_family(tmp_path):
    repository, _database_url = _repository(tmp_path, "complete-family")
    definition = repository.freeze_definition(_definition())
    record = _return_record(definition)
    with pytest.raises(RankingV4EvidenceStateError):
        repository.append_return_record(record)

    repository.append_inventory(_inventory(definition))
    with pytest.raises(RankingV4EvidenceIntegrityError):
        build_common_date_return_record(
            definition=definition,
            sequence=1,
            rebalance_date=START,
            dataset_revision=definition.identity.dataset_revision,
            source_result_digest="v2:test-result",
            model_returns=_model_returns(definition)[:-1],
            previous_record_digest=None,
            recorded_at=FROZEN_AT,
            attestor=ATTESTOR,
        )


def test_pre_freeze_backfill_duplicate_date_and_chain_gap_fail_closed(tmp_path):
    repository, _database_url = _repository(tmp_path, "fail-closed")
    definition = repository.freeze_definition(_definition())
    repository.append_inventory(_inventory(definition))
    with pytest.raises(RankingV4EvidenceIntegrityError):
        build_common_date_return_record(
            definition=definition,
            sequence=1,
            rebalance_date=date(2025, 12, 26),
            dataset_revision=definition.identity.dataset_revision,
            source_result_digest="v2:test-result",
            model_returns=_model_returns(definition),
            previous_record_digest=None,
            recorded_at=FROZEN_AT,
            attestor=ATTESTOR,
        )

    first = repository.append_return_record(_return_record(definition))
    with pytest.raises(RankingV4EvidenceConflictError):
        repository.append_return_record(
            _return_record(
                definition,
                sequence=3,
                rebalance_date=date(2026, 8, 14),
                previous=first.record_digest,
            )
        )
    with pytest.raises(RankingV4EvidenceConflictError):
        repository.append_return_record(
            _return_record(
                definition,
                sequence=2,
                rebalance_date=START,
                previous=first.record_digest,
            )
        )


def test_inventory_cannot_shrink_or_redefine_attempts(tmp_path):
    repository, _database_url = _repository(tmp_path, "inventory-extension")
    definition = repository.freeze_definition(_definition())
    first = repository.append_inventory(_inventory(definition))
    changed = build_attempt_inventory_snapshot(
        definition=definition,
        sequence=2,
        as_of_date=date(2026, 8, 1),
        pre_epoch_unverifiable_attempt_ids=("walk-forward-legacy-a",),
        prospective_attempts={
            definition.identity.epoch_id: "b" * 64,
        },
        previous_inventory_digest=first.inventory_digest,
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        attestor=ATTESTOR,
    )
    with pytest.raises(RankingV4EvidenceConflictError):
        repository.append_inventory(changed)


def test_wrong_attestation_key_and_tampered_payload_fail_closed(tmp_path):
    repository, _database_url = _repository(tmp_path, "attestation")
    definition = _definition()
    wrong = RankingV4EvidenceRepository(
        repository.session_factory,
        attestor=RankingV4EvidenceAttestor(b"x" * 32),
    )
    with pytest.raises(RankingV4EvidenceIntegrityError):
        wrong.freeze_definition(definition)

    repository.freeze_definition(definition)
    inventory = _inventory(definition)
    tampered = inventory.model_copy(
        update={
            "pre_epoch_unverifiable_attempt_ids": (
                *inventory.pre_epoch_unverifiable_attempt_ids,
                "walk-forward-injected",
            )
        }
    )
    with pytest.raises(RankingV4EvidenceIntegrityError):
        repository.append_inventory(tampered)


def test_sqlite_tables_and_immutable_triggers_reject_direct_mutation(tmp_path):
    repository, database_url = _repository(tmp_path, "sqlite-triggers")
    definition = repository.freeze_definition(_definition())
    inventory = repository.append_inventory(_inventory(definition))
    first_return = repository.append_return_record(_return_record(definition))
    repository.create_proof(
        definition.identity.epoch_id,
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    engine = create_db_engine(database_url)
    assert {
        "ranking_v4_evidence_definitions",
        "ranking_v4_evidence_inventories",
        "ranking_v4_evidence_returns",
        "ranking_v4_evidence_proofs",
    }.issubset(inspect(engine).get_table_names())
    with engine.connect() as connection:
        version = connection.execute(
            text(
                "SELECT version FROM qagent_schema_components "
                "WHERE component = 'ranking_v4_evidence_triggers'"
            )
        ).scalar_one()
        assert version == 2
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE ranking_v4_evidence_inventories "
                    "SET as_of_date = '2026-08-02' "
                    "WHERE inventory_digest = :digest"
                ),
                {"digest": inventory.inventory_digest},
            )
        connection.rollback()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO ranking_v4_evidence_returns ("
                    "record_digest, definition_digest, sequence, rebalance_date, "
                    "dataset_revision, previous_record_digest, model_count, "
                    "payload_json, attestation_json, recorded_at, created_at"
                    ") VALUES ("
                    ":record_digest, :definition_digest, 2, '2026-08-14', "
                    ":dataset_revision, :previous_record_digest, 1, "
                    "'{\"model_returns\":[{}]}', '{}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                    ")"
                ),
                {
                    "record_digest": "b" * 64,
                    "definition_digest": definition.definition_digest,
                    "dataset_revision": definition.identity.dataset_revision,
                    "previous_record_digest": first_return.record_digest,
                },
            )
        connection.rollback()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "DELETE FROM ranking_v4_evidence_definitions "
                    "WHERE epoch_id = :epoch_id"
                ),
                {"epoch_id": definition.identity.epoch_id},
            )


def test_evidence_status_api_exposes_only_signed_shadow_state(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'evidence-api.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setenv(
        "QAGENT_RANKING_V4_EVIDENCE_ATTESTATION_KEY",
        "k" * 32,
    )
    initialize_database(database_url)
    repository = RankingV4EvidenceRepository(create_session_factory(database_url))
    definition = repository.freeze_definition(_definition())
    inventory = repository.append_inventory(_inventory(definition))
    proof = repository.create_proof(
        definition.identity.epoch_id,
        generated_at=datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc),
    )

    client = TestClient(create_app())
    response = client.get(
        f"/api/ranking-v4/evidence/{definition.identity.epoch_id}"
    )
    missing = client.get("/api/ranking-v4/evidence/missing-epoch")

    assert response.status_code == 200
    assert response.json() == {
        "epoch_id": definition.identity.epoch_id,
        "status": "frozen",
        "code_revision": definition.identity.code_revision,
        "protocol_digest": definition.identity.protocol_digest,
        "experiment_registry_digest": (
            definition.identity.experiment_registry_digest
        ),
        "dataset_revision": definition.identity.dataset_revision,
        "base_dataset_revision": definition.identity.dataset_revision,
        "latest_dataset_revision": definition.identity.dataset_revision,
        "evidence_start_date": START.isoformat(),
        "definition_digest": definition.definition_digest,
        "inventory_count": 1,
        "latest_inventory_digest": inventory.inventory_digest,
        "common_date_count": 0,
        "latest_common_date": None,
        "proof_count": 1,
        "latest_proof_digest": proof.proof_digest,
        "release_scope": "shadow_only",
        "official_release_allowed": False,
    }
    assert missing.status_code == 404
