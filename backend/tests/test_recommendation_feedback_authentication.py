from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from qagent.backtesting.ranking_v3_forward import RankingV3ForwardIdentity
from qagent.backtesting.ranking_v3_production import (
    RankingV3ProductionBatch,
    RankingV3ProductionBatchInput,
    RankingV3ProductionIdentity,
    RankingV3ProductionSelectionItem,
    production_batch_fact_digest,
)
from qagent.db import create_session_factory, initialize_database
from qagent.recommendations.feedback import (
    authenticated_ranking_v3_snapshot_sources,
)
from qagent.security.ranking_v3_attestation import RankingV3Attestor
from qagent.storage.ranking_v3_production import RankingV3ProductionRepository
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import OpportunitySnapshotRow, ScanRunRow


SHANGHAI = ZoneInfo("Asia/Shanghai")
SERVER_KEY = b"f" * 32


def test_feedback_authenticates_snapshot_from_signed_persisted_production_batch(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_RANKING_V3_ATTESTATION_KEY",
        f"hex:{SERVER_KEY.hex()}",
    )
    database_url = f"sqlite:///{tmp_path / 'feedback-authentication.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    signal_date = date(2026, 7, 29)
    snapshot_id = "authenticated-feedback-snapshot"
    scan_run_id = "authenticated-feedback-scan"
    scan_started_at = datetime.combine(signal_date, time(10, 0), tzinfo=SHANGHAI)
    scan_completed_at = datetime.combine(signal_date, time(15, 30), tzinfo=SHANGHAI)
    scan_recorded_at = datetime.combine(signal_date, time(15, 31), tzinfo=SHANGHAI)
    recorded_at = datetime.combine(signal_date, time(16, 30), tzinfo=SHANGHAI)

    with session_factory() as session:
        session.add(
            ScanRunRow(
                run_id=scan_run_id,
                provider="free",
                mode="full_market_batch",
                symbols='["CN:600001"]',
                scanned=1,
                cards=1,
                data_health="{}",
                started_at=scan_started_at,
                completed_at=scan_completed_at,
                created_at=scan_recorded_at,
            )
        )
        session.add(
            OpportunitySnapshotRow(
                snapshot_id=snapshot_id,
                run_id=scan_run_id,
                card_id="authenticated-feedback-card",
                instrument_id="CN:600001",
                market="CN",
                status="setup_ready",
                signal_date=signal_date,
                latest_close=Decimal("10"),
                primary_strategy_id="trend_momentum_stage2",
                score=Decimal("0.9"),
                strategy_score=Decimal("0.9"),
                rank_score=Decimal("0.9"),
                trigger_price=Decimal("10"),
                initial_stop=Decimal("9"),
                target_1=Decimal("12"),
                card_json="{}",
                created_at=datetime(2026, 7, 29, 7, 31, tzinfo=timezone.utc),
            )
        )
        session.commit()

    identity = RankingV3ProductionIdentity.create(
        release_proof_digest="a" * 64,
        validation_run_id="feedback-validation-run",
        data_revision="feedback-data-revision",
        protocol_identity=RankingV3ForwardIdentity(
            protocol_id="QAGENT-RANK-V3.2-20260726",
            protocol_digest="b" * 64,
            model_version="point-in-time-net-excess-v3.2",
        ),
    )
    selection = RankingV3ProductionSelectionItem.create(
        candidate_id="feedback-candidate",
        instrument_id="CN:600001",
        source_snapshot_id=snapshot_id,
        strategy_id="trend_momentum_stage2",
        rank=1,
        score=Decimal("0.9"),
        source_rank_score=Decimal("0.9"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        allocation_multiplier=Decimal("1"),
    )
    source = RankingV3ProductionBatchInput.create(
        session_date=signal_date,
        candidate_snapshot_digest="c" * 64,
        selections=(selection,),
        source_scan_run_id=scan_run_id,
        source_scan_started_at=scan_started_at,
        source_scan_completed_at=scan_completed_at,
        source_scan_recorded_at=scan_recorded_at,
        recorded_at=recorded_at,
    )
    attestor = RankingV3Attestor(SERVER_KEY)
    fact_digest = production_batch_fact_digest(identity, source)
    batch = RankingV3ProductionBatch(
        **source.model_dump(mode="python"),
        identity=identity,
        fact_digest=fact_digest,
        attestation=attestor.sign("ranking-v3-production-batch", fact_digest),
        idempotency_key="feedback-authentication",
    )
    RankingV3ProductionRepository(
        session_factory,
        attestor=attestor,
    ).append_batch(batch)

    sources, health = authenticated_ranking_v3_snapshot_sources(
        QagentRepository(session_factory),
        [snapshot_id, "untrusted-snapshot"],
    )

    assert sources == {snapshot_id: "ranking_v3_production"}
    assert health["feedback_production_authentication"] == "verified"
    assert health["feedback_production_authenticated"] == "1"
    assert health["feedback_production_authentication_errors"] == "0"
