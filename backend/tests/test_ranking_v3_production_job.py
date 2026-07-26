from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from qagent.backtesting.experiment import build_walk_forward_experiment_manifest
from qagent.backtesting.ranking_v3 import (
    RankingV3FeatureVector,
    ResolvedRankingV3Observation,
    build_ranking_v3_frozen_scoring_artifact,
)
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardGateEvidence,
    RankingV3ForwardIdentity,
    RankingV3ForwardMetrics,
    RankingV3ForwardReleaseProof,
    RankingV3ForwardValidator,
    RankingV3ReleaseProofValidation,
    stable_digest,
    stable_release_proof_digest,
)
from qagent.backtesting.ranking_v3_production import (
    RankingV3ProductionAuthorizationError,
    RankingV3ProductionConflictError,
)
from qagent.backtesting.ranking_v3_protocol import (
    RankingV3Protocol,
    build_ranking_v3_protocol,
)
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.ranking_v3_production import (
    RankingV3ProductionSnapshotUnavailable,
    run_ranking_v3_production_day,
)
from qagent.market.calendars import trading_day_offset
from qagent.security.ranking_v3_attestation import RankingV3Attestor
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository
from qagent.storage.ranking_v3_production import RankingV3ProductionRepository
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import (
    OpportunitySnapshotRow,
    RankingV3ProductionBatchRow,
    RankingV3ProductionSelectionRow,
    ScanRunRow,
    WalkForwardRunRow,
)


RUN_ID = "ranking-v3-production-job-run"
PROVIDER = "free"
RELEASE_DATE = date(2026, 7, 28)
PRODUCTION_DATE = date(2026, 7, 29)
UPDATED_AT = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
DATA_REVISION = "ranking-v3-production-job-revision"
ATTESTOR = RankingV3Attestor(b"k" * 32)


@dataclass(frozen=True)
class _Harness:
    session_factory: sessionmaker
    repository: QagentRepository
    protocol: RankingV3Protocol


