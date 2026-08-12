from types import SimpleNamespace

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from qagent.api import routes
from qagent.app import create_app
from qagent.providers.fuyao import (
    FuyaoClient,
    FuyaoMarketDataProvider,
    FuyaoProviderError,
    FuyaoSnapshotProvider,
    fuyao_capability_manifest,
    to_fuyao_thscode,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payloads.pop(0))


def _snapshot_item(thscode: str, close: float) -> dict[str, object]:
    return {
        "thscode": thscode,
        "ticker": thscode.split(".", 1)[0],
        "last_price": close,
        "price_change": 1.5,
        "price_change_ratio_pct": 0.5,
        "open_price": close - 2,
        "high_price": close + 3,
        "low_price": close - 4,
        "prev_price": close - 1.5,
        "volume": 123456,
        "turnover": 7890123,
    }


def _success_payload(*items: dict[str, object]) -> dict[str, object]:
    return {
        "code": 0,
        "message": "success",
        "request_id": "request-123",
        "data": {
            "timestamp": 1_747_584_000_000,
            "total": len(items),
            "item": list(items),
        },
    }


def test_to_fuyao_thscode_normalizes_cn_instruments():
    assert to_fuyao_thscode("CN:600519") == "600519.SH"
    assert to_fuyao_thscode("CN:000001") == "000001.SZ"
    assert to_fuyao_thscode("CN:830799") == "830799.BJ"
    assert to_fuyao_thscode("CN:399006.IDX") == "399006.SZ"
    assert to_fuyao_thscode("CN:000300.IDX") == "000300.SH"

    with pytest.raises(ValueError, match="CN instruments only"):
        to_fuyao_thscode("US:AAPL")


def test_fuyao_snapshot_provider_normalizes_response_and_preserves_order():
    session = FakeSession(
        [
            _success_payload(
                _snapshot_item("600519.SH", 1500.0),
                _snapshot_item("000001.SZ", 12.5),
            )
        ]
    )
    provider = FuyaoSnapshotProvider(
        "secret-key",
        session=session,
        max_attempts=1,
    )

    frame = provider.get_snapshot(["CN:600519", "CN:000001"])

    assert frame["instrument_id"].tolist() == ["CN:600519", "CN:000001"]
    assert frame["close"].tolist() == [1500.0, 12.5]
    assert frame["provider"].unique().tolist() == ["fuyao_snapshot"]
    assert provider.last_request is not None
    assert provider.last_request.request_id == "request-123"
    assert session.calls[0][1]["params"] == {"thscodes": "600519.SH,000001.SZ"}
    assert session.calls[0][1]["headers"] == {"X-api-key": "secret-key"}


def test_fuyao_snapshot_provider_retries_rate_limit_once():
    session = FakeSession(
        [
            {"code": 4001, "message": "rate limited", "request_id": "rate-1"},
            _success_payload(_snapshot_item("600519.SH", 1500.0)),
        ]
    )
    provider = FuyaoSnapshotProvider(
        "secret-key",
        session=session,
        max_attempts=2,
        retry_backoff_seconds=0,
    )

    frame = provider.get_snapshot(["CN:600519"])

    assert len(frame) == 1
    assert len(session.calls) == 2


def test_fuyao_snapshot_provider_redacts_key_from_business_error():
    session = FakeSession(
        [{"code": 2001, "message": "bad secret-key", "request_id": "auth-1"}]
    )
    provider = FuyaoSnapshotProvider("secret-key", session=session, max_attempts=1)

    with pytest.raises(FuyaoProviderError) as captured:
        provider.get_snapshot(["CN:600519"])

    assert captured.value.code == 2001
    assert captured.value.request_id == "auth-1"
    assert "secret-key" not in str(captured.value)
    assert "[redacted]" in str(captured.value)


def test_fuyao_snapshot_provider_fails_closed_on_missing_symbol():
    session = FakeSession([_success_payload(_snapshot_item("600519.SH", 1500.0))])
    provider = FuyaoSnapshotProvider("secret-key", session=session, max_attempts=1)

    with pytest.raises(FuyaoProviderError, match="missing 000001.SZ"):
        provider.get_snapshot(["CN:600519", "CN:000001"])


