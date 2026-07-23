from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from qagent.historical_evidence.models import HistoricalTradabilityPoint
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.strategy_data.providers import BaseStrategyDataProvider


_REPLAY_FRAME_COLUMNS = (
    "instrument_id",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "provider",
    "adjusted_open",
    "adjusted_high",
    "adjusted_low",
    "adjusted_close",
    "adjustment_factor",
    "adjustment_type",
    "trading_status",
    "is_suspended",
    "is_st",
)


class ReplayMarketDataProvider:
    name = "historical_replay"

    def __init__(self, repository: ReplayEvidenceRepository, revision: int):
        self.repository = repository
        self.revision = revision
        self.last_errors: list[str] = []
        self._bars_by_instrument: dict[str, pd.DataFrame] = {}
        self._coverage: dict[str, tuple[date, date]] = {}
        self._tradability_cache: dict[
            tuple[str, date], HistoricalTradabilityPoint | None
        ] = {}
        self._pruned_through: date | None = None
        self.query_count = 0
        self.tradability_query_count = 0
        self.full_window_queries = 0
        self.incremental_queries = 0
        self.rows_loaded = 0
        self.adjusted_close_stream_queries = 0

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        if start > end:
            raise ValueError("start must be on or before end")
        requested = sorted(set(instrument_ids))
        if not requested:
            return pd.DataFrame()
        self._prune_before(start)
        self._ensure_coverage(requested, start, end)
        frames = []
        for instrument_id in requested:
            frame = self._bars_by_instrument.get(instrument_id)
            if frame is None or frame.empty:
                continue
            frames.append(
                frame.loc[
                    (frame["trade_date"] >= start)
                    & (frame["trade_date"] <= end)
                ]
            )
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def prefetch_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> None:
        if start > end:
            raise ValueError("start must be on or before end")
        requested = sorted(set(instrument_ids))
        if not requested:
            return
        self._prune_before(start)
        self._ensure_coverage(requested, start, end)

    def iter_adjusted_closes(
        self,
        instrument_ids: list[str],
        start_exclusive: date,
        end_inclusive: date,
    ):
        requested = sorted(set(instrument_ids))
        if not requested or start_exclusive >= end_inclusive:
            return
        self.adjusted_close_stream_queries += 1
        rows = self.repository.iter_replay_adjusted_closes(
            requested,
            start_exclusive,
            end_inclusive,
            self.revision,
        )
        for instrument_id, trade_date, adjusted_close in rows:
            yield instrument_id, trade_date, float(adjusted_close)

    def _ensure_coverage(
        self,
        requested: list[str],
        start: date,
        end: date,
    ) -> None:
        query_groups: dict[tuple[date, date], list[str]] = {}
        for instrument_id in requested:
            coverage = self._coverage.get(instrument_id)
            if coverage is None:
                query_groups.setdefault((start, end), []).append(instrument_id)
                continue
            covered_start, covered_end = coverage
            if start < covered_start:
                query_groups.setdefault(
                    (start, covered_start - timedelta(days=1)),
                    [],
                ).append(instrument_id)
            if end > covered_end:
                query_groups.setdefault(
                    (covered_end + timedelta(days=1), end),
                    [],
                ).append(instrument_id)
        for (query_start, query_end), query_ids in query_groups.items():
            rows = self.repository.replay_bar_rows(
                query_ids,
                query_start,
                query_end,
                self.revision,
            )
            self.query_count += 1
            if query_start == start and query_end == end:
                self.full_window_queries += 1
            else:
                self.incremental_queries += 1
            self._append_rows(rows)
            for instrument_id in query_ids:
                previous = self._coverage.get(instrument_id)
                self._coverage[instrument_id] = (
                    min(previous[0], query_start) if previous else query_start,
                    max(previous[1], query_end) if previous else query_end,
                )

    def _append_rows(self, rows) -> None:
        rows = list(rows)
        self._load_tradability(rows)
        records = []
        for item in rows:
            adjusted = all(
                value is not None
                for value in (
                    item.adjusted_open,
                    item.adjusted_high,
                    item.adjusted_low,
                    item.adjusted_close,
                )
            )
            if not adjusted and item.adjustment_mode != "none":
                self.last_errors.append(
                    f"{item.instrument_id} {item.trade_date}: adjusted OHLC is missing"
                )
                continue
            tradability = self._tradability_cache.get(
                (item.instrument_id, item.trade_date)
            )
            trading_status = (
                str(tradability.trading_status)
                if tradability is not None
                else ("suspended" if item.volume <= 0 else "trading")
            )
            records.append(
                (
                    item.instrument_id,
                    item.trade_date,
                    float(item.raw_open),
                    float(item.raw_high),
                    float(item.raw_low),
                    float(item.raw_close),
                    float(item.volume),
                    (
                        float(item.turnover) if item.turnover is not None else None
                    ),
                    item.source_provider,
                    (
                        float(item.adjusted_open)
                        if item.adjusted_open is not None
                        else None
                    ),
                    (
                        float(item.adjusted_high)
                        if item.adjusted_high is not None
                        else None
                    ),
                    (
                        float(item.adjusted_low)
                        if item.adjusted_low is not None
                        else None
                    ),
                    (
                        float(item.adjusted_close)
                        if item.adjusted_close is not None
                        else None
                    ),
                    (
                        float(item.adjustment_factor)
                        if item.adjustment_factor is not None
                        else None
                    ),
                    item.adjustment_mode,
                    trading_status,
                    trading_status.strip().lower()
                    not in {"trading", "normal", "active"},
                    bool(tradability.is_st) if tradability is not None else False,
                )
            )
        self.rows_loaded += len(records)
        if not records:
            return
        incoming = pd.DataFrame.from_records(records, columns=_REPLAY_FRAME_COLUMNS)
        for instrument_id, frame in incoming.groupby("instrument_id", sort=False):
            existing = self._bars_by_instrument.get(str(instrument_id))
            combined = (
                pd.concat([existing, frame], ignore_index=True)
                if existing is not None
                else frame.copy()
            )
            self._bars_by_instrument[str(instrument_id)] = (
                combined.drop_duplicates(subset=["trade_date"], keep="last")
                .sort_values("trade_date")
                .reset_index(drop=True)
            )

    def _load_tradability(self, rows) -> None:
        missing_by_date: dict[date, set[str]] = {}
        for item in rows:
            key = (item.instrument_id, item.trade_date)
            if key not in self._tradability_cache:
                missing_by_date.setdefault(item.trade_date, set()).add(
                    item.instrument_id
                )
        dates = sorted(missing_by_date)
        for offset in range(0, len(dates), 8):
            batch_dates = dates[offset : offset + 8]
            batch_ids = sorted(
                {
                    instrument_id
                    for trade_date in batch_dates
                    for instrument_id in missing_by_date[trade_date]
                }
            )
            points_by_date = self.repository.tradability_on_dates(
                batch_ids,
                batch_dates,
                self.revision,
            )
            self.tradability_query_count += 1
            for trade_date in batch_dates:
                points = points_by_date.get(trade_date, {})
                for instrument_id in missing_by_date[trade_date]:
                    self._tradability_cache[
                        (instrument_id, trade_date)
                    ] = points.get(instrument_id)

    def _prune_before(self, start: date) -> None:
        if self._pruned_through is not None and start <= self._pruned_through:
            return
        for instrument_id, frame in list(self._bars_by_instrument.items()):
            retained = frame.loc[frame["trade_date"] >= start].reset_index(drop=True)
            if retained.empty:
                del self._bars_by_instrument[instrument_id]
            else:
                self._bars_by_instrument[instrument_id] = retained
        for instrument_id, (covered_start, covered_end) in list(
            self._coverage.items()
        ):
            if covered_end < start:
                del self._coverage[instrument_id]
            elif covered_start < start:
                self._coverage[instrument_id] = (start, covered_end)
        for key in list(self._tradability_cache):
            if key[1] < start:
                del self._tradability_cache[key]
        self._pruned_through = start

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        return pd.DataFrame()

    def get_minute_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        return pd.DataFrame()


