from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
import hashlib
import json
import math
import random
import re
from statistics import NormalDist
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, field_validator

from qagent.backtesting.ranking_v4_pbo import (
    RankingV4DatedModelReturn,
    evaluate_ranking_v4_cscv_pbo,
)
from qagent.backtesting.ranking_v4_protocol import (
    RANKING_V4_DEVELOPMENT_END,
    RANKING_V4_DEVELOPMENT_LOOKBACK_DAYS,
    RANKING_V4_DEVELOPMENT_START,
    RankingV4Protocol,
    build_ranking_v4_protocol,
)


ValidationStatus = Literal["pass", "insufficient", "fail"]
GateStatus = Literal["pass", "insufficient", "fail", "unavailable"]
EvidenceStatus = Literal["pass", "fail", "unavailable"]
ProtocolVersion: TypeAlias = Literal["4.1", "4.2", "4.3", "4.4"]

RANKING_V41_TRIAL_LEDGER_SCHEMA_VERSION = "ranking-v4.1-immutable-trial-ledger-v1"
RANKING_V41_TRIAL_LEDGER_ID = "QAGENT-RANK-V4.1-ALL-KNOWN-TRIALS"
RANKING_V41_VALIDATION_SCHEMA_VERSION = "ranking-v4.1-historical-validation-v1"
RANKING_V42_TRIAL_LEDGER_SCHEMA_VERSION = "ranking-v4.2-immutable-trial-ledger-v1"
RANKING_V42_TRIAL_LEDGER_ID = "QAGENT-RANK-V4.2-ALL-KNOWN-TRIALS"
RANKING_V42_VALIDATION_SCHEMA_VERSION = "ranking-v4.2-historical-validation-v1"
RANKING_V43_TRIAL_LEDGER_SCHEMA_VERSION = "ranking-v4.3-immutable-trial-ledger-v1"
RANKING_V43_TRIAL_LEDGER_ID = "QAGENT-RANK-V4.3-ALL-KNOWN-TRIALS"
RANKING_V43_VALIDATION_SCHEMA_VERSION = "ranking-v4.3-historical-validation-v1"
RANKING_V44_TRIAL_LEDGER_SCHEMA_VERSION = "ranking-v4.4-immutable-trial-ledger-v1"
RANKING_V44_TRIAL_LEDGER_ID = "QAGENT-RANK-V4.4-ALL-KNOWN-TRIALS"
RANKING_V44_VALIDATION_SCHEMA_VERSION = "ranking-v4.4-historical-validation-v1"
RANKING_V4_TRIAL_LEDGER_SCHEMA_VERSION = RANKING_V44_TRIAL_LEDGER_SCHEMA_VERSION
RANKING_V4_TRIAL_LEDGER_ID = RANKING_V44_TRIAL_LEDGER_ID
RANKING_V4_VALIDATION_SCHEMA_VERSION = RANKING_V44_VALIDATION_SCHEMA_VERSION

_V41_PBO_EVIDENCE_SCHEMA_VERSION = "ranking-v4.1-cscv-pbo-evidence-v1"
_V41_PBO_MATRIX_SCHEMA_VERSION = "ranking-v4.1-real-model-return-matrix-v1"
_V42_PBO_EVIDENCE_SCHEMA_VERSION = "ranking-v4.2-cscv-pbo-evidence-v1"
_V42_PBO_MATRIX_SCHEMA_VERSION = "ranking-v4.2-real-model-return-matrix-v1"
_V43_PBO_EVIDENCE_SCHEMA_VERSION = "ranking-v4.3-cscv-pbo-evidence-v1"
_V43_PBO_MATRIX_SCHEMA_VERSION = "ranking-v4.3-real-model-return-matrix-v1"
_V44_PBO_EVIDENCE_SCHEMA_VERSION = "ranking-v4.4-cscv-pbo-evidence-v1"
_V44_PBO_MATRIX_SCHEMA_VERSION = "ranking-v4.4-real-model-return-matrix-v1"
_PBO_EVIDENCE_SCHEMA_VERSION = _V44_PBO_EVIDENCE_SCHEMA_VERSION
_PBO_MATRIX_SCHEMA_VERSION = _V44_PBO_MATRIX_SCHEMA_VERSION
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROTOCOL = build_ranking_v4_protocol()
_STATISTICS = _PROTOCOL.statistics_definition
_THRESHOLDS = _PROTOCOL.thresholds

DEPENDENCE_BLOCK_LENGTH = _STATISTICS.dependence_block_length
DEFAULT_BOOTSTRAP_SAMPLES = _STATISTICS.bootstrap_samples
DEFAULT_PERMUTATION_SAMPLES = _STATISTICS.permutation_samples
DEFAULT_RANDOM_SEED = _STATISTICS.random_seed


def _current_trial_id(version: ProtocolVersion) -> str:
    return {
        "4.1": "ranking_v41_full",
        "4.2": "ranking_v42_full",
        "4.3": "ranking_v43_full",
        "4.4": "ranking_v44_full",
    }[version]


def _trial_ledger_schema_version(version: ProtocolVersion) -> str:
    return {
        "4.1": RANKING_V41_TRIAL_LEDGER_SCHEMA_VERSION,
        "4.2": RANKING_V42_TRIAL_LEDGER_SCHEMA_VERSION,
        "4.3": RANKING_V43_TRIAL_LEDGER_SCHEMA_VERSION,
        "4.4": RANKING_V44_TRIAL_LEDGER_SCHEMA_VERSION,
    }[version]


def _trial_ledger_id(version: ProtocolVersion) -> str:
    return {
        "4.1": RANKING_V41_TRIAL_LEDGER_ID,
        "4.2": RANKING_V42_TRIAL_LEDGER_ID,
        "4.3": RANKING_V43_TRIAL_LEDGER_ID,
        "4.4": RANKING_V44_TRIAL_LEDGER_ID,
    }[version]


def _validation_schema_version(version: ProtocolVersion) -> str:
    return {
        "4.1": RANKING_V41_VALIDATION_SCHEMA_VERSION,
        "4.2": RANKING_V42_VALIDATION_SCHEMA_VERSION,
        "4.3": RANKING_V43_VALIDATION_SCHEMA_VERSION,
        "4.4": RANKING_V44_VALIDATION_SCHEMA_VERSION,
    }[version]


def _pbo_evidence_schema_version(version: ProtocolVersion) -> str:
    return {
        "4.1": _V41_PBO_EVIDENCE_SCHEMA_VERSION,
        "4.2": _V42_PBO_EVIDENCE_SCHEMA_VERSION,
        "4.3": _V43_PBO_EVIDENCE_SCHEMA_VERSION,
        "4.4": _V44_PBO_EVIDENCE_SCHEMA_VERSION,
    }[version]


def _pbo_matrix_schema_version(version: ProtocolVersion) -> str:
    return {
        "4.1": _V41_PBO_MATRIX_SCHEMA_VERSION,
        "4.2": _V42_PBO_MATRIX_SCHEMA_VERSION,
        "4.3": _V43_PBO_MATRIX_SCHEMA_VERSION,
        "4.4": _V44_PBO_MATRIX_SCHEMA_VERSION,
    }[version]


