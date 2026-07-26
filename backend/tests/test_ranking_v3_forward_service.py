from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from qagent.backtesting.experiment import build_walk_forward_experiment_manifest
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardEquityPoint,
    RankingV3ForwardIdentity,
    RankingV3ForwardPortfolioInput,
    RankingV3ForwardStateError,
    RankingV3ShadowCandidateInput,
    forward_candidate_selection_digest,
    forward_candidate_source_digest,
    stable_digest,
)
from qagent.backtesting.ranking_v3_evidence import ranking_v3_data_revision
from qagent.backtesting.ranking_v3_forward_service import (
    RankingV3ForwardCandidateFact,
    RankingV3ForwardDayFacts,
    RankingV3ForwardFactAuthority,
    RankingV3ForwardOutcomeFact,
    RankingV3ForwardService,
)
from qagent.backtesting.ranking_v3_protocol import (
    RankingV3Protocol,
    build_ranking_v3_protocol,
)
from qagent.backtesting.ranking_v3_pbo import (
    RankingV3DatedModelReturn,
    evaluate_ranking_v3_cscv_pbo,
)
from qagent.db import Base, create_db_engine
from qagent.market.calendars import trading_day_offset
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository
from qagent.storage.tables import (
    OpportunitySnapshotRow,
    PaperTradeEventRow,
    PaperTradeRow,
)


NOW = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
BENCHMARK_ID = "CN:000300.IDX"


class _RunRepository:
    def __init__(self, *runs):
        self.runs = {run.run_id: run for run in runs}

    def get_walk_forward_run(self, run_id):
        return self.runs.get(run_id)


class _FactAuthority(RankingV3ForwardFactAuthority):
    def __init__(self):
        self.facts = {}
        self.errors = {}
        self.calls = []
        self.portfolio_recompute = None

    def put(self, facts: RankingV3ForwardDayFacts) -> None:
        self.facts[(facts.validation_run_id, facts.session_date)] = facts

    def put_for_request(
        self,
        validation_run_id: str,
        session_date: date,
        facts: RankingV3ForwardDayFacts,
    ) -> None:
        self.facts[(validation_run_id, session_date)] = facts

    def fail(
        self,
        validation_run_id: str,
        session_date: date,
        error: Exception,
    ) -> None:
        self.errors[(validation_run_id, session_date)] = error

    def build_day_facts(
        self,
        *,
        validation_run_id,
        session_date,
        run,
        ranking_v3,
        protocol,
        data_revision,
    ):
        self.calls.append(
            {
                "validation_run_id": validation_run_id,
                "session_date": session_date,
                "run": run,
                "ranking_v3": ranking_v3,
                "protocol": protocol,
                "data_revision": data_revision,
            }
        )
        key = (validation_run_id, session_date)
        if key in self.errors:
            raise self.errors[key]
        if key not in self.facts:
            raise LookupError("authoritative day facts do not exist")
        return self.facts[key]

    def recompute_portfolio_evidence(self, *, submitted, **_kwargs):
        return self.portfolio_recompute or submitted


def _protocol(protocol_id: str | None = None) -> RankingV3Protocol:
    original = build_ranking_v3_protocol()
    protocol_id = protocol_id or original.protocol_id
    if protocol_id == original.protocol_id:
        return original
    payload = original.model_dump(mode="json", exclude={"protocol_digest"})
    payload["protocol_id"] = protocol_id
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return RankingV3Protocol.model_validate({**payload, "protocol_digest": digest})