@pytest.fixture(scope="module")
def authoritative_payload() -> dict[str, object]:
    protocol = build_ranking_v3_protocol()
    manifest = build_walk_forward_experiment_manifest(
        provider_mode=PROVIDER,
        dataset_revision=7,
        start_date=date(2025, 1, 2),
        end_date=date(2026, 7, 24),
        rebalance_step_sessions=10,
        lookback_days=365,
    )
    observations = [
        ResolvedRankingV3Observation(
            instrument_id=f"CN:{index:06d}",
            signal_date=date(2025, 1, 2) + timedelta(days=index // 5),
            available_at=date(2025, 1, 3) + timedelta(days=index // 5),
            outcome_status="resolved",
            triggered=True,
            return_pct=1.0,
            benchmark_return_pct=0.2,
            net_excess_return_pct=0.8,
            primary_strategy_id=f"training-{index % 3}",
            factor_signals=["quality"],
            market_regime="balanced",
            asset_type="stock",
            features=_features(),
        )
        for index in range(120)
    ]
    artifact = build_ranking_v3_frozen_scoring_artifact(
        observations,
        cutoff=protocol.prospective_shadow_start,
    )
    return {
        "experiment_manifest": manifest.model_dump(mode="json"),
        "ranking_v3": {
            "status": "forward_validation_pending",
            "model_version": protocol.model_version,
            "protocol": protocol.model_dump(mode="json"),
            "forward_scoring_artifact": artifact.model_dump(mode="json"),
            "forward_scoring_artifact_digest": artifact.stable_digest,
            "historical_validation": {"statistical_gate_status": "pass"},
            "criteria": [],
        },
    }


def _features(value: float = 0.78) -> RankingV3FeatureVector:
    return RankingV3FeatureVector(
        strategy_score=value,
        factor_score=value,
        valuation=value,
        size=value,
        quality=value,
        momentum=value,
        trend_quality=value,
        liquidity=value,
        low_risk=value,
        risk_filter=value,
        reversal=value,
        execution_penalty=0.0,
        data_completeness=1.0,
    )


def _harness(tmp_path, authoritative_payload: dict[str, object]) -> _Harness:
    database_url = f"sqlite:///{tmp_path / 'ranking-v3-production-job.db'}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    protocol = build_ranking_v3_protocol()
    with factory.begin() as session:
        session.add(
            WalkForwardRunRow(
                run_id=RUN_ID,
                provider=PROVIDER,
                status="succeeded",
                start_date=date(2025, 1, 2),
                end_date=date(2026, 7, 24),
                dataset_revision=7,
                rebalance_step_sessions=10,
                lookback_days=365,
                snapshot_count=120,
                top_5_trade_count=80,
                top_10_trade_count=100,
                top_5_return_pct=Decimal("12.5"),
                top_10_return_pct=Decimal("10.5"),
                top_5_oos_trades=40,
                top_10_oos_trades=50,
                top_5_oos_gate="pass",
                top_10_oos_gate="pass",
                reproducibility_digest="production-job-reproducible",
                payload_json=json.dumps(
                    authoritative_payload,
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                data_health="{}",
                created_at=UPDATED_AT,
                updated_at=UPDATED_AT,
            )
        )
    return _Harness(
        session_factory=factory,
        repository=QagentRepository(factory),
        protocol=protocol,
    )


def _ensure_pending_ledger(harness: _Harness) -> RankingV3ForwardRepository:
    forward = RankingV3ForwardRepository(harness.session_factory)
    forward.ensure_ledger(
        RankingV3ForwardIdentity.from_protocol(harness.protocol),
        DATA_REVISION,
    )
    return forward


def _approve_release(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    *,
    release_date: date = RELEASE_DATE,
) -> RankingV3ForwardReleaseProof:
    forward = _ensure_pending_ledger(harness)
    identity = RankingV3ForwardIdentity.from_protocol(harness.protocol)
    recorded_at = datetime.combine(
        release_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    historical = RankingV3ForwardGateEvidence(
        identity=identity,
        evidence_kind="historical_gates",
        evidence_digest=stable_digest(
            {
                "kind": "historical_gates",
                "validation_run_id": RUN_ID,
                "data_revision": DATA_REVISION,
            }
        ),
        data_revision=DATA_REVISION,
        passed=True,
        payload={"validation_run_id": RUN_ID},
        idempotency_key="production-job-historical-gates",
        recorded_at=recorded_at,
    )
    historical = forward.record_evidence(historical)
    ledger = forward.load_snapshot(identity)
    assert ledger is not None
    metrics = RankingV3ForwardMetrics(
        session_count=harness.protocol.thresholds.minimum_forward_shadow_sessions,
        completed_trade_count=10,
        candidate_count=10,
        mature_candidate_count=10,
        valid_outcome_count=10,
        invalid_outcome_count=0,
        pending_mature_outcome_count=0,
        pending_candidate_count=0,
    )
    unsigned = RankingV3ForwardReleaseProof(
        proof_digest="0" * 64,
        identity=identity,
        data_revision=DATA_REVISION,
        generated_at=recorded_at,
        ledger_revision=ledger.ledger.revision,
        ledger_evidence_digest=stable_digest({"ledger": "production-job"}),
        metrics=metrics,
        gates=[],
        historical_gates_evidence_digest=historical.evidence_digest,
        pbo_evidence_digest=historical.evidence_digest,
        portfolio_evidence_digest=historical.evidence_digest,
        attestation=ATTESTOR.sign("ranking-v3-release-proof", "0" * 64),
    )
    proof_digest = stable_release_proof_digest(unsigned)
    proof = unsigned.model_copy(
        update={
            "proof_digest": proof_digest,
            "attestation": ATTESTOR.sign("ranking-v3-release-proof", proof_digest),
        }
    )
    forward.approve(proof, expected_revision=ledger.ledger.revision)

    def accept_release_proof(
        validator,
        proof_digest,
        *,
        expected_data_revision=None,
    ):
        assert validator.identity == identity
        assert proof_digest == proof.proof_digest
        assert expected_data_revision in {None, DATA_REVISION}
        return RankingV3ReleaseProofValidation(
            valid=True,
            reason="authoritative integration-test release",
            proof=proof,
        )

    monkeypatch.setattr(
        RankingV3ForwardValidator,
        "validate_release_proof",
        accept_release_proof,
    )
    return proof


def _card(
    instrument_id: str,
    rank_score: float,
    *,
    strategy: str,
    industry: str,
) -> dict[str, object]:
    exposures = [
        {
            "factor_id": factor_id,
            "label": factor_id,
            "score": rank_score,
            "weight": 0.1,
            "explanation": factor_id,
        }
        for factor_id in (
            "valuation",
            "size",
            "quality",
            "momentum",
            "trend_quality",
            "liquidity",
            "low_risk",
            "risk_filter",
            "reversal",
        )
    ]
    return {
        "card_id": f"card-{instrument_id.replace(':', '-')}",
        "instrument_id": instrument_id,
        "market": "CN",
        "asset_type": "stock",
        "status": "watch",
        "thesis": "production integration test",
        "score": rank_score,
        "entry_plan": {
            "entry_type": "breakout",
            "confirmation": "close",
            "trigger_price": "10",
            "no_chase_above": "11",
        },
        "exit_plan": {
            "invalidation": "stop",
            "trailing_rule": "none",
            "time_stop": "20",
            "initial_stop": "9",
            "target_1": "12",
        },
        "scenario": {
            "downside_pct": -10,
            "target_1_pct": 20,
            "no_chase_pct": 10,
            "summary": "production integration test",
        },
        "primary_strategy_id": strategy,
        "strategy_score": rank_score,
        "rank_score": rank_score,
        "factor_score": rank_score,
        "factor_flags": ["quality"],
        "factor_exposures": exposures,
        "data_quality_audit": {
            "status": "ready",
            "score": 1.0,
            "can_recommend": True,
            "issues": [],
            "summary": "complete production evidence",
        },
        "market_context": {
            "board": "主板",
            "industry": industry,
            "themes": ["AI"],
            "index_memberships": ["沪深300"],
            "summary": "balanced",
        },
    }


def _cards(prefix: str = "base") -> list[dict[str, object]]:
    return [
        _card(
            f"CN:{600001 + index:06d}",
            rank_score,
            strategy=f"{prefix}-strategy-{index}",
            industry=f"{prefix}-industry-{index}",
        )
        for index, rank_score in enumerate((0.92, 0.87, 0.82))
    ]


def _insert_scan(
    harness: _Harness,
    *,
    session_date: date,
    cards: list[dict[str, object]],
    scanned: int | None = None,
    suffix: str,
    created_order: int = 0,
) -> str:
    run_id = f"scan-{session_date.isoformat()}-{suffix}"
    created_at = datetime.combine(
        session_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=1, seconds=created_order)
    scanned_count = harness.protocol.candidate_pool_limit if scanned is None else scanned
    card_symbols = [str(card["instrument_id"]) for card in cards]
    symbols = [
        *card_symbols,
        *[f"CN:{100000 + index:06d}" for index in range(scanned_count - len(card_symbols))],
    ]
    total_batches = max((scanned_count + 199) // 200, 1)
    data_health = {
        "full_market_scan_mode": "full_market_batch",
        "full_market_total_symbols": str(scanned_count),
        "full_market_scanned_symbols": str(scanned_count),
        "full_market_total_batches": str(total_batches),
        "full_market_completed_batches": str(total_batches),
        "full_market_error_count": "0",
        "full_market_batches_complete": "true",
        "full_market_scan_complete": "true",
        "full_market_signal_date": session_date.isoformat(),
    }
    with harness.session_factory.begin() as session:
        session.add(
            ScanRunRow(
                run_id=run_id,
                provider=PROVIDER,
                mode="full_market_batch",
                symbols=json.dumps(symbols, sort_keys=True),
                scanned=scanned_count,
                cards=len(cards),
                data_health=json.dumps(data_health, sort_keys=True),
                started_at=created_at - timedelta(minutes=2),
                completed_at=created_at - timedelta(minutes=1),
                created_at=created_at,
            )
        )
        for card in cards:
            score = Decimal(str(card["rank_score"]))
            session.add(
                OpportunitySnapshotRow(
                    snapshot_id=f"{run_id}:{card['card_id']}",
                    run_id=run_id,
                    card_id=str(card["card_id"]),
                    instrument_id=str(card["instrument_id"]),
                    market="CN",
                    status="watch",
                    signal_date=session_date,
                    latest_close=Decimal("10"),
                    primary_strategy_id=str(card["primary_strategy_id"]),
                    score=score,
                    strategy_score=score,
                    rank_score=score,
                    trigger_price=Decimal("10"),
                    initial_stop=Decimal("9"),
                    target_1=Decimal("12"),
                    card_json=json.dumps(card, ensure_ascii=True, sort_keys=True),
                    created_at=created_at,
                )
            )
    return run_id


def _production_row_counts(harness: _Harness) -> tuple[int, int]:
    with harness.session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(RankingV3ProductionBatchRow)),
            session.scalar(select(func.count()).select_from(RankingV3ProductionSelectionRow)),
        )


def _production_now(session_date: date) -> datetime:
    return datetime.combine(
        session_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=2)


def test_unapproved_release_blocks_production_generation(
    tmp_path,
    authoritative_payload,
):
    harness = _harness(tmp_path, authoritative_payload)
    _ensure_pending_ledger(harness)
    _insert_scan(
        harness,
        session_date=PRODUCTION_DATE,
        cards=_cards(),
        suffix="unapproved",
    )

    with pytest.raises(PermissionError, match="no current approved release"):
        run_ranking_v3_production_day(
            harness.repository,
            session_date=PRODUCTION_DATE,
            now=lambda: _production_now(PRODUCTION_DATE),
        )

    assert _production_row_counts(harness) == (0, 0)


def test_approved_release_freezes_one_complete_scan_and_exact_selection_facts(
    tmp_path,
    authoritative_payload,
    monkeypatch,
):
    harness = _harness(tmp_path, authoritative_payload)
    proof = _approve_release(harness, monkeypatch)
    cards = _cards()
    scan_run_id = _insert_scan(
        harness,
        session_date=PRODUCTION_DATE,
        cards=cards,
        suffix="complete",
    )

    result = run_ranking_v3_production_day(
        harness.repository,
        session_date=PRODUCTION_DATE,
        now=lambda: _production_now(PRODUCTION_DATE),
    )

    assert result.state == "recorded"
    assert result.release_proof_digest == proof.proof_digest
    assert result.source_scan_run_id == scan_run_id
    assert result.selected_count == len(cards)
    assert _production_row_counts(harness) == (1, len(cards))

    expected = {
        f"{scan_run_id}:{card['card_id']}": (
            card["instrument_id"],
            card["primary_strategy_id"],
        )
        for card in cards
    }
    assert {
        item.source_snapshot_id: (item.instrument_id, item.strategy_id)
        for item in result.batch.selections
    } == expected
    assert [item.rank for item in result.batch.selections] == [1, 2, 3]


def test_pre_release_session_is_rejected_without_persisting_a_batch(
    tmp_path,
    authoritative_payload,
    monkeypatch,
):
    harness = _harness(tmp_path, authoritative_payload)
    _approve_release(
        harness,
        monkeypatch,
        release_date=PRODUCTION_DATE,
    )
    pre_release_date = trading_day_offset(PRODUCTION_DATE, -1)
    _insert_scan(
        harness,
        session_date=pre_release_date,
        cards=_cards(),
        suffix="pre-release",
    )

    with pytest.raises(
        RankingV3ProductionAuthorizationError,
        match="predates the authoritative release",
    ):
        run_ranking_v3_production_day(
            harness.repository,
            session_date=pre_release_date,
        )

    assert _production_row_counts(harness) == (0, 0)


def test_production_continues_after_forward_collection_window_ends(
    tmp_path,
    authoritative_payload,
    monkeypatch,
):
    harness = _harness(tmp_path, authoritative_payload)
    _approve_release(harness, monkeypatch)
    late_date = trading_day_offset(
        harness.protocol.prospective_shadow_start,
        harness.protocol.thresholds.maximum_forward_shadow_sessions + 5,
    )
    _insert_scan(
        harness,
        session_date=late_date,
        cards=_cards("late"),
        suffix="after-forward-window",
    )

    result = run_ranking_v3_production_day(
        harness.repository,
        session_date=late_date,
        now=lambda: _production_now(late_date),
    )

    assert result.session_date == late_date
    assert result.selected_count == 3
    assert _production_row_counts(harness) == (1, 3)


def test_complete_scan_with_no_candidates_freezes_an_empty_batch(
    tmp_path,
    authoritative_payload,
    monkeypatch,
):
    harness = _harness(tmp_path, authoritative_payload)
    _approve_release(harness, monkeypatch)
    scan_run_id = _insert_scan(
        harness,
        session_date=PRODUCTION_DATE,
        cards=[],
        suffix="empty",
    )

    result = run_ranking_v3_production_day(
        harness.repository,
        session_date=PRODUCTION_DATE,
        now=lambda: _production_now(PRODUCTION_DATE),
    )

    assert result.source_scan_run_id == scan_run_id
    assert result.selected_count == 0
    assert result.batch.selections == ()
    assert _production_row_counts(harness) == (1, 0)


def test_new_or_changed_same_day_scan_conflicts_with_frozen_batch(
    tmp_path,
    authoritative_payload,
    monkeypatch,
):
    harness = _harness(tmp_path, authoritative_payload)
    _approve_release(harness, monkeypatch)
    _insert_scan(
        harness,
        session_date=PRODUCTION_DATE,
        cards=_cards("first"),
        suffix="first",
        created_order=1,
    )
    first = run_ranking_v3_production_day(
        harness.repository,
        session_date=PRODUCTION_DATE,
        now=lambda: _production_now(PRODUCTION_DATE),
    )
    _insert_scan(
        harness,
        session_date=PRODUCTION_DATE,
        cards=_cards("changed"),
        suffix="changed",
        created_order=2,
    )

    with pytest.raises(
        RankingV3ProductionConflictError,
        match="different facts|different immutable selection batch",
    ):
        run_ranking_v3_production_day(
            harness.repository,
            session_date=PRODUCTION_DATE,
            now=lambda: _production_now(PRODUCTION_DATE),
        )

    production = RankingV3ProductionRepository(harness.session_factory)
    persisted = production.get_batch_for_session(
        first.batch.identity,
        PRODUCTION_DATE,
    )
    assert persisted == first.batch
    assert _production_row_counts(harness) == (1, first.selected_count)


def test_exact_same_day_replay_is_idempotent(
    tmp_path,
    authoritative_payload,
    monkeypatch,
):
    harness = _harness(tmp_path, authoritative_payload)
    _approve_release(harness, monkeypatch)
    _insert_scan(
        harness,
        session_date=PRODUCTION_DATE,
        cards=_cards(),
        suffix="replay",
    )

    first = run_ranking_v3_production_day(
        harness.repository,
        session_date=PRODUCTION_DATE,
        now=lambda: _production_now(PRODUCTION_DATE),
    )
    replayed = run_ranking_v3_production_day(
        harness.repository,
        session_date=PRODUCTION_DATE,
        now=lambda: _production_now(PRODUCTION_DATE),
    )

    assert replayed == first
    assert _production_row_counts(harness) == (1, first.selected_count)


@pytest.mark.parametrize("scan_state", ["missing", "small"])
def test_missing_or_small_scan_waits_without_freezing_production(
    tmp_path,
    authoritative_payload,
    monkeypatch,
    scan_state,
):
    harness = _harness(tmp_path, authoritative_payload)
    _approve_release(harness, monkeypatch)
    if scan_state == "small":
        _insert_scan(
            harness,
            session_date=PRODUCTION_DATE,
            cards=_cards(),
            scanned=harness.protocol.candidate_pool_limit - 1,
            suffix="small",
        )

    with pytest.raises(
        RankingV3ProductionSnapshotUnavailable,
        match="no complete authoritative full-market scan",
    ):
        run_ranking_v3_production_day(
            harness.repository,
            session_date=PRODUCTION_DATE,
            now=lambda: _production_now(PRODUCTION_DATE),
        )

    assert _production_row_counts(harness) == (0, 0)
