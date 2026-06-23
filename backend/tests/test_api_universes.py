from fastapi.testclient import TestClient

from qagent.app import create_app


def test_universe_api_lists_builtin_and_custom_universes(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'universes.db'}")
    client = TestClient(create_app())

    create_response = client.post(
        "/api/universes",
        json={
            "universe_id": "custom_growth",
            "name": "Custom Growth",
            "description": "User growth watchlist",
            "market_scope": "US",
            "tags": ["growth"],
            "symbols": ["US:NVDA", "US:MSFT"],
        },
    )
    list_response = client.get("/api/universes")
    detail_response = client.get("/api/universes/custom_growth")

    assert create_response.status_code == 200
    assert create_response.json()["source"] == "custom"
    assert list_response.status_code == 200
    universes = list_response.json()["universes"]
    assert any(item["universe_id"] == "fixture_dev" for item in universes)
    assert any(item["universe_id"] == "custom_growth" for item in universes)
    assert detail_response.status_code == 200
    assert detail_response.json()["universe"]["symbols"] == ["US:NVDA", "US:MSFT"]


def test_universe_api_returns_404_for_missing_universe(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'missing-universe.db'}")
    client = TestClient(create_app())

    response = client.get("/api/universes/nope")

    assert response.status_code == 404
