import json
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from qagent.api import routes
from qagent.app import create_app
from qagent.backtesting.experiment import build_walk_forward_experiment_manifest
from qagent.backtesting.ranking_v4_forward_evidence import (
    build_attempt_inventory_snapshot,
    build_prospective_definition,
)
from qagent.security.ranking_v4_attestation import RankingV4EvidenceAttestor
from qagent.storage.ranking_v4_forward_evidence import RankingV4EvidenceRepository
from qagent.storage.tables import (
    HistoricalDataRevisionRow,
    PaperTradeEventRow,
    PaperTradeRow,
    RankingV3ForwardCandidateRow,
    RankingV3ForwardGateEvidenceRow,
    RankingV3ForwardReleaseProofRow,
    RankingV3ForwardSessionRow,
    RankingV4EvidenceDefinitionRow,
    RankingV4EvidenceInventoryRow,
    RankingV4EvidenceProofRow,
    RankingV4EvidenceReturnRow,
    RankingV4ProspectiveExecutionSummaryRow,
    RankingV4ProspectiveReleasePolicyRow,
    RankingV4ProspectiveReleaseProofRow,
    ScanResultCacheRow,
    WalkForwardJobRow,
    WalkForwardRunRow,
)
from qagent.research import validation_center


def test_validation_center_marks_old_tracks_and_stale_walk_forward_without_writes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'validation-center.db'}",
    )
    repo = routes._repo()
    now = datetime.now(timezone.utc)
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=8))
        session.add(
            WalkForwardRunRow(
                run_id="walk-forward-old-revision",
                provider="free",
                status="succeeded",
                start_date=date(2021, 11, 1),
                end_date=date(2025, 12, 31),
                dataset_revision=7,
                rebalance_step_sessions=10,
                lookback_days=400,
                snapshot_count=102,
                top_5_trade_count=10,
                top_10_trade_count=20,
                top_5_return_pct=Decimal("1"),
                top_10_return_pct=Decimal("2"),
                top_5_oos_trades=10,
                top_10_oos_trades=20,
                top_5_oos_gate="insufficient",
                top_10_oos_gate="insufficient",
                reproducibility_digest="old-run",
                payload_json=json.dumps({"experiment_manifest": {}}),
                data_health="{}",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    attestor = RankingV4EvidenceAttestor(b"v" * 32)
    evidence_repo = RankingV4EvidenceRepository(repo.session_factory, attestor=attestor)
    frozen_at = datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc)
    for suffix in ("a", "b"):
        definition = evidence_repo.freeze_definition(
            build_prospective_definition(
                epoch_id=f"epoch-{suffix}",
                code_revision="0" * 40,
                dataset_revision=7,
                evidence_start_date=date(2026, 7, 31),
                frozen_at=frozen_at,
                attestor=attestor,
            )
        )
        evidence_repo.append_inventory(
            build_attempt_inventory_snapshot(
                definition=definition,
                sequence=1,
                as_of_date=frozen_at.date(),
                pre_epoch_unverifiable_attempt_ids=(),
                prospective_attempts={
                    definition.identity.epoch_id: definition.definition_digest,
                },
                previous_inventory_digest=None,
                recorded_at=frozen_at,
                attestor=attestor,
            )
        )
        evidence_repo.create_proof(
            definition.identity.epoch_id,
            generated_at=datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc),
        )

    repo.save_scan_result_cache(
        cache_key="full_market_batch:free:true",
        provider="free",
        mode="full_market_batch",
        symbols=["CN:000001"],
        payload={
            "factor_shadow": {
                "run": {
                    "dataset_revision": 8,
                    "signal_date": "2026-08-28",
                    "scored_instruments": 5000,
                },
                "data_health": {"factor_shadow_status": "recorded"},
            },
            "paper_calibration_shadow": {
                "model_ready": False,
                "minimum_training_samples": 40,
                "benchmark_matched_trade_count": 12,
                "decision_date": "2026-08-28",
                "reason": "training_samples_below_minimum:matched=12,required=40",
            },
        },
    )
    before = _protected_counts(repo)

    response = TestClient(create_app()).get("/api/validation-center?provider=free")

    assert response.status_code == 200
    body = response.json()
    tracks = {item["key"]: item for item in body["tracks"]}
    assert body["current_path"] == [
        "current_shadow",
        "paper_calibration",
        "walk_forward",
    ]
    assert tracks["current_shadow"]["status"] == "recorded"
    assert tracks["current_shadow"]["sample_count"] == 5000
    assert tracks["paper_calibration"]["status"] == "collecting"
    assert tracks["paper_calibration"]["sample_count"] == 12
    assert tracks["walk_forward"]["status"] == "stale"
    assert tracks["walk_forward"]["reason"] == "dataset_revision_changed"
    assert tracks["legacy_v3"]["status"] == "inactive"
    assert tracks["legacy_v3"]["active_path"] is False
    assert tracks["preregistered_v4"]["status"] == "collecting"
    assert tracks["preregistered_v4"]["counts"] == {
        "definitions": 2,
        "inventories": 2,
        "returns": 0,
        "evidence_proofs": 2,
        "release_policies": 0,
        "execution_summaries": 0,
        "release_proofs": 0,
    }
    assert body["manual_rerun"]["automatic"] is False
    assert body["manual_rerun"]["recommended"] is True
    assert body["manual_rerun"]["path"] == "/api/walk-forward/jobs"
    assert body["side_effects"] == {
        "ranking": "none",
        "selection": "none",
        "allocation": "none",
        "orders": "none",
        "paper_trading": "none",
    }
    assert _protected_counts(repo) == before


