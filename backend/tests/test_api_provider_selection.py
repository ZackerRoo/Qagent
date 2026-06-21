from datetime import date

import pandas as pd
from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.providers.fixtures import FixtureMarketDataProvider


class RecordingProvider:
    def __init__(self, name: str, fixture_id: str):
        self.name = name
        self.fixture_id = fixture_id
        self.calls: list[list[str]] = []

    def get_daily_bars(self, instrument_ids: list[str], start: date, end: date) -> pd.DataFrame:
        self.calls.append(instrument_ids)
        fixture = FixtureMarketDataProvider()
        bars = fixture.get_daily_bars([self.fixture_id], start, end).copy()
        frames = []
        for instrument_id in instrument_ids:
            frame = bars.copy()
            frame["instrument_id"] = instrument_id
            frames.append(frame)
        return pd.concat(frames, ignore_index=True)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        bars = self.get_daily_bars(instrument_ids, date(2026, 1, 1), date(2026, 12, 31))
        return bars.groupby("instrument_id", as_index=False).tail(1).reset_index(drop=True)


def test_opportunities_endpoint_can_scan_free_us_and_cn_providers(monkeypatch):
    us_provider = RecordingProvider("free_us", "US:TEST")
    cn_provider = RecordingProvider("free_cn", "CN:000001")

    monkeypatch.setattr(
        "qagent.providers.factory.FreeUsMarketDataProvider",
        lambda: us_provider,
    )
    monkeypatch.setattr(
        "qagent.providers.factory.FreeCnMarketDataProvider",
        lambda: cn_provider,
    )

    client = TestClient(create_app())
    response = client.get("/api/opportunities?provider=free&symbols=US:AAPL,CN:000001")

    assert response.status_code == 200
    body = response.json()
    assert body["data_health"]["mode"] == "free"
    assert {card["instrument_id"] for card in body["cards"]} == {"US:AAPL", "CN:000001"}
    assert us_provider.calls == [["US:AAPL"]]
    assert cn_provider.calls == [["CN:000001"]]
