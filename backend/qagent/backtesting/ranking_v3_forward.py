from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from qagent.backtesting.ranking_v3_protocol import (
    RankingV3Protocol,
    ranking_v3_protocol_digest_is_valid,
)
from qagent.security.ranking_v3_attestation import (
    RankingV3AttestationEnvelope,
    RankingV3Attestor,
    load_attestation_key,
)
from qagent.market.calendars import trading_day_offset, trading_sessions_in_range


ForwardLedgerStatus = Literal["pending", "rejected", "approved"]
ForwardOutcomeStatus = Literal["completed", "not_triggered", "invalid", "censored"]
ForwardGateStatus = Literal["pass", "fail", "insufficient"]
ForwardEvidenceKind = Literal["historical_gates", "pbo", "portfolio"]

VALID_FORWARD_OUTCOME_STATUSES = frozenset({"completed", "not_triggered"})
_LEGACY_SESSION_BATCH_DIGEST = "0" * 64
_SESSION_BATCH_KEY_PREFIX = "ranking-v3-forward:session-batch:"
_SELECTION_BATCH_SCHEMA_VERSION = "ranking-v3-forward-selection-batch-v1"
_PORTFOLIO_VERIFICATION_SCHEMA_VERSION = "ranking-v3-forward-portfolio-verification-v1"


class RankingV3ForwardError(RuntimeError):
    """Base error for the forward validation ledger."""


class RankingV3ForwardConflictError(RankingV3ForwardError):
    """Raised when an idempotency key or immutable fact is reused with new data."""


class RankingV3ForwardStateError(RankingV3ForwardError):
    """Raised when a write is attempted after a terminal ledger decision."""


class RankingV3ForwardIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_id: str = Field(min_length=1, max_length=96)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_version: str = Field(min_length=1, max_length=96)

    @classmethod
    def from_protocol(cls, protocol: RankingV3Protocol) -> RankingV3ForwardIdentity:
        return cls(
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.protocol_digest,
            model_version=protocol.model_version,
        )


class RankingV3ForwardPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    shadow_start_date: date
    minimum_sessions: int = Field(ge=1)
    maximum_sessions: int = Field(ge=1)
    minimum_completed_trades: int = Field(ge=1)
    minimum_valid_outcome_coverage_ratio: Decimal = Field(ge=0, le=1)
    maximum_invalid_outcome_ratio: Decimal = Field(ge=0, le=1)
    maximum_drawdown_pct: Decimal = Field(le=0)
    maximum_pbo_probability: Decimal = Field(ge=0, le=1)
    minimum_mean_benchmark_excess_pct: Decimal = Decimal("0")
    minimum_mean_stress_benchmark_excess_pct: Decimal = Decimal("0")

    @model_validator(mode="after")
    def validate_window(self):
        if self.maximum_sessions < self.minimum_sessions:
            raise ValueError("maximum_sessions must be at least minimum_sessions")
        return self

    @classmethod
    def from_protocol(cls, protocol: RankingV3Protocol) -> RankingV3ForwardPolicy:
        thresholds = protocol.thresholds
        return cls(
            shadow_start_date=protocol.prospective_shadow_start,
            minimum_sessions=thresholds.minimum_forward_shadow_sessions,
            maximum_sessions=thresholds.maximum_forward_shadow_sessions,
            minimum_completed_trades=thresholds.minimum_forward_shadow_trades,
            minimum_valid_outcome_coverage_ratio=Decimal(
                str(thresholds.minimum_valid_outcome_coverage_ratio)
            ),
            maximum_invalid_outcome_ratio=Decimal(str(thresholds.maximum_invalid_outcome_ratio)),
            maximum_drawdown_pct=Decimal(str(thresholds.maximum_drawdown_pct)),
            maximum_pbo_probability=Decimal(
                str(thresholds.maximum_probability_of_backtest_overfit)
            ),
        )


class RankingV3ForwardLedger(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: RankingV3ForwardIdentity
    data_revision: str = Field(min_length=1, max_length=128)
    status: ForwardLedgerStatus
    first_session_date: date | None = None
    latest_session_date: date | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    current_release_proof_digest: str | None = None
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class RankingV3ForwardSessionInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_date: date
    benchmark_id: str = Field(min_length=1, max_length=64)
    benchmark_return_pct: Decimal
    portfolio_equity: Decimal = Field(gt=0)
    stress_portfolio_equity: Decimal = Field(gt=0)
    benchmark_equity: Decimal = Field(gt=0)
    data_revision: str = Field(min_length=1, max_length=128)
    candidate_snapshot_digest: str = Field(
        default=_LEGACY_SESSION_BATCH_DIGEST,
        pattern=r"^[0-9a-f]{64}$",
        exclude=True,
    )
    selection_batch_digest: str = Field(
        default=_LEGACY_SESSION_BATCH_DIGEST,
        pattern=r"^[0-9a-f]{64}$",
        exclude=True,
    )
    selected_candidate_count: int = Field(default=0, ge=0, exclude=True)


class RankingV3ForwardSession(RankingV3ForwardSessionInput):
    identity: RankingV3ForwardIdentity
    idempotency_key: str
    fact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def restore_persisted_batch_facts(cls, value):
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        batch_fields = {
            "candidate_snapshot_digest",
            "selection_batch_digest",
            "selected_candidate_count",
        }
        if batch_fields.issubset(payload):
            return payload
        restored = decode_forward_session_batch_key(str(payload.get("idempotency_key", "")))
        if restored is not None:
            expected_prefix = f"{_SESSION_BATCH_KEY_PREFIX}{payload.get('session_date')}:"
            if not str(payload.get("idempotency_key", "")).startswith(expected_prefix):
                raise ValueError("persisted session batch key date does not match the session")
            payload.update(restored)
        return payload


class RankingV3ShadowCandidateInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=160)
    source_snapshot_id: str = Field(min_length=1, max_length=192)
    session_date: date
    maturity_session_date: date
    instrument_id: str = Field(min_length=1, max_length=32)
    strategy_id: str = Field(min_length=1, max_length=96)
    rank: int = Field(ge=1)
    score: Decimal
    benchmark_id: str = Field(min_length=1, max_length=64)
    data_revision: str = Field(min_length=1, max_length=128)
    selection_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_dates(self):
        if self.maturity_session_date <= self.session_date:
            raise ValueError("maturity_session_date must be later than session_date")
        return self


