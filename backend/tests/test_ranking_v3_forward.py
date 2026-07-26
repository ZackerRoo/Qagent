from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from qagent import db
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardConflictError,
    RankingV3ForwardEquityPoint,
    RankingV3ForwardIdentity,
    RankingV3ForwardOutcomeInput,
    RankingV3ForwardPortfolioInput,
    RankingV3ForwardSelectionBatchInput,
    RankingV3ForwardSessionInput,
    RankingV3ForwardStateError,
    RankingV3ForwardValidator,
    RankingV3HistoricalGatesInput,
    RankingV3PBOInput,
    RankingV3ShadowCandidateInput,
    forward_candidate_source_digest,
    forward_candidate_selection_digest,
    encode_forward_session_batch_key,
    stable_digest,
    stable_release_proof_digest,
)
from qagent.backtesting.ranking_v3_protocol import build_ranking_v3_protocol
from qagent.db import create_db_engine, create_session_factory, initialize_database
from qagent.market.calendars import trading_day_offset
from qagent.security.ranking_v3_attestation import RankingV3Attestor
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository


DATA_REVISION = "historical-revision-17"
BENCHMARK_ID = "CN:000300.IDX"
NOW = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


class _AuthoritativeEvidence:
    def verify_historical_gates(self, identity, evidence) -> bool:
        return (
            identity.protocol_id.startswith("QAGENT-RANK-V3")
            and evidence.source_proof_digest == "a" * 64
        )

    def verify_pbo(self, identity, evidence) -> bool:
        return (
            identity.protocol_id.startswith("QAGENT-RANK-V3")
            and evidence.matrix_digest == "b" * 64
            and evidence.source_proof_digest == "c" * 64
        )


class _AuthoritativePortfolio:
    def __init__(self):
        self.recomputed = None

    def recompute_portfolio(self, identity, protocol, snapshot, submitted):
        return self.recomputed or submitted


def _trading_dates(count: int) -> list[date]:
    start = date(2026, 7, 27)
    return [trading_day_offset(start, offset) for offset in range(count)]


def _validator(tmp_path, name: str = "forward", *, portfolio_authority=None):
    database_url = f"sqlite:///{tmp_path / f'{name}.db'}"
    initialize_database(database_url)
    repository = RankingV3ForwardRepository(create_session_factory(database_url))
    validator = RankingV3ForwardValidator(
        repository,
        build_ranking_v3_protocol(),
        evidence_authority=_AuthoritativeEvidence(),
        portfolio_authority=portfolio_authority or _AuthoritativePortfolio(),
        now=lambda: NOW,
    )
    validator.ensure_ledger(DATA_REVISION)
    return validator, repository, database_url


def _session(
    session_date: date,
    index: int,
    *,
    benchmark_id: str = BENCHMARK_ID,
    drawdown: bool = False,
) -> RankingV3ForwardSessionInput:
    equity = Decimal("100") + Decimal(index)
    stress_equity = Decimal("100") + Decimal(index) * Decimal("0.8")
    if drawdown and index >= 10:
        equity = Decimal("80")
        stress_equity = Decimal("78")
    return RankingV3ForwardSessionInput(
        session_date=session_date,
        benchmark_id=benchmark_id,
        benchmark_return_pct=Decimal("0.2"),
        portfolio_equity=equity,
        stress_portfolio_equity=stress_equity,
        benchmark_equity=Decimal("100") + Decimal(index) * Decimal("0.2"),
        data_revision=DATA_REVISION,
    )


def _candidate(
    session_date: date,
    index: int,
    *,
    benchmark_id: str = BENCHMARK_ID,
) -> RankingV3ShadowCandidateInput:
    candidate_id = f"forward-candidate-{index:03d}"
    return RankingV3ShadowCandidateInput(
        candidate_id=candidate_id,
        source_snapshot_id=f"server-snapshot-{session_date.isoformat()}-{index:03d}",
        session_date=session_date,
        maturity_session_date=_next_trading_date(session_date),
        instrument_id=f"CN:{index + 1:06d}",
        strategy_id="ranking-v3",
        rank=index + 1,
        score=Decimal("0.8"),
        benchmark_id=benchmark_id,
        data_revision=DATA_REVISION,
        selection_digest=stable_digest({"candidate_id": candidate_id}),
    )


