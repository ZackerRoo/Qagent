#!/usr/bin/env python3
"""Guarded daily-industry/frozen-rank upgrade; original G2 schedule is untouched.

Before installing, the operator must verify that the existing loopback research
API exposes the read-only datahubco stock_basic route. This helper only swaps
the two research cron files; it does not deploy the API or start any jobs.
"""
import importlib.util
from pathlib import Path

import upgrade_financial_late_scan as baseline


HELPER = "scripts/upgrade_financial_daily_dependencies.py"
DAILY_REQUIRED = FORWARD_REQUIRED = baseline.DAILY_REQUIRED | {
    HELPER, "scripts/financial_daily_baseline.py", "scripts/financial_industry_evidence.py",
}
BASELINE_ARGUMENTS = (
    " --daily-baseline-source-dir /var/lib/qagent-research/g2-forward-sources"
    " --daily-baseline-frozen-dir /var/lib/qagent-research/g2-frozen-v1"
    " --daily-baseline-rank-dir /var/lib/qagent-research/financial-daily-ranks"
)


def configured_engine():
    engine = baseline.configured_engine()
    old = baseline.configured_engine()
    engine.DAILY_OLD_BUNDLE = old.DAILY_NEW_BUNDLE
    engine.FORWARD_OLD_BUNDLE = old.FORWARD_NEW_BUNDLE
    engine.DAILY_NEW_BUNDLE = Path("/opt/qagent-research/daily-financial-20260918-v7")
    engine.FORWARD_NEW_BUNDLE = Path("/opt/qagent-research/financial-forward-20260918-v9")
    engine.DAILY_OLD_CRON_SHA = "340f8484b34d92a0680bc7ff56519e9cbd2c03239df1632a60b96660169fdda0"
    engine.FORWARD_OLD_CRON_SHA = "ac3898d5f73f28dcb10e429dc017368508507d606a05917a29d164fccd703574"
    engine.DAILY_OLD_MANIFEST_SHA = "1e942320d572f2a61c5e0f155c3c2dcb2a4e0e1589b7c77f7af854f275cb8d55"
    engine.FORWARD_OLD_MANIFEST_SHA = "92717c3ff89a50efe27a627a40d2549a6ab585989330c9111b045dfc45e6838b"
    engine.BACKUPS = Path("/var/backups/qagent-financial-daily-dependencies")
    engine.DAILY_REQUIRED = engine.FORWARD_REQUIRED = DAILY_REQUIRED

    def validate_manifests(daily_old, daily_new, forward_old, forward_new):
        if (daily_old != engine.DAILY_OLD_MANIFEST_SHA
                or forward_old != engine.FORWARD_OLD_MANIFEST_SHA):
            raise ValueError("unexpected_upgrade_baseline")
        for old_hash, new_hash, old_bundle, new_bundle, validator in (
            (daily_old, daily_new, engine.DAILY_OLD_BUNDLE, engine.DAILY_NEW_BUNDLE,
             engine.previous.base.validate_bundle),
            (forward_old, forward_new, engine.FORWARD_OLD_BUNDLE, engine.FORWARD_NEW_BUNDLE,
             engine.previous.forward_base.validate_forward_bundle),
        ):
            validator(old_hash, bundle=old_bundle, required=baseline.DAILY_REQUIRED)
            validator(new_hash, bundle=new_bundle, required=DAILY_REQUIRED)

    engine._validate_manifests = validate_manifests

    def pinned_template(factory, expected):
        raw = factory()
        if engine.previous.base.checksum(raw) != expected:
            raise ValueError("daily_dependencies_baseline_changed")
        return raw

    engine.old_daily_cron = lambda: pinned_template(old.daily_cron, engine.DAILY_OLD_CRON_SHA)
    engine.old_forward_cron = lambda: pinned_template(old.forward_cron, engine.FORWARD_OLD_CRON_SHA)

    def add_forward_arguments(raw, expected):
        lines = raw.decode().splitlines()
        matches = [i for i, line in enumerate(lines) if "run_financial_forward_research.py" in line]
        if len(matches) != expected or "--daily-baseline-" in raw.decode():
            raise ValueError("unexpected_forward_command_shape")
        for i in matches:
            lines[i] += BASELINE_ARGUMENTS
        return ("\n".join(lines) + "\n").encode()

    def daily_cron():
        raw = engine._rewrite_bundle(engine.old_daily_cron(), engine.DAILY_OLD_BUNDLE,
                                     engine.DAILY_NEW_BUNDLE, 13)
        raw = engine._rewrite_bundle(raw, engine.FORWARD_OLD_BUNDLE, engine.FORWARD_NEW_BUNDLE, 13)
        marker = b" --bounded-same-day && "
        if raw.count(marker) != 13:
            raise ValueError("unexpected_daily_command_shape")
        raw = raw.replace(marker, b" --bounded-same-day --daily-frozen-industry && ")
        return add_forward_arguments(raw, 13)

    def forward_cron():
        raw = engine._rewrite_bundle(engine.old_forward_cron(), engine.FORWARD_OLD_BUNDLE,
                                     engine.FORWARD_NEW_BUNDLE, 4)
        return add_forward_arguments(raw, 4)

    engine.daily_cron, engine.forward_cron = daily_cron, forward_cron
    return engine


def package_bundles(destination):
    # Reuse the deterministic packager in an isolated module instance, without
    # modifying the previous upgrade helper's constants or behavior.
    spec = importlib.util.spec_from_file_location("_daily_dependency_packager", baseline.__file__)
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    packager.DAILY_REQUIRED = DAILY_REQUIRED
    packager.configured_engine = configured_engine
    return packager.package_bundles(destination)


if __name__ == "__main__":
    raise SystemExit(configured_engine().main())
