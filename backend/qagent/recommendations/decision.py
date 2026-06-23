from decimal import Decimal

from qagent.domain.models import DecisionComponents, OpportunityCard, OpportunityDecision


ACTION_LABELS = {
    "candidate_entry": "Candidate entry",
    "watch_trigger": "Watch trigger",
    "wait_pullback": "Wait for pullback",
    "avoid": "Avoid for now",
}


def build_research_decision(card: OpportunityCard) -> OpportunityDecision:
    components = DecisionComponents(
        strategy_quality=_clamp(card.strategy_score),
        risk_reward=_risk_reward_component(card.risk_reward),
        data_quality=_data_quality_component(card),
        execution_quality=_execution_quality_component(card),
        catalyst_support=_catalyst_support_component(card),
    )
    conviction = _conviction_score(components)
    action = _action(card, components, conviction)
    risk_pct = _suggested_risk_pct(action, conviction, components.data_quality)
    return OpportunityDecision(
        action=action,
        action_label=ACTION_LABELS[action],
        conviction_score=conviction,
        components=components,
        suggested_risk_pct=risk_pct,
        max_position_pct=round(risk_pct * 4, 2),
        trigger_price=card.entry_plan.trigger_price,
        initial_stop=card.exit_plan.initial_stop,
        target_1=card.exit_plan.target_1,
        no_chase_above=card.entry_plan.no_chase_above,
        horizon=_horizon(card),
        rationale=_rationale(card, components, conviction),
        failure_conditions=_failure_conditions(card),
        verification_checks=_verification_checks(card),
    )


def _conviction_score(components: DecisionComponents) -> float:
    score = (
        components.strategy_quality * 0.35
        + components.risk_reward * 0.2
        + components.data_quality * 0.2
        + components.execution_quality * 0.15
        + components.catalyst_support * 0.1
    )
    return round(_clamp(score), 4)


def _action(
    card: OpportunityCard,
    components: DecisionComponents,
    conviction: float,
) -> str:
    if conviction < 0.45 or components.risk_reward < 0.35 or components.data_quality < 0.35:
        return "avoid"
    if components.execution_quality < 0.45 or card.scenario.no_chase_pct < 2:
        return "wait_pullback"
    if conviction >= 0.72 and card.status.value == "setup_ready":
        return "candidate_entry"
    return "watch_trigger"


def _suggested_risk_pct(action: str, conviction: float, data_quality: float) -> float:
    if action == "avoid":
        return 0.0
    base = 0.4 + conviction * 0.9
    if action == "watch_trigger":
        base *= 0.65
    if action == "wait_pullback":
        base *= 0.45
    return round(min(base * data_quality, 1.5), 2)


def _risk_reward_component(risk_reward: float | None) -> float:
    if risk_reward is None:
        return 0.0
    return round(_clamp(risk_reward / 3), 4)


def _data_quality_component(card: OpportunityCard) -> float:
    total = max(len(card.strategy_evaluations), 1)
    missing = sum(1 for item in card.strategy_evaluations if item.status == "missing_data")
    active = sum(1 for item in card.strategy_evaluations if item.status in {"passed", "watch"})
    caveat_penalty = min(len(card.data_caveats) * 0.04, 0.2)
    active_support = min(active / 4, 1.0) * 0.45
    missing_penalty = min(missing / total, 1.0) * 0.25
    return round(_clamp(0.45 + active_support - missing_penalty - caveat_penalty), 4)


def _execution_quality_component(card: OpportunityCard) -> float:
    score = 0.0
    if card.entry_plan.trigger_price is not None:
        score += 0.3
    if card.exit_plan.initial_stop is not None:
        score += 0.25
    if card.exit_plan.target_1 is not None:
        score += 0.25
    if card.entry_plan.no_chase_above is not None and card.scenario.no_chase_pct >= 2:
        score += 0.2
    return round(_clamp(score), 4)


def _catalyst_support_component(card: OpportunityCard) -> float:
    if card.primary_strategy_id in {"pead_earnings_drift", "catalyst_financial_transmission"}:
        return 0.8
    if any("earnings" in item.family for item in card.strategy_evaluations):
        return 0.55
    return 0.35


def _horizon(card: OpportunityCard) -> str:
    primary = next(
        (
            evaluation
            for evaluation in card.strategy_evaluations
            if evaluation.strategy_id == card.primary_strategy_id
        ),
        None,
    )
    if primary is None:
        return "swing"
    if "quarter" in primary.horizon.lower() or "multi" in primary.horizon.lower():
        return "position"
    return "swing"


def _rationale(
    card: OpportunityCard,
    components: DecisionComponents,
    conviction: float,
) -> list[str]:
    reasons = [
        f"Conviction score is {conviction:.2f} from strategy, risk/reward, data quality, and execution quality.",
    ]
    if card.primary_strategy_id:
        reasons.append(f"Primary strategy: {card.primary_strategy_id}.")
    if card.risk_reward is not None:
        reasons.append(f"Risk/reward is {card.risk_reward:.2f}.")
    if components.data_quality < 0.7:
        reasons.append("Data quality is reduced by missing strategy inputs or caveats.")
    if card.rank_reasons:
        reasons.extend(card.rank_reasons[:2])
    return _dedupe(reasons)


def _failure_conditions(card: OpportunityCard) -> list[str]:
    conditions = []
    if card.exit_plan.initial_stop is not None:
        conditions.append(f"Invalid if price trades at or below stop {card.exit_plan.initial_stop}.")
    conditions.append(card.exit_plan.invalidation)
    if card.entry_plan.no_chase_above is not None:
        conditions.append(f"Do not chase above {card.entry_plan.no_chase_above} without a fresh setup.")
    conditions.append(card.exit_plan.time_stop)
    return _dedupe(conditions)


def _verification_checks(card: OpportunityCard) -> list[str]:
    checks = []
    if card.entry_plan.trigger_price is not None:
        checks.append(f"Confirm price respects trigger {card.entry_plan.trigger_price}.")
    checks.append(card.entry_plan.confirmation)
    if card.primary_strategy_id:
        checks.append(f"Recheck evidence for {card.primary_strategy_id}.")
    missing = sorted(
        {
            missing_item
            for evaluation in card.strategy_evaluations
            for missing_item in evaluation.missing_data
        }
    )
    if missing:
        checks.append(f"Resolve missing data before sizing up: {', '.join(missing[:5])}.")
    if card.data_caveats:
        checks.append(f"Review data caveats: {'; '.join(card.data_caveats[:3])}.")
    checks.append("Position size should be based on stop distance and portfolio risk budget.")
    return _dedupe(checks)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _clamp(value: float | Decimal) -> float:
    numeric = float(value)
    return max(0.0, min(numeric, 1.0))
