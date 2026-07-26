from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from qagent.backtesting.ranking_v3_forward import RankingV3ForwardIdentity, stable_digest
from qagent.backtesting.ranking_v3_production import (
    InMemoryRankingV3ProductionStore,
    PRODUCTION_BATCH_ATTESTATION_KIND,
    RankingV3ProductionAuthorizationError,
    RankingV3ProductionBatch,
    RankingV3ProductionBatchInput,
    RankingV3ProductionConflictError,
    RankingV3ProductionIdentity,
    RankingV3ProductionIntegrityError,
    RankingV3ProductionReleaseValidation,
    RankingV3ProductionSelectionValidation,
    RankingV3ProductionSelectionItem,
    RankingV3ProductionSelectionService,
    production_batch_fact_digest,
    production_identity_digest,
    production_selection_batch_digest,
    production_selection_item_digest,
)
from qagent.security.ranking_v3_attestation import RankingV3Attestor


PROOF_DIGEST = "a" * 64
DATA_REVISION = "ranking-v3-data-revision-2026-07-26"
VALIDATION_RUN_ID = "ranking-v3-validation-run-42"
APPROVED_AT = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)
RECORDED_AT = datetime(2026, 7, 29, 8, 30, tzinfo=timezone.utc)
ATTESTATION_KEY = b"p" * 32
WRONG_ATTESTATION_KEY = b"w" * 32


class _ReleaseAuthority:
    def __init__(
        self,
        identity: RankingV3ProductionIdentity,
        *,
        valid: bool = True,
        current: bool = True,
        status: str = "approved",
        approved_at: datetime = APPROVED_AT,
    ):
        self.identity = identity
        self.valid = valid
        self.current = current
        self.status = status
        self.approved_at = approved_at
        self.calls = 0

    def validate_current_release(self, identity):
        self.calls += 1
        if not self.valid:
            return RankingV3ProductionReleaseValidation(
                valid=False,
                current=self.current,
                status=self.status,
                reason="release proof is not approved",
            )
        return RankingV3ProductionReleaseValidation(
            valid=True,
            current=True,
            status="approved",
            reason="current authoritative release",
            release_proof_digest=self.identity.release_proof_digest,
            validation_run_id=self.identity.validation_run_id,
            data_revision=self.identity.data_revision,
            protocol_identity=self.identity.protocol_identity,
            approved_at=self.approved_at,
        )


class _SelectionAuthority:
    def __init__(self, *, authorized: bool = True):
        self.authorized = authorized
        self.calls = 0

    def validate_selection(self, identity, item):
        self.calls += 1
        if not self.authorized:
            return RankingV3ProductionSelectionValidation(
                authorized=False,
                reason="selection was rejected by the authoritative verifier",
            )
        return RankingV3ProductionSelectionValidation(
            authorized=True,
            reason="exact identity and batch input verified",
            identity_digest=identity.identity_digest,
            selection_batch_digest=item.selection_batch_digest,
        )


def _identity() -> RankingV3ProductionIdentity:
    return RankingV3ProductionIdentity.create(
        release_proof_digest=PROOF_DIGEST,
        validation_run_id=VALIDATION_RUN_ID,
        data_revision=DATA_REVISION,
        protocol_identity=RankingV3ForwardIdentity(
            protocol_id="QAGENT-RANK-V3.2-20260726",
            protocol_digest="b" * 64,
            model_version="point-in-time-net-excess-v3.2",
        ),
    )


def _selection(
    rank: int,
    *,
    instrument_id: str | None = None,
    source_snapshot_id: str | None = None,
) -> RankingV3ProductionSelectionItem:
    return RankingV3ProductionSelectionItem.create(
        candidate_id=f"candidate-{rank}",
        instrument_id=instrument_id or f"CN:{rank:06d}",
        source_snapshot_id=source_snapshot_id or f"snapshot-{rank}",
        strategy_id="ranking-v3",
        rank=rank,
        score=Decimal("0.9") - Decimal(rank) / Decimal("100"),
    )


