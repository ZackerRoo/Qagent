from datetime import date

from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.signals.engine import SignalEngine
from qagent.strategy_data.models import AnalystInsight, FilingEvent, FundamentalSnapshot
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


def test_evaluator_scores_growth_valuation_when_free_fundamentals_are_available():
    bars, signals = _fixture_inputs("US:TEST")
    fundamentals = [
        FundamentalSnapshot(
            instrument_id="US:TEST",
            as_of_date=date(2026, 3, 31),
            revenue_growth_pct=32.5,
            earnings_growth_pct=41.2,
            gross_margin_pct=68,
            operating_margin_pct=24.5,
            net_margin_pct=18,
            return_on_equity_pct=29,
            market_cap=8_500_000_000,
            pe_ratio=34,
            forward_pe=28,
            peg_ratio=0.95,
            price_to_sales=7.5,
            provider="fixture_strategy_data",
        )
    ]

    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate(
        "US:TEST",
        signals,
        bars,
        context={
            "fundamentals": fundamentals,
            "available_data": [
                "fundamentals",
                "valuation_multiples",
                "tam_assumptions",
                "growth_priors",
            ],
        },
    )
    by_id = {evaluation.strategy_id: evaluation for evaluation in evaluations}

    assert by_id["tam_adj_peg_growth"].status == "passed"
    assert by_id["tam_adj_peg_growth"].score >= 0.7
    assert by_id["tam_adj_peg_growth"].missing_data == []
    assert by_id["tam_adj_peg_growth"].evidence["tam_assumption_source"] == "free_fundamental_proxy"
    assert by_id["bayesian_intrinsic_growth"].status == "passed"
    assert by_id["bayesian_intrinsic_growth"].score >= 0.65
    assert by_id["bayesian_intrinsic_growth"].evidence["posterior_growth_probability"] > 0.6


def test_evaluator_scores_analyst_revision_when_revision_inputs_are_available():
    bars, signals = _fixture_inputs("US:TEST")
    analyst_insights = [
        AnalystInsight(
            instrument_id="US:TEST",
            as_of_date=date(2026, 3, 31),
            revision_date=date(2026, 3, 31),
            current_eps_estimate=1.35,
            prior_eps_estimate=1.1,
            current_revenue_estimate=152_000_000,
            prior_revenue_estimate=140_000_000,
            target_price=64,
            prior_target_price=54,
            current_price=50,
            strong_buy_count=6,
            buy_count=14,
            hold_count=4,
            sell_count=1,
            strong_sell_count=0,
            provider="fixture_strategy_data",
        )
    ]

    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate(
        "US:TEST",
        signals,
        bars,
        context={
            "analyst_insights": analyst_insights,
            "available_data": ["analyst_estimates", "revision_timestamps"],
        },
    )
    revision = {evaluation.strategy_id: evaluation for evaluation in evaluations}[
        "analyst_revision_momentum"
    ]

    assert revision.status == "passed"
    assert revision.score >= 0.7
    assert revision.missing_data == []
    assert "estimate_revision" in revision.triggers
    assert revision.evidence["eps_revision_pct"] > 20
    assert revision.evidence["target_revision_pct"] > 15


def test_evaluator_scores_ownership_confirmation_from_sec_filings():
    bars, signals = _fixture_inputs("US:TEST")
    filings = [
        FilingEvent(
            instrument_id="US:TEST",
            form="4",
            filing_date=date(2026, 3, 20),
            accession_number="0001",
            provider="sec_edgar",
        ),
        FilingEvent(
            instrument_id="US:TEST",
            form="13F-HR",
            filing_date=date(2026, 3, 21),
            accession_number="0002",
            provider="sec_edgar",
        ),
    ]

    evaluations = StrategyEvaluator(default_strategy_registry()).evaluate(
        "US:TEST",
        signals,
        bars,
        context={
            "filings": filings,
            "available_data": ["insider_transactions", "institutional_filings"],
        },
    )
    ownership = {evaluation.strategy_id: evaluation for evaluation in evaluations}[
        "insider_institutional_confirmation"
    ]

    assert ownership.status == "passed"
    assert ownership.score >= 0.7
    assert ownership.missing_data == []
    assert "sec_ownership_filing" in ownership.triggers
    assert ownership.evidence["insider_forms"] == 1
    assert ownership.evidence["institutional_filings"] == 1
