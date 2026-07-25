from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel, Field


DYNAMIC_RERANKER_VERSION = "bayesian-resolved-outcomes-v1"
MIN_RERANK_TRAINING_SAMPLES = 20
RERANK_PRIOR_STRENGTH = 12


class RerankCandidate(BaseModel):
    instrument_id: str
    baseline_rank_score: float
    primary_strategy_id: str | None = None
    factor_signals: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"


class ResolvedRerankObservation(BaseModel):
    instrument_id: str
    signal_date: date
    exit_date: date
    return_pct: float
    primary_strategy_id: str | None = None
    factor_signals: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"


class RerankCandidateScore(BaseModel):
    instrument_id: str
    baseline_position: int
    rerank_position: int
    baseline_score: float
    rerank_score: float
    training_sample_count: int
    strategy_sample_count: int
    factor_sample_count: int
    expected_return_pct: float | None = None
    win_probability: float | None = None
    downside_pct: float | None = None
    reason: str


class RerankDecision(BaseModel):
    model_version: str = DYNAMIC_RERANKER_VERSION
    decision_date: date
    training_cutoff_date: date | None = None
    training_sample_count: int
    model_ready: bool
    candidates: list[RerankCandidateScore] = Field(default_factory=list)


class _PosteriorEstimate(BaseModel):
    sample_count: int
    expected_return_pct: float
    win_probability: float
    downside_pct: float


def rerank_candidates(
    candidates: list[RerankCandidate],
    observations: list[ResolvedRerankObservation],
    *,
    decision_date: date,
) -> RerankDecision:
    """Rank candidates using only trades resolved before the decision date."""

    eligible_observations = sorted(
        (
            item
            for item in observations
            if item.exit_date < decision_date
        ),
        key=lambda item: (
            item.exit_date,
            item.signal_date,
            item.instrument_id,
        ),
    )
    ordered_candidates = sorted(
        candidates,
        key=lambda item: (-item.baseline_rank_score, item.instrument_id),
    )
    if not ordered_candidates:
        return RerankDecision(
            decision_date=decision_date,
            training_cutoff_date=(
                eligible_observations[-1].exit_date
                if eligible_observations
                else None
            ),
            training_sample_count=len(eligible_observations),
            model_ready=False,
        )

    baseline_position = {
        item.instrument_id: index
        for index, item in enumerate(ordered_candidates, start=1)
    }
    model_ready = len(eligible_observations) >= MIN_RERANK_TRAINING_SAMPLES
    strategy_groups = _group_observations(
        eligible_observations,
        lambda item: item.primary_strategy_id or "unknown",
    )
    strategy_regime_groups = _group_observations(
        eligible_observations,
        lambda item: (
            f"{item.primary_strategy_id or 'unknown'}|{item.market_regime}"
        ),
    )
    factor_groups: dict[str, list[ResolvedRerankObservation]] = {}
    for observation in eligible_observations:
        for factor in set(observation.factor_signals):
            factor_groups.setdefault(factor, []).append(observation)

    provisional: list[tuple[RerankCandidate, float, _PosteriorEstimate | None, int, int]] = []
    baseline_values = [item.baseline_rank_score for item in ordered_candidates]
    for candidate in ordered_candidates:
        base_score = _normalized_baseline_score(
            candidate,
            ordered_candidates,
            baseline_values,
        )
        strategy_estimate = _posterior(
            strategy_groups.get(candidate.primary_strategy_id or "unknown", [])
        )
        strategy_regime_estimate = _posterior(
            strategy_regime_groups.get(
                f"{candidate.primary_strategy_id or 'unknown'}|"
                f"{candidate.market_regime}",
                [],
            )
        )
        strategy_estimate = _blend_estimates(
            strategy_estimate,
            strategy_regime_estimate,
            left_weight=0.65,
        )
        factor_estimates = [
            estimate
            for factor in set(candidate.factor_signals)
            if (estimate := _posterior(factor_groups.get(factor, []))) is not None
        ]
        factor_estimate = _combine_estimates(factor_estimates)
        evidence = _combine_candidate_evidence(
            strategy_estimate,
            factor_estimate,
        )
        strategy_samples = strategy_estimate.sample_count if strategy_estimate else 0
        factor_samples = sum(item.sample_count for item in factor_estimates)
        rerank_score = base_score
        if model_ready and evidence is not None:
            effective_samples = strategy_samples + min(factor_samples, strategy_samples * 2)
            confidence = min(1.0, math.sqrt(max(effective_samples, 0) / 30))
            expected_component = _clamp(evidence.expected_return_pct / 5, -1, 1)
            win_component = _clamp((evidence.win_probability - 0.5) * 2, -1, 1)
            downside_component = _clamp(evidence.downside_pct / 10, 0, 1)
            rerank_score += confidence * (
                0.12 * expected_component
                + 0.08 * win_component
                - 0.05 * downside_component
            )
        provisional.append(
            (
                candidate,
                round(rerank_score, 8),
                evidence,
                strategy_samples,
                factor_samples,
            )
        )

    ranked = sorted(
        provisional,
        key=lambda item: (
            -item[1],
            baseline_position[item[0].instrument_id],
            item[0].instrument_id,
        ),
    )
    rerank_position = {
        item[0].instrument_id: index
        for index, item in enumerate(ranked, start=1)
    }
    scores = [
        RerankCandidateScore(
            instrument_id=candidate.instrument_id,
            baseline_position=baseline_position[candidate.instrument_id],
            rerank_position=rerank_position[candidate.instrument_id],
            baseline_score=round(candidate.baseline_rank_score, 8),
            rerank_score=score,
            training_sample_count=len(eligible_observations),
            strategy_sample_count=strategy_samples,
            factor_sample_count=factor_samples,
            expected_return_pct=(
                round(evidence.expected_return_pct, 4) if evidence else None
            ),
            win_probability=(
                round(evidence.win_probability, 4) if evidence else None
            ),
            downside_pct=round(evidence.downside_pct, 4) if evidence else None,
            reason=_rerank_reason(
                model_ready=model_ready,
                baseline_position=baseline_position[candidate.instrument_id],
                rerank_position=rerank_position[candidate.instrument_id],
                evidence=evidence,
                training_sample_count=len(eligible_observations),
            ),
        )
        for candidate, score, evidence, strategy_samples, factor_samples in ranked
    ]
    return RerankDecision(
        decision_date=decision_date,
        training_cutoff_date=(
            eligible_observations[-1].exit_date
            if eligible_observations
            else None
        ),
        training_sample_count=len(eligible_observations),
        model_ready=model_ready,
        candidates=scores,
    )


