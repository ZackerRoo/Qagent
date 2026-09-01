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


def test_cached_provider_delegates_snapshot_to_live_provider(tmp_path):
    repo = make_cache_repo(tmp_path)

    class LiveSnapshotProvider(CountingProvider):
        def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "instrument_id": instrument_ids[0],
                        "trade_date": date(2026, 8, 12),
                        "open": 10.0,
                        "high": 10.5,
                        "low": 9.9,
                        "close": 10.4,
                        "volume": 1_000,
                        "provider": "live_snapshot",
                    }
                ]
            )

    provider = LiveSnapshotProvider()
    cached = CachedMarketDataProvider(provider, repo, "free")

    snapshot = cached.get_snapshot(["CN:000001"])

    assert snapshot.iloc[0]["provider"] == "live_snapshot"
    assert provider.calls == 0


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
    loaded = repo.load_daily_bars("free", ["CN:601288"], date(2014, 7, 24), date(2014, 7, 24))

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


def test_market_data_cache_latest_daily_bar_skips_invalid_newer_rows(tmp_path):
    repo = make_cache_repo(tmp_path)
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 5),
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 800_000,
                "provider": "fixture",
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 1, 6),
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "close": 0.0,
                "volume": 0,
                "provider": "fixture",
            },
        ]
    )
    repo.save_daily_bars("fixture", bars)

    latest = repo.load_latest_daily_bars("fixture", ["CN:000001"])

    assert latest["trade_date"].tolist() == [date(2026, 1, 5)]
    assert latest["close"].tolist() == [10.2]


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
    loaded = repo.load_daily_bars("free", ["CN:159560"], date(2026, 7, 14), date(2026, 7, 15))

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
    repo.record_coverage("free", "CN:159582", date(2026, 7, 1), date(2026, 7, 8), len(bars))

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


class BatchCountingProvider(CountingProvider):
    def __init__(self):
        super().__init__()
        self.batch_calls = 0

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        self.batch_calls += 1
        return self.fixture.get_daily_bars(instrument_ids, start, end)


class EmptyBatchProvider(BatchCountingProvider):
    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        self.batch_calls += 1
        return pd.DataFrame()


class IncrementalBatchProvider(CountingProvider):
    def __init__(self):
        super().__init__()
        self.batch_requests: list[tuple[tuple[str, ...], date, date]] = []

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        self.batch_requests.append((tuple(instrument_ids), start, end))
        return pd.DataFrame(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": trade_date.date(),
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 800_000,
                    "provider": self.name,
                }
                for instrument_id in instrument_ids
                for trade_date in pd.date_range(start, end, freq="B")
            ]
        )


class TailSnapshotRepairProvider(CountingProvider):
    def __init__(self, snapshot_date: date):
        super().__init__()
        self.snapshot_date = snapshot_date
        self.snapshot_calls: list[list[str]] = []
        self.history_calls: list[list[str]] = []

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        del start, end
        self.history_calls.append(instrument_ids)
        return pd.DataFrame()

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        self.snapshot_calls.append(instrument_ids)
        return pd.DataFrame(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": self.snapshot_date,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 800_000,
                    "turnover": 8_000_000,
                    "provider": "fuyao_realtime",
                }
                for instrument_id in instrument_ids
            ]
        )


class SettledTailHistoryRetryProvider(TailSnapshotRepairProvider):
    def __init__(self, expected: date):
        super().__init__(expected + pd.Timedelta(days=1))
        self.expected = expected
        self.history_calls: list[list[str]] = []

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        self.history_calls.append(instrument_ids)
        if len(self.history_calls) == 1:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": self.expected,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 800_000,
                    "provider": "baostock_paired",
                    "adjusted_open": 10.0,
                    "adjusted_high": 10.5,
                    "adjusted_low": 9.8,
                    "adjusted_close": 10.2,
                    "adjustment_factor": 1.0,
                    "adjustment_type": "qfq",
                }
                for instrument_id in instrument_ids
            ]
        )