def _freeze_and_record_session(
    validator: RankingV3ForwardValidator,
    session: RankingV3ForwardSessionInput,
    candidates: tuple[RankingV3ShadowCandidateInput, ...] = (),
) -> tuple[RankingV3ShadowCandidateInput, ...]:
    candidate_snapshot_digest = stable_digest(
        {
            "session_date": session.session_date,
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
    batch = RankingV3ForwardSelectionBatchInput.create(
        session_date=session.session_date,
        benchmark_id=session.benchmark_id,
        data_revision=session.data_revision,
        candidate_snapshot_digest=candidate_snapshot_digest,
        selection_batch_digest=selection_batch_digest,
        candidates=frozen_candidates,
    )
    validator.freeze_selection_batch(
        batch,
        idempotency_key=f"test-frozen-batch:{session.session_date.isoformat()}",
    )
    validator.record_session(
        session.model_copy(
            update={
                "candidate_snapshot_digest": candidate_snapshot_digest,
                "selection_batch_digest": selection_batch_digest,
                "selected_candidate_count": len(frozen_candidates),
            }
        ),
        idempotency_key=encode_forward_session_batch_key(
            session_date=session.session_date,
            candidate_snapshot_digest=candidate_snapshot_digest,
            selection_batch_digest=selection_batch_digest,
            selected_candidate_count=len(frozen_candidates),
        ),
    )
    return frozen_candidates


def _next_trading_date(session_date: date) -> date:
    return trading_day_offset(session_date, 1)


def _completed_outcome(
    resolved_on: date,
    *,
    stress_cost_pct: Decimal = Decimal("0.2"),
) -> RankingV3ForwardOutcomeInput:
    return RankingV3ForwardOutcomeInput(
        status="completed",
        resolved_on=resolved_on,
        gross_return_pct=Decimal("2.0"),
        transaction_cost_pct=Decimal("0.1"),
        stress_transaction_cost_pct=stress_cost_pct,
        benchmark_return_pct=Decimal("0.5"),
        max_drawdown_pct=Decimal("-1.0"),
        data_revision=DATA_REVISION,
    )


def _historical_input() -> RankingV3HistoricalGatesInput:
    return RankingV3HistoricalGatesInput(
        validation_run_id="historical-v6",
        data_revision=DATA_REVISION,
        gate_results={
            "paired_oos": "pass",
            "stress_cost": "pass",
            "maximum_drawdown": "pass",
            "coverage": "pass",
        },
        source_proof_digest="a" * 64,
        source_generated_at=NOW - timedelta(days=1),
    )


def _pbo_input() -> RankingV3PBOInput:
    return RankingV3PBOInput(
        validation_run_id="historical-v6",
        data_revision=DATA_REVISION,
        probability=Decimal("0.10"),
        matrix_digest="b" * 64,
        fold_count=16,
        method="cscv",
        source_proof_digest="c" * 64,
        source_generated_at=NOW - timedelta(days=1),
    )


def _portfolio_input(
    as_of_session_date: date,
    *,
    source_candidate_digest: str,
    drawdown: bool = False,
) -> RankingV3ForwardPortfolioInput:
    base_middle = Decimal("80") if drawdown else Decimal("98")
    stress_middle = Decimal("78") if drawdown else Decimal("97")
    equity_curve = (
        RankingV3ForwardEquityPoint(
            date=as_of_session_date - timedelta(days=2),
            equity=Decimal("100"),
            cash=Decimal("100"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
        RankingV3ForwardEquityPoint(
            date=as_of_session_date - timedelta(days=1),
            equity=base_middle,
            cash=base_middle,
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("-20") if drawdown else Decimal("-2"),
        ),
        RankingV3ForwardEquityPoint(
            date=as_of_session_date,
            equity=Decimal("120"),
            cash=Decimal("120"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
    )
    stress_equity_curve = (
        RankingV3ForwardEquityPoint(
            date=as_of_session_date - timedelta(days=2),
            equity=Decimal("100"),
            cash=Decimal("100"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
        RankingV3ForwardEquityPoint(
            date=as_of_session_date - timedelta(days=1),
            equity=stress_middle,
            cash=stress_middle,
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("-22") if drawdown else Decimal("-3"),
        ),
        RankingV3ForwardEquityPoint(
            date=as_of_session_date,
            equity=Decimal("118"),
            cash=Decimal("118"),
            market_value=Decimal("0"),
            open_positions=0,
            drawdown_pct=Decimal("0"),
        ),
    )
    return RankingV3ForwardPortfolioInput(
        validation_run_id="historical-v6",
        data_revision=DATA_REVISION,
        as_of_session_date=as_of_session_date,
        benchmark_id=BENCHMARK_ID,
        provider="free",
        execution_profile="a-share-daily-v1",
        initial_equity=Decimal("100"),
        final_equity=Decimal("120"),
        stress_final_equity=Decimal("118"),
        benchmark_final_equity=Decimal("105"),
        net_return_pct=Decimal("20"),
        stress_net_return_pct=Decimal("18"),
        benchmark_return_pct=Decimal("5"),
        benchmark_excess_pct=Decimal("15"),
        stress_benchmark_excess_pct=Decimal("13"),
        maximum_drawdown_pct=Decimal("-20") if drawdown else Decimal("-2"),
        stress_maximum_drawdown_pct=Decimal("-22") if drawdown else Decimal("-3"),
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


def test_portfolio_evidence_recomputes_curve_digest_and_requires_full_cash():
    item = _portfolio_input(
        date(2026, 8, 31),
        source_candidate_digest="f" * 64,
    )
    tampered_curve = item.model_dump(mode="python")
    tampered_curve["equity_curve"][1]["equity"] = Decimal("99")
    with pytest.raises(ValidationError, match="curve digest mismatch|does not balance"):
        RankingV3ForwardPortfolioInput.model_validate(tampered_curve)

    open_position = item.model_dump(mode="python")
    open_position["equity_curve"][-1]["open_positions"] = 1
    open_position["final_open_positions"] = 1
    open_position["equity_curve_digest"] = stable_digest(
        [
            RankingV3ForwardEquityPoint.model_validate(point).model_dump(mode="json")
            for point in open_position["equity_curve"]
        ]
    )
    with pytest.raises(ValidationError, match="finish fully in cash"):
        RankingV3ForwardPortfolioInput.model_validate(open_position)


def test_portfolio_source_candidate_digest_must_match_active_ledger(tmp_path):
    validator, _repository, _database_url = _validator(tmp_path, "portfolio-source")
    session_date = _trading_dates(1)[0]
    (candidate,) = _freeze_and_record_session(
        validator,
        _session(session_date, 0),
        (_candidate(session_date, 0),),
    )
    validator.record_candidate(
        candidate,
        idempotency_key="portfolio-source-candidate",
    )

    with pytest.raises(RankingV3ForwardStateError, match="does not match the ledger"):
        validator.record_portfolio(
            _portfolio_input(
                session_date,
                source_candidate_digest="f" * 64,
            ),
            idempotency_key="portfolio-source-forgery",
        )


def test_self_consistent_forged_portfolio_curve_and_trade_count_are_rejected(tmp_path):
    authority = _AuthoritativePortfolio()
    validator, repository, _database_url = _validator(
        tmp_path,
        "portfolio-authority",
        portfolio_authority=authority,
    )
    session_date = _trading_dates(1)[0]
    (candidate,) = _freeze_and_record_session(
        validator,
        _session(session_date, 0),
        (_candidate(session_date, 0),),
    )
    validator.record_candidate(candidate, idempotency_key="portfolio-authority-candidate")
    source_digest = forward_candidate_source_digest(
        repository.load_snapshot(validator.identity).candidates
    )
    authoritative = _portfolio_input(
        session_date,
        source_candidate_digest=source_digest,
        drawdown=True,
    )
    authority.recomputed = authoritative
    forged = _portfolio_input(
        session_date,
        source_candidate_digest=source_digest,
    ).model_copy(update={"completed_trade_count": 999})

    with pytest.raises(RankingV3ForwardStateError, match="server-recomputed"):
        validator.record_portfolio(
            forged,
            idempotency_key="forged-self-consistent-portfolio",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("provider", "forged-provider", "server-recomputed"),
        ("execution_profile", "forged-execution", "server-recomputed"),
        ("data_revision", "forged-revision", "data revision"),
    ],
)
def test_portfolio_authority_context_bindings_are_enforced(
    tmp_path,
    field,
    value,
    message,
):
    authority = _AuthoritativePortfolio()
    validator, repository, _database_url = _validator(
        tmp_path,
        f"portfolio-binding-{field}",
        portfolio_authority=authority,
    )
    session_date = _trading_dates(1)[0]
    (candidate,) = _freeze_and_record_session(
        validator,
        _session(session_date, 0),
        (_candidate(session_date, 0),),
    )
    validator.record_candidate(candidate, idempotency_key=f"binding-candidate-{field}")
    source_digest = forward_candidate_source_digest(
        repository.load_snapshot(validator.identity).candidates
    )
    authoritative = _portfolio_input(
        session_date,
        source_candidate_digest=source_digest,
    )
    authority.recomputed = authoritative

    with pytest.raises(
        (RankingV3ForwardStateError, RankingV3ForwardConflictError),
        match=message,
    ):
        validator.record_portfolio(
            authoritative.model_copy(update={field: value}),
            idempotency_key=f"forged-binding-{field}",
        )


def test_partial_frozen_batch_cannot_pass_ledger_integrity(tmp_path):
    validator, _repository, _database_url = _validator(tmp_path, "partial-batch")
    session_date = _trading_dates(1)[0]
    first = _candidate(session_date, 0)
    second = _candidate(session_date, 1)
    first, second = _freeze_and_record_session(
        validator,
        _session(session_date, 0),
        (first, second),
    )
    validator.record_candidate(first, idempotency_key="partial-batch-first")

    result = validator.inspect()

    integrity = next(item for item in result.gates if item.key == "ledger_fact_integrity")
    assert integrity.status == "fail"
    assert result.release_proof is None


def test_legacy_zero_digest_session_cannot_enter_formal_approval(tmp_path):
    validator, _repository, _database_url = _validator(tmp_path, "legacy-zero-batch")
    validator.record_session(
        _session(_trading_dates(1)[0], 0),
        idempotency_key="legacy-session",
    )

    result = validator.inspect()

    integrity = next(item for item in result.gates if item.key == "ledger_fact_integrity")
    assert integrity.status == "fail"
    assert result.release_proof is None


def test_core_validator_rejects_non_protocol_benchmark_before_write(tmp_path):
    validator, repository, _database_url = _validator(tmp_path, "wrong-benchmark")
    session_date = _trading_dates(1)[0]

    with pytest.raises(ValueError, match="frozen protocol release benchmark"):
        validator.record_session(
            _session(session_date, 0, benchmark_id="CN:000905.IDX"),
            idempotency_key="wrong-benchmark-session",
        )

    snapshot = repository.load_snapshot(validator.identity)
    assert snapshot is not None
    assert snapshot.sessions == []


def _populate(
    validator: RankingV3ForwardValidator,
    *,
    session_count: int = 20,
    completed_count: int = 10,
    include_historical: bool = True,
    include_pbo: bool = True,
    mixed_benchmark: bool = False,
    stress_cost_pct: Decimal = Decimal("0.2"),
    invalid_count: int = 0,
    not_triggered_count: int = 0,
    drawdown: bool = False,
) -> None:
    dates = _trading_dates(session_count)
    candidate_items: dict[int, RankingV3ShadowCandidateInput] = {}
    for index, session_date in enumerate(dates):
        selected = ()
        if index < completed_count + invalid_count + not_triggered_count:
            selected = (_candidate(session_date, index).model_copy(update={"rank": 1}),)
        frozen = _freeze_and_record_session(
            validator,
            _session(session_date, index, drawdown=drawdown),
            selected,
        )
        if frozen:
            candidate_items[index] = frozen[0]
    for index in range(completed_count + invalid_count + not_triggered_count):
        item = candidate_items[index]
        if mixed_benchmark and index == 0:
            item = item.model_copy(update={"benchmark_id": "CN:000905.IDX"})
        validator.record_candidate(
            item,
            idempotency_key=f"candidate-{index}",
        )
        if index < completed_count:
            outcome = _completed_outcome(
                item.maturity_session_date,
                stress_cost_pct=stress_cost_pct,
            )
        elif index < completed_count + invalid_count:
            outcome = RankingV3ForwardOutcomeInput(
                status="invalid",
                resolved_on=item.maturity_session_date,
                data_revision=DATA_REVISION,
                reason="missing adjusted close",
            )
        else:
            outcome = RankingV3ForwardOutcomeInput(
                status="not_triggered",
                resolved_on=item.maturity_session_date,
                benchmark_return_pct=Decimal("2"),
                data_revision=DATA_REVISION,
                reason="entry was never reached",
            )
        validator.finalize_candidate(
            item.candidate_id,
            outcome,
            idempotency_key=f"outcome-{index}",
        )
    if include_historical:
        validator.record_historical_gates(
            _historical_input(),
            idempotency_key="historical-gates-v6",
        )
    if include_pbo:
        validator.record_pbo(
            _pbo_input(),
            idempotency_key="pbo-v6",
        )
    snapshot = validator.store.load_snapshot(validator.identity)
    assert snapshot is not None
    validator.record_portfolio(
        _portfolio_input(
            dates[-1],
            source_candidate_digest=forward_candidate_source_digest(snapshot.candidates),
            drawdown=drawdown,
        ),
        idempotency_key="portfolio-v6",
    )


def test_approved_proof_is_stable_authoritative_and_freezes_the_ledger(tmp_path):
    validator, repository, database_url = _validator(tmp_path)
    _populate(validator)

    result = validator.evaluate()
    repeated = validator.evaluate()

    assert result.status == "approved"
    assert result.release_proof is not None
    assert repeated.release_proof == result.release_proof
    assert result.release_proof.proof_digest == stable_release_proof_digest(result.release_proof)
    assert result.release_proof.generated_at == NOW
    assert result.release_proof.identity == validator.identity
    assert result.release_proof.data_revision == DATA_REVISION
    assert result.release_proof.ledger_evidence_digest
    assert result.metrics.session_count == 20
    assert result.metrics.completed_trade_count == 10
    assert result.metrics.valid_outcome_coverage_ratio == Decimal("1")
    assert result.metrics.common_benchmark_id == BENCHMARK_ID
    assert result.metrics.mean_benchmark_excess_pct == Decimal("1.4")
    assert result.metrics.mean_stress_benchmark_excess_pct == Decimal("1.3")
    assert all(gate.status == "pass" for gate in result.gates)

    validation = validator.validate_release_proof(
        result.release_proof.proof_digest,
        expected_data_revision=DATA_REVISION,
    )
    assert validation.valid is True
    assert validation.proof == result.release_proof
    assert (
        validator.validate_release_proof("f" * 64).reason
        == "release proof is not present in the authoritative store"
    )
    first_stored_session = repository.load_snapshot(validator.identity).sessions[0]
    replayed = validator.record_session(
        RankingV3ForwardSessionInput.model_validate(
            {
                **first_stored_session.model_dump(
                    mode="python",
                    exclude={"identity", "idempotency_key", "fact_digest", "created_at"},
                ),
                "candidate_snapshot_digest": first_stored_session.candidate_snapshot_digest,
                "selection_batch_digest": first_stored_session.selection_batch_digest,
                "selected_candidate_count": first_stored_session.selected_candidate_count,
            }
        ),
        idempotency_key=first_stored_session.idempotency_key,
    )
    assert replayed.session_date == _trading_dates(20)[0]

    with pytest.raises(RankingV3ForwardStateError, match="approved"):
        validator.record_session(
            _session(_trading_dates(21)[-1], 20),
            idempotency_key="after-approval",
        )

    engine = create_db_engine(database_url)
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text("UPDATE ranking_v3_forward_release_proofs SET data_revision = 'tampered'")
        )
    with pytest.raises(
        DBAPIError,
        match="ranking_v3_forward_release_proofs rows are immutable",
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM ranking_v3_forward_release_proofs "
                    "WHERE proof_digest = :proof_digest"
                ),
                {"proof_digest": result.release_proof.proof_digest},
            )
    stored = repository.get_release_proof(result.release_proof.proof_digest)
    assert stored == result.release_proof
    with pytest.raises(
        DBAPIError,
        match="ranking_v3_forward_sessions rows are immutable",
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ranking_v3_forward_sessions "
                    "SET benchmark_id = 'CN:TAMPERED' "
                    "WHERE session_date = '2026-07-27'"
                )
            )
    assert validator.validate_release_proof(result.release_proof.proof_digest).valid is True
    wrong_key_validator = RankingV3ForwardValidator(
        repository,
        build_ranking_v3_protocol(),
        evidence_authority=_AuthoritativeEvidence(),
        portfolio_authority=_AuthoritativePortfolio(),
        attestor=RankingV3Attestor(b"x" * 32),
    )
    invalid_signature = wrong_key_validator.validate_release_proof(
        result.release_proof.proof_digest
    )
    assert invalid_signature.valid is False
    assert invalid_signature.reason == "release proof server attestation is invalid"
    with pytest.raises(
        RankingV3ForwardStateError,
        match="server attestation is invalid",
    ):
        wrong_key_validator.inspect()


@pytest.mark.parametrize(
    ("case", "kwargs", "failed_gate"),
    [
        (
            "missing-history",
            {"include_historical": False},
            "historical_gates_proof",
        ),
        ("missing-pbo", {"include_pbo": False}, "pbo_proof"),
        (
            "stress-cost",
            {"stress_cost_pct": Decimal("3.0")},
            "stress_cost_benchmark_excess",
        ),
        ("drawdown", {"drawdown": True}, "maximum_drawdown"),
        (
            "coverage",
            {"invalid_count": 1},
            "valid_outcome_coverage",
        ),
        (
            "completed-trades",
            {"completed_count": 9},
            "completed_trades",
        ),
    ],
)
def test_each_required_evidence_gap_remains_fail_closed(
    tmp_path,
    case,
    kwargs,
    failed_gate,
):
    validator, repository, _database_url = _validator(tmp_path, case)
    _populate(validator, **kwargs)

    result = validator.evaluate()

    assert result.status == "pending"
    assert result.release_proof is None
    assert repository.get_release_proof("0" * 64) is None
    gate = next(item for item in result.gates if item.key == failed_gate)
    assert gate.status != "pass"


def test_missing_cost_or_drawdown_cannot_be_recorded_as_a_completed_trade():
    with pytest.raises(ValidationError, match="returns, costs, benchmark and drawdown"):
        RankingV3ForwardOutcomeInput(
            status="completed",
            resolved_on=date(2026, 8, 1),
            gross_return_pct=Decimal("1"),
            transaction_cost_pct=Decimal("0.1"),
            benchmark_return_pct=Decimal("0.2"),
            data_revision=DATA_REVISION,
        )
    with pytest.raises(ValidationError, match="benchmark opportunity return"):
        RankingV3ForwardOutcomeInput(
            status="not_triggered",
            resolved_on=date(2026, 8, 1),
            data_revision=DATA_REVISION,
        )
    with pytest.raises(ValidationError, match="cannot report financial returns"):
        RankingV3ForwardOutcomeInput(
            status="invalid",
            resolved_on=date(2026, 8, 1),
            benchmark_return_pct=Decimal("0.2"),
            data_revision=DATA_REVISION,
        )
    with pytest.raises(ValidationError, match="stress cost cannot be lower"):
        RankingV3ForwardOutcomeInput(
            status="completed",
            resolved_on=date(2026, 8, 1),
            gross_return_pct=Decimal("1"),
            transaction_cost_pct=Decimal("0.2"),
            stress_transaction_cost_pct=Decimal("0.1"),
            benchmark_return_pct=Decimal("0.2"),
            max_drawdown_pct=Decimal("-1"),
            data_revision=DATA_REVISION,
        )


def test_candidate_maturity_must_be_strictly_later_than_signal_session():
    session_date = date(2026, 8, 3)

    with pytest.raises(
        ValidationError,
        match="maturity_session_date must be later than session_date",
    ):
        RankingV3ShadowCandidateInput(
            candidate_id="same-day-maturity",
            source_snapshot_id="server-snapshot-same-day",
            session_date=session_date,
            maturity_session_date=session_date,
            instrument_id="CN:000001",
            strategy_id="ranking-v3",
            rank=1,
            score=Decimal("0.8"),
            benchmark_id=BENCHMARK_ID,
            data_revision=DATA_REVISION,
            selection_digest=stable_digest({"candidate_id": "same-day-maturity"}),
        )


def test_candidate_requires_a_server_source_snapshot_reference():
    session_date = date(2026, 8, 3)

    with pytest.raises(ValidationError, match="source_snapshot_id"):
        RankingV3ShadowCandidateInput(
            candidate_id="missing-source-snapshot",
            source_snapshot_id="",
            session_date=session_date,
            maturity_session_date=_next_trading_date(session_date),
            instrument_id="CN:000001",
            strategy_id="ranking-v3",
            rank=1,
            score=Decimal("0.8"),
            benchmark_id=BENCHMARK_ID,
            data_revision=DATA_REVISION,
            selection_digest=stable_digest({"candidate_id": "missing-source-snapshot"}),
        )


def test_source_snapshot_survives_restart_and_replay(tmp_path):
    validator, repository, database_url = _validator(tmp_path, "source-round-trip")
    session_date = _trading_dates(1)[0]
    (candidate,) = _freeze_and_record_session(
        validator,
        _session(session_date, 0),
        (_candidate(session_date, 0),),
    )
    first = validator.record_candidate(
        candidate,
        idempotency_key="source-round-trip-candidate",
    )

    restarted_repository = RankingV3ForwardRepository(create_session_factory(database_url))
    restored = restarted_repository.load_snapshot(validator.identity)
    assert restored is not None
    assert restored.candidates[0].source_snapshot_id == candidate.source_snapshot_id
    replayed = restarted_repository.record_candidate(
        validator.identity,
        candidate,
        idempotency_key="source-round-trip-candidate",
        fact_digest=stable_digest(candidate),
    )

    assert replayed == first
    assert replayed.fact_digest == stable_digest(candidate)


def test_candidate_source_snapshot_is_database_immutable(tmp_path):
    validator, repository, database_url = _validator(tmp_path, "source-tamper")
    session_date = _trading_dates(1)[0]
    (candidate,) = _freeze_and_record_session(
        validator,
        _session(session_date, 0),
        (_candidate(session_date, 0),),
    )
    validator.record_candidate(
        candidate,
        idempotency_key="source-tamper-candidate",
    )
    engine = create_db_engine(database_url)
    with pytest.raises(
        DBAPIError,
        match="candidate selection facts are immutable",
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ranking_v3_forward_candidates "
                    "SET source_snapshot_id = 'server-snapshot-tampered'"
                )
            )

    snapshot = repository.load_snapshot(validator.identity)
    assert snapshot is not None
    assert snapshot.candidates[0].source_snapshot_id == candidate.source_snapshot_id
    replayed = repository.record_candidate(
        validator.identity,
        candidate,
        idempotency_key="source-tamper-candidate",
        fact_digest=stable_digest(candidate),
    )
    assert replayed.source_snapshot_id == candidate.source_snapshot_id


def test_database_guards_allow_one_outcome_transition_and_freeze_forward_facts(tmp_path):
    validator, repository, database_url = _validator(tmp_path, "database-immutability")
    session_date = _trading_dates(1)[0]
    maturity_date = _next_trading_date(session_date)
    source_snapshot_id = "scan-forward:card-forward"
    candidate = _candidate(session_date, 0).model_copy(
        update={
            "source_snapshot_id": source_snapshot_id,
            "maturity_session_date": maturity_date,
        }
    )
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO scan_runs ("
                "run_id, provider, mode, symbols, scanned, cards, data_health, created_at"
                ") VALUES ("
                "'scan-forward', 'free', 'full-market', '[]', 1, 1, '{}', :created_at"
                ")"
            ),
            {"created_at": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO opportunity_snapshots ("
                "snapshot_id, run_id, card_id, instrument_id, market, status, "
                "signal_date, latest_close, primary_strategy_id, score, strategy_score, "
                "rank_score, trigger_price, initial_stop, target_1, card_json, created_at"
                ") VALUES ("
                ":snapshot_id, 'scan-forward', 'card-forward', 'CN:000001', 'CN', "
                "'ready', :signal_date, 10, 'ranking-v3', 0.8, 0.8, 0.8, "
                "10, 9, 12, '{}', :created_at"
                ")"
            ),
            {
                "snapshot_id": source_snapshot_id,
                "signal_date": session_date,
                "created_at": NOW,
            },
        )

    (candidate,) = _freeze_and_record_session(
        validator,
        _session(session_date, 0),
        (candidate,),
    )
    validator.record_candidate(candidate, idempotency_key="immutable-candidate")

    for statement in (
        "UPDATE ranking_v3_forward_sessions SET benchmark_id = 'CN:TAMPERED'",
        "DELETE FROM ranking_v3_forward_sessions",
    ):
        with pytest.raises(
            DBAPIError,
            match="ranking_v3_forward_sessions rows are immutable",
        ):
            with engine.begin() as connection:
                connection.execute(text(statement))

    with pytest.raises(
        DBAPIError,
        match="candidate selection facts are immutable",
    ):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE ranking_v3_forward_candidates SET selection_digest = :digest"),
                {"digest": "0" * 64},
            )
    with pytest.raises(
        DBAPIError,
        match="pending outcome cannot be partially written",
    ):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE ranking_v3_forward_candidates SET outcome_reason = 'partial'")
            )
    with pytest.raises(
        DBAPIError,
        match="terminal outcome is incomplete",
    ):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE ranking_v3_forward_candidates SET outcome_status = 'invalid'")
            )
    with pytest.raises(
        DBAPIError,
        match="forward candidates cannot be deleted",
    ):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM ranking_v3_forward_candidates"))

    for statement in (
        "UPDATE opportunity_snapshots SET status = 'tampered' WHERE snapshot_id = :snapshot_id",
        "DELETE FROM opportunity_snapshots WHERE snapshot_id = :snapshot_id",
    ):
        with pytest.raises(
            DBAPIError,
            match="opportunity snapshot referenced by Ranking V3 forward evidence is immutable",
        ):
            with engine.begin() as connection:
                connection.execute(text(statement), {"snapshot_id": source_snapshot_id})

    validator.record_session(
        _session(maturity_date, 1),
        idempotency_key="immutable-maturity-session",
    )
    outcome = _completed_outcome(maturity_date)
    finalized = validator.finalize_candidate(
        candidate.candidate_id,
        outcome,
        idempotency_key="immutable-outcome",
    )
    replayed = validator.finalize_candidate(
        candidate.candidate_id,
        outcome,
        idempotency_key="immutable-outcome",
    )
    assert finalized == replayed
    assert finalized.outcome_status == "completed"

    with pytest.raises(
        DBAPIError,
        match="terminal outcome is immutable",
    ):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE ranking_v3_forward_candidates SET outcome_reason = 'tampered'")
            )

    evidence = validator.record_historical_gates(
        _historical_input(),
        idempotency_key="immutable-evidence",
    )
    for statement in (
        "UPDATE ranking_v3_forward_gate_evidence SET passed = 0 "
        "WHERE evidence_digest = :evidence_digest",
        "DELETE FROM ranking_v3_forward_gate_evidence WHERE evidence_digest = :evidence_digest",
    ):
        with pytest.raises(
            DBAPIError,
            match="ranking_v3_forward_gate_evidence rows are immutable",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(statement),
                    {"evidence_digest": evidence.evidence_digest},
                )

    snapshot = repository.load_snapshot(validator.identity)
    assert snapshot is not None
    assert snapshot.candidates[0] == finalized


