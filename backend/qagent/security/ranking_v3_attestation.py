from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


ATTESTATION_KEY_ENV = "QAGENT_RANKING_V3_ATTESTATION_KEY"
ATTESTATION_KEY_FILE_ENV = "QAGENT_RANKING_V3_ATTESTATION_KEY_FILE"
DEFAULT_ATTESTATION_KEY_FILE = Path("~/.qagent/ranking-v3-attestation.key")

ATTESTATION_VERSION = "ranking-v3-attestation/v1"
ATTESTATION_ALGORITHM = "HMAC-SHA256"

_DOMAIN = b"qagent.ranking-v3.server-attestation"
_KEY_BYTES = 32
_HEX_DIGEST_LENGTH = hashlib.sha256().digest_size * 2


class RankingV3AttestationKeyError(RuntimeError):
    """Raised when the server attestation key cannot be loaded safely."""


class RankingV3AttestationEnvelope(BaseModel):
    """Versioned, immutable server attestation for one canonical payload digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["ranking-v3-attestation/v1"] = ATTESTATION_VERSION
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

    @field_validator("payload_digest")
    @classmethod
    def validate_payload_digest(cls, value: str) -> str:
        return _validate_canonical_sha256(value, field_name="payload_digest")

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        return _validate_canonical_sha256(value, field_name="signature")


class RankingV3Attestor:
    """Signs and verifies Ranking V3 attestations with one resolved server key."""

    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        self._key = _validate_key(key)

    def sign(self, kind: str, payload_digest: str) -> RankingV3AttestationEnvelope:
        unsigned = RankingV3AttestationEnvelope(
            kind=kind,
            payload_digest=payload_digest,
            signature="0" * _HEX_DIGEST_LENGTH,
        )
        signature = hmac.new(
            self._key,
            _signature_message(
                version=unsigned.version,
                algorithm=unsigned.algorithm,
                kind=unsigned.kind,
                payload_digest=unsigned.payload_digest,
            ),
            hashlib.sha256,
        ).hexdigest()
        return unsigned.model_copy(update={"signature": signature})

    def verify(
        self,
        envelope: RankingV3AttestationEnvelope | Mapping[str, Any],
        *,
        expected_kind: str | None = None,
        expected_payload_digest: str | None = None,
    ) -> bool:
        parsed = _parse_envelope(envelope)
        if parsed is None:
            return False
        if expected_kind is not None:
            try:
                validated_kind = RankingV3AttestationEnvelope(
                    kind=expected_kind,
                    payload_digest=parsed.payload_digest,
                    signature=parsed.signature,
                ).kind
            except ValidationError:
                return False
            if not hmac.compare_digest(parsed.kind, validated_kind):
                return False
        if expected_payload_digest is not None:
            try:
                validated_digest = _validate_canonical_sha256(
                    expected_payload_digest,
                    field_name="expected_payload_digest",
                )
            except ValueError:
                return False
            if not hmac.compare_digest(parsed.payload_digest, validated_digest):
                return False
        expected_signature = hmac.new(
            self._key,
            _signature_message(
                version=parsed.version,
                algorithm=parsed.algorithm,
                kind=parsed.kind,
                payload_digest=parsed.payload_digest,
            ),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(parsed.signature, expected_signature)


def load_attestation_key(
    *,
    key: bytes | None = None,
    key_file: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bytes:
    """Resolve the stable server key without exposing it in an envelope."""

    if key is not None:
        return _validate_key(key)

    environment = os.environ if environ is None else environ
    configured_key = environment.get(ATTESTATION_KEY_ENV)
    if configured_key is not None:
        return _decode_environment_key(configured_key)

    configured_file = key_file
    if configured_file is None:
        configured_file = environment.get(
            ATTESTATION_KEY_FILE_ENV,
            str(DEFAULT_ATTESTATION_KEY_FILE),
        )
    path = Path(configured_file).expanduser()
    return _load_or_create_key_file(path)


def sign(
    kind: str,
    payload_digest: str,
    *,
    key: bytes | None = None,
    key_file: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> RankingV3AttestationEnvelope:
    """Sign one canonical SHA-256 payload digest with domain separation."""

    resolved_key = load_attestation_key(key=key, key_file=key_file, environ=environ)
    return RankingV3Attestor(resolved_key).sign(kind, payload_digest)


def verify(
    envelope: RankingV3AttestationEnvelope | Mapping[str, Any],
    *,
    expected_kind: str | None = None,
    expected_payload_digest: str | None = None,
    key: bytes | None = None,
    key_file: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Verify an envelope and, optionally, its expected external context."""

    parsed = _parse_envelope(envelope)
    if parsed is None:
        return False
    resolved_key = load_attestation_key(key=key, key_file=key_file, environ=environ)
    return RankingV3Attestor(resolved_key).verify(
        parsed,
        expected_kind=expected_kind,
        expected_payload_digest=expected_payload_digest,
    )


