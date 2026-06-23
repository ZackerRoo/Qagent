from pydantic import BaseModel, Field

from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE


class UniverseCreate(BaseModel):
    universe_id: str
    name: str
    description: str
    market_scope: str
    tags: list[str] = Field(default_factory=list)
    symbols: list[str]


class UniverseRecord(UniverseCreate):
    source: str = "custom"


def builtin_universes() -> list[UniverseRecord]:
    return [
        UniverseRecord(
            universe_id="fixture_dev",
            name="Fixture Dev",
            description="Deterministic US + A-share fixture symbols for local development.",
            market_scope="mixed",
            tags=["fixture", "dev"],
            symbols=DEFAULT_DEV_UNIVERSE,
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="free_default",
            name="Free Default",
            description="Starter free-data universe used by the dashboard.",
            market_scope="mixed",
            tags=["free", "default"],
            symbols=DEFAULT_FREE_UNIVERSE,
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="us_ai_growth_starter",
            name="US AI Growth Starter",
            description="Editable starter pool for large US AI and growth infrastructure names.",
            market_scope="US",
            tags=["us", "ai", "growth"],
            symbols=["US:NVDA", "US:MSFT", "US:AMD", "US:AVGO", "US:TSM"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="us_mega_cap_starter",
            name="US Mega Cap Starter",
            description="Editable starter pool for high-liquidity US mega-cap technology names.",
            market_scope="US",
            tags=["us", "mega_cap", "liquid"],
            symbols=["US:AAPL", "US:MSFT", "US:NVDA", "US:AMZN", "US:GOOGL", "US:META"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_tech_starter",
            name="CN Technology Starter",
            description="Editable starter pool for China A-share technology and advanced manufacturing names.",
            market_scope="CN",
            tags=["cn", "technology", "starter"],
            symbols=["CN:000063", "CN:002230", "CN:002415", "CN:300124", "CN:300750"],
            source="builtin_starter",
        ),
    ]


def merge_universes(custom_universes: list[UniverseRecord]) -> list[UniverseRecord]:
    by_id = {universe.universe_id: universe for universe in builtin_universes()}
    for universe in custom_universes:
        by_id[universe.universe_id] = universe
    return sorted(by_id.values(), key=lambda item: (item.source != "builtin_starter", item.name))


def normalize_symbols(symbols: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for symbol in symbols:
        value = symbol.strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized
