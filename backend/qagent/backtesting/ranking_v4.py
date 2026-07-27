from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from qagent.backtesting.ranking_v4_protocol import RANKING_V4_MODEL_VERSION


MIN_V4_TRAINING_OBSERVATIONS = 120
MIN_V4_TRAINING_DATES = 24
V4_RECENCY_HALF_LIFE_DAYS = 365.0
V4_LOWER_CONFIDENCE_Z_SCORE = 1.644854
V4_GLOBAL_PRIOR_DATE_STRENGTH = 24.0
V4_ASSET_PRIOR_DATE_STRENGTH = 16.0
V4_STRATEGY_PRIOR_DATE_STRENGTH = 12.0
V4_REGIME_PRIOR_DATE_STRENGTH = 8.0
V4_MINIMUM_DATA_COMPLETENESS = 0.68
V4_MINIMUM_POSTERIOR_VOLATILITY_PCT = 0.25
V4_UNKNOWN_VALUES = {"", "unknown", "none", "missing", "未分类", "未知"}


class RankingV4FeatureVector(BaseModel):
    strategy_score: float = Field(default=0.5, ge=0.0, le=1.0)
    factor_score: float = Field(default=0.5, ge=0.0, le=1.0)
    valuation: float = Field(default=0.5, ge=0.0, le=1.0)
    size: float = Field(default=0.5, ge=0.0, le=1.0)
    quality: float = Field(default=0.5, ge=0.0, le=1.0)
    momentum: float = Field(default=0.5, ge=0.0, le=1.0)
    trend_quality: float = Field(default=0.5, ge=0.0, le=1.0)
    breakout_quality: float = Field(default=0.5, ge=0.0, le=1.0)
    liquidity: float = Field(default=0.5, ge=0.0, le=1.0)
    low_risk: float = Field(default=0.5, ge=0.0, le=1.0)
    risk_filter: float = Field(default=0.5, ge=0.0, le=1.0)
    reversal: float = Field(default=0.5, ge=0.0, le=1.0)
    industry_strength: float = Field(default=0.5, ge=0.0, le=1.0)
    market_breadth: float = Field(default=0.5, ge=0.0, le=1.0)
    benchmark_slope: float = Field(default=0.5, ge=0.0, le=1.0)
    realized_volatility: float = Field(default=0.5, ge=0.0, le=1.0)
    cross_sectional_dispersion: float = Field(default=0.5, ge=0.0, le=1.0)
    capacity: float = Field(default=0.5, ge=0.0, le=1.0)
    tail_risk: float = Field(default=0.5, ge=0.0, le=1.0)
    execution_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    data_completeness: float = Field(default=0.0, ge=0.0, le=1.0)


class RankingV4Candidate(BaseModel):
    instrument_id: str
    baseline_rank_score: float
    decision_date: date | None = None
    feature_as_of: date | None = None
    market_regime_as_of: date | None = None
    constraint_as_of: date | None = None
    cost_as_of: date | None = None
    primary_strategy_id: str | None = None
    factor_signals: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"
    asset_type: str = "unknown"
    industry: str | None = None
    themes: list[str] = Field(default_factory=list)
    index_memberships: list[str] = Field(default_factory=list)
    underlying_ids: list[str] = Field(default_factory=list)
    factor_exposures: dict[str, float] = Field(default_factory=dict)
    features: RankingV4FeatureVector
    point_in_time_evidence_complete: bool = False
    market_regime_features_complete: bool = False
    constraint_data_complete: bool = False
    underlying_evidence_complete: bool = False
    incumbent: bool = False
    replacement_cost_pct: float | None = Field(default=None, ge=0.0)
    benchmark_opportunity_cost_pct: float | None = Field(default=None, ge=0.0)
    liquidity_penalty_pct: float | None = Field(default=None, ge=0.0)
    tail_risk_penalty_pct: float | None = Field(default=None, ge=0.0)


