from datetime import date
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from qagent.db import Base, create_db_engine, create_session_factory
from qagent.providers.cached import CachedMarketDataProvider
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.storage.market_cache import MarketDataCacheRepository


def make_cache_repo(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'market-cache.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(database_url)
    return MarketDataCacheRepository(session_factory)


def test_market_data_cache_saves_and_loads_daily_bars(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = FixtureMarketDataProvider().get_daily_bars(
        ["US:TEST"], date(2026, 1, 1), date(2026, 1, 20)
    )

    saved = repo.save_daily_bars("fixture", bars)
    loaded = repo.load_daily_bars("fixture", ["US:TEST"], date(2026, 1, 1), date(2026, 1, 20))
    summaries = repo.list_summaries()

    assert saved > 0
    assert not loaded.empty
    assert loaded["instrument_id"].eq("US:TEST").all()
    assert loaded["trade_date"].min() >= date(2026, 1, 1)
    assert loaded["trade_date"].max() <= date(2026, 1, 20)
    assert summaries[0].provider_mode == "fixture"
    assert summaries[0].instrument_id == "US:TEST"
    assert summaries[0].rows == saved
    assert summaries[0].source_providers == ["fixture"]


def test_market_data_cache_coerces_missing_volume_to_zero(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:688347",
                "trade_date": date(2025, 8, 18),
                "open": 78.5,
                "high": 78.5,
                "low": 78.5,
                "close": 78.5,
                "volume": float("nan"),
                "provider": "baostock",
            }
        ]
    )

    saved = repo.save_daily_bars("free", bars)
    loaded = repo.load_daily_bars("free", ["CN:688347"], date(2025, 8, 18), date(2025, 8, 18))

    assert saved == 1
    assert float(loaded.iloc[0]["volume"]) == 0.0


def test_market_data_cache_records_coverage_idempotently_under_concurrency(tmp_path):
    repo = make_cache_repo(tmp_path)

    def record_once(_index: int) -> None:
        repo.record_coverage("free", "CN:000021", date(1900, 1, 1), date(2100, 1, 1), 0)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(record_once, range(12)))

    assert repo.has_coverage("free", "CN:000021", date(2026, 1, 1), date(2026, 12, 31))


def test_market_data_cache_upserts_daily_bars_under_concurrency(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = FixtureMarketDataProvider().get_daily_bars(
        ["US:TEST"], date(2026, 1, 1), date(2026, 1, 20)
    )

    def save_once(_index: int) -> int:
        return repo.save_daily_bars("fixture", bars)

    with ThreadPoolExecutor(max_workers=4) as executor:
        saved_counts = list(executor.map(save_once, range(12)))

    loaded = repo.load_daily_bars("fixture", ["US:TEST"], date(2026, 1, 1), date(2026, 1, 20))

    assert all(saved == len(bars) for saved in saved_counts)
    assert len(loaded) == len(bars)


class CountingProvider:
    name = "fixture"

    def __init__(self):
        self.calls = 0
        self.fixture = FixtureMarketDataProvider()

    def get_daily_bars(self, instrument_ids: list[str], start: date, end: date) -> pd.DataFrame:
        self.calls += 1
        return self.fixture.get_daily_bars(instrument_ids, start, end)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        bars = self.get_daily_bars(instrument_ids, date(1900, 1, 1), date(2100, 1, 1))
        if bars.empty:
            return bars
        return bars.groupby("instrument_id", as_index=False).tail(1).reset_index(drop=True)


def test_cached_provider_uses_cached_daily_bars_for_same_range(tmp_path):
    repo = make_cache_repo(tmp_path)
    inner = CountingProvider()
    provider = CachedMarketDataProvider(inner, cache=repo, provider_mode="fixture")

    first = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 1, 20))
    second = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 1, 20))

    assert not first.empty
    assert second.equals(first)
    assert inner.calls == 1
    assert [event.status for event in provider.last_cache_events] == ["miss", "hit"]
    assert provider.cache_stats()["hits"] == 1
    assert provider.cache_stats()["misses"] == 1
