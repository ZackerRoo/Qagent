from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.api import routes
from qagent.backtesting.experiment import build_walk_forward_experiment_manifest
from qagent.backtesting.ranking_v3_evidence import (
    RankingV3RepositoryEvidenceAuthority,
    ranking_v3_data_revision,
    ranking_v3_historical_gate_results,
    ranking_v3_historical_source_digest,
    ranking_v3_pbo_source_digest,
)
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardEquityPoint,
    RankingV3ForwardOutcomeInput,
    RankingV3ForwardPortfolioInput,
    RankingV3ForwardSelectionBatchInput,
    RankingV3ForwardSessionInput,
    RankingV3ForwardValidator,
    RankingV3HistoricalGatesInput,
    RankingV3PBOInput,
    RankingV3ShadowCandidateInput,
    encode_forward_session_batch_key,
    forward_candidate_selection_digest,
    forward_candidate_source_digest,
    stable_digest,
)
from qagent.backtesting.ranking_v3_production import (
    RankingV3ProductionBatchInput,
    RankingV3ProductionIdentity,
    RankingV3ProductionReleaseValidation,
    RankingV3ProductionSelectionItem,
    RankingV3ProductionSelectionValidation,
    RankingV3ProductionSelectionService,
)
from qagent.backtesting.ranking_v3_protocol import (
    RANKING_V3_MODEL_VERSION,
    build_ranking_v3_protocol,
)
from qagent.backtesting.ranking_v3_pbo import (
    RankingV3DatedModelReturn,
    evaluate_ranking_v3_cscv_pbo,
)
from qagent.backtesting.ranking_v4_protocol import RANKING_V4_MODEL_VERSION
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.calendars import trading_day_offset
from qagent.paper_trading.admission import evaluate_paper_snapshot_admission
from qagent.paper_trading.engine import seed_paper_trades_from_snapshots
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository
from qagent.storage.ranking_v3_production import RankingV3ProductionRepository
from qagent.storage.repository import QagentRepository
from qagent.storage.tables import OpportunitySnapshotRow, ScanRunRow


def _persist_authoritative_opportunities(
    *,
    provider: str,
    cards: list[tuple[str, str]],
):
    scan = run_daily_scan(["US:TEST"], FixtureMarketDataProvider())
    template = scan.cards[0]
    template_item = scan.items[0]
    authoritative_cards = [
        template.model_copy(
            update={
                "card_id": card_id,
                "instrument_id": instrument_id,
            }
        )
        for card_id, instrument_id in cards
    ]
    authoritative_items = [
        template_item.model_copy(update={"instrument_id": instrument_id})
        for _, instrument_id in cards
    ]
    routes._repo().save_scan_run(
        provider=provider,
        mode=provider,
        symbols=[instrument_id for _, instrument_id in cards],
        result=scan.model_copy(
            update={
                "cards": authoritative_cards,
                "items": authoritative_items,
            }
        ),
    )
    return authoritative_cards


def _opportunity_request(card, *, provider: str) -> dict[str, object]:
    return {
        "card_id": card.card_id,
        "provider": provider,
        "instrument_id": card.instrument_id,
        "strategy_id": card.primary_strategy_id,
        "trigger_price": str(card.entry_plan.trigger_price),
        "initial_stop": str(card.exit_plan.initial_stop),
        "target_1": str(card.exit_plan.target_1),
        "rank_score": card.rank_score,
        "action": "watch_trigger",
        "risk_status": "clear",
    }


def _patch_authoritative_card(
    *,
    provider: str,
    card_id: str,
    updates: dict[str, object],
) -> None:
    repo = routes._repo()
    snapshot = repo.list_latest_opportunity_snapshots_by_card_ids(
        [card_id],
        provider=provider,
    )[0]
    with repo.session_factory() as session:
        row = session.get(OpportunitySnapshotRow, snapshot.snapshot_id)
        payload = json.loads(row.card_json)
        payload.update(updates)
        row.card_json = json.dumps(payload, sort_keys=True)
        session.commit()


def _released_ranking_v3_run(*, provider: str, run_id: str):
    protocol = build_ranking_v3_protocol()
    pbo_evidence = _authoritative_pbo_evidence()
    now = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
    experiment_manifest = build_walk_forward_experiment_manifest(
        provider_mode=provider,
        dataset_revision=42,
        start_date=date(2025, 1, 2),
        end_date=date(2026, 7, 24),
        rebalance_step_sessions=10,
        lookback_days=365,
    )
    return SimpleNamespace(
        run_id=run_id,
        provider=provider,
        status="succeeded",
        dataset_revision=42,
        reproducibility_digest="d" * 64,
        updated_at=now,
        payload={
            "experiment_manifest": experiment_manifest.model_dump(mode="json"),
            "ranking_v3": {
                "model_version": RANKING_V3_MODEL_VERSION,
                "forward_scoring_artifact_digest": "a" * 64,
                "status": "forward_validation_pending",
                "deployment_scope": "shadow_only",
                "official_release_allowed": False,
                "protocol": protocol.model_dump(mode="json"),
                "historical_validation": {
                    "status": "insufficient",
                    "statistical_gate_status": "pass",
                },
                "criteria": [
                    {"key": "historical", "status": "pass"},
                    {"key": "positive_audit_return", "status": "pass"},
                    {"key": "pbo", "status": "insufficient"},
                    {"key": "prospective_shadow", "status": "insufficient"},
                ],
                "pbo_evidence": pbo_evidence,
            },
        },
    )


def _authoritative_pbo_evidence() -> dict[str, object]:
    start = date(2025, 1, 2)
    dates = [start + timedelta(days=index) for index in range(24)]
    values = {
        "ranking_v3_full": [0.025 + (index % 3) * 0.001 for index in range(24)],
        "static_balanced": [0.012 + (index % 2) * 0.001 for index in range(24)],
        "constraint_matched_baseline": [0.004 - (index % 4) * 0.001 for index in range(24)],
    }
    matrix = {
        model_id: [
            RankingV3DatedModelReturn(rebalance_date=rebalance_date, net_return=returns[index])
            for index, rebalance_date in enumerate(dates)
        ]
        for model_id, returns in values.items()
    }
    result = evaluate_ranking_v3_cscv_pbo(
        matrix,
        block_count=6,
        purge_rebalance_cohorts=2,
    )
    assert result["rejection_reason"] is None
    return {
        **result,
        "model_return_matrix": {
            model_id: [
                {
                    "rebalance_date": item.rebalance_date.isoformat(),
                    "net_return": item.net_return,
                }
                for item in rows
            ]
            for model_id, rows in matrix.items()
        },
    }


