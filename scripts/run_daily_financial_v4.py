#!/usr/bin/env python3
"""Fixed immutable bundle wiring for the prospective v4 research consumer."""
from pathlib import Path

from run_daily_financial_same_day import main


DAILY_BUNDLE = Path("/opt/qagent-research/daily-financial-20260924-v10")
FORWARD_BUNDLE = Path("/opt/qagent-research/financial-forward-20260924-v12")


if __name__ == "__main__":
    raise SystemExit(main(daily_bundle=DAILY_BUNDLE, forward_bundle=FORWARD_BUNDLE))
