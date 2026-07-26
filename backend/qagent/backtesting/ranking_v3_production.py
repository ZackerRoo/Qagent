from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardIdentity,
    RankingV3ForwardReleaseProof,
    stable_digest,
    stable_release_proof_digest,
)
from qagent.security.ranking_v3_attestation import (
    RankingV3AttestationEnvelope,
    RankingV3Attestor,
    load_attestation_key,
)


PRODUCTION_IDENTITY_SCHEMA_VERSION = "ranking-v3-production-identity-v1"
PRODUCTION_SELECTION_SCHEMA_VERSION = "ranking-v3-production-selection-v1"
PRODUCTION_BATCH_SCHEMA_VERSION = "ranking-v3-production-batch-v1"
PRODUCTION_BATCH_ATTESTATION_KIND = "ranking-v3-production-batch"


class RankingV3ProductionError(RuntimeError):
    """Base error for immutable Ranking V3 production selections."""


class RankingV3ProductionAuthorizationError(RankingV3ProductionError):
    """Raised when the referenced release is not currently authorized."""


class RankingV3ProductionConflictError(RankingV3ProductionError):
    """Raised when an immutable production key is reused with different facts."""


class RankingV3ProductionIntegrityError(RankingV3ProductionError):
    """Raised when persisted production facts fail canonical digest validation."""


class RankingV3ProductionIdentity(BaseModel):
    """Frozen identity of one approved model deployment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = PRODUCTION_IDENTITY_SCHEMA_VERSION
    identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_run_id: str = Field(min_length=1, max_length=128)
    data_revision: str = Field(min_length=1, max_length=128)
    protocol_identity: RankingV3ForwardIdentity

    @model_validator(mode="after")
    def validate_digest(self):
        if production_identity_digest(self) != self.identity_digest:
            raise ValueError("production identity digest is invalid")
        return self

    @classmethod
    def create(
        cls,
        *,
        release_proof_digest: str,
        validation_run_id: str,
        data_revision: str,
        protocol_identity: RankingV3ForwardIdentity,
    ) -> RankingV3ProductionIdentity:
        payload = {
            "schema_version": PRODUCTION_IDENTITY_SCHEMA_VERSION,
            "release_proof_digest": release_proof_digest,
            "validation_run_id": validation_run_id,
            "data_revision": data_revision,
            "protocol_identity": protocol_identity,
        }
        return cls(identity_digest=stable_digest(payload), **payload)

    @classmethod
    def from_release_proof(
        cls,
        proof: RankingV3ForwardReleaseProof,
        *,
        validation_run_id: str,
    ) -> RankingV3ProductionIdentity:
        if stable_release_proof_digest(proof) != proof.proof_digest:
            raise ValueError("release proof digest is invalid")
        return cls.create(
            release_proof_digest=proof.proof_digest,
            validation_run_id=validation_run_id,
            data_revision=proof.data_revision,
            protocol_identity=proof.identity,
        )


class RankingV3ProductionSelectionItem(BaseModel):
    """Complete immutable facts for one selected production candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = PRODUCTION_SELECTION_SCHEMA_VERSION
    item_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: str = Field(min_length=1, max_length=160)
    instrument_id: str = Field(min_length=1, max_length=32)
    source_snapshot_id: str = Field(min_length=1, max_length=192)
    strategy_id: str = Field(min_length=1, max_length=96)
    rank: int = Field(ge=1)
    score: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_digest(self):
        if production_selection_item_digest(self) != self.item_digest:
            raise ValueError("production selection item digest is invalid")
        return self

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        instrument_id: str,
        source_snapshot_id: str,
        strategy_id: str,
        rank: int,
        score: Decimal,
    ) -> RankingV3ProductionSelectionItem:
        payload = {
            "schema_version": PRODUCTION_SELECTION_SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "instrument_id": instrument_id,
            "source_snapshot_id": source_snapshot_id,
            "strategy_id": strategy_id,
            "rank": rank,
            "score": score,
        }
        return cls(item_digest=stable_digest(payload), **payload)