def _persist_ranking_v3_release_proof(
    repo,
    run,
    *,
    approved_snapshot_id: str,
    approved_instrument_id: str,
):
    protocol = build_ranking_v3_protocol()
    repository = RankingV3ForwardRepository(repo.session_factory)

    class _AuthoritativePortfolio:
        def recompute_portfolio(self, _identity, _protocol, _snapshot, submitted):
            return submitted

    validator = RankingV3ForwardValidator(
        repository,
        protocol,
        evidence_authority=RankingV3RepositoryEvidenceAuthority(repo),
        portfolio_authority=_AuthoritativePortfolio(),
        now=lambda: run.updated_at,
    )
    data_revision = ranking_v3_data_revision(run)
    validator.ensure_ledger(data_revision)
    first_candidate_binding = None
    recorded_candidates = []
    for index in range(20):
        session_date = trading_day_offset(protocol.prospective_shadow_start, index)
        candidates = ()
        if index < 10:
            candidate_id = f"api-proof-candidate-{index}"
            source_snapshot_id = approved_snapshot_id if index == 0 else f"api-proof-source-{index}"
            instrument_id = approved_instrument_id if index == 0 else f"CN:{index + 1:06d}"
            candidate = RankingV3ShadowCandidateInput(
                candidate_id=candidate_id,
                source_snapshot_id=source_snapshot_id,
                session_date=session_date,
                maturity_session_date=trading_day_offset(session_date, 1),
                instrument_id=instrument_id,
                strategy_id="ranking-v3",
                rank=1,
                score=Decimal("0.8"),
                benchmark_id=protocol.benchmark_definition.forward_release_benchmark_id,
                data_revision=data_revision,
                selection_digest="0" * 64,
            )
            candidates = (candidate,)
        candidate_snapshot_digest = stable_digest(
            {
                "session_date": session_date,
                "candidates": [
                    item.model_dump(mode="json", exclude={"selection_digest"})
                    for item in candidates
                ],
            }
        )
        selection_batch_digest = stable_digest(
            {
                "candidate_snapshot_digest": candidate_snapshot_digest,
                "selected": [
                    item.model_dump(mode="json", exclude={"selection_digest"})
                    for item in candidates
                ],
            }
        )
        frozen_candidates = tuple(
            item.model_copy(
                update={
                    "selection_digest": forward_candidate_selection_digest(
                        selection_batch_digest=selection_batch_digest,
                        source_snapshot_id=item.source_snapshot_id,
                        instrument_id=item.instrument_id,
                        strategy_id=item.strategy_id,
                        rank=item.rank,
                        score=item.score,
                    )
                }
            )
            for item in candidates
        )
        validator.freeze_selection_batch(
            RankingV3ForwardSelectionBatchInput.create(
                session_date=session_date,
                benchmark_id=protocol.benchmark_definition.forward_release_benchmark_id,
                data_revision=data_revision,
                candidate_snapshot_digest=candidate_snapshot_digest,
                selection_batch_digest=selection_batch_digest,
                candidates=frozen_candidates,
            ),
            idempotency_key=f"api-proof-selection-batch-{index}",
        )
        validator.record_session(
            RankingV3ForwardSessionInput(
                session_date=session_date,
                benchmark_id=protocol.benchmark_definition.forward_release_benchmark_id,
                benchmark_return_pct=Decimal("0.2"),
                portfolio_equity=Decimal("100") + Decimal(index),
                stress_portfolio_equity=Decimal("100") + Decimal(index) * Decimal("0.8"),
                benchmark_equity=Decimal("100") + Decimal(index) * Decimal("0.2"),
                data_revision=data_revision,
                candidate_snapshot_digest=candidate_snapshot_digest,
                selection_batch_digest=selection_batch_digest,
                selected_candidate_count=len(frozen_candidates),
            ),
            idempotency_key=encode_forward_session_batch_key(
                session_date=session_date,
                candidate_snapshot_digest=candidate_snapshot_digest,
                selection_batch_digest=selection_batch_digest,
                selected_candidate_count=len(frozen_candidates),
            ),
        )
        for frozen_candidate in frozen_candidates:
            validator.record_candidate(
                frozen_candidate,
                idempotency_key=f"api-proof-candidate-{index}",
            )
            recorded_candidates.append(frozen_candidate)
            if index == 0:
                first_candidate_binding = {
                    "candidate_id": frozen_candidate.candidate_id,
                    "source_snapshot_id": frozen_candidate.source_snapshot_id,
                    "selection_digest": frozen_candidate.selection_digest,
                }
    for index, candidate in enumerate(recorded_candidates):
        validator.finalize_candidate(
            candidate.candidate_id,
            RankingV3ForwardOutcomeInput(
                status="completed",
                resolved_on=candidate.maturity_session_date,
                gross_return_pct=Decimal("2"),
                transaction_cost_pct=Decimal("0.1"),
                stress_transaction_cost_pct=Decimal("0.2"),
                benchmark_return_pct=Decimal("0.5"),
                max_drawdown_pct=Decimal("-1"),
                data_revision=data_revision,
            ),
            idempotency_key=f"api-proof-outcome-{index}",
        )
    validator.record_historical_gates(
        RankingV3HistoricalGatesInput(
            validation_run_id=run.run_id,
            data_revision=data_revision,
            gate_results=ranking_v3_historical_gate_results(run),
            source_proof_digest=ranking_v3_historical_source_digest(run),
            source_generated_at=run.updated_at,
        ),
        idempotency_key="api-proof-history",
    )
    pbo_evidence = run.payload["ranking_v3"]["pbo_evidence"]
    validator.record_pbo(
        RankingV3PBOInput(
            validation_run_id=run.run_id,
            data_revision=data_revision,
            probability=Decimal(str(pbo_evidence["probability"])),
            matrix_digest=str(pbo_evidence["matrix_digest"]),
            fold_count=int(pbo_evidence["fold_count"]),
            method=str(pbo_evidence["method"]),
            source_proof_digest=ranking_v3_pbo_source_digest(run),
            source_generated_at=run.updated_at,
        ),
        idempotency_key="api-proof-pbo",
    )
    as_of_session_date = trading_day_offset(protocol.prospective_shadow_start, 19)
    equity_curve = (
        RankingV3ForwardEquityPoint(
            date=as_of_session_date - timedelta(days=2),
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
        RankingV3ForwardEquityPoint(
            date=as_of_session_date - timedelta(days=1),
            equity=Decimal("98000"),
            cash=Decimal("98000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("-2"),
        ),
        RankingV3ForwardEquityPoint(
            date=as_of_session_date,
            equity=Decimal("103000"),
            cash=Decimal("103000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
    )
    stress_equity_curve = (
        RankingV3ForwardEquityPoint(
            date=as_of_session_date - timedelta(days=2),
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
        RankingV3ForwardEquityPoint(
            date=as_of_session_date - timedelta(days=1),
            equity=Decimal("97000"),
            cash=Decimal("97000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("-3"),
        ),
        RankingV3ForwardEquityPoint(
            date=as_of_session_date,
            equity=Decimal("102000"),
            cash=Decimal("102000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
    )
    snapshot = repository.load_snapshot(validator.identity)
    assert snapshot is not None
    portfolio = RankingV3ForwardPortfolioInput(
        validation_run_id=run.run_id,
        data_revision=data_revision,
        as_of_session_date=as_of_session_date,
        benchmark_id=protocol.benchmark_definition.forward_release_benchmark_id,
        provider=run.provider,
        execution_profile="api-test-capital-constrained",
        initial_equity=Decimal("100000"),
        final_equity=Decimal("103000"),
        stress_final_equity=Decimal("102000"),
        benchmark_final_equity=Decimal("101000"),
        net_return_pct=Decimal("3"),
        stress_net_return_pct=Decimal("2"),
        benchmark_return_pct=Decimal("1"),
        benchmark_excess_pct=Decimal("2"),
        stress_benchmark_excess_pct=Decimal("1"),
        maximum_drawdown_pct=Decimal("-2"),
        stress_maximum_drawdown_pct=Decimal("-3"),
        completed_trade_count=10,
        equity_curve=equity_curve,
        stress_equity_curve=stress_equity_curve,
        equity_curve_digest=stable_digest([item.model_dump(mode="json") for item in equity_curve]),
        stress_equity_curve_digest=stable_digest(
            [item.model_dump(mode="json") for item in stress_equity_curve]
        ),
        final_open_positions=0,
        stress_final_open_positions=0,
        source_candidate_digest=forward_candidate_source_digest(snapshot.candidates),
    )
    validator.record_portfolio(
        portfolio,
        idempotency_key="api-proof-portfolio",
    )
    result = validator.evaluate()
    assert result.release_proof is not None
    assert first_candidate_binding is not None
    return result.release_proof, first_candidate_binding


def test_paper_trade_from_opportunity_creates_once_and_rejects_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-from-card.db'}")
    client = TestClient(create_app())
    first_card, duplicate_card, blocked_card = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[
            ("card_test_0001", "US:TEST"),
            ("card_test_0002", "US:TEST"),
            ("card_blocked", "US:TEST"),
        ],
    )
    _patch_authoritative_card(
        provider="fixture",
        card_id=blocked_card.card_id,
        updates={
            "decision": {
                **blocked_card.decision.model_dump(mode="json"),
                "risk_status": "blocked",
            }
        },
    )
    opportunity = _opportunity_request(first_card, provider="fixture")

    created = client.post("/api/paper-trades/from-opportunity", json=opportunity)
    duplicate = client.post("/api/paper-trades/from-opportunity", json=opportunity)
    duplicate_instrument = client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(duplicate_card, provider="fixture"),
    )
    blocked = client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(blocked_card, provider="fixture"),
    )
    listed = client.get("/api/paper-trades?reporting_scope=legacy")

    assert created.status_code == 200
    assert created.json()["created"] is True
    assert created.json()["trade"]["instrument_id"] == "US:TEST"
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert duplicate.json()["trade"]["trade_id"] == created.json()["trade"]["trade_id"]
    assert duplicate_instrument.status_code == 200
    assert duplicate_instrument.json()["created"] is False
    assert duplicate_instrument.json()["message"] == "already_tracking_instrument"
    assert duplicate_instrument.json()["trade"]["trade_id"] == created.json()["trade"]["trade_id"]
    assert blocked.status_code == 400
    assert listed.json()["summary"]["total"] == 1

    events = client.get(f"/api/paper-trades/{created.json()['trade']['trade_id']}/events")
    assert events.status_code == 200
    assert events.json()["data_health"]["paper_event_ledger"] == "append_only"
    assert events.json()["events"][0]["event_type"] == "created"


def test_paper_trade_from_opportunity_requires_authoritative_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-forged-card.db'}",
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/paper-trades/from-opportunity",
        json={
            "card_id": "forged-card",
            "provider": "fixture",
            "instrument_id": "US:FAKE",
            "strategy_id": "trend_momentum_stage2",
            "trigger_price": "1.00",
            "initial_stop": "0.90",
            "target_1": "1.20",
            "rank_score": 1.0,
            "action": "watch_trigger",
            "risk_status": "clear",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "authoritative opportunity snapshot not found"
    assert client.get("/api/paper-trades").json()["summary"]["total"] == 0


def test_paper_trade_from_opportunity_rejects_client_price_and_strategy_spoofing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-spoofed-fields.db'}",
    )
    client = TestClient(create_app())
    (card,) = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[("authoritative-card", "US:TEST")],
    )
    request = _opportunity_request(card, provider="fixture")

    price_spoof = client.post(
        "/api/paper-trades/from-opportunity",
        json={**request, "trigger_price": "0.01"},
    )
    strategy_spoof = client.post(
        "/api/paper-trades/from-opportunity",
        json={**request, "strategy_id": "forged_strategy"},
    )
    instrument_spoof = client.post(
        "/api/paper-trades/from-opportunity",
        json={**request, "instrument_id": "US:FAKE"},
    )

    assert price_spoof.status_code == 400
    assert "trigger_price" in price_spoof.json()["detail"]
    assert strategy_spoof.status_code == 400
    assert "strategy_id" in strategy_spoof.json()["detail"]
    assert instrument_spoof.status_code == 400
    assert "instrument_id" in instrument_spoof.json()["detail"]
    assert client.get("/api/paper-trades").json()["summary"]["total"] == 0


def test_unreleased_ranking_v3_is_fail_closed_for_manual_and_seed(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-v3-shadow.db'}",
    )
    client = TestClient(create_app())
    (card,) = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[("ranking-v3-shadow", "US:TEST")],
    )
    _patch_authoritative_card(
        provider="fixture",
        card_id=card.card_id,
        updates={
            "ranking_v3": {
                "selection_source": "ranking_v3",
                "model_version": RANKING_V3_MODEL_VERSION,
                "deployment_scope": "shadow_only",
                "official_release_allowed": False,
            }
        },
    )

    manual = client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(card, provider="fixture"),
    )
    seeded = client.post("/api/paper-trades/seed?provider=fixture&limit=5")

    assert manual.status_code == 400
    assert "approved release" in manual.json()["detail"]
    assert seeded.status_code == 200
    assert seeded.json()["created"] == 0
    assert client.get("/api/paper-trades").json()["summary"]["total"] == 0


def test_seed_api_passes_authoritative_repository_to_low_level_admission(monkeypatch):
    authoritative_repo = object()
    paper_repo = SimpleNamespace(
        list_trades=lambda **_kwargs: [],
        get_account_settings=lambda: SimpleNamespace(max_positions=10),
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(routes, "_repo", lambda: authoritative_repo)
    monkeypatch.setattr(routes, "_paper_repo", lambda: paper_repo)
    monkeypatch.setattr(
        routes,
        "_paper_seed_snapshots_from_recommendations",
        lambda repo, **_kwargs: ([], {}) if repo is authoritative_repo else None,
    )

    def fake_seed(repo, snapshots, **kwargs):
        captured["paper_repo"] = repo
        captured["snapshots"] = snapshots
        captured.update(kwargs)
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {"scanned": 0, "created": 0, "skipped": 0}
        )

    monkeypatch.setattr(routes, "seed_paper_trades_from_snapshots", fake_seed)

    result = routes.seed_paper_trades(provider="fixture", limit=5)

    assert result["created"] == 0
    assert captured["paper_repo"] is paper_repo
    assert captured["admission_repo"] is authoritative_repo


def test_cached_unreleased_ranking_v3_does_not_fall_back_to_legacy_seed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-v3-cache-fallback.db'}",
    )
    client = TestClient(create_app())
    legacy_card, v3_card = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[
            ("legacy-seed-card", "US:LEGACY"),
            ("ranking-v3-cache-card", "US:V3"),
        ],
    )
    _patch_authoritative_card(
        provider="fixture",
        card_id=v3_card.card_id,
        updates={
            "ranking_v3": {
                "selection_source": "ranking_v3",
                "model_version": RANKING_V3_MODEL_VERSION,
                "deployment_scope": "shadow_only",
                "official_release_allowed": False,
            }
        },
    )
    repo = routes._repo()
    authoritative_v3 = repo.list_latest_opportunity_snapshots_by_card_ids(
        [v3_card.card_id],
        provider="fixture",
    )[0]
    repo.save_scan_result_cache(
        cache_key=routes.full_market_batch_cache_key("fixture", True),
        provider="fixture",
        mode="full_market_batch",
        symbols=[legacy_card.instrument_id, v3_card.instrument_id],
        payload={
            "cards": [authoritative_v3.card],
            "benchmark_trend": {
                "state": "risk_on",
                "entry_allowed": True,
                "reason": "test",
            },
        },
    )

    seeded = client.post("/api/paper-trades/seed?provider=fixture&limit=5")

    assert seeded.status_code == 200
    assert seeded.json()["created"] == 0
    assert client.get("/api/paper-trades").json()["summary"]["total"] == 0


