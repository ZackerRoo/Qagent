from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from qagent.backtesting.ranking_v4_protocol import (
    RankingV4Protocol,
    build_ranking_v4_protocol,
)
from qagent.security.ranking_v4_attestation import (
    RankingV4AttestationEnvelope,
    RankingV4EvidenceAttestor,
)


DEFINITION_SCHEMA_VERSION = "ranking-v4-prospective-definition-v1"
INVENTORY_SCHEMA_VERSION = "ranking-v4-prospective-attempt-inventory-v1"
RETURN_RECORD_SCHEMA_VERSION = "ranking-v4-prospective-common-date-returns-v1"
PROOF_SCHEMA_VERSION = "ranking-v4-prospective-evidence-proof-v1"

DEFINITION_ATTESTATION_KIND = "ranking-v4-prospective-definition"
INVENTORY_ATTESTATION_KIND = "ranking-v4-prospective-inventory"
RETURN_ATTESTATION_KIND = "ranking-v4-prospective-common-date-return"
PROOF_ATTESTATION_KIND = "ranking-v4-prospective-evidence-proof"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_EPOCH_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
_MARKET_TIME_ZONE = ZoneInfo("Asia/Shanghai")


class RankingV4EvidenceError(RuntimeError):
    """Base error for prospective Ranking V4 evidence."""


class RankingV4EvidenceConflictError(RankingV4EvidenceError):
    """Raised when an immutable identity or sequence is reused with new facts."""


class RankingV4EvidenceStateError(RankingV4EvidenceError):
    """Raised when a prospective evidence write violates frozen state."""


class RankingV4EvidenceIntegrityError(RankingV4EvidenceError):
    """Raised when a digest, signature, chain, or common calendar is invalid."""


class RankingV4ProspectiveIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    epoch_id: str = Field(min_length=3, max_length=96)
    protocol_id: str = Field(min_length=1, max_length=96)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_version: str = Field(min_length=1, max_length=96)
    code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    experiment_registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_revision: int = Field(ge=1)
    evidence_start_date: date

    @field_validator("epoch_id")
    @classmethod
    def validate_epoch_id(cls, value: str) -> str:
        if not _EPOCH_ID.fullmatch(value):
            raise ValueError("epoch_id must be canonical lowercase ASCII")
        return value


class RankingV4ProspectiveDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ranking-v4-prospective-definition-v1"] = (
        DEFINITION_SCHEMA_VERSION
    )
    identity: RankingV4ProspectiveIdentity
    registered_model_ids: tuple[str, ...]
    collection_mode: Literal["prospective_only_no_backfill"] = (
        "prospective_only_no_backfill"
    )
    development_evidence_excluded: Literal[True] = True
    release_scope: Literal["shadow_only"] = "shadow_only"
    frozen_at: datetime
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation: RankingV4AttestationEnvelope

    @field_validator("registered_model_ids")
    @classmethod
    def validate_model_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if not normalized or normalized != value:
            raise ValueError("registered_model_ids must be sorted, unique, and non-empty")
        return value

    @field_validator("frozen_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("frozen_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_definition(self):
        freeze_market_date = self.frozen_at.astimezone(_MARKET_TIME_ZONE).date()
        if self.identity.evidence_start_date <= freeze_market_date:
            raise ValueError("evidence must start strictly after the signed freeze date")
        if self.definition_digest != stable_digest(self.stable_payload()):
            raise ValueError("prospective definition digest mismatch")
        if (
            self.attestation.kind != DEFINITION_ATTESTATION_KIND
            or self.attestation.payload_digest != self.definition_digest
        ):
            raise ValueError("prospective definition attestation context mismatch")
        return self

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"definition_digest", "attestation"},
        )