class RankingV3ForwardSelectionBatchInput(BaseModel):
    """One complete immutable daily selection batch stored before row fan-out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = _SELECTION_BATCH_SCHEMA_VERSION
    session_date: date
    benchmark_id: str = Field(min_length=1, max_length=64)
    data_revision: str = Field(min_length=1, max_length=128)
    candidate_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_batch_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_candidate_count: int = Field(ge=0)
    candidates: tuple[RankingV3ShadowCandidateInput, ...] = ()
    batch_fact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_complete_batch(self):
        if (
            self.candidate_snapshot_digest == _LEGACY_SESSION_BATCH_DIGEST
            or self.selection_batch_digest == _LEGACY_SESSION_BATCH_DIGEST
        ):
            raise ValueError("formal forward selection batches cannot use legacy zero digests")
        if self.selected_candidate_count != len(self.candidates):
            raise ValueError("frozen selection batch candidate count mismatch")
        ranks = [item.rank for item in self.candidates]
        if ranks != list(range(1, len(self.candidates) + 1)):
            raise ValueError("frozen selection batch ranks must be contiguous from one")
        instruments = [item.instrument_id for item in self.candidates]
        candidate_ids = [item.candidate_id for item in self.candidates]
        if len(instruments) != len(set(instruments)):
            raise ValueError("frozen selection batch instruments must be unique")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("frozen selection batch candidate ids must be unique")
        for item in self.candidates:
            if (
                item.session_date != self.session_date
                or item.benchmark_id != self.benchmark_id
                or item.data_revision != self.data_revision
            ):
                raise ValueError("frozen selection candidate does not match its daily batch")
            if item.selection_digest != forward_candidate_selection_digest(
                selection_batch_digest=self.selection_batch_digest,
                source_snapshot_id=item.source_snapshot_id,
                instrument_id=item.instrument_id,
                strategy_id=item.strategy_id,
                rank=item.rank,
                score=item.score,
            ):
                raise ValueError("frozen candidate selection digest is invalid")
        expected = stable_digest(
            self.model_dump(mode="python", exclude={"batch_fact_digest"})
        )
        if self.batch_fact_digest != expected:
            raise ValueError("frozen selection batch fact digest mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        session_date: date,
        benchmark_id: str,
        data_revision: str,
        candidate_snapshot_digest: str,
        selection_batch_digest: str,
        candidates: Sequence[RankingV3ShadowCandidateInput],
    ) -> RankingV3ForwardSelectionBatchInput:
        payload = {
            "schema_version": _SELECTION_BATCH_SCHEMA_VERSION,
            "session_date": session_date,
            "benchmark_id": benchmark_id,
            "data_revision": data_revision,
            "candidate_snapshot_digest": candidate_snapshot_digest,
            "selection_batch_digest": selection_batch_digest,
            "selected_candidate_count": len(candidates),
            "candidates": tuple(candidates),
        }
        return cls(**payload, batch_fact_digest=stable_digest(payload))


class RankingV3ForwardOutcomeInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ForwardOutcomeStatus
    resolved_on: date
    gross_return_pct: Decimal | None = None
    transaction_cost_pct: Decimal | None = Field(default=None, ge=0)
    stress_transaction_cost_pct: Decimal | None = Field(default=None, ge=0)
    benchmark_return_pct: Decimal | None = None
    max_drawdown_pct: Decimal | None = Field(default=None, le=0)
    data_revision: str = Field(min_length=1, max_length=128)
    reason: str = ""

    @model_validator(mode="after")
    def validate_outcome(self):
        deployed_capital_values = (
            self.gross_return_pct,
            self.transaction_cost_pct,
            self.stress_transaction_cost_pct,
            self.max_drawdown_pct,
        )
        if self.status == "completed":
            if any(
                value is None for value in (*deployed_capital_values, self.benchmark_return_pct)
            ):
                raise ValueError(
                    "completed outcomes require returns, costs, benchmark and drawdown"
                )
            if self.stress_transaction_cost_pct < self.transaction_cost_pct:
                raise ValueError("stress cost cannot be lower than normal transaction cost")
        elif self.status == "not_triggered":
            if self.benchmark_return_pct is None:
                raise ValueError("not-triggered outcomes require the benchmark opportunity return")
            if any(value is not None for value in deployed_capital_values):
                raise ValueError("not-triggered outcomes cannot report deployed-capital returns")
        elif any(
            value is not None for value in (*deployed_capital_values, self.benchmark_return_pct)
        ):
            raise ValueError("invalid or censored outcomes cannot report financial returns")
        return self


class RankingV3ShadowCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: RankingV3ForwardIdentity
    candidate_id: str
    source_snapshot_id: str = Field(min_length=1, max_length=192)
    session_date: date
    maturity_session_date: date
    instrument_id: str
    strategy_id: str
    rank: int
    score: Decimal
    benchmark_id: str
    data_revision: str
    selection_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str
    fact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_status: Literal["pending", "completed", "not_triggered", "invalid", "censored"] = (
        "pending"
    )
    outcome_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    outcome_idempotency_key: str | None = None
    resolved_on: date | None = None
    gross_return_pct: Decimal | None = None
    transaction_cost_pct: Decimal | None = None
    stress_transaction_cost_pct: Decimal | None = None
    net_return_pct: Decimal | None = None
    stress_net_return_pct: Decimal | None = None
    benchmark_return_pct: Decimal | None = None
    benchmark_excess_pct: Decimal | None = None
    stress_benchmark_excess_pct: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    outcome_reason: str = ""
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_dates(self):
        if self.maturity_session_date <= self.session_date:
            raise ValueError("maturity_session_date must be later than session_date")
        if self.resolved_on is not None and self.resolved_on < self.maturity_session_date:
            raise ValueError("resolved_on cannot precede maturity_session_date")
        return self


class RankingV3HistoricalGatesInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_run_id: str = Field(min_length=1, max_length=128)
    data_revision: str = Field(min_length=1, max_length=128)
    gate_results: dict[str, ForwardGateStatus] = Field(min_length=1)
    source_proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_generated_at: datetime

    @model_validator(mode="after")
    def validate_source_time(self):
        _aware_utc(self.source_generated_at)
        return self


class RankingV3PBOInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_run_id: str = Field(min_length=1, max_length=128)
    data_revision: str = Field(min_length=1, max_length=128)
    probability: Decimal = Field(ge=0, le=1)
    matrix_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fold_count: int = Field(ge=2)
    method: str = Field(min_length=1, max_length=128)
    source_proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_generated_at: datetime

    @model_validator(mode="after")
    def validate_source_time(self):
        _aware_utc(self.source_generated_at)
        return self


class RankingV3ForwardEquityPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date
    equity: Decimal = Field(gt=0)
    cash: Decimal = Field(ge=0)
    market_value: Decimal = Field(ge=0)
    open_positions: int = Field(ge=0)
    drawdown_pct: Decimal = Field(le=0)


class RankingV3ForwardPortfolioInput(BaseModel):
    """Server-reconstructed, capital-constrained portfolio evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_run_id: str = Field(min_length=1, max_length=128)
    data_revision: str = Field(min_length=1, max_length=128)
    as_of_session_date: date
    benchmark_id: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=32)
    execution_profile: str = Field(min_length=1, max_length=128)
    initial_equity: Decimal = Field(gt=0)
    final_equity: Decimal = Field(gt=0)
    stress_final_equity: Decimal = Field(gt=0)
    benchmark_final_equity: Decimal = Field(gt=0)
    net_return_pct: Decimal
    stress_net_return_pct: Decimal
    benchmark_return_pct: Decimal
    benchmark_excess_pct: Decimal
    stress_benchmark_excess_pct: Decimal
    maximum_drawdown_pct: Decimal = Field(le=0)
    stress_maximum_drawdown_pct: Decimal = Field(le=0)
    completed_trade_count: int = Field(ge=0)
    equity_curve: tuple[RankingV3ForwardEquityPoint, ...] = Field(min_length=1)
    stress_equity_curve: tuple[RankingV3ForwardEquityPoint, ...] = Field(min_length=1)
    equity_curve_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    stress_equity_curve_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_open_positions: int = Field(ge=0)
    stress_final_open_positions: int = Field(ge=0)
    source_candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_returns(self):
        expected = (self.final_equity / self.initial_equity - Decimal("1")) * Decimal("100")
        stress_expected = (self.stress_final_equity / self.initial_equity - Decimal("1")) * Decimal(
            "100"
        )
        benchmark_expected = (
            self.benchmark_final_equity / self.initial_equity - Decimal("1")
        ) * Decimal("100")
        tolerance = Decimal("0.000001")
        checks = (
            (self.net_return_pct, expected),
            (self.stress_net_return_pct, stress_expected),
            (self.benchmark_return_pct, benchmark_expected),
            (self.benchmark_excess_pct, expected - benchmark_expected),
            (
                self.stress_benchmark_excess_pct,
                stress_expected - benchmark_expected,
            ),
        )
        if any(abs(actual - calculated) > tolerance for actual, calculated in checks):
            raise ValueError("forward portfolio returns do not match the equity facts")
        self._validate_curve(
            self.equity_curve,
            expected_digest=self.equity_curve_digest,
            expected_final_equity=self.final_equity,
            expected_drawdown=self.maximum_drawdown_pct,
            expected_open_positions=self.final_open_positions,
            label="base",
        )
        self._validate_curve(
            self.stress_equity_curve,
            expected_digest=self.stress_equity_curve_digest,
            expected_final_equity=self.stress_final_equity,
            expected_drawdown=self.stress_maximum_drawdown_pct,
            expected_open_positions=self.stress_final_open_positions,
            label="stress",
        )
        if self.final_open_positions != 0 or self.stress_final_open_positions != 0:
            raise ValueError("forward portfolio evidence must finish fully in cash")
        return self

    @staticmethod
    def _validate_curve(
        curve: Sequence[RankingV3ForwardEquityPoint],
        *,
        expected_digest: str,
        expected_final_equity: Decimal,
        expected_drawdown: Decimal,
        expected_open_positions: int,
        label: str,
    ) -> None:
        dates = [item.date for item in curve]
        if dates != sorted(set(dates)):
            raise ValueError(f"{label} forward equity curve dates must be unique and sorted")
        if stable_digest([item.model_dump(mode="json") for item in curve]) != expected_digest:
            raise ValueError(f"{label} forward equity curve digest mismatch")
        tolerance = Decimal("0.000001")
        for point in curve:
            if abs(point.equity - point.cash - point.market_value) > tolerance:
                raise ValueError(f"{label} forward equity point does not balance")
        if abs(curve[-1].equity - expected_final_equity) > tolerance:
            raise ValueError(f"{label} forward equity curve final value mismatch")
        if curve[-1].open_positions != expected_open_positions:
            raise ValueError(f"{label} forward equity curve open positions mismatch")
        peak = curve[0].equity
        calculated_drawdown = Decimal("0")
        for point in curve:
            peak = max(peak, point.equity)
            point_drawdown = (point.equity / peak - Decimal("1")) * Decimal("100")
            if abs(point.drawdown_pct - point_drawdown) > tolerance:
                raise ValueError(f"{label} forward equity point drawdown mismatch")
            calculated_drawdown = min(calculated_drawdown, point_drawdown)
        if abs(expected_drawdown - calculated_drawdown) > tolerance:
            raise ValueError(f"{label} forward equity maximum drawdown mismatch")


