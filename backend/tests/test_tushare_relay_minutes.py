from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock

import pytest

from qagent.providers.tushare_relay import RelayError, RelayTable
from qagent.providers.tushare_relay_minutes import fetch_research_minutes


NOW = datetime(2026, 9, 13, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def row(**updates):
    value = dict(ts_code="000001.SZ", trade_time="2026-09-11 15:00:00",
                 open=10, high=12, low=9, close=11, vol=123)
    value.update(updates)
    return value


def fetch(rows=None, **kwargs):
    client = Mock()
    client.query.return_value = RelayTable("rt_min", (), tuple(rows or [row()]), 300)
    result = fetch_research_minutes(client, ts_code="000001.SZ", requested_date="20260911",
                                    observed_at=NOW, **kwargs)
    return result, client


def test_previous_session_sample_is_not_realtime_or_historical_capability():
    result, client = fetch()
    assert result.realtime_freshness == "not_current_session"
    assert result.requested_date.isoformat() == "2026-09-11"
    assert result.observed_at == NOW
    assert result.latest_bar_time.date() == result.requested_date
    assert result.coverage == "sample_full_session_unknown"
    assert result.volume_unit == "upstream_unspecified"
    assert result.rows[0].volume == 123
    assert result.rows[0].volume_field == "vol"
    client.query.assert_called_once_with("rt_min", ts_code="000001.SZ", freq="1min", limit=300)


def test_same_day_is_still_unverified():
    client = Mock()
    client.query.return_value = RelayTable("rt_min", (), (row(),), 300)
    result = fetch_research_minutes(client, ts_code="000001.SZ", requested_date="20260911",
                                    observed_at=NOW.replace(day=11, hour=15))
    assert result.realtime_freshness == "unverified"


@pytest.mark.parametrize("api", ["stk_mins", "a_share_mins"])
def test_explicit_historical_query_one_bounded_session(api):
    result, client = fetch(api=api, limit=1)
    assert result.limit_reached
    client.query.assert_called_once_with(api, ts_code="000001.SZ", freq="1min", limit=1,
                                         start_date="2026-09-11 09:30:00",
                                         end_date="2026-09-11 15:00:00")


def test_sorted_unique_and_no_volume_conversion():
    result, _ = fetch([row(), row(trade_time="2026-09-11 14:59:00")])
    assert [bar.trade_time.minute for bar in result.rows] == [59, 0]
    value = row()
    del value["vol"]
    result, _ = fetch([value])
    assert result.rows[0].volume is None


@pytest.mark.parametrize("update,kind", [
    ({"ts_code": "600000.SH"}, "minute_symbol_mismatch"),
    ({"trade_time": "2026-09-10 15:00:00"}, "minute_date_mismatch"),
    ({"trade_time": "20260911"}, "minute_invalid_time"),
    ({"trade_time": "2026-09-11 12:00:00"}, "minute_outside_window"),
    ({"trade_time": "2026-09-11 15:00:01"}, "minute_outside_window"),
    ({"close": 13}, "minute_invalid_ohlc"),
    ({"open": 0}, "minute_invalid_number"),
    ({"high": float("inf")}, "minute_invalid_number"),
    ({"low": True}, "minute_invalid_number"),
    ({"vol": float("nan")}, "minute_invalid_number"),
    ({"vol": -1}, "minute_invalid_number"),
    ({"volume": 999}, "minute_ambiguous_volume"),
])
def test_invalid_rows_fail_closed(update, kind):
    with pytest.raises(RelayError) as error:
        fetch([row(**update)])
    assert error.value.kind == kind


def test_duplicates_rejected():
    with pytest.raises(RelayError, match="minute_duplicate_time"):
        fetch([row(), row()])


@pytest.mark.parametrize("params", [
    {"ts_code": "000001.SZ,600000.SH"}, {"requested_date": "20260931"},
    {"requested_date": "2026-09-11"}, {"limit": 1001}, {"limit": True},
    {"start_time": "15:00:00", "end_time": "09:30:00"},
    {"frequency": "day"}, {"api": "rt_k"}, {"observed_at": datetime(2026, 9, 13)},
])
def test_bad_request_never_queries(params):
    client = Mock()
    kwargs = dict(ts_code="000001.SZ", requested_date="20260911", observed_at=NOW)
    kwargs.update(params)
    with pytest.raises(RelayError):
        fetch_research_minutes(client, **kwargs)
    client.query.assert_not_called()


def test_future_bar_is_rejected():
    client = Mock()
    client.query.return_value = RelayTable("rt_min", (), (row(),), 300)
    with pytest.raises(RelayError, match="minute_future_bar"):
        fetch_research_minutes(client, ts_code="000001.SZ", requested_date="20260911",
                               observed_at=NOW.replace(day=11))


@pytest.mark.parametrize("kind", ["pending", "upstream_pool_exhausted", "transport_error"])
def test_errors_propagate_no_fallback(kind):
    client = Mock()
    client.query.side_effect = RelayError(kind)
    with pytest.raises(RelayError, match=kind):
        fetch_research_minutes(client, ts_code="000001.SZ", requested_date="20260911",
                               observed_at=NOW)
    assert client.query.call_count == 1


def test_empty_is_not_complete():
    client = Mock()
    client.query.return_value = RelayTable("rt_min", (), (), 300)
    result = fetch_research_minutes(client, ts_code="000001.SZ", requested_date="20260911",
                                    observed_at=NOW)
    assert result.realtime_freshness == "no_data"
    assert result.latest_bar_time is None
    assert result.coverage == "sample_full_session_unknown"