class ReplayStrategyDataProvider(BaseStrategyDataProvider):
    name = "historical_replay_point_in_time"

    def __init__(self, repository: ReplayEvidenceRepository, revision: int):
        super().__init__()
        self.repository = repository
        self.revision = revision
        self._prefetched_ids: set[str] = set()
        self._prefetched_as_of: date | None = None
        self._fundamental_cache = {}
        self.query_count = 0
        self.prefetch_count = 0

    def prefetch_fundamentals(self, instrument_ids, end, snapshots=None) -> None:
        requested = sorted(set(instrument_ids))
        self._fundamental_cache = (
            dict(snapshots)
            if snapshots is not None
            else self.repository.fundamentals_as_of(
                requested,
                end,
                self.revision,
            )
        )
        self._prefetched_ids = set(requested)
        self._prefetched_as_of = end
        self.prefetch_count += 1
        if snapshots is None:
            self.query_count += 1

    def get_fundamentals(self, instrument_ids, start, end):
        requested = set(instrument_ids)
        if self._prefetched_as_of == end and requested.issubset(self._prefetched_ids):
            snapshots = {
                instrument_id: self._fundamental_cache[instrument_id]
                for instrument_id in requested
                if instrument_id in self._fundamental_cache
            }
        else:
            snapshots = self.repository.fundamentals_as_of(
                instrument_ids,
                end,
                self.revision,
            )
            self.query_count += 1
        return [
            item
            for item in snapshots.values()
            if start <= item.as_of_date <= end
        ]
