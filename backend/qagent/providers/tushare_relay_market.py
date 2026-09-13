"""Bounded opt-in end-of-day fallback; never a snapshot or minute source."""

from datetime import date, datetime, timedelta
import math
import re
from time import monotonic
from zoneinfo import ZoneInfo

import pandas as pd

from qagent.providers.base import MINUTE_BAR_COLUMNS
from qagent.providers.free_cn import BAR_COLUMNS
from qagent.providers.tushare_relay import RelayError, TushareRelayClient


class TushareRelayMarketDataProvider:
    name = "tushare_relay_promax_daily_raw"

    def __init__(self, client: TushareRelayClient):
        self.client = client
        self.last_errors: list[str] = []
        self._retry_after = 0.0

    def get_daily_bars(self, instrument_ids: list[str], start: date, end: date) -> pd.DataFrame:
        self.last_errors = []
        if monotonic() < self._retry_after:
            self.last_errors = ["tushare_relay:runtime_circuit_open"]
            return pd.DataFrame(columns=BAR_COLUMNS)
        records = []
        requested = list(dict.fromkeys(instrument_ids))
        started = monotonic()
        for instrument_id in requested[:2]:
            if monotonic() - started >= 30:
                self.last_errors.append("tushare_relay:runtime_budget_exhausted")
                break
            try:
                records.extend(self._load(instrument_id, start, end))
            except RelayError as exc:
                self.last_errors.append(f"{instrument_id}: {exc}")
                if exc.kind not in {"unsupported_instrument", "runtime_window_limit"}:
                    self._retry_after = monotonic() + 300
                    break
            except Exception:
                # Never expose credentials or remote exception text in diagnostics.
                self.last_errors.append(f"{instrument_id}: tushare_relay:runtime_error")
                self._retry_after = monotonic() + 300
                break
        if len(requested) > 2:
            self.last_errors.append("tushare_relay:runtime_instrument_limit")
        return pd.DataFrame(records, columns=BAR_COLUMNS)

    get_historical_daily_bars = get_daily_bars

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        return pd.DataFrame(columns=BAR_COLUMNS)

    def get_minute_bars(self, instrument_ids: list[str], start: datetime,
                        end: datetime) -> pd.DataFrame:
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)

    def _load(self, instrument_id: str, start: date, end: date) -> list[dict]:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        today = now.date()
        if start > end or end > today or (end - start).days > 366:
            raise RelayError("runtime_window_limit")
        # Today's daily bar is not completed before the Shanghai close.
        if end == today and now.hour < 15:
            end = today - timedelta(days=1)
        if end < start:
            return []
        if not re.fullmatch(r"CN:\d{6}", instrument_id):
            raise RelayError("unsupported_instrument")
        code = instrument_id[3:]
        if code.startswith("6"):
            exchange = "SH"
        elif code.startswith(("0", "3")):
            exchange = "SZ"
        elif code.startswith(("4", "8", "92")):
            exchange = "BJ"
        else:
            raise RelayError("unsupported_instrument")
        symbol = f"{code}.{exchange}"
        params = dict(ts_code=symbol, start_date=start.strftime("%Y%m%d"),
                      end_date=end.strftime("%Y%m%d"), limit=500)
        prices = self.client.query("daily", **params)
        if not prices.rows:
            return []
        records, seen = [], set()
        for row in prices.rows:
            day = _identity(row, symbol, start, end)
            if day in seen:
                raise RelayError("duplicate_daily_row")
            seen.add(day)
            values = {key: _number(row.get(key), positive=True)
                      for key in ("open", "high", "low", "close")}
            if not (values["low"] <= min(values["open"], values["close"])
                    <= max(values["open"], values["close"]) <= values["high"]):
                raise RelayError("price_schema")
            # Tushare daily documents vol in lots (100 shares), amount in CNY thousands:
            # https://tushare.pro/document/1?doc_id=27. Execution capacity needs shares.
            records.append(dict(instrument_id=instrument_id, trade_date=day, **values,
                                volume=_number(row.get("vol")) * 100,
                                turnover=_number(row.get("amount")) * 1000,
                                provider=self.name, adjustment_type="raw"))
        # Per-request qfq anchors are not stable across incremental cached windows.
        # Leave adjusted prices absent; explicit anchored research remains separate.
        for row in records:
            if not math.isfinite(row["volume"]) or not math.isfinite(row["turnover"]):
                raise RelayError("numeric_schema")
        return sorted(records, key=lambda row: row["trade_date"])


def _identity(row: dict, symbol: str, start: date, end: date) -> date:
    value = row.get("trade_date")
    if row.get("ts_code") != symbol or not isinstance(value, str) or not re.fullmatch(
        r"\d{8}", value
    ):
        raise RelayError("identity_mismatch")
    try:
        day = datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise RelayError("date_schema") from None
    if not start <= day <= end:
        raise RelayError("date_mismatch")
    return day


def _number(value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool) or value is None:
        raise RelayError("numeric_schema")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise RelayError("numeric_schema") from None
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise RelayError("numeric_schema")
    return number
