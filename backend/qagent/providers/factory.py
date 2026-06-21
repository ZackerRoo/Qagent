from qagent.providers.base import MarketDataProvider
from qagent.providers.composite import CompositeMarketDataProvider
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.providers.free_cn import FreeCnMarketDataProvider
from qagent.providers.free_us import FreeUsMarketDataProvider


def build_market_data_provider(provider_mode: str) -> MarketDataProvider:
    mode = provider_mode.strip().lower()
    if mode == "fixture":
        return FixtureMarketDataProvider()
    if mode == "free":
        return CompositeMarketDataProvider(
            {
                "US": FreeUsMarketDataProvider(),
                "CN": FreeCnMarketDataProvider(),
            },
            name="free",
        )
    raise ValueError(f"unsupported provider mode: {provider_mode}")