class RawOnlySettledTailRetryProvider(TailSnapshotRepairProvider):
    def __init__(self, expected: date):
        super().__init__(expected + pd.Timedelta(days=1))
        self.expected = expected

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        del start, end
        self.history_calls.append(instrument_ids)
        if len(self.history_calls) == 1:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": self.expected,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 800_000,
                    "provider": "raw_settled_history",
                }
                for instrument_id in instrument_ids
            ]
        )


class PartialStaleBatchProvider(CountingProvider):
    def _partial(self, instrument_ids: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": date(2026, 1, 5),
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 800_000,
                    "provider": self.name,
                }
                for instrument_id in instrument_ids
            ]
        )

    def get_historical_daily_bars(self, instrument_ids, start, end):
        del start, end
        return self._partial(instrument_ids)

    def get_daily_bars(self, instrument_ids, start, end):
        del start, end
        self.calls += 1
        return self._partial(instrument_ids)


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


def test_cached_provider_prefetches_batch_before_per_symbol_reads(tmp_path):
    repo = make_cache_repo(tmp_path)
    inner = BatchCountingProvider()
    provider = CachedMarketDataProvider(inner, cache=repo, provider_mode="fixture")
    instrument_ids = ["US:TEST", "CN:000001"]
    start = date(2026, 1, 1)
    end = date(2026, 1, 20)

    provider.prefetch_daily_bars(instrument_ids, start, end)
    bars = provider.get_daily_bars(instrument_ids, start, end)

    assert inner.batch_calls == 1
    assert inner.calls == 0
    assert set(bars["instrument_id"]) == set(instrument_ids)


def test_cached_provider_does_not_repeat_empty_batch_prefetch_per_symbol(tmp_path):
    repo = make_cache_repo(tmp_path)
    inner = EmptyBatchProvider()
    provider = CachedMarketDataProvider(inner, cache=repo, provider_mode="fixture")
    instrument_ids = ["US:MISS", "CN:999999"]
    start = date(2026, 1, 1)
    end = date(2026, 1, 20)

    provider.prefetch_daily_bars(instrument_ids, start, end)
    bars = provider.get_daily_bars(instrument_ids, start, end)

    assert bars.empty
    assert inner.batch_calls == 1
    assert inner.calls == 0


def test_cached_provider_prefetches_only_missing_tail_sessions(tmp_path):
    repo = make_cache_repo(tmp_path)
    inner = IncrementalBatchProvider()
    provider = CachedMarketDataProvider(inner, cache=repo, provider_mode="free")
    instrument_ids = ["CN:000001", "CN:000002"]

    provider.prefetch_daily_bars(
        instrument_ids,
        date(2026, 1, 1),
        date(2026, 1, 5),
    )
    provider.prefetch_daily_bars(
        instrument_ids,
        date(2026, 1, 1),
        date(2026, 1, 7),
    )
    second_stats = provider.prefetch_stats()
    provider.prefetch_daily_bars(
        instrument_ids,
        date(2026, 1, 1),
        date(2026, 1, 7),
    )

    assert inner.batch_requests == [
        (("CN:000001", "CN:000002"), date(2026, 1, 1), date(2026, 1, 5)),
        (("CN:000001", "CN:000002"), date(2026, 1, 6), date(2026, 1, 7)),
    ]
    assert second_stats["mode"] == "incremental_tail"
    assert second_stats["refresh_candidates"] == 2
    assert second_stats["cold_starts"] == 0
    assert second_stats["request_groups"] == 1
    assert second_stats["refreshed"] == 2
    assert second_stats["stale_after_refresh"] == 0
    assert provider.prefetch_stats()["already_current"] == 2

    bars = repo.load_daily_bars(
        "free",
        instrument_ids,
        date(2026, 1, 1),
        date(2026, 1, 7),
    )
    assert bars.groupby("instrument_id")["trade_date"].max().to_dict() == {
        "CN:000001": date(2026, 1, 7),
        "CN:000002": date(2026, 1, 7),
    }
    assert not bars.duplicated(["instrument_id", "trade_date"]).any()


