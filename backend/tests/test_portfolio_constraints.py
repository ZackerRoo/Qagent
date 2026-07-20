from datetime import date

from qagent.cards.generator import OpportunityCardGenerator
from qagent.domain.models import PortfolioPlan
from qagent.portfolio import (
    PortfolioCandidate,
    PortfolioConstraintCode,
    PortfolioConstraintConfig,
    PortfolioConstraintEngine,
)
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.recommendations.decision import build_research_decision
from qagent.recommendations.portfolio import build_portfolio_plan
from qagent.signals.engine import SignalEngine


def test_engine_admits_actions_zeroes_observations_deduplicates_and_limits_positions():
    engine = PortfolioConstraintEngine(
        PortfolioConstraintConfig(
            max_positions=2,
            max_single_position_pct=20,
            total_risk_budget_pct=10,
            min_cash_reserve_pct=0,
            max_industry_positions=None,
            max_industry_weight_pct=None,
            max_same_theme_positions=None,
            max_theme_weight_pct=None,
            max_etf_overlap_positions=None,
            max_etf_overlap_weight_pct=None,
        )
    )
    candidates = [
        _candidate("watch", "US:WATCH", action="watch_trigger", priority=0.99),
        _candidate("entry-a", "US:A", priority=0.90),
        _candidate("duplicate-a", "us:a", priority=0.70),
        _candidate("entry-b", "US:B", priority=0.80),
        _candidate("entry-c", "US:C", priority=0.60),
        _candidate("avoid", "US:AVOID", action="avoid", priority=0.95),
    ]

    first = engine.evaluate(candidates)
    second = engine.evaluate(candidates)
    by_id = {result.candidate_id: result for result in first}

    assert [result.model_dump() for result in first] == [result.model_dump() for result in second]
    assert len(first) == len(candidates)
    assert by_id["entry-a"].accepted is True
    assert by_id["entry-b"].accepted is True
    assert by_id["watch"].accepted is False
    assert by_id["watch"].target_weight == 0
    assert by_id["watch"].risk_budget == 0
    assert PortfolioConstraintCode.OBSERVATION_ONLY in by_id["watch"].constraint_codes
    assert PortfolioConstraintCode.DUPLICATE_INSTRUMENT in by_id["duplicate-a"].constraint_codes
    assert PortfolioConstraintCode.MAX_POSITIONS in by_id["entry-c"].constraint_codes
    assert PortfolioConstraintCode.ACTION_NOT_ADMITTED in by_id["avoid"].constraint_codes
    for result in first:
        payload = result.model_dump()
        assert {"accepted", "target_weight", "risk_budget", "constraint_codes"} <= payload.keys()


