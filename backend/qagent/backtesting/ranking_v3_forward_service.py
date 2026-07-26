from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from qagent.backtesting.ranking_v3_evidence import (
    RankingV3RepositoryEvidenceAuthority,
    ranking_v3_data_revision,
    ranking_v3_historical_gate_results,
    ranking_v3_historical_source_digest,
    ranking_v3_pbo_source_digest,
)
from qagent.backtesting.ranking_v3_forward import (
    ForwardLedgerStatus,
    RankingV3ForwardEvaluation,
    RankingV3ForwardOutcomeInput,
    RankingV3ForwardPortfolioInput,
    RankingV3ForwardPortfolioAuthority,
    RankingV3ForwardSelectionBatchInput,
    RankingV3ForwardSessionInput,
    RankingV3ForwardLedgerSnapshot,
    RankingV3ForwardStore,
    RankingV3ForwardValidator,
    RankingV3HistoricalGatesInput,
    RankingV3PBOInput,
    RankingV3ShadowCandidate,
    RankingV3ShadowCandidateInput,
    encode_forward_session_batch_key,
    stable_digest,
)
from qagent.backtesting.ranking_v3_protocol import (
    RankingV3Protocol,
    ranking_v3_protocol_digest_is_valid,
)
from qagent.market.calendars import trading_day_offset, trading_sessions_in_range


class RankingV3ForwardRunRepository(Protocol):
    def get_walk_forward_run(self, run_id: str) -> object | None: ...


class RankingV3ForwardCandidateFact(BaseModel):
    """Server-produced V3 selection facts without caller-controlled ledger identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_snapshot_id: str = Field(min_length=1, max_length=192)
    instrument_id: str = Field(min_length=1, max_length=32)
    strategy_id: str = Field(min_length=1, max_length=96)
    rank: int = Field(ge=1)
    score: Decimal
    benchmark_id: str = Field(min_length=1, max_length=64)
    selection_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RankingV3ForwardOutcomeFact(BaseModel):
    """A mature, server-resolved outcome; resolution date and revision are injected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=160)
    status: Literal["completed", "not_triggered", "invalid", "censored"]
    gross_return_pct: Decimal | None = None
    transaction_cost_pct: Decimal | None = Field(default=None, ge=0)
    stress_transaction_cost_pct: Decimal | None = Field(default=None, ge=0)
    benchmark_return_pct: Decimal | None = None
    max_drawdown_pct: Decimal | None = Field(default=None, le=0)
    reason: str = ""


