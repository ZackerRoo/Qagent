from datetime import date
from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    name: str

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        ...

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        ...
