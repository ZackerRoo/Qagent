from datetime import date, timedelta
import random

import pytest
from pydantic import ValidationError

from qagent.backtesting.ranking_v4 import (
    MIN_V4_TRAINING_OBSERVATIONS,
    RankingV4Candidate,
    RankingV4FeatureVector,
    RankingV4FrozenScoringArtifact,
    ResolvedRankingV4Observation,
    build_ranking_v4_frozen_scoring_artifact,
    realized_utility_block_lower_bound,
    score_ranking_v4_candidates,
)
from qagent.backtesting.ranking_v4_protocol import (
    RANKING_V41_MODEL_VERSION,
    RANKING_V42_MODEL_VERSION,
    RANKING_V43_MODEL_VERSION,
    RANKING_V44_MODEL_VERSION,
)


def _features(**updates) -> RankingV4FeatureVector:
    return RankingV4FeatureVector(
        strategy_score=0.7,
        factor_score=0.7,
        valuation=0.7,
        quality=0.7,
        momentum=0.7,
        trend_quality=0.7,
        breakout_quality=0.7,
        liquidity=0.8,
        low_risk=0.7,
        risk_filter=0.8,
        industry_strength=0.7,
        market_breadth=0.7,
        benchmark_slope=0.7,
        realized_volatility=0.3,
        cross_sectional_dispersion=0.5,
        capacity=0.8,
        tail_risk=0.2,
        data_completeness=1.0,
    ).model_copy(update=updates)


def _candidate(
    instrument_id: str,
    *,
    candidate_date: date = date(2025, 1, 2),
    evidence_date: date = date(2025, 1, 1),
    regime: str = "risk_on",
    strategy: str = "breakout_volume_confirmation",
    replacement_cost: float = 0.0,
    embedded_cost: float | None = None,
    replacement_cost_proven: bool = False,
    opportunity_cost: float = 0.0,
    liquidity_penalty: float = 0.0,
    tail_risk_penalty: float = 0.0,
    features: RankingV4FeatureVector | None = None,
    incumbent: bool = False,
    asset_type: str = "stock",
) -> RankingV4Candidate:
    return RankingV4Candidate(
        instrument_id=instrument_id,
        baseline_rank_score=0.7,
        decision_date=candidate_date,
        feature_as_of=evidence_date,
        market_regime_as_of=evidence_date,
        constraint_as_of=evidence_date,
        cost_as_of=evidence_date,
        primary_strategy_id=strategy,
        market_regime=regime,
        asset_type=asset_type,
        industry="电子",
        features=features or _features(),
        point_in_time_evidence_complete=True,
        market_regime_features_complete=True,
        constraint_data_complete=True,
        constraint_evidence_mode="point_in_time_metadata",
        underlying_evidence_complete=True,
        incumbent=incumbent,
        replacement_cost_pct=replacement_cost,
        stage_two_embedded_cost_pct=embedded_cost,
        replacement_cost_evidence_complete=replacement_cost_proven,
        benchmark_opportunity_cost_pct=opportunity_cost,
        liquidity_penalty_pct=liquidity_penalty,
        tail_risk_penalty_pct=tail_risk_penalty,
    )


def _resolved(
    index: int,
    *,
    available_at: date,
    excess: float,
    triggered: bool = True,
    regime: str = "risk_on",
    strategy: str = "breakout_volume_confirmation",
    asset_type: str = "stock",
) -> ResolvedRankingV4Observation:
    signal_date = date(2023, 1, 2) + timedelta(days=index)
    if triggered:
        return ResolvedRankingV4Observation(
            instrument_id=f"CN:{index:06d}",
            signal_date=signal_date,
            available_at=available_at,
            outcome_status="resolved",
            triggered=True,
            return_pct=excess + 0.5,
            benchmark_return_pct=0.5,
            cost_adjusted_net_excess_return_pct=excess,
            primary_strategy_id=strategy,
            market_regime=regime,
            asset_type=asset_type,
            features=_features(),
        )
    return ResolvedRankingV4Observation(
        instrument_id=f"CN:{index:06d}",
        signal_date=signal_date,
        available_at=available_at,
        outcome_status="not_triggered",
        triggered=False,
        return_pct=None,
        benchmark_return_pct=0.5,
        cost_adjusted_net_excess_return_pct=None,
        primary_strategy_id=strategy,
        market_regime=regime,
        asset_type=asset_type,
        features=_features(),
    )


