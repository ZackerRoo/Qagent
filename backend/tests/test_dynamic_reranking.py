from datetime import date, timedelta
from decimal import Decimal

from qagent.backtesting.reranking import (
    MIN_RERANK_TRAINING_SAMPLES,
    RerankCandidate,
    RerankCandidateScore,
    ResolvedRerankObservation,
    rerank_candidates,
)
from qagent.backtesting.walk_forward import (
    WalkForwardGateCriterion,
    WalkForwardSelection,
    _dynamic_rerank_gate_outcome,
    _select_constrained_dynamic_scores,
)


def _candidates() -> list[RerankCandidate]:
    return [
        RerankCandidate(
            instrument_id=f"CN:{index:06d}",
            baseline_rank_score=1 - index * 0.01,
            primary_strategy_id="weak" if index == 0 else "strong",
            factor_signals=["weak_factor"] if index == 0 else ["strong_factor"],
        )
        for index in range(10)
    ]


def _observations(
    count: int,
    *,
    decision_date: date,
) -> list[ResolvedRerankObservation]:
    observations = []
    for index in range(count):
        weak = index % 2 == 0
        observations.append(
            ResolvedRerankObservation(
                instrument_id=f"CN:{index:06d}",
                signal_date=decision_date - timedelta(days=40 + index),
                exit_date=decision_date - timedelta(days=1 + index % 10),
                return_pct=-6.0 if weak else 5.0,
                primary_strategy_id="weak" if weak else "strong",
                factor_signals=["weak_factor" if weak else "strong_factor"],
            )
        )
    return observations


def test_dynamic_reranker_keeps_baseline_until_training_history_is_mature():
    decision_date = date(2026, 7, 1)
    result = rerank_candidates(
        _candidates(),
        _observations(
            MIN_RERANK_TRAINING_SAMPLES - 1,
            decision_date=decision_date,
        ),
        decision_date=decision_date,
    )

    assert result.model_ready is False
    assert [item.instrument_id for item in result.candidates] == [
        item.instrument_id for item in _candidates()
    ]
    assert all("保持原排序" in item.reason for item in result.candidates)


def test_dynamic_reranker_promotes_positive_resolved_evidence():
    decision_date = date(2026, 7, 1)
    result = rerank_candidates(
        _candidates(),
        _observations(40, decision_date=decision_date),
        decision_date=decision_date,
    )
    positions = {item.instrument_id: item.rerank_position for item in result.candidates}

    assert result.model_ready is True
    assert positions["CN:000001"] < positions["CN:000000"]
    assert (
        next(
            item for item in result.candidates if item.instrument_id == "CN:000001"
        ).expected_return_pct
        > 0
    )
    assert (
        next(
            item for item in result.candidates if item.instrument_id == "CN:000000"
        ).expected_return_pct
        < 0
    )


def test_dynamic_reranker_ignores_outcomes_not_resolved_before_decision():
    decision_date = date(2026, 7, 1)
    historical = _observations(40, decision_date=decision_date)
    future = [
        ResolvedRerankObservation(
            instrument_id="CN:000000",
            signal_date=decision_date - timedelta(days=5),
            exit_date=decision_date + timedelta(days=1),
            return_pct=100.0,
            primary_strategy_id="weak",
            factor_signals=["weak_factor"],
        )
        for _ in range(100)
    ]

    baseline = rerank_candidates(
        _candidates(),
        historical,
        decision_date=decision_date,
    )
    with_future = rerank_candidates(
        _candidates(),
        [*historical, *future],
        decision_date=decision_date,
    )

    assert with_future.training_sample_count == baseline.training_sample_count
    assert with_future.candidates == baseline.candidates
    assert with_future.training_cutoff_date < decision_date


