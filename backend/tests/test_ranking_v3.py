from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from qagent.backtesting.ranking_v3 import (
    MIN_V3_TRAINING_DATES,
    MIN_V3_TRAINING_OBSERVATIONS,
    RankingV3Candidate,
    RankingV3FeatureVector,
    RankingV3FrozenScoringArtifact,
    ResolvedRankingV3Observation,
    build_ranking_v3_frozen_scoring_artifact,
    frozen_feature_score,
    score_ranking_v3_candidates,
    score_ranking_v3_candidates_from_artifact,
)


def _features(**updates) -> RankingV3FeatureVector:
    return RankingV3FeatureVector(
        strategy_score=0.6,
        factor_score=0.6,
        valuation=0.6,
        size=0.5,
        quality=0.6,
        momentum=0.6,
        trend_quality=0.6,
        liquidity=0.6,
        low_risk=0.6,
        risk_filter=0.6,
        reversal=0.4,
        execution_penalty=0.0,
        data_completeness=1.0,
    ).model_copy(update=updates)


def _candidate(
    instrument_id: str,
    *,
    asset_type: str = "stock",
    strategy: str = "breakout_volume_confirmation",
    incumbent: bool = False,
    features: RankingV3FeatureVector | None = None,
) -> RankingV3Candidate:
    return RankingV3Candidate(
        instrument_id=instrument_id,
        baseline_rank_score=0.7,
        primary_strategy_id=strategy,
        factor_signals=[],
        market_regime="risk_on",
        asset_type=asset_type,
        features=features or _features(),
        incumbent=incumbent,
    )


def _observation(
    index: int,
    *,
    available_at: date,
    excess: float,
    strategy: str = "breakout_volume_confirmation",
    asset_type: str = "stock",
) -> ResolvedRankingV3Observation:
    signal_date = date(2024, 1, 2) + timedelta(days=index)
    return ResolvedRankingV3Observation(
        instrument_id=f"CN:{index:06d}",
        signal_date=signal_date,
        available_at=available_at,
        outcome_status="resolved",
        triggered=True,
        return_pct=excess,
        benchmark_return_pct=0.0,
        net_excess_return_pct=excess,
        primary_strategy_id=strategy,
        asset_type=asset_type,
        features=_features(),
    )


def test_frozen_feature_score_uses_separate_stock_and_etf_formulas():
    features = _features(
        valuation=0.0,
        quality=0.0,
        momentum=1.0,
        trend_quality=1.0,
        liquidity=1.0,
        low_risk=1.0,
        risk_filter=0.0,
    )

    stock = frozen_feature_score(features, asset_type="stock")
    etf = frozen_feature_score(features, asset_type="ETF")

    assert stock == 0.67
    assert etf == 1.0


def test_v3_falls_back_to_frozen_score_before_independent_sample_gate():
    candidates = [
        _candidate("CN:000001", features=_features(trend_quality=0.9)),
        _candidate("CN:000002", features=_features(trend_quality=0.2)),
    ]
    observations = [
        _observation(
            index,
            available_at=date(2024, 6, 1),
            excess=-5.0,
        )
        for index in range(MIN_V3_TRAINING_OBSERVATIONS - 1)
    ]

    decision = score_ranking_v3_candidates(
        candidates,
        observations,
        decision_date=date(2025, 1, 2),
    )

    assert decision.model_ready is False
    assert all(item.calibration_delta == 0 for item in decision.candidates)
    assert decision.candidates[0].instrument_id == "CN:000001"


