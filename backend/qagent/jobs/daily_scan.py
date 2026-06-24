from datetime import date

from pydantic import BaseModel, Field

from qagent.cards.generator import OpportunityCardGenerator
from qagent.domain.models import OpportunityCard
from qagent.factors.engine import build_factor_rankings
from qagent.factors.models import FactorRanking
from qagent.providers.base import MarketDataProvider
from qagent.recommendations.decision import build_research_decision
from qagent.signals.engine import SignalEngine
from qagent.strategy_data.models import AnalystInsight, EarningsEvent, FilingEvent, FundamentalSnapshot
from qagent.strategy_data.providers import StrategyDataProvider, build_strategy_data_provider
from qagent.strategies.evaluator import StrategyEvaluator
from qagent.strategies.health import build_strategy_health_from_bars
from qagent.strategies.models import StrategyEvaluation, StrategyHealth
from qagent.strategies.registry import default_strategy_registry


class ScanBlocker(BaseModel):
    code: str
    severity: str
    title: str
    message: str


class ScanItem(BaseModel):
    instrument_id: str
    status: str
    reason: str
    bars: int
    signals: int
    strategies_passed: int = 0
    strategies_watch: int = 0
    strategies_missing_data: int = 0
    latest_close: str | None = None
    latest_trade_date: date | None = None
    provider: str | None = None
    factor_score: float | None = None
    factor_rank: int | None = None
    factor_flags: list[str] = Field(default_factory=list)
    blockers: list[ScanBlocker] = Field(default_factory=list)


class DailyScanResult(BaseModel):
    cards: list[OpportunityCard]
    items: list[ScanItem]
    strategy_health: list[StrategyHealth]
    factor_rankings: list[FactorRanking]
    data_health: dict[str, str]


def run_daily_scan(
    instrument_ids: list[str],
    provider: MarketDataProvider,
    mode: str = "development",
    strategy_data_provider: StrategyDataProvider | None = None,
    start: date = date(2026, 1, 1),
    end: date = date(2026, 12, 31),
) -> DailyScanResult:
    cards: list[OpportunityCard] = []
    items: list[ScanItem] = []
    bars_by_instrument = {}
    strategy_filings_count = 0
    strategy_announcements_count = 0
    strategy_fundamentals_count = 0
    strategy_analyst_insights_count = 0
    signal_engine = SignalEngine()
    registry = default_strategy_registry()
    strategy_evaluator = StrategyEvaluator(registry)
    card_generator = OpportunityCardGenerator(strategy_evaluator)
    strategy_mode = provider.name if mode == "development" else mode
    strategy_provider = strategy_data_provider or build_strategy_data_provider(strategy_mode)
    reset_cache_stats = getattr(provider, "reset_cache_stats", None)
    if callable(reset_cache_stats):
        reset_cache_stats()

    for instrument_id in instrument_ids:
        bars = provider.get_daily_bars(
            instrument_ids=[instrument_id],
            start=start,
            end=end,
        )
        earnings_events = strategy_provider.get_earnings_events(
            instrument_ids=[instrument_id],
            start=start,
            end=end,
        )
        filings = strategy_provider.get_filings(
            instrument_ids=[instrument_id],
            start=start,
            end=end,
        )
        announcements = strategy_provider.get_announcements(
            instrument_ids=[instrument_id],
            start=start,
            end=end,
        )
        fundamentals = strategy_provider.get_fundamentals(
            instrument_ids=[instrument_id],
            start=start,
            end=end,
        )
        analyst_insights = strategy_provider.get_analyst_insights(
            instrument_ids=[instrument_id],
            start=start,
            end=end,
        )
        strategy_filings_count += len(filings)
        strategy_announcements_count += len(announcements)
        strategy_fundamentals_count += len(fundamentals)
        strategy_analyst_insights_count += len(analyst_insights)
        bars_by_instrument[instrument_id] = bars
        signals = signal_engine.generate(instrument_id, bars)
        strategy_evaluations = strategy_evaluator.evaluate(
            instrument_id,
            signals,
            bars,
            context={
                "earnings_events": earnings_events,
                "filings": filings,
                "announcements": announcements,
                "fundamentals": fundamentals,
                "analyst_insights": analyst_insights,
                "available_data": _available_strategy_data(
                    earnings_events,
                    fundamentals,
                    analyst_insights,
                    filings,
                ),
            },
        )
        card = card_generator.generate(instrument_id, signals, bars, strategy_evaluations)
        if card:
            cards.append(card)
        items.append(_scan_item(instrument_id, bars, signals, strategy_evaluations, card))

    factor_rankings = _factor_rankings_from_bars(bars_by_instrument)
    factor_by_id = {ranking.instrument_id: ranking for ranking in factor_rankings}
    for card in cards:
        _apply_factor_to_card(card, factor_by_id.get(card.instrument_id))
        card.decision = build_research_decision(card)
    cards.sort(key=_card_priority_score, reverse=True)
    for item in items:
        _apply_factor_to_item(item, factor_by_id.get(item.instrument_id))

    strategy_health = build_strategy_health_from_bars(bars_by_instrument, registry)

    data_health = {
        "provider": provider.name,
        "mode": mode,
        "scanned": str(len(instrument_ids)),
        "cards": str(len(cards)),
        "factor_rankings": str(len(factor_rankings)),
        "strategy_data_provider": strategy_provider.name,
        "strategy_filings": str(strategy_filings_count),
        "strategy_announcements": str(strategy_announcements_count),
        "strategy_fundamentals": str(strategy_fundamentals_count),
        "strategy_analyst_insights": str(strategy_analyst_insights_count),
    }
    cache_stats = getattr(provider, "cache_stats", None)
    if callable(cache_stats):
        stats = cache_stats()
        data_health["market_cache"] = "enabled"
        data_health["market_cache_hits"] = str(stats["hits"])
        data_health["market_cache_misses"] = str(stats["misses"])
        data_health["market_cache_rows"] = str(stats["rows"])
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    strategy_provider_errors = getattr(strategy_provider, "last_errors", [])
    if strategy_provider_errors:
        data_health["strategy_data_errors"] = " | ".join(strategy_provider_errors[:3])
    return DailyScanResult(
        cards=cards,
        items=items,
        strategy_health=strategy_health,
        factor_rankings=factor_rankings,
        data_health=data_health,
    )


