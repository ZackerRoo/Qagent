from datetime import date, timedelta
from decimal import Decimal

from qagent.backtesting.baseline_challenger import (
    MIN_BASELINE_TRAINING_SAMPLES,
    BaselineCandidate,
    BaselineCandidateScore,
    ResolvedBaselineObservation,
    score_baseline_candidates,
)
from qagent.backtesting.walk_forward import (
    WalkForwardSelection,
    _select_baseline_challenger_scores,
)


def _observations(
    count: int,
    *,
    decision_date: date,
) -> list[ResolvedBaselineObservation]:
    result = []
    for index in range(count):
        positive = index % 2 == 1
        result.append(
            ResolvedBaselineObservation(
                instrument_id=f"CN:{index:06d}",
                signal_date=decision_date - timedelta(days=60 + index),
                exit_date=decision_date - timedelta(days=1 + index % 20),
                return_pct=4.0 if positive else -3.0,
                benchmark_return_pct=1.0,
                net_excess_return_pct=3.0 if positive else -4.0,
                primary_strategy_id="positive" if positive else "negative",
                factor_signals=["quality" if positive else "overextended"],
                market_regime="risk_on",
                industry="机器人" if positive else "银行",
                asset_type="stock",
                exit_reason="target_1" if positive else "initial_stop",
                holding_days=10,
            )
        )
    return result


def _candidates() -> list[BaselineCandidate]:
    return [
        BaselineCandidate(
            instrument_id="CN:000001",
            baseline_rank_score=0.8,
            primary_strategy_id="positive",
            factor_signals=["quality"],
            market_regime="risk_on",
            industry="机器人",
            asset_type="stock",
        ),
        BaselineCandidate(
            instrument_id="CN:000002",
            baseline_rank_score=0.9,
            primary_strategy_id="negative",
            factor_signals=["overextended"],
            market_regime="risk_on",
            industry="银行",
            asset_type="stock",
        ),
    ]


def test_baseline_challenger_uses_net_excess_and_blocks_negative_segments():
    decision_date = date(2026, 7, 1)

    decision = score_baseline_candidates(
        _candidates(),
        _observations(60, decision_date=decision_date),
        decision_date=decision_date,
    )
    by_instrument = {item.instrument_id: item for item in decision.candidates}

    assert decision.model_ready is True
    assert by_instrument["CN:000001"].selection_eligible is True
    assert by_instrument["CN:000001"].expected_excess_return_pct > 0
    assert by_instrument["CN:000002"].negative_segment is True
    assert by_instrument["CN:000002"].selection_eligible is False
    assert by_instrument["CN:000001"].challenger_position == 1


def test_baseline_challenger_ignores_trades_not_resolved_before_decision():
    decision_date = date(2026, 7, 1)
    historical = _observations(
        MIN_BASELINE_TRAINING_SAMPLES,
        decision_date=decision_date,
    )
    future = [
        item.model_copy(
            update={
                "exit_date": decision_date + timedelta(days=1),
                "net_excess_return_pct": 100.0,
            }
        )
        for item in historical
    ]

    baseline = score_baseline_candidates(
        _candidates(),
        historical,
        decision_date=decision_date,
    )
    with_future = score_baseline_candidates(
        _candidates(),
        [*historical, *future],
        decision_date=decision_date,
    )

    assert with_future.training_sample_count == baseline.training_sample_count
    assert with_future.candidates == baseline.candidates
    assert with_future.training_cutoff_date < decision_date


def test_baseline_challenger_can_hold_cash_when_no_candidate_clears_evidence():
    scores = [
        BaselineCandidateScore(
            instrument_id=f"CN:{index:06d}",
            baseline_position=index + 1,
            challenger_position=index + 1,
            baseline_score=1 - index * 0.1,
            challenger_score=1 - index * 0.1,
            training_sample_count=80,
            strategy_sample_count=20,
            evidence_sample_count=20,
            selection_eligible=False,
        )
        for index in range(5)
    ]
    source = {
        score.instrument_id: _selection(score.instrument_id, index)
        for index, score in enumerate(scores)
    }

    selected, evidence, hysteresis, constrained = _select_baseline_challenger_scores(
        scores,
        source_by_instrument=source,
        previous_instrument_ids=[],
        baseline_instrument_ids=list(source),
        model_ready=True,
        market_entry_allowed=True,
        limit=5,
        strategy_limit=2,
    )

    assert selected == []
    assert evidence == 5
    assert hysteresis == 0
    assert constrained == 0


def test_baseline_challenger_retains_nonnegative_incumbents_without_promoting_them():
    scores = [
        BaselineCandidateScore(
            instrument_id=f"CN:{index:06d}",
            baseline_position=index + 1,
            challenger_position=index + 1,
            baseline_score=1 - index * 0.1,
            challenger_score=1 - index * 0.1,
            training_sample_count=80,
            strategy_sample_count=20,
            evidence_sample_count=20,
            expected_excess_return_pct=-0.10,
            expected_excess_lower_bound_pct=-1.00,
            selection_eligible=False,
        )
        for index in range(5)
    ]
    source = {
        score.instrument_id: _selection(score.instrument_id, index)
        for index, score in enumerate(scores)
    }
    incumbent_ids = list(source)

    selected, evidence, hysteresis, constrained = _select_baseline_challenger_scores(
        scores,
        source_by_instrument=source,
        previous_instrument_ids=incumbent_ids,
        baseline_instrument_ids=incumbent_ids,
        model_ready=True,
        market_entry_allowed=True,
        limit=5,
        strategy_limit=2,
    )

    assert {item.instrument_id for item in selected} == set(incumbent_ids)
    assert evidence == 5
    assert hysteresis == 0
    assert constrained == 0


def test_baseline_challenger_requires_material_edge_before_replacing_incumbent():
    scores = [
        BaselineCandidateScore(
            instrument_id=f"CN:{index:06d}",
            baseline_position=index + 1,
            challenger_position=index + 1,
            baseline_score=1 - index * 0.1,
            challenger_score=(0.61 if index == 5 else 1 - index * 0.1),
            training_sample_count=80,
            strategy_sample_count=20,
            evidence_sample_count=20,
            expected_excess_return_pct=1.0,
            selection_eligible=True,
        )
        for index in range(6)
    ]
    source = {
        score.instrument_id: _selection(score.instrument_id, index)
        for index, score in enumerate(scores)
    }
    baseline_ids = [f"CN:{index:06d}" for index in range(5)]

    selected, evidence, hysteresis, constrained = _select_baseline_challenger_scores(
        scores,
        source_by_instrument=source,
        previous_instrument_ids=baseline_ids,
        baseline_instrument_ids=baseline_ids,
        model_ready=True,
        market_entry_allowed=True,
        limit=5,
        strategy_limit=2,
    )

    assert {item.instrument_id for item in selected} == set(baseline_ids)
    assert evidence == 0
    assert hysteresis == 1
    assert constrained == 0


def _selection(instrument_id: str, index: int) -> WalkForwardSelection:
    return WalkForwardSelection(
        instrument_id=instrument_id,
        status="watch",
        primary_strategy_id=f"strategy-{index % 3}",
        rank_score=Decimal("1"),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        industry=f"industry-{index % 3}",
        asset_type="stock",
    )