def test_cached_provider_repairs_internal_gaps_when_tail_is_current(tmp_path):
    repo = make_cache_repo(tmp_path)
    inner = IncrementalBatchProvider()
    provider = CachedMarketDataProvider(inner, cache=repo, provider_mode="free")
    instrument_id = "CN:000001"
    start = date(2026, 1, 1)
    end = date(2026, 1, 9)
    repo.save_daily_bars(
        "free",
        pd.DataFrame(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": end,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 800_000,
                    "provider": "fixture",
                }
            ]
        ),
    )
    repo.record_coverage("free", instrument_id, start, end, row_count=1)

    provider.prefetch_daily_bars([instrument_id], start, end)

    assert inner.batch_requests == [((instrument_id,), start, end)]
    assert provider.prefetch_stats()["gap_repairs"] == 1
    assert provider.prefetch_stats()["refreshed"] == 1
    bars = provider.get_daily_bars([instrument_id], start, end)
    assert not bars.empty
    assert inner.calls == 0


def test_cached_provider_keeps_raw_snapshot_but_does_not_fabricate_qfq(tmp_path):
    repo = make_cache_repo(tmp_path)
    expected = date(2026, 8, 12)
    instrument_id = "CN:300229"
    repo.save_daily_bars(
        "free",
        pd.DataFrame(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": date(2026, 8, 10),
                    "open": 9.7,
                    "high": 10.0,
                    "low": 9.6,
                    "close": 9.9,
                    "volume": 680_000,
                    "provider": "baostock_paired",
                },
                {
                    "instrument_id": instrument_id,
                    "trade_date": date(2026, 8, 11),
                    "open": 9.8,
                    "high": 10.1,
                    "low": 9.7,
                    "close": 10.0,
                    "volume": 700_000,
                    "provider": "baostock_paired",
                }
            ]
        ),
    )
    inner = TailSnapshotRepairProvider(expected)
    provider = CachedMarketDataProvider(
        inner,
        cache=repo,
        provider_mode="free",
        enable_recent_tail_snapshot_repair=True,
    )

    provider.prefetch_daily_bars(
        [instrument_id],
        date(2026, 8, 10),
        expected,
        repair_recent_tail=True,
    )

    bars = repo.load_daily_bars(
        "free",
        [instrument_id],
        date(2026, 8, 10),
        expected,
    )
    latest = bars.sort_values("trade_date").iloc[-1]
    assert inner.snapshot_calls == [[instrument_id]]
    assert latest["trade_date"] == expected
    assert latest["provider"] == "fuyao_realtime"
    assert pd.isna(latest["adjusted_close"])
    assert latest["adjustment_type"] is None
    assert inner.history_calls == [[instrument_id]]
    assert provider.prefetch_stats()["snapshot_requested"] == 1
    assert provider.prefetch_stats()["snapshot_repaired"] == 1
    assert provider.prefetch_stats()["snapshot_unrecovered"] == 0


def test_cached_provider_repairs_one_missing_adjusted_tail_from_safe_history(tmp_path):
    repo = make_cache_repo(tmp_path)
    expected = date(2026, 8, 12)
    instrument_id = "CN:603439"
    repo.save_daily_bars(
        "free",
        pd.DataFrame(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": expected,
                    "open": 40.0,
                    "high": 43.0,
                    "low": 39.5,
                    "close": 42.0,
                    "volume": 800_000,
                    "provider": "akshare",
                }
            ]
        ),
    )
    repo.record_coverage("free", instrument_id, expected, expected, row_count=1)
    inner = SettledTailHistoryRetryProvider(expected)
    # The bounded adjusted-tail repair is the second call for this fixture.
    inner.history_calls.append([])
    provider = CachedMarketDataProvider(
        inner,
        cache=repo,
        provider_mode="free",
        enable_recent_tail_snapshot_repair=True,
    )

    provider.prefetch_daily_bars(
        [instrument_id],
        expected,
        expected,
        repair_recent_tail=True,
    )

    repaired = repo.load_daily_bars("free", [instrument_id], expected, expected).iloc[0]
    assert float(repaired["close"]) == 42.0
    assert float(repaired["adjusted_close"]) == 10.2
    assert repaired["adjustment_type"] == "qfq"
    assert inner.history_calls[-1] == [instrument_id]
    assert provider.prefetch_stats()["adjusted_tail_requested"] == 1
    assert provider.prefetch_stats()["adjusted_tail_repaired"] == 1
    assert provider.prefetch_stats()["adjusted_tail_unrecovered"] == 0


