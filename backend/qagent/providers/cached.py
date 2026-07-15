from datetime import date, datetime

import pandas as pd
from pydantic import BaseModel

from qagent.providers.base import MINUTE_BAR_COLUMNS, MarketDataProvider
from qagent.storage.market_cache import BAR_COLUMNS, MarketDataCacheRepository


class MarketDataCacheEvent(BaseModel):
    provider_mode: str
    instrument_id: str
    start: date
    end: date
    status: str
    rows: int


class CachedMarketDataProvider:
    def __init__(
        self,
        provider: MarketDataProvider,
        cache: MarketDataCacheRepository,
        provider_mode: str,
    ):
        self.provider = provider
        self.cache = cache
        self.provider_mode = provider_mode
        self.name = provider.name
        self.last_errors: list[str] = []
        self.last_cache_events: list[MarketDataCacheEvent] = []
        self.last_fallback_instruments: list[str] = []

    def reset_cache_stats(self) -> None:
        self.last_cache_events = []
        self.last_errors = []
        self.last_fallback_instruments = []

    def cache_stats(self) -> dict[str, int]:
        return {
            "hits": sum(1 for event in self.last_cache_events if event.status == "hit"),
            "misses": sum(1 for event in self.last_cache_events if event.status == "miss"),
            "rows": sum(event.rows for event in self.last_cache_events),
        }

    def get_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for instrument_id in instrument_ids:
            if self.cache.has_usable_coverage(
                self.provider_mode,
                instrument_id,
                start,
                end,
            ):
                cached = self.cache.load_daily_bars(
                    self.provider_mode,
                    [instrument_id],
                    start,
                    end,
                )
                self.last_cache_events.append(
                    MarketDataCacheEvent(
                        provider_mode=self.provider_mode,
                        instrument_id=instrument_id,
                        start=start,
                        end=end,
                        status="hit",
                        rows=len(cached),
                    )
                )
                if not cached.empty:
                    frames.append(cached)
                continue

            fetched = self.provider.get_daily_bars([instrument_id], start, end)
            self.last_errors.extend(getattr(self.provider, "last_errors", []))
            self.last_fallback_instruments.extend(
                getattr(self.provider, "last_fallback_instruments", [])
            )
            saved = self.cache.save_daily_bars(self.provider_mode, fetched)
            self.cache.record_coverage(
                self.provider_mode,
                instrument_id,
                start,
                end,
                row_count=saved,
            )
            self.last_cache_events.append(
                MarketDataCacheEvent(
                    provider_mode=self.provider_mode,
                    instrument_id=instrument_id,
                    start=start,
                    end=end,
                    status="miss",
                    rows=len(fetched),
                )
            )
            if saved > 0:
                persisted = self.cache.load_daily_bars(
                    self.provider_mode,
                    [instrument_id],
                    start,
                    end,
                )
                if not persisted.empty:
                    frames.append(persisted)
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.concat(frames, ignore_index=True).sort_values(
            ["instrument_id", "trade_date"]
        ).reset_index(drop=True)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        bars = self.get_daily_bars(instrument_ids, date(1900, 1, 1), date.today())
        if bars.empty:
            return bars
        return bars.groupby("instrument_id", as_index=False).tail(1).reset_index(drop=True)

    def get_minute_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        getter = getattr(self.provider, "get_minute_bars", None)
        if getter is None:
            return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
        frame = getter(instrument_ids, start, end)
        self.last_errors.extend(getattr(self.provider, "last_errors", []))
        return frame
