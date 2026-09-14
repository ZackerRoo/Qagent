#!/usr/bin/env python3
"""Plan or explicitly install the independent weekday financial research cron."""
import argparse
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile

BUNDLE = Path("/opt/qagent-research/daily-financial-20260914")
CRON = Path("/etc/cron.d/qagent-daily-financial-research")
BACKUPS = Path("/var/backups/qagent-daily-financial-research")
TIMEZONE = Path("/etc/timezone")
MANIFEST = "manifest.json"
REQUIRED = {
    "scripts/install_daily_financial_research.py",
    "scripts/collect_daily_documented_research.py", "scripts/rank_financial_candidate.py",
    "scripts/collect_documented_research.py", "scripts/research_financial_enrichment.py",
    "scripts/research_cashflow_quality.py", "scripts/rank_g2_consensus.py",
    "scripts/compare_g2_selections.py", "backend/qagent/providers/datahubco.py",
    "backend/qagent/providers/tushare_relay.py",
}
SYMBOLS = ("600519.SH", "603259.SH", "300562.SZ", "603766.SH", "688612.SH",
           "688002.SH", "300750.SZ", "300308.SZ")


def checksum(raw):
    return sha256(raw).hexdigest()


def regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("unsafe_path")
    meta = path.stat()
    if meta.st_uid != 0 or meta.st_mode & 0o022:
        raise ValueError("unsafe_file_permissions")
    return meta


def directory(path, *, private=False):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_dir():
        raise ValueError("unsafe_directory")
    meta = path.stat()
    if meta.st_uid != 0 or meta.st_mode & (0o077 if private else 0o022):
        raise ValueError("unsafe_directory_permissions")


def validate_bundle(expected_sha, *, bundle=None, required=None):
    bundle = BUNDLE if bundle is None else bundle
    required = REQUIRED if required is None else required
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("invalid_digest")
    directory(bundle)
    regular(bundle / MANIFEST)
    raw = (bundle / MANIFEST).read_bytes()
    if checksum(raw) != expected_sha:
        raise ValueError("manifest_changed")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "files"}:
        raise ValueError("invalid_manifest")
    files = manifest["files"]
    if manifest["schema"] != "daily-financial-bundle-v1" or not isinstance(files, dict):
        raise ValueError("invalid_manifest")
    if not required <= set(files) or len(files) > 100:
        raise ValueError("missing_bundle_dependencies")
    for name, digest in files.items():
        path = PurePosixPath(name)
        if (not isinstance(name, str) or path.is_absolute() or ".." in path.parts
                or str(path) != name or name == MANIFEST
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("invalid_manifest_path")
        regular(bundle / name)
        if checksum((bundle / name).read_bytes()) != digest:
            raise ValueError("bundle_changed")
    actual = set()
    for path in bundle.rglob("*"):
        if path.is_dir():
            directory(path)
        else:
            regular(path)
            actual.add(path.relative_to(bundle).as_posix())
    if actual != set(files) | {MANIFEST}:
        raise ValueError("unmanifested_bundle_files")


def cron_bytes():
    symbols = " ".join(f"--symbol {symbol}" for symbol in SYMBOLS)
    return (
        "# Independent financial research; host timezone must be UTC.\n"
        "SHELL=/bin/sh\nPATH=/usr/bin:/bin\n"
        "40 8 * * 1-5 luozhenkun /opt/qagent/current/backend/.venv/bin/python -B "
        f"{BUNDLE}/scripts/collect_daily_documented_research.py "
        "--base-url http://127.0.0.1:8000 --source datahubco "
        f"{symbols} --period 20260630 --today-close --budget-seconds 600 "
        "--output-dir /var/lib/qagent-research/daily-financial\n"
    ).encode()


def inspect(expected_sha):
    validate_bundle(expected_sha)
    if TIMEZONE.read_text().strip() not in {"UTC", "Etc/UTC"}:
        raise ValueError("utc_required")
    directory(CRON.parent)
    wanted = cron_bytes()
    if CRON.exists() or CRON.is_symlink():
        meta = regular(CRON)
        if stat.S_IMODE(meta.st_mode) != 0o644 or CRON.read_bytes() != wanted:
            raise ValueError("existing_cron_mismatch")
        return wanted, True
    return wanted, False


def install(expected_sha, *, execute=False):
    wanted, exists = inspect(expected_sha)
    result = {"status": "already_installed" if exists else "planned",
              "cron_sha256": checksum(wanted), "manifest_sha256": expected_sha,
              "cron": wanted.decode(), "started_job": False}
    if not execute:
        return result
    if os.geteuid() != 0:
        raise ValueError("root_required")
    directory(BACKUPS.parent)
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    directory(BACKUPS, private=True)
    lock_path = BACKUPS / ".install.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        wanted, exists = inspect(expected_sha)
        if exists:
            result["status"] = "already_installed"
            return result
        # Private receipt records that the target was absent, for explicit rollback.
        fd, receipt = tempfile.mkstemp(prefix="before-install-", suffix=".json", dir=BACKUPS)
        with os.fdopen(fd, "w") as stream:
            json.dump({"cron_path": str(CRON), "previously_absent": True,
                       "installed_sha256": checksum(wanted), "manifest_sha256": expected_sha}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        fd, pending = tempfile.mkstemp(prefix=".daily-financial-", dir=CRON.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(wanted)
                os.fchmod(stream.fileno(), 0o644)
                os.fchown(stream.fileno(), 0, 0)
                stream.flush()
                os.fsync(stream.fileno())
            validate_bundle(expected_sha)
            # Exclusive hard-link publication is atomic and cannot overwrite a
            # target concurrently created by another installer or an operator.
            os.link(pending, CRON, follow_symlinks=False)
            directory_fd = os.open(CRON.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            os.unlink(pending)
        result.update(status="installed", backup=receipt)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = install(args.expected_manifest_sha256, execute=args.execute)
    except Exception:
        print(json.dumps({"status": "error", "error": "daily_financial_install_failed"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
