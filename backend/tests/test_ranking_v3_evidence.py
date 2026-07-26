from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from qagent.backtesting.experiment import build_walk_forward_experiment_manifest
from qagent.backtesting.ranking_v3_evidence import (
    RankingV3RepositoryEvidenceAuthority,
    ranking_v3_data_revision,
    ranking_v3_historical_gate_results,
    ranking_v3_historical_source_digest,
    ranking_v3_pbo_source_digest,
)
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardIdentity,
    RankingV3HistoricalGatesInput,
    RankingV3PBOInput,
)
from qagent.backtesting.ranking_v3_protocol import build_ranking_v3_protocol
from qagent.backtesting.ranking_v3_pbo import (
    RankingV3DatedModelReturn,
    evaluate_ranking_v3_cscv_pbo,
)


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


class _Repository:
    def __init__(self, run):
        self.run = run

    def get_walk_forward_run(self, run_id):
        return self.run if run_id == self.run.run_id else None


def _pbo_evidence() -> dict[str, object]:
    rebalance_dates = [date(2026, 1, 5) + timedelta(days=offset) for offset in range(12)]
    matrix = {
        "baseline": [
            RankingV3DatedModelReturn(day, value)
            for day, value in zip(
                rebalance_dates,
                (0.01, -0.01, 0.02, 0.00, 0.01, -0.02, 0.01, 0.00, 0.02, -0.01, 0.01, 0.00),
                strict=True,
            )
        ],
        "quality": [
            RankingV3DatedModelReturn(day, value)
            for day, value in zip(
                rebalance_dates,
                (0.02, 0.01, 0.01, -0.01, 0.02, 0.00, 0.01, 0.01, 0.00, 0.02, -0.01, 0.01),
                strict=True,
            )
        ],
        "trend": [
            RankingV3DatedModelReturn(day, value)
            for day, value in zip(
                rebalance_dates,
                (-0.01, 0.02, 0.00, 0.02, -0.01, 0.01, 0.02, -0.01, 0.01, 0.00, 0.02, -0.01),
                strict=True,
            )
        ],
    }
    evidence = evaluate_ranking_v3_cscv_pbo(
        matrix,
        block_count=4,
        purge_rebalance_cohorts=2,
    )
    assert evidence["rejection_reason"] is None
    evidence["model_return_matrix"] = {
        model_id: [
            {
                "rebalance_date": observation.rebalance_date.isoformat(),
                "net_return": observation.net_return,
            }
            for observation in observations
        ]
        for model_id, observations in matrix.items()
    }
    return evidence


def _run():
    protocol = build_ranking_v3_protocol()
    pbo_evidence = _pbo_evidence()
    manifest = build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=42,
        start_date=date(2024, 8, 5),
        end_date=date(2025, 12, 31),
        rebalance_step_sessions=10,
        lookback_days=220,
    )
    return SimpleNamespace(
        run_id="strict-v6",
        provider="free",
        status="succeeded",
        dataset_revision=42,
        reproducibility_digest="d" * 64,
        updated_at=NOW,
        payload={
            "experiment_manifest": manifest.model_dump(mode="json"),
            "ranking_v3": {
                "model_version": protocol.model_version,
                "protocol": protocol.model_dump(mode="json"),
                "historical_validation": {
                    "statistical_gate_status": "pass",
                },
                "criteria": [
                    {"key": "historical_statistical_evidence", "status": "pass"},
                    {"key": "positive_audit_return", "status": "pass"},
                    {"key": "pbo", "status": "insufficient"},
                    {"key": "prospective_shadow", "status": "insufficient"},
                ],
                "pbo_evidence": pbo_evidence,
            }
        },
    )


def test_repository_authority_recomputes_historical_and_pbo_evidence():
    run = _run()
    pbo_evidence = run.payload["ranking_v3"]["pbo_evidence"]
    protocol = build_ranking_v3_protocol()
    identity = RankingV3ForwardIdentity.from_protocol(protocol)
    authority = RankingV3RepositoryEvidenceAuthority(_Repository(run))
    historical = RankingV3HistoricalGatesInput(
        validation_run_id=run.run_id,
        data_revision=ranking_v3_data_revision(run),
        gate_results=ranking_v3_historical_gate_results(run),
        source_proof_digest=ranking_v3_historical_source_digest(run),
        source_generated_at=NOW,
    )
    pbo = RankingV3PBOInput(
        validation_run_id=run.run_id,
        data_revision=ranking_v3_data_revision(run),
        probability=Decimal(str(pbo_evidence["probability"])),
        matrix_digest=str(pbo_evidence["matrix_digest"]),
        fold_count=int(pbo_evidence["fold_count"]),
        method=str(pbo_evidence["method"]),
        source_proof_digest=ranking_v3_pbo_source_digest(run),
        source_generated_at=NOW,
    )

    assert authority.verify_historical_gates(identity, historical) is True
    assert authority.verify_pbo(identity, pbo) is True
    assert (
        authority.verify_historical_gates(
            identity,
            historical.model_copy(update={"source_proof_digest": "f" * 64}),
        )
        is False
    )
    assert (
        authority.verify_pbo(
            identity,
            pbo.model_copy(update={"matrix_digest": "e" * 64}),
        )
        is False
    )


def test_repository_authority_invalidates_mutated_authoritative_run():
    run = _run()
    protocol = build_ranking_v3_protocol()
    identity = RankingV3ForwardIdentity.from_protocol(protocol)
    authority = RankingV3RepositoryEvidenceAuthority(_Repository(run))
    evidence = RankingV3HistoricalGatesInput(
        validation_run_id=run.run_id,
        data_revision=ranking_v3_data_revision(run),
        gate_results=ranking_v3_historical_gate_results(run),
        source_proof_digest=ranking_v3_historical_source_digest(run),
        source_generated_at=NOW,
    )

    run.payload["ranking_v3"]["criteria"][0]["status"] = "fail"

    assert authority.verify_historical_gates(identity, evidence) is False


def test_data_revision_binds_research_execution_and_strategy_digests():
    run = _run()
    original = ranking_v3_data_revision(run)

    run.payload["experiment_manifest"]["research_source_digest"] = "f" * 64

    assert ranking_v3_data_revision(run) != original


def test_repository_authority_rejects_a_tampered_experiment_manifest():
    run = _run()
    protocol = build_ranking_v3_protocol()
    identity = RankingV3ForwardIdentity.from_protocol(protocol)
    evidence = RankingV3HistoricalGatesInput(
        validation_run_id=run.run_id,
        data_revision=ranking_v3_data_revision(run),
        gate_results=ranking_v3_historical_gate_results(run),
        source_proof_digest=ranking_v3_historical_source_digest(run),
        source_generated_at=NOW,
    )
    run.payload["experiment_manifest"]["execution_rules_digest"] = "f" * 64

    assert (
        RankingV3RepositoryEvidenceAuthority(_Repository(run)).verify_historical_gates(
            identity,
            evidence,
        )
        is False
    )