def test_fuyao_client_omits_none_query_params_and_tracks_request_metadata():
    session = FakeSession([_success_payload()])
    client = FuyaoClient("secret-key", session=session, max_attempts=1)

    client.get_corporate_actions("600519.SH")

    assert session.calls[0][1]["params"] == {"thscode": "600519.SH"}
    assert client.last_request is not None
    assert client.last_request.path == "/api/a-share/corporate-actions/adjustment-factors"


def test_fuyao_client_market_page_is_paginated_and_bounded():
    session = FakeSession([_success_payload()])
    client = FuyaoClient("secret-key", session=session, max_attempts=1)

    client.get_stock_market_page(limit=200, offset=400)

    assert session.calls[0][1]["params"] == {"limit": 200, "offset": 400}
    with pytest.raises(ValueError, match="between 1 and 1000"):
        client.get_stock_market_page(limit=1001)


def test_fuyao_client_bounds_explicit_snapshot_batches():
    client = FuyaoClient("secret-key", session=FakeSession([]), max_attempts=1)

    with pytest.raises(ValueError, match="at most 50"):
        client.get_stock_snapshot_data([f"{index:06d}.SZ" for index in range(51)])


def test_fuyao_market_provider_routes_stock_index_and_etf_snapshots():
    session = FakeSession(
        [
            _success_payload(_snapshot_item("600519.SH", 1500.0)),
            _success_payload(_snapshot_item("000300.SH", 4200.0)),
            _success_payload(_snapshot_item("510300.SH", 4.2)),
        ]
    )
    provider = FuyaoMarketDataProvider(
        "secret-key",
        session=session,
        max_attempts=1,
    )

    frame = provider.get_snapshot(["CN:600519", "CN:000300.IDX", "CN:510300"])

    assert frame["instrument_id"].tolist() == [
        "CN:600519",
        "CN:000300.IDX",
        "CN:510300",
    ]
    assert frame["close"].tolist() == [1500.0, 4200.0, 4.2]
    assert [call[0].removeprefix("https://fuyao.aicubes.cn") for call in session.calls] == [
        "/api/a-share/prices/snapshot",
        "/api/a-share-index/prices/snapshot",
        "/api/fund/market/snapshot",
    ]


def test_fuyao_capability_manifest_is_explicit_about_boundaries():
    manifest = fuyao_capability_manifest(configured=True)

    assert manifest["configured"] is True
    assert manifest["decision_weight_applied"] is False
    assert manifest["minute_bars_supported"] is False
    assert manifest["full_market_export"]["browser_session_required"] is True
    assert {group["id"] for group in manifest["groups"]} == {
        "market_data",
        "fundamentals",
        "index",
        "fund",
        "special_data",
    }


def test_fuyao_probe_api_is_read_only(monkeypatch):
    class StubFuyaoProvider:
        name = "fuyao_snapshot"

        def __init__(self, api_key, **kwargs):
            assert api_key == "configured-key"
            self.last_request = SimpleNamespace(
                request_id="request-api",
                timestamp="2026-08-12T10:00:00+08:00",
            )

        def get_snapshot(self, instrument_ids):
            return pd.DataFrame(
                [
                    {
                        "instrument_id": instrument_ids[0],
                        "close": 1500.0,
                        "provider": self.name,
                    }
                ]
            )

    monkeypatch.setenv("QAGENT_FUYAO_API_KEY", "configured-key")
    monkeypatch.setattr(routes, "FuyaoSnapshotProvider", StubFuyaoProvider)
    client = TestClient(create_app())

    response = client.get(
        "/api/provider-status/fuyao/probe",
        params={"instrument_ids": "CN:600519"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["source_role"] == "live_snapshot_and_tertiary_daily_fallback"
    assert body["execution_enabled"] is False
    assert body["paper_market_data_enabled"] is True
    assert body["minute_bars_supported"] is False
    assert body["received"] == 1


def test_fuyao_probe_api_rejects_more_than_twenty_symbols(monkeypatch):
    monkeypatch.setenv("QAGENT_FUYAO_API_KEY", "configured-key")
    client = TestClient(create_app())
    symbols = ",".join(f"CN:{index:06d}" for index in range(21))

    response = client.get(
        "/api/provider-status/fuyao/probe",
        params={"instrument_ids": symbols},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Fuyao probe accepts at most 20 instruments"