def test_candidate_cannot_finalize_before_stored_maturity_session(tmp_path):
    validator, _repository, _database_url = _validator(tmp_path)
    session_date = _trading_dates(1)[0]
    (candidate,) = _freeze_and_record_session(
        validator,
        _session(session_date, 0),
        (_candidate(session_date, 0),),
    )
    validator.record_candidate(candidate, idempotency_key="candidate-early")
    validator.record_session(
        _session(candidate.maturity_session_date, 1),
        idempotency_key="session-maturity",
    )

    with pytest.raises(
        ValueError,
        match="resolved_on cannot precede maturity_session_date",
    ):
        validator.finalize_candidate(
            candidate.candidate_id,
            _completed_outcome(session_date),
            idempotency_key="outcome-too-early",
        )

    finalized = validator.finalize_candidate(
        candidate.candidate_id,
        _completed_outcome(candidate.maturity_session_date),
        idempotency_key="outcome-on-maturity",
    )
    assert finalized.resolved_on == candidate.maturity_session_date


def test_not_triggered_cash_includes_benchmark_opportunity_cost(tmp_path):
    validator, _repository, _database_url = _validator(tmp_path)
    _populate(validator, not_triggered_count=1)

    result = validator.evaluate()

    assert result.metrics.completed_trade_count == 10
    assert result.metrics.valid_outcome_count == 11
    assert result.metrics.mean_benchmark_excess_pct == Decimal("1.090909090909090909090909091")
    assert result.metrics.mean_stress_benchmark_excess_pct == Decimal("1.0")


