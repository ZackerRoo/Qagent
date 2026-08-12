from datetime import date, datetime

import pandas as pd

from qagent.providers.base import MINUTE_BAR_COLUMNS, MarketDataProvider
from qagent.providers.free_cn import BAR_COLUMNS


class SnapshotPreferredMarketDataProvider:
    """Keep the established history chain while preferring a live quote source."""

    def __init__(
        self,
        market_data_provider: MarketDataProvider,
        snapshot_provider: MarketDataProvider,
        *,
        name: str | None = None,
        max_preferred_instruments: int | None = None,
    ) -> None:
        self.market_data_provider = market_data_provider
        self.snapshot_provider = snapshot_provider
        self.name = name or market_data_provider.name
        self.max_preferred_instruments = max_preferred_instruments
        self.last_errors: list[str] = []
        self.last_fallback_instruments: list[str] = []

    def get_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        frame = self.market_data_provider.get_daily_bars(instrument_ids, start, end)
        self._capture_market_data_state()
        return frame

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        getter = getattr(self.market_data_provider, "get_historical_daily_bars", None)
        frame = (
            getter(instrument_ids, start, end)
            if callable(getter)
            else self.market_data_provider.get_daily_bars(instrument_ids, start, end)
        )
        self._capture_market_data_state()
        return frame

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        requested = list(dict.fromkeys(instrument_ids))
        if (
            self.max_preferred_instruments is not None
            and len(requested) > self.max_preferred_instruments
        ):
            frame = self.market_data_provider.get_snapshot(requested)
            self._capture_market_data_state()
            return frame
        primary = self.snapshot_provider.get_snapshot(requested)
        primary_errors = list(getattr(self.snapshot_provider, "last_errors", []))
        received = _instrument_ids(primary)
        missing = [instrument_id for instrument_id in requested if instrument_id not in received]
        if not missing:
            self.last_errors = primary_errors
            self.last_fallback_instruments = []
            return _normalized_snapshot(primary)

        fallback = self.market_data_provider.get_snapshot(missing)
        fallback_errors = list(getattr(self.market_data_provider, "last_errors", []))
        recovered = _instrument_ids(fallback)
        self.last_fallback_instruments = [item for item in missing if item in recovered]
        self.last_errors = _unrecovered_errors(primary_errors, recovered) + fallback_errors
        return _normalized_snapshot(primary, fallback)

    def get_repair_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        """Load the preferred quote source without repeating the history chain."""

        requested = list(dict.fromkeys(instrument_ids))
        if (
            self.max_preferred_instruments is not None
            and len(requested) > self.max_preferred_instruments
        ):
            raise ValueError(
                "repair snapshot batch exceeds preferred source limit: "
                f"{len(requested)} > {self.max_preferred_instruments}"
            )
        primary = self.snapshot_provider.get_snapshot(requested)
        self.last_errors = list(getattr(self.snapshot_provider, "last_errors", []))
        self.last_fallback_instruments = []
        return _normalized_snapshot(primary)

    def get_minute_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        getter = getattr(self.market_data_provider, "get_minute_bars", None)
        if getter is None:
            return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
        frame = getter(instrument_ids, start, end)
        self._capture_market_data_state()
        return frame

    def source_circuit_retry_after_seconds(self, instrument_id: str) -> float:
        getter = getattr(
            self.market_data_provider,
            "source_circuit_retry_after_seconds",
            None,
        )
        if getter is None:
            return 0.0
        return max(0.0, float(getter(instrument_id)))

    def _capture_market_data_state(self) -> None:
        self.last_errors = list(getattr(self.market_data_provider, "last_errors", []))
        self.last_fallback_instruments = list(
            getattr(self.market_data_provider, "last_fallback_instruments", [])
        )


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


def _normalized_snapshot(*frames: pd.DataFrame) -> pd.DataFrame:
    available = [frame for frame in frames if not frame.empty]
    if not available:
        return pd.DataFrame(columns=BAR_COLUMNS)
    combined = pd.concat(available, ignore_index=True, sort=False)
    for column in BAR_COLUMNS:
        if column not in combined.columns:
            combined[column] = None
    return (
        combined[BAR_COLUMNS]
        .drop_duplicates(subset=["instrument_id"], keep="first")
        .reset_index(drop=True)
    )
