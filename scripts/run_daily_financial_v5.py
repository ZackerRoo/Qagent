#!/usr/bin/env python3
"""Pinned peer-control research entrypoint; the collector remains opt-in."""
from pathlib import Path

from run_daily_financial_same_day import main


DAILY_BUNDLE = Path("/opt/qagent-research/daily-financial-20260924-v11")
FORWARD_BUNDLE = Path("/opt/qagent-research/financial-forward-20260924-v13")


if __name__ == "__main__":
    raise SystemExit(main(daily_bundle=DAILY_BUNDLE, forward_bundle=FORWARD_BUNDLE,
                          peer_controls=True))