def _training(
    *,
    excess: float = 4.0,
    triggered_count: int = MIN_V4_TRAINING_OBSERVATIONS,
) -> list[ResolvedRankingV4Observation]:
    return [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=excess,
            triggered=index < triggered_count,
        )
        for index in range(MIN_V4_TRAINING_OBSERVATIONS)
    ]


def test_v4_uses_only_evidence_available_strictly_before_cutoff():
    training = _training()
    future = [
        _resolved(
            index + 300,
            available_at=date(2025, 2, 1),
            excess=-30.0,
        )
        for index in range(100)
    ]
    candidate = _candidate("CN:000001")

    first = score_ranking_v4_candidates(
        [candidate],
        training,
        decision_date=date(2025, 1, 2),
    )
    second = score_ranking_v4_candidates(
        [candidate],
        [*training, *future],
        decision_date=date(2025, 1, 2),
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.candidates[0].eligible_for_position is True


def test_v4_observation_rejects_signal_date_after_available_at():
    with pytest.raises(ValidationError, match="signal_date cannot follow available_at"):
        ResolvedRankingV4Observation(
            instrument_id="CN:000001",
            signal_date=date(2025, 1, 2),
            available_at=date(2025, 1, 1),
            outcome_status="resolved",
            triggered=True,
            return_pct=4.5,
            benchmark_return_pct=0.5,
            cost_adjusted_net_excess_return_pct=4.0,
            primary_strategy_id="breakout_volume_confirmation",
            market_regime="risk_on",
            asset_type="stock",
            features=_features(),
        )


def test_v4_excludes_future_signal_date_even_if_validation_is_bypassed():
    training = _training()
    malformed = training[0].model_copy(
        update={
            "signal_date": date(2025, 6, 1),
            "available_at": date(2024, 1, 1),
            "cost_adjusted_net_excess_return_pct": -100.0,
        }
    )

    expected = build_ranking_v4_frozen_scoring_artifact(
        training,
        cutoff=date(2025, 1, 2),
    )
    actual = build_ranking_v4_frozen_scoring_artifact(
        [*training, malformed],
        cutoff=date(2025, 1, 2),
    )

    assert actual.model_dump(mode="json") == expected.model_dump(mode="json")


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("decision_date", "candidate_decision_date_missing"),
        ("feature_as_of", "feature_evidence_time_missing"),
        ("market_regime_as_of", "market_regime_evidence_time_missing"),
        ("constraint_as_of", "constraint_evidence_time_missing"),
        ("cost_as_of", "cost_evidence_time_missing"),
    ],
)
def test_v4_fails_closed_when_candidate_time_evidence_is_missing(
    field: str,
    reason: str,
):
    candidate = _candidate("CN:000001").model_copy(update={field: None})

    score = score_ranking_v4_candidates(
        [candidate],
        _training(),
        decision_date=date(2025, 1, 2),
    ).candidates[0]

    assert score.eligible_for_position is False
    assert reason in score.blocked_reasons
    if field == "cost_as_of":
        assert score.expected_utility_pct is None
        assert "cost_evidence_incomplete" in score.blocked_reasons


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("feature_as_of", "feature_evidence_after_decision"),
        ("market_regime_as_of", "market_regime_evidence_after_decision"),
        ("constraint_as_of", "constraint_evidence_after_decision"),
        ("cost_as_of", "cost_evidence_after_decision"),
    ],
)
def test_v4_fails_closed_when_candidate_evidence_is_after_decision(
    field: str,
    reason: str,
):
    candidate = _candidate("CN:000001").model_copy(update={field: date(2025, 1, 3)})

    score = score_ranking_v4_candidates(
        [candidate],
        _training(),
        decision_date=date(2025, 1, 2),
    ).candidates[0]

    assert score.eligible_for_position is False
    assert reason in score.blocked_reasons
    if field == "cost_as_of":
        assert score.expected_utility_pct is None
        assert "cost_evidence_incomplete" in score.blocked_reasons


def test_v4_fails_closed_when_candidate_decision_date_does_not_match():
    score = score_ranking_v4_candidates(
        [_candidate("CN:000001", candidate_date=date(2025, 1, 1))],
        _training(),
        decision_date=date(2025, 1, 2),
    ).candidates[0]

    assert score.eligible_for_position is False
    assert "candidate_decision_date_mismatch" in score.blocked_reasons


@pytest.mark.parametrize(
    "field",
    [
        "benchmark_opportunity_cost_pct",
        "liquidity_penalty_pct",
        "tail_risk_penalty_pct",
    ],
)
def test_v4_does_not_treat_missing_cost_evidence_as_zero(field: str):
    candidate = _candidate("CN:000001").model_copy(update={field: None})

    score = score_ranking_v4_candidates(
        [candidate],
        _training(),
        decision_date=date(2025, 1, 2),
    ).candidates[0]

    assert score.eligible_for_position is False
    assert score.expected_utility_pct is None
    assert score.expected_utility_lower_bound_pct is None
    assert "cost_evidence_incomplete" in score.blocked_reasons


def test_v4_does_not_use_zero_costs_without_cost_as_of_evidence():
    candidate = _candidate("CN:000001").model_copy(update={"cost_as_of": None})

    score = score_ranking_v4_candidates(
        [candidate],
        _training(),
        decision_date=date(2025, 1, 2),
    ).candidates[0]

    assert score.eligible_for_position is False
    assert score.expected_utility_pct is None
    assert "cost_evidence_time_missing" in score.blocked_reasons
    assert "cost_evidence_incomplete" in score.blocked_reasons


def test_v4_separates_trigger_probability_from_triggered_net_excess():
    resolved = [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=4.0,
        )
        for index in range(24)
    ]
    frequent_observations = [
        item.model_copy(update={"instrument_id": f"{item.instrument_id}-{copy_index}"})
        for item in resolved
        for copy_index in range(5)
    ]
    rare_observations = [
        (
            item.model_copy(update={"instrument_id": f"{item.instrument_id}-{copy_index}"})
            if copy_index == 0
            else item.model_copy(
                update={
                    "instrument_id": f"{item.instrument_id}-{copy_index}",
                    "outcome_status": "not_triggered",
                    "triggered": False,
                    "return_pct": None,
                    "cost_adjusted_net_excess_return_pct": None,
                }
            )
        )
        for item in resolved
        for copy_index in range(5)
    ]
    frequent = score_ranking_v4_candidates(
        [_candidate("CN:000001", opportunity_cost=0.5)],
        frequent_observations,
        decision_date=date(2025, 1, 2),
    ).candidates[0]
    rare = score_ranking_v4_candidates(
        [_candidate("CN:000001", opportunity_cost=0.5)],
        rare_observations,
        decision_date=date(2025, 1, 2),
    ).candidates[0]

    assert frequent.expected_triggered_net_excess_return_pct == pytest.approx(
        rare.expected_triggered_net_excess_return_pct,
        abs=0.05,
    )
    assert frequent.trigger_probability > rare.trigger_probability
    assert frequent.expected_utility_pct > rare.expected_utility_pct


def test_v41_feature_effects_change_cross_sectional_ranking_without_future_data():
    high_features = _features(
        strategy_score=0.9,
        factor_score=0.9,
        valuation=0.9,
        size=0.9,
        quality=0.9,
        momentum=0.9,
        trend_quality=0.9,
        breakout_quality=0.9,
        low_risk=0.9,
        risk_filter=0.9,
        reversal=0.9,
        industry_strength=0.9,
    )
    low_features = high_features.model_copy(
        update={
            "strategy_score": 0.1,
            "factor_score": 0.1,
            "valuation": 0.1,
            "size": 0.1,
            "quality": 0.1,
            "momentum": 0.1,
            "trend_quality": 0.1,
            "breakout_quality": 0.1,
            "low_risk": 0.1,
            "risk_filter": 0.1,
            "reversal": 0.1,
            "industry_strength": 0.1,
        }
    )
    observations = [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=6.0 if index % 2 == 0 else -2.0,
        ).model_copy(
            update={
                "features": high_features if index % 2 == 0 else low_features,
            }
        )
        for index in range(MIN_V4_TRAINING_OBSERVATIONS)
    ]
    candidates = [
        _candidate("CN:000001", features=high_features),
        _candidate("CN:000002", features=low_features),
    ]

    decision = score_ranking_v4_candidates(
        candidates,
        observations,
        decision_date=date(2025, 1, 2),
    )
    by_id = {item.instrument_id: item for item in decision.candidates}

    assert by_id["CN:000001"].feature_adjustment_count > 0
    assert (
        by_id["CN:000001"].feature_ranking_utility_pct
        > by_id["CN:000002"].feature_ranking_utility_pct
    )
    assert (
        by_id["CN:000001"].expected_utility_lower_bound_pct
        == by_id["CN:000002"].expected_utility_lower_bound_pct
    )
    assert by_id["CN:000001"].net_excess_feature_adjustment_pct > 0
    assert by_id["CN:000002"].net_excess_feature_adjustment_pct < 0
    assert by_id["CN:000002"].net_excess_feature_lower_adjustment_pct < 0
    assert by_id["CN:000002"].eligible_for_position is True