class RankingV3ForwardPortfolioVerification(BaseModel):
    """Server-issued proof that portfolio facts were independently recomputed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = _PORTFOLIO_VERIFICATION_SCHEMA_VERSION
    identity: RankingV3ForwardIdentity
    expected_benchmark_id: str = Field(min_length=1, max_length=64)
    data_revision: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=32)
    execution_profile: str = Field(min_length=1, max_length=128)
    source_candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_session_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: RankingV3ForwardPortfolioInput
    verification_method: Literal["server-authoritative-recompute-v1"] = (
        "server-authoritative-recompute-v1"
    )
    verification_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_verification(self):
        if (
            self.evidence.benchmark_id != self.expected_benchmark_id
            or self.evidence.data_revision != self.data_revision
            or self.evidence.provider != self.provider
            or self.evidence.execution_profile != self.execution_profile
            or self.evidence.source_candidate_digest != self.source_candidate_digest
        ):
            raise ValueError("portfolio verification bindings do not match its evidence")
        expected = stable_digest(
            self.model_dump(mode="python", exclude={"verification_digest"})
        )
        if self.verification_digest != expected:
            raise ValueError("portfolio authority verification digest mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        identity: RankingV3ForwardIdentity,
        expected_benchmark_id: str,
        source_session_digest: str,
        evidence: RankingV3ForwardPortfolioInput,
    ) -> RankingV3ForwardPortfolioVerification:
        payload = {
            "schema_version": _PORTFOLIO_VERIFICATION_SCHEMA_VERSION,
            "identity": identity,
            "expected_benchmark_id": expected_benchmark_id,
            "data_revision": evidence.data_revision,
            "provider": evidence.provider,
            "execution_profile": evidence.execution_profile,
            "source_candidate_digest": evidence.source_candidate_digest,
            "source_session_digest": source_session_digest,
            "evidence": evidence,
            "verification_method": "server-authoritative-recompute-v1",
        }
        return cls(**payload, verification_digest=stable_digest(payload))


class RankingV3ForwardGateEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: RankingV3ForwardIdentity
    evidence_kind: ForwardEvidenceKind
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_revision: str
    passed: bool
    payload: dict[str, object]
    sequence: int = Field(default=1, ge=1)
    idempotency_key: str
    recorded_at: datetime


class RankingV3ForwardGateCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    status: ForwardGateStatus
    observed: str
    required: str
    reason: str
    evidence_digest: str | None = None


class RankingV3ForwardMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_count: int
    completed_trade_count: int
    candidate_count: int
    mature_candidate_count: int
    valid_outcome_count: int
    invalid_outcome_count: int
    pending_mature_outcome_count: int
    pending_candidate_count: int
    valid_outcome_coverage_ratio: Decimal | None = None
    invalid_outcome_ratio: Decimal | None = None
    common_benchmark_id: str | None = None
    mean_benchmark_excess_pct: Decimal | None = None
    mean_stress_benchmark_excess_pct: Decimal | None = None
    portfolio_net_return_pct: Decimal | None = None
    portfolio_stress_net_return_pct: Decimal | None = None
    portfolio_benchmark_return_pct: Decimal | None = None
    portfolio_benchmark_excess_pct: Decimal | None = None
    portfolio_stress_benchmark_excess_pct: Decimal | None = None
    portfolio_completed_trade_count: int | None = None
    maximum_drawdown_pct: Decimal | None = None
    first_session_date: date | None = None
    latest_session_date: date | None = None


class RankingV3ForwardReleaseProof(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "ranking-v3-forward-release-proof-v1"
    proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity: RankingV3ForwardIdentity
    data_revision: str
    status: Literal["approved"] = "approved"
    generated_at: datetime
    ledger_revision: int
    ledger_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: RankingV3ForwardMetrics
    gates: list[RankingV3ForwardGateCheck]
    historical_gates_evidence_digest: str
    pbo_evidence_digest: str
    portfolio_evidence_digest: str
    attestation: RankingV3AttestationEnvelope


class RankingV3ForwardEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: RankingV3ForwardIdentity
    data_revision: str
    status: ForwardLedgerStatus
    metrics: RankingV3ForwardMetrics
    gates: list[RankingV3ForwardGateCheck]
    reasons: list[str]
    release_proof: RankingV3ForwardReleaseProof | None = None


class RankingV3ReleaseProofValidation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    reason: str
    proof: RankingV3ForwardReleaseProof | None = None


class RankingV3ForwardLedgerSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ledger: RankingV3ForwardLedger
    sessions: list[RankingV3ForwardSession]
    candidates: list[RankingV3ShadowCandidate]
    evidence: list[RankingV3ForwardGateEvidence]
    release_proof: RankingV3ForwardReleaseProof | None = None


class RankingV3ForwardPortfolioAuthority(Protocol):
    """Independently recompute portfolio facts from server-owned market data."""

    def recompute_portfolio(
        self,
        identity: RankingV3ForwardIdentity,
        protocol: RankingV3Protocol,
        snapshot: RankingV3ForwardLedgerSnapshot,
        submitted: RankingV3ForwardPortfolioInput,
    ) -> RankingV3ForwardPortfolioInput | None: ...


class RankingV3ForwardStore(Protocol):
    def ensure_ledger(
        self,
        identity: RankingV3ForwardIdentity,
        data_revision: str,
    ) -> RankingV3ForwardLedger: ...

    def record_session(
        self,
        identity: RankingV3ForwardIdentity,
        item: RankingV3ForwardSessionInput,
        *,
        idempotency_key: str,
        fact_digest: str,
    ) -> RankingV3ForwardSession: ...

    def record_candidate(
        self,
        identity: RankingV3ForwardIdentity,
        item: RankingV3ShadowCandidateInput,
        *,
        idempotency_key: str,
        fact_digest: str,
    ) -> RankingV3ShadowCandidate: ...

    def finalize_candidate(
        self,
        identity: RankingV3ForwardIdentity,
        candidate_id: str,
        item: RankingV3ForwardOutcomeInput,
        *,
        idempotency_key: str,
        outcome_digest: str,
        computed: Mapping[str, Decimal | None],
    ) -> RankingV3ShadowCandidate: ...

    def record_evidence(
        self,
        evidence: RankingV3ForwardGateEvidence,
    ) -> RankingV3ForwardGateEvidence: ...

    def load_snapshot(
        self,
        identity: RankingV3ForwardIdentity,
    ) -> RankingV3ForwardLedgerSnapshot | None: ...

    def approve(
        self,
        proof: RankingV3ForwardReleaseProof,
        *,
        expected_revision: int,
    ) -> RankingV3ForwardReleaseProof: ...

    def reject(
        self,
        identity: RankingV3ForwardIdentity,
        reasons: Sequence[str],
        *,
        expected_revision: int,
    ) -> RankingV3ForwardLedger: ...

    def get_release_proof(
        self,
        proof_digest: str,
    ) -> RankingV3ForwardReleaseProof | None: ...


class RankingV3ForwardEvidenceAuthority(Protocol):
    """Resolve source proofs from server-owned historical validation storage."""

    def verify_historical_gates(
        self,
        identity: RankingV3ForwardIdentity,
        evidence: RankingV3HistoricalGatesInput,
    ) -> bool: ...

    def verify_pbo(
        self,
        identity: RankingV3ForwardIdentity,
        evidence: RankingV3PBOInput,
    ) -> bool: ...


class DenyAllRankingV3ForwardEvidence:
    """Fail-closed default used until an authoritative resolver is integrated."""

    def verify_historical_gates(
        self,
        identity: RankingV3ForwardIdentity,
        evidence: RankingV3HistoricalGatesInput,
    ) -> bool:
        return False

    def verify_pbo(
        self,
        identity: RankingV3ForwardIdentity,
        evidence: RankingV3PBOInput,
    ) -> bool:
        return False


class DenyAllRankingV3ForwardPortfolioAuthority:
    """Fail closed when no server portfolio recomputation boundary is installed."""

    def recompute_portfolio(
        self,
        identity: RankingV3ForwardIdentity,
        protocol: RankingV3Protocol,
        snapshot: RankingV3ForwardLedgerSnapshot,
        submitted: RankingV3ForwardPortfolioInput,
    ) -> RankingV3ForwardPortfolioInput | None:
        return None


class RankingV3ForwardValidator:
    """Persist and evaluate one immutable Ranking V3 prospective shadow ledger."""

    def __init__(
        self,
        store: RankingV3ForwardStore,
        protocol: RankingV3Protocol,
        *,
        evidence_authority: RankingV3ForwardEvidenceAuthority | None = None,
        portfolio_authority: RankingV3ForwardPortfolioAuthority | None = None,
        attestor: RankingV3Attestor | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        if not ranking_v3_protocol_digest_is_valid(protocol):
            raise ValueError("Ranking V3 protocol digest is invalid")
        self.store = store
        self.protocol = protocol
        self.identity = RankingV3ForwardIdentity.from_protocol(protocol)
        self.policy = RankingV3ForwardPolicy.from_protocol(protocol)
        self.evidence_authority = evidence_authority or DenyAllRankingV3ForwardEvidence()
        self.portfolio_authority = (
            portfolio_authority or DenyAllRankingV3ForwardPortfolioAuthority()
        )
        self.attestor = attestor or RankingV3Attestor(load_attestation_key())
        self._now = now or (lambda: datetime.now(timezone.utc))

    def ensure_ledger(self, data_revision: str) -> RankingV3ForwardLedger:
        _require_nonempty(data_revision, "data_revision")
        return self.store.ensure_ledger(self.identity, data_revision)

    def record_session(
        self,
        item: RankingV3ForwardSessionInput,
        *,
        idempotency_key: str,
    ) -> RankingV3ForwardSession:
        self._require_protocol_data(item.data_revision)
        self._require_protocol_benchmark(item.benchmark_id)
        if item.session_date < self.policy.shadow_start_date:
            raise ValueError("forward session precedes the frozen prospective shadow start")
        snapshot = self.store.load_snapshot(self.identity)
        sessions = (
            sorted(snapshot.sessions, key=lambda value: value.session_date)
            if snapshot is not None
            else []
        )
        if len(sessions) >= self.policy.maximum_sessions and all(
            session.session_date != item.session_date for session in sessions
        ):
            raise RankingV3ForwardStateError(
                "forward ledger cannot exceed the frozen maximum session count"
            )
        if item.session_date not in trading_sessions_in_range(
            item.session_date,
            item.session_date,
        ):
            raise ValueError("forward session date must be an A-share trading session")
        if sessions and all(session.session_date != item.session_date for session in sessions):
            expected = trading_day_offset(sessions[-1].session_date, 1)
            if item.session_date != expected:
                raise RankingV3ForwardConflictError(
                    "forward sessions must be consecutive A-share trading sessions"
                )
        return self.store.record_session(
            self.identity,
            item,
            idempotency_key=_require_nonempty(idempotency_key, "idempotency_key"),
            fact_digest=forward_session_fact_digest(item),
        )

    def record_candidate(
        self,
        item: RankingV3ShadowCandidateInput,
        *,
        idempotency_key: str,
    ) -> RankingV3ShadowCandidate:
        self._require_protocol_data(item.data_revision)
        self._require_protocol_benchmark(item.benchmark_id)
        snapshot = self.store.load_snapshot(self.identity)
        frozen = (
            _selection_batch_for_date(snapshot, item.session_date)
            if snapshot is not None
            else None
        )
        if frozen is None:
            raise RankingV3ForwardStateError(
                "forward candidate requires a frozen complete daily selection batch"
            )
        expected = next(
            (
                candidate
                for candidate in frozen.candidates
                if candidate.candidate_id == item.candidate_id
            ),
            None,
        )
        if expected != item:
            raise RankingV3ForwardConflictError(
                "forward candidate does not match the frozen daily selection batch"
            )
        return self.store.record_candidate(
            self.identity,
            item,
            idempotency_key=_require_nonempty(idempotency_key, "idempotency_key"),
            fact_digest=stable_digest(item),
        )

    def freeze_selection_batch(
        self,
        item: RankingV3ForwardSelectionBatchInput,
        *,
        idempotency_key: str,
    ) -> RankingV3ForwardSelectionBatchInput:
        self._require_protocol_data(item.data_revision)
        self._require_protocol_benchmark(item.benchmark_id)
        payload = item.model_dump(mode="json")
        evidence = RankingV3ForwardGateEvidence(
            identity=self.identity,
            evidence_kind="portfolio",
            evidence_digest=stable_digest(
                {
                    "identity": self.identity.model_dump(mode="json"),
                    "kind": "portfolio",
                    "payload": payload,
                }
            ),
            data_revision=item.data_revision,
            passed=False,
            payload=payload,
            idempotency_key=_require_nonempty(idempotency_key, "idempotency_key"),
            recorded_at=_session_recorded_at(item.session_date),
        )
        persisted = self.store.record_evidence(evidence)
        frozen = _selection_batch_from_evidence(persisted)
        if frozen is None or frozen != item:
            raise RankingV3ForwardConflictError(
                "persisted daily selection batch differs from the submitted frozen facts"
            )
        return frozen

    def get_frozen_selection_batch(
        self,
        session_date: date,
    ) -> RankingV3ForwardSelectionBatchInput | None:
        snapshot = self.store.load_snapshot(self.identity)
        if snapshot is None:
            return None
        return _selection_batch_for_date(snapshot, session_date)

    def finalize_candidate(
        self,
        candidate_id: str,
        item: RankingV3ForwardOutcomeInput,
        *,
        idempotency_key: str,
    ) -> RankingV3ShadowCandidate:
        self._require_protocol_data(item.data_revision)
        snapshot = self.store.load_snapshot(self.identity)
        candidate = (
            next(
                (
                    candidate
                    for candidate in snapshot.candidates
                    if candidate.candidate_id == candidate_id
                ),
                None,
            )
            if snapshot is not None
            else None
        )
        if candidate is None:
            raise RankingV3ForwardStateError(
                f"forward candidate {candidate_id!r} is not present in the ledger"
            )
        if item.resolved_on < candidate.maturity_session_date:
            raise ValueError("resolved_on cannot precede maturity_session_date")
        computed = _computed_outcome_values(item)
        return self.store.finalize_candidate(
            self.identity,
            candidate_id,
            item,
            idempotency_key=_require_nonempty(idempotency_key, "idempotency_key"),
            outcome_digest=stable_digest({"outcome": item, "computed": computed}),
            computed=computed,
        )

    def record_historical_gates(
        self,
        item: RankingV3HistoricalGatesInput,
        *,
        idempotency_key: str,
    ) -> RankingV3ForwardGateEvidence:
        self._require_protocol_data(item.data_revision)
        if not self.evidence_authority.verify_historical_gates(
            self.identity,
            item,
        ):
            raise RankingV3ForwardStateError(
                "historical gates proof is not present in authoritative server storage"
            )
        payload = item.model_dump(mode="json")
        evidence = RankingV3ForwardGateEvidence(
            identity=self.identity,
            evidence_kind="historical_gates",
            evidence_digest=stable_digest(
                {
                    "identity": self.identity.model_dump(mode="json"),
                    "kind": "historical_gates",
                    "payload": payload,
                }
            ),
            data_revision=item.data_revision,
            passed=bool(item.gate_results)
            and all(status == "pass" for status in item.gate_results.values()),
            payload=payload,
            idempotency_key=_require_nonempty(idempotency_key, "idempotency_key"),
            recorded_at=_aware_utc(item.source_generated_at),
        )
        return self.store.record_evidence(evidence)

    def record_pbo(
        self,
        item: RankingV3PBOInput,
        *,
        idempotency_key: str,
    ) -> RankingV3ForwardGateEvidence:
        self._require_protocol_data(item.data_revision)
        if not self.evidence_authority.verify_pbo(self.identity, item):
            raise RankingV3ForwardStateError(
                "PBO proof is not present in authoritative server storage"
            )
        payload = item.model_dump(mode="json")
        evidence = RankingV3ForwardGateEvidence(
            identity=self.identity,
            evidence_kind="pbo",
            evidence_digest=stable_digest(
                {
                    "identity": self.identity.model_dump(mode="json"),
                    "kind": "pbo",
                    "payload": payload,
                }
            ),
            data_revision=item.data_revision,
            passed=item.probability <= self.policy.maximum_pbo_probability,
            payload=payload,
            idempotency_key=_require_nonempty(idempotency_key, "idempotency_key"),
            recorded_at=_aware_utc(item.source_generated_at),
        )
        return self.store.record_evidence(evidence)

    def record_portfolio(
        self,
        item: RankingV3ForwardPortfolioInput,
        *,
        idempotency_key: str,
    ) -> RankingV3ForwardGateEvidence:
        self._require_protocol_data(item.data_revision)
        item = RankingV3ForwardPortfolioInput.model_validate(item.model_dump(mode="python"))
        self._require_protocol_benchmark(item.benchmark_id)
        snapshot = self.store.load_snapshot(self.identity)
        if snapshot is None:
            raise RankingV3ForwardStateError("forward portfolio evidence requires an active ledger")
        expected_source_digest = forward_candidate_source_digest(snapshot.candidates)
        if item.source_candidate_digest != expected_source_digest:
            raise RankingV3ForwardStateError(
                "forward portfolio source candidate digest does not match the ledger"
            )
        try:
            authoritative = self.portfolio_authority.recompute_portfolio(
                self.identity,
                self.protocol,
                snapshot,
                item,
            )
        except Exception as exc:
            raise RankingV3ForwardStateError(
                "authoritative forward portfolio recomputation failed"
            ) from exc
        if authoritative is None or authoritative != item:
            raise RankingV3ForwardStateError(
                "forward portfolio evidence does not match server-recomputed facts"
            )
        verification = RankingV3ForwardPortfolioVerification.create(
            identity=self.identity,
            expected_benchmark_id=(
                self.protocol.benchmark_definition.forward_release_benchmark_id
            ),
            source_session_digest=forward_session_source_digest(snapshot.sessions),
            evidence=authoritative,
        )
        payload = verification.model_dump(mode="json")
        evidence = RankingV3ForwardGateEvidence(
            identity=self.identity,
            evidence_kind="portfolio",
            evidence_digest=stable_digest(
                {
                    "identity": self.identity.model_dump(mode="json"),
                    "kind": "portfolio",
                    "payload": payload,
                }
            ),
            data_revision=item.data_revision,
            passed=True,
            payload=payload,
            idempotency_key=_require_nonempty(idempotency_key, "idempotency_key"),
            recorded_at=_session_recorded_at(item.as_of_session_date),
        )
        return self.store.record_evidence(evidence)

    def evaluate(self) -> RankingV3ForwardEvaluation:
        snapshot = self.store.load_snapshot(self.identity)
        if snapshot is None:
            raise LookupError("Ranking V3 forward ledger does not exist")
        if snapshot.ledger.status == "approved":
            if snapshot.release_proof is None:
                raise RankingV3ForwardStateError(
                    "approved ledger has no authoritative release proof"
                )
            self._require_release_attestation(snapshot.release_proof)
            return _evaluation_from_approved_snapshot(snapshot)
        if snapshot.ledger.status == "rejected":
            metrics = _forward_metrics(snapshot)
            gates = _forward_gates(snapshot, metrics, self.policy, self.protocol)
            return RankingV3ForwardEvaluation(
                identity=self.identity,
                data_revision=snapshot.ledger.data_revision,
                status="rejected",
                metrics=metrics,
                gates=gates,
                reasons=snapshot.ledger.rejection_reasons,
            )

        metrics = _forward_metrics(snapshot)
        gates = _forward_gates(snapshot, metrics, self.policy, self.protocol)
        all_pass = bool(gates) and all(gate.status == "pass" for gate in gates)
        if all_pass:
            proof = _build_release_proof(
                snapshot,
                metrics,
                gates,
                generated_at=_aware_utc(self._now()),
                attestor=self.attestor,
            )
            persisted = self.store.approve(
                proof,
                expected_revision=snapshot.ledger.revision,
            )
            return RankingV3ForwardEvaluation(
                identity=self.identity,
                data_revision=snapshot.ledger.data_revision,
                status="approved",
                metrics=metrics,
                gates=gates,
                reasons=[],
                release_proof=persisted,
            )

        reasons = [gate.reason for gate in gates if gate.status != "pass"]
        if metrics.session_count >= self.policy.maximum_sessions:
            self.store.reject(
                self.identity,
                reasons,
                expected_revision=snapshot.ledger.revision,
            )
            return RankingV3ForwardEvaluation(
                identity=self.identity,
                data_revision=snapshot.ledger.data_revision,
                status="rejected",
                metrics=metrics,
                gates=gates,
                reasons=reasons,
            )
        return RankingV3ForwardEvaluation(
            identity=self.identity,
            data_revision=snapshot.ledger.data_revision,
            status="pending",
            metrics=metrics,
            gates=gates,
            reasons=reasons,
        )

    def inspect(self) -> RankingV3ForwardEvaluation:
        """Read the current shadow result without approving or rejecting the ledger."""

        snapshot = self.store.load_snapshot(self.identity)
        if snapshot is None:
            raise LookupError("Ranking V3 forward ledger does not exist")
        if snapshot.ledger.status == "approved":
            if snapshot.release_proof is None:
                raise RankingV3ForwardStateError(
                    "approved ledger has no authoritative release proof"
                )
            self._require_release_attestation(snapshot.release_proof)
            return _evaluation_from_approved_snapshot(snapshot)
        metrics = _forward_metrics(snapshot)
        gates = _forward_gates(snapshot, metrics, self.policy, self.protocol)
        reasons = (
            snapshot.ledger.rejection_reasons
            if snapshot.ledger.status == "rejected"
            else [gate.reason for gate in gates if gate.status != "pass"]
        )
        return RankingV3ForwardEvaluation(
            identity=self.identity,
            data_revision=snapshot.ledger.data_revision,
            status=snapshot.ledger.status,
            metrics=metrics,
            gates=gates,
            reasons=reasons,
            release_proof=snapshot.release_proof,
        )

    def validate_release_proof(
        self,
        proof_digest: str,
        *,
        expected_data_revision: str | None = None,
    ) -> RankingV3ReleaseProofValidation:
        try:
            proof = self.store.get_release_proof(proof_digest)
        except (RankingV3ForwardError, TypeError, ValueError):
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="persisted release proof is malformed",
            )
        if proof is None:
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof is not present in the authoritative store",
            )
        if proof.identity != self.identity:
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof identity does not match the active protocol",
            )
        if expected_data_revision is not None and proof.data_revision != expected_data_revision:
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof data revision does not match",
            )
        if stable_release_proof_digest(proof) != proof.proof_digest:
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof digest is invalid",
            )
        if not self.attestor.verify(
            proof.attestation,
            expected_kind="ranking-v3-release-proof",
            expected_payload_digest=proof.proof_digest,
        ):
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof server attestation is invalid",
            )
        try:
            snapshot = self.store.load_snapshot(self.identity)
        except (RankingV3ForwardError, TypeError, ValueError):
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="authoritative forward ledger is malformed",
            )
        if (
            snapshot is None
            or snapshot.ledger.status != "approved"
            or snapshot.ledger.current_release_proof_digest != proof.proof_digest
            or snapshot.release_proof != proof
        ):
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof is not the ledger's authoritative approval",
            )
        if _ledger_evidence_digest(snapshot) != proof.ledger_evidence_digest:
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof ledger evidence digest is invalid",
            )
        evidence_by_digest = {item.evidence_digest: item for item in snapshot.evidence}
        required_digests = {
            proof.historical_gates_evidence_digest,
            proof.pbo_evidence_digest,
            proof.portfolio_evidence_digest,
        }
        if not required_digests.issubset(evidence_by_digest):
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof references missing gate evidence",
            )
        for digest in required_digests:
            evidence = evidence_by_digest[digest]
            if not _gate_evidence_is_valid(
                evidence,
                self.policy,
                snapshot=snapshot,
                protocol=self.protocol,
            ):
                return RankingV3ReleaseProofValidation(
                    valid=False,
                    reason="release proof references invalid gate evidence",
                )
            if not self._authority_accepts_evidence(evidence):
                return RankingV3ReleaseProofValidation(
                    valid=False,
                    reason="release proof evidence is no longer authoritative",
                )
        if not proof.gates or any(gate.status != "pass" for gate in proof.gates):
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof contains a non-passing gate",
            )
        if not _ledger_facts_are_valid(
            snapshot,
            expected_benchmark_id=(
                self.protocol.benchmark_definition.forward_release_benchmark_id
            ),
        ):
            return RankingV3ReleaseProofValidation(
                valid=False,
                reason="release proof source facts failed digest validation",
            )
        return RankingV3ReleaseProofValidation(
            valid=True,
            reason="authoritative Ranking V3 release proof is valid",
            proof=proof,
        )

    def _require_release_attestation(
        self,
        proof: RankingV3ForwardReleaseProof,
    ) -> None:
        if not self.attestor.verify(
            proof.attestation,
            expected_kind="ranking-v3-release-proof",
            expected_payload_digest=proof.proof_digest,
        ):
            raise RankingV3ForwardStateError(
                "approved release proof server attestation is invalid"
            )

    def _require_protocol_data(self, data_revision: str) -> None:
        snapshot = self.store.load_snapshot(self.identity)
        if snapshot is None:
            self.ensure_ledger(data_revision)
            return
        if snapshot.ledger.data_revision != data_revision:
            raise RankingV3ForwardConflictError(
                "data revision cannot change inside one protocol ledger"
            )

    def _require_protocol_benchmark(self, benchmark_id: str) -> None:
        expected = self.protocol.benchmark_definition.forward_release_benchmark_id
        if benchmark_id != expected:
            raise ValueError(
                "forward benchmark does not match the frozen protocol release benchmark"
            )

    def _authority_accepts_evidence(
        self,
        evidence: RankingV3ForwardGateEvidence,
    ) -> bool:
        try:
            if evidence.evidence_kind == "historical_gates":
                return self.evidence_authority.verify_historical_gates(
                    self.identity,
                    RankingV3HistoricalGatesInput.model_validate(evidence.payload),
                )
            if evidence.evidence_kind == "portfolio":
                snapshot = self.store.load_snapshot(self.identity)
                return (
                    snapshot is not None
                    and _portfolio_verification_is_valid(
                        evidence,
                        snapshot=snapshot,
                        protocol=self.protocol,
                    )
                )
            return self.evidence_authority.verify_pbo(
                self.identity,
                RankingV3PBOInput.model_validate(evidence.payload),
            )
        except (TypeError, ValueError):
            return False


def stable_digest(value: object) -> str:
    payload = _canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def forward_candidate_selection_digest(
    *,
    selection_batch_digest: str,
    source_snapshot_id: str,
    instrument_id: str,
    strategy_id: str,
    rank: int,
    score: Decimal,
) -> str:
    return stable_digest(
        {
            "selection_batch_digest": selection_batch_digest,
            "source_snapshot_id": source_snapshot_id,
            "instrument_id": instrument_id,
            "strategy_id": strategy_id,
            "rank": rank,
            "score": score,
        }
    )


def forward_candidate_source_digest(
    candidates: Sequence[RankingV3ShadowCandidate],
) -> str:
    return stable_digest(
        [
            {
                "candidate_id": item.candidate_id,
                "source_snapshot_id": item.source_snapshot_id,
                "selection_digest": item.selection_digest,
                "session_date": item.session_date.isoformat(),
                "instrument_id": item.instrument_id,
                "rank": item.rank,
                "score": str(item.score),
            }
            for item in sorted(
                candidates,
                key=lambda value: (
                    value.session_date,
                    value.rank,
                    value.candidate_id,
                ),
            )
        ]
    )


def forward_session_source_digest(
    sessions: Sequence[RankingV3ForwardSession],
) -> str:
    return stable_digest(
        [
            {
                "session_date": item.session_date,
                "benchmark_id": item.benchmark_id,
                "fact_digest": item.fact_digest,
                "candidate_snapshot_digest": item.candidate_snapshot_digest,
                "selection_batch_digest": item.selection_batch_digest,
                "selected_candidate_count": item.selected_candidate_count,
            }
            for item in sorted(sessions, key=lambda value: value.session_date)
        ]
    )


def encode_forward_session_batch_key(
    *,
    session_date: date,
    candidate_snapshot_digest: str,
    selection_batch_digest: str,
    selected_candidate_count: int,
) -> str:
    if not _is_sha256(candidate_snapshot_digest):
        raise ValueError("candidate_snapshot_digest must be a SHA-256 digest")
    if not _is_sha256(selection_batch_digest):
        raise ValueError("selection_batch_digest must be a SHA-256 digest")
    if selected_candidate_count < 0:
        raise ValueError("selected_candidate_count must be non-negative")
    return (
        f"{_SESSION_BATCH_KEY_PREFIX}{session_date.isoformat()}:"
        f"{candidate_snapshot_digest}:{selection_batch_digest}:"
        f"{selected_candidate_count}"
    )


def decode_forward_session_batch_key(value: str) -> dict[str, object] | None:
    if not value.startswith(_SESSION_BATCH_KEY_PREFIX):
        return None
    parts = value[len(_SESSION_BATCH_KEY_PREFIX) :].split(":")
    if len(parts) != 4:
        return None
    encoded_date, candidate_digest, selection_digest, raw_count = parts
    try:
        date.fromisoformat(encoded_date)
        count = int(raw_count)
    except (TypeError, ValueError):
        return None
    if not _is_sha256(candidate_digest) or not _is_sha256(selection_digest) or count < 0:
        return None
    return {
        "candidate_snapshot_digest": candidate_digest,
        "selection_batch_digest": selection_digest,
        "selected_candidate_count": count,
    }


def forward_session_fact_digest(item: RankingV3ForwardSessionInput) -> str:
    if (
        item.candidate_snapshot_digest == _LEGACY_SESSION_BATCH_DIGEST
        and item.selection_batch_digest == _LEGACY_SESSION_BATCH_DIGEST
        and item.selected_candidate_count == 0
    ):
        return stable_digest(item)
    return stable_digest(
        {
            "session": item.model_dump(mode="python"),
            "candidate_snapshot_digest": item.candidate_snapshot_digest,
            "selection_batch_digest": item.selection_batch_digest,
            "selected_candidate_count": item.selected_candidate_count,
        }
    )


def _selection_batch_from_evidence(
    evidence: RankingV3ForwardGateEvidence,
) -> RankingV3ForwardSelectionBatchInput | None:
    if (
        evidence.evidence_kind != "portfolio"
        or evidence.payload.get("schema_version") != _SELECTION_BATCH_SCHEMA_VERSION
    ):
        return None
    try:
        return RankingV3ForwardSelectionBatchInput.model_validate(evidence.payload)
    except (TypeError, ValueError):
        return None


def _selection_batch_for_date(
    snapshot: RankingV3ForwardLedgerSnapshot,
    session_date: date,
) -> RankingV3ForwardSelectionBatchInput | None:
    matches = [
        batch
        for evidence in snapshot.evidence
        if (batch := _selection_batch_from_evidence(evidence)) is not None
        and batch.session_date == session_date
    ]
    if not matches:
        return None
    first = matches[0]
    if any(item != first for item in matches[1:]):
        raise RankingV3ForwardConflictError(
            "multiple different frozen selection batches exist for one session"
        )
    return first


def _is_sha256(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def stable_release_proof_digest(proof: RankingV3ForwardReleaseProof) -> str:
    return stable_digest(
        proof.model_dump(
            mode="python",
            exclude={"proof_digest", "attestation"},
        )
    )


def _computed_outcome_values(
    item: RankingV3ForwardOutcomeInput,
) -> dict[str, Decimal | None]:
    if item.status == "not_triggered":
        benchmark_return = item.benchmark_return_pct
        return {
            "net_return_pct": Decimal("0"),
            "stress_net_return_pct": Decimal("0"),
            "benchmark_excess_pct": -benchmark_return,
            "stress_benchmark_excess_pct": -benchmark_return,
        }
    if item.status != "completed":
        return {
            "net_return_pct": None,
            "stress_net_return_pct": None,
            "benchmark_excess_pct": None,
            "stress_benchmark_excess_pct": None,
        }
    net = item.gross_return_pct - item.transaction_cost_pct
    stress_net = item.gross_return_pct - item.stress_transaction_cost_pct
    return {
        "net_return_pct": net,
        "stress_net_return_pct": stress_net,
        "benchmark_excess_pct": net - item.benchmark_return_pct,
        "stress_benchmark_excess_pct": stress_net - item.benchmark_return_pct,
    }


def _forward_metrics(snapshot: RankingV3ForwardLedgerSnapshot) -> RankingV3ForwardMetrics:
    sessions = sorted(snapshot.sessions, key=lambda item: item.session_date)
    latest_date = sessions[-1].session_date if sessions else None
    mature = [
        item
        for item in snapshot.candidates
        if latest_date is not None and item.maturity_session_date <= latest_date
    ]
    valid = [item for item in mature if item.outcome_status in VALID_FORWARD_OUTCOME_STATUSES]
    invalid = [item for item in mature if item.outcome_status in {"invalid", "censored"}]
    pending = [item for item in mature if item.outcome_status == "pending"]
    all_pending = [item for item in snapshot.candidates if item.outcome_status == "pending"]
    completed = [item for item in mature if item.outcome_status == "completed"]
    denominator = len(mature)
    coverage = Decimal(len(valid)) / Decimal(denominator) if denominator else None
    invalid_ratio = Decimal(len(invalid)) / Decimal(denominator) if denominator else None

    benchmark_ids = {item.benchmark_id for item in sessions}
    benchmark_ids.update(item.benchmark_id for item in mature)
    common_benchmark = next(iter(benchmark_ids)) if len(benchmark_ids) == 1 else None
    excess = [item.benchmark_excess_pct for item in valid]
    stress_excess = [item.stress_benchmark_excess_pct for item in valid]
    mean_excess = _mean_decimal(excess) if valid and all(v is not None for v in excess) else None
    mean_stress_excess = (
        _mean_decimal(stress_excess)
        if valid and all(value is not None for value in stress_excess)
        else None
    )

    portfolio_input = None
    portfolio_evidence = _latest_evidence(snapshot.evidence, "portfolio")
    if portfolio_evidence is not None:
        try:
            verification = RankingV3ForwardPortfolioVerification.model_validate(
                portfolio_evidence.payload
            )
            portfolio_input = verification.evidence
        except (TypeError, ValueError):
            portfolio_input = None
    maximum_drawdown = (
        min(
            portfolio_input.maximum_drawdown_pct,
            portfolio_input.stress_maximum_drawdown_pct,
        )
        if portfolio_input is not None
        else None
    )

    return RankingV3ForwardMetrics(
        session_count=len(sessions),
        completed_trade_count=len(completed),
        candidate_count=len(snapshot.candidates),
        mature_candidate_count=denominator,
        valid_outcome_count=len(valid),
        invalid_outcome_count=len(invalid),
        pending_mature_outcome_count=len(pending),
        pending_candidate_count=len(all_pending),
        valid_outcome_coverage_ratio=coverage,
        invalid_outcome_ratio=invalid_ratio,
        common_benchmark_id=common_benchmark,
        mean_benchmark_excess_pct=mean_excess,
        mean_stress_benchmark_excess_pct=mean_stress_excess,
        portfolio_net_return_pct=(
            portfolio_input.net_return_pct if portfolio_input is not None else None
        ),
        portfolio_stress_net_return_pct=(
            portfolio_input.stress_net_return_pct if portfolio_input is not None else None
        ),
        portfolio_benchmark_return_pct=(
            portfolio_input.benchmark_return_pct if portfolio_input is not None else None
        ),
        portfolio_benchmark_excess_pct=(
            portfolio_input.benchmark_excess_pct if portfolio_input is not None else None
        ),
        portfolio_stress_benchmark_excess_pct=(
            portfolio_input.stress_benchmark_excess_pct if portfolio_input is not None else None
        ),
        portfolio_completed_trade_count=(
            portfolio_input.completed_trade_count if portfolio_input is not None else None
        ),
        maximum_drawdown_pct=maximum_drawdown,
        first_session_date=sessions[0].session_date if sessions else None,
        latest_session_date=latest_date,
    )


def _forward_gates(
    snapshot: RankingV3ForwardLedgerSnapshot,
    metrics: RankingV3ForwardMetrics,
    policy: RankingV3ForwardPolicy,
    protocol: RankingV3Protocol,
) -> list[RankingV3ForwardGateCheck]:
    historical = _latest_evidence(snapshot.evidence, "historical_gates")
    pbo = _latest_evidence(snapshot.evidence, "pbo")
    portfolio = _latest_evidence(snapshot.evidence, "portfolio")
    expected_benchmark_id = protocol.benchmark_definition.forward_release_benchmark_id
    facts_valid = _ledger_facts_are_valid(
        snapshot,
        expected_benchmark_id=expected_benchmark_id,
    )
    return [
        RankingV3ForwardGateCheck(
            key="ledger_fact_integrity",
            status="pass" if facts_valid else "fail",
            observed="valid" if facts_valid else "invalid",
            required="all session, candidate and outcome digests valid",
            reason=(
                "ledger_fact_integrity: all persisted fact digests are valid"
                if facts_valid
                else "ledger_fact_integrity: one or more persisted facts were modified"
            ),
        ),
        _range_gate(
            "forward_sessions",
            metrics.session_count,
            policy.minimum_sessions,
            policy.maximum_sessions,
        ),
        _minimum_gate(
            "completed_trades",
            Decimal(metrics.completed_trade_count),
            Decimal(policy.minimum_completed_trades),
        ),
        RankingV3ForwardGateCheck(
            key="all_candidates_terminal",
            status=(
                "pass"
                if metrics.candidate_count > 0 and metrics.pending_candidate_count == 0
                else "insufficient"
            ),
            observed=(
                f"{metrics.candidate_count - metrics.pending_candidate_count}/"
                f"{metrics.candidate_count}"
            ),
            required="all selected candidates terminal and at least one candidate",
            reason=(
                "all_candidates_terminal: "
                f"{metrics.pending_candidate_count} candidates remain pending"
            ),
        ),
        _evidence_gate("historical_gates_proof", historical),
        _pbo_gate(pbo, policy.maximum_pbo_probability),
        _evidence_gate("capital_constrained_portfolio_proof", portfolio),
        _presence_gate(
            "common_benchmark",
            (
                metrics.common_benchmark_id
                if metrics.common_benchmark_id == expected_benchmark_id
                else None
            ),
            f"protocol benchmark {expected_benchmark_id}",
        ),
        _minimum_gate(
            "benchmark_excess_after_costs",
            metrics.mean_benchmark_excess_pct,
            policy.minimum_mean_benchmark_excess_pct,
            strict=True,
        ),
        _minimum_gate(
            "stress_cost_benchmark_excess",
            metrics.mean_stress_benchmark_excess_pct,
            policy.minimum_mean_stress_benchmark_excess_pct,
            strict=True,
        ),
        _minimum_gate(
            "portfolio_net_return",
            metrics.portfolio_net_return_pct,
            Decimal("0"),
            strict=True,
        ),
        _minimum_gate(
            "portfolio_benchmark_excess",
            metrics.portfolio_benchmark_excess_pct,
            Decimal("0"),
            strict=True,
        ),
        _minimum_gate(
            "portfolio_stress_benchmark_excess",
            metrics.portfolio_stress_benchmark_excess_pct,
            Decimal("0"),
            strict=True,
        ),
        _minimum_gate(
            "portfolio_completed_trades",
            (
                Decimal(metrics.portfolio_completed_trade_count)
                if metrics.portfolio_completed_trade_count is not None
                else None
            ),
            Decimal(policy.minimum_completed_trades),
        ),
        _minimum_gate(
            "maximum_drawdown",
            metrics.maximum_drawdown_pct,
            policy.maximum_drawdown_pct,
        ),
        _minimum_gate(
            "valid_outcome_coverage",
            metrics.valid_outcome_coverage_ratio,
            policy.minimum_valid_outcome_coverage_ratio,
        ),
        _maximum_gate(
            "invalid_outcome_ratio",
            metrics.invalid_outcome_ratio,
            policy.maximum_invalid_outcome_ratio,
        ),
    ]


def _build_release_proof(
    snapshot: RankingV3ForwardLedgerSnapshot,
    metrics: RankingV3ForwardMetrics,
    gates: list[RankingV3ForwardGateCheck],
    *,
    generated_at: datetime,
    attestor: RankingV3Attestor,
) -> RankingV3ForwardReleaseProof:
    historical = _latest_evidence(snapshot.evidence, "historical_gates")
    pbo = _latest_evidence(snapshot.evidence, "pbo")
    portfolio = _latest_evidence(snapshot.evidence, "portfolio")
    if historical is None or pbo is None or portfolio is None:
        raise RankingV3ForwardStateError(
            "release proof requires historical, PBO and portfolio evidence"
        )
    unsigned = RankingV3ForwardReleaseProof(
        proof_digest="0" * 64,
        identity=snapshot.ledger.identity,
        data_revision=snapshot.ledger.data_revision,
        generated_at=generated_at,
        ledger_revision=snapshot.ledger.revision,
        ledger_evidence_digest=_ledger_evidence_digest(snapshot),
        metrics=metrics,
        gates=gates,
        historical_gates_evidence_digest=historical.evidence_digest,
        pbo_evidence_digest=pbo.evidence_digest,
        portfolio_evidence_digest=portfolio.evidence_digest,
        attestation=attestor.sign(
            "ranking-v3-release-proof",
            "0" * 64,
        ),
    )
    proof_digest = stable_release_proof_digest(unsigned)
    return unsigned.model_copy(
        update={
            "proof_digest": proof_digest,
            "attestation": attestor.sign(
                "ranking-v3-release-proof",
                proof_digest,
            ),
        }
    )


def _evaluation_from_approved_snapshot(
    snapshot: RankingV3ForwardLedgerSnapshot,
) -> RankingV3ForwardEvaluation:
    proof = snapshot.release_proof
    if proof is None:
        raise RankingV3ForwardStateError("approved ledger has no release proof")
    return RankingV3ForwardEvaluation(
        identity=snapshot.ledger.identity,
        data_revision=snapshot.ledger.data_revision,
        status="approved",
        metrics=proof.metrics,
        gates=proof.gates,
        reasons=[],
        release_proof=proof,
    )


def _latest_evidence(
    evidence: Sequence[RankingV3ForwardGateEvidence],
    kind: ForwardEvidenceKind,
) -> RankingV3ForwardGateEvidence | None:
    matching = [
        item
        for item in evidence
        if item.evidence_kind == kind and _selection_batch_from_evidence(item) is None
    ]
    return max(
        matching,
        key=lambda item: (item.sequence, item.evidence_digest),
        default=None,
    )


def _range_gate(key: str, observed: int, minimum: int, maximum: int) -> RankingV3ForwardGateCheck:
    if observed < minimum:
        status: ForwardGateStatus = "insufficient"
    elif observed > maximum:
        status = "fail"
    else:
        status = "pass"
    return RankingV3ForwardGateCheck(
        key=key,
        status=status,
        observed=str(observed),
        required=f"{minimum}..{maximum}",
        reason=f"{key}: observed {observed}; required {minimum}..{maximum}",
    )


def _minimum_gate(
    key: str,
    observed: Decimal | None,
    required: Decimal,
    *,
    strict: bool = False,
) -> RankingV3ForwardGateCheck:
    if observed is None:
        status: ForwardGateStatus = "insufficient"
    elif (observed > required) if strict else (observed >= required):
        status = "pass"
    else:
        status = "fail"
    operator = ">" if strict else ">="
    return RankingV3ForwardGateCheck(
        key=key,
        status=status,
        observed=_decimal_text(observed),
        required=f"{operator} {_decimal_text(required)}",
        reason=f"{key}: observed {_decimal_text(observed)}; required {operator} {required}",
    )


def _maximum_gate(
    key: str,
    observed: Decimal | None,
    required: Decimal,
) -> RankingV3ForwardGateCheck:
    if observed is None:
        status: ForwardGateStatus = "insufficient"
    elif observed <= required:
        status = "pass"
    else:
        status = "fail"
    return RankingV3ForwardGateCheck(
        key=key,
        status=status,
        observed=_decimal_text(observed),
        required=f"<= {_decimal_text(required)}",
        reason=f"{key}: observed {_decimal_text(observed)}; required <= {required}",
    )


def _presence_gate(
    key: str,
    observed: str | None,
    required: str,
) -> RankingV3ForwardGateCheck:
    return RankingV3ForwardGateCheck(
        key=key,
        status="pass" if observed else "insufficient",
        observed=observed or "missing",
        required=required,
        reason=f"{key}: observed {observed or 'missing'}; required {required}",
    )


def _evidence_gate(
    key: str,
    evidence: RankingV3ForwardGateEvidence | None,
) -> RankingV3ForwardGateCheck:
    if evidence is None:
        return RankingV3ForwardGateCheck(
            key=key,
            status="insufficient",
            observed="missing",
            required="authoritative passing evidence",
            reason=f"{key}: authoritative evidence is missing",
        )
    return RankingV3ForwardGateCheck(
        key=key,
        status="pass" if evidence.passed else "fail",
        observed="pass" if evidence.passed else "fail",
        required="pass",
        reason=f"{key}: authoritative evidence {'passed' if evidence.passed else 'failed'}",
        evidence_digest=evidence.evidence_digest,
    )


def _pbo_gate(
    evidence: RankingV3ForwardGateEvidence | None,
    maximum: Decimal,
) -> RankingV3ForwardGateCheck:
    gate = _evidence_gate("pbo_proof", evidence)
    if evidence is None:
        return gate
    probability = Decimal(str(evidence.payload.get("probability")))
    status: ForwardGateStatus = "pass" if evidence.passed and probability <= maximum else "fail"
    return gate.model_copy(
        update={
            "status": status,
            "observed": _decimal_text(probability),
            "required": f"<= {_decimal_text(maximum)}",
            "reason": (
                f"pbo_proof: observed {_decimal_text(probability)}; "
                f"required <= {_decimal_text(maximum)}"
            ),
        }
    )


def _gate_evidence_is_valid(
    evidence: RankingV3ForwardGateEvidence,
    policy: RankingV3ForwardPolicy,
    *,
    snapshot: RankingV3ForwardLedgerSnapshot,
    protocol: RankingV3Protocol,
) -> bool:
    expected_digest = stable_digest(
        {
            "identity": evidence.identity.model_dump(mode="json"),
            "kind": evidence.evidence_kind,
            "payload": evidence.payload,
        }
    )
    if evidence.evidence_digest != expected_digest:
        return False
    if evidence.payload.get("data_revision") != evidence.data_revision:
        return False
    if evidence.evidence_kind == "historical_gates":
        gate_results = evidence.payload.get("gate_results")
        expected_pass = (
            isinstance(gate_results, dict)
            and bool(gate_results)
            and all(value == "pass" for value in gate_results.values())
        )
        return evidence.passed == expected_pass
    if evidence.evidence_kind == "portfolio":
        return _portfolio_verification_is_valid(
            evidence,
            snapshot=snapshot,
            protocol=protocol,
        )
    try:
        probability = Decimal(str(evidence.payload.get("probability")))
    except Exception:
        return False
    expected_pass = probability <= policy.maximum_pbo_probability
    return evidence.passed == expected_pass


def _portfolio_verification_is_valid(
    evidence: RankingV3ForwardGateEvidence,
    *,
    snapshot: RankingV3ForwardLedgerSnapshot,
    protocol: RankingV3Protocol,
) -> bool:
    if not evidence.passed or _selection_batch_from_evidence(evidence) is not None:
        return False
    try:
        verification = RankingV3ForwardPortfolioVerification.model_validate(
            evidence.payload
        )
    except (TypeError, ValueError):
        return False
    item = verification.evidence
    expected_benchmark_id = protocol.benchmark_definition.forward_release_benchmark_id
    return (
        verification.identity == snapshot.ledger.identity
        and verification.identity == RankingV3ForwardIdentity.from_protocol(protocol)
        and verification.data_revision == snapshot.ledger.data_revision
        and verification.expected_benchmark_id == expected_benchmark_id
        and item.benchmark_id == expected_benchmark_id
        and item.data_revision == snapshot.ledger.data_revision
        and item.as_of_session_date == snapshot.ledger.latest_session_date
        and verification.source_candidate_digest
        == forward_candidate_source_digest(snapshot.candidates)
        and verification.source_session_digest
        == forward_session_source_digest(snapshot.sessions)
    )


def _ledger_evidence_digest(snapshot: RankingV3ForwardLedgerSnapshot) -> str:
    return stable_digest(
        {
            "identity": snapshot.ledger.identity.model_dump(mode="json"),
            "data_revision": snapshot.ledger.data_revision,
            "sessions": [
                {
                    "session_date": item.session_date.isoformat(),
                    "fact_digest": item.fact_digest,
                }
                for item in sorted(
                    snapshot.sessions,
                    key=lambda value: value.session_date,
                )
            ],
            "candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "source_snapshot_id": item.source_snapshot_id,
                    "fact_digest": item.fact_digest,
                    "outcome_digest": item.outcome_digest,
                }
                for item in sorted(
                    snapshot.candidates,
                    key=lambda value: value.candidate_id,
                )
            ],
            "gate_evidence": sorted(item.evidence_digest for item in snapshot.evidence),
        }
    )


def _ledger_facts_are_valid(
    snapshot: RankingV3ForwardLedgerSnapshot,
    *,
    expected_benchmark_id: str,
) -> bool:
    sessions = sorted(snapshot.sessions, key=lambda value: value.session_date)
    if len({item.session_date for item in sessions}) != len(sessions):
        return False
    batches: dict[date, RankingV3ForwardSelectionBatchInput] = {}
    for evidence in snapshot.evidence:
        batch = _selection_batch_from_evidence(evidence)
        if batch is None:
            continue
        expected_evidence_digest = stable_digest(
            {
                "identity": evidence.identity.model_dump(mode="json"),
                "kind": evidence.evidence_kind,
                "payload": evidence.payload,
            }
        )
        if (
            evidence.passed
            or evidence.data_revision != snapshot.ledger.data_revision
            or evidence.evidence_digest != expected_evidence_digest
            or batch.benchmark_id != expected_benchmark_id
        ):
            return False
        existing = batches.get(batch.session_date)
        if existing is not None and existing != batch:
            return False
        batches[batch.session_date] = batch

    candidates_by_session: dict[date, list[RankingV3ShadowCandidate]] = {}
    for candidate in snapshot.candidates:
        candidates_by_session.setdefault(candidate.session_date, []).append(candidate)
    if set(candidates_by_session) - {item.session_date for item in sessions}:
        return False

    for item in sessions:
        if (
            item.benchmark_id != expected_benchmark_id
            or item.data_revision != snapshot.ledger.data_revision
            or item.candidate_snapshot_digest == _LEGACY_SESSION_BATCH_DIGEST
            or item.selection_batch_digest == _LEGACY_SESSION_BATCH_DIGEST
        ):
            return False
        source = RankingV3ForwardSessionInput(
            session_date=item.session_date,
            benchmark_id=item.benchmark_id,
            benchmark_return_pct=item.benchmark_return_pct,
            portfolio_equity=item.portfolio_equity,
            stress_portfolio_equity=item.stress_portfolio_equity,
            benchmark_equity=item.benchmark_equity,
            data_revision=item.data_revision,
            candidate_snapshot_digest=item.candidate_snapshot_digest,
            selection_batch_digest=item.selection_batch_digest,
            selected_candidate_count=item.selected_candidate_count,
        )
        if forward_session_fact_digest(source) != item.fact_digest:
            return False
        batch = batches.get(item.session_date)
        if batch is None:
            return False
        if (
            batch.candidate_snapshot_digest != item.candidate_snapshot_digest
            or batch.selection_batch_digest != item.selection_batch_digest
            or batch.selected_candidate_count != item.selected_candidate_count
            or batch.data_revision != item.data_revision
            or batch.benchmark_id != item.benchmark_id
        ):
            return False
        persisted = sorted(
            candidates_by_session.get(item.session_date, []),
            key=lambda value: value.rank,
        )
        if len(persisted) != item.selected_candidate_count:
            return False
        if [candidate.rank for candidate in persisted] != list(
            range(1, item.selected_candidate_count + 1)
        ):
            return False
        persisted_inputs = tuple(
            RankingV3ShadowCandidateInput(
                candidate_id=candidate.candidate_id,
                source_snapshot_id=candidate.source_snapshot_id,
                session_date=candidate.session_date,
                maturity_session_date=candidate.maturity_session_date,
                instrument_id=candidate.instrument_id,
                strategy_id=candidate.strategy_id,
                rank=candidate.rank,
                score=candidate.score,
                benchmark_id=candidate.benchmark_id,
                data_revision=candidate.data_revision,
                selection_digest=candidate.selection_digest,
            )
            for candidate in persisted
        )
        if persisted_inputs != batch.candidates:
            return False

    if set(batches) != {item.session_date for item in sessions}:
        return False
    for item in snapshot.candidates:
        if (
            item.benchmark_id != expected_benchmark_id
            or item.data_revision != snapshot.ledger.data_revision
        ):
            return False
        if item.maturity_session_date <= item.session_date:
            return False
        if item.resolved_on is not None and item.resolved_on < item.maturity_session_date:
            return False
        source = RankingV3ShadowCandidateInput(
            candidate_id=item.candidate_id,
            source_snapshot_id=item.source_snapshot_id,
            session_date=item.session_date,
            maturity_session_date=item.maturity_session_date,
            instrument_id=item.instrument_id,
            strategy_id=item.strategy_id,
            rank=item.rank,
            score=item.score,
            benchmark_id=item.benchmark_id,
            data_revision=item.data_revision,
            selection_digest=item.selection_digest,
        )
        if stable_digest(source) != item.fact_digest:
            return False
        if item.outcome_status == "pending":
            if item.outcome_digest is not None:
                return False
            continue
        if item.outcome_digest is None or item.resolved_on is None:
            return False
        outcome = RankingV3ForwardOutcomeInput(
            status=item.outcome_status,
            resolved_on=item.resolved_on,
            gross_return_pct=item.gross_return_pct,
            transaction_cost_pct=item.transaction_cost_pct,
            stress_transaction_cost_pct=item.stress_transaction_cost_pct,
            benchmark_return_pct=item.benchmark_return_pct,
            max_drawdown_pct=item.max_drawdown_pct,
            data_revision=item.data_revision,
            reason=item.outcome_reason,
        )
        computed = _computed_outcome_values(outcome)
        persisted_computed = {
            "net_return_pct": item.net_return_pct,
            "stress_net_return_pct": item.stress_net_return_pct,
            "benchmark_excess_pct": item.benchmark_excess_pct,
            "stress_benchmark_excess_pct": item.stress_benchmark_excess_pct,
        }
        if computed != persisted_computed:
            return False
        expected_digest = stable_digest(
            {
                "outcome": outcome,
                "computed": computed,
            }
        )
        if expected_digest != item.outcome_digest:
            return False
    return True


def _maximum_drawdown_pct(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    peak = values[0]
    worst = Decimal("0")
    for value in values:
        peak = max(peak, value)
        drawdown = ((value / peak) - Decimal("1")) * Decimal("100")
        worst = min(worst, drawdown)
    return worst


def _mean_decimal(values: Sequence[Decimal | None]) -> Decimal:
    concrete = [value for value in values if value is not None]
    return sum(concrete, Decimal("0")) / Decimal(len(concrete))


def _canonical_json(value: object) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_value(value: object):
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _decimal_text(value: Decimal | None) -> str:
    return "missing" if value is None else format(value.normalize(), "f")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _session_recorded_at(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _require_nonempty(value: str, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value
