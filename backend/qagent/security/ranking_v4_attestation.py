from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from qagent.security.ranking_v3_attestation import (
    RankingV3AttestationKeyError,
    load_attestation_key as load_secure_key,
)


ATTESTATION_KEY_ENV = "QAGENT_RANKING_V4_EVIDENCE_ATTESTATION_KEY"
ATTESTATION_KEY_FILE_ENV = "QAGENT_RANKING_V4_EVIDENCE_ATTESTATION_KEY_FILE"
DEFAULT_ATTESTATION_KEY_FILE = Path("~/.qagent/ranking-v4-evidence-attestation.key")

ATTESTATION_VERSION = "ranking-v4-evidence-attestation/v1"
ATTESTATION_ALGORITHM = "HMAC-SHA256"

_DOMAIN = b"qagent.ranking-v4.prospective-evidence-attestation"
_KEY_BYTES = 32
_HEX_DIGEST_LENGTH = hashlib.sha256().digest_size * 2


class RankingV4AttestationKeyError(RuntimeError):
    """Raised when the prospective-evidence signing key is unsafe or invalid."""


class RankingV4AttestationEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["ranking-v4-evidence-attestation/v1"] = ATTESTATION_VERSION
    algorithm: Literal["HMAC-SHA256"] = ATTESTATION_ALGORITHM
    kind: str = Field(min_length=1, max_length=128)
    payload_digest: str = Field(min_length=_HEX_DIGEST_LENGTH, max_length=_HEX_DIGEST_LENGTH)
    signature: str = Field(min_length=_HEX_DIGEST_LENGTH, max_length=_HEX_DIGEST_LENGTH)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("attestation kind must not contain surrounding whitespace")
        try:
            encoded = value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("attestation kind must be ASCII") from exc
        if any(byte < 0x21 or byte > 0x7E for byte in encoded):
            raise ValueError("attestation kind must contain visible ASCII characters only")
        return value

    @field_validator("payload_digest", "signature")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if (
            len(value) != _HEX_DIGEST_LENGTH
            or value != value.lower()
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("attestation digests must be canonical lowercase SHA-256 values")
        return value


class RankingV4EvidenceAttestor:
    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes):
            raise TypeError("attestation key must be bytes")
        if len(key) != _KEY_BYTES:
            raise RankingV4AttestationKeyError(
                "attestation key must contain exactly 32 bytes"
            )
        self._key = key

    def sign(self, kind: str, payload_digest: str) -> RankingV4AttestationEnvelope:
        unsigned = RankingV4AttestationEnvelope(
            kind=kind,
            payload_digest=payload_digest,
            signature="0" * _HEX_DIGEST_LENGTH,
        )
        return unsigned.model_copy(
            update={
                "signature": hmac.new(
                    self._key,
                    _signature_message(unsigned),
                    hashlib.sha256,
                ).hexdigest()
            }
        )

    def verify(
        self,
        envelope: RankingV4AttestationEnvelope | Mapping[str, Any],
        *,
        expected_kind: str | None = None,
        expected_payload_digest: str | None = None,
    ) -> bool:
        try:
            parsed = (
                envelope
                if isinstance(envelope, RankingV4AttestationEnvelope)
                else RankingV4AttestationEnvelope.model_validate(envelope)
            )
        except (TypeError, ValidationError):
            return False
        if expected_kind is not None and not hmac.compare_digest(
            parsed.kind,
            expected_kind,
        ):
            return False
        if expected_payload_digest is not None and not hmac.compare_digest(
            parsed.payload_digest,
            expected_payload_digest,
        ):
            return False
        expected_signature = hmac.new(
            self._key,
            _signature_message(parsed),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(parsed.signature, expected_signature)


def load_ranking_v4_attestation_key(
    *,
    key: bytes | None = None,
    key_file: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bytes:
    if key is not None:
        RankingV4EvidenceAttestor(key)
        return key

    environment = os.environ if environ is None else environ
    configured = environment.get(ATTESTATION_KEY_ENV)
    if configured is not None:
        try:
            if configured.startswith("hex:"):
                decoded = bytes.fromhex(configured.removeprefix("hex:"))
            elif configured.startswith("base64:"):
                decoded = base64.b64decode(
                    configured.removeprefix("base64:"),
                    validate=True,
                )
            elif len(configured) == _HEX_DIGEST_LENGTH:
                decoded = bytes.fromhex(configured)
            else:
                decoded = configured.encode("utf-8")
        except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
            raise RankingV4AttestationKeyError(
                f"{ATTESTATION_KEY_ENV} is not a valid raw, hex, or base64 key"
            ) from exc
        RankingV4EvidenceAttestor(decoded)
        return decoded

    resolved_file = key_file or environment.get(
        ATTESTATION_KEY_FILE_ENV,
        str(DEFAULT_ATTESTATION_KEY_FILE),
    )
    try:
        return load_secure_key(key_file=resolved_file, environ={})
    except RankingV3AttestationKeyError as exc:
        raise RankingV4AttestationKeyError(str(exc)) from exc


def _signature_message(envelope: RankingV4AttestationEnvelope) -> bytes:
    fields = (
        _DOMAIN,
        envelope.version.encode("ascii"),
        envelope.algorithm.encode("ascii"),
        envelope.kind.encode("ascii"),
        bytes.fromhex(envelope.payload_digest),
    )
    return b"".join(len(field).to_bytes(4, "big") + field for field in fields)
