#!/usr/bin/env python3
"""Preview, install, or roll back the isolated financial-forward cron bundle."""
from __future__ import annotations

import argparse
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import stat
import tempfile
from uuid import uuid4

import install_daily_financial_research as daily

DAILY_BUNDLE = Path("/opt/qagent-research/daily-financial-20260914-v2")
FORWARD_BUNDLE = Path("/opt/qagent-research/financial-forward-20260914-v4")
DAILY_CRON = Path("/etc/cron.d/qagent-daily-financial-research")
FORWARD_CRON = Path("/etc/cron.d/qagent-financial-forward-research")
BACKUPS = Path("/var/backups/qagent-financial-forward-research")
TIMEZONE = Path("/etc/timezone")
SIGNAL_DIR = Path("/var/lib/qagent-research/financial-forward-signals")
EVALUATION_DIR = Path("/var/lib/qagent-research/financial-forward-evaluations")
RUN_DIR = Path("/var/lib/qagent-research/financial-forward-runs")
SERVICE_USER = "luozhenkun"
DAILY_CRON_SHA = "4a80db491109badc90190fe9bdd45bf39160118ebe17733a1e934a8cf9b88b9d"
DAILY_MANIFEST_SHA = "4549c4af411f1f3cd212d154f5b340a9671cd3e54291a79f204bee91b4326a14"
REQUIRED = {
    "scripts/run_financial_forward_research.py",
    "scripts/evaluate_financial_challenger.py",
    "scripts/rank_financial_candidate.py",
    "scripts/research_financial_enrichment.py",
    "scripts/research_cashflow_quality.py",
    "scripts/rank_g2_consensus.py",
    "scripts/compare_g2_selections.py",
    "scripts/upgrade_financial_forward_research.py",
    "scripts/install_daily_financial_research.py",
    "backend/qagent/providers/datahubco.py",
    "backend/qagent/providers/tushare_relay.py",
}