class RankingV3ProductionBatchInput(BaseModel):
    """Caller-supplied content-addressed selection batch for one session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = PRODUCTION_BATCH_SCHEMA_VERSION
    session_date: date
    candidate_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_batch_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_count: int = Field(ge=0)
    selections: tuple[RankingV3ProductionSelectionItem, ...]

    @model_validator(mode="after")
    def validate_complete_batch(self):
        _validate_selection_set(self.selections, self.selected_count)
        if production_selection_batch_digest(self) != self.selection_batch_digest:
            raise ValueError("production selection batch digest is invalid")
        return self

    @classmethod
    def create(
        cls,
        *,
        session_date: date,
        candidate_snapshot_digest: str,
        selections: tuple[RankingV3ProductionSelectionItem, ...],
    ) -> RankingV3ProductionBatchInput:
        selected_count = len(selections)
        digest = _selection_batch_digest(
            session_date=session_date,
            candidate_snapshot_digest=candidate_snapshot_digest,
            selected_count=selected_count,
            selections=selections,
        )
        return cls(
            session_date=session_date,
            candidate_snapshot_digest=candidate_snapshot_digest,
            selection_batch_digest=digest,
            selected_count=selected_count,
            selections=selections,
        )


class RankingV3ProductionBatch(RankingV3ProductionBatchInput):
    """Persisted append-only proof of one approved production selection."""

    identity: RankingV3ProductionIdentity
    fact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation: RankingV3AttestationEnvelope
    idempotency_key: str = Field(min_length=1, max_length=256)
    recorded_at: datetime

    @model_validator(mode="after")
    def validate_persisted_facts(self):
        _require_aware_datetime(self.recorded_at, "recorded_at")
        if production_batch_fact_digest(self.identity, self) != self.fact_digest:
            raise ValueError("production batch fact digest is invalid")
        if self.attestation.kind != PRODUCTION_BATCH_ATTESTATION_KIND:
            raise ValueError("production batch attestation kind is invalid")
        if self.attestation.payload_digest != self.fact_digest:
            raise ValueError("production batch attestation payload digest is invalid")
        return self


class RankingV3ProductionAdmissionBinding(BaseModel):
    """Authoritative production-selection facts required for paper admission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_fact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_item_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_date: date
    candidate_id: str = Field(min_length=1, max_length=160)
    instrument_id: str = Field(min_length=1, max_length=32)
    source_snapshot_id: str = Field(min_length=1, max_length=192)
    strategy_id: str = Field(min_length=1, max_length=96)
    rank: int = Field(ge=1)
    score: Decimal = Field(ge=0, le=1)

    @classmethod
    def from_batch(
        cls,
        batch: RankingV3ProductionBatch,
        selection: RankingV3ProductionSelectionItem,
    ) -> RankingV3ProductionAdmissionBinding:
        if selection not in batch.selections:
            raise ValueError("production selection is not a member of the supplied batch")
        return cls(
            identity_digest=batch.identity.identity_digest,
            release_proof_digest=batch.identity.release_proof_digest,
            batch_fact_digest=batch.fact_digest,
            selection_item_digest=selection.item_digest,
            session_date=batch.session_date,
            candidate_id=selection.candidate_id,
            instrument_id=selection.instrument_id,
            source_snapshot_id=selection.source_snapshot_id,
            strategy_id=selection.strategy_id,
            rank=selection.rank,
            score=selection.score,
        )


ProductionReleaseStatus = Literal["approved", "pending", "rejected", "missing"]


class RankingV3ProductionReleaseValidation(BaseModel):
    """Server-owned verdict for the release bound to a production identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    current: bool
    status: ProductionReleaseStatus
    reason: str = Field(min_length=1, max_length=512)
    release_proof_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    validation_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    data_revision: str | None = Field(default=None, min_length=1, max_length=128)
    protocol_identity: RankingV3ForwardIdentity | None = None
    approved_at: datetime | None = None

    @model_validator(mode="after")
    def validate_verdict(self):
        if self.approved_at is not None:
            _require_aware_datetime(self.approved_at, "approved_at")
        if self.valid:
            if not self.current or self.status != "approved":
                raise ValueError("a valid release must be current and approved")
            required = (
                self.release_proof_digest,
                self.validation_run_id,
                self.data_revision,
                self.protocol_identity,
                self.approved_at,
            )
            if any(value is None for value in required):
                raise ValueError("a valid release verdict requires complete authoritative facts")
        return self


class RankingV3ProductionReleaseProofAuthority(Protocol):
    """Resolve whether a frozen production identity is the current approved release."""

    def validate_current_release(
        self,
        identity: RankingV3ProductionIdentity,
    ) -> RankingV3ProductionReleaseValidation: ...


class RankingV3ProductionSelectionValidation(BaseModel):
    """Explicit authority verdict bound to one identity and batch input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorized: bool
    reason: str = Field(min_length=1, max_length=512)
    identity_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    selection_batch_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_authorized_binding(self):
        if self.authorized and (
            self.identity_digest is None or self.selection_batch_digest is None
        ):
            raise ValueError(
                "an authorized production selection requires exact identity and batch bindings"
            )
        return self