def test_risk_off_candidates_allow_batch_reduced_size_research_entries(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-risk-off-candidates.db'}",
    )
    client = TestClient(create_app())
    cards = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[
            ("risk-off-visible-card-1", "US:RISK-OFF-1"),
            ("risk-off-visible-card-2", "US:RISK-OFF-2"),
        ],
    )
    for card in cards:
        _patch_authoritative_card(
            provider="fixture",
            card_id=card.card_id,
            updates={"market_context": {"industry": "宽基ETF"}},
        )
    repo = routes._repo()
    snapshots = repo.list_latest_opportunity_snapshots_by_card_ids(
        [card.card_id for card in cards],
        provider="fixture",
    )
    repo.save_scan_result_cache(
        cache_key=routes.full_market_batch_cache_key("fixture", True),
        provider="fixture",
        mode="full_market_batch",
        symbols=[card.instrument_id for card in cards],
        payload={
            "cards": [snapshot.card for snapshot in snapshots],
            "benchmark_trend": {
                "state": "risk_off",
                "entry_allowed": False,
                "reason": "test market gate",
            },
        },
    )

    pool = client.get(
        "/api/paper-trades/candidate-pool?provider=fixture&include_etfs=true&limit=10"
    )
    seeded = client.post("/api/paper-trades/seed?provider=fixture&limit=5")
    report = client.get(
        "/api/paper-trades/daily-report?provider=fixture&reporting_scope=legacy"
    )
    post_seed_pool = client.get(
        "/api/paper-trades/candidate-pool?provider=fixture&include_etfs=true&limit=10"
    )

    assert pool.status_code == 200
    assert {item["instrument_id"] for item in pool.json()["items"]} == {
        "US:RISK-OFF-1",
        "US:RISK-OFF-2",
    }
    assert {item["status"] for item in pool.json()["items"]} == {"ready_to_add"}
    assert pool.json()["summary"]["market_blocked_count"] == 0
    assert pool.json()["summary"]["risk_action"] == "throttle_new_entries"
    assert pool.json()["data_health"]["paper_market_entry_gate"] == "throttled"
    assert seeded.status_code == 200
    assert seeded.json()["created"] == 2
    assert report.status_code == 200
    assert report.json()["risk_gate"]["action"] == "throttle_new_entries"
    assert report.json()["risk_gate"]["max_new_entries"] == 3
    assert report.json()["risk_gate"]["position_size_multiplier"] == 0.35
    assert post_seed_pool.status_code == 200
    assert {item["status"] for item in post_seed_pool.json()["items"]} == {
        "active_in_paper"
    }
    assert (
        post_seed_pool.json()["data_health"]["paper_market_probe_remaining_today"]
        == "3"
    )
    trades = client.get(
        "/api/paper-trades?provider=fixture&reporting_scope=legacy"
    ).json()["trades"]
    assert len(trades) == 2
    assert all(trade["allocation_multiplier"] == "0.3500" for trade in trades)
    assert all("防守行情研究仓位" in trade["notes"] for trade in trades)


