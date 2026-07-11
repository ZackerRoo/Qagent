from datetime import date
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

from qagent.domain.enums import Market


def market_timezone(market: Market) -> str:
    return "America/New_York" if market == Market.US else "Asia/Shanghai"


def trading_calendar_name(market: Market) -> str:
    return "XNYS" if market == Market.US else "XSHG_XSHE"


def trading_day_offset(anchor: date, days: int, market: Market = Market.CN) -> date:
    if days == 0:
        return anchor
    calendar = _calendar(market)
    anchor_ts = pd.Timestamp(anchor)
    if calendar.is_session(anchor_ts):
        return calendar.session_offset(anchor_ts, days).date()

    direction = "next" if days > 0 else "previous"
    first_session = calendar.date_to_session(anchor_ts, direction=direction)
    remaining_offset = days - 1 if days > 0 else days + 1
    return calendar.session_offset(first_session, remaining_offset).date()


def trading_sessions_elapsed(
    start: date,
    end: date,
    market: Market = Market.CN,
) -> int:
    """Count exchange sessions after ``start`` through ``end`` inclusive."""
    if start == end:
        return 0
    if end < start:
        return -trading_sessions_elapsed(end, start, market)
    sessions = _calendar(market).sessions_in_range(
        pd.Timestamp(start) + pd.Timedelta(days=1),
        pd.Timestamp(end),
    )
    return len(sessions)


def trading_sessions_in_range(
    start: date,
    end: date,
    market: Market = Market.CN,
) -> list[date]:
    if end < start:
        return []
    return [
        session.date()
        for session in _calendar(market).sessions_in_range(
            pd.Timestamp(start),
            pd.Timestamp(end),
        )
    ]


@lru_cache(maxsize=2)
def _calendar(market: Market):
    name = "XNYS" if market == Market.US else "XSHG"
    return xcals.get_calendar(name)