class RankingV3ProductionSelectionAuthority(Protocol):
    """Verify the exact server-owned identity and candidate selection facts."""

    def validate_selection(
        self,
        identity: RankingV3ProductionIdentity,
        item: RankingV3ProductionBatchInput,
    ) -> RankingV3ProductionSelectionValidation: ...


class RankingV3ProductionStore(Protocol):
    """Append-only persistence contract for production selection proofs."""

    def get_batch_for_session(
        self,
        identity: RankingV3ProductionIdentity,
        session_date: date,
    ) -> RankingV3ProductionBatch | None: ...

    def get_batch_by_idempotency_key(
        self,
        identity: RankingV3ProductionIdentity,
        idempotency_key: str,
    ) -> RankingV3ProductionBatch | None: ...

    def get_batch_by_fact_digest(
        self,
        identity: RankingV3ProductionIdentity,
        fact_digest: str,
    ) -> RankingV3ProductionBatch | None: ...

    def get_selection_by_source_snapshot(
        self,
        identity: RankingV3ProductionIdentity,
        source_snapshot_id: str,
    ) -> RankingV3ProductionAdmissionBinding | None: ...

    def list_batches(
        self,
        identity: RankingV3ProductionIdentity,
        *,
        limit: int = 100,
    ) -> tuple[RankingV3ProductionBatch, ...]: ...

    def append_batch(
        self,
        batch: RankingV3ProductionBatch,
    ) -> RankingV3ProductionBatch: ...


class DenyAllRankingV3ProductionReleaseProofAuthority:
    """Fail-closed default until an authoritative release adapter is supplied."""

    def validate_current_release(
        self,
        identity: RankingV3ProductionIdentity,
    ) -> RankingV3ProductionReleaseValidation:
        return RankingV3ProductionReleaseValidation(
            valid=False,
            current=False,
            status="missing",
            reason="no authoritative Ranking V3 release proof resolver is configured",
        )


class DenyAllRankingV3ProductionSelectionAuthority:
    """Fail-closed default until a server-owned selection verifier is supplied."""

    def validate_selection(
        self,
        identity: RankingV3ProductionIdentity,
        item: RankingV3ProductionBatchInput,
    ) -> RankingV3ProductionSelectionValidation:
        return RankingV3ProductionSelectionValidation(
            authorized=False,
            reason="no authoritative Ranking V3 production selection verifier is configured",
        )


