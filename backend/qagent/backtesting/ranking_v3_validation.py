from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
import math
import random
from statistics import NormalDist
from typing import Literal

from pydantic import BaseModel, field_validator

from qagent.backtesting.ranking_v3_experiment_registry import RankingV3ExperimentRegistry
from qagent.backtesting.ranking_v3_pbo import (
    CSCV_PBO_METHOD,
    PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY,
    PBO_SEARCH_PROCESS_COVERAGE,
    RANKING_V3_FROZEN_PBO_MODEL_IDS,
)
from qagent.backtesting.ranking_v3_protocol import (
    RankingV3StatisticalDefinition,
    build_ranking_v3_protocol,
)


ValidationStatus = Literal["pass", "insufficient", "fail"]
GateStatus = Literal["pass", "insufficient", "fail", "unavailable"]

# V3 holds positions for up to 20 sessions and rebalances every 10 sessions.
# Adjacent rebalance cohorts therefore share roughly half of their return window.
_STATISTICS_DEFINITION = RankingV3StatisticalDefinition()
DEPENDENCE_BLOCK_LENGTH = _STATISTICS_DEFINITION.dependence_block_length
DEFAULT_BOOTSTRAP_SAMPLES = _STATISTICS_DEFINITION.bootstrap_samples
DEFAULT_PERMUTATION_SAMPLES = _STATISTICS_DEFINITION.permutation_samples
DEFAULT_RANDOM_SEED = _STATISTICS_DEFINITION.random_seed