def test_v4_trigger_probability_clusters_cross_section_by_signal_date():
    observations = [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=4.0,
        )
        for index in range(24)
    ]
    duplicated = [
        item.model_copy(update={"instrument_id": f"{item.instrument_id}-{copy_index}"})
        for item in observations
        for copy_index in range(20)
    ]

    original_artifact = build_ranking_v4_frozen_scoring_artifact(
        observations,
        cutoff=date(2025, 1, 2),
    )
    duplicated_artifact = build_ranking_v4_frozen_scoring_artifact(
        duplicated,
        cutoff=date(2025, 1, 2),
    )
    original = {item.segment: item for item in original_artifact.trigger_probabilities}["global"]
    repeated = {item.segment: item for item in duplicated_artifact.trigger_probabilities}["global"]

    assert original.observation_count == 24
    assert repeated.observation_count == 24
    assert repeated.triggered_count == pytest.approx(original.triggered_count)
    assert repeated.probability == pytest.approx(original.probability)
    assert repeated.lower_bound == pytest.approx(original.lower_bound)


def test_v4_trigger_probability_accepts_fractional_cluster_trigger_rates():
    observations = [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=4.0,
            triggered=(index % 2 == 0),
        ).model_copy(update={"signal_date": date(2023, 1, 2)})
        for index in range(MIN_V4_TRAINING_OBSERVATIONS)
    ]

    artifact = build_ranking_v4_frozen_scoring_artifact(
        observations,
        cutoff=date(2025, 1, 2),
    )
    global_probability = {item.segment: item for item in artifact.trigger_probabilities}["global"]

    assert global_probability.observation_count == 1
    assert global_probability.triggered_count == pytest.approx(0.5)


def test_v4_requires_positive_posterior_and_utility_lower_bounds_or_holds_cash():
    decision = score_ranking_v4_candidates(
        [_candidate("CN:000001")],
        _training(excess=-2.0),
        decision_date=date(2025, 1, 2),
    )

    score = decision.candidates[0]
    assert score.eligible_for_position is False
    assert "utility_lower_bound_not_positive" in score.blocked_reasons
    assert decision.cash_slot_count == 5


def test_v43_feature_effects_are_isolated_between_stocks_and_etfs():
    high = _features(momentum=0.9, trend_quality=0.9)
    low = _features(momentum=0.1, trend_quality=0.1)
    observations = [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=6.0 if index % 2 == 0 else -2.0,
            asset_type="stock",
        ).model_copy(
            update={
                "instrument_id": f"CN:STOCK-{index}",
                "features": high if index % 2 == 0 else low,
            }
        )
        for index in range(MIN_V4_TRAINING_OBSERVATIONS)
    ] + [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=-2.0 if index % 2 == 0 else 6.0,
            asset_type="fund",
        ).model_copy(
            update={
                "instrument_id": f"CN:ETF-{index}",
                "features": high if index % 2 == 0 else low,
            }
        )
        for index in range(MIN_V4_TRAINING_OBSERVATIONS)
    ]

    decision = score_ranking_v4_candidates(
        [
            _candidate("CN:STOCK", features=high, asset_type="equity"),
            _candidate("CN:ETF", features=high, asset_type="index-fund"),
        ],
        observations,
        decision_date=date(2025, 1, 2),
        model_version=RANKING_V43_MODEL_VERSION,
    )
    by_id = {item.instrument_id: item for item in decision.candidates}

    assert by_id["CN:STOCK"].net_excess_feature_adjustment_pct > 0
    assert by_id["CN:ETF"].net_excess_feature_adjustment_pct < 0
    assert by_id["CN:STOCK"].feature_adjustment_count > 0
    assert by_id["CN:ETF"].feature_adjustment_count > 0