def test_dynamic_top_five_enforces_industry_and_etf_overlap_limits():
    scores = [
        RerankCandidateScore(
            instrument_id=f"CN:{index:06d}",
            baseline_position=index + 1,
            rerank_position=index + 1,
            baseline_score=1 - index * 0.1,
            rerank_score=1 - index * 0.1,
            training_sample_count=40,
            strategy_sample_count=20,
            factor_sample_count=20,
            reason="fixture",
        )
        for index in range(7)
    ]
    industries = ["半导体", "半导体", "半导体", "机器人", None, None, "医药"]
    asset_types = ["stock", "stock", "stock", "stock", "etf", "etf", "stock"]
    memberships = [[], [], [], [], ["CN:000688.IDX"], ["CN:000688.IDX"], []]
    source = {
        score.instrument_id: WalkForwardSelection(
            instrument_id=score.instrument_id,
            status="watch",
            primary_strategy_id="strategy",
            rank_score=Decimal(str(score.baseline_score)),
            trigger_price=Decimal("10"),
            initial_stop=Decimal("9"),
            target_1=Decimal("12"),
            asset_type=asset_types[index],
            industry=industries[index],
            index_memberships=memberships[index],
        )
        for index, score in enumerate(scores)
    }

    selected, blocked, evidence_blocked, hysteresis_blocked = _select_constrained_dynamic_scores(
        scores,
        source_by_instrument=source,
        limit=5,
    )

    assert [item.instrument_id for item in selected] == [
        "CN:000000",
        "CN:000001",
        "CN:000003",
        "CN:000004",
        "CN:000006",
    ]
    assert blocked == 2
    assert evidence_blocked == 0
    assert hysteresis_blocked == 0


def test_dynamic_top_five_requires_evidence_and_material_margin_to_replace_baseline():
    scores = [
        RerankCandidateScore(
            instrument_id=f"CN:{index:06d}",
            baseline_position=index + 1,
            rerank_position=index + 1,
            baseline_score=1 - index * 0.1,
            rerank_score=(0.90 if index == 5 else 1 - index * 0.1),
            training_sample_count=80,
            strategy_sample_count=30,
            factor_sample_count=30,
            promotion_eligible=index == 5,
            reason="fixture",
        )
        for index in range(6)
    ]
    source = {
        score.instrument_id: WalkForwardSelection(
            instrument_id=score.instrument_id,
            status="watch",
            primary_strategy_id=f"strategy-{index % 3}",
            rank_score=Decimal(str(score.baseline_score)),
            trigger_price=Decimal("10"),
            initial_stop=Decimal("9"),
            target_1=Decimal("12"),
            industry=f"industry-{index % 3}",
        )
        for index, score in enumerate(scores)
    }

    selected, blocked, evidence_blocked, hysteresis_blocked = _select_constrained_dynamic_scores(
        scores,
        source_by_instrument=source,
        baseline_instrument_ids=[f"CN:{index:06d}" for index in range(5)],
        strategy_limit=2,
        limit=5,
    )

    assert {item.instrument_id for item in selected} == {
        "CN:000000",
        "CN:000001",
        "CN:000002",
        "CN:000003",
        "CN:000005",
    }
    assert blocked == 0
    assert evidence_blocked == 0
    assert hysteresis_blocked == 0


def test_dynamic_top_five_keeps_baseline_when_challenger_edge_is_too_small():
    scores = [
        RerankCandidateScore(
            instrument_id=f"CN:{index:06d}",
            baseline_position=index + 1,
            rerank_position=index + 1,
            baseline_score=1 - index * 0.1,
            rerank_score=(0.52 if index == 5 else 1 - index * 0.1),
            training_sample_count=80,
            strategy_sample_count=30,
            factor_sample_count=30,
            promotion_eligible=index == 5,
            reason="fixture",
        )
        for index in range(6)
    ]
    source = {
        score.instrument_id: WalkForwardSelection(
            instrument_id=score.instrument_id,
            status="watch",
            primary_strategy_id=f"strategy-{index}",
            rank_score=Decimal(str(score.baseline_score)),
            trigger_price=Decimal("10"),
            initial_stop=Decimal("9"),
            target_1=Decimal("12"),
        )
        for index, score in enumerate(scores)
    }

    selected, blocked, evidence_blocked, hysteresis_blocked = _select_constrained_dynamic_scores(
        scores,
        source_by_instrument=source,
        baseline_instrument_ids=[f"CN:{index:06d}" for index in range(5)],
        strategy_limit=2,
        limit=5,
    )

    assert [item.instrument_id for item in selected] == [f"CN:{index:06d}" for index in range(5)]
    assert blocked == 0
    assert evidence_blocked == 0
    assert hysteresis_blocked == 1


def test_dynamic_gate_rejects_known_failure_even_with_incomplete_evidence():
    status, headline = _dynamic_rerank_gate_outcome(
        [
            WalkForwardGateCriterion(
                key="index_membership_evidence",
                label="历史指数成分完整性",
                status="insufficient",
                value="84 个不完整快照",
                requirement="0 个不完整快照",
            ),
            WalkForwardGateCriterion(
                key="baseline_total_return",
                label="全期相对基线",
                status="fail",
                value="-10.69%",
                requirement="> 0%",
            ),
        ]
    )

    assert status == "rejected"
    assert "仍有证据缺口" in headline
    assert "不进入模拟盘" in headline