class RankingV4AttemptDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str = Field(min_length=1, max_length=160)
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RankingV4AttemptInventorySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ranking-v4-prospective-attempt-inventory-v1"] = (
        INVENTORY_SCHEMA_VERSION
    )
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence: int = Field(ge=1)
    as_of_date: date
    pre_epoch_unverifiable_attempt_ids: tuple[str, ...]
    prospective_attempts: tuple[RankingV4AttemptDefinition, ...]
    previous_inventory_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    inventory_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime
    attestation: RankingV4AttestationEnvelope

    @field_validator("pre_epoch_unverifiable_attempt_ids")
    @classmethod
    def validate_prior_attempts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if normalized != value:
            raise ValueError("pre-epoch attempt ids must be sorted and unique")
        return value

    @field_validator("prospective_attempts")
    @classmethod
    def validate_prospective_attempts(
        cls,
        value: tuple[RankingV4AttemptDefinition, ...],
    ) -> tuple[RankingV4AttemptDefinition, ...]:
        ids = tuple(item.attempt_id for item in value)
        if not ids or ids != tuple(sorted(set(ids))):
            raise ValueError("prospective attempts must be sorted, unique, and non-empty")
        return value

    @field_validator("recorded_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_inventory(self):
        if self.sequence == 1 and self.previous_inventory_digest is not None:
            raise ValueError("first inventory snapshot cannot have a predecessor")
        if self.sequence > 1 and self.previous_inventory_digest is None:
            raise ValueError("later inventory snapshots require a predecessor")
        if set(self.pre_epoch_unverifiable_attempt_ids) & {
            item.attempt_id for item in self.prospective_attempts
        }:
            raise ValueError("pre-epoch and prospective attempt ids must be disjoint")
        if self.inventory_digest != stable_digest(self.stable_payload()):
            raise ValueError("attempt inventory digest mismatch")
        if (
            self.attestation.kind != INVENTORY_ATTESTATION_KIND
            or self.attestation.payload_digest != self.inventory_digest
        ):
            raise ValueError("attempt inventory attestation context mismatch")
        return self

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"inventory_digest", "attestation"},
        )


class RankingV4ProspectiveModelReturn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1, max_length=128)
    net_return_pct: Decimal
    stress_net_return_pct: Decimal | None = None
    source_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("net_return_pct", "stress_net_return_pct")
    @classmethod
    def require_finite_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("return evidence values must be finite")
        return value


class RankingV4CommonDateReturnRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ranking-v4-prospective-common-date-returns-v1"] = (
        RETURN_RECORD_SCHEMA_VERSION
    )
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence: int = Field(ge=1)
    rebalance_date: date
    dataset_revision: int = Field(ge=1)
    model_returns: tuple[RankingV4ProspectiveModelReturn, ...]
    previous_record_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime
    attestation: RankingV4AttestationEnvelope

    @field_validator("model_returns")
    @classmethod
    def validate_model_returns(
        cls,
        value: tuple[RankingV4ProspectiveModelReturn, ...],
    ) -> tuple[RankingV4ProspectiveModelReturn, ...]:
        ids = tuple(item.model_id for item in value)
        if not ids or ids != tuple(sorted(set(ids))):
            raise ValueError("model returns must be sorted, unique, and non-empty")
        return value

    @field_validator("recorded_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_record(self):
        if self.sequence == 1 and self.previous_record_digest is not None:
            raise ValueError("first return record cannot have a predecessor")
        if self.sequence > 1 and self.previous_record_digest is None:
            raise ValueError("later return records require a predecessor")
        if self.record_digest != stable_digest(self.stable_payload()):
            raise ValueError("common-date return record digest mismatch")
        if (
            self.attestation.kind != RETURN_ATTESTATION_KIND
            or self.attestation.payload_digest != self.record_digest
        ):
            raise ValueError("return-record attestation context mismatch")
        return self

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"record_digest", "attestation"},
        )


