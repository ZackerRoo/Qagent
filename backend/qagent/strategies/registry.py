from qagent.strategies.models import StrategyDefinition


class StrategyRegistry:
    def __init__(self, definitions: list[StrategyDefinition]):
        self._definitions = {definition.strategy_id: definition for definition in definitions}

    def all(self) -> list[StrategyDefinition]:
        return list(self._definitions.values())

    def get(self, strategy_id: str) -> StrategyDefinition:
        return self._definitions[strategy_id]

    def strategy_ids(self) -> list[str]:
        return list(self._definitions.keys())


def default_strategy_registry() -> StrategyRegistry:
    return StrategyRegistry(
        [
            StrategyDefinition(
                strategy_id="trend_momentum_stage2",
                name="Stage 2 trend momentum",
                family="growth_momentum",
                role="primary",
                horizon="20-60d",
                description="Rising price structure with close above key moving averages.",
                required_data=["daily_ohlcv"],
                optional_data=["fundamentals", "relative_strength"],
                free_data_ready=True,
                invalidation_template="Trend weakens if price loses the rising 50DMA or the signal stack reverses.",
            ),
            StrategyDefinition(
                strategy_id="breakout_volume_confirmation",
                name="Breakout with volume confirmation",
                family="technical_breakout",
                role="primary",
                horizon="5-20d",
                description="Price clears a recent range while volume expands above normal.",
                required_data=["daily_ohlcv"],
                optional_data=["limit_status"],
                free_data_ready=True,
                invalidation_template="Breakout fails if price closes back below pivot/support on weak follow-through.",
            ),
            StrategyDefinition(
                strategy_id="healthy_pullback",
                name="Healthy trend pullback",
                family="technical_pullback",
                role="primary",
                horizon="5-20d",
                description="A strong trend pulls back toward short-term support without breaking structure.",
                required_data=["daily_ohlcv"],
                optional_data=["fundamentals", "relative_strength"],
                free_data_ready=True,
                invalidation_template="Pullback fails if price loses support or closes below the rising 50DMA.",
            ),
            StrategyDefinition(
                strategy_id="gf_dma_health",
                name="GF-DMA health index",
                family="trend_health",
                role="risk_control",
                horizon="5-20d",
                description="Moving-average alignment and overextension check for strong stocks.",
                required_data=["daily_ohlcv"],
                optional_data=["fundamental_growth", "estimate_revisions"],
                free_data_ready=True,
                invalidation_template="Health deteriorates if moving averages flatten or price becomes unsupported.",
            ),
            StrategyDefinition(
                strategy_id="catalyst_financial_transmission",
                name="Catalyst financial transmission",
                family="event_catalyst",
                role="primary",
                horizon="1-2q",
                description="News or policy catalyst mapped to revenue, margin, order, or valuation transmission.",
                required_data=["news_events", "exposure_map", "financial_metrics"],
                optional_data=["management_commentary", "supply_chain_map"],
                free_data_ready=False,
                invalidation_template="Catalyst weakens if no order, revenue, margin, or guidance verification appears.",
            ),
            StrategyDefinition(
                strategy_id="pead_earnings_drift",
                name="Post-earnings announcement drift",
                family="earnings_momentum",
                role="primary",
                horizon="5-60d",
                description="Positive earnings surprise with reasonable initial reaction and follow-through.",
                required_data=[
                    "earnings_actuals",
                    "earnings_estimates",
                    "announcement_timestamp",
                    "daily_ohlcv",
                ],
                optional_data=["guidance", "earnings_transcript", "benchmark_returns"],
                free_data_ready=False,
                invalidation_template="PEAD fails if price loses the earnings-day low or estimates reverse lower.",
            ),
            StrategyDefinition(
                strategy_id="analyst_revision_momentum",
                name="Analyst revision momentum",
                family="earnings_revision",
                role="confirmation",
                horizon="20-60d",
                description="EPS or revenue estimates revised upward after new information.",
                required_data=["analyst_estimates", "revision_timestamps"],
                optional_data=["target_price_revisions", "coverage_count"],
                free_data_ready=False,
                invalidation_template="Revision momentum fails if forward estimates flatten or reverse.",
            ),
            StrategyDefinition(
                strategy_id="tam_adj_peg_growth",
                name="TAM-adjusted PEG growth valuation",
                family="growth_valuation",
                role="valuation",
                horizon="1-3y",
                description="Growth valuation adjusted for market size, durability, quality, and margin conversion.",
                required_data=["fundamentals", "tam_assumptions", "valuation_multiples"],
                optional_data=["gross_margin", "net_retention", "competitive_intensity"],
                free_data_ready=False,
                invalidation_template="TAM-adjusted valuation weakens if growth duration or margin conversion decays.",
            ),
            StrategyDefinition(
                strategy_id="bayesian_intrinsic_growth",
                name="Bayesian intrinsic growth valuation",
                family="growth_valuation",
                role="valuation",
                horizon="3-5y",
                description="Updates intrinsic growth probability after earnings, traction, or price moves.",
                required_data=["fundamentals", "valuation_multiples", "growth_priors"],
                optional_data=["scenario_probabilities", "unit_economics"],
                free_data_ready=False,
                invalidation_template="Bayesian valuation weakens if new evidence lowers durable growth odds.",
            ),
            StrategyDefinition(
                strategy_id="sector_rotation_regime",
                name="Sector rotation and regime filter",
                family="market_regime",
                role="context",
                horizon="20-60d",
                description="Sector breadth and relative strength used as a global strategy weight.",
                required_data=["sector_constituents", "sector_breadth", "benchmark_returns"],
                optional_data=["macro_factors"],
                free_data_ready=False,
                invalidation_template="Regime support fades if sector breadth and benchmark trend roll over.",
            ),
            StrategyDefinition(
                strategy_id="short_squeeze_risk",
                name="Short squeeze risk monitor",
                family="risk_event",
                role="risk_control",
                horizon="1-10d",
                description="High short interest plus price/volume stress that may create squeeze risk.",
                required_data=["short_interest", "daily_ohlcv"],
                optional_data=["borrow_rate", "options_flow", "limit_status"],
                free_data_ready=False,
                invalidation_template="Squeeze risk fades when volume normalizes and price loses the trigger level.",
            ),
            StrategyDefinition(
                strategy_id="options_flow_confirmation",
                name="Options flow confirmation",
                family="derivatives_confirmation",
                role="confirmation",
                horizon="1-20d",
                description="Buyer-initiated options flow used only as confirmation, never as a standalone thesis.",
                required_data=["options_flow", "implied_volatility", "open_interest"],
                optional_data=["delta", "moneyness", "dte"],
                free_data_ready=False,
                invalidation_template="Options confirmation fails if flow is identified as hedge, spread, or IV-only.",
            ),
            StrategyDefinition(
                strategy_id="insider_institutional_confirmation",
                name="Insider and institutional confirmation",
                family="ownership_confirmation",
                role="confirmation",
                horizon="1-6m",
                description="Insider purchases, buybacks, and institutional ownership changes as slower confirmation.",
                required_data=["insider_transactions", "institutional_filings"],
                optional_data=["buyback_activity", "float_change"],
                free_data_ready=False,
                invalidation_template="Ownership confirmation weakens when purchases stop or filings show distribution.",
            ),
        ]
    )