def test_v43_etf_cannot_borrow_global_stock_utility_evidence():
    score = score_ranking_v4_candidates(
        [_candidate("CN:ETF", asset_type="etf")],
        _training(),
        decision_date=date(2025, 1, 2),
        model_version=RANKING_V43_MODEL_VERSION,
    ).candidates[0]

    assert score.eligible_for_position is False
    assert score.utility_evidence_date_count == 0
    assert "realized_utility_block_evidence_insufficient" in score.blocked_reasons


def test_v43_unknown_asset_type_fails_closed():
    score = score_ranking_v4_candidates(
        [_candidate("CN:UNKNOWN", asset_type="unknown")],
        _training(),
        decision_date=date(2025, 1, 2),
        model_version=RANKING_V43_MODEL_VERSION,
    ).candidates[0]

    assert score.eligible_for_position is False
    assert "asset_type_missing" in score.blocked_reasons


def test_v44_sparse_child_utility_is_partially_pooled_with_asset_evidence():
    parent = [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=4.0,
            strategy="asset_parent",
            regime="risk_on",
        )
        for index in range(MIN_V4_TRAINING_OBSERVATIONS)
    ]
    sparse_child = [
        _resolved(
            index + MIN_V4_TRAINING_OBSERVATIONS,
            available_at=date(2024, 7, 1),
            excess=-0.2,
            strategy="sparse_child",
            regime="risk_off",
        )
        for index in range(6)
    ]

    score = score_ranking_v4_candidates(
        [
            _candidate(
                "CN:SPARSE",
                strategy="sparse_child",
                regime="risk_off",
            )
        ],
        [*parent, *sparse_child],
        decision_date=date(2025, 1, 2),
        model_version=RANKING_V44_MODEL_VERSION,
    ).candidates[0]

    assert score.expected_utility_lower_bound_pct is not None
    assert 0 < score.expected_utility_lower_bound_pct < 4.0
    assert score.eligible_for_position is True
    assert score.utility_evidence_date_count == len(parent) + len(sparse_child)


def test_v44_severe_child_loss_can_still_fail_the_unchanged_positive_lcb_gate():
    parent = [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=4.0,
            strategy="asset_parent",
            regime="risk_on",
        )
        for index in range(MIN_V4_TRAINING_OBSERVATIONS)
    ]
    severe_child = [
        _resolved(
            index + MIN_V4_TRAINING_OBSERVATIONS,
            available_at=date(2024, 7, 1),
            excess=-10.0,
            strategy="severe_child",
            regime="risk_off",
        )
        for index in range(6)
    ]

    score = score_ranking_v4_candidates(
        [
            _candidate(
                "CN:SEVERE",
                strategy="severe_child",
                regime="risk_off",
            )
        ],
        [*parent, *severe_child],
        decision_date=date(2025, 1, 2),
        model_version=RANKING_V44_MODEL_VERSION,
    ).candidates[0]

    assert score.expected_utility_lower_bound_pct is not None
    assert score.expected_utility_lower_bound_pct < 0
    assert score.eligible_for_position is False
    assert "utility_lower_bound_not_positive" in score.blocked_reasons


def test_v44_etf_utility_cannot_borrow_stock_asset_evidence():
    score = score_ranking_v4_candidates(
        [_candidate("CN:ETF", asset_type="etf")],
        _training(),
        decision_date=date(2025, 1, 2),
        model_version=RANKING_V44_MODEL_VERSION,
    ).candidates[0]

    assert score.expected_utility_lower_bound_pct is None
    assert score.utility_evidence_date_count == 0
    assert "realized_utility_block_evidence_insufficient" in score.blocked_reasons


