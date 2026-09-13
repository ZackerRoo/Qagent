"""Explicit, read-only minute samples for research; never a trading price source.

One chosen API/page per call. A matching session is not evidence of realtime
freshness, complete coverage, independent upstreams, or measured cache latency.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
import math
import re
from zoneinfo import ZoneInfo

from qagent.providers.tushare_relay import RelayError, TushareRelayClient


_SH = ZoneInfo("Asia/Shanghai")
_APIS = {"rt_min", "stk_mins", "a_share_mins"}
_FREQUENCIES = {"1min", "5min", "15min", "30min", "60min"}


@dataclass(frozen=True)
class ResearchMinuteBar:
    ts_code: str
    trade_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    # Retain the upstream field identity; no unverified share/lot conversion.
    volume_field: str | None


@dataclass(frozen=True)
class ResearchMinuteSample:
    api: str
    ts_code: str
    frequency: str
    requested_date: date
    observed_at: datetime
    latest_bar_time: datetime | None
    rows: tuple[ResearchMinuteBar, ...]
    row_limit: int
    limit_reached: bool
    realtime_freshness: str
    request_semantics: str
    frequency_verified: bool = False
    coverage: str = "sample_full_session_unknown"
    volume_unit: str = "upstream_unspecified"
    source: str = "tushare_relay_promax"
    research_only: bool = True


def _number(value: object, *, positive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RelayError("minute_invalid_number")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise RelayError("minute_invalid_number") from None
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise RelayError("minute_invalid_number")
    return result


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        raise RelayError("minute_invalid_time")
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_SH)
    except ValueError:
        raise RelayError("minute_invalid_time") from None


def fetch_research_minutes(
    client: TushareRelayClient, *, ts_code: str, requested_date: str,
    api: str = "rt_min", frequency: str = "1min", limit: int = 300,
    start_time: str = "09:30:00", end_time: str = "15:00:00",
    observed_at: datetime | None = None,
) -> ResearchMinuteSample:
    """Fetch one session sample; YYYYMMDD date and HH:MM:SS window required.

    rt_min has no historical date parameter: requested_date validates returned
    bars, and does not make the upstream capable of historical queries. Explicit
    historical APIs receive the bounded session window. Failures propagate with
    safe RelayError kinds, including pending; no fallback, DB write or pagination.
    observed_at is an optional aware clock override for reproducible tests.
    """
    if (not isinstance(ts_code, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", ts_code)
            or not isinstance(api, str) or api not in _APIS
            or not isinstance(frequency, str) or frequency not in _FREQUENCIES
            or type(limit) is not int or not 1 <= limit <= 1000):
        raise RelayError("invalid_params")
    if not isinstance(requested_date, str) or not re.fullmatch(r"\d{8}", requested_date):
        raise RelayError("invalid_params")
    try:
        session = datetime.strptime(requested_date, "%Y%m%d").date()
    except ValueError:
        raise RelayError("invalid_params") from None
    stamp = session.isoformat()
    start = _timestamp(f"{stamp} {start_time}")
    end = _timestamp(f"{stamp} {end_time}")
    if start > end or start.time() < time(9, 30) or end.time() > time(15):
        raise RelayError("invalid_params")
    if observed_at is not None and (
        not isinstance(observed_at, datetime) or observed_at.utcoffset() is None
    ):
        raise RelayError("invalid_params")
    if session > (observed_at or datetime.now(_SH)).astimezone(_SH).date():
        raise RelayError("minute_future_session")
    params = {"ts_code": ts_code, "freq": frequency}
    if api != "rt_min":
        params.update(start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
                      end_date=end.strftime("%Y-%m-%d %H:%M:%S"))
    table = client.query(api, limit=limit, **params)
    observed = (observed_at or datetime.now(_SH)).astimezone(_SH)
    bars = []
    seen = set()
    for row in table.rows:
        if row.get("ts_code") != ts_code:
            raise RelayError("minute_symbol_mismatch")
        timestamp = _timestamp(row.get("trade_time"))
        if timestamp.date() != session:
            raise RelayError("minute_date_mismatch")
        if timestamp > observed:
            raise RelayError("minute_future_bar")
        if not start <= timestamp <= end or timestamp.second != 0 or (
            time(11, 30) < timestamp.time() < time(13)
        ):
            raise RelayError("minute_outside_window")
        if timestamp in seen:
            raise RelayError("minute_duplicate_time")
        seen.add(timestamp)
        prices = [_number(row.get(name), positive=True) for name in ("open", "high", "low", "close")]
        opening, high, low, close = prices
        if not low <= min(opening, close) <= max(opening, close) <= high:
            raise RelayError("minute_invalid_ohlc")
        volume_field = "vol" if "vol" in row else "volume" if "volume" in row else None
        volume = None if volume_field is None else _number(row[volume_field], positive=False)
        if "vol" in row and "volume" in row:
            if volume != _number(row["volume"], positive=False):
                raise RelayError("minute_ambiguous_volume")
        bars.append(ResearchMinuteBar(ts_code, timestamp, *prices, volume, volume_field))
    bars.sort(key=lambda bar: bar.trade_time)
    latest = bars[-1].trade_time if bars else None
    # Same-day timestamps alone cannot verify freshness or upstream cache age.
    freshness = ("no_data" if latest is None else "not_current_session"
                 if latest.date() != observed.date() else "unverified")
    semantics = ("recent_endpoint_date_validates_response_only" if api == "rt_min"
                 else "bounded_historical_session_request")
    return ResearchMinuteSample(api, ts_code, frequency, session, observed, latest,
                                tuple(bars), limit, len(bars) == limit, freshness, semantics)