class RankingV3ForwardDayFacts(BaseModel):
    """Immutable facts supplied by the server-side daily shadow pipeline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_run_id: str = Field(min_length=1, max_length=128)
    session_date: date
    benchmark_id: str = Field(min_length=1, max_length=64)
    benchmark_return_pct: Decimal
    portfolio_equity: Decimal = Field(gt=0)
    stress_portfolio_equity: Decimal = Field(gt=0)
    benchmark_equity: Decimal = Field(gt=0)
    candidate_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_batch_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_candidate_count: int = Field(ge=0)
    candidates: tuple[RankingV3ForwardCandidateFact, ...] = ()
    mature_outcomes: tuple[RankingV3ForwardOutcomeFact, ...] = ()
    portfolio_evidence: RankingV3ForwardPortfolioInput | None = None

    @model_validator(mode="after")
    def validate_daily_facts(self):
        ranks = [item.rank for item in self.candidates]
        instruments = [item.instrument_id for item in self.candidates]
        outcome_ids = [item.candidate_id for item in self.mature_outcomes]
        if len(ranks) != len(set(ranks)):
            raise ValueError("daily V3 candidate ranks must be unique")
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("daily V3 candidate ranks must be contiguous from one")
        if len(instruments) != len(set(instruments)):
            raise ValueError("daily V3 candidate instruments must be unique")
        if len(outcome_ids) != len(set(outcome_ids)):
            raise ValueError("mature outcome candidate ids must be unique")
        if self.selected_candidate_count != len(self.candidates):
            raise ValueError("selected candidate count does not match the frozen daily batch")
        if any(item.benchmark_id != self.benchmark_id for item in self.candidates):
            raise ValueError("daily V3 candidates must use the session benchmark")
        return self


class RankingV3ForwardFactAuthority(Protocol):
    """Build server-authoritative facts for one validated forward session."""

    def build_day_facts(
        self,
        *,
        validation_run_id: str,
        session_date: date,
        run: object,
        ranking_v3: Mapping[str, object],
        protocol: RankingV3Protocol,
        data_revision: str,
    ) -> RankingV3ForwardDayFacts: ...

    def recompute_portfolio_evidence(
        self,
        *,
        validation_run_id: str,
        session_date: date,
        run: object,
        ranking_v3: Mapping[str, object],
        protocol: RankingV3Protocol,
        data_revision: str,
        ledger: RankingV3ForwardLedgerSnapshot,
        submitted: RankingV3ForwardPortfolioInput,
        frozen_batch: RankingV3ForwardSelectionBatchInput,
    ) -> RankingV3ForwardPortfolioInput | None: ...


class _ServicePortfolioAuthority(RankingV3ForwardPortfolioAuthority):
    def __init__(
        self,
        *,
        authority: RankingV3ForwardFactAuthority,
        validation_run_id: str,
        session_date: date,
        run: object,
        ranking_v3: Mapping[str, object],
        protocol: RankingV3Protocol,
        data_revision: str,
        frozen_batch: RankingV3ForwardSelectionBatchInput,
    ):
        self.authority = authority
        self.validation_run_id = validation_run_id
        self.session_date = session_date
        self.run = run
        self.ranking_v3 = ranking_v3
        self.protocol = protocol
        self.data_revision = data_revision
        self.frozen_batch = frozen_batch

    def recompute_portfolio(
        self,
        identity,
        protocol,
        snapshot,
        submitted,
    ) -> RankingV3ForwardPortfolioInput | None:
        if protocol != self.protocol or identity.protocol_digest != protocol.protocol_digest:
            return None
        verifier = getattr(self.authority, "recompute_portfolio_evidence", None)
        if not callable(verifier):
            return None
        return verifier(
            validation_run_id=self.validation_run_id,
            session_date=self.session_date,
            run=self.run,
            ranking_v3=self.ranking_v3,
            protocol=self.protocol,
            data_revision=self.data_revision,
            ledger=snapshot,
            submitted=submitted,
            frozen_batch=self.frozen_batch,
        )


class RankingV3ForwardDayResult(BaseModel):
    """Shadow-only orchestration result. This service never publishes official state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_run_id: str
    data_revision: str
    protocol_id: str
    protocol_digest: str
    model_version: str
    session_date: date
    ledger_status: ForwardLedgerStatus
    recorded_candidate_ids: tuple[str, ...]
    finalized_candidate_ids: tuple[str, ...]
    shadow_state: Literal[
        "shadow_unpublished",
        "shadow_rejected",
        "approved_proof_available",
    ]
    official_state_mutated: Literal[False] = False
    release_proof_digest: str | None = None
    evaluation: RankingV3ForwardEvaluation


