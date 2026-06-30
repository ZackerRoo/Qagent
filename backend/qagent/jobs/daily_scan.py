from datetime import date

from pydantic import BaseModel, Field

from qagent.cards.factor_watch import build_factor_watch_cards
from qagent.cards.generator import OpportunityCardGenerator
from qagent.domain.models import OpportunityCard, PortfolioPlan, SectorStrength, TradabilityAssessment, TradingStatus
from qagent.factors.engine import build_factor_rankings
from qagent.factors.models import FactorRanking
from qagent.market.instruments import format_instrument_label
from qagent.market.sector_strength import build_sector_strength
from qagent.market.tradability import evaluate_tradability
from qagent.market.trading_status import evaluate_trading_status
from qagent.monitoring.signal_monitor import SignalMonitorCenter, build_signal_monitor_center
from qagent.providers.base import MarketDataProvider
from qagent.recommendations.calibration import apply_strategy_calibration
from qagent.recommendations.cn_execution import build_trading_constraints
from qagent.recommendations.decision import build_research_decision
from qagent.recommendations.enrichment import enrich_opportunity_card
from qagent.recommendations.portfolio import build_portfolio_plan
from qagent.recommendations.quality_gate import (
    apply_recommendation_quality_gate,
    recommendation_quality_data_health,
)
from qagent.recommendations.rotation import sort_recommendation_cards
from qagent.research.action_center import ManualActionCenter, build_manual_action_center
from qagent.research.decision_quality import (
    DecisionQualityCenter,
    build_decision_quality_center,
)
from qagent.research.market_intelligence import (
    MarketIntelligenceCenter,
    apply_market_intelligence_to_cards,
    build_market_intelligence_center,
)
from qagent.research.operational_readiness import (
    OperationalReadinessCenter,
    build_operational_readiness_center,
)
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
    instrument_label: str | None = None
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
    trading_status: TradingStatus | None = None
    tradability: TradabilityAssessment | None = None
    blockers: list[ScanBlocker] = Field(default_factory=list)
    rejection_category: str | None = None
    rejection_score: float | None = Field(default=None, ge=0.0, le=1.0)
    remediation: str | None = None


