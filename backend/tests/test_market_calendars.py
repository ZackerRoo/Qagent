from datetime import date

from qagent.market.calendars import trading_day_offset, trading_sessions_elapsed


def test_cn_trading_calendar_skips_national_day_market_closure():
    anchor = date(2026, 9, 30)

    assert trading_day_offset(anchor, 1) == date(2026, 10, 8)
    assert trading_day_offset(anchor, 2) == date(2026, 10, 9)
    assert trading_sessions_elapsed(anchor, date(2026, 10, 9)) == 2
