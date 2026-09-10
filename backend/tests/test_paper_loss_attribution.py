from copy import deepcopy
from decimal import Decimal

import pytest

from qagent.research.paper_loss_attribution import build_paper_loss_attribution


def ledger_fixture():
    return {
        "summary": {"initial_capital": "1000", "total_equity": "986", "total_pnl": "-14",
                    "realized_pnl": "-12", "unrealized_pnl": "-2", "total_fees": "3", "total_slippage": "3",
                    "open_trades": 1, "closed_trades": 1},
        "items": [
            {"trade_id": key, "instrument_id": key, "status": status, "entry_date": "2026-09-01",
             "holding_days": 3, "total_pnl": "9999"}
            for key, status in (("closed", "stopped"), ("open", "open"), ("unfilled", "replaced"))
        ],
        "transactions": [
            {"trade_id": "closed", "cash_flow": "-102", "fee": "1", "slippage": "1"},
            {"trade_id": "closed", "cash_flow": "90", "fee": "1", "slippage": "1"},
            {"trade_id": "open", "cash_flow": "-101", "fee": "1", "slippage": "1"},
        ],
        "positions": [{"trade_id": "open", "market_value": "99", "unrealized_pnl": "-2"}],
    }


def test_cash_flows_reconcile_without_double_subtracting_costs_or_counting_unfilled():
    ledger = ledger_fixture()
    original = deepcopy(ledger)
    result = build_paper_loss_attribution(ledger)
    assert ledger == original
    assert result["funded_trades"] == 2
    assert result["funded_closed_trades"] == 1
    assert result["lifecycle_status_counts"]["replaced"] == 1
    assert result["dimensions"]["status"]["stopped"]["total_pnl"] == Decimal("-12")
    assert all(check["difference"] == 0 for check in result["reconciliation"].values())


def test_multiple_sell_legs_are_one_funded_trade():
    ledger = ledger_fixture()
    ledger["transactions"][1]["cash_flow"] = "45"
    ledger["transactions"][1]["fee"] = "0.5"
    ledger["transactions"][1]["slippage"] = "0.5"
    ledger["transactions"].append(deepcopy(ledger["transactions"][1]))
    assert build_paper_loss_attribution(ledger)["funded_closed_trades"] == 1


@pytest.mark.parametrize("field", ["total_pnl", "realized_pnl", "unrealized_pnl", "total_fees", "total_slippage", "total_equity"])
def test_fail_closed_on_ledger_mismatch(field):
    ledger = ledger_fixture()
    ledger["summary"][field] = "123"
    with pytest.raises(ValueError):
        build_paper_loss_attribution(ledger)


def test_reject_position_without_funding():
    ledger = ledger_fixture()
    ledger["positions"][0]["trade_id"] = "missing"
    with pytest.raises(ValueError, match="no transaction"):
        build_paper_loss_attribution(ledger)


@pytest.mark.parametrize("collection", ["items", "positions", "transactions"])
def test_duplicate_ids_rejected(collection):
    ledger = ledger_fixture()
    if collection == "transactions":
        ledger[collection][0]["transaction_id"] = "tx1"
    ledger[collection].append(deepcopy(ledger[collection][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        build_paper_loss_attribution(ledger)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_amounts_rejected(value):
    ledger = ledger_fixture()
    ledger["transactions"][0]["cash_flow"] = value
    with pytest.raises(ValueError, match="Non-finite"):
        build_paper_loss_attribution(ledger)


@pytest.mark.parametrize("field", ["open_trades", "closed_trades"])
def test_funded_count_mismatch_rejected(field):
    ledger = ledger_fixture()
    ledger["summary"][field] = 99
    with pytest.raises(ValueError, match="counts"):
        build_paper_loss_attribution(ledger)