class RankingV4ReturnObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    rebalance_date: date
    net_return_pct: float
    stress_net_return_pct: float | None = None

    @field_validator("rebalance_date", mode="before")
    @classmethod
    def reject_datetime(cls, value: object) -> object:
        if isinstance(value, datetime):
            raise ValueError("rebalance_date must be a date, not a datetime")
        return value

    @field_validator("net_return_pct", "stress_net_return_pct")
    @classmethod
    def require_finite_return(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("returns must be finite")
        return value


class RankingV4TrialSeries(BaseModel):
    model_config = ConfigDict(frozen=True)

    trial_id: str
    returns: tuple[RankingV4ReturnObservation, ...]


class RankingV4TrialLedgerEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = RANKING_V4_TRIAL_LEDGER_SCHEMA_VERSION
    ledger_id: str = RANKING_V4_TRIAL_LEDGER_ID
    immutable: bool
    covers_all_known_attempts: bool
    known_trial_ids: tuple[str, ...]
    research_attempt_ids: tuple[str, ...]
    research_attempt_inventory_digest: str
    current_trial_id: str = "ranking_v44_full"
    experiment_registry_digest: str
    trial_series: tuple[RankingV4TrialSeries, ...]
    ledger_digest: str

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"ledger_digest"})


class RankingV4SubperiodResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    period: int
    start_date: date
    end_date: date
    rebalance_date_count: int
    mean_paired_net_excess_pct: float
    positive: bool