def _signature_message(
    *,
    version: str,
    algorithm: str,
    kind: str,
    payload_digest: str,
) -> bytes:
    fields = (
        _DOMAIN,
        version.encode("ascii"),
        algorithm.encode("ascii"),
        kind.encode("ascii"),
        bytes.fromhex(payload_digest),
    )
    return b"".join(len(field).to_bytes(4, "big") + field for field in fields)


def _parse_envelope(
    envelope: RankingV3AttestationEnvelope | Mapping[str, Any],
) -> RankingV3AttestationEnvelope | None:
    candidate: Mapping[str, Any]
    if isinstance(envelope, RankingV3AttestationEnvelope):
        candidate = envelope.model_dump()
    else:
        candidate = envelope
    try:
        return RankingV3AttestationEnvelope.model_validate(candidate)
    except (TypeError, ValidationError):
        return None


def _validate_canonical_sha256(value: str, *, field_name: str) -> str:
    if len(value) != _HEX_DIGEST_LENGTH:
        raise ValueError(f"{field_name} must be a 64-character SHA-256 hex digest")
    if value != value.lower():
        raise ValueError(f"{field_name} must use canonical lowercase hex")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be valid hexadecimal") from exc
    if len(decoded) != hashlib.sha256().digest_size:
        raise ValueError(f"{field_name} must encode exactly 32 bytes")
    return value


def _validate_key(key: bytes) -> bytes:
    if not isinstance(key, bytes):
        raise TypeError("attestation key must be bytes")
    if len(key) != _KEY_BYTES:
        raise RankingV3AttestationKeyError("attestation key must contain exactly 32 bytes")
    return key


def _decode_environment_key(value: str) -> bytes:
    if not value:
        raise RankingV3AttestationKeyError(f"{ATTESTATION_KEY_ENV} must not be empty")

    try:
        if value.startswith("hex:"):
            decoded = bytes.fromhex(value.removeprefix("hex:"))
        elif value.startswith("base64:"):
            decoded = base64.b64decode(value.removeprefix("base64:"), validate=True)
        elif len(value) == _HEX_DIGEST_LENGTH:
            decoded = bytes.fromhex(value)
        else:
            decoded = value.encode("utf-8")
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise RankingV3AttestationKeyError(
            f"{ATTESTATION_KEY_ENV} is not a valid raw, hex, or base64 key"
        ) from exc
    return _validate_key(decoded)


def _load_or_create_key_file(path: Path) -> bytes:
    try:
        return _read_key_file(path)
    except FileNotFoundError:
        pass

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    generated_key = secrets.token_bytes(_KEY_BYTES)
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, generated_key)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        try:
            os.link(temporary_path, path)
            _fsync_directory(path.parent)
        except FileExistsError:
            pass
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return _read_key_file(path)


def _read_key_file(path: Path) -> bytes:
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise RankingV3AttestationKeyError("attestation key path must be a regular file")
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise RankingV3AttestationKeyError(
            "attestation key file permissions must not grant group or other access"
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RankingV3AttestationKeyError("attestation key file cannot be opened safely") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise RankingV3AttestationKeyError("attestation key path must be a regular file")
        if stat.S_IMODE(opened_stat.st_mode) & 0o077:
            raise RankingV3AttestationKeyError(
                "attestation key file permissions must not grant group or other access"
            )
        key = os.read(descriptor, _KEY_BYTES + 1)
    finally:
        os.close(descriptor)
    return _validate_key(key)


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written == 0:
            raise RankingV3AttestationKeyError("failed to write attestation key")
        offset += written


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
