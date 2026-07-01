from qagent.strategies.registry import default_strategy_registry


def test_default_strategy_registry_contains_product_strategy_map():
    registry = default_strategy_registry()

    expected = {
        "trend_momentum_stage2",
        "breakout_volume_confirmation",
        "healthy_pullback",
        "gf_dma_health",
        "catalyst_financial_transmission",
        "pead_earnings_drift",
        "analyst_revision_momentum",
        "tam_adj_peg_growth",
        "bayesian_intrinsic_growth",
        "sector_rotation_regime",
        "short_squeeze_risk",
        "options_flow_confirmation",
        "insider_institutional_confirmation",
    }

    assert expected.issubset(set(registry.strategy_ids()))


def test_registry_separates_free_data_ready_and_missing_data_strategies():
    registry = default_strategy_registry()

    breakout = registry.get("breakout_volume_confirmation")
    pead = registry.get("pead_earnings_drift")
    tam_peg = registry.get("tam_adj_peg_growth")
    options_flow = registry.get("options_flow_confirmation")

    assert breakout.free_data_ready is True
    assert set(breakout.required_data) == {"daily_ohlcv"}
    assert pead.free_data_ready is False
    assert {"earnings_actuals", "earnings_estimates"}.issubset(set(pead.required_data))
    assert tam_peg.free_data_ready is False
    assert {"fundamentals", "tam_assumptions"}.issubset(set(tam_peg.required_data))
    assert options_flow.free_data_ready is False
    assert "options_flow" in options_flow.required_data
