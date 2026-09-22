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
    "scripts/run_daily_financial_same_day.py",
}
BASELINE_ARGUMENTS = (
    " --daily-baseline-source-dir /var/lib/qagent-research/g2-forward-sources"
    " --daily-baseline-frozen-dir /var/lib/qagent-research/g2-frozen-v1"
    " --daily-baseline-rank-dir /var/lib/qagent-research/financial-daily-ranks"
)
DAILY_V7_BUNDLE = Path("/opt/qagent-research/daily-financial-20260918-v7")
FORWARD_V9_BUNDLE = Path("/opt/qagent-research/financial-forward-20260918-v9")


def _v7_v9_crons():
    """Rebuild the only accepted production v7/v9 cron templates.

    The inherited late-scan engine produces v6/v8 templates.  v7/v9 added the
    frozen-industry flag and daily-baseline arguments, so treating that older
    engine output as the installed state would both reject production and risk
    generating the wrong rollback target.
    """
    previous = baseline.configured_engine()
    daily = previous.daily_cron()
    forward = previous.forward_cron()
    if (previous.previous.base.checksum(daily) != "340f8484b34d92a0680bc7ff56519e9cbd2c03239df1632a60b96660169fdda0"
            or previous.previous.base.checksum(forward) != "ac3898d5f73f28dcb10e429dc017368508507d606a05917a29d164fccd703574"):
        raise ValueError("v7_v9_baseline_template_changed")
    daily = previous._rewrite_bundle(daily, previous.DAILY_NEW_BUNDLE, DAILY_V7_BUNDLE, 13)
    daily = previous._rewrite_bundle(daily, previous.FORWARD_NEW_BUNDLE, FORWARD_V9_BUNDLE, 13)
    marker = b" --bounded-same-day && "
    if daily.count(marker) != 13:
        raise ValueError("unexpected_v7_daily_command_shape")
    daily = daily.replace(marker, b" --bounded-same-day --daily-frozen-industry && ")

    def add_arguments(raw, expected):
        lines = raw.decode().splitlines()
        matches = [i for i, line in enumerate(lines) if "run_financial_forward_research.py" in line]
        if len(matches) != expected or "--daily-baseline-" in raw.decode():
            raise ValueError("unexpected_v7_forward_command_shape")
        for index in matches:
            lines[index] += BASELINE_ARGUMENTS
        return ("\n".join(lines) + "\n").encode()

    return add_arguments(daily, 13), add_arguments(
        previous._rewrite_bundle(forward, previous.FORWARD_NEW_BUNDLE, FORWARD_V9_BUNDLE, 4), 4)


def configured_engine():
    engine = baseline.configured_engine()
    engine.DAILY_OLD_BUNDLE = DAILY_V7_BUNDLE
    engine.FORWARD_OLD_BUNDLE = FORWARD_V9_BUNDLE
    engine.DAILY_NEW_BUNDLE = Path("/opt/qagent-research/daily-financial-20260922-v9")
    engine.FORWARD_NEW_BUNDLE = Path("/opt/qagent-research/financial-forward-20260922-v11")
    engine.DAILY_OLD_CRON_SHA = "04e0023e784aad93a006011a16f6e7061f2bd1adc6bf8c1e78594542c4827e5f"
    engine.FORWARD_OLD_CRON_SHA = "f03ae7a5e252d95191967d85d79b956aed6d00d0bcc973e09e8f48ce8ad7b6ef"
    engine.DAILY_OLD_MANIFEST_SHA = "3edf4fa5b867bfe2cd452f2f4a8504bec47fe458bfa697abf14154a971daf793"
    engine.FORWARD_OLD_MANIFEST_SHA = "b09e7e639493eb8cda5e9a2da321956dded47bbbb7a202c4342c93de149dd097"
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

    engine.old_daily_cron = lambda: pinned_template(lambda: _v7_v9_crons()[0], engine.DAILY_OLD_CRON_SHA)
    engine.old_forward_cron = lambda: pinned_template(lambda: _v7_v9_crons()[1], engine.FORWARD_OLD_CRON_SHA)

    def add_forward_arguments(raw, expected):
        lines = raw.decode().splitlines()
        matches = [i for i, line in enumerate(lines) if "run_financial_forward_research.py" in line]
        if len(matches) != expected or "--daily-baseline-" in raw.decode():
            raise ValueError("unexpected_forward_command_shape")
        for i in matches:
            lines[i] += BASELINE_ARGUMENTS
        return ("\n".join(lines) + "\n").encode()

    def daily_cron():
        raw = engine.old_daily_cron()
        lines = raw.decode().splitlines()
        matches = [i for i, line in enumerate(lines) if "collect_daily_documented_research.py" in line]
        if len(matches) != 13:
            raise ValueError("unexpected_daily_command_shape")
        wrapper = ("/opt/qagent/current/backend/.venv/bin/python -B "
                   f"{engine.DAILY_NEW_BUNDLE}/scripts/run_daily_financial_same_day.py")
        for i in matches:
            prefix = lines[i].split(None, 6)[:6]
            if len(prefix) != 6 or prefix[5] != "luozhenkun":
                raise ValueError("unexpected_daily_command_shape")
            lines[i] = " ".join([*prefix, wrapper])
        return ("\n".join(lines) + "\n").encode()

    def forward_cron():
        raw = engine._rewrite_bundle(engine.old_forward_cron(), engine.FORWARD_OLD_BUNDLE,
                                     engine.FORWARD_NEW_BUNDLE, 4)
        # v9 already carries the reviewed daily-baseline arguments.  Adding
        # them again would both change its semantics and reject the real v9
        # production baseline during a safe preview.
        return raw

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