def _batch(
    *,
    session_date: date = date(2026, 7, 29),
    candidate_snapshot_digest: str = "c" * 64,
    selections: tuple[RankingV3ProductionSelectionItem, ...] | None = None,
) -> RankingV3ProductionBatchInput:
    return RankingV3ProductionBatchInput.create(
        session_date=session_date,
        candidate_snapshot_digest=candidate_snapshot_digest,
        selections=selections or (_selection(1), _selection(2)),
    )


def _service(
    identity: RankingV3ProductionIdentity,
    *,
    authority: _ReleaseAuthority | None = None,
    selection_authority: _SelectionAuthority | None = None,
):
    release_authority = authority or _ReleaseAuthority(identity)
    attestor = RankingV3Attestor(ATTESTATION_KEY)
    store = InMemoryRankingV3ProductionStore(attestor=attestor)
    return (
        RankingV3ProductionSelectionService(
            store,
            release_authority,
            selection_authority=selection_authority or _SelectionAuthority(),
            attestor=attestor,
            now=lambda: RECORDED_AT,
        ),
        store,
        release_authority,
    )


def test_frozen_identity_binds_release_validation_data_and_protocol_model():
    identity = _identity()

    assert identity.release_proof_digest == PROOF_DIGEST
    assert identity.validation_run_id == VALIDATION_RUN_ID
    assert identity.data_revision == DATA_REVISION
    assert identity.protocol_identity.protocol_id == "QAGENT-RANK-V3.2-20260726"
    assert identity.protocol_identity.protocol_digest == "b" * 64
    assert identity.protocol_identity.model_version == "point-in-time-net-excess-v3.2"
    assert identity.identity_digest == production_identity_digest(identity)

    payload = identity.model_dump(mode="python")
    payload["validation_run_id"] = "tampered-run"
    with pytest.raises(ValidationError, match="production identity digest is invalid"):
        RankingV3ProductionIdentity.model_validate(payload)


def test_post_approval_batch_accepts_fresh_signal_snapshots():
    identity = _identity()
    service, store, authority = _service(identity)
    item = _batch(
        selections=(
            _selection(
                1,
                instrument_id="CN:688981",
                source_snapshot_id="fresh-production-snapshot-688981",
            ),
            _selection(
                2,
                instrument_id="CN:300750",
                source_snapshot_id="fresh-production-snapshot-300750",
            ),
        )
    )

    recorded = service.record_batch(identity, item, idempotency_key="production-20260729")

    assert recorded.session_date == date(2026, 7, 29)
    assert recorded.selected_count == 2
    assert recorded.selections[0].source_snapshot_id.startswith("fresh-production")
    assert recorded.fact_digest == production_batch_fact_digest(identity, item)
    assert recorded.attestation.kind == PRODUCTION_BATCH_ATTESTATION_KIND
    assert recorded.attestation.payload_digest == recorded.fact_digest
    assert RankingV3Attestor(ATTESTATION_KEY).verify(
        recorded.attestation,
        expected_kind=PRODUCTION_BATCH_ATTESTATION_KIND,
        expected_payload_digest=recorded.fact_digest,
    )
    assert recorded.recorded_at == RECORDED_AT
    assert store.get_batch_for_session(identity, date(2026, 7, 29)) == recorded
    assert authority.calls == 1


@pytest.mark.parametrize(
    ("valid", "current", "status"),
    [
        (False, False, "pending"),
        (False, True, "pending"),
        (False, False, "rejected"),
        (False, False, "missing"),
    ],
)
def test_pre_approval_or_noncurrent_release_is_blocked(valid, current, status):
    identity = _identity()
    authority = _ReleaseAuthority(
        identity,
        valid=valid,
        current=current,
        status=status,
    )
    service, store, _ = _service(identity, authority=authority)

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="release proof is not approved",
    ):
        service.record_batch(identity, _batch(), idempotency_key="not-approved")

    assert store.get_batch_for_session(identity, date(2026, 7, 29)) is None


def test_authoritative_release_must_match_every_frozen_identity_field():
    identity = _identity()
    other_identity = RankingV3ProductionIdentity.create(
        release_proof_digest="d" * 64,
        validation_run_id=VALIDATION_RUN_ID,
        data_revision=DATA_REVISION,
        protocol_identity=identity.protocol_identity,
    )
    service, _, _ = _service(
        identity,
        authority=_ReleaseAuthority(other_identity),
    )

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="do not match the frozen production identity",
    ):
        service.record_batch(identity, _batch(), idempotency_key="wrong-release")


