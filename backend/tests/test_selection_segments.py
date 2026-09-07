import pytest
from decimal import Decimal

from qagent.research.selection_segments import _digest, build_selection_segments


def fixture():
    selections = [{"instrument_id": str(i), "primary_strategy_id": "s", "industry": "i",
                   "factor_signals": ["a", "b", "a"]} for i in range(10)]
    snapshots = [{"decision_date": "2024-01-01", "top_5": selections[:5], "top_10": selections}]
    window = {"windows": [{"key": "train", "start_date": "2024-01-01", "end_date": "2024-01-01"}]}
    source = {"run_id": "run", "payload": {"snapshots": snapshots,
              "top_5_temporal_validation": window, "top_10_temporal_validation": window}}
    def trade(i):
        return {"instrument_id": str(i), "signal_date": "2024-01-01", "gross_pnl": "11", "costs": "1", "net_pnl": "10"}
    replay = {"configs_identical": True, "source_run_id": "run", "source_snapshots_digest": _digest(snapshots),
              "arms": {arm: {"summary": {"initial_capital": "100"}, "portfolio": {"trades": rows}}
                       for arm, rows in [("top_5", [trade(0)]), ("top_10", [trade(0), trade(6), trade(99)])]}}
    return source, replay


def test_partition_missing_and_tags():
    result = build_selection_segments(*fixture())
    assert result["top10_partition_net_residual"] == "0"
    top10 = result["cohorts"]["top_10"]
    assert top10["total"]["net_pnl"] == "30"
    assert top10["total"]["distinct_signal_dates"] == 1
    assert top10["missing_metadata_trade_count"] == 1
    assert top10["dimensions"]["industry"]["groups"]["unknown"]["net_pnl"] == "10"
    assert top10["dimensions"]["regime"]["groups"]["unknown"]["trade_count"] == 3
    tags = top10["dimensions"]["factor_tags"]
    assert tags["additive"] is False and tags["net_residual"] is None
    assert tags["groups"]["a"]["net_pnl"] == "20"
    assert tags["groups"]["b"]["net_pnl"] == "20"
    for dimension, group in top10["dimensions"].items():
        if dimension != "factor_tags":
            assert group["net_residual"] == "0"
            assert group["cost_residual"] == "0"
    assert result["cohorts"]["shared_top10"]["total"]["trade_count"] == 1


def test_gap_is_retained():
    source, replay = fixture()
    replay["arms"]["top_10"]["portfolio"]["trades"][-1]["signal_date"] = "2024-01-02"
    result = build_selection_segments(source, replay)
    assert result["cohorts"]["top_10"]["dimensions"]["window_top_5"]["groups"]["unknown"]["net_pnl"] == "10"


def test_losses_and_costs_remain_exact():
    source, replay = fixture()
    row = replay["arms"]["top_10"]["portfolio"]["trades"][1]
    row.update(gross_pnl="-0.10", costs="0.20", net_pnl="-0.30")
    result = build_selection_segments(source, replay)
    assert result["cohorts"]["top_10"]["total"]["net_pnl"] == "19.70"
    assert result["cohorts"]["top_10"]["total"]["costs"] == "2.20"
    assert result["cohorts"]["rank_6_10"]["total"]["win_rate"] == 0
    assert Decimal(result["top10_partition_net_residual"]) == 0


@pytest.mark.parametrize("bad", ["amount", "digest", "duplicate"])
def test_invalid_evidence_rejected(bad):
    source, replay = fixture()
    if bad == "amount":
        replay["arms"]["top_5"]["portfolio"]["trades"][0]["net_pnl"] = "NaN"
    elif bad == "digest":
        replay["source_snapshots_digest"] = "wrong"
    else:
        rows = replay["arms"]["top_5"]["portfolio"]["trades"]
        rows.append(dict(rows[0]))
    with pytest.raises(ValueError):
        build_selection_segments(source, replay)