def _group_observations(
    observations: list[ResolvedRerankObservation],
    key,
) -> dict[str, list[ResolvedRerankObservation]]:
    groups: dict[str, list[ResolvedRerankObservation]] = {}
    for observation in observations:
        groups.setdefault(str(key(observation)), []).append(observation)
    return groups


def _posterior(
    observations: list[ResolvedRerankObservation],
) -> _PosteriorEstimate | None:
    if not observations:
        return None
    returns = [float(item.return_pct) for item in observations]
    sample_count = len(returns)
    denominator = sample_count + RERANK_PRIOR_STRENGTH
    expected_return = sum(returns) / denominator
    win_probability = (sum(value > 0 for value in returns) + 2) / (sample_count + 4)
    losses = [abs(value) for value in returns if value < 0]
    downside = sum(losses) / denominator
    return _PosteriorEstimate(
        sample_count=sample_count,
        expected_return_pct=expected_return,
        win_probability=win_probability,
        downside_pct=downside,
    )


def _combine_estimates(
    estimates: list[_PosteriorEstimate],
) -> _PosteriorEstimate | None:
    if not estimates:
        return None
    weights = [math.sqrt(item.sample_count) for item in estimates]
    denominator = sum(weights)
    if denominator <= 0:
        return None
    return _PosteriorEstimate(
        sample_count=sum(item.sample_count for item in estimates),
        expected_return_pct=sum(
            item.expected_return_pct * weight
            for item, weight in zip(estimates, weights, strict=True)
        )
        / denominator,
        win_probability=sum(
            item.win_probability * weight
            for item, weight in zip(estimates, weights, strict=True)
        )
        / denominator,
        downside_pct=sum(
            item.downside_pct * weight
            for item, weight in zip(estimates, weights, strict=True)
        )
        / denominator,
    )


def _combine_candidate_evidence(
    strategy: _PosteriorEstimate | None,
    factor: _PosteriorEstimate | None,
) -> _PosteriorEstimate | None:
    if strategy is None:
        return factor
    if factor is None:
        return strategy
    return _PosteriorEstimate(
        sample_count=strategy.sample_count + factor.sample_count,
        expected_return_pct=(
            strategy.expected_return_pct * 0.55
            + factor.expected_return_pct * 0.45
        ),
        win_probability=(
            strategy.win_probability * 0.55
            + factor.win_probability * 0.45
        ),
        downside_pct=(
            strategy.downside_pct * 0.55
            + factor.downside_pct * 0.45
        ),
    )


def _blend_estimates(
    left: _PosteriorEstimate | None,
    right: _PosteriorEstimate | None,
    *,
    left_weight: float,
) -> _PosteriorEstimate | None:
    if left is None:
        return right
    if right is None:
        return left
    right_weight = 1 - left_weight
    return _PosteriorEstimate(
        sample_count=max(left.sample_count, right.sample_count),
        expected_return_pct=(
            left.expected_return_pct * left_weight
            + right.expected_return_pct * right_weight
        ),
        win_probability=(
            left.win_probability * left_weight
            + right.win_probability * right_weight
        ),
        downside_pct=(
            left.downside_pct * left_weight
            + right.downside_pct * right_weight
        ),
    )


def _normalized_baseline_score(
    candidate: RerankCandidate,
    ordered_candidates: list[RerankCandidate],
    baseline_values: list[float],
) -> float:
    if len(ordered_candidates) == 1:
        return 1.0
    position = ordered_candidates.index(candidate)
    position_score = 1 - position / (len(ordered_candidates) - 1)
    minimum = min(baseline_values)
    maximum = max(baseline_values)
    raw_score = (
        (candidate.baseline_rank_score - minimum) / (maximum - minimum)
        if maximum > minimum
        else position_score
    )
    return round(position_score * 0.6 + raw_score * 0.4, 8)


def _rerank_reason(
    *,
    model_ready: bool,
    baseline_position: int,
    rerank_position: int,
    evidence: _PosteriorEstimate | None,
    training_sample_count: int,
) -> str:
    if not model_ready:
        return f"仅有 {training_sample_count} 笔已结束交易，未达到 20 笔门槛，保持原排序。"
    if evidence is None:
        return "没有同策略或同因子的已结束交易，按原始评分排序。"
    movement = baseline_position - rerank_position
    if movement > 0:
        action = f"上调 {movement} 位"
    elif movement < 0:
        action = f"下调 {abs(movement)} 位"
    else:
        action = "维持原位"
    return (
        f"历史后验收益 {evidence.expected_return_pct:+.2f}%，"
        f"胜率 {evidence.win_probability:.0%}，下行 {evidence.downside_pct:.2f}%；{action}。"
    )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
