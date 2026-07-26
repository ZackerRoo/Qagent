from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
import math
import random
from statistics import NormalDist
from typing import Literal

from pydantic import BaseModel, field_validator

from qagent.backtesting.ranking_v3_protocol import build_ranking_v3_protocol


ValidationStatus = Literal["pass", "insufficient", "fail"]
GateStatus = Literal["pass", "insufficient", "fail", "unavailable"]

# V3 holds positions for up to 20 sessions and rebalances every 10 sessions.
# Adjacent rebalance cohorts therefore share roughly half of their return window.
DEPENDENCE_BLOCK_LENGTH = 2


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
    deflated_sharpe_probability: float | None = None
    prior_experiment_count: int
    positive_subperiod_count: int
    required_positive_subperiod_count: int
    subperiod_count: int
    subperiods: list[RankingV3SubperiodResult]
    pbo_status: Literal["unavailable"] = "unavailable"
    pbo_probability: None = None
    pbo_reason: str
    gates: list[RankingV3ValidationGate]
    reasons: list[str]


ReturnObservationLike = (
    RankingV3ReturnObservation
    | tuple[date, float]
    | Mapping[str, object]
)


def evaluate_ranking_v3_validation(
    baseline_returns: Sequence[ReturnObservationLike],
    challenger_returns: Sequence[ReturnObservationLike],
    *,
    completed_trade_count: int | None = None,
    additional_hypothesis_p_values: Sequence[float] = (),
    prior_experiment_count: int | None = None,
    bootstrap_samples: int = 5000,
    permutation_samples: int = 10000,
    seed: int = 42,
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
    completed_trades = (
        len(challenger)
        if completed_trade_count is None
        else completed_trade_count
    )
    if completed_trades < 0:
        raise ValueError("completed_trade_count must be non-negative")

    additional_p_values = [
        _validated_p_value(value) for value in additional_hypothesis_p_values
    ]
    baseline_by_date = _cluster_daily_returns(baseline)
    challenger_by_date = _cluster_daily_returns(challenger)
    baseline_dates = set(baseline_by_date)
    challenger_dates = set(challenger_by_date)
    common_dates = sorted(baseline_dates & challenger_dates)
    baseline_only_dates = sorted(baseline_dates - challenger_dates)
    challenger_only_dates = sorted(challenger_dates - baseline_dates)
    dates_are_common = (
        bool(baseline_dates)
        and baseline_dates == challenger_dates
    )

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
    holm_family_size = len(additional_p_values) + (1 if positive_p_value is not None else 0)
    if positive_p_value is not None:
        holm_adjusted = holm_bonferroni(
            [positive_p_value, *additional_p_values]
        )[0]
    dsr_probability = _deflated_sharpe_probability(
        paired_returns,
        prior_experiment_count=experiments,
        effective_sample_count=effective_block_count,
    )
    if dsr_probability is not None:
        iid_dsr_probability = _deflated_sharpe_probability(
            paired_returns,
            prior_experiment_count=experiments,
        )
        if iid_dsr_probability is not None:
            dsr_probability = min(dsr_probability, iid_dsr_probability)
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
        positive_subperiod_count=positive_subperiod_count,
        actual_subperiod_count=len(subperiods),
    )
    statistical_gate_status = _statistical_gate_status(gates)
    status: ValidationStatus
    if statistical_gate_status == "fail":
        status = "fail"
    else:
        status = "insufficient"

    pbo_reason = (
        "PBO unavailable: paired baseline/challenger returns do not provide the "
        "model-return matrix required for CSCV/PBO. The result must remain shadow-only."
    )
    reasons = [gate.reason for gate in gates if gate.status != "pass"]
    reasons.append(pbo_reason)
    if statistical_gate_status == "pass":
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
        deflated_sharpe_probability=dsr_probability_metric,
        prior_experiment_count=experiments,
        positive_subperiod_count=positive_subperiod_count,
        required_positive_subperiod_count=thresholds.minimum_positive_subperiods,
        subperiod_count=len(subperiods),
        subperiods=subperiods,
        pbo_reason=pbo_reason,
        gates=gates,
        reasons=reasons,
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
    return [
        round(adjusted_by_index[index], 12)
        for index in range(len(validated))
    ]


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
        list(values[start : start + block_length])
        for start in range(count - block_length + 1)
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
    return (
        moving_block_lower
        if iid_lower is None
        else min(moving_block_lower, iid_lower)
    )


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
        null_mean = math.fsum(
            block_sum if generator.random() >= 0.5 else -block_sum
            for block_sum in block_sums
        ) / complete_count
        if null_mean >= observed:
            exceedances += 1
    block_p_value = (exceedances + 1) / (samples + 1)
    iid_p_value = _iid_positive_sign_flip_p_value(
        complete_values,
        samples=samples,
        seed=seed,
    )
    return (
        block_p_value
        if iid_p_value is None
        else max(block_p_value, iid_p_value)
    )


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
        math.fsum(generator.choice(values) for _ in range(count)) / count
        for _ in range(samples)
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
            value if generator.random() >= 0.5 else -value
            for value in values
        ) / len(values)
        if null_mean >= observed:
            exceedances += 1
    return (exceedances + 1) / (samples + 1)


