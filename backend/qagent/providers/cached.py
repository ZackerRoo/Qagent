from datetime import date, datetime, timedelta

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
        self._prefetched_empty_ranges: set[tuple[str, date, date]] = set()
        self._pending_prefetch_errors: list[str] = []
        self._pending_prefetch_fallback_instruments: list[str] = []
        self.last_prefetch_stats: dict[str, int | str] = _empty_prefetch_stats()

    def reset_cache_stats(self) -> None:
        self.last_cache_events = []
        self.last_errors = self._pending_prefetch_errors
        self.last_fallback_instruments = self._pending_prefetch_fallback_instruments
        self._pending_prefetch_errors = []
        self._pending_prefetch_fallback_instruments = []

    def cache_stats(self) -> dict[str, int]:
        return {
            "hits": sum(1 for event in self.last_cache_events if event.status == "hit"),
            "misses": sum(1 for event in self.last_cache_events if event.status == "miss"),
            "rows": sum(event.rows for event in self.last_cache_events),
        }

    def prefetch_stats(self) -> dict[str, int | str]:
        return dict(self.last_prefetch_stats)

    def get_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for instrument_id in instrument_ids:
            coverage_key = (instrument_id, start, end)
            if self.cache.has_usable_coverage(
                self.provider_mode,
                instrument_id,
                start,
                end,
                maximum_trailing_session_gap=(
                    1 if instrument_id.startswith("CN:") else None
                ),
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

            # A batch prefetch already exercised every configured source for
            # this exact range. Do not repeat the same slow fallback chain once
            # per instrument when the batch result was empty.
            if coverage_key in self._prefetched_empty_ranges:
                self.last_cache_events.append(
                    MarketDataCacheEvent(
                        provider_mode=self.provider_mode,
                        instrument_id=instrument_id,
                        start=start,
                        end=end,
                        status="miss",
                        rows=0,
                    )
                )
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

    def prefetch_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> None:
        """Fill cache misses in one provider batch before per-symbol analysis."""

        requested = list(dict.fromkeys(instrument_ids))
        self._pending_prefetch_errors = []
        self._pending_prefetch_fallback_instruments = []
        missing = [
            instrument_id
            for instrument_id in requested
            if not self.cache.has_usable_coverage(
                self.provider_mode,
                instrument_id,
                start,
                end,
                minimum_session_coverage=(
                    0.95 if instrument_id.startswith("CN:") else None
                ),
                maximum_trailing_session_gap=(
                    0 if instrument_id.startswith("CN:") else None
                ),
            )
        ]
        latest_dates = self.cache.latest_trade_dates(
            self.provider_mode,
            missing,
            not_after=end,
        )
        refresh_groups: dict[date, list[str]] = {}
        for instrument_id in missing:
            latest = latest_dates.get(instrument_id)
            # A current tail with unusable coverage means the gap is inside the
            # cached history, so repair the requested range instead of skipping it.
            refresh_start = (
                start
                if latest is None or latest >= end
                else max(start, latest + timedelta(days=1))
            )
            if refresh_start <= end:
                refresh_groups.setdefault(refresh_start, []).append(instrument_id)

        self.last_prefetch_stats = {
            "mode": "incremental_tail",
            "requested": len(requested),
            "already_current": len(requested) - len(missing),
            "refresh_candidates": len(missing),
            "cold_starts": sum(instrument_id not in latest_dates for instrument_id in missing),
            "gap_repairs": sum(latest_dates.get(instrument_id) == end for instrument_id in missing),
            "request_groups": len(refresh_groups),
            "fetched_rows": 0,
            "refreshed": 0,
            "stale_after_refresh": 0,
        }
        if not missing:
            return

        getter = getattr(self.provider, "get_historical_daily_bars", None)
        frames: list[pd.DataFrame] = []
        errors: list[str] = []
        fallback_instruments: list[str] = []
        for refresh_start, group in sorted(refresh_groups.items()):
            fetched_group = (
                getter(group, refresh_start, end)
                if callable(getter)
                else self.provider.get_daily_bars(group, refresh_start, end)
            )
            errors.extend(getattr(self.provider, "last_errors", []))
            fallback_instruments.extend(
                getattr(self.provider, "last_fallback_instruments", [])
            )
            if not fetched_group.empty:
                frames.append(fetched_group)
        self._pending_prefetch_errors = list(dict.fromkeys(errors))
        self._pending_prefetch_fallback_instruments = list(
            dict.fromkeys(fallback_instruments)
        )
        fetched = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=BAR_COLUMNS)
        self.cache.save_daily_bars(self.provider_mode, fetched)
        cached_counts = self.cache.count_daily_bars_by_instrument(
            self.provider_mode,
            requested,
            start,
            end,
        )
        for instrument_id in missing:
            self.cache.record_coverage(
                self.provider_mode,
                instrument_id,
                start,
                end,
                row_count=int(cached_counts.get(instrument_id, 0)),
            )
        usable_after_refresh = {
            instrument_id: self.cache.has_usable_coverage(
                self.provider_mode,
                instrument_id,
                start,
                end,
                minimum_session_coverage=(
                    0.95 if instrument_id.startswith("CN:") else None
                ),
                maximum_trailing_session_gap=(
                    0 if instrument_id.startswith("CN:") else None
                ),
            )
            for instrument_id in missing
        }
        for instrument_id, usable in usable_after_refresh.items():
            coverage_key = (instrument_id, start, end)
            if usable:
                self._prefetched_empty_ranges.discard(coverage_key)
            else:
                self._prefetched_empty_ranges.add(coverage_key)
        refreshed = sum(usable_after_refresh.values())
        self.last_prefetch_stats.update(
            {
                "fetched_rows": len(fetched),
                "refreshed": refreshed,
                "stale_after_refresh": len(missing) - refreshed,
            }
        )

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


def _empty_prefetch_stats() -> dict[str, int | str]:
    return {
        "mode": "not_run",
        "requested": 0,
        "already_current": 0,
        "refresh_candidates": 0,
        "cold_starts": 0,
        "gap_repairs": 0,
        "request_groups": 0,
        "fetched_rows": 0,
        "refreshed": 0,
        "stale_after_refresh": 0,
    }