def test_engine_scales_single_name_cash_total_risk_industry_and_theme_budgets():
    cash_engine = PortfolioConstraintEngine(
        PortfolioConstraintConfig(
            max_positions=5,
            max_single_position_pct=10,
            total_risk_budget_pct=10,
            min_cash_reserve_pct=85,
            max_industry_positions=None,
            max_industry_weight_pct=None,
            max_same_theme_positions=None,
            max_theme_weight_pct=None,
            max_etf_overlap_positions=None,
            max_etf_overlap_weight_pct=None,
        )
    )
    cash_results = cash_engine.evaluate(
        [
            _candidate("large", "US:LARGE", weight=20, risk=2, priority=0.9),
            _candidate("cash", "US:CASH", weight=10, risk=1, priority=0.8),
        ]
    )
    by_id = {result.candidate_id: result for result in cash_results}

    assert by_id["large"].target_weight == 10
    assert by_id["large"].risk_budget == 1
    assert PortfolioConstraintCode.SINGLE_POSITION_CAP in by_id["large"].constraint_codes
    assert by_id["cash"].target_weight == 5
    assert by_id["cash"].risk_budget == 0.5
    assert PortfolioConstraintCode.CASH_RESERVE in by_id["cash"].constraint_codes
    assert sum(result.target_weight for result in cash_results) == 15

    risk_engine = PortfolioConstraintEngine(
        PortfolioConstraintConfig(
            max_positions=5,
            max_single_position_pct=20,
            total_risk_budget_pct=1.2,
            min_cash_reserve_pct=0,
            max_industry_positions=None,
            max_industry_weight_pct=None,
            max_same_theme_positions=None,
            max_theme_weight_pct=None,
            max_etf_overlap_positions=None,
            max_etf_overlap_weight_pct=None,
        )
    )
    risk_results = risk_engine.evaluate(
        [
            _candidate("risk-a", "US:RISK-A", risk=1, priority=0.9),
            _candidate("risk-b", "US:RISK-B", risk=1, priority=0.8),
        ]
    )
    risk_by_id = {result.candidate_id: result for result in risk_results}

    assert risk_by_id["risk-b"].target_weight == 2
    assert risk_by_id["risk-b"].risk_budget == 0.2
    assert PortfolioConstraintCode.TOTAL_RISK_BUDGET in risk_by_id["risk-b"].constraint_codes
    assert sum(result.risk_budget for result in risk_results) == 1.2

    concentration_engine = PortfolioConstraintEngine(
        PortfolioConstraintConfig(
            max_positions=5,
            max_single_position_pct=20,
            total_risk_budget_pct=10,
            min_cash_reserve_pct=0,
            max_industry_positions=None,
            max_industry_weight_pct=15,
            max_same_theme_positions=None,
            max_theme_weight_pct=12,
            max_etf_overlap_positions=None,
            max_etf_overlap_weight_pct=None,
        )
    )
    concentration_results = concentration_engine.evaluate(
        [
            _candidate(
                "theme-a",
                "US:THEME-A",
                industry="Technology",
                themes=("AI",),
                priority=0.9,
            ),
            _candidate(
                "theme-b",
                "US:THEME-B",
                industry="Technology",
                themes=("AI",),
                priority=0.8,
            ),
        ]
    )
    concentration_by_id = {result.candidate_id: result for result in concentration_results}

    assert concentration_by_id["theme-b"].target_weight == 2
    assert (
        PortfolioConstraintCode.INDUSTRY_WEIGHT_CAP
        in concentration_by_id["theme-b"].constraint_codes
    )
    assert (
        PortfolioConstraintCode.THEME_WEIGHT_CAP in concentration_by_id["theme-b"].constraint_codes
    )


def test_engine_blocks_industry_theme_and_overlapping_etf_concentration():
    engine = PortfolioConstraintEngine(
        PortfolioConstraintConfig(
            max_positions=10,
            max_single_position_pct=20,
            total_risk_budget_pct=10,
            min_cash_reserve_pct=0,
            max_industry_positions=1,
            max_industry_weight_pct=100,
            max_same_theme_positions=1,
            max_theme_weight_pct=100,
            max_etf_overlap_positions=1,
            max_etf_overlap_weight_pct=100,
        )
    )
    results = engine.evaluate(
        [
            _candidate(
                "stock-a",
                "US:STOCK-A",
                industry="Semiconductors",
                themes=("AI",),
                priority=0.95,
            ),
            _candidate(
                "industry-blocked",
                "US:STOCK-B",
                industry="Semiconductors",
                themes=("Memory",),
                priority=0.90,
            ),
            _candidate(
                "theme-blocked",
                "US:STOCK-C",
                industry="Software",
                themes=("AI",),
                priority=0.85,
            ),
            _candidate(
                "etf-a",
                "CN:510001",
                industry="Index A",
                themes=("Chips",),
                overlap_keys=("semiconductor-basket",),
                asset_type="ETF",
                priority=0.80,
            ),
            _candidate(
                "etf-overlap-blocked",
                "CN:510002",
                industry="Index B",
                themes=("Hardware",),
                overlap_keys=("semiconductor-basket",),
                asset_type="ETF",
                priority=0.75,
            ),
        ]
    )
    by_id = {result.candidate_id: result for result in results}

    assert by_id["stock-a"].accepted is True
    assert (
        PortfolioConstraintCode.INDUSTRY_POSITION_CAP in by_id["industry-blocked"].constraint_codes
    )
    assert (
        PortfolioConstraintCode.SAME_THEME_CONCENTRATION in by_id["theme-blocked"].constraint_codes
    )
    assert by_id["etf-a"].accepted is True
    assert PortfolioConstraintCode.ETF_OVERLAP in by_id["etf-overlap-blocked"].constraint_codes