def _deflated_sharpe_probability(
    values: Sequence[float],
    *,
    prior_experiment_count: int,
    effective_sample_count: int | None = None,
) -> float | None:
    count = len(values)
    effective_count = (
        count
        if effective_sample_count is None
        else min(count, effective_sample_count)
    )
    if effective_count < 3:
        return None
    mean = _mean(values)
    if mean is None:
        return None
    sample_variance = math.fsum(
        (value - mean) ** 2 for value in values
    ) / (count - 1)
    if sample_variance <= 1e-18:
        return None

    sample_deviation = math.sqrt(sample_variance)
    sharpe = mean / sample_deviation
    population_deviation = math.sqrt(
        math.fsum((value - mean) ** 2 for value in values) / count
    )
    standardized = [
        (value - mean) / population_deviation
        for value in values
    ]
    skewness = math.fsum(value**3 for value in standardized) / count
    kurtosis = math.fsum(value**4 for value in standardized) / count
    sharpe_variance = max(
        (
            1.0
            - skewness * sharpe
            + ((kurtosis - 1.0) / 4.0) * sharpe**2
        )
        / (effective_count - 1),
        1e-18,
    )
    sharpe_standard_error = math.sqrt(sharpe_variance)
    trial_count = max(1, prior_experiment_count + 1)
    expected_maximum_sharpe = 0.0
    if trial_count > 1:
        normal = NormalDist()
        euler_gamma = 0.5772156649015329
        first_probability = _clamp_probability(1.0 - 1.0 / trial_count)
        second_probability = _clamp_probability(
            1.0 - 1.0 / (trial_count * math.e)
        )
        expected_maximum_sharpe = sharpe_standard_error * (
            (1.0 - euler_gamma) * normal.inv_cdf(first_probability)
            + euler_gamma * normal.inv_cdf(second_probability)
        )
    z_score = (
        sharpe - expected_maximum_sharpe
    ) / sharpe_standard_error
    return NormalDist().cdf(z_score)


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
        mean = _rounded(
            math.fsum(value for _, value in chunk) / size
        )
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
            failure_reason=(
                "Baseline and challenger do not use an identical rebalance calendar."
            ),
        ),
        _gate(
            key="independent_rebalance_dates",
            passed=effective_block_count >= thresholds.minimum_rebalance_dates,
            insufficient=effective_block_count < thresholds.minimum_rebalance_dates,
            observed=(
                f"{effective_block_count} effective blocks "
                f"({common_date_count} rebalance dates)"
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
                "The one-sided 95% moving-block bootstrap lower bound is not above "
                "zero."
            ),
        ),
        _gate(
            key="holm_adjusted_positive_edge",
            passed=(
                holm_adjusted_p_value is not None
                and holm_adjusted_p_value
                <= thresholds.maximum_holm_adjusted_p_value
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
                and dsr_probability
                >= thresholds.minimum_deflated_sharpe_probability
            ),
            insufficient=dsr_probability is None,
            observed=_format_metric(dsr_probability),
            required=f">={thresholds.minimum_deflated_sharpe_probability}",
            failure_reason=(
                "The Deflated Sharpe probability does not meet the frozen threshold."
            ),
        ),
        _gate(
            key="positive_contiguous_subperiods",
            passed=(
                actual_subperiod_count == thresholds.required_subperiods
                and positive_subperiod_count
                >= thresholds.minimum_positive_subperiods
            ),
            insufficient=actual_subperiod_count < thresholds.required_subperiods,
            observed=f"{positive_subperiod_count}/{actual_subperiod_count}",
            required=(
                f">={thresholds.minimum_positive_subperiods}/"
                f"{thresholds.required_subperiods}"
            ),
            failure_reason=(
                "Fewer than four of five contiguous subperiods have positive "
                "paired net excess."
            ),
        ),
        RankingV3ValidationGate(
            key="probability_of_backtest_overfit",
            status="unavailable",
            observed="unavailable",
            required=(
                f"<={thresholds.maximum_probability_of_backtest_overfit}"
            ),
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


def _clamp_probability(value: float) -> float:
    return min(1.0 - 1e-12, max(1e-12, value))
