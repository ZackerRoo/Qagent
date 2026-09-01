from datetime import date, datetime, timedelta
import math

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
        *,
        enable_recent_tail_snapshot_repair: bool = False,
        snapshot_repair_batch_size: int = 50,
        settled_tail_retry_batch_size: int = 20,
    ):
        self.provider = provider
        self.cache = cache
        self.provider_mode = provider_mode
        self.enable_recent_tail_snapshot_repair = enable_recent_tail_snapshot_repair
        self.snapshot_repair_batch_size = max(1, snapshot_repair_batch_size)
        self.settled_tail_retry_batch_size = max(1, settled_tail_retry_batch_size)
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

    def prefetch_refresh_candidates(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[str]:
        """Return instruments that would require provider I/O for this range."""

        return [
            instrument_id
            for instrument_id in dict.fromkeys(instrument_ids)
            if not self.cache.has_usable_coverage(
                self.provider_mode,
                instrument_id,
                start,
                end,
                minimum_session_coverage=(0.95 if instrument_id.startswith("CN:") else None),
                maximum_trailing_session_gap=(0 if instrument_id.startswith("CN:") else None),
            )
        ]

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
                maximum_trailing_session_gap=(1 if instrument_id.startswith("CN:") else None),
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
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values(["instrument_id", "trade_date"])
            .reset_index(drop=True)
        )

    def prefetch_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
        *,
        repair_recent_tail: bool = False,
    ) -> None:
        """Fill cache misses in one provider batch before per-symbol analysis."""

        requested = list(dict.fromkeys(instrument_ids))
        self._pending_prefetch_errors = []
        self._pending_prefetch_fallback_instruments = []
        missing = self.prefetch_refresh_candidates(requested, start, end)
        adjusted_tail_missing = self.cache.instruments_missing_valid_adjusted_close(
            self.provider_mode,
            [item for item in requested if item.startswith("CN:")],
            end,
        )
        self.last_prefetch_stats = {
            "mode": "incremental_tail",
            "requested": len(requested),
            "already_current": len(requested) - len(missing),
            "refresh_candidates": len(missing),
            "cold_starts": 0,
            "gap_repairs": 0,
            "request_groups": 0,
            "fetched_rows": 0,
            "refreshed": 0,
            "stale_after_refresh": 0,
            "snapshot_repair_enabled": str(
                repair_recent_tail and self.enable_recent_tail_snapshot_repair
            ).lower(),
            "snapshot_requested": 0,
            "snapshot_rows": 0,
            "snapshot_repaired": 0,
            "snapshot_unrecovered": 0,
            "snapshot_errors": 0,
            "settled_tail_retry_requested": 0,
            "settled_tail_retry_repaired": 0,
            "settled_tail_retry_unrecovered": 0,
            "settled_tail_retry_errors": 0,
            "adjusted_tail_requested": len(adjusted_tail_missing),
            "adjusted_tail_repaired": 0,
            "adjusted_tail_unrecovered": len(adjusted_tail_missing),
        }
        if not missing and not adjusted_tail_missing:
            return

        getter = getattr(self.provider, "get_historical_daily_bars", None)
        frames: list[pd.DataFrame] = []
        errors: list[str] = []
        fallback_instruments: list[str] = []
        latest_dates = self.cache.latest_trade_dates(
            self.provider_mode,
            missing,
            not_after=end,
        )
        self.last_prefetch_stats.update(
            {
                "cold_starts": sum(instrument_id not in latest_dates for instrument_id in missing),
                "gap_repairs": sum(latest_dates.get(instrument_id) == end for instrument_id in missing),
            }
        )
        snapshot_targets = [
            instrument_id
            for instrument_id in missing
            if instrument_id.startswith("CN:") and latest_dates.get(instrument_id) != end
        ]
        if repair_recent_tail and self.enable_recent_tail_snapshot_repair:
            # A completed-session snapshot can cheaply repair the newest bar
            # before a large history request falls through to rate-limited
            # providers. The normalizer rejects every other trade date.
            snapshot_frame, snapshot_errors, snapshot_fallbacks = (
                self._repair_recent_tail_from_snapshot(snapshot_targets, end=end)
            )
            errors.extend(snapshot_errors)
            fallback_instruments.extend(snapshot_fallbacks)
            repaired_ids = _instrument_ids_on_date(snapshot_frame, end)
            if repaired_ids:
                snapshot_counts = self.cache.count_daily_bars_by_instrument(
                    self.provider_mode,
                    sorted(repaired_ids),
                    start,
                    end,
                )
                for instrument_id in repaired_ids:
                    self.cache.record_coverage(
                        self.provider_mode,
                        instrument_id,
                        start,
                        end,
                        row_count=int(snapshot_counts.get(instrument_id, 0)),
                    )
            self.last_prefetch_stats.update(
                {
                    "snapshot_requested": len(snapshot_targets),
                    "snapshot_rows": len(snapshot_frame),
                    "snapshot_repaired": len(repaired_ids),
                    "snapshot_unrecovered": len(snapshot_targets) - len(repaired_ids),
                    "snapshot_errors": len(snapshot_errors),
                }
            )

        history_missing = [
            instrument_id
            for instrument_id in missing
            if not self.cache.has_usable_coverage(
                self.provider_mode,
                instrument_id,
                start,
                end,
                minimum_session_coverage=(0.95 if instrument_id.startswith("CN:") else None),
                maximum_trailing_session_gap=(0 if instrument_id.startswith("CN:") else None),
            )
        ]
        latest_after_snapshot = self.cache.latest_trade_dates(
            self.provider_mode,
            history_missing,
            not_after=end,
        )
        refresh_groups: dict[date, list[str]] = {}
        for instrument_id in history_missing:
            latest = latest_after_snapshot.get(instrument_id)
            # A current tail with unusable coverage means the gap is inside the
            # cached history, so repair the requested range instead of skipping it.
            refresh_start = (
                start if latest is None or latest >= end else max(start, latest + timedelta(days=1))
            )
            if refresh_start <= end:
                refresh_groups.setdefault(refresh_start, []).append(instrument_id)
        self.last_prefetch_stats["request_groups"] = len(refresh_groups)
        for refresh_start, group in sorted(refresh_groups.items()):
            fetched_group = (
                getter(group, refresh_start, end)
                if callable(getter)
                else self.provider.get_daily_bars(group, refresh_start, end)
            )
            errors.extend(getattr(self.provider, "last_errors", []))
            fallback_instruments.extend(getattr(self.provider, "last_fallback_instruments", []))
            if not fetched_group.empty:
                frames.append(fetched_group)
        fetched = (
            pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=BAR_COLUMNS)
        )
        self.cache.save_daily_bars(self.provider_mode, fetched)
        if repair_recent_tail and self.enable_recent_tail_snapshot_repair:
            latest_after_history = self.cache.latest_trade_dates(
                self.provider_mode,
                snapshot_targets,
                not_after=end,
            )
            raw_tail_missing = {
                instrument_id
                for instrument_id in snapshot_targets
                if latest_after_history.get(instrument_id) != end
            }
            invalid_adjusted_tail = set(
                self.cache.instruments_missing_valid_adjusted_close(
                    self.provider_mode,
                    sorted(set(snapshot_targets) | set(adjusted_tail_missing)),
                    end,
                )
            )
            retry_targets = sorted(raw_tail_missing | invalid_adjusted_tail)
            retry_frame, retry_errors, retry_fallbacks = self._retry_settled_tail_from_history(
                retry_targets,
                end=end,
                raw_tail_targets=raw_tail_missing,
            )
            errors.extend(retry_errors)
            fallback_instruments.extend(retry_fallbacks)
            retry_repaired = _instrument_ids_on_date(retry_frame, end)
            self.last_prefetch_stats.update(
                {
                    "settled_tail_retry_requested": len(retry_targets),
                    "settled_tail_retry_repaired": len(retry_repaired),
                    "settled_tail_retry_unrecovered": len(retry_targets) - len(retry_repaired),
                    "settled_tail_retry_errors": len(retry_errors),
                }
            )
        adjusted_tail_remaining = self.cache.instruments_missing_valid_adjusted_close(
            self.provider_mode,
            adjusted_tail_missing,
            end,
        )
        self.last_prefetch_stats.update(
            {
                "adjusted_tail_repaired": len(adjusted_tail_missing)
                - len(adjusted_tail_remaining),
                "adjusted_tail_unrecovered": len(adjusted_tail_remaining),
            }
        )
        self._pending_prefetch_errors = list(dict.fromkeys(errors))
        self._pending_prefetch_fallback_instruments = list(dict.fromkeys(fallback_instruments))
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
                minimum_session_coverage=(0.95 if instrument_id.startswith("CN:") else None),
                maximum_trailing_session_gap=(0 if instrument_id.startswith("CN:") else None),
            )
            for instrument_id in missing
        }
        for instrument_id, usable in usable_after_refresh.items():
            coverage_key = (instrument_id, start, end)
            if usable:
                self._prefetched_empty_ranges.discard(coverage_key)
            elif cached_counts.get(instrument_id, 0) == 0:
                self._prefetched_empty_ranges.add(coverage_key)
            else:
                # Partial histories and stale tails remain useful evidence. Only
                # suppress a per-symbol retry when the batch produced zero rows.
                self._prefetched_empty_ranges.discard(coverage_key)
        refreshed = sum(usable_after_refresh.values())
        self.last_prefetch_stats.update(
            {
                "fetched_rows": len(fetched),
                "refreshed": refreshed,
                "stale_after_refresh": len(missing) - refreshed,
            }
        )

    def _repair_recent_tail_from_snapshot(
        self,
        instrument_ids: list[str],
        *,
        end: date,
    ) -> tuple[pd.DataFrame, list[str], list[str]]:
        if not instrument_ids:
            return pd.DataFrame(columns=BAR_COLUMNS), [], []
        snapshot_getter = getattr(self.provider, "get_repair_snapshot", None)
        if not callable(snapshot_getter):
            snapshot_getter = getattr(self.provider, "get_snapshot", None)
        if not callable(snapshot_getter):
            return pd.DataFrame(columns=BAR_COLUMNS), [], []

        frames: list[pd.DataFrame] = []
        errors: list[str] = []
        fallback_instruments: list[str] = []
        for offset in range(0, len(instrument_ids), self.snapshot_repair_batch_size):
            batch = instrument_ids[offset : offset + self.snapshot_repair_batch_size]
            snapshot = snapshot_getter(batch)
            errors.extend(getattr(self.provider, "last_errors", []))
            fallback_instruments.extend(getattr(self.provider, "last_fallback_instruments", []))
            normalized = _normalized_snapshot_tail(snapshot, batch, expected=end)
            if not normalized.empty:
                frames.append(normalized)

        combined = (
            pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=BAR_COLUMNS)
        )
        if not combined.empty:
            self.cache.merge_missing_daily_bars(
                self.provider_mode,
                combined,
                allowed_keys={(instrument_id, end) for instrument_id in instrument_ids},
            )
        return (
            combined,
            list(dict.fromkeys(errors)),
            list(dict.fromkeys(fallback_instruments)),
        )

    def _retry_settled_tail_from_history(
        self,
        instrument_ids: list[str],
        *,
        end: date,
        raw_tail_targets: set[str] | None = None,
    ) -> tuple[pd.DataFrame, list[str], list[str]]:
        """Retry unresolved settled tails in fresh, bounded history sessions."""

        if not instrument_ids:
            return pd.DataFrame(columns=BAR_COLUMNS), [], []
        getter = getattr(self.provider, "get_historical_daily_bars", None)
        if not callable(getter):
            return pd.DataFrame(columns=BAR_COLUMNS), [], []

        raw_frames: list[pd.DataFrame] = []
        adjusted_frames: list[pd.DataFrame] = []
        raw_targets = raw_tail_targets or set()
        errors: list[str] = []
        fallback_instruments: list[str] = []
        for offset in range(0, len(instrument_ids), self.settled_tail_retry_batch_size):
            batch = instrument_ids[offset : offset + self.settled_tail_retry_batch_size]
            frame = getter(batch, end, end)
            errors.extend(getattr(self.provider, "last_errors", []))
            fallback_instruments.extend(getattr(self.provider, "last_fallback_instruments", []))
            normalized = _normalized_snapshot_tail(frame, batch, expected=end)
            if normalized.empty:
                continue
            raw_rows = normalized.loc[
                normalized["instrument_id"].isin(raw_targets)
            ].copy()
            if not raw_rows.empty:
                for column in (
                    "adjusted_open",
                    "adjusted_high",
                    "adjusted_low",
                    "adjusted_close",
                    "adjustment_factor",
                    "adjustment_type",
                ):
                    raw_rows[column] = None
                raw_frames.append(raw_rows)
            safe_adjusted = _safe_adjusted_repair_rows(normalized)
            if not safe_adjusted.empty:
                adjusted_frames.append(safe_adjusted)

        raw_combined = (
            pd.concat(raw_frames, ignore_index=True)
            if raw_frames
            else pd.DataFrame(columns=BAR_COLUMNS)
        )
        adjusted_combined = (
            pd.concat(adjusted_frames, ignore_index=True)
            if adjusted_frames
            else pd.DataFrame(columns=BAR_COLUMNS)
        )
        if not raw_combined.empty:
            self.cache.save_daily_bars(self.provider_mode, raw_combined)
        if not adjusted_combined.empty:
            self.cache.merge_missing_daily_bars(
                self.provider_mode,
                adjusted_combined,
                allowed_keys={(instrument_id, end) for instrument_id in instrument_ids},
            )
        combined = pd.concat(
            [frame for frame in (raw_combined, adjusted_combined) if not frame.empty],
            ignore_index=True,
        ) if not raw_combined.empty or not adjusted_combined.empty else pd.DataFrame(
            columns=BAR_COLUMNS
        )
        return (
            combined,
            list(dict.fromkeys(errors)),
            list(dict.fromkeys(fallback_instruments)),
        )

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        snapshot = self.provider.get_snapshot(instrument_ids)
        self.last_errors = list(getattr(self.provider, "last_errors", []))
        self.last_fallback_instruments = list(
            getattr(self.provider, "last_fallback_instruments", [])
        )
        return snapshot

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
        "snapshot_repair_enabled": "false",
        "snapshot_requested": 0,
        "snapshot_rows": 0,
        "snapshot_repaired": 0,
        "snapshot_unrecovered": 0,
        "snapshot_errors": 0,
        "settled_tail_retry_requested": 0,
        "settled_tail_retry_repaired": 0,
        "settled_tail_retry_unrecovered": 0,
        "settled_tail_retry_errors": 0,
        "adjusted_tail_requested": 0,
        "adjusted_tail_repaired": 0,
        "adjusted_tail_unrecovered": 0,
    }