def test_gate_evidence_is_denied_without_a_server_authority(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'deny-authority.db'}"
    initialize_database(database_url)
    repository = RankingV3ForwardRepository(create_session_factory(database_url))
    validator = RankingV3ForwardValidator(
        repository,
        build_ranking_v3_protocol(),
        now=lambda: NOW,
    )
    validator.ensure_ledger(DATA_REVISION)

    with pytest.raises(RankingV3ForwardStateError, match="authoritative"):
        validator.record_historical_gates(
            _historical_input(),
            idempotency_key="client-claimed-history",
        )
    with pytest.raises(RankingV3ForwardStateError, match="authoritative"):
        validator.record_pbo(
            _pbo_input(),
            idempotency_key="client-claimed-pbo",
        )


def test_exact_retries_are_idempotent_and_conflicting_retries_are_rejected(tmp_path):
    validator, repository, _database_url = _validator(tmp_path)
    session_date = _trading_dates(1)[0]
    candidate_item = _candidate(session_date, 0)
    (candidate_item,) = _freeze_and_record_session(
        validator,
        _session(session_date, 0),
        (candidate_item,),
    )
    snapshot = repository.load_snapshot(validator.identity)
    first_session = snapshot.sessions[0]
    session_item = RankingV3ForwardSessionInput.model_validate(
        {
            **first_session.model_dump(
                mode="python",
                exclude={"identity", "idempotency_key", "fact_digest", "created_at"},
            ),
            "candidate_snapshot_digest": first_session.candidate_snapshot_digest,
            "selection_batch_digest": first_session.selection_batch_digest,
            "selected_candidate_count": first_session.selected_candidate_count,
        }
    )
    replay_session = validator.record_session(
        session_item,
        idempotency_key=first_session.idempotency_key,
    )
    assert replay_session == first_session

    first_candidate = validator.record_candidate(
        candidate_item,
        idempotency_key="candidate-once",
    )
    replay_candidate = validator.record_candidate(
        candidate_item,
        idempotency_key="candidate-once",
    )
    assert replay_candidate == first_candidate
    validator.record_session(
        _session(candidate_item.maturity_session_date, 1),
        idempotency_key="maturity-session",
    )

    outcome = _completed_outcome(candidate_item.maturity_session_date)
    first_outcome = validator.finalize_candidate(
        candidate_item.candidate_id,
        outcome,
        idempotency_key="outcome-once",
    )
    replay_outcome = validator.finalize_candidate(
        candidate_item.candidate_id,
        outcome,
        idempotency_key="outcome-once",
    )
    assert replay_outcome == first_outcome
    assert first_outcome.net_return_pct == Decimal("1.9")
    assert first_outcome.stress_benchmark_excess_pct == Decimal("1.3")

    changed = session_item.model_copy(update={"benchmark_return_pct": Decimal("0.3")})
    with pytest.raises(RankingV3ForwardConflictError, match="immutable|idempotency"):
        validator.record_session(changed, idempotency_key="session-once")

    snapshot = repository.load_snapshot(validator.identity)
    assert snapshot is not None
    assert len(snapshot.sessions) == 2
    assert len(snapshot.candidates) == 1


def test_identity_tuple_isolates_protocol_digest_and_model_version(tmp_path):
    _validator_instance, repository, _database_url = _validator(tmp_path)
    first = RankingV3ForwardIdentity(
        protocol_id="QAGENT-RANK-V3",
        protocol_digest="1" * 64,
        model_version="v3-a",
    )
    second = RankingV3ForwardIdentity(
        protocol_id="QAGENT-RANK-V3",
        protocol_digest="2" * 64,
        model_version="v3-b",
    )

    repository.ensure_ledger(first, "revision-a")
    repository.ensure_ledger(second, "revision-b")
    shared_date = date(2026, 7, 27)
    first_session = _session(shared_date, 0).model_copy(update={"data_revision": "revision-a"})
    second_session = _session(shared_date, 0).model_copy(update={"data_revision": "revision-b"})
    repository.record_session(
        first,
        first_session,
        idempotency_key="shared-daily-session-key",
        fact_digest=stable_digest(first_session),
    )
    repository.record_session(
        second,
        second_session,
        idempotency_key="shared-daily-session-key",
        fact_digest=stable_digest(second_session),
    )

    assert repository.load_snapshot(first).ledger.data_revision == "revision-a"
    assert repository.load_snapshot(second).ledger.data_revision == "revision-b"
    assert len(repository.load_snapshot(first).sessions) == 1
    assert len(repository.load_snapshot(second).sessions) == 1
    with pytest.raises(RankingV3ForwardConflictError, match="data revision"):
        repository.ensure_ledger(first, "revision-b")


