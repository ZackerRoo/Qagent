from datetime import date, datetime, timedelta
from contextlib import contextmanager
import math
import socket
from time import monotonic

import akshare as ak
import baostock as bs
import pandas as pd
import requests
import yfinance as yf

from qagent.providers.base import MINUTE_BAR_COLUMNS
from qagent.providers.baostock_session import (
    BAOSTOCK_SESSION_LOCK,
    baostock_call_deadline,
    serialized_baostock_session,
)

BAR_COLUMNS = [
    "instrument_id",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "provider",
    "adjusted_open",
    "adjusted_high",
    "adjusted_low",
    "adjusted_close",
    "adjustment_factor",
    "adjustment_type",
]
DEFAULT_REQUEST_TIMEOUT_SECONDS = 3
DEFAULT_FAILURE_CIRCUIT_BREAKER_THRESHOLD = 3
DEFAULT_FAILURE_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 1.0
_NETWORK_TIMEOUT_LOCK = BAOSTOCK_SESSION_LOCK


class FreeCnMarketDataProvider:
    name = "free_cn"

    def __init__(
        self,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        failure_circuit_breaker_threshold: int = DEFAULT_FAILURE_CIRCUIT_BREAKER_THRESHOLD,
        failure_circuit_breaker_cooldown_seconds: float = (
            DEFAULT_FAILURE_CIRCUIT_BREAKER_COOLDOWN_SECONDS
        ),
    ):
        self.last_errors: list[str] = []
        self.request_timeout_seconds = request_timeout_seconds
        self.failure_circuit_breaker_threshold = max(1, failure_circuit_breaker_threshold)
        self.failure_circuit_breaker_cooldown_seconds = max(
            0.0,
            failure_circuit_breaker_cooldown_seconds,
        )
        self.consecutive_source_failures = 0
        self.source_circuit_open_until = 0.0

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
                    f"{self.consecutive_source_failures} consecutive source failures; "
                    f"retry in {self.source_circuit_retry_after_seconds():.2f}s"
                )
                continue
            source_errors: list[str] = []
            try:
                if _is_index_symbol(symbol):
                    normalized = self._load_akshare_index(
                        _index_code(symbol),
                        start,
                        end,
                        self.request_timeout_seconds,
                    )
                else:
                    normalized = self._load_akshare(
                        symbol,
                        start,
                        end,
                        self.request_timeout_seconds,
                    )
            except Exception as exc:
                source_errors.append(f"akshare: {exc}")
                if _is_index_symbol(symbol):
                    self._record_source_failure()
                    self.last_errors.append(f"{instrument_id}: {'; '.join(source_errors)}")
                    continue
                if _is_etf_symbol(symbol):
                    try:
                        normalized = self._load_yfinance_etf(
                            symbol,
                            start,
                            end,
                            self.request_timeout_seconds,
                        )
                    except Exception as yfinance_exc:
                        source_errors.append(f"yfinance: {yfinance_exc}")
                    else:
                        self._record_source_success()
                        normalized["instrument_id"] = instrument_id
                        normalized["trade_date"] = pd.to_datetime(
                            normalized["trade_date"]
                        ).dt.date
                        frames.append(normalized[BAR_COLUMNS])
                        continue
                try:
                    normalized = self._load_baostock(
                        symbol,
                        start,
                        end,
                        self.request_timeout_seconds,
                    )
                except Exception as fallback_exc:
                    source_errors.append(f"baostock: {fallback_exc}")
                    self._record_source_failure()
                    self.last_errors.append(f"{instrument_id}: {'; '.join(source_errors)}")
                    continue
            self._record_source_success()
            if normalized.empty:
                continue
            normalized["instrument_id"] = instrument_id
            normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
            frames.append(normalized[BAR_COLUMNS])
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Load an adjusted history batch through one BaoStock session."""
        self.last_errors = []
        symbols = [
            (instrument_id, instrument_id.split(":", 1)[1])
            for instrument_id in dict.fromkeys(instrument_ids)
            if instrument_id.startswith("CN:")
            and not _is_index_symbol(instrument_id.split(":", 1)[1])
        ]
        if not symbols:
            return pd.DataFrame(columns=BAR_COLUMNS)
        frames: list[pd.DataFrame] = []
        with serialized_baostock_session():
            with (
                baostock_call_deadline(self.request_timeout_seconds),
                _bounded_network_calls(self.request_timeout_seconds),
            ):
                login = bs.login()
            try:
                if login.error_code != "0":
                    message = login.error_msg or "login failed"
                    self.last_errors.extend(
                        f"{instrument_id}: baostock batch login: {message}"
                        for instrument_id, _ in symbols
                    )
                    self._record_source_failure()
                    return pd.DataFrame(columns=BAR_COLUMNS)
                for index, (instrument_id, symbol) in enumerate(symbols):
                    try:
                        normalized = self._load_baostock_logged_in(
                            symbol,
                            start,
                            end,
                            self.request_timeout_seconds,
                        )
                    except Exception as exc:
                        self.last_errors.append(
                            f"{instrument_id}: baostock historical batch: {exc}"
                        )
                        self._record_source_failure()
                        if _baostock_session_is_unusable(exc):
                            self.last_errors.extend(
                                f"{pending_id}: baostock historical batch deferred "
                                "after session failure"
                                for pending_id, _ in symbols[index + 1 :]
                            )
                            break
                        continue
                    self._record_source_success()
                    if normalized.empty:
                        continue
                    normalized["instrument_id"] = instrument_id
                    normalized["trade_date"] = pd.to_datetime(
                        normalized["trade_date"]
                    ).dt.date
                    frames.append(normalized[BAR_COLUMNS])
            finally:
                with (
                    baostock_call_deadline(self.request_timeout_seconds),
                    _bounded_network_calls(self.request_timeout_seconds),
                ):
                    bs.logout()
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        bars = self.get_daily_bars(instrument_ids, date(1900, 1, 1), date.today())
        if bars.empty:
            return bars
        return bars.groupby("instrument_id", as_index=False).tail(1).reset_index(drop=True)

    def get_minute_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        self.last_errors = []
        frames: list[pd.DataFrame] = []
        start_value = _local_naive_datetime(start)
        end_value = _local_naive_datetime(end)
        for instrument_id in instrument_ids:
            symbol = instrument_id.split(":", 1)[1]
            if self._source_circuit_open():
                self.last_errors.append(
                    f"{instrument_id}: skipped minute data after "
                    f"{self.consecutive_source_failures} consecutive source failures; "
                    f"retry in {self.source_circuit_retry_after_seconds():.2f}s"
                )
                continue
            try:
                normalized = self._load_akshare_sina_minute(
                    symbol,
                    start_value,
                    end_value,
                    self.request_timeout_seconds,
                )
            except Exception as exc:
                self._record_source_failure()
                self.last_errors.append(f"{instrument_id}: akshare_sina_minute: {exc}")
                continue
            self._record_source_success()
            if normalized.empty:
                continue
            normalized["instrument_id"] = instrument_id
            frames.append(normalized[MINUTE_BAR_COLUMNS])
        if not frames:
            return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def _source_circuit_open(self) -> bool:
        if self.consecutive_source_failures < self.failure_circuit_breaker_threshold:
            return False
        if self.source_circuit_retry_after_seconds() <= 0:
            self._record_source_success()
            return False
        return True

    def source_circuit_retry_after_seconds(self, instrument_id: str | None = None) -> float:
        del instrument_id
        if self.consecutive_source_failures < self.failure_circuit_breaker_threshold:
            return 0.0
        return max(0.0, self.source_circuit_open_until - monotonic())

    def _record_source_failure(self) -> None:
        self.consecutive_source_failures += 1
        if self.consecutive_source_failures >= self.failure_circuit_breaker_threshold:
            self.source_circuit_open_until = (
                monotonic() + self.failure_circuit_breaker_cooldown_seconds
            )

    def _record_source_success(self) -> None:
        self.consecutive_source_failures = 0
        self.source_circuit_open_until = 0.0

    @staticmethod
    def _load_akshare(
        symbol: str,
        start: date,
        end: date,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> pd.DataFrame:
        with _bounded_network_calls(request_timeout_seconds):
            if _is_etf_symbol(symbol):
                loader = ak.fund_etf_hist_em
                provider_name = "akshare_etf_paired"
            else:
                loader = ak.stock_zh_a_hist
                provider_name = "akshare_stock_paired"
            raw = loader(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="",
            )
            adjusted = loader(
                    symbol=symbol,
                    period="daily",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                )
        if raw.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return _merge_raw_adjusted_frames(raw, adjusted, provider_name)

    @staticmethod
    def _load_akshare_index(
        symbol: str,
        start: date,
        end: date,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> pd.DataFrame:
        with _bounded_network_calls(request_timeout_seconds):
            raw = ak.index_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
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
                "成交额": "turnover",
            }
        ).copy()
        normalized["provider"] = "akshare_index"
        for column in ("open", "high", "low", "close"):
            normalized[f"adjusted_{column}"] = normalized[column]
        normalized["adjustment_factor"] = 1.0
        normalized["adjustment_type"] = "none"
        return _coerce_bar_types(normalized)

    @staticmethod
    def _load_baostock(
        symbol: str,
        start: date,
        end: date,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> pd.DataFrame:
        with serialized_baostock_session():
            with (
                baostock_call_deadline(request_timeout_seconds),
                _bounded_network_calls(request_timeout_seconds),
            ):
                login = bs.login()
            try:
                if login.error_code != "0":
                    raise RuntimeError(login.error_msg)
                return FreeCnMarketDataProvider._load_baostock_logged_in(
                    symbol,
                    start,
                    end,
                    request_timeout_seconds,
                )
            finally:
                with (
                    baostock_call_deadline(request_timeout_seconds),
                    _bounded_network_calls(request_timeout_seconds),
                ):
                    bs.logout()

    @staticmethod
    def _load_baostock_logged_in(
        symbol: str,
        start: date,
        end: date,
        request_timeout_seconds: int,
    ) -> pd.DataFrame:
        history_deadline_seconds = max(15, request_timeout_seconds * 4)
        frames = []
        for adjustflag in ("3", "2"):
            with (
                baostock_call_deadline(history_deadline_seconds),
                _bounded_network_calls(request_timeout_seconds),
            ):
                result = bs.query_history_k_data_plus(
                    _to_baostock_symbol(symbol),
                    "date,open,high,low,close,volume,amount",
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                    frequency="d",
                    adjustflag=adjustflag,
                )
                if result.error_code != "0":
                    raise RuntimeError(result.error_msg)
                rows: list[list[str]] = []
                while result.next():
                    rows.append(result.get_row_data())
            frames.append(pd.DataFrame(rows, columns=result.fields))
        raw, adjusted = frames
        if raw.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return _merge_raw_adjusted_frames(
            raw.rename(columns={"date": "trade_date", "amount": "turnover"}),
            adjusted.rename(columns={"date": "trade_date"}),
            "baostock_paired",
        )

    @staticmethod
    def _load_yfinance_etf(
        symbol: str,
        start: date,
        end: date,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> pd.DataFrame:
        ticker = f"{symbol}.SS" if symbol.startswith(("5", "6", "9")) else f"{symbol}.SZ"
        raw = yf.download(
            ticker,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            progress=False,
            auto_adjust=False,
            timeout=request_timeout_seconds,
        )
        if raw.empty:
            raise RuntimeError("no adjusted ETF bars returned")
        normalized = raw.copy()
        if isinstance(normalized.columns, pd.MultiIndex):
            normalized.columns = normalized.columns.get_level_values(0)
        normalized = normalized.reset_index()
        date_column = "Date" if "Date" in normalized.columns else normalized.columns[0]
        normalized = normalized.rename(
            columns={
                date_column: "trade_date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Adj Close": "adjusted_close",
                "Volume": "volume",
            }
        )
        normalized["provider"] = "yfinance_cn_etf_paired"
        close = _finite_numeric(normalized["close"])
        adjusted_close = _finite_numeric(normalized["adjusted_close"])
        factor = adjusted_close.div(close.where(close.ne(0)))
        normalized["adjustment_factor"] = factor
        for column in ("open", "high", "low"):
            normalized[f"adjusted_{column}"] = _finite_numeric(
                normalized[column]
            ).mul(factor)
        normalized["adjustment_type"] = "qfq"
        return _coerce_bar_types(normalized)

    @staticmethod
    def _load_akshare_sina_minute(
        symbol: str,
        start: datetime,
        end: datetime,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> pd.DataFrame:
        with _bounded_network_calls(request_timeout_seconds):
            raw = ak.stock_zh_a_minute(
                symbol=_to_sina_symbol(symbol),
                period="1",
                adjust="",
            )
        if raw.empty:
            return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
        normalized = raw.rename(
            columns={
                "day": "timestamp",
            }
        ).copy()
        normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], errors="coerce")
        normalized = normalized[
            (normalized["timestamp"] >= start) & (normalized["timestamp"] <= end)
        ]
        if normalized.empty:
            return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
        normalized["provider"] = "akshare_sina_minute"
        return _coerce_minute_bar_types(normalized)


def _to_baostock_symbol(symbol: str) -> str:
    prefix = (
        "bj"
        if symbol.startswith(("4", "8", "92"))
        else "sh"
        if symbol.startswith(("5", "6", "9"))
        else "sz"
    )
    return f"{prefix}.{symbol}"


def _baostock_session_is_unusable(exc: Exception) -> bool:
    detail = str(exc).lower()
    return isinstance(exc, (ConnectionError, TimeoutError, UnicodeError)) or any(
        token in detail
        for token in (
            "codec",
            "decode",
            "network",
            "socket",
            "timed out",
            "timeout",
            "接收数据异常",
            "网络",
        )
    )


def _to_sina_symbol(symbol: str) -> str:
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{symbol}"


def _local_naive_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


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


def _is_index_symbol(symbol: str) -> bool:
    return symbol.upper().endswith(".IDX")


def _index_code(symbol: str) -> str:
    return symbol.split(".", 1)[0]


def _coerce_bar_types(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        normalized[column] = _finite_numeric(normalized[column])
    for column in [
        "turnover",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "adjustment_factor",
    ]:
        if column in normalized.columns:
            normalized[column] = _finite_numeric(normalized[column])
    normalized["volume"] = normalized["volume"].fillna(0)
    for column in BAR_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None
    return normalized.dropna(subset=["open", "high", "low", "close"])


def _merge_raw_adjusted_frames(
    raw: pd.DataFrame,
    adjusted: pd.DataFrame,
    provider_name: str,
) -> pd.DataFrame:
    rename = {
        "日期": "trade_date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "turnover",
    }
    raw_frame = raw.rename(columns=rename).copy()
    adjusted_frame = adjusted.rename(columns=rename).copy()
    raw_frame["trade_date"] = pd.to_datetime(
        raw_frame["trade_date"], errors="coerce"
    ).dt.date
    if "trade_date" not in adjusted_frame.columns:
        adjusted_frame["trade_date"] = pd.Series(dtype=object)
    adjusted_frame["trade_date"] = pd.to_datetime(
        adjusted_frame["trade_date"], errors="coerce"
    ).dt.date
    adjusted_source_columns = ["trade_date", "open", "high", "low", "close"]
    if all(column in adjusted_frame.columns for column in adjusted_source_columns):
        adjusted_columns = adjusted_frame[adjusted_source_columns].rename(
            columns={
                "open": "adjusted_open",
                "high": "adjusted_high",
                "low": "adjusted_low",
                "close": "adjusted_close",
            }
        )
    else:
        adjusted_columns = pd.DataFrame(
            columns=[
                "trade_date",
                "adjusted_open",
                "adjusted_high",
                "adjusted_low",
                "adjusted_close",
            ]
        )
    merged = raw_frame.merge(
        adjusted_columns,
        on="trade_date",
        how="left",
        validate="one_to_one",
    )
    raw_close = _finite_numeric(merged["close"])
    adjusted_close = _finite_numeric(merged["adjusted_close"])
    merged["adjustment_factor"] = adjusted_close.div(
        raw_close.where(raw_close.ne(0))
    )
    merged["adjustment_type"] = merged["adjusted_close"].map(
        lambda value: "qfq" if pd.notna(value) else None
    )
    merged["provider"] = provider_name
    return _coerce_bar_types(merged)


def _coerce_minute_bar_types(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in ["open", "high", "low", "close", "volume"]:
        normalized[column] = _finite_numeric(normalized[column])
    normalized["volume"] = normalized["volume"].fillna(0)
    return normalized.dropna(subset=["timestamp", "open", "high", "low", "close"])


def _finite_numeric(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    finite_mask = numeric.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
    return numeric.where(finite_mask)