def checksum(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _withdraw_owned_publication(wanted: bytes, pending_meta) -> bool:
    """Remove only the cron hard-linked from this install's pending inode."""
    try:
        current = FORWARD_CRON.stat(follow_symlinks=False)
        if ((current.st_dev, current.st_ino) != (pending_meta.st_dev, pending_meta.st_ino)
                or FORWARD_CRON.is_symlink() or FORWARD_CRON.read_bytes() != wanted):
            return False
        os.unlink(FORWARD_CRON)
        _fsync_directory(FORWARD_CRON.parent)
        return True
    except FileNotFoundError:
        return False


def _promote_receipt(path: Path, receipt: dict) -> None:
    installed = dict(receipt, status="installed")
    fd, pending = tempfile.mkstemp(prefix=".installed-receipt-", dir=BACKUPS)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(installed, stream, sort_keys=True)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        os.replace(pending, path)
        _fsync_directory(BACKUPS)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def _receipt_matches(receipt: dict, *, status_value: str, current_meta,
                     wanted: bytes, expected_daily_cron: str,
                     expected_daily_manifest: str,
                     expected_forward_manifest: str) -> bool:
    required = {"schema", "status", "install_id", "pending_device", "pending_inode",
                "cron_path", "previously_absent", "installed_sha256",
                "forward_manifest_sha256", "daily_cron_sha256"}
    required.add("daily_manifest_sha256")
    return (
        isinstance(receipt, dict) and set(receipt) == required
        and receipt.get("schema") == "financial-forward-install-receipt-v1"
        and receipt.get("status") == status_value
        and isinstance(receipt.get("install_id"), str)
        and re.fullmatch(r"[0-9a-f]{32}", receipt["install_id"]) is not None
        and receipt.get("cron_path") == str(FORWARD_CRON)
        and receipt.get("previously_absent") is True
        and receipt.get("installed_sha256") == checksum(wanted)
        and receipt.get("forward_manifest_sha256") == expected_forward_manifest
        and receipt.get("daily_cron_sha256") == expected_daily_cron
        and receipt.get("daily_manifest_sha256") == expected_daily_manifest
        and (receipt.get("pending_device"), receipt.get("pending_inode"))
        == (current_meta.st_dev, current_meta.st_ino)
    )


def _receipt_shape_valid(receipt: dict) -> bool:
    required = {"schema", "status", "install_id", "pending_device", "pending_inode",
                "cron_path", "previously_absent", "installed_sha256",
                "forward_manifest_sha256", "daily_cron_sha256",
                "daily_manifest_sha256"}
    return (
        isinstance(receipt, dict) and set(receipt) == required
        and receipt.get("schema") == "financial-forward-install-receipt-v1"
        and receipt.get("status") in {"prepared", "installed"}
        and isinstance(receipt.get("install_id"), str)
        and re.fullmatch(r"[0-9a-f]{32}", receipt["install_id"]) is not None
        and type(receipt.get("pending_device")) is int
        and type(receipt.get("pending_inode")) is int
        and receipt.get("cron_path") == str(FORWARD_CRON)
        and receipt.get("previously_absent") is True
        and all(isinstance(receipt.get(key), str)
                and re.fullmatch(r"[0-9a-f]{64}", receipt[key]) is not None
                for key in ("installed_sha256", "forward_manifest_sha256",
                            "daily_cron_sha256", "daily_manifest_sha256"))
    )


def _recover_existing(wanted: bytes, expected_daily_cron: str,
                      expected_daily_manifest: str,
                      expected_forward_manifest: str) -> dict:
    """Finalize exactly one crash-left prepared receipt, or fail closed."""
    _directory(BACKUPS, private=True)
    current_meta = _regular(FORWARD_CRON)
    if stat.S_IMODE(current_meta.st_mode) != 0o644 or FORWARD_CRON.read_bytes() != wanted:
        raise ValueError("existing_forward_cron_mismatch")
    prepared, installed, invalid, unrelated = [], [], [], []
    for path in sorted(BACKUPS.glob("before-install-*.json")):
        try:
            meta = _regular(path)
            if stat.S_IMODE(meta.st_mode) != 0o600:
                raise ValueError("unsafe_receipt_permissions")
            receipt = json.loads(path.read_text())
            if not _receipt_shape_valid(receipt):
                raise ValueError("invalid_receipt_shape")
            if receipt["forward_manifest_sha256"] != expected_forward_manifest:
                # A structurally valid receipt for a prior reviewed bundle is
                # historical evidence, not a conflict with this v3 recovery.
                unrelated.append(path)
                continue
            status_value = receipt["status"]
            if _receipt_matches(receipt, status_value=status_value, current_meta=current_meta,
                                wanted=wanted, expected_daily_cron=expected_daily_cron,
                                expected_daily_manifest=expected_daily_manifest,
                                expected_forward_manifest=expected_forward_manifest):
                (prepared if status_value == "prepared" else installed).append((path, receipt))
            else:
                invalid.append(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            invalid.append(path)
    if len(installed) == 1:
        return {"status": "already_installed", "receipt": str(installed[0][0])}
    if installed or invalid or len(prepared) != 1:
        raise ValueError("ambiguous_or_missing_install_receipt")
    path, receipt = prepared[0]
    _fsync_directory(FORWARD_CRON.parent)
    _promote_receipt(path, receipt)
    return {"status": "recovered_installed", "receipt": str(path)}


def _regular(path: Path):
    if any(item.is_symlink() for item in (path, *path.parents)) or not path.is_file():
        raise ValueError("unsafe_path")
    meta = path.stat()
    if meta.st_uid != 0 or meta.st_mode & 0o022:
        raise ValueError("unsafe_file_permissions")
    return meta


def _directory(path: Path, *, private=False):
    if any(item.is_symlink() for item in (path, *path.parents)) or not path.is_dir():
        raise ValueError("unsafe_directory")
    meta = path.stat()
    if meta.st_uid != 0 or meta.st_mode & (0o077 if private else 0o022):
        raise ValueError("unsafe_directory_permissions")


def _service_identity() -> tuple[int, int]:
    account = pwd.getpwnam(SERVICE_USER)
    return account.pw_uid, account.pw_gid


def _validate_research_directory(path: Path, uid: int, gid: int) -> None:
    if any(item.is_symlink() for item in (path, *path.parents)) or not path.is_dir():
        raise ValueError("unsafe_research_directory")
    meta = path.stat()
    if (meta.st_uid, meta.st_gid) != (uid, gid) or stat.S_IMODE(meta.st_mode) != 0o700:
        raise ValueError("unsafe_research_directory_owner_or_mode")


def _prepare_research_directories(uid: int, gid: int, *, execute: bool) -> dict:
    # Signals contains an already sealed production-independent artifact and is
    # therefore validation-only. Evaluation/run directories may be created.
    _validate_research_directory(SIGNAL_DIR, uid, gid)
    result = {str(SIGNAL_DIR): "verified"}
    for path in (EVALUATION_DIR, RUN_DIR):
        if path.exists() or path.is_symlink():
            _validate_research_directory(path, uid, gid)
            result[str(path)] = "verified"
            continue
        result[str(path)] = "planned"
        if not execute:
            continue
        os.mkdir(path, 0o700)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(descriptor, 0o700)
            os.fchown(descriptor, uid, gid)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _validate_research_directory(path, uid, gid)
        result[str(path)] = "created"
    return result


def validate_forward_bundle(expected_sha: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("invalid_digest")
    _directory(FORWARD_BUNDLE)
    raw = (FORWARD_BUNDLE / "manifest.json").read_bytes()
    _regular(FORWARD_BUNDLE / "manifest.json")
    if checksum(raw) != expected_sha:
        raise ValueError("manifest_changed")
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema", "files"}
            or manifest["schema"] != "financial-forward-bundle-v1"
            or not isinstance(manifest["files"], dict)
            or not REQUIRED <= set(manifest["files"]) or len(manifest["files"]) > 50):
        raise ValueError("invalid_manifest")
    for name, expected in manifest["files"].items():
        pure = PurePosixPath(name)
        if (not isinstance(name, str) or pure.is_absolute() or ".." in pure.parts
                or str(pure) != name or name == "manifest.json"
                or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise ValueError("invalid_manifest_path")
        path = FORWARD_BUNDLE / name
        _regular(path)
        if checksum(path.read_bytes()) != expected:
            raise ValueError("bundle_changed")
    actual = set()
    for path in FORWARD_BUNDLE.rglob("*"):
        if path.is_dir():
            _directory(path)
        else:
            _regular(path)
            actual.add(path.relative_to(FORWARD_BUNDLE).as_posix())
    if actual != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("unmanifested_bundle_files")


def cron_bytes() -> bytes:
    return (
        "# Independent financial forward research; host timezone must be UTC.\n"
        "SHELL=/bin/sh\nPATH=/usr/bin:/bin\n"
        "37 11 * * 1-5 luozhenkun PYTHONPATH=/opt/qagent/current/backend "
        "/opt/qagent/current/backend/.venv/bin/python -B "
        f"{FORWARD_BUNDLE}/scripts/run_financial_forward_research.py "
        "--daily-dir /var/lib/qagent-research/daily-financial "
        "--baseline-dir /var/lib/qagent-research/g2-forward-results/signals "
        f"--signal-dir {SIGNAL_DIR} "
        f"--evaluation-dir {EVALUATION_DIR} "
        f"--run-dir {RUN_DIR} "
        "--db /var/lib/qagent/qagent.db --provider-mode free --budget-seconds 300\n"
    ).encode()


def inspect(expected_daily_cron: str, expected_daily_manifest: str,
            expected_forward_manifest: str):
    if (expected_daily_cron != DAILY_CRON_SHA
            or expected_daily_manifest != DAILY_MANIFEST_SHA):
        raise ValueError("unexpected_upgrade_baseline")
    daily.validate_bundle(expected_daily_manifest, bundle=DAILY_BUNDLE,
                          required=daily.REQUIRED | {"scripts/upgrade_daily_financial_research.py"})
    validate_forward_bundle(expected_forward_manifest)
    if TIMEZONE.read_text().strip() not in {"UTC", "Etc/UTC"}:
        raise ValueError("utc_required")
    _directory(FORWARD_CRON.parent)
    daily_meta = _regular(DAILY_CRON)
    if stat.S_IMODE(daily_meta.st_mode) != 0o644 or checksum(DAILY_CRON.read_bytes()) != expected_daily_cron:
        raise ValueError("daily_cron_changed")
    wanted = cron_bytes()
    if FORWARD_CRON.exists() or FORWARD_CRON.is_symlink():
        meta = _regular(FORWARD_CRON)
        if stat.S_IMODE(meta.st_mode) != 0o644 or FORWARD_CRON.read_bytes() != wanted:
            raise ValueError("existing_forward_cron_mismatch")
        return wanted, True
    return wanted, False


def install(expected_daily_cron: str, expected_daily_manifest: str,
            expected_forward_manifest: str, *, execute=False,
            service_uid: int | None = None, service_gid: int | None = None) -> dict:
    if (service_uid is None) != (service_gid is None):
        raise ValueError("service_identity_pair_required")
    if service_uid is None or service_gid is None:
        service_uid, service_gid = _service_identity()
    wanted, exists = inspect(expected_daily_cron, expected_daily_manifest,
                             expected_forward_manifest)
    research_directories = _prepare_research_directories(
        service_uid, service_gid, execute=False)
    result = {"status": "already_installed" if exists else "planned",
              "cron_sha256": checksum(wanted),
              "forward_manifest_sha256": expected_forward_manifest,
              "cron": wanted.decode(), "started_job": False,
              "daily_cron_unchanged": True,
              "research_directories": research_directories}
    if not execute:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    _directory(BACKUPS, private=True)
    lock_fd = os.open(BACKUPS / ".upgrade.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        wanted, exists = inspect(expected_daily_cron, expected_daily_manifest,
                                 expected_forward_manifest)
        result["research_directories"] = _prepare_research_directories(
            service_uid, service_gid, execute=True)
        if exists:
            recovery = _recover_existing(wanted, expected_daily_cron,
                                         expected_daily_manifest,
                                         expected_forward_manifest)
            result.update(recovery)
            return result
        fd, pending = tempfile.mkstemp(prefix=".financial-forward-", dir=FORWARD_CRON.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(wanted)
                os.fchmod(stream.fileno(), 0o644)
                os.fchown(stream.fileno(), 0, 0)
                stream.flush()
                os.fsync(stream.fileno())
            pending_meta = os.stat(pending, follow_symlinks=False)
            install_id = uuid4().hex
            fd, receipt_path = tempfile.mkstemp(
                prefix="before-install-", suffix=".json", dir=BACKUPS)
            receipt = {"schema": "financial-forward-install-receipt-v1",
                       "status": "prepared", "install_id": install_id,
                       "pending_device": pending_meta.st_dev,
                       "pending_inode": pending_meta.st_ino,
                       "cron_path": str(FORWARD_CRON), "previously_absent": True,
                       "installed_sha256": checksum(wanted),
                       "forward_manifest_sha256": expected_forward_manifest,
                       "daily_cron_sha256": expected_daily_cron,
                       "daily_manifest_sha256": expected_daily_manifest}
            with os.fdopen(fd, "w") as stream:
                json.dump(receipt, stream, sort_keys=True)
                stream.flush()
                os.fchmod(stream.fileno(), 0o600)
                os.fsync(stream.fileno())
            _fsync_directory(BACKUPS)
            inspect(expected_daily_cron, expected_daily_manifest, expected_forward_manifest)
            os.link(pending, FORWARD_CRON, follow_symlinks=False)
            _fsync_directory(FORWARD_CRON.parent)
            try:
                _promote_receipt(receipt_path, receipt)
            except Exception:
                # If receipt promotion fails, restore the pre-install state only
                # while the target is still our exact hard-linked inode+bytes.
                # A concurrently replaced operator file is never removed.
                _withdraw_owned_publication(wanted, pending_meta)
                raise
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
        result.update(status="installed", receipt=receipt_path)
        return result


def rollback(receipt_path: Path, expected_forward_manifest: str, *, execute=False) -> dict:
    _regular(receipt_path)
    receipt = json.loads(receipt_path.read_text())
    wanted = cron_bytes()
    if (receipt.get("schema") != "financial-forward-install-receipt-v1"
            or receipt.get("status") != "installed"
            or receipt.get("cron_path") != str(FORWARD_CRON)
            or receipt.get("previously_absent") is not True
            or receipt.get("installed_sha256") != checksum(wanted)
            or receipt.get("forward_manifest_sha256") != expected_forward_manifest):
        raise ValueError("invalid_rollback_receipt")
    meta = _regular(FORWARD_CRON)
    if (stat.S_IMODE(meta.st_mode) != 0o644 or FORWARD_CRON.read_bytes() != wanted
            or (meta.st_dev, meta.st_ino) != (
                receipt.get("pending_device"), receipt.get("pending_inode"))):
        raise ValueError("forward_cron_changed")
    result = {"status": "rollback_planned", "cron_sha256": checksum(wanted),
              "started_job": False}
    if not execute:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    _directory(BACKUPS, private=True)
    lock_fd = os.open(BACKUPS / ".upgrade.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current_meta = _regular(FORWARD_CRON)
        if (FORWARD_CRON.read_bytes() != wanted
                or (current_meta.st_dev, current_meta.st_ino) != (
                    receipt.get("pending_device"), receipt.get("pending_inode"))):
            raise ValueError("forward_cron_changed")
        fd, removed = tempfile.mkstemp(prefix="rolled-back-", suffix=".cron", dir=BACKUPS)
        os.close(fd)
        os.unlink(removed)
        os.replace(FORWARD_CRON, removed)
        dir_fd = os.open(FORWARD_CRON.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        result.update(status="rolled_back", recovered_cron=removed)
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-daily-cron-sha256", default=DAILY_CRON_SHA)
    parser.add_argument("--expected-daily-manifest-sha256", default=DAILY_MANIFEST_SHA)
    parser.add_argument("--expected-forward-manifest-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rollback-receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.rollback_receipt:
            result = rollback(args.rollback_receipt, args.expected_forward_manifest_sha256,
                              execute=args.execute)
        else:
            result = install(args.expected_daily_cron_sha256,
                             args.expected_daily_manifest_sha256,
                             args.expected_forward_manifest_sha256, execute=args.execute)
    except Exception:
        print(json.dumps({"status": "error", "error": "financial_forward_upgrade_failed"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