def test_pre_release_signal_session_is_blocked():
    identity = _identity()
    service, store, _ = _service(identity)

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="predates the authoritative release",
    ):
        service.record_batch(
            identity,
            _batch(session_date=date(2026, 7, 27)),
            idempotency_key="pre-release",
        )

    assert store.get_batch_for_session(identity, date(2026, 7, 27)) is None


def test_exact_replay_returns_original_record():
    identity = _identity()
    service, store, authority = _service(identity)
    item = _batch()

    first = service.record_batch(identity, item, idempotency_key="same")
    repeated = service.record_batch(identity, item, idempotency_key="same")
    same_facts_new_key = service.record_batch(identity, item, idempotency_key="alias")

    assert repeated is first
    assert same_facts_new_key is first
    assert repeated.attestation == first.attestation
    assert same_facts_new_key.attestation == first.attestation
    assert store.get_batch_by_idempotency_key(identity, "same") is first
    assert store.get_batch_by_idempotency_key(identity, "alias") is first
    assert authority.calls == 3

    with pytest.raises(
        RankingV3ProductionConflictError,
        match="idempotency key is already bound",
    ):
        service.record_batch(
            identity,
            _batch(session_date=date(2026, 7, 30)),
            idempotency_key="alias",
        )


def test_exact_replay_is_reauthorized_and_blocks_after_release_revocation():
    identity = _identity()
    authority = _ReleaseAuthority(identity)
    service, _, _ = _service(identity, authority=authority)
    item = _batch()
    service.record_batch(identity, item, idempotency_key="before-revocation")
    authority.valid = False
    authority.current = False
    authority.status = "rejected"

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="release proof is not approved",
    ):
        service.record_batch(identity, item, idempotency_key="before-revocation")


def test_same_day_changed_candidate_pool_conflicts():
    identity = _identity()
    service, _, _ = _service(identity)
    service.record_batch(identity, _batch(), idempotency_key="first-pool")

    changed = _batch(
        candidate_snapshot_digest="d" * 64,
        selections=(_selection(1),),
    )
    with pytest.raises(
        RankingV3ProductionConflictError,
        match="session already has a different",
    ):
        service.record_batch(identity, changed, idempotency_key="changed-pool")


def test_same_idempotency_key_with_different_facts_conflicts():
    identity = _identity()
    service, _, _ = _service(identity)
    service.record_batch(identity, _batch(), idempotency_key="shared-key")

    with pytest.raises(
        RankingV3ProductionConflictError,
        match="idempotency key is already bound",
    ):
        service.record_batch(
            identity,
            _batch(session_date=date(2026, 7, 30)),
            idempotency_key="shared-key",
        )


def test_rank_instrument_and_source_snapshot_must_be_unique():
    first = _selection(1, instrument_id="CN:688981", source_snapshot_id="snapshot-a")

    with pytest.raises(ValidationError, match="rank values must be unique"):
        _batch(
            selections=(
                first,
                RankingV3ProductionSelectionItem.create(
                    candidate_id="other",
                    instrument_id="CN:300750",
                    source_snapshot_id="snapshot-b",
                    strategy_id="ranking-v3",
                    rank=1,
                    score=Decimal("0.88"),
                ),
            )
        )

    with pytest.raises(ValidationError, match="instrument values must be unique"):
        _batch(
            selections=(
                first,
                _selection(2, instrument_id="CN:688981", source_snapshot_id="snapshot-b"),
            )
        )

    with pytest.raises(ValidationError, match="source snapshot values must be unique"):
        _batch(
            selections=(
                first,
                _selection(2, instrument_id="CN:300750", source_snapshot_id="snapshot-a"),
            )
        )


