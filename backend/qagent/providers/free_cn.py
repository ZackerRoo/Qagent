from datetime import date

import akshare as ak
import pandas as pd


class FreeCnMarketDataProvider:
    name = "free_cn"

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for instrument_id in instrument_ids:
            symbol = instrument_id.split(":", 1)[1]
            raw = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="",
            )
            if raw.empty:
                continue
            normalized = raw.rename(
                columns={
                    "日期": "trade_date",
                    "开盘": "open",
                    "最高": "high",
                    "最低": "low",
                    "收盘": "close",
                    "成交量": "volume",
                }
            ).copy()
            normalized["instrument_id"] = instrument_id
            normalized["provider"] = "akshare"
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
