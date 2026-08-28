from copy import deepcopy

from qagent.research.top10_lag_attribution import build_top10_lag_attribution


def _payload():
    return {
        "top_5_metrics": {"total_return_pct": 1.0},
        "top_10_metrics": {"total_return_pct": 0.5},
        "top_10_temporal_validation": {
            "out_of_sample": {"start_date": "2025-01-01", "end_date": "2025-01-31"}
        },
        "snapshots": [
            {
                "decision_date": "2025-01-02",
                "benchmark_trend_state": "risk_off",
                "top_5": [
                    {
                        "instrument_id": "CN:000001",
                        "primary_strategy_id": "value",
                        "industry": "bank",
                    },
                    {"instrument_id": "CN:000002"},
                    {"instrument_id": "CN:000003"},
                    {"instrument_id": "CN:000004"},
                    {"instrument_id": "CN:000005"},
                ],
                "top_10": [
                    {
                        "instrument_id": "CN:000001",
                        "primary_strategy_id": "value",
                        "industry": "bank",
                    },
                    {"instrument_id": "CN:000002"},
                    {"instrument_id": "CN:000003"},
                    {"instrument_id": "CN:000004"},
                    {"instrument_id": "CN:000005"},
                    {
                        "instrument_id": "CN:000006",
                        "primary_strategy_id": "breakout",
                        "industry": "software",
                    },
                    {
                        "instrument_id": "CN:000007",
                        "primary_strategy_id": None,
                        "industry": None,
                    },
                ],
            }
        ],
        "top_5_portfolio": {
            "summary": {"initial_capital": "100000"},
            "trades": [
                {
                    "instrument_id": "CN:000001",
                    "signal_date": "2025-01-02",
                    "strategy_id": "value",
                    "return_pct": 2.0,
                    "gross_pnl": "1010",
                    "net_pnl": "1000",
                    "costs": "10",
                    "holding_days": 4,
                }
            ],
        },
        "top_10_portfolio": {
            "summary": {"initial_capital": "100000"},
            "trades": [
                {
                    "instrument_id": "CN:000001",
                    "signal_date": "2025-01-02",
                    "strategy_id": "value",
                    "return_pct": 2.0,
                    "gross_pnl": "1010",
                    "net_pnl": "1000",
                    "costs": "10",
                    "holding_days": 4,
                },
                {
                    "instrument_id": "CN:000006",
                    "signal_date": "2025-01-02",
                    "strategy_id": "breakout",
                    "return_pct": -4.0,
                    "gross_pnl": "-580",
                    "net_pnl": "-600",
                    "costs": "20",
                    "holding_days": 8,
                },
                {
                    "instrument_id": "CN:000007",
                    "signal_date": "2025-01-02",
                    "strategy_id": None,
                    "return_pct": 1.0,
                    "gross_pnl": "115",
                    "net_pnl": "100",
                    "costs": "15",
                    "holding_days": 12,
                },
            ],
        },
    }


def test_top10_lag_attribution_separates_common_and_incremental_layers():
    result = build_top10_lag_attribution(_payload())

    assert result["status"] == "ready"
    assert result["return_gap_pct"] == -0.5
    assert result["common_layer"]["contribution_pct"] == 1.0
    assert result["common_layer"]["gross_contribution_pct"] == 1.01
    assert result["incremental_layer"]["trade_count"] == 2
    assert result["incremental_layer"]["independent_signal_date_count"] == 1
    assert result["incremental_layer"]["win_rate"] == 0.5
    assert result["incremental_layer"]["average_return_pct"] == -1.5
    assert result["incremental_layer"]["gross_pnl"] == "-465"
    assert result["incremental_layer"]["net_pnl"] == "-500"
    assert result["incremental_layer"]["contribution_pct"] == -0.5
    assert result["incremental_layer"]["total_costs"] == "35"
    assert result["incremental_layer"]["cost_pct"] == 0.035
    strategy = next(item for item in result["dimensions"] if item["dimension"] == "strategy")
    assert [item["key"] for item in strategy["groups"]] == ["breakout", "unknown"]
    assert any(item["key"] == "breakout" for item in result["primary_drags"])
    assert result["reconciliation"]["incremental_layer_contribution_pct"] == -0.5
    assert result["reconciliation"]["common_execution_configuration_delta_pct"] == 0.0
    assert result["reconciliation"]["gross_return_gap_pct"] == -0.465
    assert result["reconciliation"]["extra_cost_drag_pct"] == -0.035
    assert result["reconciliation"]["residual_pct"] == 0.0
    assert result["reconciliation"]["closed"] is True
    assert result["incremental_layer_out_of_sample"]["trade_count"] == 2
    assert result["incremental_layer_out_of_sample"]["independent_signal_date_count"] == 1
    assert result["incremental_layer_out_of_sample"]["share_of_full_incremental_net_loss"] == 1.0
    assert result["official_release_allowed"] is False


def test_top10_lag_attribution_preserves_unknown_instead_of_zero():
    payload = _payload()
    payload["top_10_portfolio"]["trades"][1]["net_pnl"] = None
    payload["top_10_portfolio"]["trades"][1]["costs"] = None
    payload["top_10_portfolio"]["trades"][1]["return_pct"] = None

    result = build_top10_lag_attribution(payload)

    assert result["incremental_layer"]["contribution_pct"] is None
    assert result["incremental_layer"]["total_costs"] is None
    assert result["incremental_layer"]["cost_pct"] is None
    assert result["incremental_layer"]["average_return_pct"] is None
    assert result["incremental_layer"]["field_completeness"]["return_pct"] == {
        "known": 1,
        "total": 2,
    }
    assert result["data_health"]["unknown_values_are_zero"] is False


def test_top10_lag_attribution_marks_legacy_payload_unsupported():
    result = build_top10_lag_attribution(
        {
            "top_5_metrics": {"total_return_pct": 8.0},
            "top_10_metrics": {"total_return_pct": 7.0},
            "snapshots": [{"decision_date": "2025-01-02"}],
        }
    )

    assert result["status"] == "unsupported"
    assert result["incremental_layer"] is None
    assert result["return_gap_pct"] == -1.0
    assert "top_10_portfolio.trades" in result["data_health"]["missing_fields"]


def test_top10_lag_attribution_is_idempotent_and_does_not_mutate_signed_payload():
    payload = _payload()
    original = deepcopy(payload)

    first = build_top10_lag_attribution(
        payload,
        source_run_id="walk-forward-1",
        source_reproducibility_digest="v2digest",
    )
    second = build_top10_lag_attribution(
        payload,
        source_run_id="walk-forward-1",
        source_reproducibility_digest="v2digest",
    )

    assert first == second
    assert payload == original
    assert first["source"] == {
        "kind": "validated_walk_forward_result_payload",
        "run_id": "walk-forward-1",
        "reproducibility_digest": "v2digest",
    }
