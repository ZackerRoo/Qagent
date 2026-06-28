from qagent.market.tradable import TradableInstrument, TradableInstrumentCatalog
from qagent.app import create_app
from fastapi.testclient import TestClient


def _catalog():
    items = [
        TradableInstrument(
            instrument_id="CN:000001",
            symbol="000001",
            name="平安银行",
            label="平安银行 000001.SZ",
            asset_type="stock",
            exchange="SZ",
            source="test",
        ),
        TradableInstrument(
            instrument_id="CN:688059",
            symbol="688059",
            name="华锐精密",
            label="华锐精密 688059.SH",
            asset_type="stock",
            exchange="SH",
            source="test",
        ),
    ]
    return TradableInstrumentCatalog(items=items, data_health={"test": "ok"})


def test_instruments_labels_returns_requested_symbols(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-instruments-requested.db'}")
    monkeypatch.setattr(
        "qagent.market.tradable.load_cn_tradable_instruments",
        lambda include_full_etfs=True, use_cache=False: _catalog(),
    )
    client = TestClient(create_app())

    response = client.get("/api/instruments/labels?symbols=CN:000001,CN:688059")

    assert response.status_code == 200
    body = response.json()
    assert body["labels"]["CN:000001"] == "平安银行 000001.SZ"
    assert body["labels"]["CN:688059"] == "华锐精密 688059.SH"
    assert body["data_health"]["requested"] == "2"


def test_instruments_labels_falls_back_to_tradable_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'api-instruments-full.db'}")
    monkeypatch.setattr(
        "qagent.market.tradable.load_cn_tradable_instruments",
        lambda include_full_etfs=True, use_cache=False: _catalog(),
    )
    client = TestClient(create_app())

    response = client.get("/api/instruments/labels")

    assert response.status_code == 200
    body = response.json()
    assert body["labels"]["CN:000001"] == "平安银行 000001.SZ"
    assert body["labels"]["CN:688059"] == "华锐精密 688059.SH"
    assert body["data_health"]["requested"] == "2"
