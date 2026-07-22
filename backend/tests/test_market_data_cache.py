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


def test_market_data_cache_chunks_bulk_upserts_below_sqlite_variable_limit(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = pd.DataFrame(
        [
            {
                "instrument_id": f"CN:{instrument_number:06d}",
                "trade_date": date(2026, 1, 5),
                "open": 10.0,
                "high": 10.5,
                "low": 9.9,
                "close": 10.3,
                "volume": 800_000,
                "turnover": 8_000_000,
                "provider": "akshare",
            }
            for instrument_number in range(1, 121)
        ]
    )

    assert repo.save_daily_bars("free", bars) == 120
    assert len(repo.list_summaries(provider_mode="free")) == 120


def test_market_data_cache_preserves_adjustment_metadata(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 5),
                "open": 10.0,
                "high": 10.5,
                "low": 9.9,
                "close": 10.3,
                "volume": 800_000,
                "turnover": 8_000_000,
                "provider": "akshare_stock_paired",
                "adjusted_open": 5.0,
                "adjusted_high": 5.25,
                "adjusted_low": 4.95,
                "adjusted_close": 5.15,
                "adjustment_factor": 0.5,
                "adjustment_type": "qfq",
            }
        ]
    )

    repo.save_daily_bars("free", bars)
    loaded = repo.load_daily_bars(
        "free",
        ["CN:000001"],
        date(2026, 1, 1),
        date(2026, 1, 31),
    )
    summary = repo.list_summaries("free", "CN:000001")[0]

    assert float(loaded.iloc[0]["turnover"]) == 8_000_000
    assert float(loaded.iloc[0]["adjusted_open"]) == 5.0
    assert float(loaded.iloc[0]["adjusted_high"]) == 5.25
    assert float(loaded.iloc[0]["adjusted_low"]) == 4.95
    assert float(loaded.iloc[0]["adjusted_close"]) == 5.15
    assert float(loaded.iloc[0]["adjustment_factor"]) == 0.5
    assert loaded.iloc[0]["adjustment_type"] == "qfq"
    assert summary.adjusted_rows == 1
    assert summary.adjustment_types == ["qfq"]


def test_market_data_cache_clears_invalid_adjusted_ohlc(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:601288",
                "trade_date": date(2014, 7, 24),
                "open": 2.41,
                "high": 2.46,
                "low": 2.40,
                "close": 2.45,
                "volume": 800_000,
                "provider": "akshare_stock_paired",
                "adjusted_open": -0.03,
                "adjusted_high": 0.02,
                "adjusted_low": -0.03,
                "adjusted_close": 0.01,
                "adjustment_factor": 0.004,
                "adjustment_type": "qfq",
            }
        ]
    )

    assert repo.save_daily_bars("free", bars) == 1
    loaded = repo.load_daily_bars(
        "free", ["CN:601288"], date(2014, 7, 24), date(2014, 7, 24)
    )

    assert loaded.iloc[0]["close"] == 2.45
    assert pd.isna(loaded.iloc[0]["adjusted_close"])
    assert pd.isna(loaded.iloc[0]["adjustment_factor"])
    assert pd.isna(loaded.iloc[0]["adjustment_type"])


def test_market_data_cache_rejects_partial_cn_span_as_usable_coverage(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": trade_date,
                "open": 10.0,
                "high": 10.5,
                "low": 9.9,
                "close": 10.3,
                "volume": 800_000,
                "provider": "akshare_qfq",
                "adjusted_close": 10.3,
                "adjustment_factor": 1.0,
                "adjustment_type": "qfq",
            }
            for trade_date in [date(2026, 1, 5), date(2026, 1, 6)]
        ]
    )
    repo.save_daily_bars("free", bars)
    repo.record_coverage(
        "free",
        "CN:000001",
        date(2026, 1, 1),
        date(2026, 1, 9),
        len(bars),
    )

    assert not repo.has_usable_coverage(
        "free",
        "CN:000001",
        date(2026, 1, 1),
        date(2026, 1, 9),
        require_adjusted=True,
        minimum_session_coverage=0.95,
    )


def test_market_data_cache_loads_latest_daily_bar_per_instrument(tmp_path):
    repo = make_cache_repo(tmp_path)
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(
        ["US:TEST", "CN:000001"],
        date(2026, 1, 1),
        date(2026, 3, 31),
    )

    repo.save_daily_bars("fixture", bars)

    latest = repo.load_latest_daily_bars("fixture", ["CN:000001", "US:TEST", "US:MISS"])

    assert latest["instrument_id"].tolist() == ["CN:000001", "US:TEST"]
    assert latest.groupby("instrument_id")["trade_date"].nunique().tolist() == [1, 1]
    assert latest["trade_date"].tolist() == [
        bars[bars["instrument_id"].eq("CN:000001")]["trade_date"].max(),
        bars[bars["instrument_id"].eq("US:TEST")]["trade_date"].max(),
    ]


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


def test_market_data_cache_drops_nonfinite_ohlc_rows(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 2),
                "open": 10.0,
                "high": 10.4,
                "low": 9.9,
                "close": 10.3,
                "volume": 800_000,
                "provider": "akshare",
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 5),
                "open": 10.2,
                "high": float("inf"),
                "low": 10.1,
                "close": 10.4,
                "volume": 820_000,
                "provider": "akshare",
            },
        ]
    )

    saved = repo.save_daily_bars("free", bars)
    loaded = repo.load_daily_bars("free", ["CN:000001"], date(2026, 1, 1), date(2026, 1, 31))

    assert saved == 1
    assert loaded["trade_date"].tolist() == [date(2026, 1, 2)]


def test_market_data_cache_drops_structurally_invalid_ohlc_rows(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:159560",
                "trade_date": date(2026, 7, 14),
                "open": 2.82,
                "high": 2.93,
                "low": 2.80,
                "close": 2.89,
                "volume": 8_000_000,
                "provider": "yfinance_cn_etf_paired",
            },
            {
                "instrument_id": "CN:159560",
                "trade_date": date(2026, 7, 15),
                "open": 3.22,
                "high": 2.91,
                "low": 2.70,
                "close": 2.89,
                "volume": 9_000_000,
                "provider": "yfinance_cn_etf_paired",
            },
        ]
    )

    saved = repo.save_daily_bars("free", bars)
    loaded = repo.load_daily_bars(
        "free", ["CN:159560"], date(2026, 7, 14), date(2026, 7, 15)
    )

    assert saved == 1
    assert loaded["trade_date"].tolist() == [date(2026, 7, 14)]


def test_market_data_cache_rejects_stale_cn_trailing_coverage(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:159582",
                "trade_date": trade_date,
                "open": 4.60,
                "high": 4.72,
                "low": 4.55,
                "close": 4.66,
                "volume": 8_000_000,
                "provider": "akshare_etf_paired",
            }
            for trade_date in [date(2026, 7, 1), date(2026, 7, 2)]
        ]
    )
    repo.save_daily_bars("free", bars)
    repo.record_coverage(
        "free", "CN:159582", date(2026, 7, 1), date(2026, 7, 8), len(bars)
    )

    assert not repo.has_usable_coverage(
        "free",
        "CN:159582",
        date(2026, 7, 1),
        date(2026, 7, 8),
        maximum_trailing_session_gap=1,
    )


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
