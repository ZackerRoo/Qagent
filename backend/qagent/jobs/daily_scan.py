from datetime import date

from pydantic import BaseModel

from qagent.cards.generator import OpportunityCardGenerator
from qagent.domain.models import OpportunityCard
from qagent.providers.base import MarketDataProvider
from qagent.signals.engine import SignalEngine
from qagent.strategy_data.models import EarningsEvent
from qagent.strategy_data.providers import StrategyDataProvider, build_strategy_data_provider
from qagent.strategies.evaluator import StrategyEvaluator
from qagent.strategies.health import build_strategy_health_from_bars
from qagent.strategies.models import StrategyEvaluation, StrategyHealth
from qagent.strategies.registry import default_strategy_registry


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
    provider: str | None = None


class DailyScanResult(BaseModel):
    cards: list[OpportunityCard]
    items: list[ScanItem]
    strategy_health: list[StrategyHealth]
    data_health: dict[str, str]


def run_daily_scan(
    instrument_ids: list[str],
    provider: MarketDataProvider,
    mode: str = "development",
    strategy_data_provider: StrategyDataProvider | None = None,
) -> DailyScanResult:
    cards: list[OpportunityCard] = []
    items: list[ScanItem] = []
    bars_by_instrument = {}
    signal_engine = SignalEngine()
    registry = default_strategy_registry()
    strategy_evaluator = StrategyEvaluator(registry)
    card_generator = OpportunityCardGenerator(strategy_evaluator)
    strategy_mode = provider.name if mode == "development" else mode
    strategy_provider = strategy_data_provider or build_strategy_data_provider(strategy_mode)

    for instrument_id in instrument_ids:
        bars = provider.get_daily_bars(
            instrument_ids=[instrument_id],
            start=date(2026, 1, 1),
            end=date(2026, 12, 31),
        )
        earnings_events = strategy_provider.get_earnings_events(
            instrument_ids=[instrument_id],
            start=date(2026, 1, 1),
            end=date(2026, 12, 31),
        )
        bars_by_instrument[instrument_id] = bars
        signals = signal_engine.generate(instrument_id, bars)
        strategy_evaluations = strategy_evaluator.evaluate(
            instrument_id,
            signals,
            bars,
            context={
                "earnings_events": earnings_events,
                "available_data": _available_strategy_data(earnings_events),
            },
        )
        card = card_generator.generate(instrument_id, signals, bars, strategy_evaluations)
        if card:
            cards.append(card)
        items.append(_scan_item(instrument_id, bars, signals, strategy_evaluations, card))

    strategy_health = build_strategy_health_from_bars(bars_by_instrument, registry)

    data_health = {
        "provider": provider.name,
        "mode": mode,
        "scanned": str(len(instrument_ids)),
        "cards": str(len(cards)),
        "strategy_data_provider": strategy_provider.name,
    }
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return DailyScanResult(
        cards=cards,
        items=items,
        strategy_health=strategy_health,
        data_health=data_health,
    )


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
            **strategy_counts,
        )

    latest = bars.sort_values("trade_date").iloc[-1]
    latest_close = str(round(float(latest["close"]), 2))
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
            provider=provider,
        )

    return ScanItem(
        instrument_id=instrument_id,
        status="no_setup",
        reason="Signal stack did not meet opportunity-card threshold.",
        bars=len(bars),
        signals=len(signals),
        **strategy_counts,
        latest_close=latest_close,
        provider=provider,
    )


def _strategy_counts(evaluations: list[StrategyEvaluation]) -> dict[str, int]:
    return {
        "strategies_passed": sum(1 for item in evaluations if item.status == "passed"),
        "strategies_watch": sum(1 for item in evaluations if item.status == "watch"),
        "strategies_missing_data": sum(1 for item in evaluations if item.status == "missing_data"),
    }


def _available_strategy_data(earnings_events: list[EarningsEvent]) -> list[str]:
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
    return available
