from datetime import date, datetime
from typing import Protocol

import pandas as pd


MINUTE_BAR_COLUMNS = [
    "instrument_id",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "provider",
]


class MarketDataProvider(Protocol):
    name: str

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        ...

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        ...

    def get_minute_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        ...