def test_candidate_pool_reports_industry_capacity_block(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-industry-capacity.db'}",
    )
    client = TestClient(create_app())
    cards = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[
            ("bank-card-1", "US:BANK-1"),
            ("bank-card-2", "US:BANK-2"),
            ("bank-card-3", "US:BANK-3"),
        ],
    )
    for card in cards:
        _patch_authoritative_card(
            provider="fixture",
            card_id=card.card_id,
            updates={"market_context": {"industry": "银行"}},
        )
    first = client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(cards[0], provider="fixture"),
    )
    second = client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(cards[1], provider="fixture"),
    )
    assert first.status_code == 200
    assert second.status_code == 200

    repo = routes._repo()
    snapshots = repo.list_latest_opportunity_snapshots_by_card_ids(
        [card.card_id for card in cards],
        provider="fixture",
    )
    repo.save_scan_result_cache(
        cache_key=routes.full_market_batch_cache_key("fixture", True),
        provider="fixture",
        mode="full_market_batch",
        symbols=[card.instrument_id for card in cards],
        payload={
            "cards": [snapshot.card for snapshot in snapshots],
            "benchmark_trend": {
                "state": "risk_on",
                "entry_allowed": True,
                "reason": "test",
            },
        },
    )

    pool = client.get(
        "/api/paper-trades/candidate-pool?provider=fixture&include_etfs=true&limit=10"
    )

    assert pool.status_code == 200
    item = next(
        candidate
        for candidate in pool.json()["items"]
        if candidate["instrument_id"] == "US:BANK-3"
    )
    assert item["status"] == "blocked_by_industry"
    assert item["industry"] == "银行"
    assert item["exposure_group"] == "银行"
    assert item["industry_active_count"] == 2
    assert item["industry_capacity_used"] == 2
    assert item["industry_capacity_limit"] == 2
    assert item["industry_blocked"] is True
    assert pool.json()["summary"]["industry_blocked_count"] == 1


def test_ranking_v4_shadow_claim_is_admitted_to_research_paper_lane(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-v4-fail-closed.db'}",
    )
    _persist_authoritative_opportunities(
        provider="fixture",
        cards=[("ranking-v4-shadow-card", "US:V4")],
    )
    _patch_authoritative_card(
        provider="fixture",
        card_id="ranking-v4-shadow-card",
        updates={
            "ranking_v4": {
                "selection_source": "ranking_v4",
                "model_version": RANKING_V4_MODEL_VERSION,
                "deployment_scope": "shadow_only",
                "official_release_allowed": False,
            }
        },
    )
    repo = routes._repo()
    snapshot = repo.list_latest_opportunity_snapshots_by_card_ids(
        ["ranking-v4-shadow-card"],
        provider="fixture",
    )[0]

    decision = evaluate_paper_snapshot_admission(
        repo,
        snapshot,
        provider="fixture",
    )

    assert decision.eligible is True
    assert decision.reason is None
    assert decision.admission_source == "ranking_v4_shadow"
    assert decision.selection_source == "ranking_v4"
    assert decision.model_version == RANKING_V4_MODEL_VERSION
    assert decision.deployment_scope == "shadow_only"
    assert decision.release_proof_digest is None


def test_ranking_v4_official_claim_requires_signed_production_membership(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-v4-official-fail-closed.db'}",
    )
    _persist_authoritative_opportunities(
        provider="fixture",
        cards=[("ranking-v4-official-card", "US:V4-OFFICIAL")],
    )
    _patch_authoritative_card(
        provider="fixture",
        card_id="ranking-v4-official-card",
        updates={
            "ranking_v4": {
                "selection_source": "ranking_v4",
                "model_version": RANKING_V4_MODEL_VERSION,
                "deployment_scope": "official_paper",
                "official_release_allowed": True,
            }
        },
    )
    repo = routes._repo()
    snapshot = repo.list_latest_opportunity_snapshots_by_card_ids(
        ["ranking-v4-official-card"],
        provider="fixture",
    )[0]

    decision = evaluate_paper_snapshot_admission(
        repo,
        snapshot,
        provider="fixture",
    )

    assert decision.eligible is False
    assert decision.admission_source == "ranking_v4_production"
    assert "signed release" in (decision.reason or "")