def test_v3_uses_only_outcomes_available_before_decision_date():
    observations = []
    for index in range(MIN_V3_TRAINING_OBSERVATIONS):
        observations.append(
            _observation(
                index,
                available_at=date(2024, 6, 1),
                excess=2.0,
            )
        )
    future = [
        _observation(
            index + 1000,
            available_at=date(2025, 2, 1),
            excess=-20.0,
        )
        for index in range(100)
    ]
    candidate = _candidate("CN:000001")

    first = score_ranking_v3_candidates(
        [candidate],
        observations,
        decision_date=date(2025, 1, 2),
    )
    second = score_ranking_v3_candidates(
        [candidate],
        [*observations, *future],
        decision_date=date(2025, 1, 2),
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.candidates[0].calibration_delta > 0


def test_v3_freezes_calibration_inside_common_historical_audit_window():
    training = [
        _observation(
            index,
            available_at=date(2024, 6, 1),
            excess=2.0,
        )
        for index in range(MIN_V3_TRAINING_OBSERVATIONS)
    ]
    audit_outcomes = [
        _observation(
            index + 1000,
            available_at=date(2025, 2, 1),
            excess=-20.0,
        )
        for index in range(100)
    ]
    candidate = _candidate("CN:000001")

    before = score_ranking_v3_candidates(
        [candidate],
        training,
        decision_date=date(2025, 6, 2),
        evidence_cutoff_date=date(2024, 7, 29),
    )
    after = score_ranking_v3_candidates(
        [candidate],
        [*training, *audit_outcomes],
        decision_date=date(2025, 6, 2),
        evidence_cutoff_date=date(2024, 7, 29),
    )

    assert before.model_dump(mode="json") == after.model_dump(mode="json")


def test_v3_counts_rebalance_dates_instead_of_overlapping_stock_rows():
    observations = []
    for index in range(MIN_V3_TRAINING_OBSERVATIONS):
        item = _observation(
            index,
            available_at=date(2024, 6, 1),
            excess=2.0,
        )
        observations.append(item.model_copy(update={"signal_date": date(2024, 1, 2)}))

    decision = score_ranking_v3_candidates(
        [_candidate("CN:000001")],
        observations,
        decision_date=date(2025, 1, 2),
    )

    assert decision.training_observation_count == MIN_V3_TRAINING_OBSERVATIONS
    assert decision.training_date_count == 1
    assert decision.model_ready is False


def test_v3_calibration_is_bounded_and_incumbent_bonus_reduces_turnover():
    observations = [
        _observation(
            index,
            available_at=date(2024, 8, 1),
            excess=20.0,
        )
        for index in range(max(MIN_V3_TRAINING_OBSERVATIONS, MIN_V3_TRAINING_DATES))
    ]
    candidates = [
        _candidate("CN:000001", incumbent=True),
        _candidate("CN:000002"),
    ]

    decision = score_ranking_v3_candidates(
        candidates,
        observations,
        decision_date=date(2025, 1, 2),
    )

    assert decision.model_ready is True
    assert all(abs(item.calibration_delta) <= 0.05 for item in decision.candidates)
    assert decision.candidates[0].instrument_id == "CN:000001"
    assert decision.candidates[0].turnover_bonus > 0


def test_frozen_artifact_contains_stable_point_in_time_parameters():
    observations = [
        _observation(
            index,
            available_at=date(2024, 8, 1),
            excess=2.0 if index % 3 else -1.0,
            strategy="two_stage_trend_momentum",
        ).model_copy(
            update={
                "features": _features(momentum=0.8, valuation=0.2),
            }
        )
        for index in range(MIN_V3_TRAINING_OBSERVATIONS)
    ]
    not_triggered = _observation(
        999,
        available_at=date(2024, 8, 2),
        excess=0.0,
        strategy="two_stage_trend_momentum",
    ).model_copy(
        update={
            "outcome_status": "not_triggered",
            "triggered": False,
            "return_pct": None,
            "benchmark_return_pct": None,
            "net_excess_return_pct": None,
        }
    )
    invalid_status = _observation(
        1000,
        available_at=date(2024, 8, 2),
        excess=999.0,
        strategy="two_stage_trend_momentum",
    ).model_copy(update={"outcome_status": "invalid_plan"})
    nonfinite = _observation(
        1001,
        available_at=date(2024, 8, 2),
        excess=float("nan"),
        strategy="two_stage_trend_momentum",
    )
    ambiguous_not_triggered = _observation(
        1002,
        available_at=date(2024, 8, 2),
        excess=0.0,
        strategy="two_stage_trend_momentum",
    ).model_copy(
        update={
            "outcome_status": "not_triggered_or_unfillable",
            "triggered": False,
            "net_excess_return_pct": None,
        }
    )

    artifact = build_ranking_v3_frozen_scoring_artifact(
        [
            invalid_status,
            nonfinite,
            ambiguous_not_triggered,
            not_triggered,
            *reversed(observations),
        ],
        cutoff=date(2025, 1, 2),
    )
    rebuilt = build_ranking_v3_frozen_scoring_artifact(
        [*observations, not_triggered],
        cutoff=date(2025, 1, 2),
    )

    assert artifact.model_dump(mode="json") == rebuilt.model_dump(mode="json")
    assert artifact.training_observation_count == MIN_V3_TRAINING_OBSERVATIONS
    assert artifact.training_date_count == MIN_V3_TRAINING_OBSERVATIONS
    assert len(artifact.stable_digest) == 64
    assert {item.segment for item in artifact.segment_posteriors} >= {
        "strategy:two_stage_trend_momentum",
        "asset:stock",
        "factor:momentum:high",
        "factor:valuation:low",
    }
    trigger = {item.segment: item for item in artifact.trigger_probabilities}[
        "strategy:two_stage_trend_momentum"
    ]
    assert trigger.observation_count == MIN_V3_TRAINING_OBSERVATIONS + 1
    assert trigger.triggered_count == MIN_V3_TRAINING_OBSERVATIONS
    assert trigger.probability == pytest.approx(122 / 125)
    assert (
        RankingV3FrozenScoringArtifact.model_validate(artifact.model_dump(mode="json")) == artifact
    )
    with pytest.raises(ValidationError):
        artifact.cutoff = date(2026, 1, 1)


def test_not_triggered_cash_outcome_enters_posterior_with_benchmark_opportunity_cost():
    resolved = [
        _observation(
            index,
            available_at=date(2024, 8, 1),
            excess=1.0,
            strategy="two_stage_trend_momentum",
        )
        for index in range(MIN_V3_TRAINING_OBSERVATIONS)
    ]
    cash = _observation(
        999,
        available_at=date(2024, 8, 2),
        excess=-2.0,
        strategy="two_stage_trend_momentum",
    ).model_copy(
        update={
            "outcome_status": "not_triggered",
            "triggered": False,
            "return_pct": 0.0,
            "benchmark_return_pct": 2.0,
            "net_excess_return_pct": -2.0,
        }
    )

    without_cash = build_ranking_v3_frozen_scoring_artifact(
        resolved,
        cutoff=date(2025, 1, 2),
    )
    with_cash = build_ranking_v3_frozen_scoring_artifact(
        [*resolved, cash],
        cutoff=date(2025, 1, 2),
    )

    assert with_cash.training_observation_count == MIN_V3_TRAINING_OBSERVATIONS + 1
    assert with_cash.training_date_count == MIN_V3_TRAINING_OBSERVATIONS + 1
    without_posterior = {item.segment: item for item in without_cash.segment_posteriors}[
        "strategy:two_stage_trend_momentum"
    ]
    with_posterior = {item.segment: item for item in with_cash.segment_posteriors}[
        "strategy:two_stage_trend_momentum"
    ]
    assert with_posterior.date_count == without_posterior.date_count + 1
    assert (
        with_posterior.expected_excess_return_pct
        < without_posterior.expected_excess_return_pct
    )


def test_artifact_scoring_exactly_matches_observation_scoring():
    observations = [
        _observation(
            index,
            available_at=date(2024, 8, 1),
            excess=3.0 if index % 4 else -2.0,
        )
        for index in range(MIN_V3_TRAINING_OBSERVATIONS)
    ]
    observations.extend(
        [
            _observation(
                2000,
                available_at=date(2024, 8, 2),
                excess=0.0,
            ).model_copy(
                update={
                    "outcome_status": "not_triggered",
                    "triggered": False,
                    "return_pct": None,
                    "benchmark_return_pct": None,
                    "net_excess_return_pct": None,
                }
            ),
            _observation(
                2001,
                available_at=date(2024, 8, 2),
                excess=0.0,
            ).model_copy(
                update={
                    "outcome_status": "not_triggered_or_unfillable",
                    "triggered": False,
                    "return_pct": None,
                    "benchmark_return_pct": None,
                    "net_excess_return_pct": None,
                }
            ),
        ]
    )
    candidates = [
        _candidate(
            "CN:000001",
            incumbent=True,
            features=_features(momentum=0.9, trend_quality=0.8),
        ),
        _candidate(
            "CN:000002",
            asset_type="ETF",
            features=_features(momentum=0.7, trend_quality=0.7),
        ),
    ]
    cutoff = date(2025, 1, 2)
    artifact = build_ranking_v3_frozen_scoring_artifact(
        observations,
        cutoff=cutoff,
    )

    direct = score_ranking_v3_candidates(
        candidates,
        observations,
        decision_date=cutoff,
    )
    frozen = score_ranking_v3_candidates_from_artifact(
        candidates,
        artifact,
        decision_date=cutoff,
    )

    assert direct.model_dump(mode="json") == frozen.model_dump(mode="json")


def test_observations_at_or_after_cutoff_do_not_change_artifact():
    cutoff = date(2025, 1, 2)
    training = [
        _observation(
            index,
            available_at=date(2024, 8, 1),
            excess=2.0,
        )
        for index in range(MIN_V3_TRAINING_OBSERVATIONS)
    ]
    future = [
        _observation(
            index + 1000,
            available_at=cutoff if index % 2 else date(2025, 2, 1),
            excess=-99.0,
        )
        for index in range(40)
    ]

    before = build_ranking_v3_frozen_scoring_artifact(
        training,
        cutoff=cutoff,
    )
    after = build_ranking_v3_frozen_scoring_artifact(
        [*training, *future],
        cutoff=cutoff,
    )

    assert before.model_dump(mode="json") == after.model_dump(mode="json")


def test_artifact_rejects_tampered_digest_and_pre_cutoff_decision():
    artifact = build_ranking_v3_frozen_scoring_artifact(
        [],
        cutoff=date(2025, 1, 2),
    )

    with pytest.raises(ValidationError, match="digest mismatch"):
        RankingV3FrozenScoringArtifact.model_validate(
            {
                **artifact.model_dump(mode="json"),
                "stable_digest": "0" * 64,
            }
        )
    with pytest.raises(ValueError, match="earlier than artifact cutoff"):
        score_ranking_v3_candidates_from_artifact(
            [_candidate("CN:000001")],
            artifact,
            decision_date=date(2025, 1, 1),
        )