class RankingV3ForwardService:
    """Orchestrate one authoritative, prospective Ranking V3 shadow ledger."""

    def __init__(
        self,
        store: RankingV3ForwardStore,
        run_repository: RankingV3ForwardRunRepository,
        fact_authority: RankingV3ForwardFactAuthority,
        *,
        now=None,
    ):
        if fact_authority is None:
            raise ValueError("Ranking V3 forward service requires a fact authority")
        self.store = store
        self.run_repository = run_repository
        self.fact_authority = fact_authority
        self._now = now

    def process_day(
        self,
        validation_run_id: str,
        session_date: date,
    ) -> RankingV3ForwardDayResult:
        run, ranking_v3, protocol, data_revision = self._authoritative_context(validation_run_id)
        self._require_trading_session(session_date)
        facts = self._load_authoritative_facts(
            validation_run_id=validation_run_id,
            session_date=session_date,
            run=run,
            ranking_v3=ranking_v3,
            protocol=protocol,
            data_revision=data_revision,
        )
        self._validate_candidates(facts.candidates, protocol)
        self._validate_protocol_benchmark(facts, protocol)

        authority = RankingV3RepositoryEvidenceAuthority(self.run_repository)
        validator = RankingV3ForwardValidator(
            self.store,
            protocol,
            evidence_authority=authority,
            **({"now": self._now} if self._now is not None else {}),
        )
        validator.ensure_ledger(data_revision)
        self._validate_mature_outcome_targets(
            validator,
            facts.session_date,
            facts.mature_outcomes,
        )
        frozen_batch = validator.get_frozen_selection_batch(session_date)
        if frozen_batch is None:
            frozen_batch = RankingV3ForwardSelectionBatchInput.create(
                session_date=facts.session_date,
                benchmark_id=facts.benchmark_id,
                data_revision=data_revision,
                candidate_snapshot_digest=facts.candidate_snapshot_digest,
                selection_batch_digest=facts.selection_batch_digest,
                candidates=tuple(
                    self._candidate_input(
                        protocol,
                        data_revision,
                        facts.session_date,
                        item,
                    )
                    for item in sorted(facts.candidates, key=lambda value: value.rank)
                ),
            )
            frozen_batch = validator.freeze_selection_batch(
                frozen_batch,
                idempotency_key=self._selection_batch_key(protocol, facts.session_date),
            )
        validator = RankingV3ForwardValidator(
            self.store,
            protocol,
            evidence_authority=authority,
            portfolio_authority=_ServicePortfolioAuthority(
                authority=self.fact_authority,
                validation_run_id=validation_run_id,
                session_date=session_date,
                run=run,
                ranking_v3=ranking_v3,
                protocol=protocol,
                data_revision=data_revision,
                frozen_batch=frozen_batch,
            ),
            **({"now": self._now} if self._now is not None else {}),
        )
        validator.record_session(
            RankingV3ForwardSessionInput(
                session_date=facts.session_date,
                benchmark_id=facts.benchmark_id,
                benchmark_return_pct=facts.benchmark_return_pct,
                portfolio_equity=facts.portfolio_equity,
                stress_portfolio_equity=facts.stress_portfolio_equity,
                benchmark_equity=facts.benchmark_equity,
                data_revision=data_revision,
                candidate_snapshot_digest=frozen_batch.candidate_snapshot_digest,
                selection_batch_digest=frozen_batch.selection_batch_digest,
                selected_candidate_count=frozen_batch.selected_candidate_count,
            ),
            idempotency_key=self._session_key_from_batch(frozen_batch),
        )

        recorded = tuple(
            self._record_candidate_input(
                validator,
                protocol,
                item,
            )
            for item in frozen_batch.candidates
        )
        finalized = self._record_mature_outcomes(
            validator,
            data_revision,
            facts.session_date,
            facts.mature_outcomes,
        )
        self._record_authoritative_evidence(
            validator,
            run,
            ranking_v3,
            data_revision,
        )
        if facts.portfolio_evidence is not None:
            validator.record_portfolio(
                facts.portfolio_evidence,
                idempotency_key=(
                    f"ranking-v3-forward:portfolio:{stable_digest(facts.portfolio_evidence)[:32]}"
                ),
            )
        evaluation = validator.evaluate()
        proof_digest = (
            evaluation.release_proof.proof_digest if evaluation.release_proof is not None else None
        )
        return RankingV3ForwardDayResult(
            validation_run_id=validation_run_id,
            data_revision=data_revision,
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.protocol_digest,
            model_version=protocol.model_version,
            session_date=session_date,
            ledger_status=evaluation.status,
            recorded_candidate_ids=tuple(item.candidate_id for item in recorded),
            finalized_candidate_ids=tuple(item.candidate_id for item in finalized),
            shadow_state=self._shadow_state(evaluation),
            release_proof_digest=proof_digest,
            evaluation=evaluation,
        )

    def _load_authoritative_facts(
        self,
        *,
        validation_run_id: str,
        session_date: date,
        run: object,
        ranking_v3: Mapping[str, object],
        protocol: RankingV3Protocol,
        data_revision: str,
    ) -> RankingV3ForwardDayFacts:
        facts = self.fact_authority.build_day_facts(
            validation_run_id=validation_run_id,
            session_date=session_date,
            run=run,
            ranking_v3=ranking_v3,
            protocol=protocol,
            data_revision=data_revision,
        )
        if not isinstance(facts, RankingV3ForwardDayFacts):
            raise TypeError("fact authority returned an invalid Ranking V3 day facts object")
        if facts.validation_run_id != validation_run_id:
            raise ValueError("fact authority returned a mismatched validation run")
        if facts.session_date != session_date:
            raise ValueError("fact authority returned a mismatched session date")
        expected_benchmark_id = (
            protocol.benchmark_definition.forward_release_benchmark_id
        )
        if facts.benchmark_id != expected_benchmark_id:
            raise ValueError(
                "fact authority benchmark does not match the frozen protocol"
            )
        if facts.portfolio_evidence is not None and (
            facts.portfolio_evidence.validation_run_id != validation_run_id
            or facts.portfolio_evidence.data_revision != data_revision
            or facts.portfolio_evidence.as_of_session_date != session_date
            or facts.portfolio_evidence.benchmark_id != expected_benchmark_id
        ):
            raise ValueError("fact authority returned mismatched portfolio evidence")
        return facts

    def _authoritative_context(
        self,
        validation_run_id: str,
    ) -> tuple[object, Mapping[str, object], RankingV3Protocol, str]:
        run = self.run_repository.get_walk_forward_run(validation_run_id)
        if run is None:
            raise LookupError("authoritative walk-forward run does not exist")
        if _text(getattr(run, "run_id", None)) != validation_run_id:
            raise ValueError("walk-forward repository returned a mismatched run")
        if _text(getattr(run, "status", None)) != "succeeded":
            raise ValueError("authoritative walk-forward run is not successful")

        payload = getattr(run, "payload", None)
        ranking_v3 = payload.get("ranking_v3") if isinstance(payload, Mapping) else None
        if not isinstance(ranking_v3, Mapping):
            raise ValueError("walk-forward run has no authoritative Ranking V3 payload")
        protocol_payload = ranking_v3.get("protocol")
        if not isinstance(protocol_payload, Mapping):
            raise ValueError("walk-forward run has no frozen Ranking V3 protocol")
        protocol = RankingV3Protocol.model_validate(protocol_payload)
        if not ranking_v3_protocol_digest_is_valid(protocol):
            raise ValueError("walk-forward Ranking V3 protocol digest is invalid")
        if _text(ranking_v3.get("model_version")) != protocol.model_version:
            raise ValueError("walk-forward model version does not match its frozen protocol")

        updated_at = getattr(run, "updated_at", None)
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError("authoritative walk-forward run timestamp must be timezone-aware")
        return run, ranking_v3, protocol, ranking_v3_data_revision(run)

    @staticmethod
    def _require_trading_session(session_date: date) -> None:
        if trading_sessions_in_range(session_date, session_date) != [session_date]:
            raise ValueError("forward orchestration date must be an A-share trading session")

    @staticmethod
    def _validate_candidates(
        candidates: Sequence[RankingV3ForwardCandidateFact],
        protocol: RankingV3Protocol,
    ) -> None:
        if len(candidates) > protocol.max_positions:
            raise ValueError("daily V3 candidates exceed the frozen maximum positions")
        if any(item.rank > protocol.max_positions for item in candidates):
            raise ValueError("daily V3 candidate rank exceeds the frozen maximum positions")

    @staticmethod
    def _validate_protocol_benchmark(
        facts: RankingV3ForwardDayFacts,
        protocol: RankingV3Protocol,
    ) -> None:
        expected = protocol.benchmark_definition.forward_release_benchmark_id
        if facts.benchmark_id != expected or any(
            item.benchmark_id != expected for item in facts.candidates
        ):
            raise ValueError(
                "forward day benchmark does not match the frozen protocol release benchmark"
            )

    def _candidate_input(
        self,
        protocol: RankingV3Protocol,
        data_revision: str,
        session_date: date,
        fact: RankingV3ForwardCandidateFact,
    ) -> RankingV3ShadowCandidateInput:
        candidate_id = self._candidate_id(session_date, fact)
        return RankingV3ShadowCandidateInput(
            candidate_id=candidate_id,
            source_snapshot_id=fact.source_snapshot_id,
            session_date=session_date,
            maturity_session_date=trading_day_offset(
                session_date,
                protocol.statistics_definition.entry_wait_sessions
                + protocol.statistics_definition.holding_sessions,
            ),
            instrument_id=fact.instrument_id,
            strategy_id=fact.strategy_id,
            rank=fact.rank,
            score=fact.score,
            benchmark_id=fact.benchmark_id,
            data_revision=data_revision,
            selection_digest=fact.selection_digest,
        )

    def _record_candidate_input(
        self,
        validator: RankingV3ForwardValidator,
        protocol: RankingV3Protocol,
        item: RankingV3ShadowCandidateInput,
    ) -> RankingV3ShadowCandidate:
        return validator.record_candidate(
            item,
            idempotency_key=self._candidate_key(protocol, item),
        )

    def _record_mature_outcomes(
        self,
        validator: RankingV3ForwardValidator,
        data_revision: str,
        session_date: date,
        facts: Sequence[RankingV3ForwardOutcomeFact],
    ) -> tuple[RankingV3ShadowCandidate, ...]:
        snapshot = self.store.load_snapshot(validator.identity)
        if snapshot is None:
            raise LookupError("Ranking V3 forward ledger does not exist")
        candidates = {item.candidate_id: item for item in snapshot.candidates}
        finalized: list[RankingV3ShadowCandidate] = []
        for fact in sorted(facts, key=lambda value: value.candidate_id):
            candidate = candidates.get(fact.candidate_id)
            if candidate is None:
                raise LookupError("mature outcome does not belong to the active Ranking V3 ledger")
            if candidate.maturity_session_date > session_date:
                raise ValueError("candidate outcome cannot be recorded before maturity")
            outcome = RankingV3ForwardOutcomeInput(
                status=fact.status,
                resolved_on=session_date,
                gross_return_pct=fact.gross_return_pct,
                transaction_cost_pct=fact.transaction_cost_pct,
                stress_transaction_cost_pct=fact.stress_transaction_cost_pct,
                benchmark_return_pct=fact.benchmark_return_pct,
                max_drawdown_pct=fact.max_drawdown_pct,
                data_revision=data_revision,
                reason=fact.reason,
            )
            finalized.append(
                validator.finalize_candidate(
                    fact.candidate_id,
                    outcome,
                    idempotency_key=self._outcome_key(
                        validator.protocol,
                        fact.candidate_id,
                        session_date,
                    ),
                )
            )
        return tuple(finalized)

    def _validate_mature_outcome_targets(
        self,
        validator: RankingV3ForwardValidator,
        session_date: date,
        facts: Sequence[RankingV3ForwardOutcomeFact],
    ) -> None:
        if not facts:
            return
        snapshot = self.store.load_snapshot(validator.identity)
        if snapshot is None:
            raise LookupError("Ranking V3 forward ledger does not exist")
        candidates = {item.candidate_id: item for item in snapshot.candidates}
        for fact in facts:
            candidate = candidates.get(fact.candidate_id)
            if candidate is None:
                raise LookupError("mature outcome does not belong to the active Ranking V3 ledger")
            if candidate.maturity_session_date > session_date:
                raise ValueError("candidate outcome cannot be recorded before maturity")

    def _record_authoritative_evidence(
        self,
        validator: RankingV3ForwardValidator,
        run: object,
        ranking_v3: Mapping[str, object],
        data_revision: str,
    ) -> None:
        generated_at = getattr(run, "updated_at")
        historical_results = ranking_v3_historical_gate_results(run)
        if historical_results:
            historical = RankingV3HistoricalGatesInput(
                validation_run_id=_text(getattr(run, "run_id", None)),
                data_revision=data_revision,
                gate_results=historical_results,
                source_proof_digest=ranking_v3_historical_source_digest(run),
                source_generated_at=generated_at,
            )
            if validator.evidence_authority.verify_historical_gates(
                validator.identity,
                historical,
            ):
                validator.record_historical_gates(
                    historical,
                    idempotency_key=(
                        f"ranking-v3-forward:historical:{historical.source_proof_digest[:32]}"
                    ),
                )

        pbo_payload = ranking_v3.get("pbo_evidence")
        if not isinstance(pbo_payload, Mapping):
            return
        try:
            pbo = RankingV3PBOInput(
                validation_run_id=_text(getattr(run, "run_id", None)),
                data_revision=data_revision,
                probability=Decimal(str(pbo_payload.get("probability"))),
                matrix_digest=_text(pbo_payload.get("matrix_digest")),
                fold_count=int(pbo_payload.get("fold_count", 0)),
                method=_text(pbo_payload.get("method")),
                source_proof_digest=ranking_v3_pbo_source_digest(run),
                source_generated_at=generated_at,
            )
        except (InvalidOperation, TypeError, ValueError):
            return
        if validator.evidence_authority.verify_pbo(validator.identity, pbo):
            validator.record_pbo(
                pbo,
                idempotency_key=f"ranking-v3-forward:pbo:{pbo.source_proof_digest[:32]}",
            )

    @staticmethod
    def _candidate_id(
        session_date: date,
        fact: RankingV3ForwardCandidateFact,
    ) -> str:
        identity = stable_digest(
            {
                "session_date": session_date,
                "source_snapshot_id": fact.source_snapshot_id,
                "instrument_id": fact.instrument_id,
                "strategy_id": fact.strategy_id,
                "rank": fact.rank,
                "selection_digest": fact.selection_digest,
            }
        )[:24]
        return f"{session_date.isoformat()}:{fact.instrument_id}:{fact.rank}:{identity}"

    @staticmethod
    def _session_key_from_batch(
        batch: RankingV3ForwardSelectionBatchInput,
    ) -> str:
        return encode_forward_session_batch_key(
            session_date=batch.session_date,
            candidate_snapshot_digest=batch.candidate_snapshot_digest,
            selection_batch_digest=batch.selection_batch_digest,
            selected_candidate_count=batch.selected_candidate_count,
        )

    @staticmethod
    def _selection_batch_key(
        protocol: RankingV3Protocol,
        session_date: date,
    ) -> str:
        return (
            "ranking-v3-forward:frozen-batch:"
            f"{protocol.protocol_digest[:16]}:{session_date.isoformat()}"
        )

    @staticmethod
    def _candidate_key(
        protocol: RankingV3Protocol,
        item: RankingV3ShadowCandidateInput,
    ) -> str:
        return (
            "ranking-v3-forward:candidate:"
            f"{protocol.protocol_digest[:16]}:{stable_digest(item)[:32]}"
        )

    @staticmethod
    def _outcome_key(
        protocol: RankingV3Protocol,
        candidate_id: str,
        session_date: date,
    ) -> str:
        return (
            "ranking-v3-forward:outcome:"
            f"{protocol.protocol_digest[:16]}:"
            f"{stable_digest({'candidate_id': candidate_id, 'date': session_date})[:32]}"
        )

    @staticmethod
    def _shadow_state(
        evaluation: RankingV3ForwardEvaluation,
    ) -> Literal[
        "shadow_unpublished",
        "shadow_rejected",
        "approved_proof_available",
    ]:
        if evaluation.status == "approved":
            return "approved_proof_available"
        if evaluation.status == "rejected":
            return "shadow_rejected"
        return "shadow_unpublished"


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