def test_ranking_v3_requires_matching_authoritative_release_proof(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-v3-proof.db'}",
    )
    client = TestClient(create_app())
    production_card, forward_only_card, tagged_only_card = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[
            ("ranking-v3-production", "US:PRODUCTION"),
            ("ranking-v3-forward-only", "US:FORWARD"),
            ("ranking-v3-tagged-only", "US:TAGGED"),
        ],
    )
    run_id = "walk-forward-release-1"
    released_run = _released_ranking_v3_run(provider="fixture", run_id=run_id)
    monkeypatch.setattr(
        QagentRepository,
        "get_walk_forward_run",
        lambda _repo, requested_run_id: (
            released_run if requested_run_id == released_run.run_id else None
        ),
    )
    repo = routes._repo()
    initial_snapshots = repo.list_latest_opportunity_snapshots_by_card_ids(
        [
            production_card.card_id,
            forward_only_card.card_id,
            tagged_only_card.card_id,
        ],
        provider="fixture",
    )
    initial_by_card = {snapshot.card_id: snapshot for snapshot in initial_snapshots}
    production_date = trading_day_offset(
        build_ranking_v3_protocol().prospective_shadow_start,
        20,
    )
    with repo.session_factory() as session:
        for snapshot in initial_snapshots:
            row = session.get(OpportunitySnapshotRow, snapshot.snapshot_id)
            assert row is not None
            row.signal_date = production_date
        session.commit()

    ranking_v3_metadata = {
        "selection_source": "ranking_v3",
        "model_version": RANKING_V3_MODEL_VERSION,
        "deployment_scope": "paper",
        "official_release_allowed": True,
    }
    for card in (production_card, forward_only_card, tagged_only_card):
        _patch_authoritative_card(
            provider="fixture",
            card_id=card.card_id,
            updates={"ranking_v3": ranking_v3_metadata},
        )

    snapshots = repo.list_latest_opportunity_snapshots_by_card_ids(
        [
            production_card.card_id,
            forward_only_card.card_id,
            tagged_only_card.card_id,
        ],
        provider="fixture",
    )
    snapshots_by_card = {snapshot.card_id: snapshot for snapshot in snapshots}
    production_snapshot = snapshots_by_card[production_card.card_id]
    forward_only_snapshot = snapshots_by_card[forward_only_card.card_id]
    tagged_only_snapshot = snapshots_by_card[tagged_only_card.card_id]
    assert {snapshot.snapshot_id for snapshot in snapshots} == {
        snapshot.snapshot_id for snapshot in initial_by_card.values()
    }

    proof, _forward_candidate_binding = _persist_ranking_v3_release_proof(
        repo,
        released_run,
        approved_snapshot_id=forward_only_snapshot.snapshot_id,
        approved_instrument_id=forward_only_snapshot.instrument_id,
    )

    identity = RankingV3ProductionIdentity.from_release_proof(
        proof,
        validation_run_id=released_run.run_id,
    )

    class _ApprovedRelease:
        def validate_current_release(self, requested_identity):
            return RankingV3ProductionReleaseValidation(
                valid=requested_identity == identity,
                current=requested_identity == identity,
                status="approved" if requested_identity == identity else "missing",
                reason="authoritative test release",
                release_proof_digest=proof.proof_digest,
                validation_run_id=released_run.run_id,
                data_revision=proof.data_revision,
                protocol_identity=proof.identity,
                approved_at=proof.generated_at,
            )

    production_selection = RankingV3ProductionSelectionItem.create(
        candidate_id="production-candidate-1",
        instrument_id=production_snapshot.instrument_id,
        source_snapshot_id=production_snapshot.snapshot_id,
        strategy_id=production_snapshot.primary_strategy_id or "",
        rank=1,
        score=production_snapshot.rank_score,
        source_rank_score=production_snapshot.rank_score,
        trigger_price=production_snapshot.trigger_price,
        initial_stop=production_snapshot.initial_stop,
        target_1=production_snapshot.target_1,
        allocation_multiplier=Decimal("1"),
    )
    scan_started_at = datetime.combine(
        production_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=1)
    scan_completed_at = scan_started_at + timedelta(hours=6)
    scan_recorded_at = scan_completed_at + timedelta(minutes=1)
    batch_recorded_at = scan_recorded_at + timedelta(minutes=1)
    with repo.session_factory.begin() as session:
        scan_row = session.get(ScanRunRow, production_snapshot.run_id)
        assert scan_row is not None
        scan_row.started_at = scan_started_at
        scan_row.completed_at = scan_completed_at
        scan_row.created_at = scan_recorded_at
    production_batch_input = RankingV3ProductionBatchInput.create(
        session_date=production_date,
        candidate_snapshot_digest=stable_digest(
            {
                "session_date": production_date,
                "snapshot_id": production_snapshot.snapshot_id,
                "instrument_id": production_snapshot.instrument_id,
                "strategy_id": production_snapshot.primary_strategy_id,
            }
        ),
        selections=(production_selection,),
        source_scan_run_id=production_snapshot.run_id,
        source_scan_started_at=scan_started_at,
        source_scan_completed_at=scan_completed_at,
        source_scan_recorded_at=scan_recorded_at,
        recorded_at=batch_recorded_at,
    )

    class _ApprovedSelection:
        def validate_selection(self, requested_identity, requested_batch):
            allowed = (
                requested_identity == identity
                and requested_batch == production_batch_input
            )
            return RankingV3ProductionSelectionValidation(
                authorized=allowed,
                reason="authoritative test selection",
                identity_digest=identity.identity_digest if allowed else None,
                selection_batch_digest=(
                    production_batch_input.selection_batch_digest if allowed else None
                ),
            )

    production_service = RankingV3ProductionSelectionService(
        RankingV3ProductionRepository(repo.session_factory),
        _ApprovedRelease(),
        selection_authority=_ApprovedSelection(),
        now=lambda: batch_recorded_at,
    )
    production_batch = production_service.record_batch(
        identity,
        production_batch_input,
        idempotency_key="api-production-batch",
    )

    accepted = evaluate_paper_snapshot_admission(
        repo,
        production_snapshot,
        provider="fixture",
        mode="automatic",
    )
    forward_only = evaluate_paper_snapshot_admission(
        repo,
        forward_only_snapshot,
        provider="fixture",
        mode="automatic",
    )
    tagged_only = evaluate_paper_snapshot_admission(
        repo,
        tagged_only_snapshot,
        provider="fixture",
        mode="automatic",
    )
    manual_tagged_only = evaluate_paper_snapshot_admission(
        repo,
        tagged_only_snapshot,
        provider="fixture",
        mode="manual",
    )

    assert accepted.eligible is True
    assert accepted.admission_source == "ranking_v3_production"
    assert accepted.production_identity_digest == identity.identity_digest
    assert accepted.production_batch_fact_digest == production_batch.fact_digest
    assert accepted.production_selection_item_digest == production_selection.item_digest
    assert accepted.release_proof_digest == proof.proof_digest
    assert forward_only.eligible is False
    assert "not an exact member" in (forward_only.reason or "")
    assert tagged_only.eligible is False
    assert "not an exact member" in (tagged_only.reason or "")
    assert manual_tagged_only.eligible is False
    assert "not an exact member" in (manual_tagged_only.reason or "")

    seed_result = seed_paper_trades_from_snapshots(
        routes._paper_repo(),
        [production_snapshot, forward_only_snapshot, tagged_only_snapshot],
        provider="fixture",
        max_created=3,
        max_active_trades=3,
        admission_repo=repo,
        admission_mode="automatic",
    )
    trades = routes._paper_repo().list_trades(limit=10, provider="fixture")

    assert seed_result.scanned == 3
    assert seed_result.created == 1
    assert seed_result.skipped == 2
    assert len(trades) == 1
    assert trades[0].source_snapshot_id == production_snapshot.snapshot_id
    assert trades[0].admission_source == "ranking_v3_production"
    assert trades[0].production_identity_digest == identity.identity_digest
    assert trades[0].production_batch_fact_digest == production_batch.fact_digest
    assert trades[0].production_selection_item_digest == production_selection.item_digest
    listed = client.get("/api/paper-trades?provider=fixture")
    assert listed.status_code == 200
    assert listed.json()["summary"]["total"] == 1
    assert len(listed.json()["trades"]) == 1
    assert (
        listed.json()["data_health"]["paper_production_authentication"]
        == "verified"
    )
    retained_session = client.post(
        "/api/paper-trades/session/start",
        json={
            "label": "保留正式样本",
            "reset_existing": False,
            "initial_capital": "100000",
            "allocation_per_trade_pct": "10",
            "max_positions": 5,
            "transaction_cost_bps": "5",
            "slippage_bps": "5",
            "take_profit_pct": "50",
        },
    )
    assert retained_session.status_code == 200
    assert retained_session.json()["ledger"]["summary"]["total_trades"] == 1