def test_validation_center_has_stable_missing_evidence_state(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'validation-center-empty.db'}",
    )
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=3))
        session.commit()

    response = TestClient(create_app()).get("/api/validation-center?provider=free")

    assert response.status_code == 200
    tracks = {item["key"]: item for item in response.json()["tracks"]}
    assert tracks["current_shadow"]["freshness"] == "missing"
    assert tracks["paper_calibration"]["status"] == "unavailable"
    assert tracks["walk_forward"]["status"] == "inactive"
    assert tracks["legacy_v3"]["status"] == "inactive"
    assert tracks["preregistered_v4"]["status"] == "inactive"


def test_validation_center_marks_current_data_stale_when_model_definition_changes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'validation-center-model-change.db'}",
    )
    repo = routes._repo()
    manifest = build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=5,
        start_date=date(2024, 1, 2),
        end_date=date(2025, 1, 2),
        rebalance_step_sessions=10,
        lookback_days=400,
    )
    now = datetime.now(timezone.utc)
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=5))
        session.add(
            WalkForwardRunRow(
                run_id="walk-forward-old-model",
                provider="free",
                status="succeeded",
                start_date=date(2024, 1, 2),
                end_date=date(2025, 1, 2),
                dataset_revision=5,
                rebalance_step_sessions=10,
                lookback_days=400,
                snapshot_count=25,
                top_5_trade_count=5,
                top_10_trade_count=10,
                top_5_return_pct=Decimal("1"),
                top_10_return_pct=Decimal("2"),
                top_5_oos_trades=5,
                top_10_oos_trades=10,
                top_5_oos_gate="insufficient",
                top_10_oos_gate="insufficient",
                reproducibility_digest="old-model",
                payload_json=json.dumps({"experiment_manifest": manifest.model_dump(mode="json")}),
                data_health="{}",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    monkeypatch.setattr(
        validation_center,
        "walk_forward_selection_manifests_semantically_compatible",
        lambda _stored, _current: False,
    )

    response = TestClient(create_app()).get("/api/validation-center?provider=free")

    track = next(item for item in response.json()["tracks"] if item["key"] == "walk_forward")
    assert track["freshness"] == "stale"
    assert track["reason"] == "model_or_selection_revision_changed"
    assert response.json()["manual_rerun"]["automatic"] is False


def test_validation_center_uses_report_as_of_age_without_weekend_false_positive(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'validation-center-report-age.db'}",
    )
    repo = routes._repo()
    with repo.session_factory() as session:
        session.add(HistoricalDataRevisionRow(provider_mode="free", revision=11))
        session.commit()

    def save_report(as_of: str) -> None:
        repo.save_scan_result_cache(
            cache_key="full_market_batch:free:true",
            provider="free",
            mode="full_market_batch",
            symbols=["CN:000001"],
            payload={
                "factor_shadow": {
                    "run": {
                        "dataset_revision": 11,
                        "signal_date": as_of,
                        "scored_instruments": 100,
                    },
                    "data_health": {"factor_shadow_status": "recorded"},
                },
                "paper_calibration_shadow": {
                    "model_ready": False,
                    "minimum_training_samples": 40,
                    "benchmark_matched_trade_count": 10,
                    "decision_date": as_of,
                    "reason": "training_samples_below_minimum",
                },
            },
        )

    save_report("2026-08-15")
    before_stale_read = _protected_counts(repo)
    stale = validation_center.build_validation_center(
        repo,
        provider="free",
        generated_at=datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc),
    )
    stale_tracks = {item["key"]: item for item in stale["tracks"]}
    assert stale["generated_at"] == "2026-08-24T01:00:00+00:00"
    assert stale_tracks["current_shadow"]["freshness"] == "stale"
    assert stale_tracks["current_shadow"]["reason"] == "stale_as_of"
    assert stale_tracks["paper_calibration"]["status"] == "stale"
    assert stale_tracks["paper_calibration"]["reason"] == "stale_as_of"
    assert _protected_counts(repo) == before_stale_read

    # Three calendar days can include a weekend and remains safely within the
    # seven-day threshold.
    save_report("2026-08-21")
    before_fresh_read = _protected_counts(repo)
    fresh = validation_center.build_validation_center(
        repo,
        provider="free",
        generated_at=datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc),
    )
    fresh_tracks = {item["key"]: item for item in fresh["tracks"]}
    assert fresh_tracks["current_shadow"]["freshness"] == "fresh"
    assert fresh_tracks["paper_calibration"]["freshness"] == "fresh"
    assert fresh_tracks["paper_calibration"]["status"] == "collecting"
    assert _protected_counts(repo) == before_fresh_read

    cache_age_fallback = validation_center._paper_calibration_track(
        {"model_ready": False, "benchmark_matched_trade_count": 10},
        datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc),
    )
    assert cache_age_fallback["freshness"] == "stale"
    assert cache_age_fallback["reason"] == "stale_as_of"


def _protected_counts(repo) -> dict[str, int]:
    models = {
        "paper_trades": PaperTradeRow,
        "paper_events": PaperTradeEventRow,
        "scan_cache": ScanResultCacheRow,
        "walk_forward_jobs": WalkForwardJobRow,
        "walk_forward_runs": WalkForwardRunRow,
        "v3_sessions": RankingV3ForwardSessionRow,
        "v3_candidates": RankingV3ForwardCandidateRow,
        "v3_evidence": RankingV3ForwardGateEvidenceRow,
        "v3_proofs": RankingV3ForwardReleaseProofRow,
        "v4_definitions": RankingV4EvidenceDefinitionRow,
        "v4_inventories": RankingV4EvidenceInventoryRow,
        "v4_evidence_proofs": RankingV4EvidenceProofRow,
        "v4_returns": RankingV4EvidenceReturnRow,
        "v4_policies": RankingV4ProspectiveReleasePolicyRow,
        "v4_execution": RankingV4ProspectiveExecutionSummaryRow,
        "v4_release": RankingV4ProspectiveReleaseProofRow,
    }
    with repo.session_factory() as session:
        return {
            key: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for key, model in models.items()
        }
