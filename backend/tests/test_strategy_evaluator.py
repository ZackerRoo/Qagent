from datetime import date

from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.signals.engine import SignalEngine
from qagent.strategy_data.providers import FixtureStrategyDataProvider
from qagent.strategies.evaluator import StrategyEvaluator
from qagent.strategies.registry import default_strategy_registry


def _fixture_inputs(instrument_id: str = "US:TEST"):
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars([instrument_id], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate(instrument_id, bars)
    return bars, signals


def test_evaluator_scores_tradeable_free_data_strategy_stack():
    bars, signals = _fixture_inputs("US:TEST")
    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate("US:TEST", signals, bars)
    by_id = {evaluation.strategy_id: evaluation for evaluation in evaluations}

    assert by_id["trend_momentum_stage2"].status == "passed"
    assert by_id["trend_momentum_stage2"].score >= 0.8
    assert "trend_strength" in by_id["trend_momentum_stage2"].triggers
    assert by_id["breakout_volume_confirmation"].status == "passed"
    assert by_id["breakout_volume_confirmation"].score >= 0.8
    assert {"breakout", "volume_anomaly"}.issubset(
        set(by_id["breakout_volume_confirmation"].triggers)
    )
    assert by_id["gf_dma_health"].status in {"passed", "watch"}
    assert by_id["gf_dma_health"].evidence["close_vs_ma_20_pct"] > 0


def test_evaluator_does_not_fabricate_commercial_data_strategies():
    bars, signals = _fixture_inputs("US:TEST")
    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate("US:TEST", signals, bars)
    by_id = {evaluation.strategy_id: evaluation for evaluation in evaluations}

    for strategy_id in [
        "pead_earnings_drift",
        "analyst_revision_momentum",
        "tam_adj_peg_growth",
        "bayesian_intrinsic_growth",
        "options_flow_confirmation",
        "insider_institutional_confirmation",
    ]:
        assert by_id[strategy_id].status == "missing_data"
        assert by_id[strategy_id].score == 0
        assert by_id[strategy_id].missing_data


def test_evaluator_scores_pead_when_earnings_inputs_are_available():
    bars, signals = _fixture_inputs("US:TEST")
    earnings_events = FixtureStrategyDataProvider().get_earnings_events(
        ["US:TEST"], start=date(2026, 3, 1), end=date(2026, 3, 31)
    )

    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate(
        "US:TEST",
        signals,
        bars,
        context={
            "earnings_events": earnings_events,
            "available_data": [
                "earnings_actuals",
                "earnings_estimates",
                "announcement_timestamp",
            ],
        },
    )
    pead = {evaluation.strategy_id: evaluation for evaluation in evaluations}[
        "pead_earnings_drift"
    ]

    assert pead.status == "passed"
    assert pead.score >= 0.8
    assert pead.missing_data == []
    assert "earnings_surprise" in pead.triggers
    assert pead.evidence["eps_surprise_pct"] > 20
    assert pead.evidence["announcement_return_pct"] > 5
    assert pead.score_components["earnings_surprise"] > 0


def test_evaluator_keeps_pead_missing_when_estimates_are_unavailable():
    bars, signals = _fixture_inputs("CN:000001")
    earnings_events = FixtureStrategyDataProvider().get_earnings_events(
        ["CN:000001"], start=date(2026, 3, 1), end=date(2026, 3, 31)
    )

    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate(
        "CN:000001",
        signals,
        bars,
        context={"earnings_events": earnings_events, "available_data": ["earnings_actuals"]},
    )
    pead = {evaluation.strategy_id: evaluation for evaluation in evaluations}[
        "pead_earnings_drift"
    ]

    assert pead.status == "missing_data"
    assert "earnings_estimates" in pead.missing_data
    assert pead.score == 0


def test_evaluator_marks_a_share_limit_as_risk_confirmation():
    bars, signals = _fixture_inputs("CN:000001")
    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate("CN:000001", signals, bars)
    by_id = {evaluation.strategy_id: evaluation for evaluation in evaluations}

    assert by_id["breakout_volume_confirmation"].status == "passed"
    assert "limit_status" in by_id["breakout_volume_confirmation"].confirmations
    assert by_id["short_squeeze_risk"].status in {"watch", "missing_data"}
