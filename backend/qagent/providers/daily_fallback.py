from datetime import date, datetime

import pandas as pd

from qagent.providers.base import MINUTE_BAR_COLUMNS, MarketDataProvider
from qagent.providers.free_cn import BAR_COLUMNS


class DailyFallbackMarketDataProvider:
    """Use a secondary provider only when the primary returns no daily rows."""

    def __init__(
        self,
        primary: MarketDataProvider,
        fallback: MarketDataProvider,
        *,
        name: str | None = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.name = name or primary.name
        self.last_errors: list[str] = []
        self.last_fallback_instruments: list[str] = []

    def get_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        return self._load("get_daily_bars", instrument_ids, start, end)

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        return self._load("get_historical_daily_bars", instrument_ids, start, end)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        # Keep real-time/snapshot behavior owned by the primary provider. The
        # TickFlow free service is historical end-of-day data only.
        snapshot = self.primary.get_snapshot(instrument_ids)
        self.last_errors = list(getattr(self.primary, "last_errors", []))
        self.last_fallback_instruments = []
        return snapshot

    def get_minute_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        getter = getattr(self.primary, "get_minute_bars", None)
        if getter is None:
            return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
        frame = getter(instrument_ids, start, end)
        self.last_errors = list(getattr(self.primary, "last_errors", []))
        self.last_fallback_instruments = []
        return frame

    def source_circuit_retry_after_seconds(self, instrument_id: str) -> float:
        getter = getattr(self.primary, "source_circuit_retry_after_seconds", None)
        if getter is None:
            return 0.0
        return max(0.0, float(getter(instrument_id)))

    def _load(
        self,
        method_name: str,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        self.last_errors = []
        self.last_fallback_instruments = []
        requested = list(dict.fromkeys(instrument_ids))
        primary_getter = getattr(self.primary, method_name, None)
        if primary_getter is None:
            primary_getter = self.primary.get_daily_bars
        primary_bars = primary_getter(requested, start, end)
        primary_errors = list(getattr(self.primary, "last_errors", []))
        primary_ids = _instrument_ids(primary_bars)
        missing = [instrument_id for instrument_id in requested if instrument_id not in primary_ids]
        if not missing:
            self.last_errors = primary_errors
            return _normalized_result(primary_bars)

        fallback_getter = getattr(self.fallback, method_name, None)
        if fallback_getter is None:
            fallback_getter = self.fallback.get_daily_bars
        fallback_bars = fallback_getter(missing, start, end)
        fallback_errors = list(getattr(self.fallback, "last_errors", []))
        recovered = _instrument_ids(fallback_bars)
        self.last_fallback_instruments = [
            instrument_id for instrument_id in missing if instrument_id in recovered
        ]
        self.last_errors = _unrecovered_errors(primary_errors, recovered) + fallback_errors
        return _normalized_result(primary_bars, fallback_bars)


def _instrument_ids(frame: pd.DataFrame) -> set[str]:
    if frame.empty or "instrument_id" not in frame.columns:
        return set()
    return {str(value) for value in frame["instrument_id"].dropna().unique().tolist()}


def _unrecovered_errors(errors: list[str], recovered: set[str]) -> list[str]:
    if not recovered:
        return errors
    return [
        error
        for error in errors
        if not any(error.startswith(f"{instrument_id}:") for instrument_id in recovered)
    ]


def _normalized_result(*frames: pd.DataFrame) -> pd.DataFrame:
    available = [frame for frame in frames if not frame.empty]
    if not available:
        return pd.DataFrame(columns=BAR_COLUMNS)
    combined = pd.concat(available, ignore_index=True)
    for column in BAR_COLUMNS:
        if column not in combined.columns:
            combined[column] = None
    return (
        combined[BAR_COLUMNS]
        .drop_duplicates(subset=["instrument_id", "trade_date"], keep="first")
        .sort_values(["instrument_id", "trade_date"])
        .reset_index(drop=True)
    )