def test_paper_trade_events_return_not_found_for_unknown_trade(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-events.db'}")
    client = TestClient(create_app())

    response = client.get("/api/paper-trades/missing/events")

    assert response.status_code == 404
    assert response.json()["detail"] == "paper trade not found"


def test_paper_trade_from_opportunity_rejects_recently_invalidated_price_data(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-invalidated-card.db'}",
    )
    client = TestClient(create_app())
    first_card, second_card = _persist_authoritative_opportunities(
        provider="free",
        cards=[
            ("card_invalidated_0001", "CN:159516"),
            ("card_invalidated_0002", "CN:159516"),
        ],
    )
    payload = _opportunity_request(first_card, provider="free")
    created = client.post("/api/paper-trades/from-opportunity", json=payload)
    trade_id = created.json()["trade"]["trade_id"]
    routes._paper_repo().update_trade(
        trade_id,
        status="invalidated",
        exit_date=date.today(),
        latest_date=date.today(),
        latest_price=Decimal("0.85"),
        notes="推荐快照与首个盘中价格口径跳变超过 45%，样本作废并释放名额。",
    )

    response = client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(second_card, provider="free"),
    )

    assert response.status_code == 400
    assert "price data was invalidated" in response.json()["detail"]


def test_paper_trade_from_opportunity_enforces_account_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-card-capacity.db'}",
    )
    client = TestClient(create_app())
    client.post(
        "/api/paper-trades/session/start",
        json={
            "label": "单仓测试",
            "reset_existing": True,
            "initial_capital": "100000",
            "allocation_per_trade_pct": "10",
            "max_positions": 1,
            "transaction_cost_bps": "5",
            "slippage_bps": "5",
            "take_profit_pct": "50",
        },
    )
    first_card, second_card = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[
            ("capacity_1", "US:ONE"),
            ("capacity_2", "US:TWO"),
        ],
    )

    first = client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(first_card, provider="fixture"),
    )
    second = client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(second_card, provider="fixture"),
    )

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert second.status_code == 409
    assert second.json()["detail"] == "paper portfolio is full (1/1)"


def test_paper_candidate_pool_blocks_recently_invalidated_price_basis(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-invalidated-candidate.db'}",
    )
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    seeded = client.post("/api/paper-trades/seed?provider=fixture&limit=1")
    assert seeded.status_code == 200
    trade = client.get(
        "/api/paper-trades?provider=fixture&reporting_scope=legacy"
    ).json()["trades"][0]
    trigger = Decimal(trade["trigger_price"])
    routes._paper_repo().update_trade(
        trade["trade_id"],
        status="invalidated",
        exit_date=date.today(),
        latest_date=date.today(),
        latest_price=trigger * Decimal("0.40"),
        notes="推荐快照与首个盘中价格口径跳变超过 45%，样本作废并释放名额。",
    )

    response = client.get(
        "/api/paper-trades/candidate-pool?provider=fixture&include_etfs=true&limit=10"
    )

    assert response.status_code == 200
    item = next(
        candidate
        for candidate in response.json()["items"]
        if candidate["instrument_id"] == "US:TEST"
    )
    assert item["status"] == "blocked_by_data"
    assert item["price_basis_consistent"] is False
    assert item["action"] == "价格基准不一致"


def test_paper_trade_api_deletes_trade(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-delete.db'}")
    client = TestClient(create_app())
    (card,) = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[("card_delete_0001", "US:TEST")],
    )
    opportunity = _opportunity_request(card, provider="fixture")

    created = client.post("/api/paper-trades/from-opportunity", json=opportunity)
    trade_id = created.json()["trade"]["trade_id"]
    deleted = client.delete(f"/api/paper-trades/{trade_id}")
    listed = client.get("/api/paper-trades")
    events = client.get(f"/api/paper-trades/{trade_id}/events")
    deleted_again = client.delete(f"/api/paper-trades/{trade_id}")

    assert created.status_code == 200
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["trade_id"] == trade_id
    assert listed.json()["summary"]["total"] == 0
    assert events.status_code == 200
    assert events.json()["status"] == "deleted"
    assert events.json()["events"][-1]["event_type"] == "deleted"
    assert deleted_again.status_code == 404


def test_paper_trade_api_filters_by_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-provider-filter.db'}")
    client = TestClient(create_app())
    (free_card,) = _persist_authoritative_opportunities(
        provider="free",
        cards=[("card_filter_free", "CN:000001")],
    )
    (fixture_card,) = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[("card_filter_fixture", "CN:000001")],
    )
    client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(free_card, provider="free"),
    )
    client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(fixture_card, provider="fixture"),
    )

    listed = client.get("/api/paper-trades?provider=free&reporting_scope=legacy")
    ledger = client.get("/api/paper-trades/ledger?provider=free&reporting_scope=legacy")
    validation = client.get(
        "/api/paper-trades/validation?provider=free&reporting_scope=legacy"
    )
    report = client.get(
        "/api/paper-trades/daily-report?provider=free&reporting_scope=legacy"
    )

    assert listed.status_code == 200
    assert listed.json()["summary"]["total"] == 1
    assert listed.json()["trades"][0]["provider"] == "free"
    assert ledger.json()["summary"]["total_trades"] == 1
    assert validation.json()["summary"]["total_trades"] == 1
    assert report.json()["summary"]["total_trades"] == 1


