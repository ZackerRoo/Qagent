from pydantic import BaseModel, Field

from qagent.market.universe import (
    DEFAULT_A_SHARE_STARTER_UNIVERSE,
    DEFAULT_DEV_UNIVERSE,
    DEFAULT_FREE_UNIVERSE,
)


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
            name="A-Share Full Market",
            description="Full A-share free-data universe token. The scanner expands it into filtered liquid candidates.",
            market_scope="CN",
            tags=["free", "default", "cn", "full_market"],
            symbols=DEFAULT_FREE_UNIVERSE,
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_liquid_starter",
            name="A-Share Liquid Starter",
            description="Fast 30-name A-share pool for development and smoke checks.",
            market_scope="CN",
            tags=["cn", "liquid", "starter"],
            symbols=DEFAULT_A_SHARE_STARTER_UNIVERSE,
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_index_kcb50",
            name="STAR 50 Constituents",
            description="Science and Technology Innovation Board 50 index constituents.",
            market_scope="CN",
            tags=["cn", "index", "star50", "constituents"],
            symbols=["CN:INDEX:KCB50"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_index_csi300",
            name="CSI 300 Constituents",
            description="CSI 300 index constituents.",
            market_scope="CN",
            tags=["cn", "index", "csi300", "constituents"],
            symbols=["CN:INDEX:CSI300"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_index_csi500",
            name="CSI 500 Constituents",
            description="CSI 500 index constituents.",
            market_scope="CN",
            tags=["cn", "index", "csi500", "constituents"],
            symbols=["CN:INDEX:CSI500"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_index_csi1000",
            name="CSI 1000 Constituents",
            description="CSI 1000 index constituents.",
            market_scope="CN",
            tags=["cn", "index", "csi1000", "constituents"],
            symbols=["CN:INDEX:CSI1000"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_index_chinext50",
            name="ChiNext 50 Constituents",
            description="ChiNext 50 index constituents.",
            market_scope="CN",
            tags=["cn", "index", "chinext50", "constituents"],
            symbols=["CN:INDEX:CHINEXT50"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_etf_core",
            name="Core China Index ETFs",
            description="Representative A-share broad index and growth-board ETFs.",
            market_scope="CN",
            tags=["cn", "etf", "index"],
            symbols=[
                "CN:ETF:KCB50",
                "CN:ETF:CSI300",
                "CN:ETF:CSI500",
                "CN:ETF:CSI1000",
                "CN:ETF:CHINEXT50",
            ],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_theme_semiconductor",
            name="A-Share Semiconductor & Chip Theme",
            description="Theme pool for A-share semiconductor, chip equipment, design, foundry, and AI chip names.",
            market_scope="CN",
            tags=["cn", "theme", "semiconductor", "chip"],
            symbols=["CN:THEME:SEMICONDUCTOR"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_theme_memory",
            name="A-Share Memory Chip Theme",
            description="Theme pool for A-share memory, HBM, DRAM, packaging, and storage-chip names.",
            market_scope="CN",
            tags=["cn", "theme", "memory", "chip"],
            symbols=["CN:THEME:MEMORY"],
            source="builtin_starter",
        ),
        UniverseRecord(
            universe_id="cn_theme_ai_compute",
            name="A-Share AI Compute Chain",
            description="Theme pool for A-share AI compute, optical module, server, and AI accelerator names.",
            market_scope="CN",
            tags=["cn", "theme", "ai_compute"],
            symbols=["CN:THEME:AI_COMPUTE"],
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
