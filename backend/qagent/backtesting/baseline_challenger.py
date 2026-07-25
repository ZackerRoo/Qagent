from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel, Field


BASELINE_CHALLENGER_VERSION = "net-excess-baseline-v2-two-threshold-hysteresis"
MIN_BASELINE_TRAINING_SAMPLES = 40
MIN_BASELINE_STRATEGY_SAMPLES = 12
BASELINE_PRIOR_STRENGTH = 16
BASELINE_ONE_SIDED_Z = 1.2816
MIN_SELECTION_EXPECTED_EXCESS_PCT = 0.15
MIN_SELECTION_EXCESS_LOWER_BOUND_PCT = -0.40
MIN_SELECTION_WIN_PROBABILITY = 0.50
MIN_SELECTION_WIN_LOWER_BOUND = 0.42
NEGATIVE_SEGMENT_SAMPLE_COUNT = 10
NEGATIVE_SEGMENT_EXPECTED_EXCESS_PCT = -0.35
BASELINE_REPLACEMENT_SCORE_MARGIN = 0.05
BASELINE_REPLACEMENT_EXCESS_MARGIN_PCT = 0.30


class BaselineCandidate(BaseModel):
    instrument_id: str
    baseline_rank_score: float
    primary_strategy_id: str | None = None
    factor_signals: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"
    industry: str | None = None
    asset_type: str = "unknown"


class ResolvedBaselineObservation(BaseModel):
    instrument_id: str
    signal_date: date
    exit_date: date
    return_pct: float
    benchmark_return_pct: float
    net_excess_return_pct: float
    primary_strategy_id: str | None = None
    factor_signals: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"
    industry: str | None = None
    asset_type: str = "unknown"
    exit_reason: str = "unknown"
    holding_days: int = 0


class BaselineCandidateScore(BaseModel):
    instrument_id: str
    baseline_position: int
    challenger_position: int
    baseline_score: float
    challenger_score: float
    training_sample_count: int
    strategy_sample_count: int
    evidence_sample_count: int
    expected_excess_return_pct: float | None = None
    expected_excess_lower_bound_pct: float | None = None
    win_probability: float | None = None
    win_probability_lower_bound: float | None = None
    downside_pct: float | None = None
    selection_eligible: bool = False
    negative_segment: bool = False
    reason: str = ""


class BaselineDecision(BaseModel):
    model_version: str = BASELINE_CHALLENGER_VERSION
    decision_date: date
    training_cutoff_date: date | None = None
    training_sample_count: int
    model_ready: bool
    candidates: list[BaselineCandidateScore] = Field(default_factory=list)


class _PosteriorEstimate(BaseModel):
    sample_count: int
    expected_excess_return_pct: float
    expected_excess_lower_bound_pct: float
    win_probability: float
    win_probability_lower_bound: float
    downside_pct: float