class RankingV4EvidenceProof(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ranking-v4-prospective-evidence-proof-v1"] = (
        PROOF_SCHEMA_VERSION
    )
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    inventory_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    return_record_count: int = Field(ge=0)
    first_rebalance_date: date | None = None
    latest_rebalance_date: date | None = None
    returns_chain_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_scope: Literal["shadow_only"] = "shadow_only"
    official_release_allowed: Literal[False] = False
    generated_at: datetime
    proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation: RankingV4AttestationEnvelope

    @field_validator("generated_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_proof(self):
        if self.return_record_count == 0:
            if self.first_rebalance_date is not None or self.latest_rebalance_date is not None:
                raise ValueError("empty evidence proof cannot claim a date range")
        elif self.first_rebalance_date is None or self.latest_rebalance_date is None:
            raise ValueError("non-empty evidence proof requires a complete date range")
        if self.proof_digest != stable_digest(self.stable_payload()):
            raise ValueError("prospective evidence proof digest mismatch")
        if (
            self.attestation.kind != PROOF_ATTESTATION_KIND
            or self.attestation.payload_digest != self.proof_digest
        ):
            raise ValueError("prospective evidence proof attestation context mismatch")
        return self

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"proof_digest", "attestation"})


class RankingV4EvidenceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    definition: RankingV4ProspectiveDefinition
    inventories: tuple[RankingV4AttemptInventorySnapshot, ...]
    return_records: tuple[RankingV4CommonDateReturnRecord, ...]
    proofs: tuple[RankingV4EvidenceProof, ...]


class RankingV4EvidenceStore(Protocol):
    def freeze_definition(
        self,
        definition: RankingV4ProspectiveDefinition,
    ) -> RankingV4ProspectiveDefinition: ...

    def append_inventory(
        self,
        inventory: RankingV4AttemptInventorySnapshot,
    ) -> RankingV4AttemptInventorySnapshot: ...

    def append_return_record(
        self,
        record: RankingV4CommonDateReturnRecord,
    ) -> RankingV4CommonDateReturnRecord: ...

    def append_proof(self, proof: RankingV4EvidenceProof) -> RankingV4EvidenceProof: ...

    def load_snapshot(self, epoch_id: str) -> RankingV4EvidenceSnapshot | None: ...


def build_prospective_definition(
    *,
    epoch_id: str,
    code_revision: str,
    dataset_revision: int,
    evidence_start_date: date,
    frozen_at: datetime,
    attestor: RankingV4EvidenceAttestor,
    protocol: RankingV4Protocol | None = None,
) -> RankingV4ProspectiveDefinition:
    active = protocol or build_ranking_v4_protocol()
    if not _GIT_REVISION.fullmatch(code_revision):
        raise ValueError("code_revision must be a full lowercase Git revision")
    registered_model_ids = tuple(sorted(active.statistics_definition.pbo_model_ids))
    identity = RankingV4ProspectiveIdentity(
        epoch_id=epoch_id,
        protocol_id=active.protocol_id,
        protocol_digest=active.protocol_digest,
        model_version=active.model_version,
        code_revision=code_revision,
        experiment_registry_digest=active.experiment_registry.registry_digest,
        dataset_revision=dataset_revision,
        evidence_start_date=evidence_start_date,
    )
    unsigned = {
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "identity": identity.model_dump(mode="json"),
        "registered_model_ids": list(registered_model_ids),
        "collection_mode": "prospective_only_no_backfill",
        "development_evidence_excluded": True,
        "release_scope": "shadow_only",
        "frozen_at": _utc_json_timestamp(frozen_at),
    }
    digest = stable_digest(unsigned)
    return RankingV4ProspectiveDefinition(
        **unsigned,
        definition_digest=digest,
        attestation=attestor.sign(DEFINITION_ATTESTATION_KIND, digest),
    )