class RankingV4ValidationGate(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    status: GateStatus
    observed: str
    required: str
    reason: str


class RankingV4HistoricalValidationEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    validation_schema_version: str = RANKING_V4_VALIDATION_SCHEMA_VERSION
    protocol_id: str
    protocol_digest: str
    experiment_registry_digest: str
    evidence_window: Literal["development"] = "development"
    evidence_class: Literal["exploratory_development_evidence"] = "exploratory_development_evidence"
    status: ValidationStatus
    historical_gate_status: ValidationStatus
    deployment_scope: Literal["shadow_only"] = "shadow_only"
    eligible_for_confirmatory_forward: bool
    official_release_allowed: Literal[False] = False
    execution_start_date: date | None
    execution_end_date: date | None
    execution_rebalance_step_sessions: int | None
    execution_lookback_days: int | None
    execution_plan_matches_protocol: bool
    baseline_row_count: int
    challenger_row_count: int
    completed_trade_count: int
    valid_outcome_count: int
    expected_outcome_count: int
    valid_outcome_coverage_ratio: float | None
    baseline_rebalance_date_count: int
    challenger_rebalance_date_count: int
    common_rebalance_date_count: int
    dates_are_common: bool
    baseline_only_dates: tuple[date, ...]
    challenger_only_dates: tuple[date, ...]
    dependence_block_length: int
    effective_independent_block_count: int
    paired_mean_net_excess_pct: float | None
    cumulative_benchmark_excess_return_pct: float | None
    cumulative_stress_cost_adjusted_return_pct: float | None
    maximum_drawdown_pct: float | None
    profit_factor: float | None
    profit_factor_is_infinite: bool
    bootstrap_one_sided_95_lower_bound_pct: float | None
    positive_edge_p_value: float | None
    holm_adjusted_positive_edge_p_value: float | None
    holm_family_size: int
    positive_subperiod_count: int
    required_positive_subperiod_count: int
    subperiod_count: int
    subperiods: tuple[RankingV4SubperiodResult, ...]
    pbo_status: EvidenceStatus
    pbo_probability: float | None
    pbo_reason: str
    pbo_evidence_digest: str | None
    trial_ledger_status: EvidenceStatus
    trial_ledger_reason: str
    trial_count: int
    deflated_sharpe_status: EvidenceStatus
    deflated_sharpe_probability: float | None
    deflated_sharpe_reason: str
    gates: tuple[RankingV4ValidationGate, ...]
    reasons: tuple[str, ...]
    evaluation_digest: str


ReturnObservationLike: TypeAlias = (
    RankingV4ReturnObservation
    | tuple[date, float]
    | tuple[date, float, float]
    | Mapping[str, object]
)
TrialReturnLike: TypeAlias = RankingV4DatedModelReturn | ReturnObservationLike


def build_ranking_v4_trial_ledger(
    trial_returns: Mapping[str, Sequence[TrialReturnLike]],
    *,
    experiment_registry_digest: str,
    known_research_attempt_ids: Sequence[str] = (),
    immutable: bool = True,
    protocol_version: ProtocolVersion = "4.4",
) -> RankingV4TrialLedgerEvidence:
    """Build digest-backed evidence for every known research attempt.

    The required trial ids come from the frozen protocol and experiment
    registry. Callers cannot omit a failed predecessor and then redefine the
    known set around the supplied matrix.
    """

    normalized_attempt_ids = tuple(
        sorted({str(item).strip() for item in known_research_attempt_ids if str(item).strip()})
    )
    protocol = build_ranking_v4_protocol(version=protocol_version)
    normalized_known_ids = _required_trial_ids(
        normalized_attempt_ids,
        protocol=protocol,
    )
    supplied_trial_ids = tuple(sorted(trial_returns))
    series = tuple(
        RankingV4TrialSeries(
            trial_id=trial_id,
            returns=tuple(
                RankingV4ReturnObservation(
                    rebalance_date=item.rebalance_date,
                    net_return_pct=item.net_return,
                )
                if isinstance(item, RankingV4DatedModelReturn)
                else _coerce_observation(item)
                for item in trial_returns[trial_id]
            ),
        )
        for trial_id in supplied_trial_ids
    )
    payload: dict[str, object] = {
        "schema_version": _trial_ledger_schema_version(protocol_version),
        "ledger_id": _trial_ledger_id(protocol_version),
        "immutable": immutable,
        "covers_all_known_attempts": (
            supplied_trial_ids == normalized_known_ids
            and (
                protocol_version == "4.1"
                or protocol.experiment_registry.historical_trial_inventory_complete
            )
        ),
        "known_trial_ids": list(normalized_known_ids),
        "research_attempt_ids": list(normalized_attempt_ids),
        "research_attempt_inventory_digest": _sha256(
            {"research_attempt_ids": list(normalized_attempt_ids)}
        ),
        "current_trial_id": _current_trial_id(protocol_version),
        "experiment_registry_digest": experiment_registry_digest,
        "trial_series": [item.model_dump(mode="json") for item in series],
    }
    return RankingV4TrialLedgerEvidence(
        **payload,
        ledger_digest=_sha256(payload),
    )


def _required_trial_ids(
    known_research_attempt_ids: Sequence[str] = (),
    *,
    protocol: RankingV4Protocol = _PROTOCOL,
) -> tuple[str, ...]:
    predecessor_ids = tuple(
        summary.experiment_id for summary in protocol.experiment_registry.predecessor_summaries
    )
    return tuple(
        sorted(
            {
                *protocol.statistics_definition.pbo_model_ids,
                *predecessor_ids,
                *known_research_attempt_ids,
            }
        )
    )


def evaluate_ranking_v4_historical_validation(
    baseline_returns: Sequence[ReturnObservationLike],
    challenger_returns: Sequence[ReturnObservationLike],
    *,
    completed_trade_count: int,
    valid_outcome_count: int,
    expected_outcome_count: int,
    execution_start_date: date | None = None,
    execution_end_date: date | None = None,
    execution_rebalance_step_sessions: int | None = None,
    execution_lookback_days: int | None = None,
    challenger_max_drawdown_pct: float | None = None,
    known_research_attempt_ids: Sequence[str] = (),
    pbo_evidence: Mapping[str, object] | None,
    trial_ledger: RankingV4TrialLedgerEvidence | Mapping[str, object] | None,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    permutation_samples: int = DEFAULT_PERMUTATION_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
    protocol_version: ProtocolVersion = "4.4",
) -> RankingV4HistoricalValidationEvaluation:
    """Evaluate preregistered V4 historical evidence without granting release.

    Rows are first clustered by genuine rebalance date. Baseline and challenger
    must then have identical calendars. Historical development evidence can
    make the model eligible for a separately frozen forward test, but it can
    never admit the model to official recommendations or paper trading.
    """

    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    if permutation_samples <= 0:
        raise ValueError("permutation_samples must be positive")
    if completed_trade_count < 0:
        raise ValueError("completed_trade_count must be non-negative")
    if valid_outcome_count < 0 or expected_outcome_count < 0:
        raise ValueError("outcome counts must be non-negative")
    if valid_outcome_count > expected_outcome_count:
        raise ValueError("valid_outcome_count cannot exceed expected_outcome_count")

    protocol = build_ranking_v4_protocol(version=protocol_version)
    statistics = protocol.statistics_definition
    model_ids = statistics.pbo_model_ids
    current_trial_id = _current_trial_id(protocol_version)
    dependence_block_length = statistics.dependence_block_length
    execution_plan_matches_protocol = (
        execution_start_date == RANKING_V4_DEVELOPMENT_START
        and execution_end_date == RANKING_V4_DEVELOPMENT_END
        and execution_rebalance_step_sessions
        == protocol.temporal_definition.rebalance_step_sessions
        and execution_lookback_days == RANKING_V4_DEVELOPMENT_LOOKBACK_DAYS
    )
    baseline = tuple(_coerce_observation(item) for item in baseline_returns)
    challenger = tuple(_coerce_observation(item) for item in challenger_returns)
    baseline_by_date, _ = _cluster_returns(baseline)
    challenger_by_date, challenger_stress_by_date = _cluster_returns(challenger)
    baseline_dates = set(baseline_by_date)
    challenger_dates = set(challenger_by_date)
    common_dates = tuple(sorted(baseline_dates & challenger_dates))
    baseline_only_dates = tuple(sorted(baseline_dates - challenger_dates))
    challenger_only_dates = tuple(sorted(challenger_dates - baseline_dates))
    dates_are_common = bool(baseline_dates) and baseline_dates == challenger_dates

    paired_values: tuple[tuple[date, float], ...] = ()
    if dates_are_common:
        paired_values = tuple(
            (
                rebalance_date,
                challenger_by_date[rebalance_date] - baseline_by_date[rebalance_date],
            )
            for rebalance_date in common_dates
        )
    paired_returns = tuple(value for _, value in paired_values)

    bootstrap_lower = _one_sided_moving_block_lower_bound(
        paired_returns,
        samples=bootstrap_samples,
        seed=seed,
        block_length=dependence_block_length,
    )
    positive_p_value = _block_sign_flip_p_value(
        paired_returns,
        samples=permutation_samples,
        seed=seed + 1,
        block_length=dependence_block_length,
    )
    subperiods = _contiguous_subperiods(
        paired_values,
        required_subperiods=protocol.thresholds.required_subperiods,
    )
    positive_subperiod_count = sum(item.positive for item in subperiods)

    challenger_values = (
        tuple(challenger_by_date[item] for item in common_dates) if dates_are_common else ()
    )
    baseline_values = (
        tuple(baseline_by_date[item] for item in common_dates) if dates_are_common else ()
    )
    stress_values: tuple[float, ...] | None = None
    if dates_are_common and all(
        challenger_stress_by_date.get(item) is not None for item in common_dates
    ):
        stress_values = tuple(float(challenger_stress_by_date[item]) for item in common_dates)

    benchmark_excess = (
        _compound_return(challenger_values) - _compound_return(baseline_values)
        if dates_are_common
        else None
    )
    stress_return = _compound_return(stress_values) if stress_values is not None else None
    maximum_drawdown = (
        float(challenger_max_drawdown_pct)
        if challenger_max_drawdown_pct is not None
        and math.isfinite(float(challenger_max_drawdown_pct))
        else None
    )
    profit_factor, profit_factor_is_infinite = _profit_factor(challenger_values)
    coverage_ratio = (
        valid_outcome_count / expected_outcome_count if expected_outcome_count > 0 else None
    )

    pbo = _validate_pbo_evidence(
        pbo_evidence,
        validation_dates=common_dates if dates_are_common else (),
        baseline_values=baseline_values,
        challenger_values=challenger_values,
        protocol=protocol,
        protocol_version=protocol_version,
    )
    holm_adjusted: float | None = None
    holm_family_size = len(model_ids)
    if pbo.matrix is not None and dates_are_common:
        family_p_values = _registered_family_p_values(
            pbo.matrix,
            samples=permutation_samples,
            seed=seed + 100,
            model_ids=model_ids,
            block_length=dependence_block_length,
        )
        adjusted = holm_bonferroni([family_p_values[model_id] for model_id in model_ids])
        holm_adjusted = adjusted[model_ids.index(current_trial_id)]

    ledger = _validate_trial_ledger(
        trial_ledger,
        protocol=protocol,
        protocol_version=protocol_version,
        validation_dates=common_dates if dates_are_common else (),
        pbo_matrix=pbo.matrix,
        known_research_attempt_ids=known_research_attempt_ids,
    )
    dsr_probability, dsr_reason = _deflated_sharpe_probability(
        ledger.matrix,
        current_trial_id=current_trial_id,
        baseline_values=baseline_values,
        validation_dates=common_dates if dates_are_common else (),
        block_length=dependence_block_length,
    )
    dsr_status: EvidenceStatus
    if dsr_probability is None:
        dsr_status = "unavailable"
        if ledger.status != "pass":
            dsr_reason = f"Deflated Sharpe unavailable: {ledger.reason}"
    elif dsr_probability >= float(protocol.thresholds.minimum_deflated_sharpe_probability):
        dsr_status = "pass"
    else:
        dsr_status = "fail"

    metrics = {
        "paired_mean": _mean(paired_returns),
        "benchmark_excess": benchmark_excess,
        "stress_return": stress_return,
        "maximum_drawdown": maximum_drawdown,
        "profit_factor": profit_factor,
        "bootstrap_lower": bootstrap_lower,
        "holm_adjusted": holm_adjusted,
        "dsr_probability": dsr_probability,
        "coverage_ratio": coverage_ratio,
    }
    gates = _build_gates(
        execution_plan_matches_protocol=execution_plan_matches_protocol,
        execution_start_date=execution_start_date,
        execution_end_date=execution_end_date,
        execution_rebalance_step_sessions=execution_rebalance_step_sessions,
        execution_lookback_days=execution_lookback_days,
        dates_are_common=dates_are_common,
        common_date_count=len(common_dates),
        completed_trade_count=completed_trade_count,
        positive_subperiod_count=positive_subperiod_count,
        subperiod_count=len(subperiods),
        profit_factor_is_infinite=profit_factor_is_infinite,
        pbo_status=pbo.status,
        pbo_probability=pbo.probability,
        dsr_status=dsr_status,
        metrics=metrics,
        protocol=protocol,
    )
    status = _aggregate_gate_status(gates)
    reasons = tuple(gate.reason for gate in gates if gate.status != "pass")
    if status == "pass":
        reasons = (
            "Historical development gates passed; this only permits a separately "
            "frozen confirmatory-forward test and never official release.",
        )

    rounded_metrics = {key: _rounded(value) for key, value in metrics.items()}
    payload: dict[str, object] = {
        "validation_schema_version": _validation_schema_version(protocol_version),
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.protocol_digest,
        "experiment_registry_digest": protocol.experiment_registry.registry_digest,
        "evidence_window": "development",
        "evidence_class": "exploratory_development_evidence",
        "status": status,
        "historical_gate_status": status,
        "deployment_scope": "shadow_only",
        "eligible_for_confirmatory_forward": status == "pass",
        "official_release_allowed": False,
        "execution_start_date": execution_start_date,
        "execution_end_date": execution_end_date,
        "execution_rebalance_step_sessions": execution_rebalance_step_sessions,
        "execution_lookback_days": execution_lookback_days,
        "execution_plan_matches_protocol": execution_plan_matches_protocol,
        "baseline_row_count": len(baseline),
        "challenger_row_count": len(challenger),
        "completed_trade_count": completed_trade_count,
        "valid_outcome_count": valid_outcome_count,
        "expected_outcome_count": expected_outcome_count,
        "valid_outcome_coverage_ratio": rounded_metrics["coverage_ratio"],
        "baseline_rebalance_date_count": len(baseline_dates),
        "challenger_rebalance_date_count": len(challenger_dates),
        "common_rebalance_date_count": len(common_dates),
        "dates_are_common": dates_are_common,
        "baseline_only_dates": baseline_only_dates,
        "challenger_only_dates": challenger_only_dates,
        "dependence_block_length": dependence_block_length,
        "effective_independent_block_count": len(common_dates) // dependence_block_length,
        "paired_mean_net_excess_pct": rounded_metrics["paired_mean"],
        "cumulative_benchmark_excess_return_pct": rounded_metrics["benchmark_excess"],
        "cumulative_stress_cost_adjusted_return_pct": rounded_metrics["stress_return"],
        "maximum_drawdown_pct": rounded_metrics["maximum_drawdown"],
        "profit_factor": rounded_metrics["profit_factor"],
        "profit_factor_is_infinite": profit_factor_is_infinite,
        "bootstrap_one_sided_95_lower_bound_pct": rounded_metrics["bootstrap_lower"],
        "positive_edge_p_value": _rounded(positive_p_value),
        "holm_adjusted_positive_edge_p_value": rounded_metrics["holm_adjusted"],
        "holm_family_size": holm_family_size,
        "positive_subperiod_count": positive_subperiod_count,
        "required_positive_subperiod_count": (protocol.thresholds.minimum_positive_subperiods),
        "subperiod_count": len(subperiods),
        "subperiods": [item.model_dump(mode="json") for item in subperiods],
        "pbo_status": pbo.status,
        "pbo_probability": _rounded(pbo.probability),
        "pbo_reason": pbo.reason,
        "pbo_evidence_digest": pbo.evidence_digest,
        "trial_ledger_status": ledger.status,
        "trial_ledger_reason": ledger.reason,
        "trial_count": len(ledger.matrix or {}),
        "deflated_sharpe_status": dsr_status,
        "deflated_sharpe_probability": rounded_metrics["dsr_probability"],
        "deflated_sharpe_reason": dsr_reason,
        "gates": [item.model_dump(mode="json") for item in gates],
        "reasons": list(reasons),
    }
    return RankingV4HistoricalValidationEvaluation(
        **payload,
        evaluation_digest=_sha256(payload),
    )


def evaluate_ranking_v4_validation(
    baseline_returns: Sequence[ReturnObservationLike],
    challenger_returns: Sequence[ReturnObservationLike],
    **kwargs: object,
) -> RankingV4HistoricalValidationEvaluation:
    """Compatibility name for the historical-only V4 evaluator."""

    return evaluate_ranking_v4_historical_validation(
        baseline_returns,
        challenger_returns,
        **kwargs,
    )


class _PBOValidation:
    def __init__(
        self,
        *,
        status: EvidenceStatus,
        probability: float | None,
        reason: str,
        evidence_digest: str | None = None,
        matrix: dict[str, tuple[tuple[date, float], ...]] | None = None,
    ) -> None:
        self.status = status
        self.probability = probability
        self.reason = reason
        self.evidence_digest = evidence_digest
        self.matrix = matrix


class _LedgerValidation:
    def __init__(
        self,
        *,
        status: EvidenceStatus,
        reason: str,
        matrix: dict[str, tuple[tuple[date, float], ...]] | None = None,
    ) -> None:
        self.status = status
        self.reason = reason
        self.matrix = matrix


def _validate_pbo_evidence(
    evidence: Mapping[str, object] | None,
    *,
    validation_dates: Sequence[date],
    baseline_values: Sequence[float],
    challenger_values: Sequence[float],
    protocol: RankingV4Protocol,
    protocol_version: ProtocolVersion,
) -> _PBOValidation:
    statistics = protocol.statistics_definition
    model_ids = statistics.pbo_model_ids
    current_trial_id = _current_trial_id(protocol_version)
    evidence_schema_version = _pbo_evidence_schema_version(protocol_version)
    matrix_schema_version = _pbo_matrix_schema_version(protocol_version)
    if not isinstance(evidence, Mapping):
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason=(
                "PBO unavailable: a digest-backed V4 eight-model common-date "
                "matrix was not provided."
            ),
        )
    rejection_reason = evidence.get("rejection_reason")
    if rejection_reason:
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason=f"PBO unavailable: {str(rejection_reason).strip()}",
        )

    evidence_digest = evidence.get("evidence_digest")
    matrix_digest = evidence.get("matrix_digest")
    if (
        not isinstance(evidence_digest, str)
        or not _SHA256.fullmatch(evidence_digest)
        or evidence_digest
        != _sha256({key: value for key, value in evidence.items() if key != "evidence_digest"})
    ):
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason="PBO unavailable: evidence digest is missing or invalid.",
        )
    if not isinstance(matrix_digest, str) or not _SHA256.fullmatch(matrix_digest):
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason="PBO unavailable: matrix digest is missing or invalid.",
        )
    if (
        evidence.get("evidence_schema_version") != evidence_schema_version
        or evidence.get("method") != statistics.pbo_method
        or evidence.get("scope") != statistics.pbo_scope
        or evidence.get("search_process_coverage") != "partial"
        or evidence.get("purge_rebalance_cohorts") != statistics.pbo_purge_rebalance_cohorts
        or evidence.get("purge_rebalance_cohorts") != 2
        or evidence.get("block_count") != statistics.pbo_block_count
        or evidence.get("registered_model_ids") != list(model_ids)
        or evidence.get("model_count") != len(model_ids)
        or (
            protocol_version != "4.1"
            and evidence.get("minimum_dates_per_half")
            != protocol.thresholds.minimum_rebalance_dates
        )
    ):
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason=(
                "PBO unavailable: evidence must identify the frozen V4 eight-model "
                "family, partial search scope, implemented method, and purge=2."
            ),
        )

    sparse_matrix: (
        dict[
            str,
            tuple[tuple[date, float | None], ...],
        ]
        | None
    ) = None
    if protocol_version == "4.4":
        sparse_matrix, matrix_reason = _parse_v44_pbo_return_matrix(
            evidence.get("model_return_matrix"),
            expected_ids=model_ids,
        )
        parsed_matrix = sparse_matrix
    else:
        matrix, matrix_reason = _parse_return_matrix(
            evidence.get("model_return_matrix"),
            expected_ids=model_ids,
        )
        parsed_matrix = matrix
    if parsed_matrix is None:
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason=f"PBO unavailable: {matrix_reason}",
        )
    serialized = {
        model_id: [
            {
                "rebalance_date": rebalance_date.isoformat(),
                "net_return": net_return,
            }
            for rebalance_date, net_return in rows
        ]
        for model_id, rows in parsed_matrix.items()
    }
    if matrix_digest != _pbo_matrix_digest(
        serialized,
        schema_version=matrix_schema_version,
    ):
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason="PBO unavailable: matrix digest does not match matrix content.",
        )
    recomputed = evaluate_ranking_v4_cscv_pbo(
        {
            model_id: [
                RankingV4DatedModelReturn(
                    rebalance_date=rebalance_date,
                    net_return=net_return,
                )
                for rebalance_date, net_return in rows
            ]
            for model_id, rows in parsed_matrix.items()
        },
        protocol_version=protocol_version,
    )
    if recomputed != dict(evidence):
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason=(
                "PBO unavailable: supplied statistics do not match an independent "
                "CSCV recomputation from the verified return matrix."
            ),
        )

    if sparse_matrix is not None:
        matrix = {
            model_id: tuple(
                (rebalance_date, 0.0 if net_return is None else net_return)
                for rebalance_date, net_return in rows
            )
            for model_id, rows in sparse_matrix.items()
        }

    matrix_dates = tuple(item[0] for item in matrix["constraint_matched_baseline"])
    if (
        tuple(validation_dates) != matrix_dates
        or tuple(baseline_values)
        != tuple(item[1] for item in matrix["constraint_matched_baseline"])
        or tuple(challenger_values) != tuple(item[1] for item in matrix[current_trial_id])
    ):
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason=(
                "PBO unavailable: matrix calendar or baseline/challenger returns "
                "do not exactly match the validation evidence."
            ),
        )

    probability_value = evidence.get("probability")
    if (
        isinstance(probability_value, bool)
        or not isinstance(probability_value, (int, float))
        or not math.isfinite(float(probability_value))
        or not 0 <= float(probability_value) <= 1
        or not isinstance(evidence.get("fold_count"), int)
        or int(evidence["fold_count"]) <= 0
        or evidence.get("date_count") != len(matrix_dates)
    ):
        return _PBOValidation(
            status="unavailable",
            probability=None,
            reason="PBO unavailable: probability, fold count, or date count is invalid.",
        )
    probability = float(probability_value)
    maximum = float(protocol.thresholds.maximum_probability_of_backtest_overfit)
    if probability <= maximum:
        return _PBOValidation(
            status="pass",
            probability=probability,
            evidence_digest=evidence_digest,
            matrix=matrix,
            reason=(
                f"Frozen eight-model partial-scope PBO {probability:.2%} is within "
                f"the {maximum:.0%} historical limit."
            ),
        )
    return _PBOValidation(
        status="fail",
        probability=probability,
        evidence_digest=evidence_digest,
        matrix=matrix,
        reason=(
            f"Frozen eight-model partial-scope PBO {probability:.2%} exceeds "
            f"the {maximum:.0%} historical limit."
        ),
    )


