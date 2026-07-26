from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class RankingV3PosteriorParameters(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment: str
    date_count: int = Field(ge=1)
    expected_excess_return_pct: float
    lower_bound_pct: float
    win_probability: float = Field(ge=0.0, le=1.0)
    win_probability_lower_bound: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_parameters(self) -> RankingV3PosteriorParameters:
        values = (
            self.expected_excess_return_pct,
            self.lower_bound_pct,
            self.win_probability,
            self.win_probability_lower_bound,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Ranking V3 posterior parameters must be finite")
        if self.win_probability_lower_bound > self.win_probability:
            raise ValueError("Ranking V3 posterior lower bound exceeds probability")
        return self


class RankingV3TriggerProbability(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment: str
    observation_count: int = Field(ge=1)
    triggered_count: int = Field(ge=0)
    probability: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_probability(self) -> RankingV3TriggerProbability:
        if self.triggered_count > self.observation_count:
            raise ValueError("Ranking V3 triggered count exceeds observations")
        expected = (self.triggered_count + 2.0) / (self.observation_count + 4.0)
        if not math.isclose(self.probability, expected, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("Ranking V3 trigger probability does not match counts")
        return self


class RankingV3FrozenScoringArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_version: str = RANKING_V3_MODEL_VERSION
    cutoff: date
    training_cutoff_date: date | None = None
    training_observation_count: int = Field(ge=0)
    training_date_count: int = Field(ge=0)
    model_ready: bool
    segment_posteriors: tuple[RankingV3PosteriorParameters, ...] = ()
    trigger_probabilities: tuple[RankingV3TriggerProbability, ...] = ()
    stable_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_stable_digest(self) -> RankingV3FrozenScoringArtifact:
        posterior_segments = [item.segment for item in self.segment_posteriors]
        trigger_segments = [item.segment for item in self.trigger_probabilities]
        if posterior_segments != sorted(set(posterior_segments)):
            raise ValueError("Ranking V3 posterior segments must be unique and sorted")
        if trigger_segments != sorted(set(trigger_segments)):
            raise ValueError("Ranking V3 trigger segments must be unique and sorted")
        if self.training_cutoff_date is not None and self.training_cutoff_date >= self.cutoff:
            raise ValueError("Ranking V3 training cutoff must precede artifact cutoff")
        expected_ready = (
            self.training_observation_count >= MIN_V3_TRAINING_OBSERVATIONS
            and self.training_date_count >= MIN_V3_TRAINING_DATES
        )
        if self.model_ready != expected_ready:
            raise ValueError("Ranking V3 model readiness does not match training counts")
        expected = _ranking_v3_artifact_digest(
            self.model_dump(mode="json", exclude={"stable_digest"})
        )
        if self.stable_digest != expected:
            raise ValueError("Ranking V3 frozen scoring artifact digest mismatch")
        return self


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
    artifact = build_ranking_v3_frozen_scoring_artifact(
        observations,
        cutoff=effective_cutoff,
    )
    return score_ranking_v3_candidates_from_artifact(
        candidates,
        artifact,
        decision_date=decision_date,
    )


def build_ranking_v3_frozen_scoring_artifact(
    observations: list[ResolvedRankingV3Observation],
    *,
    cutoff: date,
) -> RankingV3FrozenScoringArtifact:
    eligible = sorted(
        (
            item
            for item in observations
            if item.available_at < cutoff and _is_valid_posterior_observation(item)
        ),
        key=_observation_sort_key,
    )
    eligible_dates = {item.signal_date for item in eligible}
    model_ready = (
        len(eligible) >= MIN_V3_TRAINING_OBSERVATIONS
        and len(eligible_dates) >= MIN_V3_TRAINING_DATES
    )
    segment_groups = _segment_groups(eligible)
    segment_posteriors = tuple(
        RankingV3PosteriorParameters(
            segment=segment,
            **posterior.model_dump(),
        )
        for segment, values in sorted(segment_groups.items())
        if (posterior := _posterior(values, decision_date=cutoff)) is not None
    )
    trigger_groups = _trigger_segment_groups(
        sorted(
            (
                item
                for item in observations
                if item.available_at < cutoff and _is_valid_trigger_observation(item)
            ),
            key=_observation_sort_key,
        )
    )
    trigger_probabilities = tuple(
        RankingV3TriggerProbability(
            segment=segment,
            observation_count=len(values),
            triggered_count=sum(item.triggered for item in values),
            probability=(sum(item.triggered for item in values) + 2.0) / (len(values) + 4.0),
        )
        for segment, values in sorted(trigger_groups.items())
        if values
    )
    payload = {
        "model_version": RANKING_V3_MODEL_VERSION,
        "cutoff": cutoff,
        "training_cutoff_date": eligible[-1].available_at if eligible else None,
        "training_observation_count": len(eligible),
        "training_date_count": len(eligible_dates),
        "model_ready": model_ready,
        "segment_posteriors": segment_posteriors,
        "trigger_probabilities": trigger_probabilities,
    }
    return RankingV3FrozenScoringArtifact(
        **payload,
        stable_digest=_ranking_v3_artifact_digest(payload),
    )


def score_ranking_v3_candidates_from_artifact(
    candidates: list[RankingV3Candidate],
    artifact: RankingV3FrozenScoringArtifact,
    *,
    decision_date: date,
) -> RankingV3Decision:
    if decision_date < artifact.cutoff:
        raise ValueError("decision_date cannot be earlier than artifact cutoff")
    posterior_by_segment = {
        item.segment: _PosteriorEstimate(
            date_count=item.date_count,
            expected_excess_return_pct=item.expected_excess_return_pct,
            lower_bound_pct=item.lower_bound_pct,
            win_probability=item.win_probability,
            win_probability_lower_bound=item.win_probability_lower_bound,
        )
        for item in artifact.segment_posteriors
    }
    trigger_by_segment = {item.segment: item.probability for item in artifact.trigger_probabilities}
    return _score_ranking_v3_candidates_core(
        candidates,
        decision_date=decision_date,
        training_cutoff_date=artifact.training_cutoff_date,
        training_observation_count=artifact.training_observation_count,
        training_date_count=artifact.training_date_count,
        model_ready=artifact.model_ready,
        posterior_by_segment=posterior_by_segment,
        trigger_by_segment=trigger_by_segment,
    )


def _score_ranking_v3_candidates_core(
    candidates: list[RankingV3Candidate],
    *,
    decision_date: date,
    training_cutoff_date: date | None,
    training_observation_count: int,
    training_date_count: int,
    model_ready: bool,
    posterior_by_segment: dict[str, _PosteriorEstimate],
    trigger_by_segment: dict[str, float],
) -> RankingV3Decision:
    ordered = sorted(
        candidates,
        key=lambda item: (-item.baseline_rank_score, item.instrument_id),
    )
    baseline_positions = {item.instrument_id: index for index, item in enumerate(ordered, start=1)}

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
            posterior_by_segment,
        )
        calibration = _calibration_delta(evidence) if model_ready and evidence is not None else 0.0
        turnover_bonus = V3_INCUMBENT_TURNOVER_BONUS if candidate.incumbent else 0.0
        trigger_probability = _candidate_trigger_probability(
            candidate,
            trigger_by_segment,
        )
        trigger_penalty = (
            min(0.02, (0.45 - trigger_probability) * 0.05)
            if model_ready and trigger_probability is not None and trigger_probability < 0.45
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
    v3_positions = {item[0].instrument_id: index for index, item in enumerate(ranked, start=1)}
    scores = [
        RankingV3CandidateScore(
            instrument_id=candidate.instrument_id,
            baseline_position=baseline_positions[candidate.instrument_id],
            v3_position=v3_positions[candidate.instrument_id],
            baseline_rank_score=round(candidate.baseline_rank_score, 8),
            frozen_factor_score=round(frozen_score, 8),
            calibration_delta=round(calibration, 8),
            turnover_bonus=(V3_INCUMBENT_TURNOVER_BONUS if candidate.incumbent else 0.0),
            v3_score=round(score, 8),
            training_observation_count=training_observation_count,
            training_date_count=training_date_count,
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
        training_cutoff_date=training_cutoff_date,
        training_observation_count=training_observation_count,
        training_date_count=training_date_count,
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
    posteriors: dict[str, _PosteriorEstimate],
) -> _PosteriorEstimate | None:
    segments = _candidate_segments(
        primary_strategy_id=candidate.primary_strategy_id,
        asset_type=candidate.asset_type,
        features=candidate.features,
    )
    strategy = posteriors.get(f"strategy:{candidate.primary_strategy_id or 'unknown'}")
    asset = posteriors.get(f"asset:{candidate.asset_type or 'unknown'}")
    factor_estimates = [
        posteriors[key] for key in segments if key.startswith("factor:") and key in posteriors
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
        (signal_date, sum(values) / len(values)) for signal_date, values in sorted(by_date.items())
    ]
    weights = [
        0.5 ** (max((decision_date - signal_date).days, 0) / V3_RECENCY_HALF_LIFE_DAYS)
        for signal_date, _ in dated_values
    ]
    effective_weight = sum(weights)
    denominator = effective_weight + V3_PRIOR_DATE_STRENGTH
    expected = (
        sum(value * weight for (_, value), weight in zip(dated_values, weights, strict=True))
        / denominator
    )
    variance = (
        sum(
            weight * (value - expected) ** 2
            for (_, value), weight in zip(dated_values, weights, strict=True)
        )
        + V3_PRIOR_DATE_STRENGTH * expected**2
    ) / denominator
    standard_error = math.sqrt(max(variance, 0.0) / denominator)
    wins = sum(
        weight for (_, value), weight in zip(dated_values, weights, strict=True) if value > 0
    )
    win_probability = (wins + 2.0) / (effective_weight + 4.0)
    win_standard_error = math.sqrt(
        max(win_probability * (1.0 - win_probability), 0.0) / (effective_weight + 4.0)
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
            estimate.expected_excess_return_pct * weight for estimate, weight in available
        )
        / total_weight,
        lower_bound_pct=sum(estimate.lower_bound_pct * weight for estimate, weight in available)
        / total_weight,
        win_probability=sum(estimate.win_probability * weight for estimate, weight in available)
        / total_weight,
        win_probability_lower_bound=sum(
            estimate.win_probability_lower_bound * weight for estimate, weight in available
        )
        / total_weight,
    )


def _calibration_delta(evidence: _PosteriorEstimate) -> float:
    alpha_component = _clamp(evidence.expected_excess_return_pct / 4.0, -1.0, 1.0)
    win_component = _clamp((evidence.win_probability - 0.5) * 2.0, -1.0, 1.0)
    raw = V3_MAX_CALIBRATION_DELTA * (alpha_component * 0.70 + win_component * 0.30)
    return _clamp(raw, -V3_MAX_CALIBRATION_DELTA, V3_MAX_CALIBRATION_DELTA)


def _candidate_trigger_probability(
    candidate: RankingV3Candidate,
    probabilities_by_segment: dict[str, float],
) -> float | None:
    keys = [
        f"strategy:{candidate.primary_strategy_id or 'unknown'}",
        f"asset:{candidate.asset_type or 'unknown'}",
    ]
    probabilities = []
    for key in keys:
        probability = probabilities_by_segment.get(key)
        if probability is None:
            continue
        probabilities.append(probability)
    return sum(probabilities) / len(probabilities) if probabilities else None


def _is_valid_resolved_observation(
    observation: ResolvedRankingV3Observation,
) -> bool:
    return (
        observation.outcome_status == "resolved"
        and observation.triggered
        and observation.return_pct is not None
        and math.isfinite(float(observation.return_pct))
        and observation.benchmark_return_pct is not None
        and math.isfinite(float(observation.benchmark_return_pct))
        and observation.net_excess_return_pct is not None
        and math.isfinite(float(observation.net_excess_return_pct))
    )


def _is_valid_posterior_observation(
    observation: ResolvedRankingV3Observation,
) -> bool:
    if _is_valid_resolved_observation(observation):
        return True
    return (
        observation.outcome_status == "not_triggered"
        and not observation.triggered
        and observation.return_pct is not None
        and math.isclose(float(observation.return_pct), 0.0, rel_tol=0.0, abs_tol=1e-12)
        and observation.benchmark_return_pct is not None
        and math.isfinite(float(observation.benchmark_return_pct))
        and observation.net_excess_return_pct is not None
        and math.isfinite(float(observation.net_excess_return_pct))
        and math.isclose(
            float(observation.net_excess_return_pct),
            -float(observation.benchmark_return_pct),
            rel_tol=0.0,
            abs_tol=1e-6,
        )
    )


def _is_valid_trigger_observation(
    observation: ResolvedRankingV3Observation,
) -> bool:
    if _is_valid_resolved_observation(observation):
        return observation.triggered
    return observation.outcome_status == "not_triggered" and not observation.triggered


def _observation_sort_key(
    observation: ResolvedRankingV3Observation,
) -> tuple[date, date, str, str, str, float]:
    return (
        observation.available_at,
        observation.signal_date,
        observation.instrument_id,
        observation.primary_strategy_id or "",
        observation.asset_type,
        float(observation.net_excess_return_pct or 0.0),
    )


def _ranking_v3_artifact_digest(payload: dict[str, object]) -> str:
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
