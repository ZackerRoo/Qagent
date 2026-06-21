from pydantic import BaseModel

from qagent.strategies.models import StrategyEvaluation


class OpportunityRank(BaseModel):
    rank_score: float
    rank_reasons: list[str]


def rank_opportunity(
    primary: StrategyEvaluation | None,
    evaluations: list[StrategyEvaluation],
    strategy_score: float,
    risk_reward: float | None,
) -> OpportunityRank:
    missing = sum(1 for item in evaluations if item.status == "missing_data")
    active = sum(1 for item in evaluations if item.status in {"passed", "watch"})
    total = max(len(evaluations), 1)
    data_completeness = 1 - missing / total
    risk_reward_score = min((risk_reward or 0) / 3, 1.0)
    active_strategy_score = min(active / 4, 1.0)
    computed = (
        strategy_score * 0.55
        + data_completeness * 0.2
        + risk_reward_score * 0.15
        + active_strategy_score * 0.1
    )
    rank_score = round(max(strategy_score, min(computed, 1.0)), 4)
    reasons = []

    if primary:
        if primary.strategy_id == "pead_earnings_drift":
            reasons.append("PEAD earnings drift is the primary strategy")
        else:
            reasons.append(f"{primary.name} is the primary strategy")
    if risk_reward is not None:
        reasons.append(f"Risk/reward is {risk_reward:.2f}")
    if missing:
        reasons.append(f"{missing} registered strategies still need data")
    if active:
        reasons.append(f"{active} strategies are passed or watch")

    return OpportunityRank(rank_score=rank_score, rank_reasons=reasons)