def _validate_trial_ledger(
    ledger_like: RankingV4TrialLedgerEvidence | Mapping[str, object] | None,
    *,
    protocol: RankingV4Protocol,
    protocol_version: ProtocolVersion,
    validation_dates: Sequence[date],
    pbo_matrix: Mapping[str, Sequence[tuple[date, float]]] | None,
    known_research_attempt_ids: Sequence[str],
) -> _LedgerValidation:
    registry = protocol.experiment_registry
    model_ids = protocol.statistics_definition.pbo_model_ids
    current_trial_id = _current_trial_id(protocol_version)
    expected_schema_version = _trial_ledger_schema_version(protocol_version)
    expected_ledger_id = _trial_ledger_id(protocol_version)
    if ledger_like is None:
        return _LedgerValidation(
            status="unavailable",
            reason=(
                "immutable trial ledger was not provided; the number and return "
                "matrix of all known research attempts are unknown"
            ),
        )
    try:
        ledger = (
            ledger_like
            if isinstance(ledger_like, RankingV4TrialLedgerEvidence)
            else RankingV4TrialLedgerEvidence.model_validate(ledger_like)
        )
    except ValueError:
        return _LedgerValidation(
            status="unavailable",
            reason="trial ledger payload is malformed",
        )
    if ledger.schema_version != expected_schema_version:
        return _LedgerValidation(status="unavailable", reason="trial ledger schema is invalid")
    if ledger.ledger_id != expected_ledger_id:
        return _LedgerValidation(status="unavailable", reason="trial ledger id is invalid")
    if not _SHA256.fullmatch(ledger.ledger_digest) or ledger.ledger_digest != _sha256(
        ledger.stable_payload()
    ):
        return _LedgerValidation(
            status="unavailable",
            reason="trial ledger digest is missing or invalid",
        )
    if ledger.experiment_registry_digest != registry.registry_digest:
        return _LedgerValidation(
            status="unavailable",
            reason="trial ledger is not bound to the active immutable experiment registry",
        )
    if protocol_version != "4.1" and (
        not registry.historical_trial_inventory_complete
        or registry.historical_trial_inventory_digest is None
        or not registry.historical_trial_return_series_digests
    ):
        return _LedgerValidation(
            status="unavailable",
            reason=(
                "the active Ranking V4 registry has no audited complete inventory and "
                "return-series digests for all historical experiments; supplied "
                "trial rows cannot manufacture that evidence"
            ),
        )
    if not ledger.immutable or not ledger.covers_all_known_attempts:
        return _LedgerValidation(
            status="unavailable",
            reason=(
                "trial ledger does not immutably and explicitly cover every known research attempt"
            ),
        )
    normalized_attempt_ids = tuple(
        sorted({str(item).strip() for item in known_research_attempt_ids if str(item).strip()})
    )
    if (
        ledger.research_attempt_ids != normalized_attempt_ids
        or ledger.research_attempt_inventory_digest
        != _sha256({"research_attempt_ids": list(normalized_attempt_ids)})
    ):
        return _LedgerValidation(
            status="unavailable",
            reason="trial ledger does not match the persisted research-attempt inventory",
        )
    if (
        ledger.known_trial_ids != _required_trial_ids(normalized_attempt_ids, protocol=protocol)
        or ledger.current_trial_id != current_trial_id
    ):
        return _LedgerValidation(
            status="unavailable",
            reason=(
                "known trial ids do not exactly match the frozen V4 family and "
                "registered predecessor experiments"
            ),
        )
    raw_matrix = {item.trial_id: item.returns for item in ledger.trial_series}
    if len(raw_matrix) != len(ledger.trial_series) or set(raw_matrix) != set(
        ledger.known_trial_ids
    ):
        return _LedgerValidation(
            status="unavailable",
            reason="trial return matrix does not exactly cover all declared known trials",
        )
    matrix, reason = _parse_return_matrix(
        {
            trial_id: [
                {
                    "rebalance_date": item.rebalance_date.isoformat(),
                    "net_return": item.net_return_pct,
                }
                for item in rows
            ]
            for trial_id, rows in raw_matrix.items()
        },
        expected_ids=ledger.known_trial_ids,
    )
    if matrix is None:
        return _LedgerValidation(
            status="unavailable",
            reason=f"trial return matrix is incomplete: {reason}",
        )
    reference_dates = tuple(item[0] for item in next(iter(matrix.values())))
    if reference_dates != tuple(validation_dates):
        return _LedgerValidation(
            status="unavailable",
            reason="trial ledger calendar does not exactly match validation dates",
        )
    if not set(model_ids).issubset(matrix):
        return _LedgerValidation(
            status="unavailable",
            reason="trial ledger omits one or more frozen V4 family members",
        )
    if pbo_matrix is None:
        return _LedgerValidation(
            status="unavailable",
            reason="trial ledger cannot be reconciled because valid V4 PBO evidence is absent",
        )
    for model_id in model_ids:
        if tuple(matrix[model_id]) != tuple(pbo_matrix[model_id]):
            return _LedgerValidation(
                status="unavailable",
                reason=f"trial ledger disagrees with PBO matrix for {model_id}",
            )
    return _LedgerValidation(
        status="pass",
        reason=(
            f"Immutable ledger covers all {len(matrix)} declared research attempts "
            "on the complete common-date matrix."
        ),
        matrix=matrix,
    )


