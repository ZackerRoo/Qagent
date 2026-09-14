#!/usr/bin/env python3
"""Upgrade the reviewed financial cron to v2 without starting any job."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

from install_daily_financial_research import (
    BACKUPS, CRON, REQUIRED, SYMBOLS, TIMEZONE, checksum, cron_bytes, directory, regular,
    validate_bundle,
)

OLD_BUNDLE = Path("/opt/qagent-research/daily-financial-20260914")
NEW_BUNDLE = Path("/opt/qagent-research/daily-financial-20260914-v2")
OLD_CRON_SHA = "c9582b6d90d9a70e9be2cd02760312bff8104b37e458fae17820df6e53878bba"
OLD_MANIFEST_SHA = "a60212b96de7e83b7e34ea29ad27b3304368a53dcf2e9c0ba94e74d555947142"
NEW_REQUIRED = REQUIRED | {"scripts/upgrade_daily_financial_research.py"}
NEW_SYMBOLS = (
    "600519.SH", "603259.SH", "300562.SZ", "603766.SH", "688612.SH", "688002.SH",
    "300750.SZ", "300308.SZ", "600918.SH", "301287.SZ", "601528.SH", "600398.SH",
    "300194.SZ", "601198.SH", "002602.SZ", "001227.SZ", "688600.SH", "002746.SZ",
    "603444.SH", "300871.SZ",
)


def upgraded_cron():
    original = cron_bytes()
    if checksum(original) != OLD_CRON_SHA:
        raise ValueError("baseline_template_changed")
    old_symbols = " ".join(f"--symbol {symbol}" for symbol in SYMBOLS).encode()
    new_symbols = " ".join(f"--symbol {symbol}" for symbol in NEW_SYMBOLS).encode()
    return original.replace(str(OLD_BUNDLE).encode(), str(NEW_BUNDLE).encode()).replace(
        old_symbols, new_symbols)


def inspect(old_cron_sha, old_manifest_sha, new_manifest_sha):
    if old_cron_sha != OLD_CRON_SHA or old_manifest_sha != OLD_MANIFEST_SHA:
        raise ValueError("unexpected_upgrade_baseline")
    validate_bundle(old_manifest_sha, bundle=OLD_BUNDLE)
    validate_bundle(new_manifest_sha, bundle=NEW_BUNDLE, required=NEW_REQUIRED)
    if TIMEZONE.read_text().strip() not in {"UTC", "Etc/UTC"}:
        raise ValueError("utc_required")
    directory(CRON.parent)
    meta = regular(CRON)
    if stat.S_IMODE(meta.st_mode) != 0o644:
        raise ValueError("unsafe_cron_permissions")
    current = CRON.read_bytes()
    wanted = upgraded_cron()
    # Only the reviewed bundle and explicitly authorized twenty-symbol cohort
    # change. All schedule, period, output and budget bytes remain fixed.
    if current == wanted:
        return current, current, True, meta
    if checksum(current) != old_cron_sha:
        raise ValueError("existing_cron_mismatch")
    return current, wanted, False, meta


def install(old_cron_sha, old_manifest_sha, new_manifest_sha, *, execute=False):
    old, new, installed, _ = inspect(old_cron_sha, old_manifest_sha, new_manifest_sha)
    result = {"status": "already_installed" if installed else "planned",
              "old_cron_sha256": old_cron_sha, "new_cron_sha256": checksum(new),
              "new_manifest_sha256": new_manifest_sha, "cron": new.decode(),
              "started_job": False}
    if not execute:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    # Same lock as the original installer; no lock or backup lives in cron.d.
    directory(BACKUPS, private=True)
    fd = os.open(BACKUPS / ".install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old, new, installed, original_meta = inspect(
            old_cron_sha, old_manifest_sha, new_manifest_sha)
        if installed:
            result["status"] = "already_installed"
            return result
        fd, backup = tempfile.mkstemp(prefix="before-v2-", suffix=".cron", dir=BACKUPS)
        with os.fdopen(fd, "wb") as stream:
            stream.write(old)
            stream.flush()
            os.fsync(stream.fileno())
        fd, pending = tempfile.mkstemp(prefix=".daily-financial-v2-", dir=CRON.parent)
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
            directory_fd = os.open(CRON.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
        result.update(status="upgraded", backup=backup)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-cron-sha256", required=True)
    parser.add_argument("--expected-old-manifest-sha256", required=True)
    parser.add_argument("--expected-new-manifest-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = install(args.expected_cron_sha256, args.expected_old_manifest_sha256,
                         args.expected_new_manifest_sha256, execute=args.execute)
    except Exception:
        print(json.dumps({"status": "error", "error": "daily_financial_upgrade_failed"}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