def score_baseline_candidates(
    candidates: list[BaselineCandidate],
    observations: list[ResolvedBaselineObservation],
    *,
    decision_date: date,
) -> BaselineDecision:
    """Score candidates from information fully resolved before ``decision_date``."""

    eligible_observations = sorted(
        (item for item in observations if item.exit_date < decision_date),
        key=lambda item: (item.exit_date, item.signal_date, item.instrument_id),
    )
    ordered_candidates = sorted(
        candidates,
        key=lambda item: (-item.baseline_rank_score, item.instrument_id),
    )
    cutoff = eligible_observations[-1].exit_date if eligible_observations else None
    if not ordered_candidates:
        return BaselineDecision(
            decision_date=decision_date,
            training_cutoff_date=cutoff,
            training_sample_count=len(eligible_observations),
            model_ready=False,
        )

    model_ready = len(eligible_observations) >= MIN_BASELINE_TRAINING_SAMPLES
    baseline_positions = {
        item.instrument_id: index for index, item in enumerate(ordered_candidates, start=1)
    }
    strategy_groups = _group(
        eligible_observations,
        lambda item: item.primary_strategy_id or "unknown",
    )
    strategy_regime_groups = _group(
        eligible_observations,
        lambda item: f"{item.primary_strategy_id or 'unknown'}|{item.market_regime}",
    )
    industry_groups = _group(
        eligible_observations,
        lambda item: item.industry or "unknown",
    )
    industry_regime_groups = _group(
        eligible_observations,
        lambda item: f"{item.industry or 'unknown'}|{item.market_regime}",
    )
    asset_groups = _group(
        eligible_observations,
        lambda item: item.asset_type or "unknown",
    )
    factor_groups: dict[str, list[ResolvedBaselineObservation]] = {}
    for observation in eligible_observations:
        for factor in set(observation.factor_signals):
            factor_groups.setdefault(factor, []).append(observation)

    baseline_values = [item.baseline_rank_score for item in ordered_candidates]
    provisional: list[
        tuple[
            BaselineCandidate,
            float,
            _PosteriorEstimate | None,
            int,
            bool,
            bool,
        ]
    ] = []
    for candidate in ordered_candidates:
        strategy_key = candidate.primary_strategy_id or "unknown"
        industry_key = candidate.industry or "unknown"
        strategy_estimate = _posterior(strategy_groups.get(strategy_key, []))
        strategy_regime_estimate = _posterior(
            strategy_regime_groups.get(
                f"{strategy_key}|{candidate.market_regime}",
                [],
            )
        )
        industry_estimate = _posterior(industry_groups.get(industry_key, []))
        industry_regime_estimate = _posterior(
            industry_regime_groups.get(
                f"{industry_key}|{candidate.market_regime}",
                [],
            )
        )
        factor_estimate = _combine_equal(
            [
                estimate
                for factor in set(candidate.factor_signals)
                if (estimate := _posterior(factor_groups.get(factor, []))) is not None
            ]
        )
        asset_estimate = _posterior(asset_groups.get(candidate.asset_type or "unknown", []))
        evidence = _combine_weighted(
            [
                (strategy_estimate, 0.25),
                (strategy_regime_estimate, 0.30),
                (factor_estimate, 0.20),
                (industry_estimate, 0.08),
                (industry_regime_estimate, 0.12),
                (asset_estimate, 0.05),
            ]
        )
        strategy_samples = strategy_estimate.sample_count if strategy_estimate else 0
        negative_segment = bool(
            strategy_regime_estimate
            and strategy_regime_estimate.sample_count >= NEGATIVE_SEGMENT_SAMPLE_COUNT
            and strategy_regime_estimate.expected_excess_return_pct
            <= NEGATIVE_SEGMENT_EXPECTED_EXCESS_PCT
        )
        selection_eligible = bool(
            model_ready
            and evidence is not None
            and strategy_samples >= MIN_BASELINE_STRATEGY_SAMPLES
            and evidence.expected_excess_return_pct >= MIN_SELECTION_EXPECTED_EXCESS_PCT
            and evidence.expected_excess_lower_bound_pct >= MIN_SELECTION_EXCESS_LOWER_BOUND_PCT
            and evidence.win_probability >= MIN_SELECTION_WIN_PROBABILITY
            and evidence.win_probability_lower_bound >= MIN_SELECTION_WIN_LOWER_BOUND
            and not negative_segment
        )
        baseline_score = _normalized_baseline_score(
            candidate,
            ordered_candidates,
            baseline_values,
        )
        challenger_score = baseline_score
        if model_ready and evidence is not None:
            confidence = min(1.0, math.sqrt(evidence.sample_count / 36))
            alpha_component = _clamp(
                evidence.expected_excess_return_pct / 4,
                -1,
                1,
            )
            win_component = _clamp((evidence.win_probability - 0.5) * 2, -1, 1)
            downside_component = _clamp(evidence.downside_pct / 8, 0, 1)
            challenger_score = baseline_score * 0.45 + 0.55 * (
                0.50
                + confidence
                * (0.30 * alpha_component + 0.18 * win_component - 0.12 * downside_component)
            )
            if negative_segment:
                challenger_score -= 0.20
        provisional.append(
            (
                candidate,
                round(challenger_score, 8),
                evidence,
                strategy_samples,
                selection_eligible,
                negative_segment,
            )
        )

    ranked = sorted(
        provisional,
        key=lambda item: (
            -item[1],
            baseline_positions[item[0].instrument_id],
            item[0].instrument_id,
        ),
    )
    challenger_positions = {
        item[0].instrument_id: index for index, item in enumerate(ranked, start=1)
    }
    scores = [
        BaselineCandidateScore(
            instrument_id=candidate.instrument_id,
            baseline_position=baseline_positions[candidate.instrument_id],
            challenger_position=challenger_positions[candidate.instrument_id],
            baseline_score=round(candidate.baseline_rank_score, 8),
            challenger_score=score,
            training_sample_count=len(eligible_observations),
            strategy_sample_count=strategy_samples,
            evidence_sample_count=evidence.sample_count if evidence else 0,
            expected_excess_return_pct=(
                round(evidence.expected_excess_return_pct, 4) if evidence else None
            ),
            expected_excess_lower_bound_pct=(
                round(evidence.expected_excess_lower_bound_pct, 4) if evidence else None
            ),
            win_probability=(round(evidence.win_probability, 4) if evidence else None),
            win_probability_lower_bound=(
                round(evidence.win_probability_lower_bound, 4) if evidence else None
            ),
            downside_pct=round(evidence.downside_pct, 4) if evidence else None,
            selection_eligible=selection_eligible,
            negative_segment=negative_segment,
            reason=_reason(
                model_ready=model_ready,
                evidence=evidence,
                strategy_samples=strategy_samples,
                selection_eligible=selection_eligible,
                negative_segment=negative_segment,
            ),
        )
        for (
            candidate,
            score,
            evidence,
            strategy_samples,
            selection_eligible,
            negative_segment,
        ) in ranked
    ]
    return BaselineDecision(
        decision_date=decision_date,
        training_cutoff_date=cutoff,
        training_sample_count=len(eligible_observations),
        model_ready=model_ready,
        candidates=scores,
    )


