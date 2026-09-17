#!/usr/bin/env python3
"""Bounded late-scan schedule upgrade using the existing guarded upgrade engine."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import upgrade_financial_matched_control as baseline


HELPER = "scripts/upgrade_financial_late_scan.py"
DAILY_REQUIRED = FORWARD_REQUIRED = baseline.TARGET_REQUIRED | {HELPER}


def configured_engine():
    # An isolated module instance keeps all existing install/rollback checks and
    # avoids mutating the imported historical baseline (or copying its engine).
    spec = importlib.util.spec_from_file_location("_late_scan_upgrade_engine", baseline.__file__)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.DAILY_OLD_BUNDLE = baseline.DAILY_NEW_BUNDLE
    engine.FORWARD_OLD_BUNDLE = baseline.FORWARD_NEW_BUNDLE
    engine.DAILY_NEW_BUNDLE = Path("/opt/qagent-research/daily-financial-20260917-v6")
    engine.FORWARD_NEW_BUNDLE = Path("/opt/qagent-research/financial-forward-20260917-v8")
    engine.DAILY_OLD_CRON_SHA = "45ee481081dd3e3ba06f6df02bbd55f07944d1b896ff54e08f8ffcff10ff62f8"
    engine.FORWARD_OLD_CRON_SHA = "1305eb71acc82ecb3730433044f218815c40ec23aff004a602db3f622cc9dec3"
    engine.DAILY_OLD_MANIFEST_SHA = "551edc0ce38c5e7892ab3158ef4bb337db9f41b6bbb265853aff91fd2e68bf2f"
    engine.FORWARD_OLD_MANIFEST_SHA = "ec9dfd26617eda1b037d433b5eb5f0e5f51e0526786f9b4c044ab72c64a8186e"
    engine.BACKUPS = Path("/var/backups/qagent-financial-late-scan")
    engine.DAILY_REQUIRED = engine.FORWARD_REQUIRED = DAILY_REQUIRED

    def validate_manifests(daily_old, daily_new, forward_old, forward_new):
        if (daily_old != engine.DAILY_OLD_MANIFEST_SHA
                or forward_old != engine.FORWARD_OLD_MANIFEST_SHA):
            raise ValueError("unexpected_upgrade_baseline")
        for old, new, old_bundle, new_bundle, validator in (
            (daily_old, daily_new, engine.DAILY_OLD_BUNDLE, engine.DAILY_NEW_BUNDLE,
             engine.previous.base.validate_bundle),
            (forward_old, forward_new, engine.FORWARD_OLD_BUNDLE, engine.FORWARD_NEW_BUNDLE,
             engine.previous.forward_base.validate_forward_bundle),
        ):
            validator(old, bundle=old_bundle, required=baseline.TARGET_REQUIRED)
            validator(new, bundle=new_bundle, required=DAILY_REQUIRED)

    engine._validate_manifests = validate_manifests

    def pinned_template(factory, expected):
        raw = factory()
        if engine.previous.base.checksum(raw) != expected:
            raise ValueError("late_scan_baseline_changed")
        return raw

    engine.old_daily_cron = lambda: pinned_template(baseline.daily_cron, engine.DAILY_OLD_CRON_SHA)
    engine.old_forward_cron = lambda: pinned_template(baseline.forward_cron, engine.FORWARD_OLD_CRON_SHA)

    def command(raw, old, new):
        lines = [line for line in raw.decode().splitlines() if str(old) in line]
        commands = {line.split(None, 5)[5] for line in lines}
        if len(commands) != 1:
            raise ValueError("unexpected_baseline_commands")
        return commands.pop().replace(str(old), str(new))

    def forward_command():
        return command(engine.old_forward_cron(), engine.FORWARD_OLD_BUNDLE, engine.FORWARD_NEW_BUNDLE)

    def daily_cron():
        daily = command(engine.old_daily_cron(), engine.DAILY_OLD_BUNDLE, engine.DAILY_NEW_BUNDLE)
        user, forward = forward_command().split(None, 1)
        if daily.split(None, 1)[0] != user:
            raise ValueError("cron_user_mismatch")
        daily += " --bounded-same-day && " + forward
        slots = range(8 * 60 + 40, 14 * 60 + 41, 30)
        return ("# Same-day dependency polls 16:40-22:40 Shanghai; two financial batches maximum.\n"
                "SHELL=/bin/sh\nPATH=/usr/bin:/bin\n" + "".join(
                    f"{minute % 60} {minute // 60} * * 1-5 {daily}\n" for minute in slots)).encode()

    def forward_cron():
        # Independent maturity evaluation remains available without a fresh daily.
        return ("# Independent forward recovery/evaluation; host timezone UTC.\n"
                "SHELL=/bin/sh\nPATH=/usr/bin:/bin\n" + "".join(
                    f"{slot} * * 1-5 {forward_command()}\n"
                    for slot in ("37 11", "7 12", "37 12", "7 15"))).encode()

    engine.daily_cron = daily_cron
    engine.forward_cron = forward_cron
    return engine


def package_bundles(destination):
    """Reproducible tar artifacts: exact allowlist, root-owned immutable files."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    files = {name: (root / name).read_bytes() for name in sorted(DAILY_REQUIRED)}
    checksum = baseline.previous.base.checksum
    engine = configured_engine()
    results = []
    for bundle, schema in ((engine.DAILY_NEW_BUNDLE, "daily-financial-bundle-v1"),
                           (engine.FORWARD_NEW_BUNDLE, "financial-forward-bundle-v1")):
        manifest = (json.dumps({"schema": schema, "files": {
            name: checksum(raw) for name, raw in files.items()}}, sort_keys=True,
            separators=(",", ":")) + "\n").encode()
        path = destination / (bundle.name + ".tar")
        with path.open("xb") as stream, tarfile.open(fileobj=stream, mode="w") as archive:
            for name, raw in sorted({**files, "manifest.json": manifest}.items()):
                info = tarfile.TarInfo(name)
                info.mode, info.size = 0o444, len(raw)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = "root"
                archive.addfile(info, io.BytesIO(raw))
        results.append({"bundle": str(bundle), "tar": str(path),
                        "manifest_sha256": checksum(manifest),
                        "tar_sha256": checksum(path.read_bytes())})
    return results


if __name__ == "__main__":
    raise SystemExit(configured_engine().main())