class RankingV3ProductionSelectionService:
    """Authorize and append one immutable post-release selection batch per day."""

    def __init__(
        self,
        store: RankingV3ProductionStore,
        release_authority: RankingV3ProductionReleaseProofAuthority | None = None,
        *,
        selection_authority: RankingV3ProductionSelectionAuthority | None = None,
        attestor: RankingV3Attestor | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.release_authority = (
            release_authority or DenyAllRankingV3ProductionReleaseProofAuthority()
        )
        self.selection_authority = (
            selection_authority or DenyAllRankingV3ProductionSelectionAuthority()
        )
        self.attestor = attestor or RankingV3Attestor(load_attestation_key())
        self._now = now or (lambda: datetime.now(timezone.utc))

    def record_batch(
        self,
        identity: RankingV3ProductionIdentity,
        item: RankingV3ProductionBatchInput,
        *,
        idempotency_key: str,
    ) -> RankingV3ProductionBatch:
        key = _require_nonempty(idempotency_key, "idempotency_key")
        self._require_authoritative_release(identity, item.session_date)
        self._require_authoritative_selection(identity, item)
        fact_digest = production_batch_fact_digest(identity, item)

        by_key = self.store.get_batch_by_idempotency_key(identity, key)
        if by_key is not None:
            if by_key.fact_digest != fact_digest:
                raise RankingV3ProductionConflictError(
                    "production idempotency key is already bound to different facts"
                )
            _require_batch_integrity(by_key, self.attestor)
            return by_key

        by_session = self.store.get_batch_for_session(identity, item.session_date)
        if by_session is not None:
            if by_session.fact_digest != fact_digest:
                raise RankingV3ProductionConflictError(
                    "production session already has a different immutable selection batch"
                )
            _require_batch_integrity(by_session, self.attestor)
            if by_session.idempotency_key == key:
                return by_session
            alias = RankingV3ProductionBatch(
                **by_session.model_dump(
                    mode="python",
                    exclude={"idempotency_key"},
                ),
                idempotency_key=key,
            )
            return self.store.append_batch(alias)

        batch = RankingV3ProductionBatch(
            **item.model_dump(mode="python"),
            identity=identity,
            fact_digest=fact_digest,
            attestation=self.attestor.sign(
                PRODUCTION_BATCH_ATTESTATION_KIND,
                fact_digest,
            ),
            idempotency_key=key,
            recorded_at=_require_aware_datetime(self._now(), "clock"),
        )
        persisted = self.store.append_batch(batch)
        _require_batch_integrity(persisted, self.attestor)
        if persisted.fact_digest != fact_digest:
            raise RankingV3ProductionConflictError(
                "production store returned facts that do not match the append request"
            )
        return persisted

    def get_batch_for_session(
        self,
        identity: RankingV3ProductionIdentity,
        session_date: date,
    ) -> RankingV3ProductionBatch | None:
        batch = self.store.get_batch_for_session(identity, session_date)
        if batch is not None:
            _require_batch_integrity(batch, self.attestor)
        return batch

    def get_batch_by_fact_digest(
        self,
        identity: RankingV3ProductionIdentity,
        fact_digest: str,
    ) -> RankingV3ProductionBatch | None:
        batch = self.store.get_batch_by_fact_digest(identity, fact_digest)
        if batch is not None:
            _require_batch_integrity(batch, self.attestor)
        return batch

    def get_batch_by_idempotency_key(
        self,
        identity: RankingV3ProductionIdentity,
        idempotency_key: str,
    ) -> RankingV3ProductionBatch | None:
        batch = self.store.get_batch_by_idempotency_key(identity, idempotency_key)
        if batch is not None:
            _require_batch_integrity(batch, self.attestor)
        return batch

    def get_selection_by_source_snapshot(
        self,
        identity: RankingV3ProductionIdentity,
        source_snapshot_id: str,
    ) -> RankingV3ProductionAdmissionBinding | None:
        binding = self.store.get_selection_by_source_snapshot(
            identity,
            source_snapshot_id,
        )
        if binding is None:
            return None
        batch = self.store.get_batch_by_fact_digest(
            identity,
            binding.batch_fact_digest,
        )
        if batch is None:
            raise RankingV3ProductionIntegrityError(
                "production selection references a missing batch"
            )
        _require_batch_integrity(batch, self.attestor)
        matching = [
            RankingV3ProductionAdmissionBinding.from_batch(batch, selection)
            for selection in batch.selections
            if selection.source_snapshot_id == source_snapshot_id
        ]
        if len(matching) != 1 or matching[0] != binding:
            raise RankingV3ProductionIntegrityError(
                "production selection binding does not match its signed batch"
            )
        return binding

    def list_batches(
        self,
        identity: RankingV3ProductionIdentity,
        *,
        limit: int = 100,
    ) -> tuple[RankingV3ProductionBatch, ...]:
        batches = self.store.list_batches(identity, limit=limit)
        for batch in batches:
            _require_batch_integrity(batch, self.attestor)
        return batches

    def _require_authoritative_release(
        self,
        identity: RankingV3ProductionIdentity,
        session_date: date,
    ) -> None:
        try:
            validation = RankingV3ProductionReleaseValidation.model_validate(
                self.release_authority.validate_current_release(identity)
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise RankingV3ProductionAuthorizationError(
                "authoritative release proof validation failed"
            ) from exc

        if not validation.valid or not validation.current or validation.status != "approved":
            raise RankingV3ProductionAuthorizationError(validation.reason)
        expected = (
            validation.release_proof_digest == identity.release_proof_digest
            and validation.validation_run_id == identity.validation_run_id
            and validation.data_revision == identity.data_revision
            and validation.protocol_identity == identity.protocol_identity
        )
        if not expected:
            raise RankingV3ProductionAuthorizationError(
                "authoritative release facts do not match the frozen production identity"
            )
        if validation.approved_at is None:
            raise RankingV3ProductionAuthorizationError(
                "authoritative release has no approval timestamp"
            )
        if session_date < validation.approved_at.date():
            raise RankingV3ProductionAuthorizationError(
                "production signal session predates the authoritative release"
            )

    def _require_authoritative_selection(
        self,
        identity: RankingV3ProductionIdentity,
        item: RankingV3ProductionBatchInput,
    ) -> None:
        try:
            validation = RankingV3ProductionSelectionValidation.model_validate(
                self.selection_authority.validate_selection(identity, item)
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise RankingV3ProductionAuthorizationError(
                "authoritative production selection validation failed"
            ) from exc

        if not validation.authorized:
            raise RankingV3ProductionAuthorizationError(validation.reason)
        if (
            validation.identity_digest != identity.identity_digest
            or validation.selection_batch_digest != item.selection_batch_digest
        ):
            raise RankingV3ProductionAuthorizationError(
                "authoritative production selection facts do not match the supplied batch"
            )


class InMemoryRankingV3ProductionStore:
    """Thread-safe append-only reference implementation of the store contract."""

    def __init__(self, *, attestor: RankingV3Attestor | None = None):
        self._by_session: dict[tuple[str, date], RankingV3ProductionBatch] = {}
        self._by_key: dict[tuple[str, str], RankingV3ProductionBatch] = {}
        self._by_fact: dict[tuple[str, str], RankingV3ProductionBatch] = {}
        self._by_source_snapshot: dict[
            tuple[str, str],
            RankingV3ProductionAdmissionBinding,
        ] = {}
        self.attestor = attestor or RankingV3Attestor(load_attestation_key())
        self._lock = RLock()

    def get_batch_for_session(
        self,
        identity: RankingV3ProductionIdentity,
        session_date: date,
    ) -> RankingV3ProductionBatch | None:
        with self._lock:
            batch = self._by_session.get((identity.identity_digest, session_date))
            if batch is not None:
                _require_batch_integrity(batch, self.attestor)
            return batch

    def get_batch_by_idempotency_key(
        self,
        identity: RankingV3ProductionIdentity,
        idempotency_key: str,
    ) -> RankingV3ProductionBatch | None:
        with self._lock:
            batch = self._by_key.get((identity.identity_digest, idempotency_key))
            if batch is not None:
                _require_batch_integrity(batch, self.attestor)
            return batch

    def get_batch_by_fact_digest(
        self,
        identity: RankingV3ProductionIdentity,
        fact_digest: str,
    ) -> RankingV3ProductionBatch | None:
        with self._lock:
            batch = self._by_fact.get((identity.identity_digest, fact_digest))
            if batch is not None:
                _require_batch_integrity(batch, self.attestor)
            return batch

    def get_selection_by_source_snapshot(
        self,
        identity: RankingV3ProductionIdentity,
        source_snapshot_id: str,
    ) -> RankingV3ProductionAdmissionBinding | None:
        with self._lock:
            binding = self._by_source_snapshot.get((identity.identity_digest, source_snapshot_id))
            if binding is None:
                return None
            batch = self._by_fact.get((identity.identity_digest, binding.batch_fact_digest))
            if batch is None:
                raise RankingV3ProductionIntegrityError(
                    "production selection references a missing batch"
                )
            _require_batch_integrity(batch, self.attestor)
            return binding

    def list_batches(
        self,
        identity: RankingV3ProductionIdentity,
        *,
        limit: int = 100,
    ) -> tuple[RankingV3ProductionBatch, ...]:
        bounded_limit = max(1, min(int(limit), 1000))
        with self._lock:
            batches = [
                batch
                for (identity_digest, _), batch in self._by_session.items()
                if identity_digest == identity.identity_digest
            ]
            batches.sort(key=lambda item: (item.session_date, item.recorded_at), reverse=True)
            selected = tuple(batches[:bounded_limit])
            for batch in selected:
                _require_batch_integrity(batch, self.attestor)
            return selected

    def append_batch(
        self,
        batch: RankingV3ProductionBatch,
    ) -> RankingV3ProductionBatch:
        _require_batch_integrity(batch, self.attestor)
        identity_digest = batch.identity.identity_digest
        session_key = (identity_digest, batch.session_date)
        idempotency_key = (identity_digest, batch.idempotency_key)

        with self._lock:
            existing_by_key = self._by_key.get(idempotency_key)
            if existing_by_key is not None:
                _require_batch_integrity(existing_by_key, self.attestor)
                if existing_by_key.fact_digest != batch.fact_digest:
                    raise RankingV3ProductionConflictError(
                        "production idempotency key is already bound to different facts"
                    )
                return existing_by_key

            existing_by_session = self._by_session.get(session_key)
            if existing_by_session is not None:
                _require_batch_integrity(existing_by_session, self.attestor)
                if existing_by_session.fact_digest != batch.fact_digest:
                    raise RankingV3ProductionConflictError(
                        "production session already has a different immutable selection batch"
                    )
                self._by_key[idempotency_key] = existing_by_session
                return existing_by_session

            self._by_session[session_key] = batch
            self._by_key[idempotency_key] = batch
            self._by_fact[(identity_digest, batch.fact_digest)] = batch
            for selection in batch.selections:
                self._by_source_snapshot[(identity_digest, selection.source_snapshot_id)] = (
                    RankingV3ProductionAdmissionBinding.from_batch(batch, selection)
                )
            return batch


def production_identity_digest(identity: RankingV3ProductionIdentity) -> str:
    return stable_digest(identity.model_dump(mode="python", exclude={"identity_digest"}))


def production_selection_item_digest(
    item: RankingV3ProductionSelectionItem,
) -> str:
    return stable_digest(item.model_dump(mode="python", exclude={"item_digest"}))


def production_selection_batch_digest(item: RankingV3ProductionBatchInput) -> str:
    return _selection_batch_digest(
        session_date=item.session_date,
        candidate_snapshot_digest=item.candidate_snapshot_digest,
        selected_count=item.selected_count,
        selections=item.selections,
    )


def production_batch_fact_digest(
    identity: RankingV3ProductionIdentity,
    item: RankingV3ProductionBatchInput | RankingV3ProductionBatch,
) -> str:
    return stable_digest(
        {
            "identity": identity,
            "batch": item.model_dump(
                mode="python",
                exclude={
                    "identity",
                    "fact_digest",
                    "attestation",
                    "idempotency_key",
                    "recorded_at",
                },
            ),
        }
    )


def _selection_batch_digest(
    *,
    session_date: date,
    candidate_snapshot_digest: str,
    selected_count: int,
    selections: tuple[RankingV3ProductionSelectionItem, ...],
) -> str:
    return stable_digest(
        {
            "schema_version": PRODUCTION_BATCH_SCHEMA_VERSION,
            "session_date": session_date,
            "candidate_snapshot_digest": candidate_snapshot_digest,
            "selected_count": selected_count,
            "selections": selections,
        }
    )


def _validate_selection_set(
    selections: tuple[RankingV3ProductionSelectionItem, ...],
    selected_count: int,
) -> None:
    if selected_count != len(selections):
        raise ValueError("selected_count must equal the complete selection item count")

    fields = {
        "rank": [item.rank for item in selections],
        "instrument": [item.instrument_id for item in selections],
        "source snapshot": [item.source_snapshot_id for item in selections],
        "candidate": [item.candidate_id for item in selections],
    }
    for label, values in fields.items():
        if len(values) != len(set(values)):
            raise ValueError(f"production selection {label} values must be unique")

    ranks = fields["rank"]
    if ranks != list(range(1, selected_count + 1)):
        raise ValueError("production selections must be complete and ordered by contiguous rank")


def require_ranking_v3_production_batch_integrity(
    batch: RankingV3ProductionBatch,
    attestor: RankingV3Attestor,
) -> None:
    if production_identity_digest(batch.identity) != batch.identity.identity_digest:
        raise RankingV3ProductionIntegrityError("persisted production identity digest is invalid")
    for item in batch.selections:
        if production_selection_item_digest(item) != item.item_digest:
            raise RankingV3ProductionIntegrityError(
                "persisted production selection item digest is invalid"
            )
    if production_selection_batch_digest(batch) != batch.selection_batch_digest:
        raise RankingV3ProductionIntegrityError(
            "persisted production selection batch digest is invalid"
        )
    if production_batch_fact_digest(batch.identity, batch) != batch.fact_digest:
        raise RankingV3ProductionIntegrityError("persisted production batch fact digest is invalid")
    if not attestor.verify(
        batch.attestation,
        expected_kind=PRODUCTION_BATCH_ATTESTATION_KIND,
        expected_payload_digest=batch.fact_digest,
    ):
        raise RankingV3ProductionIntegrityError("persisted production batch attestation is invalid")


_require_batch_integrity = require_ranking_v3_production_batch_integrity


def _require_aware_datetime(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _require_nonempty(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized
