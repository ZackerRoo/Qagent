from datetime import date
from http.client import RemoteDisconnected
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pandas as pd
import pytest
import requests

from qagent.api import routes
from qagent.app import create_app
from qagent.providers.fuyao import (
    FuyaoClient,
    FuyaoMarketDataProvider,
    FuyaoProviderError,
    FuyaoSnapshotProvider,
    fuyao_telemetry_data_health,
    fuyao_capability_manifest,
    reset_fuyao_telemetry,
    to_fuyao_thscode,
)
from qagent.providers.failure_state import ProviderFailureStateRegistry


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


def test_fuyao_snapshot_provider_degrades_noncritical_numeric_fields():
    item = _snapshot_item("600519.SH", 1500.0)
    item.update(
        {
            "open_price": "--",
            "price_change": "unavailable",
            "price_change_ratio_pct": None,
            "volume": "unknown",
        }
    )
    provider = FuyaoSnapshotProvider(
        "secret-key",
        session=FakeSession([_success_payload(item)]),
        max_attempts=1,
    )

    frame = provider.get_snapshot(["CN:600519"])

    row = frame.iloc[0]
    assert row["close"] == 1500.0
    assert row["open"] == 1498.5
    assert row["previous_close"] == 1498.5
    assert row["price_change"] == 1.5
    assert row["price_change_ratio_pct"] == pytest.approx(1.5 / 1498.5 * 100)
    assert pd.isna(row["volume"])
    assert provider.last_errors == []
    health = fuyao_telemetry_data_health(provider)
    assert health["fuyao_telemetry"] == "ready"
    assert health["fuyao_errors"] == "0"
    assert health["fuyao_degraded_snapshot_rows"] == "1"
    assert health["fuyao_degraded_snapshot_field_mix"] == (
        "open_price=1,price_change=1,price_change_ratio_pct=1,volume=1"
    )


def test_fuyao_snapshot_provider_uses_last_price_when_open_and_previous_close_are_bad():
    item = _snapshot_item("600519.SH", 1500.0)
    item["open_price"] = "--"
    item["prev_price"] = None
    provider = FuyaoSnapshotProvider(
        "secret-key",
        session=FakeSession([_success_payload(item)]),
        max_attempts=1,
    )

    row = provider.get_snapshot(["CN:600519"]).iloc[0]

    assert row["close"] == 1500.0
    assert row["open"] == 1500.0
    assert pd.isna(row["previous_close"])


@pytest.mark.parametrize("last_price", ["--", None, 0, float("nan")])
def test_fuyao_snapshot_provider_fails_closed_on_invalid_last_price(last_price):
    item = _snapshot_item("600519.SH", 1500.0)
    item["last_price"] = last_price
    provider = FuyaoSnapshotProvider(
        "secret-key",
        session=FakeSession([_success_payload(item)]),
        max_attempts=1,
    )

    with pytest.raises(FuyaoProviderError, match="last_price"):
        provider.get_snapshot(["CN:600519"])


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
    health = fuyao_telemetry_data_health(
        SimpleNamespace(
            snapshot_provider=provider,
            fallback=provider,
            providers_by_market={"CN": provider},
        )
    )
    assert health["fuyao_clients"] == "1"
    assert health["fuyao_requests"] == "1"
    assert health["fuyao_attempts"] == "2"
    assert health["fuyao_successes"] == "1"
    assert health["fuyao_retries"] == "1"
    assert health["fuyao_last_request_id"] == "request-123"

    assert reset_fuyao_telemetry(provider) == 1
    assert fuyao_telemetry_data_health(provider)["fuyao_telemetry"] == "idle"


def test_fuyao_client_retries_remote_disconnect_with_bounded_transport_retry():
    class RemoteDisconnectThenSuccessSession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if len(self.calls) == 1:
                raise RemoteDisconnected("peer closed connection")
            return FakeResponse(_success_payload(_snapshot_item("600519.SH", 1500.0)))

    session = RemoteDisconnectThenSuccessSession([])
    provider = FuyaoSnapshotProvider(
        "secret-key",
        base_url="https://remote-disconnect-retry.example",
        session=session,
        max_attempts=2,
        retry_backoff_seconds=0,
    )

    frame = provider.get_snapshot(["CN:600519"])

    assert frame["close"].tolist() == [1500.0]
    assert len(session.calls) == 2
    health = fuyao_telemetry_data_health(provider)
    assert health["fuyao_attempts"] == "2"
    assert health["fuyao_retries"] == "1"


def test_fuyao_remote_disconnect_never_populates_negative_capability_cache():
    class RemoteDisconnectSession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            raise RemoteDisconnected("peer closed connection")

    base_url = "https://remote-disconnect-no-negative.example"
    failing = FuyaoClient(
        "secret-key",
        base_url=base_url,
        session=RemoteDisconnectSession([]),
        max_attempts=1,
    )
    with pytest.raises(FuyaoProviderError, match="transport layer"):
        failing.get_stock_snapshot_data(["600519.SH"])

    recovery_session = FakeSession([_success_payload()])
    recovery = FuyaoClient(
        "secret-key",
        base_url=base_url,
        session=recovery_session,
        max_attempts=1,
    )
    recovery.get_stock_snapshot_data(["600519.SH"])

    assert len(recovery_session.calls) == 1
    assert fuyao_telemetry_data_health(recovery)["fuyao_negative_capability_skips"] == "0"


def test_fuyao_snapshot_provider_redacts_key_from_business_error():
    session = FakeSession([{"code": 2001, "message": "bad secret-key", "request_id": "auth-1"}])
    provider = FuyaoSnapshotProvider("secret-key", session=session, max_attempts=1)

    with pytest.raises(FuyaoProviderError) as captured:
        provider.get_snapshot(["CN:600519"])

    assert captured.value.code == 2001
    assert captured.value.request_id == "auth-1"
    assert "secret-key" not in str(captured.value)
    assert "[redacted]" in str(captured.value)
    health = fuyao_telemetry_data_health(provider)
    assert health["fuyao_error_category_mix"] == "authentication=1"


def test_fuyao_telemetry_classifies_unsupported_assets():
    session = FakeSession(
        [{"code": 29028, "message": "unsupported ETF asset", "request_id": "asset-1"}]
    )
    provider = FuyaoSnapshotProvider("secret-key", session=session, max_attempts=1)

    with pytest.raises(FuyaoProviderError):
        provider.get_snapshot(["CN:510300"])

    health = fuyao_telemetry_data_health(provider)
    assert health["fuyao_errors"] == "1"
    assert health["fuyao_error_category_mix"] == "unsupported_asset=1"


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


def test_fuyao_limit_up_pool_uses_documented_trade_date_contract():
    session = FakeSession([_success_payload()])
    client = FuyaoClient("secret-key", session=session, max_attempts=1)

    client.get_limit_up_pool(trade_date=date(2026, 7, 1), page=1, size=200)

    assert session.calls[0][1]["params"] == {
        "date_ms": 1_782_835_200_000,
        "page": 1,
        "size": 200,
    }
    with pytest.raises(ValueError, match="between 1 and 200"):
        client.get_limit_up_pool(size=201)


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


def test_fuyao_market_provider_isolates_bad_symbol_in_snapshot_batch():
    session = FakeSession(
        [
            {"code": 1002, "message": "Unknown thscode", "request_id": "batch"},
            _success_payload(_snapshot_item("600519.SH", 1500.0)),
            {"code": 1002, "message": "Unknown thscode", "request_id": "single"},
        ]
    )
    provider = FuyaoMarketDataProvider(
        "secret-key",
        session=session,
        max_attempts=1,
    )

    frame = provider.get_snapshot(["CN:600519", "CN:000004"])

    assert frame["instrument_id"].tolist() == ["CN:600519"]
    assert len(session.calls) == 3
    assert len(provider.last_errors) == 1
    assert provider.last_errors[0].startswith("CN:000004: fuyao snapshot:")


