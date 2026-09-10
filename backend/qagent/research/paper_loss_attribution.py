"""Read-only attribution of an existing paper ledger JSON; no providers or writes.

Usage: curl .../paper-trades/ledger?reporting_scope=legacy | python -m
qagent.research.paper_loss_attribution
"""

import json
import hashlib
import sys
from collections import Counter, defaultdict
from decimal import Decimal


def _decimal(value):
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("Non-finite ledger amount")
    return number


def _unique(rows, field):
    keys = [row[field] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError(f"Duplicate {field}")
    return dict(zip(keys, rows))


def build_paper_loss_attribution(ledger: dict) -> dict:
    """Attribute actual cash flows, not the ledger's indicative item allocations.

    Fees and slippage are already reflected in cash flows/fill prices. Their
    reported amounts are diagnostics and must not be subtracted again.
    """
    summary = ledger["summary"]
    items = _unique(ledger["items"], "trade_id")
    positions = _unique(ledger["positions"], "trade_id")
    _unique([row for row in ledger["transactions"] if row.get("transaction_id")], "transaction_id")
    flows = defaultdict(lambda: Decimal("0"))
    fees = defaultdict(lambda: Decimal("0"))
    slippage = defaultdict(lambda: Decimal("0"))
    for tx in ledger["transactions"]:
        trade_id = tx["trade_id"]
        flows[trade_id] += _decimal(tx["cash_flow"])
        fees[trade_id] += _decimal(tx["fee"])
        slippage[trade_id] += _decimal(tx["slippage"])
    if set(positions) - set(flows):
        raise ValueError("Position has no transaction cash flows")
    rows = []
    for trade_id, cash_flow in flows.items():
        item = items[trade_id]
        position = positions.get(trade_id)
        unrealized = _decimal(position["unrealized_pnl"]) if position else Decimal("0")
        pnl = cash_flow + (_decimal(position["market_value"]) if position else 0)
        rows.append({
            "trade_id": trade_id,
            "instrument_id": item["instrument_id"],
            "status": item["status"],
            "strategy_id": item.get("strategy_id") or "unknown",
            "entry_month": (item.get("entry_date") or "unknown")[:7],
            "holding_cohort": "0-5" if item["holding_days"] <= 5 else "6-10" if item["holding_days"] <= 10 else "11+",
            "holding_days": item["holding_days"],
            "open": position is not None,
            "realized_pnl": pnl - unrealized,
            "unrealized_pnl": unrealized,
            "total_pnl": pnl,
            "fees": fees[trade_id],
            "slippage": slippage[trade_id],
        })
    checks = {}
    for field, source in (("total_pnl", "total_pnl"), ("realized_pnl", "realized_pnl"),
                          ("unrealized_pnl", "unrealized_pnl"), ("fees", "total_fees"),
                          ("slippage", "total_slippage")):
        actual = sum((row[field] for row in rows), Decimal("0"))
        expected = _decimal(summary[source])
        checks[source] = {"actual": actual, "expected": expected, "difference": actual - expected}
        if actual != expected:
            raise ValueError(f"Ledger reconciliation failed for {source}: {actual} != {expected}")
    if _decimal(summary["initial_capital"]) + _decimal(summary["total_pnl"]) != _decimal(summary["total_equity"]):
        raise ValueError("Equity does not reconcile")
    if len(positions) != summary["open_trades"] or len(rows) - len(positions) != summary["closed_trades"]:
        raise ValueError("Funded trade counts do not reconcile")
    dimensions = {}
    for dimension in ("status", "strategy_id", "holding_cohort", "entry_month"):
        groups = {}
        for row in rows:
            group = groups.setdefault(row[dimension], {"funded_trades": 0, "open_trades": 0,
                "realized_pnl": Decimal("0"), "unrealized_pnl": Decimal("0"), "total_pnl": Decimal("0")})
            group["funded_trades"] += 1
            group["open_trades"] += int(row["open"])
            for field in ("realized_pnl", "unrealized_pnl", "total_pnl"):
                group[field] += row[field]
        dimensions[dimension] = groups
    return {
        "schema_version": "paper-loss-attribution-v1",
        "source_sha256": hashlib.sha256(json.dumps(ledger, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
        "data_health": ledger.get("data_health", {}),
        "summary": summary,
        "lifecycle_status_counts": dict(Counter(item["status"] for item in items.values())),
        "funded_trades": len(rows),
        "funded_closed_trades": sum(not row["open"] for row in rows),
        "reconciliation": checks,
        "dimensions": dimensions,
        "largest_losses": sorted(rows, key=lambda row: row["total_pnl"])[:10],
        "largest_gains": sorted(rows, key=lambda row: row["total_pnl"], reverse=True)[:5],
        "caveats": ["Descriptive attribution, not evidence that exit rules caused losses.",
                    "Fees and slippage already affect net PnL; do not subtract twice.",
                    "Holding days are stored ledger values, not independently recalculated sessions.",
                    "Open positions use the ledger marks, not independently validated live prices."],
    }


if __name__ == "__main__":
    print(json.dumps(build_paper_loss_attribution(json.load(sys.stdin)), default=str, ensure_ascii=False, indent=2))
