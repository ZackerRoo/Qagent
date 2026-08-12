from datetime import date

import pandas as pd

from qagent.providers.snapshot_preferred import SnapshotPreferredMarketDataProvider


def _bar(instrument_id: str, close: float, provider: str) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "trade_date": date(2026, 8, 12),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100,
        "turnover": 1_000,
        "provider": provider,
    }


class StubProvider:
    def __init__(self, name: str, snapshots: dict[str, float]):
        self.name = name
        self.snapshots = snapshots
        self.last_errors: list[str] = []
        self.snapshot_calls: list[list[str]] = []

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        self.snapshot_calls.append(instrument_ids)
        rows = [
            _bar(instrument_id, self.snapshots[instrument_id], self.name)
            for instrument_id in instrument_ids
            if instrument_id in self.snapshots
        ]
        self.last_errors = [
            f"{instrument_id}: unavailable"
            for instrument_id in instrument_ids
            if instrument_id not in self.snapshots
        ]
        return pd.DataFrame(rows)

    def get_daily_bars(self, instrument_ids, start, end):
        del start, end
        return self.get_snapshot(instrument_ids)


def test_snapshot_preferred_uses_live_source_and_only_falls_back_for_missing():
    history = StubProvider("history", {"CN:000001": 10.0, "CN:600519": 1400.0})
    live = StubProvider("live", {"CN:000001": 10.5})
    provider = SnapshotPreferredMarketDataProvider(history, live)

    frame = provider.get_snapshot(["CN:000001", "CN:600519"])

    assert frame["instrument_id"].tolist() == ["CN:000001", "CN:600519"]
    assert frame["close"].tolist() == [10.5, 1400.0]
    assert frame["provider"].tolist() == ["live", "history"]
    assert live.snapshot_calls == [["CN:000001", "CN:600519"]]
    assert history.snapshot_calls == [["CN:600519"]]
    assert provider.last_fallback_instruments == ["CN:600519"]
    assert provider.last_errors == []


def test_snapshot_preferred_keeps_unrecovered_live_errors():
    history = StubProvider("history", {})
    live = StubProvider("live", {})
    provider = SnapshotPreferredMarketDataProvider(history, live)

    frame = provider.get_snapshot(["CN:000001"])

    assert frame.empty
    assert provider.last_errors == ["CN:000001: unavailable", "CN:000001: unavailable"]


def test_snapshot_preferred_skips_live_source_for_large_batches():
    requested = [f"CN:{index:06d}" for index in range(3)]
    history = StubProvider("history", {item: 10.0 for item in requested})
    live = StubProvider("live", {item: 20.0 for item in requested})
    provider = SnapshotPreferredMarketDataProvider(
        history,
        live,
        max_preferred_instruments=2,
    )

    frame = provider.get_snapshot(requested)

    assert frame["provider"].unique().tolist() == ["history"]
    assert history.snapshot_calls == [requested]
    assert live.snapshot_calls == []