def test_unapproved_ledger_is_rejected_at_the_protocol_session_deadline(tmp_path):
    validator, _repository, _database_url = _validator(tmp_path)
    maximum_sessions = build_ranking_v3_protocol().thresholds.maximum_forward_shadow_sessions
    _populate(
        validator,
        session_count=maximum_sessions,
        completed_count=0,
        include_historical=False,
        include_pbo=False,
    )

    result = validator.evaluate()

    assert result.status == "rejected"
    assert result.release_proof is None
    assert result.reasons
    with pytest.raises(RankingV3ForwardStateError, match="rejected"):
        validator.record_historical_gates(
            _historical_input(),
            idempotency_key="too-late",
        )


def test_session_window_respects_frozen_protocol_bounds(tmp_path):
    thresholds = build_ranking_v3_protocol().thresholds
    below_minimum = thresholds.minimum_forward_shadow_sessions - 1
    validator, _repository, _database_url = _validator(tmp_path)
    _populate(validator, session_count=below_minimum)

    result = validator.evaluate()

    assert result.status == "pending"
    assert (
        next(gate for gate in result.gates if gate.key == "forward_sessions").status
        == "insufficient"
    )

    validator_at_limit, _repository, _database_url = _validator(tmp_path, "at-limit")
    maximum_sessions = thresholds.maximum_forward_shadow_sessions
    dates = _trading_dates(maximum_sessions + 1)
    for index, session_date in enumerate(dates[:maximum_sessions]):
        validator_at_limit.record_session(
            _session(session_date, index),
            idempotency_key=f"limit-session-{index}",
        )
    with pytest.raises(RankingV3ForwardStateError, match="maximum session"):
        validator_at_limit.record_session(
            _session(dates[maximum_sessions], maximum_sessions),
            idempotency_key="limit-session-overflow",
        )


