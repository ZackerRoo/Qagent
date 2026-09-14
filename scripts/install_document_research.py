#!/usr/bin/env python3
"""Upgrade the existing research cron to the reviewed 20260914 bundle; never run a job.

Requires root and explicit expected SHA256 of the current cron and new Relay client.
Preserves schedule, two symbols and output path. Backs up the original cron outside
cron.d, changes only APP_DIR and ENV_FILE, and never modifies credentials or bundles.
"""
import argparse
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile

OLD_BUNDLE = "/opt/qagent-research/tushare-relay-20260913"
NEW_BUNDLE = "/opt/qagent-research/tushare-relay-20260914"
OLD_ENV = "/etc/qagent/relay-research.env"
NEW_ENV = "/etc/qagent/qagent.env"
CRON = Path("/etc/cron.d/qagent-relay-research")
BACKUPS = Path("/var/backups/qagent-document-research")
EXPECTED_JOB = (
    "30 8 * * 1-5 luozhenkun /bin/sh -c 'set -a; . \"" + OLD_ENV +
    "\"; set +a; exec \"/opt/qagent/current/backend/.venv/bin/python\" -B \"" +
    OLD_BUNDLE + "/scripts/archive_tushare_research.py\" --output-dir "
    '\"/var/lib/qagent-research/tushare-relay\" --symbols CN:000001,CN:600519 --limit 2 --timeout 180\''
)


def checksum(raw):
    return sha256(raw).hexdigest()


def plan(raw, expected_sha):
    if checksum(raw) != expected_sha:
        raise ValueError("cron_changed")
    text = raw.decode("utf-8")
    jobs = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if jobs != [EXPECTED_JOB] or text.count(OLD_BUNDLE) != 1 or text.count(OLD_ENV) != 1:
        raise ValueError("unexpected_cron")
    return text.replace(OLD_BUNDLE, NEW_BUNDLE).replace(OLD_ENV, NEW_ENV).encode()


def regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("unsafe_path")
    return path.stat()


def env_value(raw, key):
    matches = re.findall(r"^(?:export )?" + re.escape(key) + r"=(.*)$", raw, re.M)
    if len(matches) != 1:
        raise ValueError("invalid_environment")
    words = shlex.split(matches[0], comments=True)
    if len(words) != 1 or not words[0] or any(c in words[0] for c in ("$", "`", "\n")):
        raise ValueError("invalid_environment")
    return words[0]


def validate_environment(old, new):
    if env_value(old, "QAGENT_TUSHARE_RELAY_KEY") != env_value(new, "QAGENT_TUSHARE_RELAY_KEY"):
        raise ValueError("credential_mismatch")
    proxy_name = "HTTPS_PROXY" if re.search(r"^(?:export )?HTTPS_PROXY=", new, re.M) else "https_proxy"
    env_value(new, proxy_name)


def install(expected_cron_sha, expected_client_sha):
    if os.geteuid() != 0:
        raise ValueError("root_required")
    # Lock is outside cron.d, so it never becomes a cron candidate.
    if any(p.is_symlink() for p in (BACKUPS, *BACKUPS.parents)):
        raise ValueError("unsafe_path")
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    if BACKUPS.stat().st_uid != 0 or BACKUPS.stat().st_mode & 0o022:
        raise ValueError("unsafe_backup_directory")
    lock_path = BACKUPS / ".research-cron.lock"
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        meta = regular(CRON)
        if meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o644:
            raise ValueError("unsafe_cron_permissions")
        if Path("/etc/timezone").read_text().strip() not in ("Etc/UTC", "UTC"):
            raise ValueError("utc_required")
        for name in (OLD_ENV, NEW_ENV):
            env_meta = regular(Path(name))
            if env_meta.st_uid != 0 or stat.S_IMODE(env_meta.st_mode) != 0o640:
                raise ValueError("unsafe_environment_permissions")
        validate_environment(Path(OLD_ENV).read_text(), Path(NEW_ENV).read_text())
        bundle = Path(NEW_BUNDLE)
        for name in ("scripts/archive_tushare_research.py", "scripts/collect_tushare_research.py",
                     "backend/qagent/providers/tushare_relay.py"):
            regular(bundle / name)
        client_raw = (bundle / "backend/qagent/providers/tushare_relay.py").read_bytes()
        if checksum(client_raw) != expected_client_sha:
            raise ValueError("bundle_digest_mismatch")
        original = CRON.read_bytes()
        updated = plan(original, expected_cron_sha)
        fd, backup = tempfile.mkstemp(prefix="relay-research-before-20260914-", suffix=".cron", dir=BACKUPS)
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        fd, pending = tempfile.mkstemp(prefix=".relay-research-", dir=CRON.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(updated)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fchown(stream.fileno(), meta.st_uid, meta.st_gid)
                os.fsync(stream.fileno())
            if CRON.read_bytes() != original:
                raise ValueError("cron_changed")
            os.replace(pending, CRON)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
        return {"status": "updated", "backup": backup, "cron_sha256": checksum(updated)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-cron-sha256", required=True)
    parser.add_argument("--expected-client-sha256", required=True)
    args = parser.parse_args()
    try:
        for value in (args.expected_cron_sha256, args.expected_client_sha256):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("invalid_digest")
        print(json.dumps(install(args.expected_cron_sha256, args.expected_client_sha256)))
        return 0
    except Exception:
        print(json.dumps({"status": "error", "error": "research_cron_upgrade_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
