"""Frozen Top 5 ranking-head challenger evaluated on factor-shadow evidence."""

from __future__ import annotations

import json
from datetime import date
from hashlib import sha256
from typing import Iterable, Literal, Mapping, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from qagent.market.calendars import trading_day_offset
from qagent.storage.factor_research import (
    FactorShadowOutcome,
    FactorShadowRunRef,
    FactorShadowScore,
)


RANKING_HEAD_CHALLENGER_SCHEMA_VERSION = "ranking-head-challenger-evaluation-v1"
RANKING_HEAD_CHALLENGER_PROTOCOL_VERSION = "ranking-head-challenger-top5-shadow-v1"
RANKING_HEAD_CHALLENGER_POLICY_VERSION = "ranking-head-top5-v1"
RANKING_HEAD_CHALLENGER_BASELINE_TOP_N = 10
RANKING_HEAD_CHALLENGER_TOP_N = 5
RANKING_HEAD_CHALLENGER_POLICY_DIGEST = (
    "f462073a5708ba9eb9bb86e6a322dec012b814f5ca4b9eadc11f5089c4c1a93d"
)
RANKING_HEAD_ENTRY_WAIT_SESSIONS = 1


def _policy_payload() -> dict[str, object]:
    return {
        "automatic_promotion": False,
        "baseline_top_n": RANKING_HEAD_CHALLENGER_BASELINE_TOP_N,
        "challenger_top_n": RANKING_HEAD_CHALLENGER_TOP_N,
        "decision_weight": False,
        "outcome_basis": ("existing_factor_shadow_next_open_adjusted_net_excess_return"),
        "paper_ledger_mutated": False,
        "paper_order_effect": "none",
        "policy_version": RANKING_HEAD_CHALLENGER_POLICY_VERSION,
        "production_ranking_effect": "none",
        "protocol_version": RANKING_HEAD_CHALLENGER_PROTOCOL_VERSION,
        "rank_field": "challenger_rank",
        "schema_version": RANKING_HEAD_CHALLENGER_SCHEMA_VERSION,
        "scope": "research_shadow",
        "selection_rule": "ascending_rank_then_instrument_id_no_backfill",
    }


