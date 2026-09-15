#!/usr/bin/env python3
"""Atomically upgrade financial-forward research from evaluator v4 to v5."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

import upgrade_financial_forward_research as v4

OLD_BUNDLE = Path("/opt/qagent-research/financial-forward-20260914-v4")
NEW_BUNDLE = Path("/opt/qagent-research/financial-forward-20260914-v5")
CRON = v4.FORWARD_CRON
BACKUPS = v4.BACKUPS
TIMEZONE = v4.TIMEZONE
OLD_CRON_SHA = "a570099924da2e4540a58aa7fe3952c689fc609ce821a13df21de569fe29d81f"
OLD_MANIFEST_SHA = "01bd17e086321a7b1cd990858383992fa62270608a66bb6ef7aa2c6029f03a28"
NEW_REQUIRED = v4.REQUIRED | {"scripts/upgrade_financial_forward_research_v5.py"}


def old_cron_bytes():
    value = v4.cron_bytes()
    if v4.checksum(value) != OLD_CRON_SHA:
        raise ValueError("baseline_template_changed")
    return value


def upgraded_cron():
    value = old_cron_bytes()
    if value.count(str(OLD_BUNDLE).encode()) != 1:
        raise ValueError("baseline_cron_shape_changed")
    result = value.replace(str(OLD_BUNDLE).encode(), str(NEW_BUNDLE).encode())
    if result.replace(str(NEW_BUNDLE).encode(), str(OLD_BUNDLE).encode()) != value:
        raise ValueError("unsafe_cron_rewrite")
    return result


def inspect(old_cron_sha, old_manifest_sha, new_manifest_sha):
    if old_cron_sha != OLD_CRON_SHA or old_manifest_sha != OLD_MANIFEST_SHA:
        raise ValueError("unexpected_upgrade_baseline")
    v4.validate_forward_bundle(old_manifest_sha, bundle=OLD_BUNDLE, required=v4.REQUIRED)
    v4.validate_forward_bundle(new_manifest_sha, bundle=NEW_BUNDLE, required=NEW_REQUIRED)
    if TIMEZONE.read_text().strip() not in {"UTC", "Etc/UTC"}:
        raise ValueError("utc_required")
    v4._directory(CRON.parent)
    meta = v4._regular(CRON)
    if stat.S_IMODE(meta.st_mode) != 0o644:
        raise ValueError("unsafe_cron_permissions")
    current, wanted = CRON.read_bytes(), upgraded_cron()
    if current == wanted:
        return current, wanted, True, meta
    if v4.checksum(current) != old_cron_sha or current != old_cron_bytes():
        raise ValueError("existing_cron_mismatch")
    return current, wanted, False, meta


def _fsync_parent(path):
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def install(old_cron_sha, old_manifest_sha, new_manifest_sha, *, execute=False):
    old, new, installed, _ = inspect(old_cron_sha, old_manifest_sha, new_manifest_sha)
    result = {"status": "already_installed" if installed else "planned",
              "old_cron_sha256": old_cron_sha, "new_cron_sha256": v4.checksum(new),
              "new_manifest_sha256": new_manifest_sha, "cron": new.decode(),
              "started_job": False}
    if not execute:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    v4._directory(BACKUPS, private=True)
    fd = os.open(BACKUPS / ".upgrade.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old, new, installed, original_meta = inspect(
            old_cron_sha, old_manifest_sha, new_manifest_sha)
        if installed:
            result["status"] = "already_installed"
            return result
        fd, backup = tempfile.mkstemp(prefix="before-v5-", suffix=".cron", dir=BACKUPS)
        with os.fdopen(fd, "wb") as stream:
            stream.write(old)
            stream.flush()
            os.fsync(stream.fileno())
        fd, pending = tempfile.mkstemp(prefix=".financial-forward-v5-", dir=CRON.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(new)
                os.fchmod(stream.fileno(), 0o644)
                os.fchown(stream.fileno(), 0, 0)
                stream.flush()
                os.fsync(stream.fileno())
            current, wanted, installed, meta = inspect(
                old_cron_sha, old_manifest_sha, new_manifest_sha)
            if (installed or current != old or wanted != new
                    or (meta.st_dev, meta.st_ino) != (original_meta.st_dev, original_meta.st_ino)):
                raise ValueError("cron_changed_during_upgrade")
            os.replace(pending, CRON)
            _fsync_parent(CRON)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
        result.update(status="upgraded", backup=str(backup))
        return result


def rollback(backup, old_manifest_sha, new_manifest_sha, *, execute=False):
    if backup is None:
        restored = old_cron_bytes()
    else:
        backup = Path(backup)
        if (backup.parent.resolve() != BACKUPS.resolve()
                or not backup.name.startswith("before-v5-")
                or stat.S_IMODE(v4._regular(backup).st_mode) != 0o600
                or v4.checksum(backup.read_bytes()) != OLD_CRON_SHA):
            raise ValueError("invalid_rollback_backup")
        restored = backup.read_bytes()
    _, wanted, installed, _ = inspect(OLD_CRON_SHA, old_manifest_sha, new_manifest_sha)
    if not installed or CRON.read_bytes() != wanted:
        raise ValueError("forward_v5_cron_changed")
    result = {"status": "rollback_planned", "restored_cron_sha256": OLD_CRON_SHA,
              "started_job": False}
    if not execute:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    fd = os.open(BACKUPS / ".upgrade.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if CRON.read_bytes() != wanted:
            raise ValueError("forward_v5_cron_changed")
        fd, pending = tempfile.mkstemp(prefix=".financial-forward-v4-restore-", dir=CRON.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(restored)
                os.fchmod(stream.fileno(), 0o644)
                os.fchown(stream.fileno(), 0, 0)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, CRON)
            _fsync_parent(CRON)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
    result["status"] = "rolled_back_to_v4"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-cron-sha256", default=OLD_CRON_SHA)
    parser.add_argument("--expected-old-manifest-sha256", default=OLD_MANIFEST_SHA)
    parser.add_argument("--expected-new-manifest-sha256", required=True)
    parser.add_argument("--rollback-backup", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.rollback_backup:
            report = rollback(args.rollback_backup, args.expected_old_manifest_sha256,
                              args.expected_new_manifest_sha256, execute=args.execute)
        else:
            report = install(args.expected_cron_sha256, args.expected_old_manifest_sha256,
                             args.expected_new_manifest_sha256, execute=args.execute)
    except Exception:
        print(json.dumps({"status": "error", "error": "financial_forward_v5_upgrade_failed"}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
