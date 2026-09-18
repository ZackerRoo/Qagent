from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from qagent.paper_trading import engine


@pytest.mark.parametrize("aware", [False, True])
def test_response_freshness_excludes_future_and_pre_signal_only_in_telemetry(aware):
    times = ["2026-09-18 10:00", "2026-09-18 10:09", "2026-09-18 10:11"]
    timestamps = pd.to_datetime(times)
    if aware:
        timestamps = timestamps.tz_localize("Asia/Shanghai").tz_convert("UTC")
    frame = pd.DataFrame({"timestamp": timestamps})
    original = frame.copy(deep=True)
    result = engine._minute_response_freshness(
        frame, datetime(2026, 9, 18, 10),
        datetime(2026, 9, 18, 2, 10, tzinfo=timezone.utc),
    )
    assert result["latest_received_at"] == "2026-09-18T10:11:00+08:00"
    assert result["latest_effective_asof_at"] == "2026-09-18T10:09:00+08:00"
    assert result["as_of_lag_seconds"] == "60.0"
    assert result["future_timestamp_rows"] == "1"
    pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize(
    "frame,reason",
    [
        (pd.DataFrame({"timestamp": ["bad"]}), "no_valid_timestamp"),
        (pd.DataFrame({"timestamp": ["2026-09-18 10:11"]}),
         "no_post_signal_asof_timestamp"),
        (pd.DataFrame({"close": [1]}), "missing_timestamp_column"),
    ],
)
def test_missing_timestamp_evidence_is_explicit(frame, reason):
    result = engine._minute_response_freshness(
        frame, datetime(2026, 9, 18, 10), datetime(2026, 9, 18, 10, 10),
    )
    assert result["reason"] == reason
    assert "as_of_lag_seconds" not in result


@pytest.mark.parametrize("kind", ["normal", "empty", "error", "diagnostic_error"])
def test_minute_wrapper_preserves_input_return_and_request(monkeypatch, kind):
    frame = pd.DataFrame({"timestamp": ["bad", "2026-09-18 10:11"]})
    if kind == "empty":
        frame = pd.DataFrame()
    if kind == "diagnostic_error":
        # Mixed unsortable values fail telemetry; a successful executor is still returned.
        frame = pd.DataFrame({"timestamp": [object(), datetime(2026, 9, 18)]})
    requests = []
    consumed = []

    def getter(ids, start, end):
        requests.append((ids, start, end))
        if kind == "error":
            raise RuntimeError("provider failed")
        return frame

    def evaluate(trade, bars, **kwargs):
        consumed.append(bars)
        return {"status": "open", "latest_price": 42}

    monkeypatch.setattr(engine, "_is_a_share_trade", lambda trade: True)
    monkeypatch.setattr(engine, "_trade_signal_datetime", lambda *args: datetime(2026, 9, 18, 10))
    monkeypatch.setattr(engine, "_trade_no_chase_above", lambda *args: None)
    monkeypatch.setattr(engine, "_source_context_latest_close", lambda *args: None)
    monkeypatch.setattr(engine, "_evaluate_trade_with_minutes", evaluate)
    observation = {}
    result = engine._try_evaluate_trade_with_minutes(
        None, SimpleNamespace(get_minute_bars=getter), SimpleNamespace(instrument_id="CN:000001"),
        max_holding_days=10, max_entry_wait_days=3,
        as_of=datetime(2026, 9, 18, 10, 10), source_context=object(),
        minute_observation=observation,
    )
    assert requests == [(["CN:000001"], datetime(2026, 9, 18, 10), datetime(2026, 9, 18, 10, 10))]
    if kind in ("empty", "error"):
        assert result == (None, 1, 0, 0)
        assert observation["reason"] == ("empty_response" if kind == "empty" else "minute_request_error")
        assert consumed == []
    else:
        assert result == ({"status": "open", "latest_price": 42}, 1, 2, 0)
        assert consumed[0] is frame
        if kind == "diagnostic_error":
            assert observation["reason"] == "timestamp_diagnostic_error"
