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
            name="A-Share Free Default",
            description="Default A-share free-data universe used by the dashboard.",
            market_scope="CN",
            tags=["free", "default", "cn"],
            symbols=DEFAULT_FREE_UNIVERSE,
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_tech_starter",
            name="A-Share Technology Starter",
            description="Editable starter pool for China A-share technology and advanced manufacturing names.",
            market_scope="CN",
            tags=["cn", "technology", "starter"],
            symbols=["CN:000063", "CN:002230", "CN:002415", "CN:300124", "CN:300750"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_blue_chip_starter",
            name="A-Share Blue Chip Starter",
            description="Editable starter pool for high-liquidity China A-share blue-chip names.",
            market_scope="CN",
            tags=["cn", "blue_chip", "liquid"],
            symbols=["CN:000001", "CN:600036", "CN:600519", "CN:601318", "CN:601398"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_growth_starter",
            name="A-Share Growth Starter",
            description="Editable starter pool for China A-share growth and new-economy names.",
            market_scope="CN",
            tags=["cn", "growth", "starter"],
            symbols=["CN:300750", "CN:300124", "CN:002230", "CN:002475", "CN:300760"],
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