def _deflated_sharpe_probability(
    matrix: Mapping[str, Sequence[tuple[date, float]]] | None,
    *,
    current_trial_id: str,
    baseline_values: Sequence[float],
    validation_dates: Sequence[date],
    block_length: int,
) -> tuple[float | None, str]:
    if matrix is None:
        return None, "Deflated Sharpe unavailable: immutable complete trial evidence is absent."
    if current_trial_id not in matrix or len(matrix) < 2:
        return None, "Deflated Sharpe unavailable: current or comparison trials are absent."
    if not validation_dates or len(validation_dates) != len(baseline_values):
        return None, "Deflated Sharpe unavailable: validation calendar is absent."

    block_matrix: dict[str, tuple[float, ...]] = {}
    for trial_id, rows in matrix.items():
        dates = tuple(item[0] for item in rows)
        if dates != tuple(validation_dates):
            return None, "Deflated Sharpe unavailable: trial calendars are incomplete."
        excess = tuple(
            item[1] - baseline for item, baseline in zip(rows, baseline_values, strict=True)
        )
        blocks = _non_overlapping_block_means(excess, block_length=block_length)
        if len(blocks) < 3:
            return None, "Deflated Sharpe unavailable: fewer than three independent blocks."
        block_matrix[trial_id] = blocks

    trial_sharpes: list[float] = []
    for trial_id in sorted(block_matrix):
        sharpe = _sample_sharpe(
            block_matrix[trial_id],
            allow_zero_series=trial_id == "constraint_matched_baseline",
        )
        if sharpe is None:
            return (
                None,
                f"Deflated Sharpe unavailable: trial {trial_id} has invalid dispersion.",
            )
        trial_sharpes.append(sharpe)
    current_blocks = block_matrix[current_trial_id]
    current_sharpe = _sample_sharpe(current_blocks)
    if current_sharpe is None:
        return None, "Deflated Sharpe unavailable: current trial Sharpe is invalid."
    sharpe_std = _sample_standard_deviation(trial_sharpes)
    if sharpe_std is None or sharpe_std <= 0:
        return None, "Deflated Sharpe unavailable: cross-trial Sharpe dispersion is invalid."
    expected_maximum = _expected_maximum_sharpe(
        mean_sharpe=math.fsum(trial_sharpes) / len(trial_sharpes),
        sharpe_standard_deviation=sharpe_std,
        trial_count=len(trial_sharpes),
    )
    if expected_maximum is None:
        return None, "Deflated Sharpe unavailable: expected maximum is invalid."
    skewness, kurtosis = _sample_skewness_and_kurtosis(current_blocks)
    denominator = (
        1 - skewness * current_sharpe + ((kurtosis - 1) / 4) * current_sharpe * current_sharpe
    )
    if not math.isfinite(denominator) or denominator <= 0:
        return None, "Deflated Sharpe unavailable: non-normality correction is invalid."
    statistic = (
        (current_sharpe - expected_maximum)
        * math.sqrt(len(current_blocks) - 1)
        / math.sqrt(denominator)
    )
    probability = NormalDist().cdf(statistic)
    if not math.isfinite(probability):
        return None, "Deflated Sharpe unavailable: probability is non-finite."
    return (
        probability,
        (
            f"Deflated Sharpe used {len(current_blocks)} non-overlapping "
            f"three-cohort blocks and all {len(trial_sharpes)} ledger trials."
        ),
    )


