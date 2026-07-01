from datetime import date
from pathlib import Path

import pandas as pd


class FixtureMarketDataProvider:
    name = "fixture"

    def __init__(self, fixture_dir: Path | None = None):
        self.fixture_dir = fixture_dir or Path(__file__).parent / "fixture_data"

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        frames = []
        for path in [self.fixture_dir / "us_daily_bars.csv", self.fixture_dir / "cn_daily_bars.csv"]:
            if path.exists():
                frames.append(pd.read_csv(path, parse_dates=["trade_date"]))
        if not frames:
            return pd.DataFrame()
        frame = pd.concat(frames, ignore_index=True)
        frame["trade_date"] = frame["trade_date"].dt.date
        mask = (
            frame["instrument_id"].isin(instrument_ids)
            & (frame["trade_date"] >= start)
            & (frame["trade_date"] <= end)
        )
        result = frame.loc[mask].sort_values(["instrument_id", "trade_date"]).reset_index(drop=True)
        result["provider"] = "fixture"
        return result

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        frame = self.get_daily_bars(instrument_ids, date(1900, 1, 1), date(2100, 1, 1))
        if frame.empty:
            return frame
        return frame.groupby("instrument_id", as_index=False).tail(1).reset_index(drop=True)
