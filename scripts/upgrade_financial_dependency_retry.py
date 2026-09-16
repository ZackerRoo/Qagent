#!/usr/bin/env python3
"""Guardedly install or roll back the bounded daily/forward retry schedules."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

import install_daily_financial_research as base
import upgrade_daily_financial_research_v3 as daily_v3
import upgrade_financial_forward_research as forward_base
import upgrade_financial_forward_research_v5 as forward_v5

DAILY_OLD_BUNDLE = Path("/opt/qagent-research/daily-financial-20260914-v3")
DAILY_NEW_BUNDLE = Path("/opt/qagent-research/daily-financial-20260914-v4")
FORWARD_OLD_BUNDLE = Path("/opt/qagent-research/financial-forward-20260914-v5")
FORWARD_NEW_BUNDLE = Path("/opt/qagent-research/financial-forward-20260914-v6")
DAILY_CRON = base.CRON
FORWARD_CRON = forward_base.FORWARD_CRON
BACKUPS = Path("/var/backups/qagent-financial-dependency-retry")
TIMEZONE = base.TIMEZONE
DAILY_OLD_CRON_SHA = "701215372bdb050190ab1df52910e74040ec6545b3691978575d7de823b19aa7"
FORWARD_OLD_CRON_SHA = "04040c5f48a42865c5f43289c5012fdfadd0dcd6f0f50500505397e783ad2016"
DAILY_OLD_MANIFEST_SHA = "1a6855501e14e4d54b2eca0f59afc618971135b932e74d612de835399307efbe"
FORWARD_OLD_MANIFEST_SHA = "386ea1dbc0c427a4693e0109161145c89b73df83a9820e6ffefa35d7333711ae"
HELPER = "scripts/upgrade_financial_dependency_retry.py"
# The helper is intentionally runnable from either independently installed new
# bundle. Keep both manifests on the same complete import/validation closure so
# neither bundle can borrow modules from a checkout through cwd or PYTHONPATH.
HELPER_REQUIRED = daily_v3.NEW_REQUIRED | forward_v5.NEW_REQUIRED | {HELPER}
DAILY_REQUIRED = HELPER_REQUIRED
FORWARD_REQUIRED = HELPER_REQUIRED
STATE_OLD = "old"
STATE_UPGRADE_PARTIAL = "daily_old_forward_new"
STATE_NEW = "new"


def old_daily_cron():
    value = daily_v3.upgraded_cron()
    if base.checksum(value) != DAILY_OLD_CRON_SHA:
        raise ValueError("daily_baseline_template_changed")
    return value


def old_forward_cron():
    value = forward_v5.upgraded_cron()
    if base.checksum(value) != FORWARD_OLD_CRON_SHA:
        raise ValueError("forward_baseline_template_changed")
    return value


def _command(value, bundle):
    lines = value.decode().splitlines()
    commands = [line for line in lines if str(bundle) in line]
    if len(commands) != 1:
        raise ValueError("baseline_cron_shape_changed")
    # Keep the cron user together with the command; only the five schedule
    # fields are replaced.
    return commands[0].split(None, 5)[5]


def daily_cron():
    command = _command(old_daily_cron(), DAILY_OLD_BUNDLE).replace(
        str(DAILY_OLD_BUNDLE), str(DAILY_NEW_BUNDLE), 1)
    schedules = ("40 8", "10 9", "40 9", "10 10", "40 10", "10 11")
    return ("# Financial daily retry window: 16:40-19:10 Asia/Shanghai; host timezone UTC.\n"
            "# Stops by daily trade-date idempotence after the first observed artifact.\n"
            "SHELL=/bin/sh\nPATH=/usr/bin:/bin\n" +
            "".join(f"{slot} * * 1-5 {command}\n" for slot in schedules)).encode()


def forward_cron():
    command = _command(old_forward_cron(), FORWARD_OLD_BUNDLE).replace(
        str(FORWARD_OLD_BUNDLE), str(FORWARD_NEW_BUNDLE), 1)
    schedules = ("37 11", "7 12", "37 12")
    return ("# Financial forward retry window: 19:37-20:37 Asia/Shanghai; host timezone UTC.\n"
            "# Sealing waits for same-day daily success; immutable signal paths prevent duplicates.\n"
            "SHELL=/bin/sh\nPATH=/usr/bin:/bin\n" +
            "".join(f"{slot} * * 1-5 {command}\n" for slot in schedules)).encode()


def _validate_manifests(daily_old, daily_new, forward_old, forward_new):
    if daily_old != DAILY_OLD_MANIFEST_SHA or forward_old != FORWARD_OLD_MANIFEST_SHA:
        raise ValueError("unexpected_upgrade_baseline")
    base.validate_bundle(daily_old, bundle=DAILY_OLD_BUNDLE, required=daily_v3.NEW_REQUIRED)
    base.validate_bundle(daily_new, bundle=DAILY_NEW_BUNDLE, required=DAILY_REQUIRED)
    forward_base.validate_forward_bundle(
        forward_old, bundle=FORWARD_OLD_BUNDLE, required=forward_v5.NEW_REQUIRED)
    forward_base.validate_forward_bundle(
        forward_new, bundle=FORWARD_NEW_BUNDLE, required=FORWARD_REQUIRED)


def inspect(daily_old, daily_new, forward_old, forward_new):
    _validate_manifests(daily_old, daily_new, forward_old, forward_new)
    if TIMEZONE.read_text().strip() not in {"UTC", "Etc/UTC"}:
        raise ValueError("utc_required")
    base.directory(DAILY_CRON.parent)
    daily_meta, forward_meta = base.regular(DAILY_CRON), base.regular(FORWARD_CRON)
    if stat.S_IMODE(daily_meta.st_mode) != 0o644 or stat.S_IMODE(forward_meta.st_mode) != 0o644:
        raise ValueError("unsafe_cron_permissions")
    current = (DAILY_CRON.read_bytes(), FORWARD_CRON.read_bytes())
    wanted = (daily_cron(), forward_cron())
    old = (old_daily_cron(), old_forward_cron())
    if current == old:
        state = STATE_OLD
    elif current == (old[0], wanted[1]):
        state = STATE_UPGRADE_PARTIAL
    elif current == wanted:
        state = STATE_NEW
    elif current == (wanted[0], old[1]):
        # A new producer must never run behind the old consumer. Refuse to
        # guess whether an operator intends upgrade or rollback.
        raise ValueError("unsafe_daily_new_forward_old_state")
    else:
        raise ValueError("existing_cron_mismatch")
    return current, wanted, state, (daily_meta, forward_meta)


def _write_atomic(path, raw):
    fd, pending = tempfile.mkstemp(prefix=".financial-dependency-retry-", dir=path.parent)
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


def install(daily_old, daily_new, forward_old, forward_new, *, execute=False):
    _, wanted, state, _ = inspect(daily_old, daily_new, forward_old, forward_new)
    status = {STATE_OLD: "planned", STATE_UPGRADE_PARTIAL: "resume_planned",
              STATE_NEW: "already_installed"}[state]
    result = {"status": status, "state": state,
              "daily_cron_sha256": base.checksum(wanted[0]),
              "forward_cron_sha256": base.checksum(wanted[1]),
              "started_job": False}
    if not execute or state == STATE_NEW:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    base.directory(BACKUPS, private=True)
    fd = os.open(BACKUPS / ".upgrade.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, wanted, state, _ = inspect(daily_old, daily_new, forward_old, forward_new)
        if state == STATE_NEW:
            result["status"] = "already_installed"
            result["state"] = state
            return result
        receipt = {"schema": "financial-dependency-retry-receipt-v1",
                   "daily_rollback_sha256": DAILY_OLD_CRON_SHA,
                   "forward_rollback_sha256": FORWARD_OLD_CRON_SHA,
                   "daily_after_sha256": base.checksum(wanted[0]),
                   "forward_after_sha256": base.checksum(wanted[1])}
        receipt_fd, receipt_path = tempfile.mkstemp(prefix="before-install-", suffix=".json", dir=BACKUPS)
        with os.fdopen(receipt_fd, "w") as stream:
            json.dump(receipt, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        # Consumer first. The only interrupted state is old producer with new
        # consumer, which is compatible and can be resumed by a later call.
        if state == STATE_OLD:
            _write_atomic(FORWARD_CRON, wanted[1])
        _write_atomic(DAILY_CRON, wanted[0])
        result.update(status="upgraded" if state == STATE_OLD else "resumed_upgrade",
                      state=STATE_NEW, backup=receipt_path)
        return result


def rollback(receipt_path, daily_old, daily_new, forward_old, forward_new, *, execute=False):
    receipt_path = Path(receipt_path)
    if (receipt_path.parent.resolve() != BACKUPS.resolve()
            or not receipt_path.name.startswith("before-install-")
            or stat.S_IMODE(base.regular(receipt_path).st_mode) != 0o600):
        raise ValueError("invalid_rollback_receipt")
    receipt = json.loads(receipt_path.read_text())
    _, wanted, state, _ = inspect(daily_old, daily_new, forward_old, forward_new)
    if receipt != {
        "schema": "financial-dependency-retry-receipt-v1",
        "daily_rollback_sha256": DAILY_OLD_CRON_SHA,
        "forward_rollback_sha256": FORWARD_OLD_CRON_SHA,
        "daily_after_sha256": base.checksum(wanted[0]),
        "forward_after_sha256": base.checksum(wanted[1]),
    }:
        raise ValueError("invalid_rollback_state")
    status = {STATE_NEW: "rollback_planned",
              STATE_UPGRADE_PARTIAL: "rollback_resume_planned",
              STATE_OLD: "already_rolled_back"}[state]
    result = {"status": status, "state": state, "started_job": False}
    if not execute or state == STATE_OLD:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    base.directory(BACKUPS, private=True)
    fd = os.open(BACKUPS / ".upgrade.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, wanted, state, _ = inspect(daily_old, daily_new, forward_old, forward_new)
        if state not in {STATE_NEW, STATE_UPGRADE_PARTIAL}:
            raise ValueError("cron_changed_during_rollback")
        # Producer first. If the consumer write fails, old producer with new
        # consumer remains compatible and a later rollback call can resume.
        if state == STATE_NEW:
            _write_atomic(DAILY_CRON, old_daily_cron())
        _write_atomic(FORWARD_CRON, old_forward_cron())
    result.update(status="rolled_back" if state == STATE_NEW else "resumed_rollback",
                  state=STATE_OLD)
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
        values = (args.expected_daily_old_manifest_sha256,
                  args.expected_daily_new_manifest_sha256,
                  args.expected_forward_old_manifest_sha256,
                  args.expected_forward_new_manifest_sha256)
        report = (rollback(args.rollback_receipt, *values, execute=args.execute)
                  if args.rollback_receipt else install(*values, execute=args.execute))
    except Exception:
        print(json.dumps({"status": "error", "error": "financial_dependency_retry_upgrade_failed"}))
        return 1
    print(json.dumps(report, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
