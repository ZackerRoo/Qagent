from datetime import date, datetime

import pandas as pd
import pytest
import requests

from qagent.providers.daily_fallback import DailyFallbackMarketDataProvider
from qagent.providers.tickflow_free import (
    TICKFLOW_INDEX_SOURCE_PROVIDER,
    TICKFLOW_PAIRED_SOURCE_PROVIDER,
    TickFlowFreeDailyProvider,
    _date_to_epoch_ms,
    _normalize_kline_payload,
    _to_tickflow_symbol,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self.payload


class RecordingSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def _payload(*, scale: float = 1.0) -> dict:
    return {
        "data": {
            "timestamp": [1767312000000, 1767571200000],
            "open": [10.0 * scale, 10.2 * scale],
            "high": [10.4 * scale, 10.5 * scale],
            "low": [9.9 * scale, 10.1 * scale],
            "close": [10.3 * scale, 10.4 * scale],
            "volume": [800_000, 820_000],
            "amount": [8_000_000, 8_200_000],
        }
    }


@pytest.mark.parametrize(
    ("instrument_id", "expected"),
    [
        ("CN:600000", ("600000.SH", False)),
        ("CN:000001", ("000001.SZ", False)),
        ("CN:920662", ("920662.BJ", False)),
        ("CN:588000", ("588000.SH", False)),
        ("CN:000688.IDX", ("000688.SH", True)),
        ("CN:399006.IDX", ("399006.SZ", True)),
    ],
)
def test_tickflow_symbol_mapping(instrument_id, expected):
    assert _to_tickflow_symbol(instrument_id) == expected


def test_tickflow_free_provider_normalizes_raw_and_forward_adjusted_bars():
    session = RecordingSession([FakeResponse(_payload()), FakeResponse(_payload(scale=0.5))])
    provider = TickFlowFreeDailyProvider(session=session)

    bars = provider.get_daily_bars(
        ["CN:600000"],
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert bars["instrument_id"].tolist() == ["CN:600000", "CN:600000"]
    assert bars["provider"].eq(TICKFLOW_PAIRED_SOURCE_PROVIDER).all()
    assert bars["trade_date"].tolist() == [date(2026, 1, 2), date(2026, 1, 5)]
    assert bars["close"].tolist() == [10.3, 10.4]
    assert bars["adjusted_close"].tolist() == [5.15, 5.2]
    assert bars["adjustment_factor"].tolist() == [0.5, 0.5]
    assert bars["adjustment_type"].eq("forward").all()
    assert bars["turnover"].tolist() == [8_000_000, 8_200_000]
    assert [call["params"]["adjust"] for call in session.calls] == ["none", "forward"]
    assert all("x-api-key" not in call["headers"] for call in session.calls)


def test_tickflow_free_provider_treats_index_as_unadjusted():
    session = RecordingSession([FakeResponse(_payload())])
    provider = TickFlowFreeDailyProvider(session=session)

    bars = provider.get_daily_bars(
        ["CN:000688.IDX"],
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert len(session.calls) == 1
    assert session.calls[0]["params"]["symbol"] == "000688.SH"
    assert bars["provider"].eq(TICKFLOW_INDEX_SOURCE_PROVIDER).all()
    assert bars["adjusted_close"].tolist() == bars["close"].tolist()
    assert bars["adjustment_type"].eq("none").all()


def test_tickflow_free_provider_rejects_malformed_column_lengths():
    payload = _payload()
    payload["data"]["close"] = [10.3]
    provider = TickFlowFreeDailyProvider(
        session=RecordingSession([FakeResponse(payload)])
    )

    bars = provider.get_daily_bars(
        ["CN:600000"],
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert bars.empty
    assert "inconsistent lengths" in provider.last_errors[0]


def test_tickflow_epoch_uses_shanghai_trade_date_and_request_boundaries():
    trade_date = date(2026, 8, 17)

    assert _date_to_epoch_ms(trade_date) == 1786896000000
    assert _date_to_epoch_ms(trade_date, end_of_day=True) == 1786982399999

    frame = _normalize_kline_payload(
        {
            "timestamp": [1786896000000],
            "open": [9.07],
            "high": [9.16],
            "low": [8.83],
            "close": [9.06],
            "volume": [100],
            "amount": [906],
        },
        trade_date,
        trade_date,
    )

    assert frame["trade_date"].tolist() == [trade_date]


def test_tickflow_free_provider_keeps_raw_rows_when_adjusted_request_fails():
    session = RecordingSession(
        [FakeResponse(_payload()), FakeResponse({}, status_code=503)]
    )
    provider = TickFlowFreeDailyProvider(session=session)

    bars = provider.get_daily_bars(
        ["CN:600000"],
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert bars["close"].tolist() == [10.3, 10.4]
    assert bars["adjusted_close"].isna().all()
    assert bars["adjustment_type"].isna().all()
    assert "adjusted history" in provider.last_errors[0]


class StubDailyProvider:
    def __init__(self, name: str, rows: dict[str, float], errors: list[str] | None = None):
        self.name = name
        self.rows = rows
        self.errors = errors or []
        self.last_errors: list[str] = []
        self.daily_calls: list[list[str]] = []
        self.historical_calls: list[list[str]] = []
        self.minute_calls = 0

    def _bars(self, instrument_ids):
        records = [
            {
                "instrument_id": instrument_id,
                "trade_date": date(2026, 1, 5),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 100,
                "turnover": 1000,
                "provider": self.name,
                "adjusted_open": close,
                "adjusted_high": close,
                "adjusted_low": close,
                "adjusted_close": close,
                "adjustment_factor": 1.0,
                "adjustment_type": "none",
            }
            for instrument_id in instrument_ids
            if (close := self.rows.get(instrument_id)) is not None
        ]
        return pd.DataFrame(records)

    def get_daily_bars(self, instrument_ids, start, end):
        del start, end
        self.daily_calls.append(instrument_ids)
        self.last_errors = list(self.errors)
        return self._bars(instrument_ids)

    def get_historical_daily_bars(self, instrument_ids, start, end):
        del start, end
        self.historical_calls.append(instrument_ids)
        self.last_errors = list(self.errors)
        return self._bars(instrument_ids)

    def get_snapshot(self, instrument_ids):
        self.last_errors = list(self.errors)
        return self._bars(instrument_ids)

    def get_minute_bars(self, instrument_ids, start, end):
        del instrument_ids, start, end
        self.minute_calls += 1
        return pd.DataFrame(
            [
                {
                    "instrument_id": "CN:000001",
                    "timestamp": datetime(2026, 1, 5, 9, 31),
                    "open": 10,
                    "high": 10,
                    "low": 10,
                    "close": 10,
                    "volume": 100,
                    "provider": self.name,
                }
            ]
        )


def test_daily_fallback_only_requests_symbols_missing_from_primary():
    primary = StubDailyProvider(
        "primary",
        {"CN:000001": 10.0},
        errors=["CN:600000: upstream failed"],
    )
    fallback = StubDailyProvider("fallback", {"CN:600000": 20.0})
    provider = DailyFallbackMarketDataProvider(primary, fallback, name="free_cn")

    bars = provider.get_daily_bars(
        ["CN:000001", "CN:600000"],
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert primary.daily_calls == [["CN:000001", "CN:600000"]]
    assert fallback.daily_calls == [["CN:600000"]]
    assert bars.groupby("instrument_id")["provider"].first().to_dict() == {
        "CN:000001": "primary",
        "CN:600000": "fallback",
    }
    assert provider.last_fallback_instruments == ["CN:600000"]
    assert provider.last_errors == []


def test_daily_fallback_skips_large_missing_batches_when_bounded():
    requested = [f"CN:{index:06d}" for index in range(25)]
    primary = StubDailyProvider("primary", {})
    fallback = StubDailyProvider("fallback", {item: 20.0 for item in requested})
    provider = DailyFallbackMarketDataProvider(
        primary,
        fallback,
        max_fallback_instruments=20,
    )

    bars = provider.get_daily_bars(
        requested,
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert bars.empty
    assert fallback.daily_calls == []
    assert provider.last_fallback_instruments == []
    assert provider.last_errors == [
        "daily fallback skipped: 25 instruments exceeds the configured limit of 20"
    ]


def test_daily_fallback_uses_historical_contract_and_never_falls_back_minutes():
    primary = StubDailyProvider("primary", {})
    fallback = StubDailyProvider("fallback", {"CN:600000": 20.0})
    provider = DailyFallbackMarketDataProvider(primary, fallback)

    historical = provider.get_historical_daily_bars(
        ["CN:600000"],
        date(2026, 1, 1),
        date(2026, 1, 31),
    )
    minute = provider.get_minute_bars(
        ["CN:600000"],
        datetime(2026, 1, 5, 9, 30),
        datetime(2026, 1, 5, 15, 0),
    )

    assert not historical.empty
    assert primary.historical_calls == [["CN:600000"]]
    assert fallback.historical_calls == [["CN:600000"]]
    assert primary.minute_calls == 1
    assert fallback.minute_calls == 0
    assert minute["provider"].tolist() == ["primary"]
