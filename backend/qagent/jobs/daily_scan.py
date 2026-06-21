from datetime import date

from pydantic import BaseModel

from qagent.cards.generator import OpportunityCardGenerator
from qagent.domain.models import OpportunityCard
from qagent.providers.base import MarketDataProvider
from qagent.signals.engine import SignalEngine


class ScanItem(BaseModel):
    instrument_id: str
    status: str
    reason: str
    bars: int
    signals: int
    latest_close: str | None = None
    provider: str | None = None


class DailyScanResult(BaseModel):
    cards: list[OpportunityCard]
    items: list[ScanItem]
    data_health: dict[str, str]


def run_daily_scan(
    instrument_ids: list[str], provider: MarketDataProvider, mode: str = "development"
) -> DailyScanResult:
    cards: list[OpportunityCard] = []
    items: list[ScanItem] = []
    signal_engine = SignalEngine()
    card_generator = OpportunityCardGenerator()

    for instrument_id in instrument_ids:
        bars = provider.get_daily_bars(
            instrument_ids=[instrument_id],
            start=date(2026, 1, 1),
            end=date(2026, 12, 31),
        )
        signals = signal_engine.generate(instrument_id, bars)
        card = card_generator.generate(instrument_id, signals, bars)
        if card:
            cards.append(card)
        items.append(_scan_item(instrument_id, bars, signals, card))

    data_health = {
        "provider": provider.name,
        "mode": mode,
        "scanned": str(len(instrument_ids)),
        "cards": str(len(cards)),
    }
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return DailyScanResult(cards=cards, items=items, data_health=data_health)


def _scan_item(
    instrument_id: str,
    bars,
    signals: list,
    card: OpportunityCard | None,
) -> ScanItem:
    if bars.empty:
        return ScanItem(
            instrument_id=instrument_id,
            status="no_data",
            reason="No daily bars returned by provider.",
            bars=0,
            signals=0,
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
            latest_close=latest_close,
            provider=provider,
        )

    return ScanItem(
        instrument_id=instrument_id,
        status="no_setup",
        reason="Signal stack did not meet opportunity-card threshold.",
        bars=len(bars),
        signals=len(signals),
        latest_close=latest_close,
        provider=provider,
    )