def build_attempt_inventory_snapshot(
    *,
    definition: RankingV4ProspectiveDefinition,
    sequence: int,
    as_of_date: date,
    pre_epoch_unverifiable_attempt_ids: Sequence[str],
    prospective_attempts: Mapping[str, str],
    previous_inventory_digest: str | None,
    recorded_at: datetime,
    attestor: RankingV4EvidenceAttestor,
) -> RankingV4AttemptInventorySnapshot:
    payload = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "definition_digest": definition.definition_digest,
        "sequence": sequence,
        "as_of_date": as_of_date.isoformat(),
        "pre_epoch_unverifiable_attempt_ids": sorted(
            {str(item).strip() for item in pre_epoch_unverifiable_attempt_ids if str(item).strip()}
        ),
        "prospective_attempts": [
            {"attempt_id": attempt_id, "definition_digest": digest}
            for attempt_id, digest in sorted(prospective_attempts.items())
        ],
        "previous_inventory_digest": previous_inventory_digest,
        "recorded_at": _utc_json_timestamp(recorded_at),
    }
    digest = stable_digest(payload)
    return RankingV4AttemptInventorySnapshot(
        **payload,
        inventory_digest=digest,
        attestation=attestor.sign(INVENTORY_ATTESTATION_KIND, digest),
    )


def build_common_date_return_record(
    *,
    definition: RankingV4ProspectiveDefinition,
    sequence: int,
    rebalance_date: date,
    model_returns: Sequence[RankingV4ProspectiveModelReturn],
    previous_record_digest: str | None,
    recorded_at: datetime,
    attestor: RankingV4EvidenceAttestor,
) -> RankingV4CommonDateReturnRecord:
    ordered = tuple(sorted(model_returns, key=lambda item: item.model_id))
    observed_ids = tuple(item.model_id for item in ordered)
    if observed_ids != definition.registered_model_ids:
        raise RankingV4EvidenceIntegrityError(
            "common-date record must contain every frozen model exactly once"
        )
    if rebalance_date < definition.identity.evidence_start_date:
        raise RankingV4EvidenceIntegrityError(
            "historical or pre-freeze returns cannot enter the prospective ledger"
        )
    payload = {
        "schema_version": RETURN_RECORD_SCHEMA_VERSION,
        "definition_digest": definition.definition_digest,
        "sequence": sequence,
        "rebalance_date": rebalance_date.isoformat(),
        "dataset_revision": definition.identity.dataset_revision,
        "model_returns": [item.model_dump(mode="json") for item in ordered],
        "previous_record_digest": previous_record_digest,
        "recorded_at": _utc_json_timestamp(recorded_at),
    }
    digest = stable_digest(payload)
    return RankingV4CommonDateReturnRecord(
        **payload,
        record_digest=digest,
        attestation=attestor.sign(RETURN_ATTESTATION_KIND, digest),
    )


def build_evidence_proof(
    snapshot: RankingV4EvidenceSnapshot,
    *,
    generated_at: datetime,
    attestor: RankingV4EvidenceAttestor,
) -> RankingV4EvidenceProof:
    if not snapshot.inventories:
        raise RankingV4EvidenceStateError("evidence proof requires an inventory snapshot")
    records = snapshot.return_records
    chain_digest = stable_digest(
        {
            "definition_digest": snapshot.definition.definition_digest,
            "record_digests": [item.record_digest for item in records],
        }
    )
    payload = {
        "schema_version": PROOF_SCHEMA_VERSION,
        "definition_digest": snapshot.definition.definition_digest,
        "inventory_digest": snapshot.inventories[-1].inventory_digest,
        "return_record_count": len(records),
        "first_rebalance_date": (
            records[0].rebalance_date.isoformat() if records else None
        ),
        "latest_rebalance_date": (
            records[-1].rebalance_date.isoformat() if records else None
        ),
        "returns_chain_digest": chain_digest,
        "release_scope": "shadow_only",
        "official_release_allowed": False,
        "generated_at": _utc_json_timestamp(generated_at),
    }
    digest = stable_digest(payload)
    return RankingV4EvidenceProof(
        **payload,
        proof_digest=digest,
        attestation=attestor.sign(PROOF_ATTESTATION_KIND, digest),
    )


