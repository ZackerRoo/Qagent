#!/usr/bin/env python3
"""Guardedly enable peer-control v5 in the existing financial research chain."""
import importlib.util
import os
from pathlib import Path

import upgrade_financial_global_control as baseline
from run_daily_financial_v5 import DAILY_BUNDLE, FORWARD_BUNDLE

QAGENT_HOME = Path(os.environ.get("QAGENT_HOME", "/home/luozhenkun/qagent"))


REQUIRED = baseline.DAILY_REQUIRED | {
    "scripts/financial_peer_evidence.py",
    "scripts/run_daily_financial_v5.py",
    "scripts/upgrade_financial_peer_control.py",
}


def configured_engine():
    previous = baseline.configured_engine()
    engine = baseline.configured_engine()
    engine.DAILY_OLD_BUNDLE = previous.DAILY_NEW_BUNDLE
    engine.FORWARD_OLD_BUNDLE = previous.FORWARD_NEW_BUNDLE
    engine.DAILY_NEW_BUNDLE = DAILY_BUNDLE
    engine.FORWARD_NEW_BUNDLE = FORWARD_BUNDLE
    engine.DAILY_OLD_CRON_SHA = "ba04633a4d19eac7255ef5b7af83a387a1d8ad424aef40d0651de96bfc01eb91"
    engine.FORWARD_OLD_CRON_SHA = "aad4e58bdbf27f4d914c713ab1908432d358523b479533bdfe458f1a62e036e2"
    engine.DAILY_OLD_MANIFEST_SHA = "08b2d4ede0d46d26586e21001c904ed95b20762ff9d3cfe0bf5abf38a1def1f8"
    engine.FORWARD_OLD_MANIFEST_SHA = "e21a920aa9bfe47213568b346a1aeb26f1cdf31a820bd66481c06b77f84d35aa"
    engine.BACKUPS = QAGENT_HOME / "backups/financial-peer-control"
    engine.DAILY_REQUIRED = engine.FORWARD_REQUIRED = REQUIRED

    def pinned_template(factory, expected):
        raw = factory()
        if engine.previous.base.checksum(raw) != expected:
            raise ValueError("peer_control_baseline_changed")
        return raw

    engine.old_daily_cron = lambda: pinned_template(previous.daily_cron, engine.DAILY_OLD_CRON_SHA)
    engine.old_forward_cron = lambda: pinned_template(previous.forward_cron, engine.FORWARD_OLD_CRON_SHA)

    def daily_cron():
        raw = engine._rewrite_bundle(engine.old_daily_cron(), engine.DAILY_OLD_BUNDLE,
                                     engine.DAILY_NEW_BUNDLE, 13)
        old = b"/scripts/run_daily_financial_v4.py"
        if raw.count(old) != 13:
            raise ValueError("unexpected_daily_wrapper_shape")
        return (raw.replace(old, b"/scripts/run_daily_financial_v5.py")
                   .replace(b"/opt/qagent/current", str(QAGENT_HOME / "current").encode())
                   .replace(b"/var/lib/qagent-research", str(QAGENT_HOME / "research-data").encode())
                   .replace(b"/var/lib/qagent", str(QAGENT_HOME / "state").encode()))

    engine.daily_cron = daily_cron
    def forward_cron():
        raw = engine._rewrite_bundle(
            engine.old_forward_cron(), engine.FORWARD_OLD_BUNDLE, engine.FORWARD_NEW_BUNDLE, 4)
        return (raw.replace(b"/opt/qagent/current", str(QAGENT_HOME / "current").encode())
                   .replace(b"/var/lib/qagent-research", str(QAGENT_HOME / "research-data").encode())
                   .replace(b"/var/lib/qagent", str(QAGENT_HOME / "state").encode()))

    engine.forward_cron = forward_cron

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
            validator(old, bundle=old_bundle, required=baseline.DAILY_REQUIRED)
            validator(new, bundle=new_bundle, required=REQUIRED)

    engine._validate_manifests = validate_manifests
    return engine


def package_bundles(destination):
    spec = importlib.util.spec_from_file_location("_peer_control_packager", baseline.baseline.baseline.__file__)
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    packager.DAILY_REQUIRED = REQUIRED
    packager.configured_engine = configured_engine
    return packager.package_bundles(destination)


if __name__ == "__main__":
    raise SystemExit(configured_engine().main())
