from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

from pydantic import BaseModel, Field

from qagent.backtesting.ranking_v3_protocol import RANKING_V3_MODEL_VERSION


MIN_V3_TRAINING_OBSERVATIONS = 120
MIN_V3_TRAINING_DATES = 24
V3_PRIOR_DATE_STRENGTH = 12.0
V3_RECENCY_HALF_LIFE_DAYS = 365.0
V3_MAX_CALIBRATION_DELTA = 0.05
V3_INCUMBENT_TURNOVER_BONUS = 0.025


class RankingV3FeatureVector(BaseModel):
    strategy_score: float = Field(default=0.5, ge=0.0, le=1.0)
    factor_score: float = Field(default=0.5, ge=0.0, le=1.0)
    valuation: float = Field(default=0.5, ge=0.0, le=1.0)
    size: float = Field(default=0.5, ge=0.0, le=1.0)
    quality: float = Field(default=0.5, ge=0.0, le=1.0)
    momentum: float = Field(default=0.5, ge=0.0, le=1.0)
    trend_quality: float = Field(default=0.5, ge=0.0, le=1.0)
    liquidity: float = Field(default=0.5, ge=0.0, le=1.0)
    low_risk: float = Field(default=0.5, ge=0.0, le=1.0)
    risk_filter: float = Field(default=0.5, ge=0.0, le=1.0)
    reversal: float = Field(default=0.5, ge=0.0, le=1.0)
    execution_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    data_completeness: float = Field(default=0.0, ge=0.0, le=1.0)


class RankingV3Candidate(BaseModel):
    instrument_id: str
    baseline_rank_score: float
    primary_strategy_id: str | None = None
    factor_signals: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"
    asset_type: str = "unknown"
    industry: str | None = None
    index_memberships: list[str] = Field(default_factory=list)
    features: RankingV3FeatureVector
    incumbent: bool = False


class ResolvedRankingV3Observation(BaseModel):
    instrument_id: str
    signal_date: date
    available_at: date
    outcome_status: str
    triggered: bool
    return_pct: float | None = None
    benchmark_return_pct: float | None = None
    net_excess_return_pct: float | None = None
    primary_strategy_id: str | None = None
    factor_signals: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"
    asset_type: str = "unknown"
    features: RankingV3FeatureVector


class RankingV3CandidateScore(BaseModel):
    instrument_id: str
    baseline_position: int
    v3_position: int
    baseline_rank_score: float
    frozen_factor_score: float
    calibration_delta: float
    turnover_bonus: float
    v3_score: float
    training_observation_count: int
    training_date_count: int
    evidence_date_count: int
    expected_net_excess_return_pct: float | None = None
    expected_net_excess_lower_bound_pct: float | None = None
    win_probability: float | None = None
    win_probability_lower_bound: float | None = None
    trigger_probability: float | None = None
    reason: str


class RankingV3Decision(BaseModel):
    model_version: str = RANKING_V3_MODEL_VERSION
    decision_date: date
    training_cutoff_date: date | None = None
    training_observation_count: int
    training_date_count: int
    model_ready: bool
    candidates: list[RankingV3CandidateScore] = Field(default_factory=list)


class _PosteriorEstimate(BaseModel):
    date_count: int
    expected_excess_return_pct: float
    lower_bound_pct: float
    win_probability: float
    win_probability_lower_bound: float