@pytest.mark.parametrize(
    ("code", "message", "method_name", "arguments"),
    [
        (1002, "Unknown thscode", "get_stock_snapshot_data", (["600519.SH"],)),
        (3001, "Fund not found", "get_fund_profile", ("510300.SH",)),
        (
            3004,
            "This fund does not support market data",
            "get_fund_snapshot_data",
            ("510300.SH",),
        ),
    ],
)
def test_fuyao_soft_negative_capability_cache_expires_and_recovers(
    monkeypatch,
    code,
    message,
    method_name,
    arguments,
):
    clock = [100.0]
    monkeypatch.setattr("qagent.providers.fuyao.time.monotonic", lambda: clock[0])
    session = FakeSession(
        [
            {"code": code, "message": message, "request_id": f"error-{code}"},
            _success_payload(),
            _success_payload(),
        ]
    )
    client = FuyaoClient(
        "secret-key",
        base_url=f"https://negative-{code}.example",
        session=session,
        max_attempts=1,
        negative_capability_ttl_seconds=10,
    )
    request = getattr(client, method_name)

    with pytest.raises(FuyaoProviderError) as initial:
        request(*arguments)

    recovery_client = FuyaoClient(
        "secret-key",
        base_url=client.base_url,
        session=session,
        max_attempts=1,
        negative_capability_ttl_seconds=10,
    )
    request = getattr(recovery_client, method_name)
    with pytest.raises(FuyaoProviderError, match="soft negative capability cache skipped"):
        request(*arguments)

    assert initial.value.code == code
    assert len(session.calls) == 1

    clock[0] = 111.0
    request(*arguments)
    request(*arguments)

    assert len(session.calls) == 3
    health = fuyao_telemetry_data_health(recovery_client)
    assert health["fuyao_negative_capability_skips"] == "1"
    assert health["fuyao_negative_capability_expired"] == "1"
    assert health["fuyao_negative_capability_reprobes"] == "1"
    assert health["fuyao_negative_capability_success_clears"] == "1"


def test_fuyao_soft_negative_capability_cache_is_endpoint_scoped():
    session = FakeSession(
        [
            {"code": 3004, "message": "Unsupported asset", "request_id": "snapshot"},
            _success_payload(),
        ]
    )
    client = FuyaoClient(
        "secret-key",
        base_url="https://endpoint-scope.example",
        session=session,
        max_attempts=1,
    )

    with pytest.raises(FuyaoProviderError):
        client.get_fund_snapshot_data("510300.SH")
    client.get_fund_history_data("510300.SH", date(2026, 8, 1), date(2026, 8, 2))
    with pytest.raises(FuyaoProviderError, match="soft negative capability cache skipped"):
        client.get_fund_snapshot_data("510300.SH")

    assert [call[0].removeprefix(client.base_url) for call in session.calls] == [
        "/api/fund/market/snapshot",
        "/api/fund/market/historical",
    ]


def test_fuyao_soft_negative_capability_cache_ignores_transient_errors():
    rate_session = FakeSession(
        [
            {"code": 4001, "message": "rate limited", "request_id": "rate"},
            _success_payload(),
        ]
    )
    rate_client = FuyaoClient(
        "secret-key",
        base_url="https://transient-rate.example",
        session=rate_session,
        max_attempts=1,
        failure_registry=ProviderFailureStateRegistry(
            base_backoff_seconds=0,
            max_backoff_seconds=0,
            jitter_ratio=0,
        ),
    )
    with pytest.raises(FuyaoProviderError):
        rate_client.get_stock_snapshot_data(["600519.SH"])
    rate_client.get_stock_snapshot_data(["600519.SH"])
    assert len(rate_session.calls) == 2

    class TimeoutThenSuccessSession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if len(self.calls) == 1:
                raise requests.Timeout("timed out")
            return FakeResponse(_success_payload())

    timeout_session = TimeoutThenSuccessSession([])
    timeout_client = FuyaoClient(
        "secret-key",
        base_url="https://transient-timeout.example",
        session=timeout_session,
        max_attempts=1,
    )
    with pytest.raises(FuyaoProviderError):
        timeout_client.get_stock_snapshot_data(["600519.SH"])
    timeout_client.get_stock_snapshot_data(["600519.SH"])
    assert len(timeout_session.calls) == 2


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