def holm_bonferroni(p_values: Sequence[float]) -> list[float]:
    validated = [_validated_probability(value) for value in p_values]
    if not validated:
        return []
    ordered = sorted(enumerate(validated), key=lambda item: (item[1], item[0]))
    running_max = 0.0
    adjusted_by_index: dict[int, float] = {}
    total = len(ordered)
    for rank, (original_index, p_value) in enumerate(ordered):
        running_max = max(running_max, min(1.0, (total - rank) * p_value))
        adjusted_by_index[original_index] = running_max
    return [round(adjusted_by_index[index], 12) for index in range(total)]


def _registered_family_p_values(
    matrix: Mapping[str, Sequence[tuple[date, float]]],
    *,
    samples: int,
    seed: int,
    model_ids: Sequence[str],
    block_length: int,
) -> dict[str, float]:
    baseline = matrix["constraint_matched_baseline"]
    result: dict[str, float] = {}
    for index, model_id in enumerate(model_ids):
        values = tuple(
            model_row[1] - baseline_row[1]
            for model_row, baseline_row in zip(
                matrix[model_id],
                baseline,
                strict=True,
            )
        )
        result[model_id] = (
            _block_sign_flip_p_value(
                values,
                samples=samples,
                seed=seed + index,
                block_length=block_length,
            )
            or 1.0
        )
    return result


