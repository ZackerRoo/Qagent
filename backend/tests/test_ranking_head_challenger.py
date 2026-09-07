from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256

import pytest
from pydantic import ValidationError

from qagent.research.ranking_head_challenger import (
    RANKING_HEAD_CHALLENGER_POLICY_DIGEST,
    RankingHeadChallengerPolicy,
    evaluate_ranking_head_challenger,
    ranking_head_challenger_policy_digest,
)
from qagent.storage.factor_research import (
    FactorShadowOutcome,
    FactorShadowRunRef,
    FactorShadowScore,
)


HORIZON = 5
SIGNAL_DATE = date(2026, 7, 1)


def _run(signal_date: date = SIGNAL_DATE) -> FactorShadowRunRef:
    return FactorShadowRunRef(
        experiment_id="factor-shadow-ranking-head",
        scan_job_id=f"scan-{signal_date.isoformat()}",
        signal_date=signal_date,
        dataset_revision=11,
        model_digest="a" * 64,
        scored_instruments=12,
        created_at=datetime.combine(signal_date, datetime.min.time(), timezone.utc),
    )


def _scores() -> list[FactorShadowScore]:
    return [
        FactorShadowScore(
            instrument_id=f"CN:{rank:06d}",
            baseline_score=float(13 - rank),
            challenger_score=float(13 - rank),
            baseline_rank=rank,
            challenger_rank=rank,
            feature_coverage=1.0,
            industry=f"industry-{rank % 3}",
        )
        for rank in range(1, 13)
    ]


def _outcomes(
    run: FactorShadowRunRef,
    scores: list[FactorShadowScore],
) -> dict[tuple[str, str, int], FactorShadowOutcome]:
    return {
        (run.scan_job_id, score.instrument_id, HORIZON): FactorShadowOutcome(
            experiment_id=run.experiment_id,
            scan_job_id=run.scan_job_id,
            instrument_id=score.instrument_id,
            horizon_sessions=HORIZON,
            signal_date=run.signal_date,
            entry_date=date(2026, 7, 2),
            outcome_date=date(2026, 7, 8),
            benchmark_id="CN:000300.IDX",
            instrument_return_pct=float(11 - score.challenger_rank),
            benchmark_return_pct=0.0,
            excess_return_pct=float(11 - score.challenger_rank),
            net_excess_return_pct=float(11 - score.challenger_rank),
            round_trip_cost_bps=0.0,
            signal_dataset_revision=run.dataset_revision,
            model_digest=run.model_digest,
            source_digest=sha256(score.instrument_id.encode()).hexdigest(),
        )
        for score in scores
    }


def test_policy_is_frozen_and_explicitly_isolated():
    policy = RankingHeadChallengerPolicy()

    assert policy.top_n == 5
    assert policy.baseline_top_n == 10
    assert policy.policy_digest == RANKING_HEAD_CHALLENGER_POLICY_DIGEST
    assert policy.policy_digest == ranking_head_challenger_policy_digest()
    assert policy.scope == "research_shadow"
    assert policy.decision_weight is False
    assert policy.production_ranking_effect == "none"
    assert policy.paper_order_effect == "none"
    assert policy.paper_ledger_mutated is False
    assert policy.automatic_promotion is False
    with pytest.raises(ValidationError):
        RankingHeadChallengerPolicy(policy_digest="0" * 64)
    with pytest.raises(ValidationError):
        RankingHeadChallengerPolicy(top_n=6)


def test_top5_is_deterministic_and_never_backfills_from_ranks_6_to_10():
    run = _run()
    scores = _scores()
    outcomes = _outcomes(run, scores)

    first = evaluate_ranking_head_challenger(
        [run],
        {run.scan_job_id: list(reversed(scores))},
        outcomes,
        as_of_date=date(2026, 7, 8),
        horizon_sessions=HORIZON,
    )
    second = evaluate_ranking_head_challenger(
        [run],
        {run.scan_job_id: scores},
        outcomes,
        as_of_date=date(2026, 7, 8),
        horizon_sessions=HORIZON,
    )

    assert first == second
    comparison = first.same_day_comparisons[0]
    assert comparison.challenger_ids == [f"CN:{rank:06d}" for rank in range(1, 6)]
    assert comparison.baseline_only_ids == [f"CN:{rank:06d}" for rank in range(6, 11)]
    assert comparison.excluded_rank_6_10_ids == comparison.baseline_only_ids
    assert comparison.challenger_only_ids == []
    assert first.baseline_net_excess_return_pct == pytest.approx(5.5)
    assert first.challenger_net_excess_return_pct == pytest.approx(8.0)
    assert first.challenger_lift_pct == pytest.approx(2.5)


def test_unmatured_and_missing_outcomes_fail_closed_without_partial_returns():
    run = _run()
    scores = _scores()
    outcomes = _outcomes(run, scores)

    waiting = evaluate_ranking_head_challenger(
        [run],
        {run.scan_job_id: scores},
        outcomes,
        as_of_date=date(2026, 7, 7),
        horizon_sessions=HORIZON,
    )
    assert waiting.status == "collecting"
    assert waiting.matured_session_count == 0
    assert waiting.same_day_comparisons[0].status == "waiting_for_maturity"
    assert waiting.challenger_net_excess_return_pct is None

    outcomes.pop((run.scan_job_id, "CN:000010", HORIZON))
    incomplete = evaluate_ranking_head_challenger(
        [run],
        {run.scan_job_id: scores},
        outcomes,
        as_of_date=date(2026, 7, 8),
        horizon_sessions=HORIZON,
    )
    comparison = incomplete.same_day_comparisons[0]
    assert comparison.status == "outcomes_incomplete"
    assert comparison.baseline_completed_outcomes == 9
    assert comparison.challenger_completed_outcomes == 5
    assert comparison.baseline_net_excess_return_pct is None
    assert comparison.challenger_net_excess_return_pct is None
    assert incomplete.paired_outcome_session_count == 0
    assert incomplete.data_health == {
        "scope": "research_shadow",
        "decision_weight": "false",
        "production_ranking_effect": "none",
        "paper_order_effect": "none",
        "paper_ledger_mutated": "false",
        "automatic_promotion": "false",
        "future_data_guard": "fixed_signal_selection_then_maturity_date_outcomes",
        "dynamic_weight_from_backtest": "false",
        "ranking_head_challenger_status": "collecting",
        "ranking_head_challenger_policy_digest": RANKING_HEAD_CHALLENGER_POLICY_DIGEST,
    }


def test_insufficient_selection_does_not_borrow_ranked_names_or_report_returns():
    run = _run()
    scores = _scores()[:9]
    evaluation = evaluate_ranking_head_challenger(
        [run],
        {run.scan_job_id: scores},
        _outcomes(run, scores),
        as_of_date=date(2026, 7, 8),
        horizon_sessions=HORIZON,
    )

    comparison = evaluation.same_day_comparisons[0]
    assert comparison.status == "selection_incomplete"
    assert comparison.challenger_ids == [f"CN:{rank:06d}" for rank in range(1, 6)]
    assert comparison.baseline_net_excess_return_pct is None
    assert comparison.challenger_net_excess_return_pct is None