class DailyScanResult(BaseModel):
    cards: list[OpportunityCard]
    items: list[ScanItem]
    strategy_health: list[StrategyHealth]
    factor_rankings: list[FactorRanking]
    sector_strength: list[SectorStrength]
    portfolio_plan: PortfolioPlan
    market_intelligence: MarketIntelligenceCenter | None = None
    manual_action_center: ManualActionCenter | None = None
    signal_monitor: SignalMonitorCenter | None = None
    decision_quality_center: DecisionQualityCenter | None = None
    operational_readiness_center: OperationalReadinessCenter | None = None
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
    trading_status_by_instrument = {}
    tradability_by_instrument = {}
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
    scan_error_samples: list[str] = []
    reset_cache_stats = getattr(provider, "reset_cache_stats", None)
    if callable(reset_cache_stats):
        reset_cache_stats()

    for instrument_id in instrument_ids:
        try:
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
            instrument_label = format_instrument_label(instrument_id)
            trading_constraints = build_trading_constraints(instrument_id, instrument_label)
            trading_status = evaluate_trading_status(instrument_id, bars, trading_constraints)
            tradability = evaluate_tradability(
                instrument_id,
                instrument_label,
                bars,
                trading_status,
                trading_constraints,
            )
            trading_status_by_instrument[instrument_id] = trading_status
            tradability_by_instrument[instrument_id] = tradability
            if card:
                card.trading_constraints = trading_constraints
                card.trading_status = trading_status
                card.tradability = tradability
                cards.append(card)
            items.append(
                _scan_item(
                    instrument_id,
                    bars,
                    signals,
                    strategy_evaluations,
                    card,
                    trading_status,
                    tradability,
                )
            )
        except Exception as exc:
            error_message = f"{instrument_id}: {exc}"
            scan_error_samples.append(error_message)
            items.append(_scan_error_item(instrument_id, exc))

    factor_rankings = _factor_rankings_from_bars(bars_by_instrument)
    for ranking in factor_rankings:
        ranking.instrument_label = format_instrument_label(ranking.instrument_id)
    factor_by_id = {ranking.instrument_id: ranking for ranking in factor_rankings}
    factor_watch_cards = build_factor_watch_cards(
        factor_rankings=factor_rankings,
        bars_by_instrument=bars_by_instrument,
        existing_instrument_ids={card.instrument_id for card in cards},
    )
    for card in factor_watch_cards:
        card.trading_status = trading_status_by_instrument.get(card.instrument_id)
        card.tradability = tradability_by_instrument.get(card.instrument_id)
        card.decision = build_research_decision(card)
        enrich_opportunity_card(card)
    cards.extend(factor_watch_cards)
    strategy_health = build_strategy_health_from_bars(bars_by_instrument, registry)
    apply_strategy_calibration(cards, strategy_health)
    for card in cards:
        _apply_factor_to_card(card, factor_by_id.get(card.instrument_id))
        card.decision = build_research_decision(card)
        enrich_opportunity_card(card)
    cards = sort_recommendation_cards(cards)
    for item in items:
        _apply_factor_to_item(item, factor_by_id.get(item.instrument_id))
    _promote_items_for_cards(items, cards)

    sector_strength = build_sector_strength(cards, bars_by_instrument)
    portfolio_plan = build_portfolio_plan(cards)

    data_health = {
        "provider": provider.name,
        "mode": mode,
        "scanned": str(len(instrument_ids)),
        "cards": str(len(cards)),
        "factor_rankings": str(len(factor_rankings)),
        "sector_strength": str(len(sector_strength)),
        "portfolio_allocations": str(len(portfolio_plan.allocations)),
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
    if scan_error_samples:
        data_health["scan_errors"] = str(len(scan_error_samples))
        data_health["scan_error_samples"] = " | ".join(scan_error_samples[:3])
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["provider_error_count"] = str(len(provider_errors))
        data_health["errors"] = " | ".join(provider_errors[:3])
    strategy_provider_errors = getattr(strategy_provider, "last_errors", [])
    if strategy_provider_errors:
        data_health["strategy_data_errors"] = " | ".join(strategy_provider_errors[:3])
    data_health.update(
        _a_share_data_readiness(
            cards=cards,
            items=items,
            bars_by_instrument=bars_by_instrument,
            data_health=data_health,
        )
    )

    market_intelligence = build_market_intelligence_center(
        cards=cards,
        items=items,
        bars_by_instrument=bars_by_instrument,
        strategy_health=strategy_health,
        data_health=data_health,
    )
    apply_market_intelligence_to_cards(cards, market_intelligence)
    apply_recommendation_quality_gate(cards)
    cards = sort_recommendation_cards(cards)
    sector_strength = build_sector_strength(cards, bars_by_instrument)
    portfolio_plan = build_portfolio_plan(cards)
    data_health.update(market_intelligence.data_health)
    data_health.update(recommendation_quality_data_health(cards))
    manual_action_center = build_manual_action_center(
        cards=cards,
        market_intelligence=market_intelligence,
        strategy_health=strategy_health,
        data_health=data_health,
    )
    data_health.update(manual_action_center.data_health)
    signal_monitor = build_signal_monitor_center(
        cards,
        bars_by_instrument=bars_by_instrument,
        as_of=end,
    )
    data_health.update(signal_monitor.data_health)
    decision_quality_center = build_decision_quality_center(
        cards=cards,
        market_intelligence=market_intelligence,
        portfolio_plan=portfolio_plan,
        signal_monitor=signal_monitor,
        strategy_health=strategy_health,
        data_health=data_health,
        as_of=end,
    )
    data_health.update(decision_quality_center.data_health)
    operational_readiness_center = build_operational_readiness_center(
        cards=cards,
        market_intelligence=market_intelligence,
        decision_quality_center=decision_quality_center,
        signal_monitor=signal_monitor,
        strategy_health=strategy_health,
        data_health=data_health,
        as_of=end,
    )
    data_health.update(operational_readiness_center.data_health)

    return DailyScanResult(
        cards=cards,
        items=items,
        strategy_health=strategy_health,
        factor_rankings=factor_rankings,
        sector_strength=sector_strength,
        portfolio_plan=portfolio_plan,
        market_intelligence=market_intelligence,
        manual_action_center=manual_action_center,
        signal_monitor=signal_monitor,
        decision_quality_center=decision_quality_center,
        operational_readiness_center=operational_readiness_center,
        data_health=data_health,
    )


def _scan_error_item(instrument_id: str, exc: Exception) -> ScanItem:
    return ScanItem(
        instrument_id=instrument_id,
        instrument_label=format_instrument_label(instrument_id),
        status="data_error",
        reason=f"Instrument scan failed: {exc}",
        bars=0,
        signals=0,
            blockers=[
                ScanBlocker(
                    code="instrument_scan_error",
                    severity="block",
                    title="Instrument scan error",
                    message=str(exc),
                )
            ],
            rejection_category="scan_error",
            rejection_score=1.0,
            remediation="重试行情源或暂时移出该标的，避免单只数据异常影响全市场排序。",
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


def _promote_items_for_cards(items: list[ScanItem], cards: list[OpportunityCard]) -> None:
    card_by_instrument = {card.instrument_id: card for card in cards}
    for item in items:
        card = card_by_instrument.get(item.instrument_id)
        if card is None or item.status != "no_setup":
            continue
        item.status = card.status.value
        if card.primary_strategy_id == "factor_rotation_watch":
            item.reason = "Factor ranking generated an observation card."
        else:
            item.reason = "Opportunity card generated."


def _scan_item(
    instrument_id: str,
    bars,
    signals: list,
    strategy_evaluations: list[StrategyEvaluation],
    card: OpportunityCard | None,
    trading_status: TradingStatus | None,
    tradability: TradabilityAssessment | None,
) -> ScanItem:
    strategy_counts = _strategy_counts(strategy_evaluations)
    if bars.empty:
        return ScanItem(
            instrument_id=instrument_id,
            instrument_label=format_instrument_label(instrument_id),
            status="no_data",
            reason="No daily bars returned by provider.",
            bars=0,
            signals=0,
            trading_status=trading_status,
            tradability=tradability,
            blockers=[
                ScanBlocker(
                    code="no_daily_bars",
                    severity="block",
                    title="No daily bars",
                    message="The market data provider did not return daily OHLCV bars.",
                )
            ],
            rejection_category="data_missing",
            rejection_score=1.0,
            remediation="补齐日线 OHLCV 后再进入推荐池；开发阶段可先换用 SQLite 最近快照。",
            **strategy_counts,
        )

    latest = bars.sort_values("trade_date").iloc[-1]
    latest_close = str(round(float(latest["close"]), 2))
    latest_trade_date = latest["trade_date"]
    provider = str(latest["provider"]) if "provider" in bars.columns else None
    if card:
        return ScanItem(
            instrument_id=instrument_id,
            instrument_label=format_instrument_label(instrument_id),
            status=card.status.value,
            reason="Opportunity card generated.",
            bars=len(bars),
            signals=len(signals),
            **strategy_counts,
            latest_close=latest_close,
            latest_trade_date=latest_trade_date,
            provider=provider,
            trading_status=trading_status,
            tradability=tradability,
        )

    blockers = _setup_blockers(signals, strategy_evaluations, strategy_counts)
    if trading_status and not trading_status.can_buy:
        blockers.append(
            ScanBlocker(
                code=f"trading_status_{trading_status.status}",
                severity=trading_status.severity,
                title=trading_status.label,
                message=" ".join(trading_status.notes),
            )
        )
    if tradability:
        blockers.extend(
            ScanBlocker(
                code=check.code,
                severity=check.severity,
                title=check.title,
                message=check.message,
            )
            for check in tradability.checks
            if check.severity == "block"
        )

    return ScanItem(
        instrument_id=instrument_id,
        instrument_label=format_instrument_label(instrument_id),
        status="no_setup",
        reason="Signal stack did not meet opportunity-card threshold.",
        bars=len(bars),
        signals=len(signals),
        blockers=blockers,
        **strategy_counts,
        latest_close=latest_close,
        latest_trade_date=latest_trade_date,
        provider=provider,
        trading_status=trading_status,
        tradability=tradability,
        rejection_category=_rejection_category(blockers),
        rejection_score=_rejection_score(blockers, signals, strategy_counts),
        remediation=_rejection_remediation(blockers),
    )


def _rejection_category(blockers: list[ScanBlocker]) -> str:
    codes = {blocker.code for blocker in blockers}
    if any(blocker.severity == "block" for blocker in blockers):
        return "execution_blocked"
    if "strategy_data_missing" in codes:
        return "data_missing"
    if "no_active_signals" in codes or "no_strategy_passed" in codes or "signal_threshold_not_met" in codes:
        return "weak_signal"
    return "not_ranked"


def _rejection_score(
    blockers: list[ScanBlocker],
    signals: list,
    strategy_counts: dict[str, int],
) -> float:
    score = min(1.0, 0.2 + len(blockers) * 0.16)
    if not signals:
        score += 0.18
    if strategy_counts["strategies_passed"] == 0:
        score += 0.18
    if any(blocker.severity == "block" for blocker in blockers):
        score += 0.24
    return round(min(1.0, score), 4)


def _rejection_remediation(blockers: list[ScanBlocker]) -> str:
    category = _rejection_category(blockers)
    if category == "execution_blocked":
        return "先确认涨跌停、停牌、流动性和权限限制；可交易性恢复前不要纳入买入候选。"
    if category == "data_missing":
        return "补齐策略需要的数据源，如复权价格、财报公告、资金流或足够历史 K 线。"
    if category == "weak_signal":
        return "等待趋势、突破、量能或健康回调信号增强，再重新进入排序。"
    return "当前排序优势不足，继续观察因子分、策略胜率和市场环境是否改善。"


def _a_share_data_readiness(
    *,
    cards: list[OpportunityCard],
    items: list[ScanItem],
    bars_by_instrument: dict[str, object],
    data_health: dict[str, str],
) -> dict[str, str]:
    cn_items = [item for item in items if item.instrument_id.startswith("CN:")]
    cn_cards = [card for card in cards if card.instrument_id.startswith("CN:")]
    if not cn_items and not cn_cards:
        return {
            "a_share_data_readiness_score": "0.00",
            "a_share_data_scope": "no_cn_symbols",
        }

    liquidity_ready = any(
        item.tradability and (item.tradability.avg_amount_20d or item.tradability.avg_volume_20d)
        for item in cn_items
    )
    price_limit_ready = any(item.trading_status for item in cn_items) or any(
        card.trading_constraints and card.trading_constraints.price_limit_pct is not None
        for card in cn_cards
    )
    industry_ready = any(card.market_context for card in cn_cards)
    index_ready = any(
        card.market_context and card.market_context.index_memberships for card in cn_cards
    )
    has_etf = any(card.asset_type.upper() == "ETF" for card in cn_cards)
    statuses = {
        "a_share_adjusted_price": "ready"
        if data_health.get("adjusted_bars")
        else "partial"
        if data_health.get("provider") in {"free", "fixture"}
        else "missing",
        "a_share_suspension": "ready" if any(item.trading_status for item in cn_items) else "missing",
        "a_share_price_limit": "ready" if price_limit_ready else "missing",
        "a_share_industry": "ready" if industry_ready else "missing",
        "a_share_liquidity": "ready" if liquidity_ready else "partial" if cn_items else "missing",
        "a_share_turnover": "partial" if liquidity_ready else "missing",
        "a_share_index_constituents": "ready" if index_ready else "partial" if has_etf else "missing",
        "a_share_fund_flow": "ready" if data_health.get("fund_flow") else "missing",
        "a_share_announcements": "ready"
        if _int_health(data_health, "strategy_announcements") > 0
        else "partial"
        if _int_health(data_health, "strategy_fundamentals") > 0
        else "missing",
    }
    score = sum(_readiness_value(status) for status in statuses.values()) / len(statuses)
    covered_bars = sum(
        1
        for instrument_id, bars in bars_by_instrument.items()
        if instrument_id.startswith("CN:") and not bars.empty
    )
    return {
        **statuses,
        "a_share_data_readiness_score": f"{score:.2f}",
        "a_share_bars_coverage": f"{covered_bars}/{len(cn_items)}",
    }


def _readiness_value(status: str) -> float:
    if status == "ready":
        return 1.0
    if status == "partial":
        return 0.55
    return 0.0


def _int_health(source: dict[str, str], key: str) -> int:
    try:
        return int(source.get(key, "0"))
    except (TypeError, ValueError):
        return 0


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