def _factor_rankings_from_bars(bars_by_instrument: dict[str, object]) -> list[FactorRanking]:
    frames = [bars for bars in bars_by_instrument.values() if not bars.empty]
    if not frames:
        return []
    import pandas as pd

    return build_factor_rankings(pd.concat(frames, ignore_index=True))


def _apply_factor_to_card(card: OpportunityCard, ranking: FactorRanking | None) -> None:
    if ranking is None:
        return
    card.factor_score = ranking.factor_score
    card.factor_rank = ranking.factor_rank
    card.factor_percentile = ranking.percentile
    card.factor_flags = ranking.flags
    card.factor_exposures = ranking.factor_exposures
    if ranking.flags:
        card.rank_reasons.extend([f"factor flag: {flag}" for flag in ranking.flags])


def _apply_factor_to_item(item: ScanItem, ranking: FactorRanking | None) -> None:
    if ranking is None:
        return
    item.factor_score = ranking.factor_score
    item.factor_rank = ranking.factor_rank
    item.factor_flags = ranking.flags


def _card_priority_score(card: OpportunityCard) -> float:
    return round(card.rank_score * 0.55 + card.factor_score * 0.45, 4)


def _scan_item(
    instrument_id: str,
    bars,
    signals: list,
    strategy_evaluations: list[StrategyEvaluation],
    card: OpportunityCard | None,
) -> ScanItem:
    strategy_counts = _strategy_counts(strategy_evaluations)
    if bars.empty:
        return ScanItem(
            instrument_id=instrument_id,
            status="no_data",
            reason="No daily bars returned by provider.",
            bars=0,
            signals=0,
            blockers=[
                ScanBlocker(
                    code="no_daily_bars",
                    severity="block",
                    title="No daily bars",
                    message="The market data provider did not return daily OHLCV bars.",
                )
            ],
            **strategy_counts,
        )

    latest = bars.sort_values("trade_date").iloc[-1]
    latest_close = str(round(float(latest["close"]), 2))
    latest_trade_date = latest["trade_date"]
    provider = str(latest["provider"]) if "provider" in bars.columns else None
    if card:
        return ScanItem(
            instrument_id=instrument_id,
            status=card.status.value,
            reason="Opportunity card generated.",
            bars=len(bars),
            signals=len(signals),
            **strategy_counts,
            latest_close=latest_close,
            latest_trade_date=latest_trade_date,
            provider=provider,
        )

    return ScanItem(
        instrument_id=instrument_id,
        status="no_setup",
        reason="Signal stack did not meet opportunity-card threshold.",
        bars=len(bars),
        signals=len(signals),
        blockers=_setup_blockers(signals, strategy_evaluations, strategy_counts),
        **strategy_counts,
        latest_close=latest_close,
        latest_trade_date=latest_trade_date,
        provider=provider,
    )


