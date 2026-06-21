from datetime import date

import akshare as ak
import baostock as bs
import pandas as pd

BAR_COLUMNS = ["instrument_id", "trade_date", "open", "high", "low", "close", "volume", "provider"]


class FreeCnMarketDataProvider:
    name = "free_cn"

    def __init__(self):
        self.last_errors: list[str] = []

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        self.last_errors = []
        frames: list[pd.DataFrame] = []
        for instrument_id in instrument_ids:
            symbol = instrument_id.split(":", 1)[1]
            source_errors: list[str] = []
            try:
                normalized = self._load_akshare(symbol, start, end)
            except Exception as exc:
                source_errors.append(f"akshare: {exc}")
                try:
                    normalized = self._load_baostock(symbol, start, end)
                except Exception as fallback_exc:
                    source_errors.append(f"baostock: {fallback_exc}")
                    self.last_errors.append(f"{instrument_id}: {'; '.join(source_errors)}")
                    continue
            if normalized.empty:
                continue
            normalized["instrument_id"] = instrument_id
            normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
            frames.append(normalized[BAR_COLUMNS])
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        bars = self.get_daily_bars(instrument_ids, date(1900, 1, 1), date.today())
        if bars.empty:
            return bars
        return bars.groupby("instrument_id", as_index=False).tail(1).reset_index(drop=True)

    @staticmethod
    def _load_akshare(symbol: str, start: date, end: date) -> pd.DataFrame:
        raw = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="",
        )
        if raw.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
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
        normalized["provider"] = "akshare"
        return _coerce_bar_types(normalized)

    @staticmethod
    def _load_baostock(symbol: str, start: date, end: date) -> pd.DataFrame:
        login = bs.login()
        try:
            if login.error_code != "0":
                raise RuntimeError(login.error_msg)
            result = bs.query_history_k_data_plus(
                _to_baostock_symbol(symbol),
                "date,open,high,low,close,volume",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency="d",
                adjustflag="3",
            )
            if result.error_code != "0":
                raise RuntimeError(result.error_msg)
            rows: list[list[str]] = []
            while result.next():
                rows.append(result.get_row_data())
            raw = pd.DataFrame(rows, columns=result.fields)
        finally:
            bs.logout()
        if raw.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        normalized = raw.rename(columns={"date": "trade_date"}).copy()
        normalized["provider"] = "baostock"
        return _coerce_bar_types(normalized)


def _to_baostock_symbol(symbol: str) -> str:
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}.{symbol}"


def _coerce_bar_types(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    return normalized.dropna(subset=["open", "high", "low", "close"])