def test_selection_count_and_contiguous_order_are_strict():
    first = _selection(1)
    third = _selection(3)
    raw = {
        "session_date": date(2026, 7, 29),
        "candidate_snapshot_digest": "c" * 64,
        "selection_batch_digest": "d" * 64,
        "selected_count": 2,
        "selections": (first, third),
    }
    with pytest.raises(ValidationError, match="complete and ordered"):
        RankingV3ProductionBatchInput.model_validate(raw)

    valid = _batch()
    payload = valid.model_dump(mode="python")
    payload["selected_count"] = 3
    with pytest.raises(ValidationError, match="selected_count must equal"):
        RankingV3ProductionBatchInput.model_validate(payload)


def test_every_owned_digest_is_canonical_and_tamper_evident():
    identity = _identity()
    selection = _selection(1)
    item = _batch(selections=(selection,))
    service, _, _ = _service(identity)
    persisted = service.record_batch(identity, item, idempotency_key="digest-test")

    assert selection.item_digest == production_selection_item_digest(selection)
    assert item.selection_batch_digest == production_selection_batch_digest(item)
    assert persisted.fact_digest == production_batch_fact_digest(identity, persisted)

    selection_payload = selection.model_dump(mode="python")
    selection_payload["score"] = Decimal("0.01")
    with pytest.raises(ValidationError, match="selection item digest is invalid"):
        RankingV3ProductionSelectionItem.model_validate(selection_payload)

    batch_payload = item.model_dump(mode="python")
    batch_payload["candidate_snapshot_digest"] = stable_digest({"tampered": True})
    with pytest.raises(ValidationError, match="selection batch digest is invalid"):
        RankingV3ProductionBatchInput.model_validate(batch_payload)

    persisted_payload = persisted.model_dump(mode="python")
    persisted_payload["fact_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="batch fact digest is invalid"):
        RankingV3ProductionBatch.model_validate(persisted_payload)


def test_fact_digest_excludes_attestation_but_forged_signature_is_rejected():
    identity = _identity()
    item = _batch()
    service, store, _ = _service(identity)
    persisted = service.record_batch(identity, item, idempotency_key="signed")

    forged_attestation = persisted.attestation.model_copy(update={"signature": "f" * 64})
    forged = RankingV3ProductionBatch.model_validate(
        {
            **persisted.model_dump(mode="python", exclude={"attestation"}),
            "attestation": forged_attestation,
        }
    )

    assert production_batch_fact_digest(identity, forged) == persisted.fact_digest
    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="attestation is invalid",
    ):
        store.append_batch(forged)


def test_forged_recomputed_fact_digest_without_server_key_is_rejected():
    identity = _identity()
    service, store, _ = _service(identity)
    persisted = service.record_batch(identity, _batch(), idempotency_key="original")
    forged_input = _batch(
        candidate_snapshot_digest="d" * 64,
        selections=(_selection(1),),
    )
    forged_digest = production_batch_fact_digest(identity, forged_input)
    forged_attestation = persisted.attestation.model_copy(update={"payload_digest": forged_digest})
    forged = RankingV3ProductionBatch(
        **forged_input.model_dump(mode="python"),
        identity=identity,
        fact_digest=forged_digest,
        attestation=forged_attestation,
        idempotency_key="forged",
        recorded_at=RECORDED_AT,
    )

    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="attestation is invalid",
    ):
        store.append_batch(forged)


def test_wrong_attestation_key_rejects_every_service_read_and_replay():
    identity = _identity()
    service, store, release_authority = _service(identity)
    item = _batch()
    persisted = service.record_batch(identity, item, idempotency_key="right-key")
    wrong_key_service = RankingV3ProductionSelectionService(
        store,
        release_authority,
        selection_authority=_SelectionAuthority(),
        attestor=RankingV3Attestor(WRONG_ATTESTATION_KEY),
        now=lambda: RECORDED_AT,
    )

    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="attestation is invalid",
    ):
        wrong_key_service.get_batch_for_session(identity, item.session_date)
    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="attestation is invalid",
    ):
        wrong_key_service.get_batch_by_fact_digest(identity, persisted.fact_digest)
    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="attestation is invalid",
    ):
        wrong_key_service.get_batch_by_idempotency_key(identity, "right-key")
    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="attestation is invalid",
    ):
        wrong_key_service.get_selection_by_source_snapshot(
            identity,
            item.selections[0].source_snapshot_id,
        )
    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="attestation is invalid",
    ):
        wrong_key_service.list_batches(identity)
    with pytest.raises(
        RankingV3ProductionIntegrityError,
        match="attestation is invalid",
    ):
        wrong_key_service.record_batch(identity, item, idempotency_key="right-key")