def verify_snapshot(
    snapshot: RankingV4EvidenceSnapshot,
    *,
    attestor: RankingV4EvidenceAttestor,
) -> None:
    definition = snapshot.definition
    if not attestor.verify(
        definition.attestation,
        expected_kind=DEFINITION_ATTESTATION_KIND,
        expected_payload_digest=definition.definition_digest,
    ):
        raise RankingV4EvidenceIntegrityError("definition signature is invalid")

    previous_inventory: RankingV4AttemptInventorySnapshot | None = None
    for expected_sequence, inventory in enumerate(snapshot.inventories, start=1):
        if inventory.definition_digest != definition.definition_digest:
            raise RankingV4EvidenceIntegrityError("inventory definition mismatch")
        if inventory.sequence != expected_sequence:
            raise RankingV4EvidenceIntegrityError("inventory sequence is not contiguous")
        expected_previous = (
            previous_inventory.inventory_digest if previous_inventory is not None else None
        )
        if inventory.previous_inventory_digest != expected_previous:
            raise RankingV4EvidenceIntegrityError("inventory chain is invalid")
        if not attestor.verify(
            inventory.attestation,
            expected_kind=INVENTORY_ATTESTATION_KIND,
            expected_payload_digest=inventory.inventory_digest,
        ):
            raise RankingV4EvidenceIntegrityError("inventory signature is invalid")
        if previous_inventory is not None:
            if inventory.as_of_date < previous_inventory.as_of_date:
                raise RankingV4EvidenceIntegrityError("inventory dates must not go backwards")
            if not set(previous_inventory.pre_epoch_unverifiable_attempt_ids).issubset(
                inventory.pre_epoch_unverifiable_attempt_ids
            ):
                raise RankingV4EvidenceIntegrityError("pre-epoch attempt inventory shrank")
            previous_attempts = {
                item.attempt_id: item.definition_digest
                for item in previous_inventory.prospective_attempts
            }
            current_attempts = {
                item.attempt_id: item.definition_digest
                for item in inventory.prospective_attempts
            }
            if any(current_attempts.get(key) != value for key, value in previous_attempts.items()):
                raise RankingV4EvidenceIntegrityError("prospective attempt inventory changed")
        previous_inventory = inventory

    previous_record: RankingV4CommonDateReturnRecord | None = None
    for expected_sequence, record in enumerate(snapshot.return_records, start=1):
        if record.definition_digest != definition.definition_digest:
            raise RankingV4EvidenceIntegrityError("return definition mismatch")
        if record.dataset_revision != definition.identity.dataset_revision:
            raise RankingV4EvidenceIntegrityError("return data revision mismatch")
        if record.rebalance_date < definition.identity.evidence_start_date:
            raise RankingV4EvidenceIntegrityError("pre-epoch return was persisted")
        if record.sequence != expected_sequence:
            raise RankingV4EvidenceIntegrityError("return sequence is not contiguous")
        expected_previous = previous_record.record_digest if previous_record is not None else None
        if record.previous_record_digest != expected_previous:
            raise RankingV4EvidenceIntegrityError("return chain is invalid")
        if previous_record is not None and record.rebalance_date <= previous_record.rebalance_date:
            raise RankingV4EvidenceIntegrityError("return dates must be strictly increasing")
        if tuple(item.model_id for item in record.model_returns) != (
            definition.registered_model_ids
        ):
            raise RankingV4EvidenceIntegrityError("return model family mismatch")
        if not attestor.verify(
            record.attestation,
            expected_kind=RETURN_ATTESTATION_KIND,
            expected_payload_digest=record.record_digest,
        ):
            raise RankingV4EvidenceIntegrityError("return signature is invalid")
        previous_record = record

    for proof in snapshot.proofs:
        if proof.definition_digest != definition.definition_digest:
            raise RankingV4EvidenceIntegrityError("proof definition mismatch")
        if not attestor.verify(
            proof.attestation,
            expected_kind=PROOF_ATTESTATION_KIND,
            expected_payload_digest=proof.proof_digest,
        ):
            raise RankingV4EvidenceIntegrityError("proof signature is invalid")


def stable_digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _jsonable(payload),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return _utc_json_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("digest payload cannot contain a non-finite decimal")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("digest payload cannot contain a non-finite float")
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _utc_json_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
