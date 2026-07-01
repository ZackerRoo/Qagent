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


def test_opportunities_endpoint_can_scan_free_us_and_cn_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'provider-selection.db'}")
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


def test_agent_endpoint_uses_requested_provider_and_symbols(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'agent-provider.db'}")
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
    response = client.post(
        "/api/agent/query",
        json={
            "question": "今天推荐什么股票，什么时候买什么时候卖？",
            "provider": "free",
            "symbols": "CN:000001",
        },
    )

    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "平安银行 000001.SZ" in answer
    assert "CN:000001" not in answer
    assert "US:TEST" not in answer
    assert "触发" in answer or "买点" in answer
    assert "止损" in answer
    assert cn_provider.calls == [["CN:000001"]]
    assert us_provider.calls == []


def test_opportunities_endpoint_expands_cn_all_with_universe_metadata(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'cn-all.db'}")
    cn_provider = RecordingProvider("free_cn", "CN:000001")

    monkeypatch.setattr(
        "qagent.providers.factory.FreeCnMarketDataProvider",
        lambda: cn_provider,
    )
    monkeypatch.setattr(
        "qagent.providers.factory.FreeUsMarketDataProvider",
        lambda: RecordingProvider("free_us", "US:TEST"),
    )

    def fake_resolve_symbol_tokens(symbols, **kwargs):
        assert symbols == ["CN:ALL"]
        return type(
            "Resolved",
            (),
            {
                "symbols": ["CN:000001", "CN:600000"],
                "data_health": {
                    "universe_total": "2",
                    "universe_eligible": "2",
                    "universe_selected": "2",
                    "universe_source": "akshare_spot_em",
                },
                "is_dynamic": True,
            },
        )()

    monkeypatch.setattr("qagent.api.routes.resolve_symbol_tokens", fake_resolve_symbol_tokens)

    client = TestClient(create_app())
    response = client.get("/api/opportunities?provider=free&symbols=CN:ALL")

    assert response.status_code == 200
    body = response.json()
    assert body["data_health"]["universe_total"] == "2"
    assert body["data_health"]["universe_selected"] == "2"
    assert body["data_health"]["strategy_data_skipped"] == "true"
    assert body["data_health"]["scanned"] == "2"
    assert cn_provider.calls == [["CN:000001"], ["CN:600000"]]
