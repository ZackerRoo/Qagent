#!/usr/bin/env python3
"""Install prospective v4 research bundles through the existing guarded engine."""
import importlib.util
from pathlib import Path

import upgrade_financial_daily_dependencies as baseline
from run_daily_financial_v4 import DAILY_BUNDLE, FORWARD_BUNDLE


DAILY_REQUIRED = FORWARD_REQUIRED = baseline.DAILY_REQUIRED | {
    "scripts/upgrade_financial_global_control.py", "scripts/run_daily_financial_v4.py"}


def configured_engine():
    previous = baseline.configured_engine()
    engine = baseline.configured_engine()
    engine.DAILY_OLD_BUNDLE = previous.DAILY_NEW_BUNDLE
    engine.FORWARD_OLD_BUNDLE = previous.FORWARD_NEW_BUNDLE
    engine.DAILY_NEW_BUNDLE = DAILY_BUNDLE
    engine.FORWARD_NEW_BUNDLE = FORWARD_BUNDLE
    engine.DAILY_OLD_CRON_SHA = "0b7bc0d04f265dd3201b5c5924bba85d3b417e59ca3cace8b8d77e896b4d7f72"
    engine.FORWARD_OLD_CRON_SHA = "7026385dd81cbe8752cc819b0d6f7cfe5318d732a5b1cda594d1726b3f82d4bb"
    engine.DAILY_OLD_MANIFEST_SHA = "e64db44c9a06f6250e74b864ebbf0997cc295ad2b54e5e61fe8dbb102bd923d5"
    engine.FORWARD_OLD_MANIFEST_SHA = "9a3c9b2720cb4941fe951a3d8e90113885d004bb2e5075aa41a42c355adcad27"
    engine.BACKUPS = Path("/var/backups/qagent-financial-global-control")
    engine.DAILY_REQUIRED = engine.FORWARD_REQUIRED = DAILY_REQUIRED

    def pinned_template(factory, expected):
        raw = factory()
        if engine.previous.base.checksum(raw) != expected:
            raise ValueError("global_control_baseline_changed")
        return raw

    engine.old_daily_cron = lambda: pinned_template(previous.daily_cron, engine.DAILY_OLD_CRON_SHA)
    engine.old_forward_cron = lambda: pinned_template(previous.forward_cron, engine.FORWARD_OLD_CRON_SHA)

    def daily_cron():
        raw = engine._rewrite_bundle(engine.old_daily_cron(), engine.DAILY_OLD_BUNDLE,
                                     engine.DAILY_NEW_BUNDLE, 13)
        old = b"/scripts/run_daily_financial_same_day.py"
        if raw.count(old) != 13:
            raise ValueError("unexpected_daily_wrapper_shape")
        return raw.replace(old, b"/scripts/run_daily_financial_v4.py")

    engine.daily_cron = daily_cron
    engine.forward_cron = lambda: engine._rewrite_bundle(
        engine.old_forward_cron(), engine.FORWARD_OLD_BUNDLE, engine.FORWARD_NEW_BUNDLE, 4)

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
            validator(new, bundle=new_bundle, required=DAILY_REQUIRED)

    engine._validate_manifests = validate_manifests
    return engine


def package_bundles(destination):
    spec = importlib.util.spec_from_file_location("_global_control_packager", baseline.baseline.__file__)
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    packager.DAILY_REQUIRED = DAILY_REQUIRED
    packager.configured_engine = configured_engine
    return packager.package_bundles(destination)


if __name__ == "__main__":
    raise SystemExit(configured_engine().main())
