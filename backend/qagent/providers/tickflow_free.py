from datetime import date, datetime, time, timedelta, timezone
import math

import pandas as pd
import requests

from qagent.providers.base import MINUTE_BAR_COLUMNS
from qagent.providers.free_cn import BAR_COLUMNS


DEFAULT_TICKFLOW_FREE_BASE_URL = "https://free-api.tickflow.org"
DEFAULT_TICKFLOW_TIMEOUT_SECONDS = 6
MAX_KLINE_COUNT = 10_000


class TickFlowFreeDailyProvider:
    """No-key TickFlow adapter for historical daily bars only."""

    name = "tickflow_free"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_TICKFLOW_FREE_BASE_URL,
        request_timeout_seconds: int = DEFAULT_TICKFLOW_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = max(1, request_timeout_seconds)
        self.session = session or requests.Session()
        self.last_errors: list[str] = []

    def get_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        self.last_errors = []
        frames: list[pd.DataFrame] = []
        for instrument_id in dict.fromkeys(instrument_ids):
            try:
                frame = self._load_instrument(instrument_id, start, end)
            except Exception as exc:
                self.last_errors.append(f"{instrument_id}: tickflow_free: {exc}")
                continue
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values(["instrument_id", "trade_date"])
            .reset_index(drop=True)
        )

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        return self.get_daily_bars(instrument_ids, start, end)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        # TickFlow Free is end-of-day history, not a real-time quote source.
        bars = self.get_daily_bars(
            instrument_ids,
            date.today() - timedelta(days=14),
            date.today(),
        )
        if bars.empty:
            return bars
        return bars.groupby("instrument_id", as_index=False).tail(1).reset_index(drop=True)

    def get_minute_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        del instrument_ids, start, end
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)

    def _load_instrument(
        self,
        instrument_id: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        if end < start:
            raise ValueError("end date must be on or after start date")
        symbol, is_index = _to_tickflow_symbol(instrument_id)
        raw = self._request_klines(symbol, start, end, adjust="none")
        if raw.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)

        if is_index:
            adjusted = raw[["trade_date", "open", "high", "low", "close"]].copy()
            adjustment_type = "none"
            provider_name = "tickflow_free_index"
        else:
            try:
                adjusted = self._request_klines(symbol, start, end, adjust="forward")
            except Exception as exc:
                self.last_errors.append(
                    f"{instrument_id}: tickflow_free adjusted history: {exc}"
                )
                adjusted = pd.DataFrame()
            adjustment_type = "forward"
            provider_name = "tickflow_free_paired"

        adjusted = adjusted.rename(
            columns={
                "open": "adjusted_open",
                "high": "adjusted_high",
                "low": "adjusted_low",
                "close": "adjusted_close",
            }
        )
        adjusted_columns = [
            "trade_date",
            "adjusted_open",
            "adjusted_high",
            "adjusted_low",
            "adjusted_close",
        ]
        if not all(column in adjusted.columns for column in adjusted_columns):
            adjusted = pd.DataFrame(columns=adjusted_columns)

        merged = raw.merge(
            adjusted[adjusted_columns],
            on="trade_date",
            how="left",
            validate="one_to_one",
        )
        merged["instrument_id"] = instrument_id
        merged["provider"] = provider_name
        raw_close = pd.to_numeric(merged["close"], errors="coerce")
        adjusted_close = pd.to_numeric(merged["adjusted_close"], errors="coerce")
        merged["adjustment_factor"] = adjusted_close.div(raw_close.where(raw_close.ne(0)))
        merged["adjustment_type"] = merged["adjusted_close"].map(
            lambda value: adjustment_type if pd.notna(value) else None
        )
        for column in BAR_COLUMNS:
            if column not in merged.columns:
                merged[column] = None
        return merged[BAR_COLUMNS]

    def _request_klines(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        adjust: str,
    ) -> pd.DataFrame:
        response = self.session.get(
            f"{self.base_url}/v1/klines",
            params={
                "symbol": symbol,
                "period": "1d",
                "count": MAX_KLINE_COUNT,
                "start_time": _date_to_epoch_ms(start),
                "end_time": _date_to_epoch_ms(end, end_of_day=True),
                "adjust": adjust,
            },
            timeout=(self.request_timeout_seconds, self.request_timeout_seconds),
            headers={"User-Agent": "Qagent/0.1 tickflow-free-fallback"},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("response is missing the data object")
        return _normalize_kline_payload(data, start, end)


def _to_tickflow_symbol(instrument_id: str) -> tuple[str, bool]:
    if not instrument_id.startswith("CN:"):
        raise ValueError("TickFlow free fallback only supports CN instruments")
    raw_symbol = instrument_id.split(":", 1)[1].upper()
    is_index = raw_symbol.endswith(".IDX")
    code = raw_symbol.removesuffix(".IDX")
    if not code.isdigit() or len(code) != 6:
        raise ValueError(f"unsupported CN symbol: {raw_symbol}")
    if is_index:
        exchange = "SZ" if code.startswith("399") else "SH"
    elif code.startswith(("4", "8", "92")):
        exchange = "BJ"
    elif code.startswith(("5", "6", "9")):
        exchange = "SH"
    else:
        exchange = "SZ"
    return f"{code}.{exchange}", is_index


def _normalize_kline_payload(data: dict, start: date, end: date) -> pd.DataFrame:
    required = ("timestamp", "open", "high", "low", "close", "volume")
    values: dict[str, list] = {}
    for field in required:
        value = data.get(field)
        if not isinstance(value, list):
            raise ValueError(f"data.{field} must be a list")
        values[field] = value
    lengths = {len(value) for value in values.values()}
    if len(lengths) != 1:
        raise ValueError("K-line columns have inconsistent lengths")
    if not values["timestamp"]:
        return pd.DataFrame(
            columns=["trade_date", "open", "high", "low", "close", "volume", "turnover"]
        )

    amount = data.get("amount")
    if amount is None:
        amount = [None] * len(values["timestamp"])
    if not isinstance(amount, list) or len(amount) != len(values["timestamp"]):
        raise ValueError("data.amount has an inconsistent length")

    frame = pd.DataFrame({**values, "turnover": amount})
    frame["trade_date"] = pd.to_datetime(
        frame.pop("timestamp"), unit="ms", utc=True, errors="coerce"
    ).dt.date
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame[column] = frame[column].where(
            frame[column].map(lambda value: pd.notna(value) and math.isfinite(float(value)))
        )
    frame = frame.loc[frame["trade_date"].between(start, end)].copy()
    frame = frame.dropna(subset=["trade_date", "open", "high", "low", "close"])
    frame = frame.loc[
        (frame["open"] > 0)
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
        & (frame["low"] > 0)
        & (frame["volume"].fillna(0) >= 0)
    ]
    return (
        frame.drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def _date_to_epoch_ms(value: date, *, end_of_day: bool = False) -> int:
    moment = datetime.combine(value, time.max if end_of_day else time.min, tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)