def _pbo_evidence() -> dict[str, object]:
    rebalance_dates = [date(2026, 1, 5) + timedelta(days=offset) for offset in range(12)]
    matrix = {
        model_id: [
            RankingV3DatedModelReturn(day, value)
            for day, value in zip(rebalance_dates, values, strict=True)
        ]
        for model_id, values in {
            "baseline": (
                -0.01,
                -0.02,
                -0.01,
                -0.02,
                -0.01,
                -0.02,
                -0.01,
                -0.02,
                -0.01,
                -0.02,
                -0.01,
                -0.02,
            ),
            "quality": (0.02, 0.03, 0.02, 0.03, 0.02, 0.03, 0.02, 0.03, 0.02, 0.03, 0.02, 0.03),
            "trend": (0.00, 0.01, 0.01, 0.00, 0.01, 0.01, 0.00, 0.01, 0.01, 0.00, 0.01, 0.01),
        }.items()
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


def _run(run_id: str, protocol: RankingV3Protocol, revision: int = 42):
    pbo_evidence = _pbo_evidence()
    experiment_manifest = build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=revision,
        start_date=date(2025, 1, 2),
        end_date=date(2026, 7, 24),
        rebalance_step_sessions=10,
        lookback_days=365,
    )
    return SimpleNamespace(
        run_id=run_id,
        provider="free",
        status="succeeded",
        dataset_revision=revision,
        reproducibility_digest=stable_digest({"run_id": run_id, "revision": revision}),
        updated_at=NOW,
        payload={
            "experiment_manifest": experiment_manifest.model_dump(mode="json"),
            "ranking_v3": {
                "model_version": protocol.model_version,
                "protocol": protocol.model_dump(mode="json"),
                "historical_validation": {"statistical_gate_status": "pass"},
                "criteria": [
                    {"key": "historical_statistical_evidence", "status": "pass"},
                    {"key": "positive_audit_return", "status": "pass"},
                    {"key": "pbo", "status": "pass"},
                    {"key": "prospective_shadow", "status": "insufficient"},
                ],
                "pbo_evidence": pbo_evidence,
            },
        },
    )


def _service(*runs, fact_authority=None):
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = RankingV3ForwardRepository(factory)
    authority = fact_authority or _FactAuthority()
    service = RankingV3ForwardService(
        repository,
        _RunRepository(*runs),
        authority,
        now=lambda: NOW,
    )
    return service, repository, factory, authority


def _candidate(
    instrument_id: str = "CN:600000",
    *,
    rank: int = 1,
) -> RankingV3ForwardCandidateFact:
    source_snapshot_id = f"server-snapshot-{instrument_id}-{rank}"
    return RankingV3ForwardCandidateFact(
        source_snapshot_id=source_snapshot_id,
        instrument_id=instrument_id,
        strategy_id="ranking-v3",
        rank=rank,
        score=Decimal("0.73"),
        benchmark_id=BENCHMARK_ID,
        selection_digest=stable_digest(
            {
                "source_snapshot_id": source_snapshot_id,
                "instrument_id": instrument_id,
                "rank": rank,
            }
        ),
    )


def _day(
    run_id: str,
    session_date: date,
    *,
    candidates=(),
    outcomes=(),
    portfolio_equity=Decimal("100000"),
    stress_portfolio_equity=Decimal("100000"),
    benchmark_equity=Decimal("100000"),
    portfolio_evidence=None,
    candidate_snapshot_digest=None,
    selection_batch_digest=None,
) -> RankingV3ForwardDayFacts:
    source_candidates = tuple(candidates)
    snapshot_digest = candidate_snapshot_digest or stable_digest(
        {
            "session_date": session_date,
            "candidates": [item.model_dump(mode="json") for item in source_candidates],
        }
    )
    batch_digest = selection_batch_digest or stable_digest(
        {
            "candidate_snapshot_digest": snapshot_digest,
            "selected": [
                item.model_dump(mode="json", exclude={"selection_digest"})
                for item in source_candidates
            ],
        }
    )
    frozen_candidates = tuple(
        item.model_copy(
            update={
                "selection_digest": forward_candidate_selection_digest(
                    selection_batch_digest=batch_digest,
                    source_snapshot_id=item.source_snapshot_id,
                    instrument_id=item.instrument_id,
                    strategy_id=item.strategy_id,
                    rank=item.rank,
                    score=item.score,
                )
            }
        )
        for item in source_candidates
    )
    return RankingV3ForwardDayFacts(
        validation_run_id=run_id,
        session_date=session_date,
        benchmark_id=BENCHMARK_ID,
        benchmark_return_pct=Decimal("0.20"),
        portfolio_equity=portfolio_equity,
        stress_portfolio_equity=stress_portfolio_equity,
        benchmark_equity=benchmark_equity,
        candidate_snapshot_digest=snapshot_digest,
        selection_batch_digest=batch_digest,
        selected_candidate_count=len(frozen_candidates),
        candidates=frozen_candidates,
        mature_outcomes=tuple(outcomes),
        portfolio_evidence=portfolio_evidence,
    )


