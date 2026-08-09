from fastapi.testclient import TestClient

from qagent.app import create_app


def test_storage_checkpoint_maintenance_defaults_to_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'storage-maintenance-api.db'}",
    )
    client = TestClient(create_app())

    preview = client.get("/api/storage/full-market-checkpoints")
    applied_default = client.post("/api/storage/full-market-checkpoints/maintenance")
    invalid = client.get("/api/storage/full-market-checkpoints?retention_days=0")

    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert preview.json()["protected_evidence_domains"]
    assert applied_default.status_code == 200
    assert applied_default.json()["dry_run"] is True
    assert invalid.status_code == 400


def test_paper_execution_audit_api_reports_building_sample(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'execution-audit-api.db'}",
    )
    client = TestClient(create_app())

    response = client.get("/api/paper-trades/execution-audit?provider=free")

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "building_sample"
    assert body["total_trades"] == 0
    assert {check["key"] for check in body["checks"]} >= {
        "immutable_execution_facts",
        "t_plus_one",
        "tradability_guards",
        "cost_and_slippage",
    }
