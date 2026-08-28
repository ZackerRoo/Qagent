from qagent.config import get_settings
from qagent.db import create_session_factory, initialize_database
from qagent.providers.base import MarketDataProvider
from qagent.providers.cached import CachedMarketDataProvider
from qagent.providers.composite import CompositeMarketDataProvider
from qagent.providers.daily_fallback import DailyFallbackMarketDataProvider
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.providers.free_cn import FreeCnMarketDataProvider
from qagent.providers.failure_state import ProviderFailureStateRegistry
from qagent.providers.free_us import FreeUsMarketDataProvider
from qagent.providers.fuyao import FuyaoMarketDataProvider
from qagent.providers.snapshot_preferred import SnapshotPreferredMarketDataProvider
from qagent.providers.tickflow_free import TickFlowFreeDailyProvider
from qagent.storage.market_cache import MarketDataCacheRepository


def build_market_data_provider(provider_mode: str) -> MarketDataProvider:
    mode = provider_mode.strip().lower()
    if mode == "fixture":
        return _with_market_cache(FixtureMarketDataProvider(), mode)
    if mode == "free":
        settings = get_settings()
        failure_registry = ProviderFailureStateRegistry()
        free_cn = FreeCnMarketDataProvider()
        tickflow = TickFlowFreeDailyProvider()
        # Assign after construction so lightweight test/provider substitutes that
        # implement the existing zero-argument contract remain compatible.
        if hasattr(free_cn, "failure_registry"):
            free_cn.failure_registry = failure_registry
        if hasattr(tickflow, "failure_registry"):
            tickflow.failure_registry = failure_registry
        cn_provider: MarketDataProvider = DailyFallbackMarketDataProvider(
            free_cn,
            tickflow,
            name="free_cn",
            max_fallback_instruments=20,
            max_fallback_batches=10,
            fallback_time_budget_seconds=120,
        )
        if settings.fuyao_api_key:
            fuyao = FuyaoMarketDataProvider(
                settings.fuyao_api_key,
                base_url=settings.fuyao_base_url,
                request_timeout_seconds=settings.fuyao_timeout_seconds,
                failure_registry=failure_registry,
            )
            cn_provider = SnapshotPreferredMarketDataProvider(
                DailyFallbackMarketDataProvider(
                    cn_provider,
                    fuyao,
                    name="free_cn",
                    max_fallback_instruments=20,
                    max_fallback_batches=10,
                    fallback_time_budget_seconds=120,
                ),
                fuyao,
                name="free_cn",
                max_preferred_instruments=50,
            )
        return _with_market_cache(
            CompositeMarketDataProvider(
                {
                    "US": FreeUsMarketDataProvider(),
                    "CN": cn_provider,
                },
                name="free",
            ),
            mode,
            enable_recent_tail_snapshot_repair=bool(settings.fuyao_api_key),
        )
    raise ValueError(f"unsupported provider mode: {provider_mode}")


def _with_market_cache(
    provider: MarketDataProvider,
    provider_mode: str,
    *,
    enable_recent_tail_snapshot_repair: bool = False,
) -> MarketDataProvider:
    initialize_database()
    cache = MarketDataCacheRepository(create_session_factory())
    return CachedMarketDataProvider(
        provider,
        cache=cache,
        provider_mode=provider_mode,
        enable_recent_tail_snapshot_repair=enable_recent_tail_snapshot_repair,
    )