def score_ranking_v3_candidates(
    candidates: list[RankingV3Candidate],
    observations: list[ResolvedRankingV3Observation],
    *,
    decision_date: date,
    evidence_cutoff_date: date | None = None,
) -> RankingV3Decision:
    effective_cutoff = min(
        decision_date,
        evidence_cutoff_date or decision_date,
    )
    eligible = sorted(
        (
            item
            for item in observations
            if item.available_at < effective_cutoff
            and item.outcome_status == "resolved"
            and item.net_excess_return_pct is not None
        ),
        key=lambda item: (item.available_at, item.signal_date, item.instrument_id),
    )
    eligible_dates = {item.signal_date for item in eligible}
    model_ready = (
        len(eligible) >= MIN_V3_TRAINING_OBSERVATIONS
        and len(eligible_dates) >= MIN_V3_TRAINING_DATES
    )
    ordered = sorted(
        candidates,
        key=lambda item: (-item.baseline_rank_score, item.instrument_id),
    )
    baseline_positions = {
        item.instrument_id: index for index, item in enumerate(ordered, start=1)
    }
    segment_groups = _segment_groups(eligible)
    resolved_or_not = [
        item
        for item in observations
        if item.available_at < effective_cutoff
        and item.outcome_status in {"resolved", "not_triggered_or_unfillable"}
    ]
    trigger_groups = _trigger_segment_groups(resolved_or_not)

    provisional: list[
        tuple[
            RankingV3Candidate,
            float,
            float,
            float,
            _PosteriorEstimate | None,
            float | None,
        ]
    ] = []
    for candidate in ordered:
        frozen_score = frozen_feature_score(
            candidate.features,
            asset_type=candidate.asset_type,
        )
        evidence = _candidate_evidence(
            candidate,
            segment_groups,
            decision_date=effective_cutoff,
        )
        calibration = (
            _calibration_delta(evidence)
            if model_ready and evidence is not None
            else 0.0
        )
        turnover_bonus = V3_INCUMBENT_TURNOVER_BONUS if candidate.incumbent else 0.0
        trigger_probability = _candidate_trigger_probability(candidate, trigger_groups)
        trigger_penalty = (
            min(0.02, (0.45 - trigger_probability) * 0.05)
            if model_ready
            and trigger_probability is not None
            and trigger_probability < 0.45
            else 0.0
        )
        score = _clamp(
            frozen_score + calibration + turnover_bonus - trigger_penalty,
            0.0,
            1.0,
        )
        provisional.append(
            (
                candidate,
                score,
                frozen_score,
                calibration,
                evidence,
                trigger_probability,
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
    v3_positions = {
        item[0].instrument_id: index for index, item in enumerate(ranked, start=1)
    }
    scores = [
        RankingV3CandidateScore(
            instrument_id=candidate.instrument_id,
            baseline_position=baseline_positions[candidate.instrument_id],
            v3_position=v3_positions[candidate.instrument_id],
            baseline_rank_score=round(candidate.baseline_rank_score, 8),
            frozen_factor_score=round(frozen_score, 8),
            calibration_delta=round(calibration, 8),
            turnover_bonus=(
                V3_INCUMBENT_TURNOVER_BONUS if candidate.incumbent else 0.0
            ),
            v3_score=round(score, 8),
            training_observation_count=len(eligible),
            training_date_count=len(eligible_dates),
            evidence_date_count=evidence.date_count if evidence else 0,
            expected_net_excess_return_pct=(
                round(evidence.expected_excess_return_pct, 4) if evidence else None
            ),
            expected_net_excess_lower_bound_pct=(
                round(evidence.lower_bound_pct, 4) if evidence else None
            ),
            win_probability=round(evidence.win_probability, 4) if evidence else None,
            win_probability_lower_bound=(
                round(evidence.win_probability_lower_bound, 4) if evidence else None
            ),
            trigger_probability=(
                round(trigger_probability, 4) if trigger_probability is not None else None
            ),
            reason=_score_reason(
                model_ready=model_ready,
                evidence=evidence,
                calibration_delta=calibration,
                incumbent=candidate.incumbent,
                trigger_probability=trigger_probability,
            ),
        )
        for candidate, score, frozen_score, calibration, evidence, trigger_probability in ranked
    ]
    return RankingV3Decision(
        decision_date=decision_date,
        training_cutoff_date=eligible[-1].available_at if eligible else None,
        training_observation_count=len(eligible),
        training_date_count=len(eligible_dates),
        model_ready=model_ready,
        candidates=scores,
    )


def frozen_feature_score(
    features: RankingV3FeatureVector,
    *,
    asset_type: str,
) -> float:
    if asset_type.lower() in {"etf", "fund", "index_fund"}:
        raw = (
            features.trend_quality * 0.40
            + features.momentum * 0.35
            + features.low_risk * 0.15
            + features.liquidity * 0.10
        )
    else:
        raw = (
            features.trend_quality * 0.22
            + features.momentum * 0.20
            + features.quality * 0.15
            + features.valuation * 0.10
            + features.low_risk * 0.15
            + features.liquidity * 0.10
            + features.risk_filter * 0.08
        )
    data_penalty = (1.0 - features.data_completeness) * 0.10
    return _clamp(raw - features.execution_penalty * 0.15 - data_penalty, 0.0, 1.0)


def _segment_groups(
    observations: list[ResolvedRankingV3Observation],
) -> dict[str, list[ResolvedRankingV3Observation]]:
    groups: dict[str, list[ResolvedRankingV3Observation]] = defaultdict(list)
    for observation in observations:
        for key in _observation_segments(observation):
            groups[key].append(observation)
    return groups


def _trigger_segment_groups(
    observations: list[ResolvedRankingV3Observation],
) -> dict[str, list[ResolvedRankingV3Observation]]:
    groups: dict[str, list[ResolvedRankingV3Observation]] = defaultdict(list)
    for observation in observations:
        groups[f"strategy:{observation.primary_strategy_id or 'unknown'}"].append(observation)
        groups[f"asset:{observation.asset_type or 'unknown'}"].append(observation)
    return groups


def _observation_segments(
    observation: ResolvedRankingV3Observation,
) -> list[str]:
    return _candidate_segments(
        primary_strategy_id=observation.primary_strategy_id,
        asset_type=observation.asset_type,
        features=observation.features,
    )


def _candidate_segments(
    *,
    primary_strategy_id: str | None,
    asset_type: str,
    features: RankingV3FeatureVector,
) -> list[str]:
    segments = [
        f"strategy:{primary_strategy_id or 'unknown'}",
        f"asset:{asset_type or 'unknown'}",
    ]
    for name in (
        "valuation",
        "quality",
        "momentum",
        "trend_quality",
        "liquidity",
        "low_risk",
        "risk_filter",
        "reversal",
    ):
        value = float(getattr(features, name))
        bucket = "high" if value >= 0.67 else "low" if value <= 0.33 else "mid"
        if bucket != "mid":
            segments.append(f"factor:{name}:{bucket}")
    return segments


def _candidate_evidence(
    candidate: RankingV3Candidate,
    groups: dict[str, list[ResolvedRankingV3Observation]],
    *,
    decision_date: date,
) -> _PosteriorEstimate | None:
    segments = _candidate_segments(
        primary_strategy_id=candidate.primary_strategy_id,
        asset_type=candidate.asset_type,
        features=candidate.features,
    )
    strategy = _posterior(
        groups.get(f"strategy:{candidate.primary_strategy_id or 'unknown'}", []),
        decision_date=decision_date,
    )
    asset = _posterior(
        groups.get(f"asset:{candidate.asset_type or 'unknown'}", []),
        decision_date=decision_date,
    )
    factor_estimates = [
        estimate
        for key in segments
        if key.startswith("factor:")
        and (
            estimate := _posterior(
                groups.get(key, []),
                decision_date=decision_date,
            )
        )
        is not None
    ]
    factor = _combine_equal(factor_estimates)
    return _combine_weighted(
        [
            (strategy, 0.50),
            (asset, 0.20),
            (factor, 0.30),
        ]
    )


def _posterior(
    observations: list[ResolvedRankingV3Observation],
    *,
    decision_date: date,
) -> _PosteriorEstimate | None:
    if not observations:
        return None
    by_date: dict[date, list[float]] = defaultdict(list)
    for item in observations:
        if item.net_excess_return_pct is not None:
            by_date[item.signal_date].append(float(item.net_excess_return_pct))
    if not by_date:
        return None
    dated_values = [
        (signal_date, sum(values) / len(values))
        for signal_date, values in sorted(by_date.items())
    ]
    weights = [
        0.5 ** (max((decision_date - signal_date).days, 0) / V3_RECENCY_HALF_LIFE_DAYS)
        for signal_date, _ in dated_values
    ]
    effective_weight = sum(weights)
    denominator = effective_weight + V3_PRIOR_DATE_STRENGTH
    expected = sum(
        value * weight
        for (_, value), weight in zip(dated_values, weights, strict=True)
    ) / denominator
    variance = (
        sum(
            weight * (value - expected) ** 2
            for (_, value), weight in zip(dated_values, weights, strict=True)
        )
        + V3_PRIOR_DATE_STRENGTH * expected**2
    ) / denominator
    standard_error = math.sqrt(max(variance, 0.0) / denominator)
    wins = sum(
        weight
        for (_, value), weight in zip(dated_values, weights, strict=True)
        if value > 0
    )
    win_probability = (wins + 2.0) / (effective_weight + 4.0)
    win_standard_error = math.sqrt(
        max(win_probability * (1.0 - win_probability), 0.0)
        / (effective_weight + 4.0)
    )
    return _PosteriorEstimate(
        date_count=len(dated_values),
        expected_excess_return_pct=expected,
        lower_bound_pct=expected - 1.644854 * standard_error,
        win_probability=win_probability,
        win_probability_lower_bound=max(
            0.0,
            win_probability - 1.644854 * win_standard_error,
        ),
    )


def _combine_equal(
    estimates: list[_PosteriorEstimate],
) -> _PosteriorEstimate | None:
    if not estimates:
        return None
    return _combine_weighted([(item, 1.0) for item in estimates])


def _combine_weighted(
    values: list[tuple[_PosteriorEstimate | None, float]],
) -> _PosteriorEstimate | None:
    available = [(estimate, weight) for estimate, weight in values if estimate is not None]
    if not available:
        return None
    total_weight = sum(weight for _, weight in available)
    return _PosteriorEstimate(
        date_count=max(estimate.date_count for estimate, _ in available),
        expected_excess_return_pct=sum(
            estimate.expected_excess_return_pct * weight
            for estimate, weight in available
        )
        / total_weight,
        lower_bound_pct=sum(
            estimate.lower_bound_pct * weight for estimate, weight in available
        )
        / total_weight,
        win_probability=sum(
            estimate.win_probability * weight for estimate, weight in available
        )
        / total_weight,
        win_probability_lower_bound=sum(
            estimate.win_probability_lower_bound * weight
            for estimate, weight in available
        )
        / total_weight,
    )


def _calibration_delta(evidence: _PosteriorEstimate) -> float:
    alpha_component = _clamp(evidence.expected_excess_return_pct / 4.0, -1.0, 1.0)
    win_component = _clamp((evidence.win_probability - 0.5) * 2.0, -1.0, 1.0)
    raw = V3_MAX_CALIBRATION_DELTA * (
        alpha_component * 0.70 + win_component * 0.30
    )
    return _clamp(raw, -V3_MAX_CALIBRATION_DELTA, V3_MAX_CALIBRATION_DELTA)


def _candidate_trigger_probability(
    candidate: RankingV3Candidate,
    groups: dict[str, list[ResolvedRankingV3Observation]],
) -> float | None:
    keys = [
        f"strategy:{candidate.primary_strategy_id or 'unknown'}",
        f"asset:{candidate.asset_type or 'unknown'}",
    ]
    probabilities = []
    for key in keys:
        values = groups.get(key, [])
        if not values:
            continue
        probabilities.append(
            (sum(item.triggered for item in values) + 2.0) / (len(values) + 4.0)
        )
    return sum(probabilities) / len(probabilities) if probabilities else None


def _score_reason(
    *,
    model_ready: bool,
    evidence: _PosteriorEstimate | None,
    calibration_delta: float,
    incumbent: bool,
    trigger_probability: float | None,
) -> str:
    if not model_ready:
        return "历史证据未达到120笔且24个独立调仓日，使用冻结因子分。"
    parts = [
        (
            f"点时净超额校准 {calibration_delta:+.3f}"
            if evidence is not None
            else "没有匹配的点时证据，校准为0"
        )
    ]
    if incumbent:
        parts.append("保留小幅换手成本优势")
    if trigger_probability is not None:
        parts.append(f"历史触发率 {trigger_probability:.0%}")
    return "；".join(parts)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
