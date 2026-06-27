from datetime import date
from contextlib import contextmanager
from threading import RLock
import math
import socket

import akshare as ak
import baostock as bs
import pandas as pd
import requests

BAR_COLUMNS = ["instrument_id", "trade_date", "open", "high", "low", "close", "volume", "provider"]
DEFAULT_REQUEST_TIMEOUT_SECONDS = 3
DEFAULT_FAILURE_CIRCUIT_BREAKER_THRESHOLD = 3
_NETWORK_TIMEOUT_LOCK = RLock()


class FreeCnMarketDataProvider:
    name = "free_cn"

    def __init__(
        self,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        failure_circuit_breaker_threshold: int = DEFAULT_FAILURE_CIRCUIT_BREAKER_THRESHOLD,
    ):
        self.last_errors: list[str] = []
        self.request_timeout_seconds = request_timeout_seconds
        self.failure_circuit_breaker_threshold = max(1, failure_circuit_breaker_threshold)
        self.consecutive_source_failures = 0

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        self.last_errors = []
        frames: list[pd.DataFrame] = []
        for instrument_id in instrument_ids:
            symbol = instrument_id.split(":", 1)[1]
            if self._source_circuit_open():
                self.last_errors.append(
                    f"{instrument_id}: skipped after "
                    f"{self.consecutive_source_failures} consecutive source failures"
                )
                continue
            source_errors: list[str] = []
            try:
                normalized = self._load_akshare(
                    symbol,
                    start,
                    end,
                    self.request_timeout_seconds,
                )
            except Exception as exc:
                source_errors.append(f"akshare: {exc}")
                try:
                    normalized = self._load_baostock(
                        symbol,
                        start,
                        end,
                        self.request_timeout_seconds,
                    )
                except Exception as fallback_exc:
                    source_errors.append(f"baostock: {fallback_exc}")
                    self.consecutive_source_failures += 1
                    self.last_errors.append(f"{instrument_id}: {'; '.join(source_errors)}")
                    continue
            self.consecutive_source_failures = 0
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

    def _source_circuit_open(self) -> bool:
        return self.consecutive_source_failures >= self.failure_circuit_breaker_threshold

    @staticmethod
    def _load_akshare(
        symbol: str,
        start: date,
        end: date,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> pd.DataFrame:
        with _bounded_network_calls(request_timeout_seconds):
            if _is_etf_symbol(symbol):
                raw = ak.fund_etf_hist_em(
                    symbol=symbol,
                    period="daily",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="",
                )
                provider_name = "akshare_etf"
            else:
                raw = ak.stock_zh_a_hist(
                    symbol=symbol,
                    period="daily",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="",
                )
                provider_name = "akshare"
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
        normalized["provider"] = provider_name
        return _coerce_bar_types(normalized)

    @staticmethod
    def _load_baostock(
        symbol: str,
        start: date,
        end: date,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> pd.DataFrame:
        with _bounded_network_calls(request_timeout_seconds):
            login = bs.login()
        try:
            if login.error_code != "0":
                raise RuntimeError(login.error_msg)
            with _bounded_network_calls(request_timeout_seconds):
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


@contextmanager
def _bounded_network_calls(timeout_seconds: int):
    with _NETWORK_TIMEOUT_LOCK:
        previous_socket_timeout = socket.getdefaulttimeout()
        original_request = requests.sessions.Session.request

        def request_with_timeout(self, method, url, **kwargs):
            kwargs.setdefault("timeout", (timeout_seconds, timeout_seconds))
            return original_request(self, method, url, **kwargs)

        socket.setdefaulttimeout(timeout_seconds)
        requests.sessions.Session.request = request_with_timeout
        try:
            yield
        finally:
            requests.sessions.Session.request = original_request
            socket.setdefaulttimeout(previous_socket_timeout)


def _is_etf_symbol(symbol: str) -> bool:
    return symbol.startswith(("15", "16", "51", "52", "56", "58"))


def _coerce_bar_types(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        normalized[column] = _finite_numeric(normalized[column])
    normalized["volume"] = normalized["volume"].fillna(0)
    return normalized.dropna(subset=["open", "high", "low", "close"])


def _finite_numeric(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    finite_mask = numeric.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
    return numeric.where(finite_mask)
