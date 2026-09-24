#!/usr/bin/env python3
"""Explicitly create one new paper ledger; never open an existing database for writes."""

import argparse
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from qagent.db import create_session_factory, initialize_database
from qagent.storage.paper import PaperTradingRepository


@dataclass(frozen=True)
class AccountInputs:
    label: str
    initial_capital: Decimal
    allocation_per_trade_pct: Decimal
    max_positions: int
    transaction_cost_bps: Decimal
    slippage_bps: Decimal
    take_profit_pct: Decimal

    def validate(self) -> None:
        if not self.label.strip():
            raise ValueError("label must be nonempty")
        amounts = (
            self.initial_capital, self.allocation_per_trade_pct,
            self.transaction_cost_bps, self.slippage_bps, self.take_profit_pct,
        )
        if not all(value.is_finite() for value in amounts):
            raise ValueError("account numeric settings must be finite")
        if self.initial_capital <= 0 or not 0 < self.allocation_per_trade_pct <= 100:
            raise ValueError("capital and allocation must be positive; allocation at most 100")
        if self.max_positions <= 0 or self.transaction_cost_bps < 0 or self.slippage_bps < 0:
            raise ValueError("positions must be positive and cost/slippage nonnegative")
        if not 0 < self.take_profit_pct <= 100:
            raise ValueError("take profit must be positive and at most 100")


def initialize(home: Path, account_inputs: AccountInputs) -> dict[str, object]:
    account_inputs.validate()
    home = home.resolve(strict=True)
    state = home / "state"
    if not state.is_dir() or state.is_symlink():
        raise ValueError("persistent state directory must exist and must not be a symlink")
    database = state / "qagent.db"
    if (state / ".single-writer-approved").exists():
        raise ValueError("single-writer approval already exists")
    if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("existing SQLite sidecar found")
    # O_EXCL is the final guard against replacing a ledger or a concurrent initializer.
    fd = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    url = f"sqlite:///{database}"
    engine = initialize_database(url)
    try:
        repo = PaperTradingRepository(create_session_factory(url))
        account = repo.start_account_session(
            label=account_inputs.label.strip(),
            initial_capital=account_inputs.initial_capital,
            allocation_per_trade_pct=account_inputs.allocation_per_trade_pct,
            max_positions=account_inputs.max_positions,
            transaction_cost_bps=account_inputs.transaction_cost_bps,
            slippage_bps=account_inputs.slippage_bps,
            take_profit_pct=account_inputs.take_profit_pct,
        )
        return {"database": str(database), "account": account.model_dump(mode="json")}
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path("/home/luozhenkun/qagent"))
    parser.add_argument("--confirm-new-ledger", action="store_true", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--initial-capital", type=Decimal, required=True)
    parser.add_argument("--allocation-per-trade-pct", type=Decimal, required=True)
    parser.add_argument("--max-positions", type=int, required=True)
    parser.add_argument("--transaction-cost-bps", type=Decimal, required=True)
    parser.add_argument("--slippage-bps", type=Decimal, required=True)
    parser.add_argument("--take-profit-pct", type=Decimal, required=True)
    args = parser.parse_args()
    result = initialize(args.home, AccountInputs(
        label=args.label,
        initial_capital=args.initial_capital,
        allocation_per_trade_pct=args.allocation_per_trade_pct,
        max_positions=args.max_positions,
        transaction_cost_bps=args.transaction_cost_bps,
        slippage_bps=args.slippage_bps,
        take_profit_pct=args.take_profit_pct,
    ))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