def test_v42_ignores_unproven_fixed_replacement_cost_and_deducts_only_increment():
    observations = _training()
    incumbent = _candidate("CN:000001", incumbent=True, replacement_cost=0.0)
    replacement = _candidate("CN:000002", incumbent=False, replacement_cost=0.2)

    decision = score_ranking_v4_candidates(
        [replacement, incumbent],
        observations,
        decision_date=date(2025, 1, 2),
    )

    assert decision.candidates[0].instrument_id == incumbent.instrument_id
    assert decision.candidates[0].expected_utility_pct == (
        decision.candidates[1].expected_utility_pct
    )
    assert all(item.replacement_cost_pct == 0 for item in decision.candidates)
    proven = score_ranking_v4_candidates(
        [
            incumbent,
            replacement.model_copy(
                update={
                    "stage_two_embedded_cost_pct": 0.15,
                    "replacement_cost_evidence_complete": True,
                }
            ),
        ],
        observations,
        decision_date=date(2025, 1, 2),
    )
    by_id = {item.instrument_id: item for item in proven.candidates}
    assert by_id["CN:000002"].replacement_cost_pct == pytest.approx(0.05)
    assert by_id["CN:000002"].incremental_replacement_cost_proven is True
    assert by_id["CN:000001"].expected_utility_pct - by_id[
        "CN:000002"
    ].expected_utility_pct == pytest.approx(0.05)


def test_v42_proven_cost_already_embedded_in_stage2_is_not_charged_again():
    candidate = _candidate(
        "CN:000001",
        replacement_cost=0.15,
        embedded_cost=0.15,
        replacement_cost_proven=True,
    )

    score = score_ranking_v4_candidates(
        [candidate],
        _training(),
        decision_date=date(2025, 1, 2),
    ).candidates[0]

    assert score.replacement_cost_pct == 0
    assert score.incremental_replacement_cost_proven is True


def test_v42_requires_both_cost_values_when_incremental_cost_is_claimed():
    candidate = _candidate(
        "CN:000001",
        replacement_cost=0.15,
        embedded_cost=None,
        replacement_cost_proven=True,
    )

    score = score_ranking_v4_candidates(
        [candidate],
        _training(),
        decision_date=date(2025, 1, 2),
    ).candidates[0]

    assert score.expected_utility_pct is None
    assert "cost_evidence_incomplete" in score.blocked_reasons


def test_v4_fails_closed_for_unknown_regime_and_incomplete_data():
    decision = score_ranking_v4_candidates(
        [
            _candidate("CN:000001", regime="unknown"),
            _candidate(
                "CN:000002",
                features=_features(data_completeness=0.67),
            ),
        ],
        _training(),
        decision_date=date(2025, 1, 2),
    )

    by_id = {item.instrument_id: item for item in decision.candidates}
    assert "market_regime_missing" in by_id["CN:000001"].blocked_reasons
    assert "data_incomplete" in by_id["CN:000002"].blocked_reasons
    assert decision.cash_slot_count == 5


def test_v4_fails_closed_when_market_regime_feature_evidence_is_incomplete():
    candidate = _candidate("CN:000001").model_copy(
        update={"market_regime_features_complete": False}
    )

    decision = score_ranking_v4_candidates(
        [candidate],
        _training(),
        decision_date=date(2025, 1, 2),
    )

    score = decision.candidates[0]
    assert score.eligible_for_position is False
    assert "market_regime_evidence_incomplete" in score.blocked_reasons
    assert decision.cash_slot_count == 5


def test_v41_rejects_inconsistent_constraint_evidence_state():
    payload = _candidate("CN:000001").model_dump(mode="python")
    payload["constraint_evidence_mode"] = "incomplete"

    with pytest.raises(ValidationError, match="constraint completeness and evidence mode disagree"):
        RankingV4Candidate.model_validate(payload)