class RankingV3ReturnObservation(BaseModel):
    rebalance_date: date
    net_return_pct: float

    @field_validator("net_return_pct")
    @classmethod
    def validate_finite_return(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("net_return_pct must be finite")
        return value


class RankingV3SubperiodResult(BaseModel):
    period: int
    start_date: date
    end_date: date
    rebalance_date_count: int
    mean_paired_net_excess_pct: float
    positive: bool


class RankingV3ValidationGate(BaseModel):
    key: str
    status: GateStatus
    observed: str
    required: str
    reason: str


class RankingV3ValidationEvaluation(BaseModel):
    protocol_id: str
    protocol_digest: str
    status: ValidationStatus
    statistical_gate_status: ValidationStatus
    deployment_scope: Literal["shadow_only"]
    official_release_allowed: bool = False
    baseline_row_count: int
    challenger_row_count: int
    completed_trade_count: int
    baseline_rebalance_date_count: int
    challenger_rebalance_date_count: int
    common_rebalance_date_count: int
    dependence_block_length: int
    effective_independent_block_count: int
    dates_are_common: bool
    baseline_only_dates: list[date]
    challenger_only_dates: list[date]
    paired_mean_net_excess_pct: float | None = None
    bootstrap_one_sided_95_lower_bound_pct: float | None = None
    positive_edge_p_value: float | None = None
    holm_adjusted_positive_edge_p_value: float | None = None
    holm_family_size: int
    holm_observed_prior_p_value_count: int
    holm_unobserved_prior_p_value_count: int
    holm_adjustment_method: Literal[
        "exact_holm_bonferroni",
        "conservative_bonferroni_unknown_prior_p_values",
        "unavailable",
    ]
    holm_adjustment_reason: str
    deflated_sharpe_status: Literal["pass", "fail", "unavailable"] = "unavailable"
    deflated_sharpe_probability: float | None = None
    deflated_sharpe_reason: str
    prior_experiment_count: int
    positive_subperiod_count: int
    required_positive_subperiod_count: int
    subperiod_count: int
    subperiods: list[RankingV3SubperiodResult]
    pbo_status: Literal["pass", "fail", "unavailable"] = "unavailable"
    pbo_probability: float | None = None
    pbo_reason: str
    gates: list[RankingV3ValidationGate]
    reasons: list[str]


ReturnObservationLike = RankingV3ReturnObservation | tuple[date, float] | Mapping[str, object]


def evaluate_ranking_v3_validation(
    baseline_returns: Sequence[ReturnObservationLike],
    challenger_returns: Sequence[ReturnObservationLike],
    *,
    completed_trade_count: int | None = None,
    additional_hypothesis_p_values: Sequence[float] = (),
    prior_experiment_count: int | None = None,
    pbo_evidence: Mapping[str, object] | None = None,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    permutation_samples: int = DEFAULT_PERMUTATION_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
) -> RankingV3ValidationEvaluation:
    """Evaluate V3 against a constraint-matched baseline on common rebalance dates.

    Multiple rows on one rebalance date are averaged before any paired inference.
    Because a 20-session holding period overlaps adjacent 10-session rebalance
    cohorts, inference uses two-cohort time blocks and a conservative effective
    sample count. This utility deliberately does not estimate PBO from one pair
    of return series. Without a genuine model-return matrix the result remains
    shadow-only.
    """
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    if permutation_samples <= 0:
        raise ValueError("permutation_samples must be positive")

    protocol = build_ranking_v3_protocol()
    thresholds = protocol.thresholds
    experiments = (
        protocol.prior_experiment_count
        if prior_experiment_count is None
        else prior_experiment_count
    )
    if experiments < 0:
        raise ValueError("prior_experiment_count must be non-negative")

    baseline = [_coerce_observation(item) for item in baseline_returns]
    challenger = [_coerce_observation(item) for item in challenger_returns]
    completed_trades = len(challenger) if completed_trade_count is None else completed_trade_count
    if completed_trades < 0:
        raise ValueError("completed_trade_count must be non-negative")

    additional_p_values = [_validated_p_value(value) for value in additional_hypothesis_p_values]
    if len(additional_p_values) > experiments:
        raise ValueError("observed prior p-value count cannot exceed prior_experiment_count")
    baseline_by_date = _cluster_daily_returns(baseline)
    challenger_by_date = _cluster_daily_returns(challenger)
    baseline_dates = set(baseline_by_date)
    challenger_dates = set(challenger_by_date)
    common_dates = sorted(baseline_dates & challenger_dates)
    baseline_only_dates = sorted(baseline_dates - challenger_dates)
    challenger_only_dates = sorted(challenger_dates - baseline_dates)
    dates_are_common = bool(baseline_dates) and baseline_dates == challenger_dates

    paired_values: list[tuple[date, float]] = []
    if dates_are_common:
        paired_values = [
            (
                rebalance_date,
                challenger_by_date[rebalance_date] - baseline_by_date[rebalance_date],
            )
            for rebalance_date in common_dates
        ]

    paired_returns = [value for _, value in paired_values]
    effective_block_count = _effective_independent_block_count(
        len(paired_returns),
        block_length=DEPENDENCE_BLOCK_LENGTH,
    )
    paired_mean = _mean(paired_returns)
    bootstrap_lower = _one_sided_bootstrap_lower_bound(
        paired_returns,
        samples=bootstrap_samples,
        seed=seed,
        block_length=DEPENDENCE_BLOCK_LENGTH,
    )
    positive_p_value = _positive_sign_flip_p_value(
        paired_returns,
        samples=permutation_samples,
        seed=seed + 1,
        block_length=DEPENDENCE_BLOCK_LENGTH,
    )
    holm_adjusted = None
    holm_family_size = experiments + 1
    unobserved_prior_p_value_count = experiments - len(additional_p_values)
    holm_adjustment_method: Literal[
        "exact_holm_bonferroni",
        "conservative_bonferroni_unknown_prior_p_values",
        "unavailable",
    ] = "unavailable"
    holm_adjustment_reason = (
        "The current positive-edge p-value is unavailable; multiple-testing "
        "adjustment cannot be evaluated."
    )
    if positive_p_value is not None:
        if unobserved_prior_p_value_count == 0:
            holm_adjusted = holm_bonferroni([positive_p_value, *additional_p_values])[0]
            holm_adjustment_method = "exact_holm_bonferroni"
            holm_adjustment_reason = (
                "All registered family members have observed, provenance-backed "
                "p-values; exact Holm-Bonferroni was applied."
            )
        else:
            holm_adjusted = min(1.0, holm_family_size * positive_p_value)
            holm_adjustment_method = "conservative_bonferroni_unknown_prior_p_values"
            holm_adjustment_reason = (
                f"{unobserved_prior_p_value_count} of {experiments} registered "
                "prior hypotheses lack provenance-backed p-values. The current "
                f"hypothesis therefore uses the fail-closed upper bound "
                f"min(1, {holm_family_size} * p) instead of silently shrinking "
                "the family."
            )
    dsr_probability, dsr_reason = _evaluate_deflated_sharpe_evidence(
        protocol.experiment_registry,
        pbo_evidence=pbo_evidence,
        paired_values=paired_values,
        registered_trial_count=experiments + 1,
        dependence_block_length=DEPENDENCE_BLOCK_LENGTH,
    )
    subperiods = _contiguous_subperiods(
        paired_values,
        required_subperiods=thresholds.required_subperiods,
    )
    positive_subperiod_count = sum(item.positive for item in subperiods)
    paired_mean_metric = _rounded(paired_mean)
    bootstrap_lower_metric = _rounded(bootstrap_lower)
    positive_p_value_metric = _rounded(positive_p_value)
    holm_adjusted_metric = _rounded(holm_adjusted)
    dsr_probability_metric = _rounded(dsr_probability)

    gates = _build_gates(
        dates_are_common=dates_are_common,
        common_date_count=len(common_dates),
        effective_block_count=effective_block_count,
        completed_trade_count=completed_trades,
        paired_mean=paired_mean_metric,
        bootstrap_lower=bootstrap_lower_metric,
        holm_adjusted_p_value=holm_adjusted_metric,
        dsr_probability=dsr_probability_metric,
        dsr_reason=dsr_reason,
        positive_subperiod_count=positive_subperiod_count,
        actual_subperiod_count=len(subperiods),
    )
    statistical_gate_status = _statistical_gate_status(gates)
    pbo_status, pbo_probability, pbo_reason = _evaluate_pbo_evidence(
        pbo_evidence,
        maximum_probability=thresholds.maximum_probability_of_backtest_overfit,
    )
    status: ValidationStatus
    if statistical_gate_status == "fail" or pbo_status == "fail":
        status = "fail"
    elif statistical_gate_status == "pass" and pbo_status == "pass":
        status = "pass"
    else:
        status = "insufficient"

    reasons = [gate.reason for gate in gates if gate.status != "pass"]
    if pbo_status != "pass":
        reasons.append(pbo_reason)
    if statistical_gate_status == "pass" and pbo_status == "unavailable":
        reasons.append(
            "All observable statistical gates passed, but PBO evidence is unavailable; "
            "official admission is not allowed."
        )

    return RankingV3ValidationEvaluation(
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.protocol_digest,
        status=status,
        statistical_gate_status=statistical_gate_status,
        deployment_scope="shadow_only",
        baseline_row_count=len(baseline),
        challenger_row_count=len(challenger),
        completed_trade_count=completed_trades,
        baseline_rebalance_date_count=len(baseline_dates),
        challenger_rebalance_date_count=len(challenger_dates),
        common_rebalance_date_count=len(common_dates),
        dependence_block_length=DEPENDENCE_BLOCK_LENGTH,
        effective_independent_block_count=effective_block_count,
        dates_are_common=dates_are_common,
        baseline_only_dates=baseline_only_dates,
        challenger_only_dates=challenger_only_dates,
        paired_mean_net_excess_pct=paired_mean_metric,
        bootstrap_one_sided_95_lower_bound_pct=bootstrap_lower_metric,
        positive_edge_p_value=positive_p_value_metric,
        holm_adjusted_positive_edge_p_value=holm_adjusted_metric,
        holm_family_size=holm_family_size,
        holm_observed_prior_p_value_count=len(additional_p_values),
        holm_unobserved_prior_p_value_count=unobserved_prior_p_value_count,
        holm_adjustment_method=holm_adjustment_method,
        holm_adjustment_reason=holm_adjustment_reason,
        deflated_sharpe_status=(
            "unavailable"
            if dsr_probability_metric is None
            else "pass"
            if dsr_probability_metric
            >= thresholds.minimum_deflated_sharpe_probability
            else "fail"
        ),
        deflated_sharpe_probability=dsr_probability_metric,
        deflated_sharpe_reason=dsr_reason,
        prior_experiment_count=experiments,
        positive_subperiod_count=positive_subperiod_count,
        required_positive_subperiod_count=thresholds.minimum_positive_subperiods,
        subperiod_count=len(subperiods),
        subperiods=subperiods,
        pbo_status=pbo_status,
        pbo_probability=pbo_probability,
        pbo_reason=pbo_reason,
        gates=gates,
        reasons=reasons,
    )


def _evaluate_pbo_evidence(
    evidence: Mapping[str, object] | None,
    *,
    maximum_probability: float,
) -> tuple[Literal["pass", "fail", "unavailable"], float | None, str]:
    if evidence is None:
        return (
            "unavailable",
            None,
            "PBO unavailable: a genuine common-date model-return matrix was not provided. "
            "The result must remain shadow-only.",
        )
    rejection_reason = evidence.get("rejection_reason")
    probability_value = evidence.get("probability")
    matrix_digest = evidence.get("matrix_digest")
    fold_count = evidence.get("fold_count")
    model_count = evidence.get("model_count")
    date_count = evidence.get("date_count")
    method = evidence.get("method")
    scope = evidence.get("scope")
    search_process_coverage = evidence.get("search_process_coverage")
    if rejection_reason:
        return (
            "unavailable",
            None,
            f"PBO unavailable: {str(rejection_reason).strip()}",
        )
    if (
        isinstance(probability_value, bool)
        or not isinstance(probability_value, (int, float))
        or not math.isfinite(float(probability_value))
        or not 0.0 <= float(probability_value) <= 1.0
        or not isinstance(matrix_digest, str)
        or len(matrix_digest) != 64
        or not isinstance(fold_count, int)
        or fold_count <= 0
        or not isinstance(model_count, int)
        or model_count != len(RANKING_V3_FROZEN_PBO_MODEL_IDS)
        or not isinstance(date_count, int)
        or date_count <= 0
        or method != CSCV_PBO_METHOD
        or scope != PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY
        or search_process_coverage != PBO_SEARCH_PROCESS_COVERAGE
    ):
        return (
            "unavailable",
            None,
            "PBO unavailable: evidence must identify the frozen six-model family, "
            "the implemented CSCV method, and partial search-process coverage.",
        )
    probability = round(float(probability_value), 12)
    if probability <= maximum_probability:
        return (
            "pass",
            probability,
            f"Six-model-family PBO {probability:.2%} is within the "
            f"{maximum_probability:.0%} release limit. This is not a full "
            "search-process PBO estimate.",
        )
    return (
        "fail",
        probability,
        f"Six-model-family PBO {probability:.2%} exceeds the "
        f"{maximum_probability:.0%} release limit. This is not a full "
        "search-process PBO estimate.",
    )


def holm_bonferroni(p_values: Sequence[float]) -> list[float]:
    """Return family-wise-error adjusted p-values in original order."""
    validated = [_validated_p_value(value) for value in p_values]
    if not validated:
        return []
    ordered = sorted(enumerate(validated), key=lambda item: (item[1], item[0]))
    total = len(ordered)
    running_max = 0.0
    adjusted_by_index: dict[int, float] = {}
    for rank, (original_index, p_value) in enumerate(ordered):
        adjusted = min(1.0, (total - rank) * p_value)
        running_max = max(running_max, adjusted)
        adjusted_by_index[original_index] = running_max
    return [round(adjusted_by_index[index], 12) for index in range(len(validated))]


def _coerce_observation(item: ReturnObservationLike) -> RankingV3ReturnObservation:
    if isinstance(item, RankingV3ReturnObservation):
        return item
    if isinstance(item, tuple) and len(item) == 2:
        return RankingV3ReturnObservation(
            rebalance_date=item[0],
            net_return_pct=item[1],
        )
    return RankingV3ReturnObservation.model_validate(item)


def _cluster_daily_returns(
    observations: Sequence[RankingV3ReturnObservation],
) -> dict[date, float]:
    grouped: dict[date, list[float]] = defaultdict(list)
    for item in observations:
        grouped[item.rebalance_date].append(item.net_return_pct)
    return {
        rebalance_date: math.fsum(sorted(values)) / len(values)
        for rebalance_date, values in grouped.items()
    }


def _one_sided_bootstrap_lower_bound(
    values: Sequence[float],
    *,
    samples: int,
    seed: int,
    block_length: int = DEPENDENCE_BLOCK_LENGTH,
) -> float | None:
    if block_length <= 0:
        raise ValueError("block_length must be positive")
    count = len(values)
    if count < block_length * 2:
        return None
    generator = random.Random(seed)
    blocks = [
        list(values[start : start + block_length]) for start in range(count - block_length + 1)
    ]
    blocks_per_sample = math.ceil(count / block_length)
    bootstrap_means: list[float] = []
    for _ in range(samples):
        sample: list[float] = []
        for _ in range(blocks_per_sample):
            sample.extend(generator.choice(blocks))
        bootstrap_means.append(math.fsum(sample[:count]) / count)
    bootstrap_means.sort()
    lower_index = max(0, math.floor((len(bootstrap_means) - 1) * 0.05))
    moving_block_lower = bootstrap_means[lower_index]
    iid_lower = _iid_bootstrap_lower_bound(
        values,
        samples=samples,
        seed=seed,
    )
    return moving_block_lower if iid_lower is None else min(moving_block_lower, iid_lower)


def _positive_sign_flip_p_value(
    values: Sequence[float],
    *,
    samples: int,
    seed: int,
    block_length: int = DEPENDENCE_BLOCK_LENGTH,
) -> float | None:
    if block_length <= 0:
        raise ValueError("block_length must be positive")
    complete_count = (len(values) // block_length) * block_length
    if complete_count < block_length:
        return None
    complete_values = list(values[:complete_count])
    observed = _mean(complete_values)
    if observed is None or observed <= 0:
        return 1.0
    block_sums = [
        math.fsum(complete_values[start : start + block_length])
        for start in range(0, complete_count, block_length)
    ]
    generator = random.Random(seed)
    exceedances = 0
    for _ in range(samples):
        null_mean = (
            math.fsum(
                block_sum if generator.random() >= 0.5 else -block_sum for block_sum in block_sums
            )
            / complete_count
        )
        if null_mean >= observed:
            exceedances += 1
    block_p_value = (exceedances + 1) / (samples + 1)
    iid_p_value = _iid_positive_sign_flip_p_value(
        complete_values,
        samples=samples,
        seed=seed,
    )
    return block_p_value if iid_p_value is None else max(block_p_value, iid_p_value)


def _iid_bootstrap_lower_bound(
    values: Sequence[float],
    *,
    samples: int,
    seed: int,
) -> float | None:
    if len(values) < 2:
        return None
    generator = random.Random(seed)
    count = len(values)
    bootstrap_means = sorted(
        math.fsum(generator.choice(values) for _ in range(count)) / count for _ in range(samples)
    )
    lower_index = max(0, math.floor((len(bootstrap_means) - 1) * 0.05))
    return bootstrap_means[lower_index]


def _iid_positive_sign_flip_p_value(
    values: Sequence[float],
    *,
    samples: int,
    seed: int,
) -> float | None:
    observed = _mean(values)
    if observed is None:
        return None
    if observed <= 0:
        return 1.0
    generator = random.Random(seed)
    exceedances = 0
    for _ in range(samples):
        null_mean = math.fsum(
            value if generator.random() >= 0.5 else -value for value in values
        ) / len(values)
        if null_mean >= observed:
            exceedances += 1
    return (exceedances + 1) / (samples + 1)


def _evaluate_deflated_sharpe_evidence(
    registry: RankingV3ExperimentRegistry,
    *,
    pbo_evidence: Mapping[str, object] | None,
    paired_values: Sequence[tuple[date, float]],
    registered_trial_count: int,
    dependence_block_length: int,
) -> tuple[float | None, str]:
    """Compute Bailey-Lopez de Prado DSR from frozen common-date evidence.

    The current strategy uses challenger-minus-baseline returns. Cross-trial
    Sharpe dispersion comes from the immutable six-model PBO matrix after each
    model is differenced against the same constraint-matched baseline. The
    expected-maximum penalty still uses every registered research attempt.
    Overlapping holding cohorts are collapsed into non-overlapping time blocks
    before estimating Sharpe, skewness, and kurtosis.
    """

    if registered_trial_count < registry.prior_attempt_count + 1:
        return (
            None,
            "Deflated Sharpe unavailable: the supplied trial count omits "
            "registered research attempts.",
        )
    if dependence_block_length <= 0:
        return None, "Deflated Sharpe unavailable: dependence block length is invalid."
    matrix, matrix_reason = _deflated_sharpe_matrix(pbo_evidence)
    if matrix_reason is not None:
        return None, f"Deflated Sharpe unavailable: {matrix_reason}."
    assert matrix is not None

    paired_dates = tuple(item[0] for item in paired_values)
    if not paired_dates:
        return None, "Deflated Sharpe unavailable: no paired return observations exist."
    matrix_dates = tuple(item[0] for item in next(iter(matrix.values())))
    if matrix_dates != paired_dates:
        return (
            None,
            "Deflated Sharpe unavailable: the frozen model matrix calendar does "
            "not exactly match the paired validation calendar.",
        )

    current_blocks = _non_overlapping_block_means(
        [item[1] for item in paired_values],
        block_length=dependence_block_length,
    )
    if len(current_blocks) < 3:
        return (
            None,
            "Deflated Sharpe unavailable: fewer than three independent return "
            "blocks are available.",
        )
    current_sharpe = _sample_sharpe(current_blocks)
    if current_sharpe is None:
        return (
            None,
            "Deflated Sharpe unavailable: the current paired return series has "
            "zero or invalid dispersion.",
        )

    baseline = matrix["constraint_matched_baseline"]
    trial_sharpes: list[float] = []
    for model_id in RANKING_V3_FROZEN_PBO_MODEL_IDS:
        model = matrix[model_id]
        excess = [
            model_row[1] - baseline_row[1]
            for model_row, baseline_row in zip(model, baseline, strict=True)
        ]
        blocks = _non_overlapping_block_means(
            excess,
            block_length=dependence_block_length,
        )
        trial_sharpe = _sample_sharpe(blocks, allow_zero_series=True)
        if trial_sharpe is None:
            return (
                None,
                "Deflated Sharpe unavailable: the frozen model family contains "
                f"an invalid Sharpe series for {model_id}.",
            )
        trial_sharpes.append(trial_sharpe)

    cross_trial_std = _sample_standard_deviation(trial_sharpes)
    if cross_trial_std is None or cross_trial_std <= 0:
        return (
            None,
            "Deflated Sharpe unavailable: frozen cross-model Sharpe dispersion "
            "is zero or invalid.",
        )
    expected_maximum = _expected_maximum_sharpe(
        mean_sharpe=_mean(trial_sharpes) or 0.0,
        sharpe_standard_deviation=cross_trial_std,
        trial_count=max(registered_trial_count, len(trial_sharpes)),
    )
    if expected_maximum is None:
        return None, "Deflated Sharpe unavailable: expected-maximum Sharpe is invalid."

    skewness, kurtosis = _sample_skewness_and_kurtosis(current_blocks)
    denominator_term = (
        1.0
        - skewness * current_sharpe
        + ((kurtosis - 1.0) / 4.0) * current_sharpe * current_sharpe
    )
    if not math.isfinite(denominator_term) or denominator_term <= 0:
        return (
            None,
            "Deflated Sharpe unavailable: the non-normality correction is "
            "non-positive.",
        )
    statistic = (
        (current_sharpe - expected_maximum)
        * math.sqrt(len(current_blocks) - 1)
        / math.sqrt(denominator_term)
    )
    probability = NormalDist().cdf(statistic)
    if not math.isfinite(probability):
        return None, "Deflated Sharpe unavailable: the probability is non-finite."
    return (
        probability,
        "Deflated Sharpe uses "
        f"{len(current_blocks)} independent blocks, "
        f"{registered_trial_count} registered trials, observed Sharpe "
        f"{current_sharpe:.4f}, and expected maximum Sharpe "
        f"{expected_maximum:.4f}.",
    )


def _deflated_sharpe_matrix(
    evidence: Mapping[str, object] | None,
) -> tuple[
    dict[str, tuple[tuple[date, float], ...]] | None,
    str | None,
]:
    if not isinstance(evidence, Mapping):
        return None, "a frozen PBO model-return matrix was not provided"
    if evidence.get("rejection_reason") is not None:
        return None, "the frozen PBO matrix was rejected"
    if evidence.get("scope") != PBO_SCOPE_FROZEN_SIX_MODEL_FAMILY:
        return None, "the evidence does not disclose the frozen six-model family"
    payload = evidence.get("model_return_matrix")
    if not isinstance(payload, Mapping):
        return None, "the PBO evidence has no model-return matrix"
    if set(payload) != set(RANKING_V3_FROZEN_PBO_MODEL_IDS):
        return None, "the model-return matrix does not match the frozen family"

    matrix: dict[str, tuple[tuple[date, float], ...]] = {}
    reference_dates: tuple[date, ...] | None = None
    try:
        for model_id in RANKING_V3_FROZEN_PBO_MODEL_IDS:
            raw_rows = payload[model_id]
            if not isinstance(raw_rows, Sequence) or isinstance(
                raw_rows, (str, bytes)
            ):
                return None, f"matrix rows for {model_id} are not a sequence"
            rows: list[tuple[date, float]] = []
            for item in raw_rows:
                if not isinstance(item, Mapping):
                    return None, f"matrix row for {model_id} is not an object"
                rebalance_date = date.fromisoformat(str(item["rebalance_date"]))
                net_return = float(item["net_return"])
                if not math.isfinite(net_return):
                    return None, f"matrix return for {model_id} is non-finite"
                rows.append((rebalance_date, net_return))
            dates = tuple(item[0] for item in rows)
            if not dates or any(right <= left for left, right in zip(dates, dates[1:])):
                return None, f"matrix calendar for {model_id} is empty or unordered"
            if reference_dates is None:
                reference_dates = dates
            elif dates != reference_dates:
                return None, "matrix models do not use an identical calendar"
            matrix[model_id] = tuple(rows)
    except (KeyError, TypeError, ValueError):
        return None, "the model-return matrix contains malformed values"
    return matrix, None


def _non_overlapping_block_means(
    values: Sequence[float],
    *,
    block_length: int,
) -> list[float]:
    return [
        math.fsum(values[offset : offset + block_length])
        / len(values[offset : offset + block_length])
        for offset in range(0, len(values), block_length)
        if len(values[offset : offset + block_length]) == block_length
    ]


def _sample_sharpe(
    values: Sequence[float],
    *,
    allow_zero_series: bool = False,
) -> float | None:
    standard_deviation = _sample_standard_deviation(values)
    mean = _mean(values)
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
    variance = math.fsum((value - mean) ** 2 for value in values) / (
        len(values) - 1
    )
    if not math.isfinite(variance) or variance < 0:
        return None
    return math.sqrt(variance)


def _sample_skewness_and_kurtosis(
    values: Sequence[float],
) -> tuple[float, float]:
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
    first = normal.inv_cdf(1.0 - 1.0 / trial_count)
    second = normal.inv_cdf(1.0 - 1.0 / (trial_count * math.e))
    result = mean_sharpe + sharpe_standard_deviation * (
        (1.0 - euler_gamma) * first + euler_gamma * second
    )
    return result if math.isfinite(result) else None


def _contiguous_subperiods(
    paired_values: Sequence[tuple[date, float]],
    *,
    required_subperiods: int,
) -> list[RankingV3SubperiodResult]:
    if not paired_values or required_subperiods <= 0:
        return []
    ordered = sorted(paired_values, key=lambda item: item[0])
    base_size, remainder = divmod(len(ordered), required_subperiods)
    results: list[RankingV3SubperiodResult] = []
    offset = 0
    for period in range(1, required_subperiods + 1):
        size = base_size + (1 if period <= remainder else 0)
        if size == 0:
            continue
        chunk = ordered[offset : offset + size]
        offset += size
        mean = _rounded(math.fsum(value for _, value in chunk) / size)
        assert mean is not None
        results.append(
            RankingV3SubperiodResult(
                period=period,
                start_date=chunk[0][0],
                end_date=chunk[-1][0],
                rebalance_date_count=size,
                mean_paired_net_excess_pct=mean,
                positive=mean > 0,
            )
        )
    return results


def _build_gates(
    *,
    dates_are_common: bool,
    common_date_count: int,
    effective_block_count: int,
    completed_trade_count: int,
    paired_mean: float | None,
    bootstrap_lower: float | None,
    holm_adjusted_p_value: float | None,
    dsr_probability: float | None,
    dsr_reason: str,
    positive_subperiod_count: int,
    actual_subperiod_count: int,
) -> list[RankingV3ValidationGate]:
    thresholds = build_ranking_v3_protocol().thresholds
    gates = [
        _gate(
            key="common_rebalance_calendar",
            passed=dates_are_common,
            insufficient=False,
            observed="common" if dates_are_common else "mismatch",
            required="identical non-empty rebalance-date sets",
            failure_reason=("Baseline and challenger do not use an identical rebalance calendar."),
        ),
        _gate(
            key="independent_rebalance_dates",
            passed=effective_block_count >= thresholds.minimum_rebalance_dates,
            insufficient=effective_block_count < thresholds.minimum_rebalance_dates,
            observed=(
                f"{effective_block_count} effective blocks ({common_date_count} rebalance dates)"
            ),
            required=(
                f">={thresholds.minimum_rebalance_dates} effective "
                f"{DEPENDENCE_BLOCK_LENGTH}-cohort blocks"
            ),
            failure_reason=(
                f"Only {effective_block_count} independent time blocks are available "
                f"from {common_date_count} rebalance dates; at least "
                f"{thresholds.minimum_rebalance_dates} are required."
            ),
        ),
        _gate(
            key="completed_trades",
            passed=completed_trade_count >= thresholds.minimum_completed_trades,
            insufficient=completed_trade_count < thresholds.minimum_completed_trades,
            observed=str(completed_trade_count),
            required=f">={thresholds.minimum_completed_trades}",
            failure_reason=(
                f"Only {completed_trade_count} completed trades are available; "
                f"at least {thresholds.minimum_completed_trades} are required."
            ),
        ),
        _gate(
            key="positive_paired_mean",
            passed=paired_mean is not None and paired_mean > 0,
            insufficient=paired_mean is None,
            observed=_format_metric(paired_mean, suffix="%"),
            required=">0%",
            failure_reason="The paired challenger-minus-baseline mean is not positive.",
        ),
        _gate(
            key="one_sided_95_lower_bound",
            passed=bootstrap_lower is not None and bootstrap_lower > 0,
            insufficient=bootstrap_lower is None,
            observed=_format_metric(bootstrap_lower, suffix="%"),
            required=">0%",
            failure_reason=(
                "The one-sided 95% moving-block bootstrap lower bound is not above zero."
            ),
        ),
        _gate(
            key="holm_adjusted_positive_edge",
            passed=(
                holm_adjusted_p_value is not None
                and holm_adjusted_p_value <= thresholds.maximum_holm_adjusted_p_value
            ),
            insufficient=holm_adjusted_p_value is None,
            observed=_format_metric(holm_adjusted_p_value),
            required=f"<={thresholds.maximum_holm_adjusted_p_value}",
            failure_reason=(
                "The Holm-Bonferroni adjusted positive-edge p-value exceeds the "
                "confirmatory threshold."
            ),
        ),
        _gate(
            key="deflated_sharpe_probability",
            passed=(
                dsr_probability is not None
                and dsr_probability >= thresholds.minimum_deflated_sharpe_probability
            ),
            insufficient=dsr_probability is None,
            observed=_format_metric(dsr_probability),
            required=f">={thresholds.minimum_deflated_sharpe_probability}",
            failure_reason=dsr_reason,
        ),
        _gate(
            key="positive_contiguous_subperiods",
            passed=(
                actual_subperiod_count == thresholds.required_subperiods
                and positive_subperiod_count >= thresholds.minimum_positive_subperiods
            ),
            insufficient=actual_subperiod_count < thresholds.required_subperiods,
            observed=f"{positive_subperiod_count}/{actual_subperiod_count}",
            required=(
                f">={thresholds.minimum_positive_subperiods}/{thresholds.required_subperiods}"
            ),
            failure_reason=(
                "Fewer than four of five contiguous subperiods have positive paired net excess."
            ),
        ),
        RankingV3ValidationGate(
            key="probability_of_backtest_overfit",
            status="unavailable",
            observed="unavailable",
            required=(f"<={thresholds.maximum_probability_of_backtest_overfit}"),
            reason=(
                "PBO requires a genuine model-return matrix and is not estimated "
                "from one baseline/challenger pair."
            ),
        ),
    ]
    return gates


def _gate(
    *,
    key: str,
    passed: bool,
    insufficient: bool,
    observed: str,
    required: str,
    failure_reason: str,
) -> RankingV3ValidationGate:
    if passed:
        return RankingV3ValidationGate(
            key=key,
            status="pass",
            observed=observed,
            required=required,
            reason=f"{key} passed.",
        )
    return RankingV3ValidationGate(
        key=key,
        status="insufficient" if insufficient else "fail",
        observed=observed,
        required=required,
        reason=failure_reason,
    )


def _statistical_gate_status(
    gates: Sequence[RankingV3ValidationGate],
) -> ValidationStatus:
    observable = [gate for gate in gates if gate.status != "unavailable"]
    if any(gate.status == "fail" for gate in observable):
        return "fail"
    if any(gate.status == "insufficient" for gate in observable):
        return "insufficient"
    return "pass"


def _validated_p_value(value: float) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise ValueError("p-values must be finite and between 0 and 1")
    return parsed


def _effective_independent_block_count(
    observation_count: int,
    *,
    block_length: int,
) -> int:
    if block_length <= 0:
        raise ValueError("block_length must be positive")
    return observation_count // block_length


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return math.fsum(values) / len(values)


def _rounded(value: float | None, *, digits: int = 12) -> float | None:
    return None if value is None else round(float(value), digits)


def _format_metric(value: float | None, *, suffix: str = "") -> str:
    if value is None:
        return "unavailable"
    return f"{value:.12g}{suffix}"