def _strategy_counts(evaluations: list[StrategyEvaluation]) -> dict[str, int]:
    return {
        "strategies_passed": sum(1 for item in evaluations if item.status == "passed"),
        "strategies_watch": sum(1 for item in evaluations if item.status == "watch"),
        "strategies_missing_data": sum(1 for item in evaluations if item.status == "missing_data"),
    }


def _setup_blockers(
    signals: list,
    evaluations: list[StrategyEvaluation],
    strategy_counts: dict[str, int],
) -> list[ScanBlocker]:
    blockers = [
        ScanBlocker(
            code="signal_threshold_not_met",
            severity="watch",
            title="Signal threshold not met",
            message="The signal stack did not reach the opportunity-card threshold.",
        )
    ]
    if not signals:
        blockers.append(
            ScanBlocker(
                code="no_active_signals",
                severity="watch",
                title="No active signals",
                message="No trend, pullback, breakout, volume, or limit-status signal is active.",
            )
        )
    if strategy_counts["strategies_passed"] == 0:
        blockers.append(
            ScanBlocker(
                code="no_strategy_passed",
                severity="watch",
                title="No strategy passed",
                message="No strategy in the registry passed its preconditions.",
            )
        )
    missing = sorted(
        {
            item
            for evaluation in evaluations
            for item in evaluation.missing_data
        }
    )
    if missing:
        blockers.append(
            ScanBlocker(
                code="strategy_data_missing",
                severity="watch",
                title="Strategy data missing",
                message=f"Missing strategy inputs: {', '.join(missing[:5])}.",
            )
        )
    return blockers


def _available_strategy_data(
    earnings_events: list[EarningsEvent],
    fundamentals: list[FundamentalSnapshot] | None = None,
    analyst_insights: list[AnalystInsight] | None = None,
    filings: list[FilingEvent] | None = None,
) -> list[str]:
    available = []
    if any(
        event.actual_eps is not None and event.actual_revenue is not None
        for event in earnings_events
    ):
        available.append("earnings_actuals")
    if any(
        event.estimated_eps is not None and event.estimated_revenue is not None
        for event in earnings_events
    ):
        available.append("earnings_estimates")
    if any(event.announcement_time in {"bmo", "amc", "intraday"} for event in earnings_events):
        available.append("announcement_timestamp")
    fundamentals = fundamentals or []
    if any(snapshot.has_growth_inputs for snapshot in fundamentals):
        available.append("fundamentals")
        available.append("growth_priors")
    if any(snapshot.has_valuation_inputs for snapshot in fundamentals):
        available.append("valuation_multiples")
    if any(
        snapshot.market_cap is not None and snapshot.has_growth_inputs and snapshot.has_valuation_inputs
        for snapshot in fundamentals
    ):
        available.append("tam_assumptions")
    analyst_insights = analyst_insights or []
    if analyst_insights:
        available.append("analyst_estimates")
    if any(insight.has_revision_inputs for insight in analyst_insights):
        available.append("revision_timestamps")
    filings = filings or []
    forms = {filing.form.upper() for filing in filings}
    if forms.intersection({"3", "4", "5"}):
        available.append("insider_transactions")
    if any(form.startswith("13F") or form in {"SC 13D", "SC 13G"} for form in forms):
        available.append("institutional_filings")
    return available