def test_v4_artifact_is_hierarchical_stable_and_tamper_evident():
    artifact = build_ranking_v4_frozen_scoring_artifact(
        _training(),
        cutoff=date(2025, 1, 2),
    )
    segments = {item.segment: item for item in artifact.posteriors}

    assert artifact.model_ready is True
    assert segments["global"].parent_segment is None
    assert segments["asset:stock"].parent_segment == "global"
    strategy_key = "strategy:stock:breakout_volume_confirmation"
    regime_key = "regime:stock:breakout_volume_confirmation:risk_on"
    assert segments[strategy_key].parent_segment == "asset:stock"
    assert segments[regime_key].parent_segment == strategy_key
    assert (
        artifact.stable_digest
        == build_ranking_v4_frozen_scoring_artifact(
            list(reversed(_training())),
            cutoff=date(2025, 1, 2),
        ).stable_digest
    )

    payload = artifact.model_dump(mode="python")
    payload["training_observation_count"] += 1
    with pytest.raises(ValidationError, match="digest mismatch"):
        RankingV4FrozenScoringArtifact.model_validate(payload)


def test_v4_does_not_treat_many_same_date_rows_as_independent_dates():
    observations = [
        item.model_copy(update={"signal_date": date(2023, 1, 2)}) for item in _training()
    ]
    decision = score_ranking_v4_candidates(
        [_candidate("CN:000001")],
        observations,
        decision_date=date(2025, 1, 2),
    )

    assert decision.training_observation_count == MIN_V4_TRAINING_OBSERVATIONS
    assert decision.training_date_count == 1
    assert decision.model_ready is False
    assert decision.cash_slot_count == 5


def test_v42_utility_lcb_is_computed_once_from_realized_date_blocks():
    observations = [
        _resolved(
            index,
            available_at=date(2024, 7, 1),
            excess=4.0,
            triggered=index % 2 == 0,
        )
        for index in range(MIN_V4_TRAINING_OBSERVATIONS)
    ]
    candidate = _candidate("CN:000001", opportunity_cost=1.0)

    score = score_ranking_v4_candidates(
        [candidate],
        observations,
        decision_date=date(2025, 1, 2),
        model_version=RANKING_V42_MODEL_VERSION,
    ).candidates[0]
    direct_lower = realized_utility_block_lower_bound(
        [4.0 if index % 2 == 0 else -1.0 for index in range(MIN_V4_TRAINING_OBSERVATIONS)]
    )

    assert direct_lower is not None
    assert score.expected_utility_lower_bound_pct == pytest.approx(
        direct_lower,
        abs=0.0001,
    )
    assert score.utility_evidence_date_count == MIN_V4_TRAINING_OBSERVATIONS
    assert score.utility_evidence_block_count == MIN_V4_TRAINING_OBSERVATIONS // 3


def test_realized_utility_lcb_has_known_dgp_coverage_and_null_is_not_positive():
    generator = random.Random(4201)
    true_mean = 0.3
    covered = 0
    simulations = 300
    for _ in range(simulations):
        values = [generator.gauss(true_mean, 1.0) for _ in range(120)]
        lower = realized_utility_block_lower_bound(values)
        assert lower is not None
        covered += lower <= true_mean

    assert 0.90 <= covered / simulations <= 0.99
    assert realized_utility_block_lower_bound([0.0] * 120) == 0

    null_score = score_ranking_v4_candidates(
        [_candidate("CN:000001")],
        _training(excess=0.0),
        decision_date=date(2025, 1, 2),
    ).candidates[0]
    assert null_score.expected_utility_lower_bound_pct == 0
    assert null_score.eligible_for_position is False
    assert "utility_lower_bound_not_positive" in null_score.blocked_reasons


def test_v41_scoring_preserves_fixed_cost_and_marginal_lcb_gates():
    observations = _training()
    incumbent = _candidate("CN:000001", replacement_cost=0.0)
    replacement = _candidate("CN:000002", replacement_cost=0.2)

    decision = score_ranking_v4_candidates(
        [replacement, incumbent],
        observations,
        decision_date=date(2025, 1, 2),
        model_version="4.1",
    )

    assert decision.model_version == RANKING_V41_MODEL_VERSION
    assert decision.candidates[0].expected_utility_pct - decision.candidates[
        1
    ].expected_utility_pct == pytest.approx(0.2)
    assert decision.candidates[0].utility_evidence_block_count == 0

    negative = score_ranking_v4_candidates(
        [_candidate("CN:000003")],
        _training(excess=-2.0),
        decision_date=date(2025, 1, 2),
        model_version="4.1",
    ).candidates[0]
    assert "net_excess_lower_bound_not_positive" in negative.blocked_reasons