def _build_gates(
    *,
    execution_plan_matches_protocol: bool,
    execution_start_date: date | None,
    execution_end_date: date | None,
    execution_rebalance_step_sessions: int | None,
    execution_lookback_days: int | None,
    dates_are_common: bool,
    common_date_count: int,
    completed_trade_count: int,
    positive_subperiod_count: int,
    subperiod_count: int,
    profit_factor_is_infinite: bool,
    pbo_status: EvidenceStatus,
    pbo_probability: float | None,
    dsr_status: EvidenceStatus,
    metrics: Mapping[str, float | None],
    protocol: RankingV4Protocol,
) -> tuple[RankingV4ValidationGate, ...]:
    threshold = protocol.thresholds
    gates = [
        _gate(
            "preregistered_execution_plan",
            "pass" if execution_plan_matches_protocol else "fail",
            (
                f"{execution_start_date}..{execution_end_date}; "
                f"step={execution_rebalance_step_sessions}; "
                f"lookback={execution_lookback_days}"
            ),
            (
                f"{RANKING_V4_DEVELOPMENT_START}..{RANKING_V4_DEVELOPMENT_END}; "
                f"step={protocol.temporal_definition.rebalance_step_sessions}; "
                f"lookback={RANKING_V4_DEVELOPMENT_LOOKBACK_DAYS}"
            ),
            "Historical development gates are valid only for the frozen execution plan.",
        ),
        _gate(
            "common_rebalance_calendar",
            "pass" if dates_are_common else "fail",
            "identical" if dates_are_common else "mismatched",
            "exactly identical non-empty rebalance_date sets",
            "Baseline and challenger must be aggregated on the same genuine dates.",
        ),
        _gate(
            "minimum_rebalance_dates",
            "pass" if common_date_count >= threshold.minimum_rebalance_dates else "insufficient",
            str(common_date_count),
            f">={threshold.minimum_rebalance_dates}",
            "Historical inference requires the preregistered minimum date count.",
        ),
        _gate(
            "completed_trades",
            "pass"
            if completed_trade_count >= threshold.minimum_completed_trades
            else "insufficient",
            str(completed_trade_count),
            f">={threshold.minimum_completed_trades}",
            "The completed-trade gate measures executable evidence, not candidate rows.",
        ),
        _numeric_gate(
            "valid_outcome_coverage",
            metrics["coverage_ratio"],
            comparator=lambda value: value >= float(threshold.minimum_valid_outcome_coverage_ratio),
            observed_format=".1%",
            required=f">={threshold.minimum_valid_outcome_coverage_ratio:.0%}",
            reason="Missing outcomes cannot be silently dropped.",
        ),
        _numeric_gate(
            "positive_benchmark_excess",
            metrics["benchmark_excess"],
            comparator=lambda value: value > 0,
            observed_format="+.6f",
            required=">0 cumulative percentage points",
            reason="V4 must beat the constraint-matched baseline after normal costs.",
        ),
        _numeric_gate(
            "positive_stress_cost_return",
            metrics["stress_return"],
            comparator=lambda value: value > 0,
            observed_format="+.6f",
            required=">0 cumulative return after stress costs",
            reason="The strategy must remain profitable under frozen stress costs.",
        ),
        _numeric_gate(
            "maximum_drawdown",
            metrics["maximum_drawdown"],
            comparator=lambda value: value >= float(threshold.maximum_drawdown_floor_pct),
            observed_format=".6f",
            required=f">={threshold.maximum_drawdown_floor_pct}%",
            reason="Historical drawdown must remain inside the preregistered floor.",
        ),
        _gate(
            "minimum_profit_factor",
            (
                "pass"
                if profit_factor_is_infinite
                or (
                    metrics["profit_factor"] is not None
                    and metrics["profit_factor"] >= float(threshold.minimum_profit_factor)
                )
                else "fail"
                if metrics["profit_factor"] is not None
                else "unavailable"
            ),
            (
                "infinite"
                if profit_factor_is_infinite
                else _format_optional(metrics["profit_factor"], ".6f")
            ),
            f">={threshold.minimum_profit_factor}",
            "Gross gains must cover gross losses by the frozen margin.",
        ),
        _numeric_gate(
            "bootstrap_positive_lower_bound",
            metrics["bootstrap_lower"],
            comparator=lambda value: value > 0,
            observed_format="+.6f",
            required="one-sided 95% lower bound >0",
            reason="Three-cohort moving-block bootstrap must support positive edge.",
        ),
        _numeric_gate(
            "holm_registered_family",
            metrics["holm_adjusted"],
            comparator=lambda value: value <= float(threshold.maximum_holm_adjusted_p_value),
            observed_format=".6f",
            required=(
                f"<={threshold.maximum_holm_adjusted_p_value} across the frozen eight-model family"
            ),
            reason="Holm adjustment must include every preregistered V4 model.",
        ),
        _gate(
            "positive_subperiods",
            (
                "pass"
                if subperiod_count == threshold.required_subperiods
                and positive_subperiod_count >= threshold.minimum_positive_subperiods
                else "fail"
                if subperiod_count == threshold.required_subperiods
                else "insufficient"
            ),
            f"{positive_subperiod_count}/{subperiod_count}",
            (f">={threshold.minimum_positive_subperiods}/{threshold.required_subperiods}"),
            "At least four of five contiguous development periods must be positive.",
        ),
        _gate(
            "pbo",
            pbo_status,
            _format_optional(pbo_probability, ".2%"),
            f"<={threshold.maximum_probability_of_backtest_overfit:.0%}",
            "PBO must use the digest-backed partial-scope V4 eight-model matrix.",
        ),
        _gate(
            "deflated_sharpe",
            dsr_status,
            _format_optional(metrics["dsr_probability"], ".2%"),
            f">={threshold.minimum_deflated_sharpe_probability:.0%}",
            ("DSR is computable only from an immutable complete ledger of all known attempts."),
        ),
    ]
    return tuple(gates)


def _gate(
    key: str,
    status: GateStatus,
    observed: str,
    required: str,
    reason: str,
) -> RankingV4ValidationGate:
    return RankingV4ValidationGate(
        key=key,
        status=status,
        observed=observed,
        required=required,
        reason=reason,
    )


def _numeric_gate(
    key: str,
    value: float | None,
    *,
    comparator: Callable[[float], bool],
    observed_format: str,
    required: str,
    reason: str,
) -> RankingV4ValidationGate:
    status: GateStatus = "unavailable" if value is None else "pass" if comparator(value) else "fail"
    return _gate(
        key,
        status,
        _format_optional(value, observed_format),
        required,
        reason,
    )


def _aggregate_gate_status(
    gates: Sequence[RankingV4ValidationGate],
) -> ValidationStatus:
    if any(item.status == "fail" for item in gates):
        return "fail"
    if all(item.status == "pass" for item in gates):
        return "pass"
    return "insufficient"


def _coerce_observation(item: ReturnObservationLike) -> RankingV4ReturnObservation:
    if isinstance(item, RankingV4ReturnObservation):
        return item
    if isinstance(item, tuple):
        if len(item) == 2:
            return RankingV4ReturnObservation(
                rebalance_date=item[0],
                net_return_pct=item[1],
            )
        if len(item) == 3:
            return RankingV4ReturnObservation(
                rebalance_date=item[0],
                net_return_pct=item[1],
                stress_net_return_pct=item[2],
            )
    return RankingV4ReturnObservation.model_validate(item)


def _cluster_returns(
    observations: Sequence[RankingV4ReturnObservation],
) -> tuple[dict[date, float], dict[date, float | None]]:
    normal: dict[date, list[float]] = defaultdict(list)
    stress: dict[date, list[float | None]] = defaultdict(list)
    for item in observations:
        normal[item.rebalance_date].append(item.net_return_pct)
        stress[item.rebalance_date].append(item.stress_net_return_pct)
    clustered_normal = {
        rebalance_date: math.fsum(sorted(values)) / len(values)
        for rebalance_date, values in normal.items()
    }
    clustered_stress = {
        rebalance_date: (
            math.fsum(sorted(float(value) for value in values)) / len(values)
            if values and all(value is not None for value in values)
            else None
        )
        for rebalance_date, values in stress.items()
    }
    return clustered_normal, clustered_stress


def _one_sided_moving_block_lower_bound(
    values: Sequence[float],
    *,
    samples: int,
    seed: int,
    block_length: int,
) -> float | None:
    if len(values) < block_length * 2:
        return None
    blocks = tuple(
        tuple(values[start : start + block_length])
        for start in range(len(values) - block_length + 1)
    )
    generator = random.Random(seed)
    blocks_per_sample = math.ceil(len(values) / block_length)
    means: list[float] = []
    for _ in range(samples):
        sample: list[float] = []
        for _ in range(blocks_per_sample):
            sample.extend(generator.choice(blocks))
        means.append(math.fsum(sample[: len(values)]) / len(values))
    means.sort()
    return means[max(0, math.floor((len(means) - 1) * 0.05))]