def _portfolio_evidence(
    run_id: str,
    data_revision: str,
    session_date: date,
    source_candidate_digest: str,
) -> RankingV3ForwardPortfolioInput:
    equity_curve = (
        RankingV3ForwardEquityPoint(
            date=session_date - timedelta(days=2),
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
        RankingV3ForwardEquityPoint(
            date=session_date - timedelta(days=1),
            equity=Decimal("98000"),
            cash=Decimal("98000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("-2"),
        ),
        RankingV3ForwardEquityPoint(
            date=session_date,
            equity=Decimal("103000"),
            cash=Decimal("103000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
    )
    stress_equity_curve = (
        RankingV3ForwardEquityPoint(
            date=session_date - timedelta(days=2),
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
        RankingV3ForwardEquityPoint(
            date=session_date - timedelta(days=1),
            equity=Decimal("97000"),
            cash=Decimal("97000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("-3"),
        ),
        RankingV3ForwardEquityPoint(
            date=session_date,
            equity=Decimal("102000"),
            cash=Decimal("102000"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
    )
    return RankingV3ForwardPortfolioInput(
        validation_run_id=run_id,
        data_revision=data_revision,
        as_of_session_date=session_date,
        benchmark_id=BENCHMARK_ID,
        provider="free",
        execution_profile="test-capital-constrained",
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
        source_candidate_digest=source_candidate_digest,
    )


def test_process_day_is_idempotent_and_remains_unpublished_before_approval():
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, repository, factory, authority = _service(run)
    session_date = protocol.prospective_shadow_start
    facts = _day("strict-v6", session_date, candidates=(_candidate(),))
    authority.put(facts)

    first = service.process_day("strict-v6", session_date)
    repeated = service.process_day("strict-v6", session_date)

    assert repeated == first
    assert first.ledger_status == "pending"
    assert first.shadow_state == "shadow_unpublished"
    assert first.official_state_mutated is False
    assert first.release_proof_digest is None
    snapshot = repository.load_snapshot(first.evaluation.identity)
    assert snapshot is not None
    assert len(snapshot.sessions) == 1
    assert len(snapshot.candidates) == 1
    assert len(snapshot.evidence) == 3
    assert snapshot.candidates[0].outcome_status == "pending"
    assert snapshot.candidates[0].source_snapshot_id == facts.candidates[0].source_snapshot_id
    assert snapshot.sessions[0].candidate_snapshot_digest == facts.candidate_snapshot_digest
    assert snapshot.sessions[0].selection_batch_digest == facts.selection_batch_digest
    assert snapshot.sessions[0].selected_candidate_count == 1
    assert snapshot.candidates[0].fact_digest == stable_digest(
        RankingV3ShadowCandidateInput(
            candidate_id=snapshot.candidates[0].candidate_id,
            source_snapshot_id=facts.candidates[0].source_snapshot_id,
            session_date=session_date,
            maturity_session_date=snapshot.candidates[0].maturity_session_date,
            instrument_id=facts.candidates[0].instrument_id,
            strategy_id=facts.candidates[0].strategy_id,
            rank=facts.candidates[0].rank,
            score=facts.candidates[0].score,
            benchmark_id=facts.candidates[0].benchmark_id,
            data_revision=first.data_revision,
            selection_digest=facts.candidates[0].selection_digest,
        )
    )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OpportunitySnapshotRow)) == 0
        assert session.scalar(select(func.count()).select_from(PaperTradeRow)) == 0
        assert session.scalar(select(func.count()).select_from(PaperTradeEventRow)) == 0


def test_partial_same_day_replay_resumes_only_with_the_identical_frozen_batch(
    monkeypatch,
):
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, repository, _factory, authority = _service(run)
    session_date = protocol.prospective_shadow_start
    facts = _day("strict-v6", session_date, candidates=(_candidate(),))
    authority.put(facts)
    original = service._record_candidate_input

    def fail_after_session(*args, **kwargs):
        raise RuntimeError("simulated crash after session commit")

    monkeypatch.setattr(service, "_record_candidate_input", fail_after_session)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.process_day("strict-v6", session_date)

    identity = RankingV3ForwardIdentity.from_protocol(protocol)
    partial = repository.load_snapshot(identity)
    assert partial is not None
    assert len(partial.sessions) == 1
    assert partial.candidates == []

    monkeypatch.setattr(service, "_record_candidate_input", original)
    resumed = service.process_day("strict-v6", session_date)
    assert len(resumed.recorded_candidate_ids) == 1
    restored = repository.load_snapshot(identity)
    assert restored.sessions[0].candidate_snapshot_digest == facts.candidate_snapshot_digest
    assert restored.sessions[0].selection_batch_digest == facts.selection_batch_digest
    assert restored.sessions[0].selected_candidate_count == 1


def test_partial_same_day_replay_recovers_the_original_frozen_batch(
    monkeypatch,
):
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, repository, _factory, authority = _service(run)
    session_date = protocol.prospective_shadow_start
    original_facts = _day("strict-v6", session_date, candidates=(_candidate(),))
    authority.put(original_facts)

    def fail_after_session(*args, **kwargs):
        raise RuntimeError("simulated crash after session commit")

    original_record = service._record_candidate_input
    monkeypatch.setattr(service, "_record_candidate_input", fail_after_session)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.process_day("strict-v6", session_date)

    changed_facts = _day(
        "strict-v6",
        session_date,
        candidates=(_candidate("CN:600001"),),
    )
    authority.put(changed_facts)
    monkeypatch.setattr(service, "_record_candidate_input", original_record)
    resumed = service.process_day("strict-v6", session_date)

    partial = repository.load_snapshot(RankingV3ForwardIdentity.from_protocol(protocol))
    assert partial is not None
    assert len(partial.sessions) == 1
    assert len(partial.candidates) == 1
    assert partial.candidates[0].instrument_id == "CN:600000"
    assert resumed.recorded_candidate_ids == (partial.candidates[0].candidate_id,)


def test_candidate_identity_and_selection_facts_bind_source_snapshot():
    session_date = date(2026, 7, 27)
    first = _candidate()
    changed = first.model_copy(update={"source_snapshot_id": "server-snapshot-independent-copy"})

    assert first.selection_digest != stable_digest(
        {
            "source_snapshot_id": changed.source_snapshot_id,
            "instrument_id": changed.instrument_id,
            "rank": changed.rank,
        }
    )
    assert RankingV3ForwardService._candidate_id(
        session_date, first
    ) != RankingV3ForwardService._candidate_id(session_date, changed)


def test_service_cannot_be_constructed_without_a_fact_authority():
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = RankingV3ForwardRepository(factory)

    with pytest.raises(ValueError, match="requires a fact authority"):
        RankingV3ForwardService(
            repository,
            _RunRepository(),
            None,
        )


def test_weekend_is_rejected_before_any_shadow_ledger_is_created():
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, repository, _factory, authority = _service(run)

    with pytest.raises(ValueError, match="A-share trading session"):
        service.process_day("strict-v6", date(2026, 8, 1))

    assert authority.calls == []
    assert repository.load_snapshot(RankingV3ForwardIdentity.from_protocol(protocol)) is None


@pytest.mark.parametrize("forgery", ["run", "date"])
def test_authority_cannot_forge_requested_run_or_session_date(forgery):
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, repository, _factory, authority = _service(run)
    requested_date = protocol.prospective_shadow_start
    returned_run_id = "forged-run" if forgery == "run" else "strict-v6"
    returned_date = trading_day_offset(requested_date, 1) if forgery == "date" else requested_date
    authority.put_for_request(
        "strict-v6",
        requested_date,
        _day(returned_run_id, returned_date, candidates=(_candidate(),)),
    )

    expected = "mismatched validation run" if forgery == "run" else "mismatched session date"
    with pytest.raises(ValueError, match=expected):
        service.process_day("strict-v6", requested_date)

    assert repository.load_snapshot(RankingV3ForwardIdentity.from_protocol(protocol)) is None


def test_authority_failure_is_fail_closed_before_any_ledger_write():
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, repository, _factory, authority = _service(run)
    session_date = protocol.prospective_shadow_start
    authority.fail("strict-v6", session_date, RuntimeError("upstream facts unavailable"))

    with pytest.raises(RuntimeError, match="upstream facts unavailable"):
        service.process_day("strict-v6", session_date)

    assert len(authority.calls) == 1
    assert authority.calls[0]["run"] is run
    assert authority.calls[0]["protocol"] == protocol
    assert authority.calls[0]["data_revision"]
    assert repository.load_snapshot(RankingV3ForwardIdentity.from_protocol(protocol)) is None


def test_service_rejects_a_consistent_but_non_protocol_benchmark_before_write():
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, repository, _factory, authority = _service(run)
    session_date = protocol.prospective_shadow_start
    authority.put(
        _day("strict-v6", session_date).model_copy(
            update={"benchmark_id": "CN:000905.IDX"}
        )
    )

    with pytest.raises(ValueError, match="benchmark does not match"):
        service.process_day("strict-v6", session_date)

    assert repository.load_snapshot(RankingV3ForwardIdentity.from_protocol(protocol)) is None


def test_service_rejects_portfolio_evidence_without_independent_recomputation():
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    authority = _FactAuthority()
    authority.recompute_portfolio_evidence = None
    service, _repository, _factory, authority = _service(
        run,
        fact_authority=authority,
    )
    session_date = protocol.prospective_shadow_start
    authority.put(
        _day(
            "strict-v6",
            session_date,
            portfolio_evidence=_portfolio_evidence(
                run.run_id,
                ranking_v3_data_revision(run),
                session_date,
                stable_digest([]),
            ),
        )
    )

    with pytest.raises(RankingV3ForwardStateError, match="server-recomputed"):
        service.process_day("strict-v6", session_date)


def test_protocol_identity_isolates_runs_and_rejects_cross_ledger_outcomes():
    first_protocol = _protocol()
    second_protocol = _protocol("QAGENT-RANK-V3-ISOLATED")
    first_run = _run("strict-v6-a", first_protocol, revision=42)
    second_run = _run("strict-v6-b", second_protocol, revision=43)
    service, repository, _factory, authority = _service(first_run, second_run)
    first_date = first_protocol.prospective_shadow_start
    authority.put(_day("strict-v6-a", first_date, candidates=(_candidate("CN:600000"),)))
    authority.put(_day("strict-v6-b", first_date, candidates=(_candidate("CN:000001"),)))

    first = service.process_day("strict-v6-a", first_date)
    second = service.process_day("strict-v6-b", first_date)

    assert first.protocol_digest != second.protocol_digest
    assert len(repository.load_snapshot(first.evaluation.identity).candidates) == 1
    assert len(repository.load_snapshot(second.evaluation.identity).candidates) == 1
    second_snapshot_before = repository.load_snapshot(second.evaluation.identity)
    next_date = trading_day_offset(first_date, 1)
    authority.put(
        _day(
            "strict-v6-b",
            next_date,
            outcomes=(
                RankingV3ForwardOutcomeFact(
                    candidate_id=first.recorded_candidate_ids[0],
                    status="invalid",
                    reason="must not cross protocol identity",
                ),
            ),
        )
    )
    with pytest.raises(LookupError, match="active Ranking V3 ledger"):
        service.process_day("strict-v6-b", next_date)
    second_snapshot_after = repository.load_snapshot(second.evaluation.identity)
    assert second_snapshot_after == second_snapshot_before


def test_mature_outcome_is_written_once_without_fabricating_prior_returns():
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, repository, _factory, authority = _service(run)
    first_date = protocol.prospective_shadow_start
    authority.put(_day("strict-v6", first_date, candidates=(_candidate(),)))
    first = service.process_day("strict-v6", first_date)
    candidate_id = first.recorded_candidate_ids[0]

    maturity_offset = (
        protocol.statistics_definition.entry_wait_sessions
        + protocol.statistics_definition.holding_sessions
    )
    for offset in range(1, maturity_offset):
        session_date = trading_day_offset(first_date, offset)
        authority.put(_day("strict-v6", session_date))
        service.process_day("strict-v6", session_date)
    maturity_date = trading_day_offset(
        first_date,
        maturity_offset,
    )
    outcome = RankingV3ForwardOutcomeFact(
        candidate_id=candidate_id,
        status="completed",
        gross_return_pct=Decimal("3.00"),
        transaction_cost_pct=Decimal("0.10"),
        stress_transaction_cost_pct=Decimal("0.20"),
        benchmark_return_pct=Decimal("1.00"),
        max_drawdown_pct=Decimal("-2.00"),
        reason="resolved from prospective prices",
    )

    authority.put(_day("strict-v6", maturity_date, outcomes=(outcome,)))
    result = service.process_day("strict-v6", maturity_date)
    repeated = service.process_day("strict-v6", maturity_date)

    assert result == repeated
    assert result.finalized_candidate_ids == (candidate_id,)
    assert result.shadow_state == "shadow_unpublished"
    snapshot = repository.load_snapshot(result.evaluation.identity)
    stored = next(item for item in snapshot.candidates if item.candidate_id == candidate_id)
    assert stored.outcome_status == "completed"
    assert stored.gross_return_pct == Decimal("3")
    assert stored.net_return_pct == Decimal("2.9")
    assert stored.benchmark_excess_pct == Decimal("1.9")
    assert result.evaluation.metrics.completed_trade_count == 1


def test_outcome_cannot_be_finalized_before_protocol_maturity():
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, _repository, _factory, authority = _service(run)
    first_date = protocol.prospective_shadow_start
    authority.put(_day("strict-v6", first_date, candidates=(_candidate(),)))
    first = service.process_day("strict-v6", first_date)

    next_date = trading_day_offset(first_date, 1)
    authority.put(
        _day(
            "strict-v6",
            next_date,
            outcomes=(
                RankingV3ForwardOutcomeFact(
                    candidate_id=first.recorded_candidate_ids[0],
                    status="invalid",
                    reason="too early",
                ),
            ),
        )
    )
    with pytest.raises(ValueError, match="before maturity"):
        service.process_day("strict-v6", next_date)


def test_authoritative_daily_facts_can_produce_a_persisted_release_proof():
    protocol = _protocol()
    run = _run("strict-v6", protocol)
    service, repository, _factory, authority = _service(run)
    start = protocol.prospective_shadow_start
    maturity_offset = (
        protocol.statistics_definition.entry_wait_sessions
        + protocol.statistics_definition.holding_sessions
    )
    candidate_ids_by_session = {}

    for offset in range(maturity_offset + 2):
        session_date = trading_day_offset(start, offset)
        candidates = ()
        outcomes = ()
        if offset < 2:
            candidates = tuple(
                _candidate(f"CN:6000{offset}{rank}", rank=rank)
                for rank in range(1, protocol.max_positions + 1)
            )
        if offset >= maturity_offset:
            source_offset = offset - maturity_offset
            outcomes = tuple(
                RankingV3ForwardOutcomeFact(
                    candidate_id=candidate_id,
                    status="completed",
                    gross_return_pct=Decimal("3.00"),
                    transaction_cost_pct=Decimal("0.10"),
                    stress_transaction_cost_pct=Decimal("0.20"),
                    benchmark_return_pct=Decimal("1.00"),
                    max_drawdown_pct=Decimal("-1.00"),
                    reason="resolved by authoritative forward facts",
                )
                for candidate_id in candidate_ids_by_session[source_offset]
            )
        evidence = (
            _portfolio_evidence(
                run.run_id,
                ranking_v3_data_revision(run),
                session_date,
                forward_candidate_source_digest(
                    repository.load_snapshot(
                        RankingV3ForwardIdentity.from_protocol(protocol)
                    ).candidates
                ),
            )
            if offset == maturity_offset + 1
            else None
        )
        authority.put(
            _day(
                "strict-v6",
                session_date,
                candidates=candidates,
                outcomes=outcomes,
                portfolio_equity=Decimal("100000") + Decimal(offset * 100),
                stress_portfolio_equity=Decimal("100000") + Decimal(offset * 80),
                benchmark_equity=Decimal("100000") + Decimal(offset * 20),
                portfolio_evidence=evidence,
            )
        )
        result = service.process_day("strict-v6", session_date)
        if candidates:
            candidate_ids_by_session[offset] = result.recorded_candidate_ids

    assert result.ledger_status == "approved"
    assert result.shadow_state == "approved_proof_available"
    assert result.release_proof_digest is not None
    assert result.evaluation.release_proof is not None
    assert repository.get_release_proof(result.release_proof_digest) is not None