def test_forward_sessions_require_a_share_trading_dates_without_gaps(tmp_path):
    validator, _repository, _database_url = _validator(tmp_path)
    dates = _trading_dates(3)
    validator.record_session(
        _session(dates[0], 0),
        idempotency_key="first-session",
    )

    with pytest.raises(RankingV3ForwardConflictError, match="consecutive"):
        validator.record_session(
            _session(dates[2], 2),
            idempotency_key="skipped-session",
        )
    with pytest.raises(ValueError, match="A-share trading session"):
        validator.record_session(
            _session(date(2026, 8, 1), 3),
            idempotency_key="weekend-session",
        )


def test_latest_authoritative_gate_evidence_supersedes_an_earlier_failure(tmp_path):
    validator, repository, _database_url = _validator(tmp_path)
    _populate(validator, include_pbo=False)
    failed_pbo = _pbo_input().model_copy(update={"probability": Decimal("0.40")})

    first = validator.record_pbo(failed_pbo, idempotency_key="pbo-failed")
    second = validator.record_pbo(_pbo_input(), idempotency_key="pbo-passed")
    result = validator.evaluate()

    assert first.sequence == 1
    assert first.passed is False
    assert second.sequence == 2
    assert second.passed is True
    assert result.status == "approved"
    snapshot = repository.load_snapshot(validator.identity)
    assert snapshot is not None
    assert [item.sequence for item in snapshot.evidence if item.evidence_kind == "pbo"] == [
        1,
        2,
    ]


