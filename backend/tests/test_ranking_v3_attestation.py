from __future__ import annotations

import base64
import hashlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from qagent.security.ranking_v3_attestation import (
    ATTESTATION_ALGORITHM,
    ATTESTATION_KEY_ENV,
    ATTESTATION_KEY_FILE_ENV,
    ATTESTATION_VERSION,
    RankingV3AttestationEnvelope,
    RankingV3AttestationKeyError,
    RankingV3Attestor,
    load_attestation_key,
    sign,
    verify,
)


KEY = b"k" * 32
OTHER_KEY = b"z" * 32
PAYLOAD_DIGEST = hashlib.sha256(b"approved-release-proof").hexdigest()
OTHER_PAYLOAD_DIGEST = hashlib.sha256(b"other-release-proof").hexdigest()


def test_explicit_key_signs_a_versioned_envelope_and_verifies() -> None:
    envelope = sign("release-proof", PAYLOAD_DIGEST, key=KEY)

    assert envelope.version == ATTESTATION_VERSION
    assert envelope.algorithm == ATTESTATION_ALGORITHM
    assert envelope.kind == "release-proof"
    assert envelope.payload_digest == PAYLOAD_DIGEST
    assert len(envelope.signature) == 64
    assert verify(envelope, key=KEY)
    assert verify(
        envelope,
        expected_kind="release-proof",
        expected_payload_digest=PAYLOAD_DIGEST,
        key=KEY,
    )
    assert not verify(envelope, key=OTHER_KEY)


def test_signing_is_deterministic_and_domain_separated_by_kind() -> None:
    attestor = RankingV3Attestor(KEY)

    release = attestor.sign("release-proof", PAYLOAD_DIGEST)
    replay = attestor.sign("release-proof", PAYLOAD_DIGEST)
    selection = attestor.sign("production-selection", PAYLOAD_DIGEST)

    assert replay == release
    assert selection.signature != release.signature
    assert not attestor.verify(
        release,
        expected_kind="production-selection",
        expected_payload_digest=PAYLOAD_DIGEST,
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("kind", "production-selection"),
        ("payload_digest", OTHER_PAYLOAD_DIGEST),
        ("signature", "0" * 64),
    ],
)
def test_tampering_kind_payload_or_signature_fails(
    field: str,
    replacement: str,
) -> None:
    envelope = sign("release-proof", PAYLOAD_DIGEST, key=KEY)
    tampered = envelope.model_dump()
    tampered[field] = replacement

    assert not verify(tampered, key=KEY)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("version", "ranking-v3-attestation/v2"),
        ("algorithm", "SHA256"),
    ],
)
def test_tampering_envelope_protocol_fails(field: str, replacement: str) -> None:
    envelope = sign("release-proof", PAYLOAD_DIGEST, key=KEY).model_dump()
    envelope[field] = replacement

    assert not verify(envelope, key=KEY)


def test_verify_rejects_wrong_expected_context() -> None:
    envelope = sign("release-proof", PAYLOAD_DIGEST, key=KEY)

    assert not verify(envelope, expected_kind="production-selection", key=KEY)
    assert not verify(envelope, expected_payload_digest=OTHER_PAYLOAD_DIGEST, key=KEY)
    assert not verify(envelope, expected_payload_digest="not-a-digest", key=KEY)


def test_envelope_forbids_unknown_fields_and_noncanonical_values() -> None:
    envelope = sign("release-proof", PAYLOAD_DIGEST, key=KEY)

    with pytest.raises(ValidationError):
        RankingV3AttestationEnvelope.model_validate(
            {
                **envelope.model_dump(),
                "unknown": "not-signed",
            }
        )
    with pytest.raises(ValidationError):
        RankingV3AttestationEnvelope(
            kind=" release-proof",
            payload_digest=PAYLOAD_DIGEST,
            signature=envelope.signature,
        )
    with pytest.raises(ValidationError):
        RankingV3AttestationEnvelope(
            kind="release-proof",
            payload_digest=PAYLOAD_DIGEST.upper(),
            signature=envelope.signature,
        )
    assert not verify({"kind": "release-proof"}, key=KEY)
    assert not verify(envelope.model_copy(update={"kind": "非规范类型"}), key=KEY)


def test_direct_environment_key_takes_precedence_over_key_file(tmp_path: Path) -> None:
    missing_file = tmp_path / "must-not-be-created.key"
    environ = {
        ATTESTATION_KEY_ENV: KEY.hex(),
        ATTESTATION_KEY_FILE_ENV: str(missing_file),
    }

    assert load_attestation_key(environ=environ) == KEY
    assert not missing_file.exists()


def test_environment_key_accepts_explicit_hex_and_base64_encodings() -> None:
    assert load_attestation_key(environ={ATTESTATION_KEY_ENV: f"hex:{KEY.hex()}"}) == KEY

    encoded = base64.b64encode(KEY).decode("ascii")
    assert load_attestation_key(environ={ATTESTATION_KEY_ENV: f"base64:{encoded}"}) == KEY


def test_key_file_is_generated_once_with_0600_permissions(tmp_path: Path) -> None:
    key_file = tmp_path / "private" / "ranking-v3.key"
    environ = {ATTESTATION_KEY_FILE_ENV: str(key_file)}

    first = load_attestation_key(environ=environ)
    second = load_attestation_key(environ=environ)

    assert first == second
    assert len(first) == 32
    assert key_file.read_bytes() == first
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_concurrent_key_file_initialization_converges_on_one_key(tmp_path: Path) -> None:
    key_file = tmp_path / "concurrent" / "ranking-v3.key"

    def load() -> bytes:
        return load_attestation_key(key_file=key_file, environ={})

    with ThreadPoolExecutor(max_workers=8) as executor:
        resolved = tuple(executor.map(lambda _: load(), range(24)))

    assert len(set(resolved)) == 1
    assert key_file.read_bytes() == resolved[0]
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_explicit_key_file_does_not_touch_user_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "home"
    key_file = tmp_path / "configured" / "ranking-v3.key"
    monkeypatch.setenv("HOME", str(fake_home))

    envelope = sign(
        "production-selection",
        PAYLOAD_DIGEST,
        key_file=key_file,
        environ={},
    )

    assert verify(envelope, key_file=key_file, environ={})
    assert key_file.exists()
    assert not (fake_home / ".qagent").exists()


def test_unsafe_or_invalid_key_files_are_rejected(tmp_path: Path) -> None:
    permissive = tmp_path / "permissive.key"
    permissive.write_bytes(KEY)
    permissive.chmod(0o644)

    with pytest.raises(RankingV3AttestationKeyError, match="permissions"):
        load_attestation_key(key_file=permissive, environ={})

    wrong_size = tmp_path / "wrong-size.key"
    wrong_size.write_bytes(KEY + b"x")
    wrong_size.chmod(0o600)

    with pytest.raises(RankingV3AttestationKeyError, match="exactly 32"):
        load_attestation_key(key_file=wrong_size, environ={})


def test_symlink_key_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.key"
    target.write_bytes(KEY)
    target.chmod(0o600)
    link = tmp_path / "link.key"
    os.symlink(target, link)

    with pytest.raises(RankingV3AttestationKeyError, match="regular file"):
        load_attestation_key(key_file=link, environ={})


@pytest.mark.parametrize("configured", ["", "short", "hex:not-hex", "base64:not-base64"])
def test_invalid_environment_keys_are_rejected(configured: str) -> None:
    with pytest.raises(RankingV3AttestationKeyError):
        load_attestation_key(environ={ATTESTATION_KEY_ENV: configured})