def test_paper_trade_session_start_resets_records_and_saves_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-session.db'}")
    client = TestClient(create_app())
    (card,) = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[("card_session_0001", "US:TEST")],
    )
    client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(card, provider="fixture"),
    )

    started = client.post(
        "/api/paper-trades/session/start",
        json={
            "label": "A股研究模拟盘",
            "reset_existing": True,
            "initial_capital": "100000",
            "allocation_per_trade_pct": "10",
            "max_positions": 5,
            "transaction_cost_bps": "5",
            "slippage_bps": "5",
            "take_profit_pct": "50",
        },
    )
    listed = client.get("/api/paper-trades")
    session = client.get("/api/paper-trades/session")
    ledger = client.get("/api/paper-trades/ledger")

    assert started.status_code == 200
    body = started.json()
    assert body["cleared_trades"] == 1
    assert body["account"]["label"] == "A股研究模拟盘"
    assert body["account"]["status"] == "active"
    assert body["account"]["initial_capital"] == "100000.0000"
    assert body["account"]["max_positions"] == 5
    assert body["account"]["transaction_cost_bps"] == "5.0000"
    assert body["ledger"]["summary"]["max_positions"] == 5
    assert body["ledger"]["summary"]["transaction_cost_bps"] == 5.0
    assert listed.json()["summary"]["total"] == 0
    assert session.json()["account"]["label"] == "A股研究模拟盘"
    assert ledger.json()["summary"]["take_profit_pct"] == 50.0
    assert ledger.json()["summary"]["max_positions"] == 5
    assert ledger.json()["data_health"]["paper_session_status"] == "active"


def test_paper_account_status_separates_active_capacity_from_manual_positions(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL",
        f"sqlite:///{tmp_path / 'paper-account-status.db'}",
    )
    client = TestClient(create_app())
    started = client.post(
        "/api/paper-trades/session/start",
        json={
            "label": "A股研究模拟盘",
            "reset_existing": False,
            "initial_capital": "100000",
            "allocation_per_trade_pct": "10",
            "max_positions": 10,
            "transaction_cost_bps": "5",
            "slippage_bps": "5",
            "take_profit_pct": "50",
        },
    )
    assert started.status_code == 200

    pending = routes._paper_repo().create_trade(
        source_snapshot_id="account-status-pending",
        provider="fixture",
        instrument_id="CN:000001",
        strategy_id="trend_momentum",
        signal_date=date(2026, 8, 3),
        trigger_price=Decimal("12.00"),
        initial_stop=Decimal("11.40"),
        target_1=Decimal("13.20"),
        rank_score=Decimal("0.80"),
    )
    opened = routes._paper_repo().create_trade(
        source_snapshot_id="account-status-open",
        provider="fixture",
        instrument_id="US:TEST",
        strategy_id="trend_momentum",
        signal_date=date(2026, 8, 3),
        trigger_price=Decimal("82.00"),
        initial_stop=Decimal("78.00"),
        target_1=Decimal("90.00"),
        rank_score=Decimal("0.82"),
    )
    routes._paper_repo().update_trade(
        opened.trade_id,
        status="open",
        entry_date=date(2026, 8, 4),
        entry_price=Decimal("82.00"),
        latest_date=date(2026, 8, 4),
        latest_price=Decimal("82.00"),
        unrealized_return_pct=0.0,
        holding_days=0,
    )
    assert pending.status == "pending"
    manual = client.post(
        "/api/positions",
        json={
            "instrument_id": "CN:600519",
            "shares": "100",
            "entry_price": "1400",
            "entry_date": "2026-08-04",
            "strategy_tag": "manual",
            "initial_stop": "1300",
            "target_1": "1550",
            "thesis": "manual tracking only",
        },
    )
    assert manual.status_code == 200

    response = client.get("/api/paper-trades/account-status?provider=fixture")

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["max_positions"] == 10
    assert body["research"]["pending"] == 1
    assert body["research"]["open"] == 1
    assert body["research"]["active"] == 2
    assert body["research"]["remaining"] == 8
    assert body["official"]["active"] == 0
    assert body["official"]["remaining"] == 10
    assert body["current_model"] is None
    assert body["observation"]["calendar"] == "XSHG"
    assert body["observation"]["account_completed_sessions"] >= 0
    assert body["manual"] == {"count": 1, "uses_paper_capacity": False}
    assert body["data_health"]["manual_positions_are_separate"] == "true"


def test_paper_forward_calendar_uses_exchange_sessions_and_flags_cache_dates():
    sessions, unexpected = routes._paper_forward_calendar(
        start_date=date(2026, 7, 2),
        report_date=date(2026, 8, 10),
        completed_session=date(2026, 8, 7),
        cached_dates={
            date(2026, 7, 2),
            date(2026, 7, 5),
            date(2026, 8, 10),
        },
    )

    assert len(sessions) == 27
    assert sessions[0] == date(2026, 7, 2)
    assert sessions[-1] == date(2026, 8, 7)
    assert unexpected == [date(2026, 7, 5)]