def _group(observations, key) -> dict[str, list[ResolvedBaselineObservation]]:
    groups: dict[str, list[ResolvedBaselineObservation]] = {}
    for observation in observations:
        groups.setdefault(str(key(observation)), []).append(observation)
    return groups


def _posterior(
    observations: list[ResolvedBaselineObservation],
) -> _PosteriorEstimate | None:
    if not observations:
        return None
    returns = [float(item.net_excess_return_pct) for item in observations]
    sample_count = len(returns)
    denominator = sample_count + BASELINE_PRIOR_STRENGTH
    expected = sum(returns) / denominator
    variance = (
        sum((value - expected) ** 2 for value in returns) + BASELINE_PRIOR_STRENGTH * expected**2
    ) / denominator
    standard_error = math.sqrt(max(variance, 0) / denominator)
    win_probability = (sum(value > 0 for value in returns) + 2) / (sample_count + 4)
    win_standard_error = math.sqrt(
        max(win_probability * (1 - win_probability), 0) / (sample_count + 4)
    )
    losses = [abs(value) for value in returns if value < 0]
    return _PosteriorEstimate(
        sample_count=sample_count,
        expected_excess_return_pct=expected,
        expected_excess_lower_bound_pct=(expected - BASELINE_ONE_SIDED_Z * standard_error),
        win_probability=win_probability,
        win_probability_lower_bound=max(
            0.0,
            win_probability - BASELINE_ONE_SIDED_Z * win_standard_error,
        ),
        downside_pct=sum(losses) / denominator,
    )


def _combine_equal(
    estimates: list[_PosteriorEstimate],
) -> _PosteriorEstimate | None:
    if not estimates:
        return None
    return _combine_weighted([(item, 1.0) for item in estimates])


def _combine_weighted(
    estimates: list[tuple[_PosteriorEstimate | None, float]],
) -> _PosteriorEstimate | None:
    usable = [
        (estimate, weight * min(1.0, math.sqrt(estimate.sample_count / 20)))
        for estimate, weight in estimates
        if estimate is not None and weight > 0
    ]
    denominator = sum(weight for _, weight in usable)
    if denominator <= 0:
        return None
    return _PosteriorEstimate(
        sample_count=max(item.sample_count for item, _ in usable),
        expected_excess_return_pct=sum(
            item.expected_excess_return_pct * weight for item, weight in usable
        )
        / denominator,
        expected_excess_lower_bound_pct=sum(
            item.expected_excess_lower_bound_pct * weight for item, weight in usable
        )
        / denominator,
        win_probability=sum(item.win_probability * weight for item, weight in usable) / denominator,
        win_probability_lower_bound=sum(
            item.win_probability_lower_bound * weight for item, weight in usable
        )
        / denominator,
        downside_pct=sum(item.downside_pct * weight for item, weight in usable) / denominator,
    )


def _normalized_baseline_score(
    candidate: BaselineCandidate,
    candidates: list[BaselineCandidate],
    values: list[float],
) -> float:
    if len(candidates) == 1:
        return 1.0
    position = candidates.index(candidate)
    position_score = 1 - position / (len(candidates) - 1)
    minimum = min(values)
    maximum = max(values)
    raw_score = (
        (candidate.baseline_rank_score - minimum) / (maximum - minimum)
        if maximum > minimum
        else position_score
    )
    return round(position_score * 0.60 + raw_score * 0.40, 8)


def _reason(
    *,
    model_ready: bool,
    evidence: _PosteriorEstimate | None,
    strategy_samples: int,
    selection_eligible: bool,
    negative_segment: bool,
) -> str:
    if not model_ready:
        return f"严格时序训练样本未达到 {MIN_BASELINE_TRAINING_SAMPLES} 笔，保持固定 Top 5。"
    if evidence is None:
        return "没有可用的同策略、因子、行业或市场状态历史证据，保留现金。"
    if negative_segment:
        return "同策略与当前市场状态的历史净超额为负，暂停新增。"
    action = "允许进入挑战组合" if selection_eligible else "证据不足，保留现金或原持仓"
    return (
        f"历史净超额后验 {evidence.expected_excess_return_pct:+.2f}%，"
        f"下界 {evidence.expected_excess_lower_bound_pct:+.2f}%，"
        f"超额胜率 {evidence.win_probability:.0%}"
        f"（下界 {evidence.win_probability_lower_bound:.0%}），"
        f"策略样本 {strategy_samples}；{action}。"
    )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