class ResolvedRankingV4Observation(BaseModel):
    instrument_id: str
    signal_date: date
    available_at: date
    outcome_status: str
    triggered: bool
    return_pct: float | None = None
    benchmark_return_pct: float | None = None
    cost_adjusted_net_excess_return_pct: float | None = None
    primary_strategy_id: str | None = None
    factor_signals: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"
    asset_type: str = "unknown"
    industry: str | None = None
    features: RankingV4FeatureVector

    @model_validator(mode="after")
    def validate_evidence_timeline(self) -> ResolvedRankingV4Observation:
        if self.signal_date > self.available_at:
            raise ValueError("Ranking V4 observation signal_date cannot follow available_at")
        return self


class RankingV4PosteriorParameters(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment: str
    parent_segment: str | None = None
    observation_count: int = Field(ge=1)
    date_count: int = Field(ge=1)
    expected_net_excess_return_pct: float
    lower_bound_pct: float
    win_probability: float = Field(ge=0.0, le=1.0)
    win_probability_lower_bound: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_parameters(self) -> RankingV4PosteriorParameters:
        values = (
            self.expected_net_excess_return_pct,
            self.lower_bound_pct,
            self.win_probability,
            self.win_probability_lower_bound,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Ranking V4 posterior parameters must be finite")
        if self.lower_bound_pct > self.expected_net_excess_return_pct:
            raise ValueError("Ranking V4 posterior lower bound exceeds its mean")
        if self.win_probability_lower_bound > self.win_probability:
            raise ValueError("Ranking V4 win lower bound exceeds its mean")
        return self


class RankingV4TriggerProbability(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment: str
    parent_segment: str | None = None
    observation_count: int = Field(ge=1)
    triggered_count: float = Field(ge=0.0)
    probability: float = Field(ge=0.0, le=1.0)
    lower_bound: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_probability(self) -> RankingV4TriggerProbability:
        if self.triggered_count > self.observation_count:
            raise ValueError("Ranking V4 triggered count exceeds observations")
        if self.lower_bound > self.probability:
            raise ValueError("Ranking V4 trigger lower bound exceeds its mean")
        return self


class RankingV4FrozenScoringArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_version: str = RANKING_V4_MODEL_VERSION
    cutoff: date
    training_cutoff_date: date | None = None
    training_observation_count: int = Field(ge=0)
    training_triggered_observation_count: int = Field(ge=0)
    training_date_count: int = Field(ge=0)
    model_ready: bool
    posteriors: tuple[RankingV4PosteriorParameters, ...] = ()
    trigger_probabilities: tuple[RankingV4TriggerProbability, ...] = ()
    stable_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_stable_digest(self) -> RankingV4FrozenScoringArtifact:
        posterior_segments = [item.segment for item in self.posteriors]
        trigger_segments = [item.segment for item in self.trigger_probabilities]
        if posterior_segments != sorted(set(posterior_segments)):
            raise ValueError("Ranking V4 posterior segments must be unique and sorted")
        if trigger_segments != sorted(set(trigger_segments)):
            raise ValueError("Ranking V4 trigger segments must be unique and sorted")
        if self.training_triggered_observation_count > self.training_observation_count:
            raise ValueError("Ranking V4 triggered training count exceeds all observations")
        if self.training_cutoff_date is not None and self.training_cutoff_date >= self.cutoff:
            raise ValueError("Ranking V4 training cutoff must precede artifact cutoff")
        expected_ready = (
            self.training_observation_count >= MIN_V4_TRAINING_OBSERVATIONS
            and self.training_date_count >= MIN_V4_TRAINING_DATES
            and self.training_triggered_observation_count > 0
        )
        if self.model_ready != expected_ready:
            raise ValueError("Ranking V4 model readiness does not match training counts")
        expected = _stable_digest(self.model_dump(mode="json", exclude={"stable_digest"}))
        if self.stable_digest != expected:
            raise ValueError("Ranking V4 frozen scoring artifact digest mismatch")
        return self


class RankingV4CandidateScore(BaseModel):
    instrument_id: str
    baseline_position: int
    v4_position: int
    baseline_rank_score: float
    trigger_probability: float | None = None
    trigger_probability_lower_bound: float | None = None
    expected_triggered_net_excess_return_pct: float | None = None
    expected_triggered_net_excess_lower_bound_pct: float | None = None
    expected_utility_pct: float | None = None
    expected_utility_lower_bound_pct: float | None = None
    win_probability: float | None = None
    win_probability_lower_bound: float | None = None
    replacement_cost_pct: float | None = None
    benchmark_opportunity_cost_pct: float | None = None
    liquidity_penalty_pct: float | None = None
    tail_risk_penalty_pct: float | None = None
    evidence_segment: str | None = None
    evidence_date_count: int = 0
    eligible_for_position: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)
    reason: str


class RankingV4Decision(BaseModel):
    model_version: str = RANKING_V4_MODEL_VERSION
    decision_date: date
    training_cutoff_date: date | None = None
    training_observation_count: int
    training_triggered_observation_count: int
    training_date_count: int
    model_ready: bool
    eligible_position_count: int
    cash_slot_count: int
    candidates: list[RankingV4CandidateScore] = Field(default_factory=list)


def build_ranking_v4_frozen_scoring_artifact(
    observations: list[ResolvedRankingV4Observation],
    *,
    cutoff: date,
) -> RankingV4FrozenScoringArtifact:
    trigger_observations = sorted(
        (
            item
            for item in observations
            if _is_training_observation_eligible(item, cutoff=cutoff)
            and _is_valid_trigger_observation(item)
        ),
        key=_observation_sort_key,
    )
    excess_observations = [
        item for item in trigger_observations if _is_valid_excess_observation(item)
    ]
    training_dates = {item.signal_date for item in trigger_observations}
    posteriors = _build_hierarchical_posteriors(
        excess_observations,
        decision_date=cutoff,
    )
    trigger_probabilities = _build_hierarchical_trigger_probabilities(
        trigger_observations,
        decision_date=cutoff,
    )
    payload = {
        "model_version": RANKING_V4_MODEL_VERSION,
        "cutoff": cutoff,
        "training_cutoff_date": (
            max(item.available_at for item in trigger_observations)
            if trigger_observations
            else None
        ),
        "training_observation_count": len(trigger_observations),
        "training_triggered_observation_count": len(excess_observations),
        "training_date_count": len(training_dates),
        "model_ready": (
            len(trigger_observations) >= MIN_V4_TRAINING_OBSERVATIONS
            and len(training_dates) >= MIN_V4_TRAINING_DATES
            and bool(excess_observations)
        ),
        "posteriors": tuple(sorted(posteriors, key=lambda item: item.segment)),
        "trigger_probabilities": tuple(
            sorted(trigger_probabilities, key=lambda item: item.segment)
        ),
    }
    return RankingV4FrozenScoringArtifact(
        **payload,
        stable_digest=_stable_digest(payload),
    )


def score_ranking_v4_candidates(
    candidates: list[RankingV4Candidate],
    observations: list[ResolvedRankingV4Observation],
    *,
    decision_date: date,
    evidence_cutoff_date: date | None = None,
    maximum_positions: int = 5,
) -> RankingV4Decision:
    effective_cutoff = min(decision_date, evidence_cutoff_date or decision_date)
    artifact = build_ranking_v4_frozen_scoring_artifact(
        observations,
        cutoff=effective_cutoff,
    )
    return score_ranking_v4_candidates_from_artifact(
        candidates,
        artifact,
        decision_date=decision_date,
        maximum_positions=maximum_positions,
    )


def score_ranking_v4_candidates_from_artifact(
    candidates: list[RankingV4Candidate],
    artifact: RankingV4FrozenScoringArtifact,
    *,
    decision_date: date,
    maximum_positions: int = 5,
) -> RankingV4Decision:
    if decision_date < artifact.cutoff:
        raise ValueError("decision_date cannot be earlier than artifact cutoff")
    if maximum_positions < 0:
        raise ValueError("maximum_positions cannot be negative")
    ordered = sorted(
        candidates,
        key=lambda item: (-item.baseline_rank_score, item.instrument_id),
    )
    baseline_positions = {
        item.instrument_id: position for position, item in enumerate(ordered, start=1)
    }
    posterior_by_segment = {item.segment: item for item in artifact.posteriors}
    trigger_by_segment = {item.segment: item for item in artifact.trigger_probabilities}
    provisional = [
        _score_candidate(
            candidate,
            model_ready=artifact.model_ready,
            posterior_by_segment=posterior_by_segment,
            trigger_by_segment=trigger_by_segment,
            baseline_position=baseline_positions[candidate.instrument_id],
            decision_date=decision_date,
        )
        for candidate in ordered
    ]
    ranked = sorted(
        provisional,
        key=lambda item: (
            not item.eligible_for_position,
            -_sort_value(item.expected_utility_lower_bound_pct),
            -_sort_value(item.expected_utility_pct),
            item.baseline_position,
            item.instrument_id,
        ),
    )
    scores = [
        item.model_copy(update={"v4_position": position})
        for position, item in enumerate(ranked, start=1)
    ]
    eligible_count = sum(item.eligible_for_position for item in scores)
    occupied_slots = min(maximum_positions, eligible_count)
    return RankingV4Decision(
        decision_date=decision_date,
        training_cutoff_date=artifact.training_cutoff_date,
        training_observation_count=artifact.training_observation_count,
        training_triggered_observation_count=(artifact.training_triggered_observation_count),
        training_date_count=artifact.training_date_count,
        model_ready=artifact.model_ready,
        eligible_position_count=eligible_count,
        cash_slot_count=max(maximum_positions - occupied_slots, 0),
        candidates=scores,
    )


def _score_candidate(
    candidate: RankingV4Candidate,
    *,
    model_ready: bool,
    posterior_by_segment: dict[str, RankingV4PosteriorParameters],
    trigger_by_segment: dict[str, RankingV4TriggerProbability],
    baseline_position: int,
    decision_date: date,
) -> RankingV4CandidateScore:
    posterior = _deepest_candidate_value(candidate, posterior_by_segment)
    trigger = _deepest_candidate_value(candidate, trigger_by_segment)
    blocked_reasons: list[str] = []
    if not model_ready:
        blocked_reasons.append("model_not_ready")
    blocked_reasons.extend(
        _candidate_temporal_blocked_reasons(
            candidate,
            decision_date=decision_date,
        )
    )
    if _is_unknown(candidate.market_regime):
        blocked_reasons.append("market_regime_missing")
    if not candidate.market_regime_features_complete:
        blocked_reasons.append("market_regime_evidence_incomplete")
    if not candidate.point_in_time_evidence_complete:
        blocked_reasons.append("point_in_time_evidence_incomplete")
    if not candidate.constraint_data_complete:
        blocked_reasons.append("constraint_evidence_incomplete")
    if candidate.features.data_completeness < V4_MINIMUM_DATA_COMPLETENESS:
        blocked_reasons.append("data_incomplete")
    costs = _candidate_costs(candidate, decision_date=decision_date)
    if costs is None:
        blocked_reasons.append("cost_evidence_incomplete")
    if posterior is None:
        blocked_reasons.append("net_excess_evidence_missing")
    if trigger is None:
        blocked_reasons.append("trigger_evidence_missing")

    expected_utility = None
    lower_utility = None
    if posterior is not None and trigger is not None and costs is not None:
        replacement_cost, opportunity_cost, liquidity_penalty, tail_risk_penalty = costs
        fixed_penalty = replacement_cost + liquidity_penalty + tail_risk_penalty
        expected_utility = (
            trigger.probability * posterior.expected_net_excess_return_pct
            - (1.0 - trigger.probability) * opportunity_cost
            - fixed_penalty
        )
        lower_utility = (
            trigger.lower_bound * posterior.lower_bound_pct
            - (1.0 - trigger.lower_bound) * opportunity_cost
            - fixed_penalty
        )
        if posterior.lower_bound_pct <= 0:
            blocked_reasons.append("net_excess_lower_bound_not_positive")
        if lower_utility <= 0:
            blocked_reasons.append("utility_lower_bound_not_positive")

    eligible = not blocked_reasons
    reason = _candidate_reason(
        eligible=eligible,
        posterior=posterior,
        trigger=trigger,
        expected_utility=expected_utility,
        lower_utility=lower_utility,
        blocked_reasons=blocked_reasons,
    )
    return RankingV4CandidateScore(
        instrument_id=candidate.instrument_id,
        baseline_position=baseline_position,
        v4_position=0,
        baseline_rank_score=round(float(candidate.baseline_rank_score), 8),
        trigger_probability=_rounded(trigger.probability if trigger else None),
        trigger_probability_lower_bound=_rounded(trigger.lower_bound if trigger else None),
        expected_triggered_net_excess_return_pct=_rounded(
            posterior.expected_net_excess_return_pct if posterior else None,
            digits=4,
        ),
        expected_triggered_net_excess_lower_bound_pct=_rounded(
            posterior.lower_bound_pct if posterior else None,
            digits=4,
        ),
        expected_utility_pct=_rounded(expected_utility, digits=4),
        expected_utility_lower_bound_pct=_rounded(lower_utility, digits=4),
        win_probability=_rounded(posterior.win_probability if posterior else None),
        win_probability_lower_bound=_rounded(
            posterior.win_probability_lower_bound if posterior else None
        ),
        replacement_cost_pct=_rounded(candidate.replacement_cost_pct),
        benchmark_opportunity_cost_pct=_rounded(candidate.benchmark_opportunity_cost_pct),
        liquidity_penalty_pct=_rounded(candidate.liquidity_penalty_pct),
        tail_risk_penalty_pct=_rounded(candidate.tail_risk_penalty_pct),
        evidence_segment=posterior.segment if posterior else None,
        evidence_date_count=posterior.date_count if posterior else 0,
        eligible_for_position=eligible,
        blocked_reasons=blocked_reasons,
        reason=reason,
    )


def _build_hierarchical_posteriors(
    observations: list[ResolvedRankingV4Observation],
    *,
    decision_date: date,
) -> list[RankingV4PosteriorParameters]:
    if not observations:
        return []
    result: dict[str, RankingV4PosteriorParameters] = {}
    global_posterior = _posterior(
        observations,
        segment="global",
        parent=None,
        parent_strength=V4_GLOBAL_PRIOR_DATE_STRENGTH,
        decision_date=decision_date,
    )
    if global_posterior is None:
        return []
    result[global_posterior.segment] = global_posterior

    asset_groups = _group_by(observations, lambda item: f"asset:{_asset(item.asset_type)}")
    for segment, values in sorted(asset_groups.items()):
        posterior = _posterior(
            values,
            segment=segment,
            parent=global_posterior,
            parent_strength=V4_ASSET_PRIOR_DATE_STRENGTH,
            decision_date=decision_date,
        )
        if posterior is not None:
            result[segment] = posterior

    strategy_groups = _group_by(
        observations,
        lambda item: _strategy_segment(item.asset_type, item.primary_strategy_id),
    )
    for segment, values in sorted(strategy_groups.items()):
        asset_segment = f"asset:{_asset(values[0].asset_type)}"
        posterior = _posterior(
            values,
            segment=segment,
            parent=result.get(asset_segment, global_posterior),
            parent_strength=V4_STRATEGY_PRIOR_DATE_STRENGTH,
            decision_date=decision_date,
        )
        if posterior is not None:
            result[segment] = posterior

    regime_groups = _group_by(
        observations,
        lambda item: _regime_segment(
            item.asset_type,
            item.primary_strategy_id,
            item.market_regime,
        ),
    )
    for segment, values in sorted(regime_groups.items()):
        strategy_segment = _strategy_segment(
            values[0].asset_type,
            values[0].primary_strategy_id,
        )
        posterior = _posterior(
            values,
            segment=segment,
            parent=result.get(strategy_segment, global_posterior),
            parent_strength=V4_REGIME_PRIOR_DATE_STRENGTH,
            decision_date=decision_date,
        )
        if posterior is not None:
            result[segment] = posterior
    return list(result.values())


def _build_hierarchical_trigger_probabilities(
    observations: list[ResolvedRankingV4Observation],
    *,
    decision_date: date,
) -> list[RankingV4TriggerProbability]:
    if not observations:
        return []
    result: dict[str, RankingV4TriggerProbability] = {}
    global_probability = _trigger_probability(
        observations,
        segment="global",
        parent=None,
        parent_strength=V4_GLOBAL_PRIOR_DATE_STRENGTH,
        decision_date=decision_date,
    )
    if global_probability is None:
        return []
    result[global_probability.segment] = global_probability

    asset_groups = _group_by(observations, lambda item: f"asset:{_asset(item.asset_type)}")
    for segment, values in sorted(asset_groups.items()):
        probability = _trigger_probability(
            values,
            segment=segment,
            parent=global_probability,
            parent_strength=V4_ASSET_PRIOR_DATE_STRENGTH,
            decision_date=decision_date,
        )
        if probability is not None:
            result[segment] = probability

    strategy_groups = _group_by(
        observations,
        lambda item: _strategy_segment(item.asset_type, item.primary_strategy_id),
    )
    for segment, values in sorted(strategy_groups.items()):
        probability = _trigger_probability(
            values,
            segment=segment,
            parent=result.get(
                f"asset:{_asset(values[0].asset_type)}",
                global_probability,
            ),
            parent_strength=V4_STRATEGY_PRIOR_DATE_STRENGTH,
            decision_date=decision_date,
        )
        if probability is not None:
            result[segment] = probability

    regime_groups = _group_by(
        observations,
        lambda item: _regime_segment(
            item.asset_type,
            item.primary_strategy_id,
            item.market_regime,
        ),
    )
    for segment, values in sorted(regime_groups.items()):
        probability = _trigger_probability(
            values,
            segment=segment,
            parent=result.get(
                _strategy_segment(values[0].asset_type, values[0].primary_strategy_id),
                global_probability,
            ),
            parent_strength=V4_REGIME_PRIOR_DATE_STRENGTH,
            decision_date=decision_date,
        )
        if probability is not None:
            result[segment] = probability
    return list(result.values())


def _posterior(
    observations: list[ResolvedRankingV4Observation],
    *,
    segment: str,
    parent: RankingV4PosteriorParameters | None,
    parent_strength: float,
    decision_date: date,
) -> RankingV4PosteriorParameters | None:
    by_date: dict[date, list[float]] = defaultdict(list)
    for item in observations:
        value = item.cost_adjusted_net_excess_return_pct
        if value is not None and math.isfinite(float(value)):
            by_date[item.signal_date].append(float(value))
    if not by_date:
        return None
    dated_values = [
        (signal_date, sum(values) / len(values)) for signal_date, values in sorted(by_date.items())
    ]
    weights = _recency_weights(dated_values, decision_date=decision_date)
    effective_weight = sum(weights)
    prior_mean = parent.expected_net_excess_return_pct if parent else 0.0
    denominator = effective_weight + parent_strength
    expected = (
        sum(value * weight for (_, value), weight in zip(dated_values, weights, strict=True))
        + prior_mean * parent_strength
    ) / denominator
    variance = (
        sum(
            weight * (value - expected) ** 2
            for (_, value), weight in zip(dated_values, weights, strict=True)
        )
        + parent_strength * (prior_mean - expected) ** 2
    ) / denominator
    variance = max(variance, V4_MINIMUM_POSTERIOR_VOLATILITY_PCT**2)
    standard_error = math.sqrt(variance / denominator)
    prior_win = parent.win_probability if parent else 0.5
    wins = sum(
        weight for (_, value), weight in zip(dated_values, weights, strict=True) if value > 0
    )
    win_probability = (wins + prior_win * parent_strength) / denominator
    win_standard_error = math.sqrt(
        max(win_probability * (1.0 - win_probability), 0.0) / denominator
    )
    return RankingV4PosteriorParameters(
        segment=segment,
        parent_segment=parent.segment if parent else None,
        observation_count=len(observations),
        date_count=len(dated_values),
        expected_net_excess_return_pct=expected,
        lower_bound_pct=(expected - V4_LOWER_CONFIDENCE_Z_SCORE * standard_error),
        win_probability=win_probability,
        win_probability_lower_bound=max(
            0.0,
            win_probability - V4_LOWER_CONFIDENCE_Z_SCORE * win_standard_error,
        ),
    )


def _trigger_probability(
    observations: list[ResolvedRankingV4Observation],
    *,
    segment: str,
    parent: RankingV4TriggerProbability | None,
    parent_strength: float,
    decision_date: date,
) -> RankingV4TriggerProbability | None:
    if not observations:
        return None
    by_date: dict[date, list[float]] = defaultdict(list)
    for item in observations:
        by_date[item.signal_date].append(1.0 if item.triggered else 0.0)
    dated = [
        (signal_date, sum(values) / len(values)) for signal_date, values in sorted(by_date.items())
    ]
    weights = _recency_weights(dated, decision_date=decision_date)
    effective_weight = sum(weights)
    prior_probability = parent.probability if parent else 0.5
    denominator = effective_weight + parent_strength
    probability = (
        sum(value * weight for (_, value), weight in zip(dated, weights, strict=True))
        + prior_probability * parent_strength
    ) / denominator
    standard_error = math.sqrt(max(probability * (1.0 - probability), 0.0) / denominator)
    return RankingV4TriggerProbability(
        segment=segment,
        parent_segment=parent.segment if parent else None,
        observation_count=len(dated),
        triggered_count=sum(value for _, value in dated),
        probability=probability,
        lower_bound=max(
            0.0,
            probability - V4_LOWER_CONFIDENCE_Z_SCORE * standard_error,
        ),
    )


def _candidate_segments(candidate: RankingV4Candidate) -> tuple[str, ...]:
    return (
        "global",
        f"asset:{_asset(candidate.asset_type)}",
        _strategy_segment(candidate.asset_type, candidate.primary_strategy_id),
        _regime_segment(
            candidate.asset_type,
            candidate.primary_strategy_id,
            candidate.market_regime,
        ),
    )


def _deepest_candidate_value(candidate: RankingV4Candidate, values: dict[str, object]):
    for segment in reversed(_candidate_segments(candidate)):
        if segment in values:
            return values[segment]
    return None


def _strategy_segment(asset_type: str, strategy_id: str | None) -> str:
    return f"strategy:{_asset(asset_type)}:{_value(strategy_id)}"


def _regime_segment(
    asset_type: str,
    strategy_id: str | None,
    market_regime: str,
) -> str:
    return f"regime:{_asset(asset_type)}:{_value(strategy_id)}:{_value(market_regime)}"


def _asset(asset_type: str) -> str:
    return _value(asset_type)


def _value(value: str | None) -> str:
    normalized = (value or "unknown").strip().lower()
    return normalized or "unknown"


def _is_unknown(value: str | None) -> bool:
    return _value(value) in V4_UNKNOWN_VALUES


def _group_by(
    observations: Iterable[ResolvedRankingV4Observation],
    key,
) -> dict[str, list[ResolvedRankingV4Observation]]:
    groups: dict[str, list[ResolvedRankingV4Observation]] = defaultdict(list)
    for observation in observations:
        groups[key(observation)].append(observation)
    return groups


def _recency_weights(
    dated_values: list[tuple[date, float]],
    *,
    decision_date: date,
) -> list[float]:
    future_dates = [signal_date for signal_date, _ in dated_values if signal_date >= decision_date]
    if future_dates:
        raise ValueError("Ranking V4 recency weights require signal dates before decision_date")
    return [
        0.5 ** ((decision_date - signal_date).days / V4_RECENCY_HALF_LIFE_DAYS)
        for signal_date, _ in dated_values
    ]


def _is_training_observation_eligible(
    observation: ResolvedRankingV4Observation,
    *,
    cutoff: date,
) -> bool:
    return (
        observation.signal_date <= observation.available_at
        and observation.signal_date < cutoff
        and observation.available_at < cutoff
    )


def _is_valid_excess_observation(
    observation: ResolvedRankingV4Observation,
) -> bool:
    return (
        observation.outcome_status == "resolved"
        and observation.triggered
        and observation.return_pct is not None
        and math.isfinite(float(observation.return_pct))
        and observation.benchmark_return_pct is not None
        and math.isfinite(float(observation.benchmark_return_pct))
        and observation.cost_adjusted_net_excess_return_pct is not None
        and math.isfinite(float(observation.cost_adjusted_net_excess_return_pct))
        and not _is_unknown(observation.market_regime)
    )


def _is_valid_trigger_observation(
    observation: ResolvedRankingV4Observation,
) -> bool:
    if _is_unknown(observation.market_regime):
        return False
    if _is_valid_excess_observation(observation):
        return True
    return (
        observation.outcome_status == "not_triggered"
        and not observation.triggered
        and observation.return_pct is None
        and observation.cost_adjusted_net_excess_return_pct is None
    )


def _observation_sort_key(
    observation: ResolvedRankingV4Observation,
) -> tuple[date, date, str, str, str]:
    return (
        observation.available_at,
        observation.signal_date,
        observation.instrument_id,
        observation.primary_strategy_id or "",
        observation.asset_type,
    )


def _candidate_reason(
    *,
    eligible: bool,
    posterior: RankingV4PosteriorParameters | None,
    trigger: RankingV4TriggerProbability | None,
    expected_utility: float | None,
    lower_utility: float | None,
    blocked_reasons: list[str],
) -> str:
    if eligible and posterior is not None and trigger is not None:
        return (
            f"触发概率 {trigger.probability:.0%}，触发后净超额下界 "
            f"{posterior.lower_bound_pct:+.2f}%，保守效用 {lower_utility:+.2f}%；"
            "满足占仓门禁。"
        )
    labels = {
        "model_not_ready": "历史训练证据不足",
        "candidate_decision_date_missing": "候选决策日缺失",
        "candidate_decision_date_mismatch": "候选决策日与本次评分不一致",
        "feature_evidence_time_missing": "特征时点缺失",
        "feature_evidence_after_decision": "特征证据晚于决策日",
        "market_regime_evidence_time_missing": "市场状态时点缺失",
        "market_regime_evidence_after_decision": "市场状态证据晚于决策日",
        "constraint_evidence_time_missing": "组合约束时点缺失",
        "constraint_evidence_after_decision": "组合约束证据晚于决策日",
        "cost_evidence_time_missing": "成本证据时点缺失",
        "cost_evidence_after_decision": "成本证据晚于决策日",
        "market_regime_missing": "市场状态缺失",
        "market_regime_evidence_incomplete": "市场状态四项证据不完整",
        "constraint_evidence_incomplete": "组合约束证据不完整",
        "cost_evidence_incomplete": "交易成本证据不完整",
        "data_incomplete": "数据完整度不足",
        "net_excess_evidence_missing": "缺少触发后净超额证据",
        "trigger_evidence_missing": "缺少买点触发证据",
        "net_excess_lower_bound_not_positive": "触发后净超额下界不为正",
        "utility_lower_bound_not_positive": "扣除机会成本和交易成本后下界不为正",
    }
    detail = "、".join(labels.get(item, item) for item in blocked_reasons)
    if expected_utility is not None and lower_utility is not None:
        detail += f"；期望效用 {expected_utility:+.2f}%，保守效用 {lower_utility:+.2f}%"
    return detail + "；保持现金或观察。"


def _candidate_temporal_blocked_reasons(
    candidate: RankingV4Candidate,
    *,
    decision_date: date,
) -> list[str]:
    reasons: list[str] = []
    if candidate.decision_date is None:
        reasons.append("candidate_decision_date_missing")
    elif candidate.decision_date != decision_date:
        reasons.append("candidate_decision_date_mismatch")

    evidence_dates = (
        ("feature", candidate.feature_as_of),
        ("market_regime", candidate.market_regime_as_of),
        ("constraint", candidate.constraint_as_of),
        ("cost", candidate.cost_as_of),
    )
    for evidence_name, as_of in evidence_dates:
        if as_of is None:
            reasons.append(f"{evidence_name}_evidence_time_missing")
        elif as_of > decision_date:
            reasons.append(f"{evidence_name}_evidence_after_decision")
    return reasons


def _candidate_costs(
    candidate: RankingV4Candidate,
    *,
    decision_date: date,
) -> tuple[float, float, float, float] | None:
    if candidate.cost_as_of is None or candidate.cost_as_of > decision_date:
        return None
    costs = (
        candidate.replacement_cost_pct,
        candidate.benchmark_opportunity_cost_pct,
        candidate.liquidity_penalty_pct,
        candidate.tail_risk_penalty_pct,
    )
    if any(value is None for value in costs):
        return None
    resolved = tuple(float(value) for value in costs if value is not None)
    if len(resolved) != 4 or not all(math.isfinite(value) for value in resolved):
        return None
    return resolved


def _rounded(value: float | None, *, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _sort_value(value: float | None) -> float:
    return value if value is not None and math.isfinite(value) else -math.inf


def _stable_digest(payload: object) -> str:
    canonical = json.dumps(
        _json_compatible(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_compatible(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _json_compatible(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value
