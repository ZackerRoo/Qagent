from datetime import date

import pandas as pd
import yfinance as yf


class FreeUsMarketDataProvider:
    name = "free_us"

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for instrument_id in instrument_ids:
            symbol = instrument_id.split(":", 1)[1]
            raw = yf.download(
                symbol,
                start=start.isoformat(),
                end=end.isoformat(),
                progress=False,
                auto_adjust=False,
            )
            if raw.empty:
                continue
            normalized = raw.reset_index().rename(
                columns={
                    "Date": "trade_date",
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            )
            normalized["instrument_id"] = instrument_id
            normalized["provider"] = "yfinance"
            normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
            frames.append(
                normalized[
                    ["instrument_id", "trade_date", "open", "high", "low", "close", "volume", "provider"]
                ]
            )
        if not frames:
            return pd.DataFrame(
                columns=["instrument_id", "trade_date", "open", "high", "low", "close", "volume", "provider"]
            )
        return pd.concat(frames, ignore_index=True)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        bars = self.get_daily_bars(instrument_ids, date(1900, 1, 1), date.today())
        if bars.empty:
            return bars
        return bars.groupby("instrument_id", as_index=False).tail(1).reset_index(drop=True)