def ranking_head_challenger_policy_digest() -> str:
    return sha256(
        json.dumps(_policy_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class RankingHeadChallengerPolicy(BaseModel):
    """Immutable preregistration; it is not a ranking or execution policy hook."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ranking-head-challenger-evaluation-v1"] = (
        RANKING_HEAD_CHALLENGER_SCHEMA_VERSION
    )
    protocol_version: Literal["ranking-head-challenger-top5-shadow-v1"] = (
        RANKING_HEAD_CHALLENGER_PROTOCOL_VERSION
    )
    policy_version: Literal["ranking-head-top5-v1"] = RANKING_HEAD_CHALLENGER_POLICY_VERSION
    policy_digest: str = RANKING_HEAD_CHALLENGER_POLICY_DIGEST
    baseline_top_n: Literal[10] = RANKING_HEAD_CHALLENGER_BASELINE_TOP_N
    top_n: Literal[5] = RANKING_HEAD_CHALLENGER_TOP_N
    rank_field: Literal["challenger_rank"] = "challenger_rank"
    selection_rule: Literal["ascending_rank_then_instrument_id_no_backfill"] = (
        "ascending_rank_then_instrument_id_no_backfill"
    )
    outcome_basis: Literal["existing_factor_shadow_next_open_adjusted_net_excess_return"] = (
        "existing_factor_shadow_next_open_adjusted_net_excess_return"
    )
    scope: Literal["research_shadow"] = "research_shadow"
    decision_weight: Literal[False] = False
    production_ranking_effect: Literal["none"] = "none"
    paper_order_effect: Literal["none"] = "none"
    paper_ledger_mutated: Literal[False] = False
    automatic_promotion: Literal[False] = False

    @model_validator(mode="after")
    def validate_frozen_digest(self) -> "RankingHeadChallengerPolicy":
        if self.policy_digest != ranking_head_challenger_policy_digest():
            raise ValueError("ranking head challenger policy digest mismatch")
        return self


class RankingHeadSameDayComparison(BaseModel):
    signal_date: date
    maturity_date: date
    status: Literal[
        "waiting_for_maturity",
        "selection_incomplete",
        "outcomes_incomplete",
        "ready",
    ]
    baseline_ids: list[str]
    challenger_ids: list[str]
    common_ids: list[str]
    baseline_only_ids: list[str]
    challenger_only_ids: list[str]
    excluded_rank_6_10_ids: list[str]
    baseline_completed_outcomes: int = 0
    challenger_completed_outcomes: int = 0
    baseline_net_excess_return_pct: float | None = None
    challenger_net_excess_return_pct: float | None = None
    challenger_lift_pct: float | None = None


class RankingHeadChallengerEvaluation(BaseModel):
    policy: RankingHeadChallengerPolicy = Field(default_factory=RankingHeadChallengerPolicy)
    status: Literal["not_started", "collecting", "ready"] = "not_started"
    session_count: int = 0
    matured_session_count: int = 0
    paired_outcome_session_count: int = 0
    baseline_net_excess_return_pct: float | None = None
    challenger_net_excess_return_pct: float | None = None
    challenger_lift_pct: float | None = None
    same_day_comparisons: list[RankingHeadSameDayComparison] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def evaluate_ranking_head_challenger(
    runs: Sequence[FactorShadowRunRef],
    scores_by_run: Mapping[str, Sequence[FactorShadowScore]],
    outcomes_by_key: Mapping[tuple[str, str, int], FactorShadowOutcome],
    *,
    as_of_date: date,
    horizon_sessions: int,
) -> RankingHeadChallengerEvaluation:
    """Compare frozen Top 10 and Top 5 heads on identical shadow signal dates.

    Selection is made only from the scores recorded on the signal date. Future
    outcomes are read solely after their fixed maturity date, and incomplete
    sessions do not contribute partial returns to the aggregate.
    """

    if horizon_sessions <= 0:
        raise ValueError("ranking head challenger horizon must be positive")
    policy = RankingHeadChallengerPolicy()
    comparisons: list[RankingHeadSameDayComparison] = []
    baseline_returns: list[float] = []
    challenger_returns: list[float] = []
    matured_sessions = 0

    for run in sorted(runs, key=lambda item: (item.signal_date, item.scan_job_id)):
        ordered = _ordered_scores(scores_by_run.get(run.scan_job_id, ()))
        baseline = ordered[: policy.baseline_top_n]
        # Slice the preregistered head directly. Never scan ranks 6-10 to fill it.
        challenger = ordered[: policy.top_n]
        baseline_ids = [item.instrument_id for item in baseline]
        challenger_ids = [item.instrument_id for item in challenger]
        challenger_id_set = set(challenger_ids)
        baseline_id_set = set(baseline_ids)
        maturity_date = _maturity_date(run.signal_date, horizon_sessions)
        status: Literal[
            "waiting_for_maturity",
            "selection_incomplete",
            "outcomes_incomplete",
            "ready",
        ]
        baseline_completed = 0
        challenger_completed = 0
        baseline_return = None
        challenger_return = None
        lift = None
        if maturity_date > as_of_date:
            status = "waiting_for_maturity"
        else:
            matured_sessions += 1
            if len(baseline) != policy.baseline_top_n or len(challenger) != policy.top_n:
                status = "selection_incomplete"
            else:
                baseline_outcomes = _outcomes_for(
                    run,
                    baseline,
                    outcomes_by_key,
                    horizon_sessions=horizon_sessions,
                )
                challenger_outcomes = _outcomes_for(
                    run,
                    challenger,
                    outcomes_by_key,
                    horizon_sessions=horizon_sessions,
                )
                baseline_completed = len(baseline_outcomes)
                challenger_completed = len(challenger_outcomes)
                if (
                    baseline_completed != policy.baseline_top_n
                    or challenger_completed != policy.top_n
                ):
                    status = "outcomes_incomplete"
                else:
                    status = "ready"
                    baseline_return = _rounded_mean(
                        item.net_excess_return_pct for item in baseline_outcomes
                    )
                    challenger_return = _rounded_mean(
                        item.net_excess_return_pct for item in challenger_outcomes
                    )
                    assert baseline_return is not None and challenger_return is not None
                    lift = round(challenger_return - baseline_return, 10)
                    baseline_returns.append(baseline_return)
                    challenger_returns.append(challenger_return)
        comparisons.append(
            RankingHeadSameDayComparison(
                signal_date=run.signal_date,
                maturity_date=maturity_date,
                status=status,
                baseline_ids=baseline_ids,
                challenger_ids=challenger_ids,
                common_ids=[item for item in baseline_ids if item in challenger_id_set],
                baseline_only_ids=[item for item in baseline_ids if item not in challenger_id_set],
                challenger_only_ids=[
                    item for item in challenger_ids if item not in baseline_id_set
                ],
                excluded_rank_6_10_ids=baseline_ids[policy.top_n :],
                baseline_completed_outcomes=baseline_completed,
                challenger_completed_outcomes=challenger_completed,
                baseline_net_excess_return_pct=baseline_return,
                challenger_net_excess_return_pct=challenger_return,
                challenger_lift_pct=lift,
            )
        )

    paired = len(baseline_returns)
    status = (
        "not_started"
        if not comparisons
        else "ready"
        if paired == len(comparisons)
        else "collecting"
    )
    baseline_average = _rounded_mean(baseline_returns)
    challenger_average = _rounded_mean(challenger_returns)
    return RankingHeadChallengerEvaluation(
        policy=policy,
        status=status,
        session_count=len(comparisons),
        matured_session_count=matured_sessions,
        paired_outcome_session_count=paired,
        baseline_net_excess_return_pct=baseline_average,
        challenger_net_excess_return_pct=challenger_average,
        challenger_lift_pct=(
            round(challenger_average - baseline_average, 10)
            if baseline_average is not None and challenger_average is not None
            else None
        ),
        same_day_comparisons=comparisons,
        data_health={
            "scope": "research_shadow",
            "decision_weight": "false",
            "production_ranking_effect": "none",
            "paper_order_effect": "none",
            "paper_ledger_mutated": "false",
            "automatic_promotion": "false",
            "future_data_guard": "fixed_signal_selection_then_maturity_date_outcomes",
            "dynamic_weight_from_backtest": "false",
            "ranking_head_challenger_status": status,
            "ranking_head_challenger_policy_digest": policy.policy_digest,
        },
    )


def _ordered_scores(scores: Iterable[FactorShadowScore]) -> list[FactorShadowScore]:
    return sorted(scores, key=lambda item: (item.challenger_rank, item.instrument_id))


def _maturity_date(signal_date: date, horizon_sessions: int) -> date:
    entry_date = trading_day_offset(signal_date, RANKING_HEAD_ENTRY_WAIT_SESSIONS)
    return trading_day_offset(entry_date, horizon_sessions - 1)


def _outcomes_for(
    run: FactorShadowRunRef,
    scores: Sequence[FactorShadowScore],
    outcomes_by_key: Mapping[tuple[str, str, int], FactorShadowOutcome],
    *,
    horizon_sessions: int,
) -> list[FactorShadowOutcome]:
    return [
        outcome
        for score in scores
        if (
            outcome := outcomes_by_key.get((run.scan_job_id, score.instrument_id, horizon_sessions))
        )
        is not None
    ]


def _rounded_mean(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return round(float(np.mean(materialized)), 10) if materialized else None


__all__ = [
    "RANKING_HEAD_CHALLENGER_POLICY_DIGEST",
    "RANKING_HEAD_CHALLENGER_TOP_N",
    "RankingHeadChallengerEvaluation",
    "RankingHeadChallengerPolicy",
    "RankingHeadSameDayComparison",
    "evaluate_ranking_head_challenger",
    "ranking_head_challenger_policy_digest",
]