def test_initialize_database_adds_forward_tables_and_immutability_triggers(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-forward.db'}"
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_marker (marker INTEGER PRIMARY KEY)"))

    db._initialized_urls.discard(database_url)
    migrated = initialize_database(database_url)
    inspector = inspect(migrated)

    assert {
        "ranking_v3_forward_ledgers",
        "ranking_v3_forward_sessions",
        "ranking_v3_forward_candidates",
        "ranking_v3_forward_gate_evidence",
        "ranking_v3_forward_release_proofs",
    }.issubset(inspector.get_table_names())
    with migrated.connect() as connection:
        triggers = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' "
                    "AND (name LIKE 'trg_ranking_v3_forward_%' "
                    "OR name LIKE 'trg_opportunity_snapshots_forward_reference_%')"
                )
            )
        }
    assert {
        "trg_ranking_v3_forward_sessions_immutable_update",
        "trg_ranking_v3_forward_sessions_immutable_delete",
        "trg_ranking_v3_forward_candidates_selection_immutable_update",
        "trg_ranking_v3_forward_candidates_pending_outcome_guard_update",
        "trg_ranking_v3_forward_candidates_terminal_shape_update",
        "trg_ranking_v3_forward_candidates_terminal_immutable_update",
        "trg_ranking_v3_forward_candidates_immutable_delete",
        "trg_ranking_v3_forward_gate_evidence_immutable_update",
        "trg_ranking_v3_forward_gate_evidence_immutable_delete",
        "trg_ranking_v3_forward_release_proofs_immutable_update",
        "trg_ranking_v3_forward_release_proofs_immutable_delete",
        "trg_opportunity_snapshots_forward_reference_update",
        "trg_opportunity_snapshots_forward_reference_delete",
    }.issubset(triggers)