def _block_sign_flip_p_value(
    values: Sequence[float],
    *,
    samples: int,
    seed: int,
    block_length: int,
) -> float | None:
    complete_count = (len(values) // block_length) * block_length
    if complete_count < block_length:
        return None
    complete = tuple(values[:complete_count])
    observed = math.fsum(complete) / complete_count
    if observed <= 0:
        return 1.0
    block_sums = tuple(
        math.fsum(complete[offset : offset + block_length])
        for offset in range(0, complete_count, block_length)
    )
    generator = random.Random(seed)
    exceedances = 0
    for _ in range(samples):
        null_mean = (
            math.fsum(value if generator.random() >= 0.5 else -value for value in block_sums)
            / complete_count
        )
        if null_mean >= observed:
            exceedances += 1
    return (exceedances + 1) / (samples + 1)


def _contiguous_subperiods(
    paired_values: Sequence[tuple[date, float]],
    *,
    required_subperiods: int,
) -> tuple[RankingV4SubperiodResult, ...]:
    if not paired_values or required_subperiods <= 0:
        return ()
    base_size, remainder = divmod(len(paired_values), required_subperiods)
    offset = 0
    results: list[RankingV4SubperiodResult] = []
    for period in range(1, required_subperiods + 1):
        size = base_size + (1 if period <= remainder else 0)
        if size == 0:
            continue
        chunk = paired_values[offset : offset + size]
        offset += size
        mean = math.fsum(value for _, value in chunk) / size
        results.append(
            RankingV4SubperiodResult(
                period=period,
                start_date=chunk[0][0],
                end_date=chunk[-1][0],
                rebalance_date_count=size,
                mean_paired_net_excess_pct=round(mean, 12),
                positive=mean > 0,
            )
        )
    return tuple(results)


def _parse_return_matrix(
    payload: object,
    *,
    expected_ids: Sequence[str],
) -> tuple[
    dict[str, tuple[tuple[date, float], ...]] | None,
    str | None,
]:
    if not isinstance(payload, Mapping) or set(payload) != set(expected_ids):
        return None, "model identifiers do not exactly match the declared family"
    matrix: dict[str, tuple[tuple[date, float], ...]] = {}
    reference_dates: tuple[date, ...] | None = None
    try:
        for model_id in sorted(expected_ids):
            raw_rows = payload[model_id]
            if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
                return None, f"rows for {model_id} are not a sequence"
            rows: list[tuple[date, float]] = []
            for raw_row in raw_rows:
                if isinstance(raw_row, RankingV4ReturnObservation):
                    rebalance_date = raw_row.rebalance_date
                    net_return = raw_row.net_return_pct
                elif isinstance(raw_row, Mapping):
                    rebalance_date = date.fromisoformat(str(raw_row["rebalance_date"]))
                    net_return = float(raw_row.get("net_return", raw_row.get("net_return_pct")))
                else:
                    return None, f"row for {model_id} is malformed"
                if not math.isfinite(net_return):
                    return None, f"return for {model_id} is non-finite"
                rows.append((rebalance_date, net_return))
            dates = tuple(item[0] for item in rows)
            if not dates or any(right <= left for left, right in zip(dates, dates[1:])):
                return None, f"calendar for {model_id} is empty, duplicate, or unordered"
            if reference_dates is None:
                reference_dates = dates
            elif dates != reference_dates:
                return None, "model calendars are not exactly identical"
            matrix[model_id] = tuple(rows)
    except (KeyError, TypeError, ValueError):
        return None, "matrix contains malformed dates or returns"
    return matrix, None


def _parse_v44_pbo_return_matrix(
    payload: object,
    *,
    expected_ids: Sequence[str],
) -> tuple[
    dict[str, tuple[tuple[date, float | None], ...]] | None,
    str | None,
]:
    if not isinstance(payload, Mapping) or set(payload) != set(expected_ids):
        return None, "model identifiers do not exactly match the declared family"
    matrix: dict[str, tuple[tuple[date, float | None], ...]] = {}
    reference_dates: tuple[date, ...] | None = None
    try:
        for model_id in sorted(expected_ids):
            raw_rows = payload[model_id]
            if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
                return None, f"rows for {model_id} are not a sequence"
            rows: list[tuple[date, float | None]] = []
            for raw_row in raw_rows:
                if not isinstance(raw_row, Mapping):
                    return None, f"row for {model_id} is malformed"
                rebalance_date = date.fromisoformat(str(raw_row["rebalance_date"]))
                if "net_return" in raw_row:
                    raw_return = raw_row["net_return"]
                elif "net_return_pct" in raw_row:
                    raw_return = raw_row["net_return_pct"]
                else:
                    return None, f"row for {model_id} is malformed"
                if raw_return is None:
                    net_return = None
                else:
                    net_return = float(raw_return)
                    if not math.isfinite(net_return):
                        return None, f"return for {model_id} is non-finite"
                rows.append((rebalance_date, net_return))
            dates = tuple(item[0] for item in rows)
            if not dates or any(right <= left for left, right in zip(dates, dates[1:])):
                return None, f"calendar for {model_id} is empty, duplicate, or unordered"
            if reference_dates is None:
                reference_dates = dates
            elif dates != reference_dates:
                return None, "model calendars are not exactly identical"
            matrix[model_id] = tuple(rows)
    except (KeyError, TypeError, ValueError):
        return None, "matrix contains malformed dates or returns"
    return matrix, None


def _pbo_matrix_digest(
    payload: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    schema_version: str = _PBO_MATRIX_SCHEMA_VERSION,
) -> str:
    canonical = {
        "schema_version": schema_version,
        "model_return_matrix": {
            model_id: [
                {
                    "rebalance_date": str(row["rebalance_date"]),
                    "net_return_hex": (
                        None if row["net_return"] is None else float(row["net_return"]).hex()
                    ),
                }
                for row in rows
            ]
            for model_id, rows in sorted(payload.items())
        },
    }
    return _sha256(canonical)


def _compound_return(values: Sequence[float]) -> float:
    wealth = 1.0
    for value in values:
        wealth *= 1.0 + value / 100.0
    return (wealth - 1.0) * 100.0


def _maximum_drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    wealth = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for value in values:
        wealth *= 1.0 + value / 100.0
        peak = max(peak, wealth)
        if peak > 0:
            maximum_drawdown = min(maximum_drawdown, (wealth / peak - 1.0) * 100.0)
    return maximum_drawdown


def _profit_factor(values: Sequence[float]) -> tuple[float | None, bool]:
    if not values:
        return None, False
    gains = math.fsum(value for value in values if value > 0)
    losses = -math.fsum(value for value in values if value < 0)
    if losses == 0:
        return (None, gains > 0)
    return gains / losses, False


def _non_overlapping_block_means(
    values: Sequence[float],
    *,
    block_length: int,
) -> tuple[float, ...]:
    return tuple(
        math.fsum(values[offset : offset + block_length]) / block_length
        for offset in range(0, len(values), block_length)
        if len(values[offset : offset + block_length]) == block_length
    )


def _sample_sharpe(
    values: Sequence[float],
    *,
    allow_zero_series: bool = False,
) -> float | None:
    mean = _mean(values)
    standard_deviation = _sample_standard_deviation(values)
    if mean is None or standard_deviation is None:
        return None
    if standard_deviation == 0:
        return 0.0 if allow_zero_series and mean == 0 else None
    result = mean / standard_deviation
    return result if math.isfinite(result) else None


def _sample_standard_deviation(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = math.fsum(values) / len(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)
    if not math.isfinite(variance) or variance < 0:
        return None
    return math.sqrt(variance)


def _sample_skewness_and_kurtosis(values: Sequence[float]) -> tuple[float, float]:
    mean = math.fsum(values) / len(values)
    second = math.fsum((value - mean) ** 2 for value in values) / len(values)
    if second <= 0:
        return 0.0, 3.0
    third = math.fsum((value - mean) ** 3 for value in values) / len(values)
    fourth = math.fsum((value - mean) ** 4 for value in values) / len(values)
    return third / (second**1.5), fourth / (second**2)


def _expected_maximum_sharpe(
    *,
    mean_sharpe: float,
    sharpe_standard_deviation: float,
    trial_count: int,
) -> float | None:
    if trial_count < 2 or sharpe_standard_deviation <= 0:
        return None
    normal = NormalDist()
    euler_gamma = 0.5772156649015329
    first = normal.inv_cdf(1 - 1 / trial_count)
    second = normal.inv_cdf(1 - 1 / (trial_count * math.e))
    result = mean_sharpe + sharpe_standard_deviation * (
        (1 - euler_gamma) * first + euler_gamma * second
    )
    return result if math.isfinite(result) else None


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _validated_probability(value: float) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("p-values must be finite and between zero and one")
    return float(value)


def _format_optional(value: float | None, format_spec: str) -> str:
    return "unavailable" if value is None else format(value, format_spec)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 12)


def _sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            default=_json_default,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")