def test_cached_provider_quarantines_snapshot_outside_expected_session(tmp_path):
    repo = make_cache_repo(tmp_path)
    expected = date(2026, 8, 12)
    instrument_id = "CN:300229"
    inner = TailSnapshotRepairProvider(date(2026, 8, 13))
    provider = CachedMarketDataProvider(
        inner,
        cache=repo,
        provider_mode="free",
        enable_recent_tail_snapshot_repair=True,
    )

    provider.prefetch_daily_bars(
        [instrument_id],
        date(2026, 8, 10),
        expected,
        repair_recent_tail=True,
    )

    assert repo.load_daily_bars(
        "free",
        [instrument_id],
        date(2026, 8, 10),
        date(2026, 8, 13),
    ).empty
    assert provider.prefetch_stats()["snapshot_requested"] == 1
    assert provider.prefetch_stats()["snapshot_repaired"] == 0
    assert provider.prefetch_stats()["snapshot_unrecovered"] == 1


def test_cached_provider_retries_unresolved_settled_tail_in_fresh_history_session(tmp_path):
    repo = make_cache_repo(tmp_path)
    expected = date(2026, 8, 12)
    instrument_ids = ["CN:300229", "CN:300230"]
    inner = SettledTailHistoryRetryProvider(expected)
    provider = CachedMarketDataProvider(
        inner,
        cache=repo,
        provider_mode="free",
        enable_recent_tail_snapshot_repair=True,
    )

    provider.prefetch_daily_bars(
        instrument_ids,
        date(2026, 8, 10),
        expected,
        repair_recent_tail=True,
    )

    bars = repo.load_daily_bars("free", instrument_ids, expected, expected)
    assert set(bars["instrument_id"]) == set(instrument_ids)
    assert bars["provider"].unique().tolist() == ["baostock_paired"]
    assert inner.history_calls == [instrument_ids, instrument_ids]
    assert provider.prefetch_stats()["snapshot_unrecovered"] == 2
    assert provider.prefetch_stats()["settled_tail_retry_requested"] == 2
    assert provider.prefetch_stats()["settled_tail_retry_repaired"] == 2
    assert provider.prefetch_stats()["settled_tail_retry_unrecovered"] == 0


def test_cached_provider_preserves_raw_only_settled_tail_retry(tmp_path):
    repo = make_cache_repo(tmp_path)
    expected = date(2026, 8, 12)
    instrument_id = "CN:300229"
    inner = RawOnlySettledTailRetryProvider(expected)
    provider = CachedMarketDataProvider(
        inner,
        cache=repo,
        provider_mode="free",
        enable_recent_tail_snapshot_repair=True,
    )

    provider.prefetch_daily_bars(
        [instrument_id],
        date(2026, 8, 10),
        expected,
        repair_recent_tail=True,
    )

    bars = repo.load_daily_bars("free", [instrument_id], expected, expected)
    assert len(bars) == 1
    assert bars.iloc[0]["provider"] == "raw_settled_history"
    assert float(bars.iloc[0]["close"]) == 10.2
    assert pd.isna(bars.iloc[0]["adjusted_close"])
    assert bars.iloc[0]["adjustment_type"] is None
    assert provider.prefetch_stats()["settled_tail_retry_repaired"] == 1


def test_cached_provider_does_not_treat_partial_stale_history_as_empty(tmp_path):
    repo = make_cache_repo(tmp_path)
    inner = PartialStaleBatchProvider()
    provider = CachedMarketDataProvider(inner, cache=repo, provider_mode="free")
    instrument_id = "CN:000001"
    start = date(2026, 1, 1)
    end = date(2026, 1, 9)

    provider.prefetch_daily_bars([instrument_id], start, end)
    bars = provider.get_daily_bars([instrument_id], start, end)

    assert not bars.empty
    assert bars["trade_date"].tolist() == [date(2026, 1, 5)]
    assert inner.calls == 1
