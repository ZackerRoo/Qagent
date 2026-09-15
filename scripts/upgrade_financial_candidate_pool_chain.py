#!/usr/bin/env python3
"""Upgrade forward v5 before daily v3, compensating forward if daily fails."""
import argparse
import fcntl
import json
import os

import install_daily_financial_research as base
import upgrade_daily_financial_research_v3 as daily
import upgrade_financial_forward_research_v5 as forward


def upgrade(daily_manifest_sha, forward_manifest_sha, *, execute=False):
    if not execute:
        return {"status": "planned", "order": ["forward_v5", "daily_v3"],
                "forward": forward.install(forward.OLD_CRON_SHA, forward.OLD_MANIFEST_SHA,
                                           forward_manifest_sha),
                "daily": daily.install(daily.OLD_CRON_SHA, daily.OLD_MANIFEST_SHA,
                                       daily_manifest_sha),
                "started_job": False}
    base.directory(daily.BACKUPS, private=True)
    fd = os.open(daily.BACKUPS / ".candidate-pool-chain.lock",
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        forward_result = forward.install(
            forward.OLD_CRON_SHA, forward.OLD_MANIFEST_SHA,
            forward_manifest_sha, execute=True)
        try:
            daily_result = daily.install(daily.OLD_CRON_SHA, daily.OLD_MANIFEST_SHA,
                                         daily_manifest_sha, execute=True)
        except Exception:
            if forward_result.get("status") in {"upgraded", "already_installed"}:
                try:
                    forward.rollback(forward_result.get("backup"), forward.OLD_MANIFEST_SHA,
                                     forward_manifest_sha, execute=True)
                except Exception as rollback_error:
                    raise RuntimeError("daily_failed_and_forward_rollback_failed") from rollback_error
            raise
    return {"status": "upgraded", "order": ["forward_v5", "daily_v3"],
            "daily": daily_result, "forward": forward_result, "started_job": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-daily-v3-manifest-sha256", required=True)
    parser.add_argument("--expected-forward-v5-manifest-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = upgrade(args.expected_daily_v3_manifest_sha256,
                         args.expected_forward_v5_manifest_sha256, execute=args.execute)
    except Exception:
        print(json.dumps({"status": "error", "error": "candidate_pool_chain_upgrade_failed"}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
