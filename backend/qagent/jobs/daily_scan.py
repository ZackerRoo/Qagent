from datetime import date

from pydantic import BaseModel

from qagent.cards.generator import OpportunityCardGenerator
from qagent.domain.models import OpportunityCard
from qagent.providers.base import MarketDataProvider
from qagent.signals.engine import SignalEngine


class DailyScanResult(BaseModel):
    cards: list[OpportunityCard]
    data_health: dict[str, str]


def run_daily_scan(
    instrument_ids: list[str], provider: MarketDataProvider, mode: str = "development"
) -> DailyScanResult:
    cards: list[OpportunityCard] = []
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

    data_health = {
        "provider": provider.name,
        "mode": mode,
        "scanned": str(len(instrument_ids)),
        "cards": str(len(cards)),
    }
    provider_errors = getattr(provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return DailyScanResult(cards=cards, data_health=data_health)