def test_paper_trade_api_returns_ledger_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-ledger.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    client.post("/api/paper-trades/seed?provider=fixture&limit=5")
    client.post("/api/paper-trades/update?provider=fixture")

    response = client.get(
        "/api/paper-trades/ledger?initial_capital=100000&reporting_scope=legacy"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_trades"] == 1
    assert body["summary"]["closed_trades"] == 1
    assert body["summary"]["total_equity"] == "99855.77"
    assert body["curve"]
    assert body["items"][0]["outcome"] == "止损离场"
    assert "transactions" in body
    assert "positions" in body


def test_paper_trade_api_returns_daily_report(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-daily-report.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    client.post("/api/paper-trades/seed?provider=fixture&limit=5")
    client.post("/api/paper-trades/update?provider=fixture")

    response = client.get("/api/paper-trades/daily-report?reporting_scope=legacy")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_trades"] == 1
    assert "next_trade_day_focus" in body
    assert body["data_health"]["paper_daily_report"] == "ready"


def test_paper_trade_daily_report_uses_cached_benchmarks_only(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-daily-report-cache.db'}"
    )
    client = TestClient(create_app())
    (card,) = _persist_authoritative_opportunities(
        provider="free",
        cards=[("card_report_cache_0001", "CN:000001")],
    )
    client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(card, provider="free"),
    )

    def fail_live_provider(*_args, **_kwargs):
        raise AssertionError("daily report should not fetch live benchmark data")

    monkeypatch.setattr("qagent.api.routes.build_market_data_provider", fail_live_provider)

    response = client.get(
        "/api/paper-trades/daily-report?provider=free&reporting_scope=legacy"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_trades"] == 1
    assert body["benchmark"]["items"]
    assert body["data_health"]["paper_daily_benchmarks_source"] == "market_cache_only"


def test_paper_trade_daily_report_uses_cached_etf_proxy_benchmarks(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-daily-report-proxy.db'}"
    )
    client = TestClient(create_app())
    trade = routes._paper_repo().create_trade(
        source_snapshot_id="opportunity:card_report_proxy_0001",
        provider="free",
        instrument_id="CN:002747",
        strategy_id="trend_momentum",
        signal_date=date(2026, 7, 1),
        trigger_price=Decimal("30.00"),
        initial_stop=Decimal("28.50"),
        target_1=Decimal("33.00"),
        rank_score=Decimal("0.82"),
        notes="test proxy benchmark trade",
    )
    routes._paper_repo().update_trade(
        trade.trade_id,
        latest_date=date(2026, 7, 6),
        latest_price=Decimal("30.60"),
    )
    routes._market_cache_repo().save_daily_bars(
        "free",
        pd.DataFrame(
            [
                {
                    "instrument_id": proxy_id,
                    "trade_date": trade_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000,
                    "provider": "proxy_fixture",
                }
                for proxy_id, first_close, last_close in [
                    ("CN:510300", 100, 102),
                    ("CN:510500", 100, 101),
                    ("CN:159915", 100, 98),
                    ("CN:588000", 100, 104),
                ]
                for trade_date, close in [
                    (date(2026, 7, 1), first_close),
                    (date(2026, 7, 6), last_close),
                ]
            ]
        ),
    )

    def fail_live_provider(*_args, **_kwargs):
        raise AssertionError("daily report should not fetch live benchmark data")

    monkeypatch.setattr("qagent.api.routes.build_market_data_provider", fail_live_provider)

    response = client.get(
        "/api/paper-trades/daily-report?provider=free&reporting_scope=legacy"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data_health"]["paper_daily_benchmarks_source"] == "market_cache_only"
    assert body["data_health"]["paper_daily_benchmark_rows"] == "8"
    assert {item["name"] for item in body["benchmark"]["items"]} == {
        "沪深300",
        "中证500",
        "创业板指",
        "科创50",
    }
    assert all(item["return_pct"] is not None for item in body["benchmark"]["items"])


def test_paper_trade_daily_report_falls_back_to_recent_cached_benchmarks(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-daily-report-recent-proxy.db'}"
    )
    client = TestClient(create_app())
    trade = routes._paper_repo().create_trade(
        source_snapshot_id="opportunity:card_report_recent_proxy_0001",
        provider="free",
        instrument_id="CN:002747",
        strategy_id="trend_momentum",
        signal_date=date(2026, 7, 6),
        trigger_price=Decimal("30.00"),
        initial_stop=Decimal("28.50"),
        target_1=Decimal("33.00"),
        rank_score=Decimal("0.82"),
        notes="test recent proxy benchmark fallback",
    )
    routes._paper_repo().update_trade(
        trade.trade_id,
        latest_date=date(2026, 7, 6),
        latest_price=Decimal("30.60"),
    )
    routes._market_cache_repo().save_daily_bars(
        "free",
        pd.DataFrame(
            [
                {
                    "instrument_id": proxy_id,
                    "trade_date": trade_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000,
                    "provider": "proxy_fixture",
                }
                for proxy_id, first_close, last_close in [
                    ("CN:510300", 100, 102),
                    ("CN:510500", 100, 101),
                    ("CN:159915", 100, 98),
                    ("CN:588000", 100, 104),
                ]
                for trade_date, close in [
                    (date(2026, 6, 20), first_close),
                    (date(2026, 6, 26), last_close),
                ]
            ]
        ),
    )

    def fail_live_provider(*_args, **_kwargs):
        raise AssertionError("daily report should not fetch live benchmark data")

    monkeypatch.setattr("qagent.api.routes.build_market_data_provider", fail_live_provider)

    response = client.get(
        "/api/paper-trades/daily-report?provider=free&reporting_scope=legacy"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data_health"]["paper_daily_benchmark_rows"] == "8"
    assert all(item["return_pct"] is not None for item in body["benchmark"]["items"])


def test_paper_trade_auto_validation_reports_5_10_20_day_outcomes(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-validation.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    client.post("/api/paper-trades/seed?provider=fixture&limit=5")

    response = client.post(
        "/api/paper-trades/validation/run?provider=fixture&reporting_scope=legacy"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_trades"] == 1
    assert body["summary"]["closed_trades"] == 1
    assert body["summary"]["primary_window_days"] == 20
    assert body["summary"]["verdict"] in {"profitable", "risk", "building_sample", "no_data"}
    assert [window["window_days"] for window in body["windows"]] == [5, 10, 20]
    assert body["windows"][0]["evaluated_trades"] == 0
    assert body["windows"][1]["evaluated_trades"] == 0
    assert body["windows"][2]["evaluated_trades"] == 0
    assert body["items"][0]["instrument_id"] == "US:TEST"
    assert body["items"][0]["validation_state"] in {
        "closed",
        "open",
        "waiting_entry",
        "expired",
        "missed_entry",
    }
    assert body["curve"]
    assert body["sample_age"]["average_days_since_signal"] >= 0
    assert body["sample_age"]["mature_5d"] >= 0
    assert body["sample_age"]["mature_10d"] >= 0
    assert body["sample_age"]["mature_20d"] >= 0
    assert body["batches"]
    assert body["batches"][0]["batch_date"]
    assert body["batches"][0]["total_trades"] == 1
    assert [window["window_days"] for window in body["batches"][0]["windows"]] == [5, 10, 20]
    assert body["credibility"]["level"] in {"high", "medium", "low", "insufficient"}
    assert body["credibility"]["score"] >= 0
    assert body["credibility"]["summary"]
    assert body["data_health"]["validation_windows"] == "5,10,20"


def test_paper_trade_api_returns_flow_ledger_with_costs(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-flow-ledger.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")
    client.post("/api/paper-trades/seed?provider=fixture&limit=5")
    client.post("/api/paper-trades/update?provider=fixture")

    response = client.get(
        "/api/paper-trades/ledger"
        "?initial_capital=100000&transaction_cost_bps=3&slippage_bps=5"
        "&take_profit_pct=50&reporting_scope=legacy"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_fees"] != "0.00"
    assert body["summary"]["total_slippage"] != "0.00"
    assert body["summary"]["turnover"] != "0.00"
    assert body["transactions"][0]["action"] == "entry_buy"
    assert body["transactions"][0]["cash_flow"].startswith("-")


def test_agent_answers_from_paper_trade_context(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-agent.db'}")
    client = TestClient(create_app())
    (card,) = _persist_authoritative_opportunities(
        provider="fixture",
        cards=[("card_agent_0001", "US:TEST")],
    )
    client.post(
        "/api/paper-trades/from-opportunity",
        json=_opportunity_request(card, provider="fixture"),
    )

    response = client.post(
        "/api/agent/query",
        json={"question": "我买了这个现在怎么办？", "instrument_id": "US:TEST"},
    )

    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "模拟盘" in answer
    assert "US:TEST" in answer
    assert "不是个性化投资建议" in answer


def test_paper_trading_api_seeds_updates_and_lists_trades(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'paper-api.db'}")
    client = TestClient(create_app())
    client.get("/api/opportunities?provider=fixture&symbols=US:TEST")

    seed_response = client.post("/api/paper-trades/seed?provider=fixture&limit=5")
    update_response = client.post("/api/paper-trades/update?provider=fixture")
    list_response = client.get("/api/paper-trades?reporting_scope=legacy")

    assert seed_response.status_code == 200
    assert seed_response.json()["created"] == 1
    assert update_response.status_code == 200
    update_body = update_response.json()
    assert update_body["summary"]["total"] == 1
    assert update_body["summary"]["closed"] == 1
    assert list_response.status_code == 200
    assert list_response.json()["trades"][0]["instrument_id"] == "US:TEST"
