from copy import deepcopy
from decimal import Decimal

import pytest

from qagent.research.common_execution_delta import build_common_execution_delta


def trade(instrument="A", gross="20", costs="2", shares="10", **kwargs):
    return dict(instrument_id=instrument, signal_date="2025-01-01",
                entry_date="2025-01-02", exit_date="2025-01-03",
                entry_price="10", exit_price="12", shares=shares,
                gross_pnl=gross, costs=costs,
                net_pnl=str(Decimal(gross) - Decimal(costs)), **kwargs)


def test_matched_and_only_paths_close_with_own_capital_and_preserve_inputs():
    left = [trade(), trade("B")]
    right = [trade(gross="40", costs="3", shares="20"), trade("C", gross="-10")]
    before = deepcopy((left, right))
    result = build_common_execution_delta(left, right, "100", "200")
    assert result["status"] == "ready"
    assert result["closed"]
    assert result["matched"]["trade_count"] == 1
    assert result["matched"]["net_contribution_delta_pct"] == .5
    assert result["matched"]["gross_contribution_delta_pct"] == 0
    assert result["matched"]["cost_delta_pct"] == -.5
    assert result["top5_only"]["contribution_pct"] == 18
    assert result["top10_common_only"]["contribution_pct"] == -6
    assert result["common_execution_configuration_delta_pct"] == -23.5
    assert Decimal(result["residual_pct_exact"]) == 0
    assert (left, right) == before


def test_quantity_price_and_signed_date_differences_are_recorded():
    left, right = trade(), trade(gross="40", shares="20")
    right.update(entry_date="2025-01-01", exit_date="2025-01-05", entry_price="11")
    row = build_common_execution_delta([left], [right], 100, 100)["matched"]["trades"][0]
    assert row["quantity_delta"] == "10"
    assert row["entry_price_delta"] == "1"
    assert row["entry_date_delta_calendar_days"] == -1
    assert row["exit_date_delta_calendar_days"] == 2


@pytest.mark.parametrize("field,value", [("net_pnl", None), ("costs", "NaN"),
    ("gross_pnl", "Infinity"), ("shares", None), ("entry_price", "-Infinity"),
    ("exit_price", False), ("entry_date", None), ("exit_date", "bad"),
    ("signal_date", None), ("instrument_id", ""), ("shares", "0"),
    ("costs", "-1"), ("entry_date", "2024-12-31"), ("exit_date", "2025-01-01")])
def test_missing_or_invalid_facts_fail_closed(field, value):
    row = trade()
    row[field] = value
    result = build_common_execution_delta([row], [trade()], 100, 100)
    assert result["status"] == "unavailable"
    assert result["matched"] is None
    assert result["common_execution_configuration_delta_pct"] is None
    assert result["errors"]
    assert not result["closed"]


@pytest.mark.parametrize("side", [0, 1])
def test_duplicates_fail_closed(side):
    rows = [[trade()], [trade()]]
    rows[side].append(trade())
    result = build_common_execution_delta(*rows, 100, 100)
    assert result["status"] == "unavailable"
    assert any("duplicate_key" in error for error in result["errors"])


def test_unresolved_identity_and_invalid_accounting_fail_closed():
    assert not build_common_execution_delta([trade()], [trade()], 100, 100,
                                            identity_valid=False)["closed"]
    row = trade()
    row["net_pnl"] = "100"
    assert build_common_execution_delta([row], [trade()], 100, 100)["status"] == "unavailable"


def test_empty_known_subsets_and_nonterminating_normalization():
    result = build_common_execution_delta([trade()], [trade()], 300, 700)
    assert result["closed"]
    assert result["top5_only"]["contribution_pct"] == 0
    assert result["top10_common_only"]["trade_count"] == 0
    result = build_common_execution_delta([], [trade()], 100, 100)
    assert result["matched"]["net_contribution_delta_pct"] == 0
    assert result["closed"]


def test_equal_prices_share_effect_and_rounding_residual():
    result = build_common_execution_delta([trade()], [trade(gross="40", shares="20")], 100, 100)
    effect = result["matched"]["same_price_quantity_effect"]
    assert effect["gross_contribution_delta_pct"] == 20
    assert Decimal(effect["recorded_gross_minus_quantity_effect_pct_exact"]) == 0
    result = build_common_execution_delta([trade()], [trade(gross="41", shares="20")], 100, 100)
    assert not result["matched"]["same_price_quantity_effect"]["reconciled_to_recorded_gross"]


@pytest.mark.parametrize("capital", [None, "NaN", 0, -1])
def test_invalid_capital_fails_closed(capital):
    assert build_common_execution_delta([trade()], [trade()], capital, 100)["status"] == "unavailable"
