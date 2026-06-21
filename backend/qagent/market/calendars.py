from datetime import date, timedelta

from qagent.domain.enums import Market


def market_timezone(market: Market) -> str:
    return "America/New_York" if market == Market.US else "Asia/Shanghai"


def trading_calendar_name(market: Market) -> str:
    return "XNYS" if market == Market.US else "XSHG_XSHE"


def trading_day_offset(anchor: date, days: int) -> date:
    step = 1 if days >= 0 else -1
    remaining = abs(days)
    current = anchor

    while remaining:
        current = current + timedelta(days=step)
        if current.weekday() < 5:
            remaining -= 1
    return current
