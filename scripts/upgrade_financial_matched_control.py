#!/usr/bin/env python3
"""Guardedly upgrade the financial research chain to matched-control v2."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

import upgrade_financial_dependency_retry as previous


DAILY_OLD_BUNDLE = Path("/opt/qagent-research/daily-financial-20260914-v4")
DAILY_NEW_BUNDLE = Path("/opt/qagent-research/daily-financial-20260914-v5")
FORWARD_OLD_BUNDLE = Path("/opt/qagent-research/financial-forward-20260914-v6")
FORWARD_NEW_BUNDLE = Path("/opt/qagent-research/financial-forward-20260914-v7")
DAILY_CRON = previous.DAILY_CRON
FORWARD_CRON = previous.FORWARD_CRON
BACKUPS = Path("/var/backups/qagent-financial-matched-control")
TIMEZONE = previous.TIMEZONE
DAILY_OLD_CRON_SHA = "ff8dfcc89647d9af4404d6f234de5583d87dc811a7c6eb962bd16fc3b77bf16d"
FORWARD_OLD_CRON_SHA = "c7425b014f6dfa37681e5f5e57e481b58d7c13a6d7f6e4f46f7689b75b1c5ecc"
DAILY_OLD_MANIFEST_SHA = "800d60b8fe3b83bcf33acd139a521744c9b739e6ac4751812deae123be4c0b6f"
FORWARD_OLD_MANIFEST_SHA = "f670920638b094522847fb868db8ed86da5d942ad4a4a63208338f37ee3c2ef1"
HELPER = "scripts/upgrade_financial_matched_control.py"
# Both independently installed target bundles contain the complete dependency
# closure.  The helper must never borrow modules from a repository checkout.
TARGET_REQUIRED = previous.HELPER_REQUIRED | {HELPER}
DAILY_REQUIRED = TARGET_REQUIRED
FORWARD_REQUIRED = TARGET_REQUIRED
STATE_OLD = "old"
STATE_UPGRADE_PARTIAL = "daily_old_forward_new"
STATE_NEW = "new"


def old_daily_cron():
    value = previous.daily_cron()
    if previous.base.checksum(value) != DAILY_OLD_CRON_SHA:
        raise ValueError("daily_baseline_template_changed")
    return value


def old_forward_cron():
    value = previous.forward_cron()
    if previous.base.checksum(value) != FORWARD_OLD_CRON_SHA:
        raise ValueError("forward_baseline_template_changed")
    return value


def _rewrite_bundle(value, old_bundle, new_bundle, expected_count):
    old_raw, new_raw = str(old_bundle).encode(), str(new_bundle).encode()
    if value.count(old_raw) != expected_count or value.count(new_raw):
        raise ValueError("baseline_cron_shape_changed")
    result = value.replace(old_raw, new_raw)
    if (result.count(new_raw) != expected_count or result.count(old_raw)
            or result.replace(new_raw, old_raw) != value):
        raise ValueError("unsafe_cron_rewrite")
    return result


def daily_cron():
    return _rewrite_bundle(old_daily_cron(), DAILY_OLD_BUNDLE, DAILY_NEW_BUNDLE, 6)


def forward_cron():
    return _rewrite_bundle(old_forward_cron(), FORWARD_OLD_BUNDLE, FORWARD_NEW_BUNDLE, 3)


def _validate_manifests(daily_old, daily_new, forward_old, forward_new):
    if daily_old != DAILY_OLD_MANIFEST_SHA or forward_old != FORWARD_OLD_MANIFEST_SHA:
        raise ValueError("unexpected_upgrade_baseline")
    previous.base.validate_bundle(
        daily_old, bundle=DAILY_OLD_BUNDLE, required=previous.DAILY_REQUIRED)
    previous.base.validate_bundle(
        daily_new, bundle=DAILY_NEW_BUNDLE, required=DAILY_REQUIRED)
    previous.forward_base.validate_forward_bundle(
        forward_old, bundle=FORWARD_OLD_BUNDLE, required=previous.FORWARD_REQUIRED)
    previous.forward_base.validate_forward_bundle(
        forward_new, bundle=FORWARD_NEW_BUNDLE, required=FORWARD_REQUIRED)


def inspect(daily_old, daily_new, forward_old, forward_new):
    _validate_manifests(daily_old, daily_new, forward_old, forward_new)
    if TIMEZONE.read_text().strip() not in {"UTC", "Etc/UTC"}:
        raise ValueError("utc_required")
    previous.base.directory(DAILY_CRON.parent)
    daily_meta = previous.base.regular(DAILY_CRON)
    forward_meta = previous.base.regular(FORWARD_CRON)
    if stat.S_IMODE(daily_meta.st_mode) != 0o644 or stat.S_IMODE(forward_meta.st_mode) != 0o644:
        raise ValueError("unsafe_cron_permissions")
    current = DAILY_CRON.read_bytes(), FORWARD_CRON.read_bytes()
    wanted = daily_cron(), forward_cron()
    old = old_daily_cron(), old_forward_cron()
    if current == old:
        state = STATE_OLD
    elif current == (old[0], wanted[1]):
        state = STATE_UPGRADE_PARTIAL
    elif current == wanted:
        state = STATE_NEW
    elif current == (wanted[0], old[1]):
        raise ValueError("unsafe_daily_new_forward_old_state")
    else:
        raise ValueError("existing_cron_mismatch")
    return current, wanted, state, (daily_meta, forward_meta)


def _write_atomic(path, raw):
    fd, pending = tempfile.mkstemp(prefix=".financial-matched-control-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            os.fchmod(stream.fileno(), 0o644)
            os.fchown(stream.fileno(), 0, 0)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def _receipt(wanted):
    return {
        "schema": "financial-matched-control-upgrade-receipt-v1",
        "daily_rollback_sha256": DAILY_OLD_CRON_SHA,
        "forward_rollback_sha256": FORWARD_OLD_CRON_SHA,
        "daily_after_sha256": previous.base.checksum(wanted[0]),
        "forward_after_sha256": previous.base.checksum(wanted[1]),
    }


def install(daily_old, daily_new, forward_old, forward_new, *, execute=False):
    _, wanted, state, _ = inspect(daily_old, daily_new, forward_old, forward_new)
    status = {STATE_OLD: "planned", STATE_UPGRADE_PARTIAL: "resume_planned",
              STATE_NEW: "already_installed"}[state]
    result = {
        "status": status,
        "state": state,
        "daily_cron_sha256": previous.base.checksum(wanted[0]),
        "forward_cron_sha256": previous.base.checksum(wanted[1]),
        "started_job": False,
    }
    if not execute or state == STATE_NEW:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    previous.base.directory(BACKUPS, private=True)
    fd = os.open(BACKUPS / ".upgrade.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, wanted, state, _ = inspect(daily_old, daily_new, forward_old, forward_new)
        if state == STATE_NEW:
            result.update(status="already_installed", state=state)
            return result
        receipt_fd, receipt_path = tempfile.mkstemp(
            prefix="before-install-", suffix=".json", dir=BACKUPS)
        with os.fdopen(receipt_fd, "w") as stream:
            json.dump(_receipt(wanted), stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        # Consumer first.  If the producer write is interrupted, the old
        # producer remains compatible with the new consumer and retry resumes.
        if state == STATE_OLD:
            _write_atomic(FORWARD_CRON, wanted[1])
        _write_atomic(DAILY_CRON, wanted[0])
        result.update(
            status="upgraded" if state == STATE_OLD else "resumed_upgrade",
            state=STATE_NEW,
            backup=receipt_path,
        )
        return result


def rollback(receipt_path, daily_old, daily_new, forward_old, forward_new, *, execute=False):
    receipt_path = Path(receipt_path)
    if (receipt_path.parent.resolve() != BACKUPS.resolve()
            or not receipt_path.name.startswith("before-install-")
            or stat.S_IMODE(previous.base.regular(receipt_path).st_mode) != 0o600):
        raise ValueError("invalid_rollback_receipt")
    receipt = json.loads(receipt_path.read_text())
    _, wanted, state, _ = inspect(daily_old, daily_new, forward_old, forward_new)
    if receipt != _receipt(wanted):
        raise ValueError("invalid_rollback_state")
    status = {STATE_NEW: "rollback_planned",
              STATE_UPGRADE_PARTIAL: "rollback_resume_planned",
              STATE_OLD: "already_rolled_back"}[state]
    result = {"status": status, "state": state, "started_job": False}
    if not execute or state == STATE_OLD:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    previous.base.directory(BACKUPS, private=True)
    fd = os.open(BACKUPS / ".upgrade.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, _, state, _ = inspect(daily_old, daily_new, forward_old, forward_new)
        if state not in {STATE_NEW, STATE_UPGRADE_PARTIAL}:
            raise ValueError("cron_changed_during_rollback")
        # Producer first.  The only interrupted rollback state is compatible:
        # old producer with new consumer.
        if state == STATE_NEW:
            _write_atomic(DAILY_CRON, old_daily_cron())
        _write_atomic(FORWARD_CRON, old_forward_cron())
    result.update(
        status="rolled_back" if state == STATE_NEW else "resumed_rollback",
        state=STATE_OLD,
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-daily-old-manifest-sha256", default=DAILY_OLD_MANIFEST_SHA)
    parser.add_argument("--expected-daily-new-manifest-sha256", required=True)
    parser.add_argument("--expected-forward-old-manifest-sha256", default=FORWARD_OLD_MANIFEST_SHA)
    parser.add_argument("--expected-forward-new-manifest-sha256", required=True)
    parser.add_argument("--rollback-receipt", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        values = (
            args.expected_daily_old_manifest_sha256,
            args.expected_daily_new_manifest_sha256,
            args.expected_forward_old_manifest_sha256,
            args.expected_forward_new_manifest_sha256,
        )
        report = (rollback(args.rollback_receipt, *values, execute=args.execute)
                  if args.rollback_receipt else install(*values, execute=args.execute))
    except Exception:
        print(json.dumps({"status": "error", "error": "financial_matched_control_upgrade_failed"}))
        return 1
    print(json.dumps(report, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