def test_empty_selection_batch_is_valid_and_content_addressed():
    item = RankingV3ProductionBatchInput.create(
        session_date=date(2026, 7, 29),
        candidate_snapshot_digest="c" * 64,
        selections=(),
    )

    assert item.selected_count == 0
    assert item.selection_batch_digest == production_selection_batch_digest(item)


def test_service_fails_closed_without_release_authority():
    identity = _identity()
    attestor = RankingV3Attestor(ATTESTATION_KEY)
    service = RankingV3ProductionSelectionService(
        InMemoryRankingV3ProductionStore(attestor=attestor),
        selection_authority=_SelectionAuthority(),
        attestor=attestor,
        now=lambda: RECORDED_AT,
    )

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="no authoritative",
    ):
        service.record_batch(identity, _batch(), idempotency_key="deny-all")


def test_service_fails_closed_on_malformed_authority_response():
    class _MalformedAuthority:
        def validate_current_release(self, identity):
            return {"valid": True}

    identity = _identity()
    attestor = RankingV3Attestor(ATTESTATION_KEY)
    service = RankingV3ProductionSelectionService(
        InMemoryRankingV3ProductionStore(attestor=attestor),
        _MalformedAuthority(),
        selection_authority=_SelectionAuthority(),
        attestor=attestor,
        now=lambda: RECORDED_AT,
    )

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="validation failed",
    ):
        service.record_batch(identity, _batch(), idempotency_key="malformed")


def test_service_fails_closed_without_selection_authority():
    identity = _identity()
    attestor = RankingV3Attestor(ATTESTATION_KEY)
    service = RankingV3ProductionSelectionService(
        InMemoryRankingV3ProductionStore(attestor=attestor),
        _ReleaseAuthority(identity),
        attestor=attestor,
        now=lambda: RECORDED_AT,
    )

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="no authoritative Ranking V3 production selection verifier",
    ):
        service.record_batch(identity, _batch(), idempotency_key="deny-by-default")


def test_explicit_selection_authority_rejection_is_fail_closed():
    identity = _identity()
    selection_authority = _SelectionAuthority(authorized=False)
    service, store, _ = _service(
        identity,
        selection_authority=selection_authority,
    )

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="selection was rejected",
    ):
        service.record_batch(identity, _batch(), idempotency_key="explicit-reject")

    assert selection_authority.calls == 1
    assert store.get_batch_for_session(identity, date(2026, 7, 29)) is None


def test_selection_authority_must_bind_exact_identity_and_batch_input():
    class _WrongBindingAuthority:
        def validate_selection(self, identity, item):
            return RankingV3ProductionSelectionValidation(
                authorized=True,
                reason="claims to authorize different facts",
                identity_digest="f" * 64,
                selection_batch_digest=item.selection_batch_digest,
            )

    identity = _identity()
    attestor = RankingV3Attestor(ATTESTATION_KEY)
    service = RankingV3ProductionSelectionService(
        InMemoryRankingV3ProductionStore(attestor=attestor),
        _ReleaseAuthority(identity),
        selection_authority=_WrongBindingAuthority(),
        attestor=attestor,
        now=lambda: RECORDED_AT,
    )

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="do not match the supplied batch",
    ):
        service.record_batch(identity, _batch(), idempotency_key="wrong-binding")


def test_in_memory_store_serializes_concurrent_exact_replays():
    identity = _identity()
    service, store, _ = _service(identity)
    item = _batch()

    def record(index: int):
        return service.record_batch(
            identity,
            item,
            idempotency_key=f"concurrent-{index}",
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        batches = list(executor.map(record, range(32)))

    assert len({batch.fact_digest for batch in batches}) == 1
    assert len({batch.recorded_at for batch in batches}) == 1
    authoritative = store.get_batch_for_session(identity, item.session_date)
    assert authoritative is not None
    assert all(batch == authoritative for batch in batches)
    assert all(
        store.get_batch_by_idempotency_key(identity, f"concurrent-{index}") == authoritative
        for index in range(32)
    )
