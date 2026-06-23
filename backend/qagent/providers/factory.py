from qagent.db import create_session_factory, initialize_database
from qagent.providers.base import MarketDataProvider
from qagent.providers.cached import CachedMarketDataProvider
from qagent.providers.composite import CompositeMarketDataProvider
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.providers.free_cn import FreeCnMarketDataProvider
from qagent.providers.free_us import FreeUsMarketDataProvider
from qagent.storage.market_cache import MarketDataCacheRepository


def build_market_data_provider(provider_mode: str) -> MarketDataProvider:
    mode = provider_mode.strip().lower()
    if mode == "fixture":
        return _with_market_cache(FixtureMarketDataProvider(), mode)
    if mode == "free":
        return _with_market_cache(
            CompositeMarketDataProvider(
                {
                    "US": FreeUsMarketDataProvider(),
                    "CN": FreeCnMarketDataProvider(),
                },
                name="free",
            ),
            mode,
        )
    raise ValueError(f"unsupported provider mode: {provider_mode}")


def _with_market_cache(provider: MarketDataProvider, provider_mode: str) -> MarketDataProvider:
    initialize_database()
    cache = MarketDataCacheRepository(create_session_factory())
    return CachedMarketDataProvider(provider, cache=cache, provider_mode=provider_mode)