def _normalized_snapshot_tail(
    frame: pd.DataFrame,
    requested: list[str],
    *,
    expected: date,
) -> pd.DataFrame:
    if frame.empty or not {"instrument_id", "trade_date"}.issubset(frame.columns):
        return pd.DataFrame(columns=BAR_COLUMNS)
    normalized = frame.copy()
    normalized["instrument_id"] = normalized["instrument_id"].astype(str)
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"], errors="coerce").dt.date
    normalized = normalized.loc[
        normalized["instrument_id"].isin(requested) & normalized["trade_date"].eq(expected)
    ].copy()
    if normalized.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    for column in BAR_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None
    return (
        normalized[BAR_COLUMNS]
        .drop_duplicates(subset=["instrument_id", "trade_date"], keep="last")
        .sort_values(["instrument_id", "trade_date"])
        .reset_index(drop=True)
    )


def _instrument_ids_on_date(frame: pd.DataFrame, expected: date) -> set[str]:
    if frame.empty:
        return set()
    trade_dates = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    return set(frame.loc[trade_dates.eq(expected), "instrument_id"].astype(str))


def _safe_adjusted_repair_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep only adjusted rows with explicit, non-realtime provenance."""

    if frame.empty:
        return frame
    adjusted_close = pd.to_numeric(frame["adjusted_close"], errors="coerce")
    adjustment_factor = pd.to_numeric(frame["adjustment_factor"], errors="coerce")
    provider = frame["provider"].fillna("").astype(str).str.lower()
    adjustment_type = frame["adjustment_type"].fillna("").astype(str).str.lower()
    safe = (
        adjusted_close.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
        & adjusted_close.gt(0)
        & adjustment_factor.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
        & adjustment_factor.gt(0)
        & adjustment_type.ne("")
        & adjustment_type.ne("snapshot_qfq_anchor")
        & provider.ne("fuyao_realtime")
        & provider.ne("fuyao_etf_unadjusted")
    )
    return frame.loc[safe].copy()