def test_engine_applies_market_state_multiplier_to_weight_risk_and_policy():
    engine = PortfolioConstraintEngine(
        PortfolioConstraintConfig(
            max_positions=5,
            max_single_position_pct=20,
            total_risk_budget_pct=10,
            min_cash_reserve_pct=0,
            max_industry_positions=None,
            max_industry_weight_pct=None,
            max_same_theme_positions=None,
            max_theme_weight_pct=None,
            max_etf_overlap_positions=None,
            max_etf_overlap_weight_pct=None,
        )
    )
    candidate = _candidate("market", "US:MARKET", weight=8, risk=1)

    neutral = engine.evaluate([candidate], market_state="neutral")[0]
    risk_off = engine.evaluate([candidate], market_state="risk_off")[0]
    risk_on = engine.evaluate([candidate], market_state="risk_on")[0]
    policy = engine.policy_audit(market_state="risk_off")

    assert (neutral.target_weight, neutral.risk_budget) == (8, 1)
    assert (risk_off.target_weight, risk_off.risk_budget) == (4, 0.5)
    assert (risk_on.target_weight, risk_on.risk_budget) == (8.8, 1.1)
    assert PortfolioConstraintCode.MARKET_STATE_MULTIPLIER in risk_off.constraint_codes
    assert policy.market_state_multiplier == 0.5
    assert policy.effective_risk_budget_pct == 5
    assert policy.max_invested_weight_pct == 50


def test_build_portfolio_plan_preserves_legacy_fields_and_adds_full_audit():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    source = OpportunityCardGenerator().generate("US:TEST", signals, bars)
    assert source is not None

    entry = source.model_copy(deep=True)
    entry.card_id = "entry-card"
    entry.instrument_id = "US:ENTRY"
    entry.instrument_label = "Entry"
    entry.rank_score = 0.80
    entry.decision = build_research_decision(entry)
    entry.decision.action = "candidate_entry"
    entry.decision.risk_status = "clear"
    entry.decision.suggested_risk_pct = 1.0
    entry.decision.max_position_pct = 8.0

    watch = source.model_copy(deep=True)
    watch.card_id = "watch-card"
    watch.instrument_id = "US:WATCH"
    watch.instrument_label = "Watch"
    watch.rank_score = 0.90
    watch.decision = build_research_decision(watch)
    watch.decision.action = "watch_trigger"
    watch.decision.risk_status = "clear"
    watch.decision.suggested_risk_pct = 0.8
    watch.decision.max_position_pct = 6.0

    plan = build_portfolio_plan([watch, entry], max_positions=2)
    payload = plan.model_dump(mode="json")

    assert len(plan.constraint_results) == 2
    assert len(plan.allocations) == 1
    assert plan.allocations[0].instrument_id == "US:ENTRY"
    assert plan.allocations[0].weight_pct == plan.allocations[0].target_weight == 8
    assert plan.allocations[0].risk_budget_pct == plan.allocations[0].risk_budget == 1
    assert len(plan.watchlist) == 1
    assert plan.watchlist[0].instrument_id == "US:WATCH"
    assert plan.watchlist[0].weight_pct == 0
    assert plan.watchlist[0].risk_budget_pct == 0
    assert plan.allocated_weight_pct == 8
    assert plan.allocated_risk_budget_pct == 1
    assert plan.cash_reserve_pct == 92
    assert "constraint_results" in payload
    assert "constraint_policy" in payload
    assert PortfolioPlan.model_validate(payload).allocated_weight_pct == 8


def _candidate(
    candidate_id: str,
    instrument_id: str,
    *,
    action: str = "candidate_entry",
    weight: float = 10,
    risk: float = 1,
    priority: float = 0.5,
    industry: str | None = None,
    themes: tuple[str, ...] = (),
    overlap_keys: tuple[str, ...] = (),
    asset_type: str = "stock",
) -> PortfolioCandidate:
    return PortfolioCandidate(
        candidate_id=candidate_id,
        instrument_id=instrument_id,
        action=action,
        requested_weight=weight,
        requested_risk_budget=risk,
        max_position_pct=20,
        priority=priority,
        industry=industry,
        themes=themes,
        etf_overlap_keys=overlap_keys,
        asset_type=asset_type,
    )
