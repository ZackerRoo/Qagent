from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json

from pydantic import BaseModel, Field

from qagent.domain.models import (
    ConfidenceDriver,
    OpportunityCard,
    PreTradeRiskCheck,
    PreTradeRiskProfile,
    RecommendationQualityCheck,
    RiskVeto,
)
from qagent.recommendations.explainability import build_confidence_explanation
from qagent.recommendations.feedback import (
    apply_paper_trading_feedback,
    apply_recommendation_feedback_calibration,
    apply_recommendation_feedback_quality_gate,
    apply_walk_forward_validation_feedback,
    paper_trading_feedback_data_health,
    recommendation_feedback_data_health,
    walk_forward_feedback_data_health,
)
from qagent.strategies.models import StrategyDefinition, StrategyPolicy, StrategyState
from qagent.strategies.registry import default_strategy_registry


FINAL_RECOMMENDATION_POLICY_VERSION = "final-recommendation-policy-v1"
INITIAL_STRATEGY_POLICY_VERSION = "a-share-shadow-policy-v1"
INITIAL_STRATEGY_VERSION = "builtin-registry-v1"
INITIAL_FACTOR_VERSION = "cross-sectional-factor-v2"
INITIAL_UNIVERSE_VERSION = "cn-all-v1"


class StrategyRuntimePolicy(BaseModel):
    strategy_id: str
    strategy_version: str = "legacy"
    state: str = "unmanaged"
    policy_version: str = "legacy"
    effective_weight: float = 1.0
    policy: dict[str, object] = Field(default_factory=dict)
    updated_at: datetime | None = None


class StrategyGovernanceContext(BaseModel):
    strategies: dict[str, StrategyRuntimePolicy] = Field(default_factory=dict)
    source: str = "compatibility_default"


class RecommendationGateDecision(BaseModel):
    action: str
    allowed: bool
    paper_candidate_eligible: bool
    score_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str
    reasons: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class CardStrategyGovernance(BaseModel):
    card_id: str
    instrument_id: str
    strategy_id: str | None = None
    strategy_version: str = "legacy"
    state: str = "unmanaged"
    policy_version: str = "legacy"
    policy: dict[str, object] = Field(default_factory=dict)
    gate_decision: RecommendationGateDecision


class FinalRecommendationPolicyResult(BaseModel):
    cards: list[OpportunityCard]
    audits: list[CardStrategyGovernance]
    data_health: dict[str, str]


def load_strategy_governance_context(repo: object) -> StrategyGovernanceContext:
    """Load the optional governance repository surface without making it mandatory."""

    states = _repository_records(repo, "list_strategy_states")
    if states is None:
        return StrategyGovernanceContext(source="repository_interface_missing")
    states = _initialize_missing_strategy_governance(repo, states)
    deployments = _repository_records(repo, "list_policy_deployments") or []
    by_id = {
        str(deployment_id): deployment
        for deployment in deployments
        if (deployment_id := _record_value(deployment, "deployment_id"))
    }
    by_identity = {
        (
            str(_record_value(deployment, "strategy_id", "")),
            str(_record_value(deployment, "policy_version", "")),
        ): deployment
        for deployment in deployments
    }
    strategies: dict[str, StrategyRuntimePolicy] = {}
    for state_record in states:
        strategy_id = str(_record_value(state_record, "strategy_id", "")).strip()
        if not strategy_id:
            continue
        deployment = by_id.get(
            str(_record_value(state_record, "current_deployment_id", ""))
        )
        if deployment is None:
            deployment = by_identity.get(
                (
                    strategy_id,
                    str(_record_value(state_record, "current_policy_version", "")),
                )
            )
        policy = _model_payload(_record_value(deployment, "policy"))
        strategies[strategy_id] = StrategyRuntimePolicy(
            strategy_id=strategy_id,
            strategy_version=str(
                _record_value(deployment, "strategy_version")
                or policy.get("strategy_version")
                or "legacy"
            ),
            state=_enum_text(_record_value(state_record, "state", "unmanaged")),
            policy_version=str(
                _record_value(state_record, "current_policy_version")
                or _record_value(deployment, "policy_version")
                or policy.get("policy_version")
                or "legacy"
            ),
            effective_weight=_float_value(
                _record_value(state_record, "effective_weight", 0.0),
                default=0.0,
            ),
            policy=policy,
            updated_at=_record_value(state_record, "updated_at"),
        )
    return StrategyGovernanceContext(
        strategies=strategies,
        source="strategy_governance_repository" if strategies else "repository_empty",
    )


def _initialize_missing_strategy_governance(
    repo: object,
    states: list[object],
) -> list[object]:
    initializer = getattr(repo, "initialize_strategy_governance_defaults", None)
    if not callable(initializer):
        return states
    definitions = _default_strategy_definitions()
    existing_ids = {
        str(_record_value(record, "strategy_id", "")).strip() for record in states
    }
    if {definition.strategy_id for definition in definitions}.issubset(existing_ids):
        return states
    policies = [
        StrategyPolicy(
            strategy_id=definition.strategy_id,
            policy_version=INITIAL_STRATEGY_POLICY_VERSION,
            strategy_version=INITIAL_STRATEGY_VERSION,
            factor_version=INITIAL_FACTOR_VERSION,
            parameter_version=FINAL_RECOMMENDATION_POLICY_VERSION,
            universe_version=INITIAL_UNIVERSE_VERSION,
            data_revision="bootstrap",
            state=StrategyState.SHADOW,
            base_weight=0.2,
        )
        for definition in definitions
    ]
    try:
        initialized = initializer(
            definitions=definitions,
            policies=policies,
            strategy_version=INITIAL_STRATEGY_VERSION,
        )
    except Exception:
        return states
    return list(initialized)


def _default_strategy_definitions() -> list[StrategyDefinition]:
    definitions = default_strategy_registry().all()
    if any(item.strategy_id == "factor_rotation_watch" for item in definitions):
        return definitions
    return [
        *definitions,
        StrategyDefinition(
            strategy_id="factor_rotation_watch",
            name="Cross-sectional factor rotation watch",
            family="multifactor_rotation",
            role="primary",
            horizon="5-20d",
            description=(
                "Cross-sectional momentum, quality, value, low-volatility, and "
                "liquidity ranking used to build trigger-based watch candidates."
            ),
            required_data=["daily_ohlcv", "cross_sectional_universe"],
            optional_data=["point_in_time_fundamentals", "industry_classification"],
            free_data_ready=True,
            invalidation_template=(
                "The observation is invalidated when the factor rank decays, the "
                "trigger expires, or the risk filter blocks entry."
            ),
        ),
    ]


def load_latest_walk_forward_validation(
    repo: object,
    provider: str,
) -> Mapping[str, object] | None:
    records = _repository_records(
        repo,
        "list_walk_forward_runs",
        provider=provider.strip().lower(),
        limit=1,
    )
    if not records:
        return None
    payload = _record_value(records[0], "payload")
    if not isinstance(payload, Mapping):
        return None
    validation = payload.get("strategy_validation")
    return validation if isinstance(validation, Mapping) else None


def apply_final_recommendation_policy(
    cards: list[OpportunityCard],
    *,
    recommendation_feedback_center: object | None = None,
    paper_report: object | None = None,
    walk_forward_validation: Mapping[str, object] | None = None,
    governance_context: StrategyGovernanceContext | None = None,
) -> FinalRecommendationPolicyResult:
    """Apply every dynamic adjustment and final strategy gate exactly once per card."""

    context = governance_context or StrategyGovernanceContext()
    apply_recommendation_feedback_calibration(cards, recommendation_feedback_center)
    apply_recommendation_feedback_quality_gate(cards, recommendation_feedback_center)
    apply_paper_trading_feedback(cards, paper_report)
    apply_walk_forward_validation_feedback(cards, walk_forward_validation)

    audits: list[CardStrategyGovernance] = []
    for card in cards:
        runtime = _runtime_policy_for_card(card, context)
        walk_forward = _walk_forward_gate_for_card(card, walk_forward_validation)
        decision = _resolve_gate_decision(card, runtime, walk_forward)
        audit = CardStrategyGovernance(
            card_id=card.card_id,
            instrument_id=card.instrument_id,
            strategy_id=card.primary_strategy_id,
            strategy_version=runtime.strategy_version,
            state=runtime.state,
            policy_version=runtime.policy_version,
            policy=runtime.policy,
            gate_decision=decision,
        )
        _enforce_card_gate(card, audit)
        _attach_governance_explanation(card, audit)
        audits.append(audit)

    health = {
        **recommendation_feedback_data_health(cards),
        **paper_trading_feedback_data_health(cards),
        **walk_forward_feedback_data_health(cards, walk_forward_validation),
        **recommendation_policy_data_health(audits),
    }
    health["paper_feedback_source"] = (
        "paper_daily_report" if paper_report is not None else "unavailable"
    )
    return FinalRecommendationPolicyResult(cards=cards, audits=audits, data_health=health)


def recommendation_policy_data_health(
    audits: list[CardStrategyGovernance],
) -> dict[str, str]:
    decisions = {
        audit.card_id: {
            "strategy_id": audit.strategy_id,
            "strategy_version": audit.strategy_version,
            "state": audit.state,
            "policy": audit.policy_version,
            "gate_decision": audit.gate_decision.action,
            "paper_candidate_eligible": audit.gate_decision.paper_candidate_eligible,
            "reason": audit.gate_decision.reason,
        }
        for audit in audits
    }
    return {
        "recommendation_policy_entrypoint": FINAL_RECOMMENDATION_POLICY_VERSION,
        "dynamic_calibration_passes": "1",
        "strategy_governance_cards": str(len(audits)),
        "strategy_governance_allowed": str(
            sum(1 for audit in audits if audit.gate_decision.allowed)
        ),
        "strategy_governance_throttled": str(
            sum(1 for audit in audits if audit.gate_decision.action == "throttle")
        ),
        "strategy_governance_disabled": str(
            sum(1 for audit in audits if audit.gate_decision.action == "disable")
        ),
        "strategy_governance_paper_blocked": str(
            sum(1 for audit in audits if not audit.gate_decision.paper_candidate_eligible)
        ),
        "strategy_governance_gate_decisions": json.dumps(
            decisions,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "walk_forward_gate_applied_before_ranking": "true",
    }


def governed_card_payloads(
    cards: list[OpportunityCard],
    audits: list[CardStrategyGovernance],
) -> list[dict[str, object]]:
    audit_by_card = {audit.card_id: audit for audit in audits}
    payloads: list[dict[str, object]] = []
    for card in cards:
        payload = card.model_dump(mode="json")
        audit = audit_by_card.get(card.card_id)
        if audit is not None:
            payload["strategy_governance"] = audit.model_dump(mode="json")
            payload["data_health"] = {
                "strategy_version": audit.strategy_version,
                "strategy_state": audit.state,
                "strategy_policy": audit.policy_version,
                "strategy_gate_decision": audit.gate_decision.action,
                "strategy_gate_reason": audit.gate_decision.reason,
                "paper_candidate_eligible": str(
                    audit.gate_decision.paper_candidate_eligible
                ).lower(),
            }
        payloads.append(payload)
    return payloads


def build_strategy_governance_status(
    repo: object,
    *,
    strategy_id: str | None = None,
    event_limit: int = 50,
) -> dict[str, object]:
    context = load_strategy_governance_context(repo)
    selected = [
        runtime
        for key, runtime in sorted(context.strategies.items())
        if strategy_id is None or key == strategy_id
    ]
    strategies = []
    gate_reasons = []
    for runtime in selected:
        decision = _state_gate_decision(runtime)
        strategies.append(
            {
                **runtime.model_dump(mode="json"),
                "gate_decision": decision.model_dump(mode="json"),
            }
        )
        gate_reasons.append(
            {
                "strategy_id": runtime.strategy_id,
                "state": runtime.state,
                "decision": decision.action,
                "reason": decision.reason,
            }
        )

    deployments = _repository_records(
        repo,
        "list_policy_deployments",
        **({"strategy_id": strategy_id} if strategy_id else {}),
    ) or []
    events = _repository_records(
        repo,
        "list_strategy_state_events",
        strategy_id=strategy_id,
        limit=max(event_limit, 1000),
    ) or []
    event_payloads = sorted(
        (_model_payload(event) for event in events),
        key=lambda item: str(item.get("created_at", "")),
        reverse=True,
    )[:event_limit]
    policy_payloads = [_model_payload(deployment) for deployment in deployments]
    return {
        "strategies": strategies,
        "policies": policy_payloads,
        "recent_events": event_payloads,
        "gate_reasons": gate_reasons,
        "data_health": {
            "strategy_governance_source": context.source,
            "strategy_governance_states": str(len(strategies)),
            "strategy_governance_policies": str(len(policy_payloads)),
            "strategy_governance_events": str(len(event_payloads)),
            "strategy_governance_filter": strategy_id or "all",
        },
    }


def _runtime_policy_for_card(
    card: OpportunityCard,
    context: StrategyGovernanceContext,
) -> StrategyRuntimePolicy:
    strategy_id = card.primary_strategy_id or "unassigned"
    return context.strategies.get(strategy_id) or StrategyRuntimePolicy(
        strategy_id=strategy_id,
        state="unmanaged",
        effective_weight=1.0,
    )


def _state_gate_decision(runtime: StrategyRuntimePolicy) -> RecommendationGateDecision:
    state = runtime.state
    if state == "disabled":
        reason = (
            f"策略 {runtime.strategy_id} 的政策 {runtime.policy_version} 已禁用，"
            "不得进入最终推荐或模拟盘候选。"
        )
        return RecommendationGateDecision(
            action="disable",
            allowed=False,
            paper_candidate_eligible=False,
            score_multiplier=0.0,
            reason=reason,
            reasons=[reason],
            sources=["strategy_state"],
        )
    if state == "research":
        reason = (
            f"策略 {runtime.strategy_id} 当前为 research，尚未进入影子验证，"
            "仅保留研究记录。"
        )
        return RecommendationGateDecision(
            action="observe",
            allowed=False,
            paper_candidate_eligible=False,
            score_multiplier=0.0,
            reason=reason,
            reasons=[reason],
            sources=["strategy_state"],
        )
    if state == "shadow":
        reason = (
            f"策略 {runtime.strategy_id} 当前为 shadow，尚未通过样本外准入；"
            "仅作为观察信号展示，并允许进入模拟盘积累前向证据。"
        )
        return RecommendationGateDecision(
            action="observe",
            allowed=False,
            paper_candidate_eligible=True,
            score_multiplier=1.0,
            reason=reason,
            reasons=[reason],
            sources=["strategy_state"],
        )
    if state == "throttled":
        multiplier = _throttle_multiplier(runtime)
        reason = (
            f"策略 {runtime.strategy_id} 当前限流，最终推荐权重按 "
            f"{multiplier:.0%} 生效。"
        )
        return RecommendationGateDecision(
            action="throttle",
            allowed=True,
            paper_candidate_eligible=True,
            score_multiplier=multiplier,
            reason=reason,
            reasons=[reason],
            sources=["strategy_state"],
        )
    if state == "admitted":
        reason = f"策略 {runtime.strategy_id} 已通过政策 {runtime.policy_version} 准入。"
        return RecommendationGateDecision(
            action="allow",
            allowed=True,
            paper_candidate_eligible=True,
            reason=reason,
            reasons=[reason],
            sources=["strategy_state"],
        )
    reason = "未发现持久化策略治理状态，按 legacy 兼容政策放行。"
    return RecommendationGateDecision(
        action="allow",
        allowed=True,
        paper_candidate_eligible=True,
        reason=reason,
        reasons=[reason],
        sources=["compatibility_default"],
    )


def _resolve_gate_decision(
    card: OpportunityCard,
    runtime: StrategyRuntimePolicy,
    walk_forward: RecommendationGateDecision | None,
) -> RecommendationGateDecision:
    state_decision = _state_gate_decision(runtime)
    decisions = [state_decision, *([walk_forward] if walk_forward else [])]
    disable = next((item for item in decisions if item.action == "disable"), None)
    if disable is not None:
        return _combined_decision("disable", decisions, disable.reason, 0.0, False)
    if _card_is_blocked(card):
        reason = "推荐质量或盘前风险门禁已阻断该卡，不得进入模拟盘候选。"
        decisions.append(
            RecommendationGateDecision(
                action="block",
                allowed=False,
                paper_candidate_eligible=False,
                score_multiplier=1.0,
                reason=reason,
                reasons=[reason],
                sources=["recommendation_quality"],
            )
        )
        return _combined_decision("block", decisions, reason, 1.0, False)
    if state_decision.action == "observe":
        return _combined_decision(
            "observe",
            decisions,
            state_decision.reason,
            state_decision.score_multiplier,
            False,
            paper_candidate_eligible=state_decision.paper_candidate_eligible,
        )
    throttles = [item for item in decisions if item.action == "throttle"]
    if throttles:
        multiplier = min(item.score_multiplier for item in throttles)
        reason = "；".join(item.reason for item in throttles)
        return _combined_decision("throttle", decisions, reason, multiplier, True)
    return _combined_decision("allow", decisions, state_decision.reason, 1.0, True)


def _combined_decision(
    action: str,
    decisions: list[RecommendationGateDecision],
    reason: str,
    multiplier: float,
    allowed: bool,
    *,
    paper_candidate_eligible: bool | None = None,
) -> RecommendationGateDecision:
    return RecommendationGateDecision(
        action=action,
        allowed=allowed,
        paper_candidate_eligible=(
            allowed if paper_candidate_eligible is None else paper_candidate_eligible
        ),
        score_multiplier=multiplier,
        reason=reason,
        reasons=list(dict.fromkeys(reason for item in decisions for reason in item.reasons)),
        sources=list(dict.fromkeys(source for item in decisions for source in item.sources)),
    )


def _walk_forward_gate_for_card(
    card: OpportunityCard,
    validation: Mapping[str, object] | None,
) -> RecommendationGateDecision | None:
    if not validation:
        return None
    raw_metrics = [
        *list(validation.get("strategies", []) or []),
        *list(validation.get("factors", []) or []),
    ]
    signal_keys = set(_card_signal_keys(card))
    matched: list[Mapping[str, object]] = []
    for raw in raw_metrics:
        if not isinstance(raw, Mapping):
            continue
        if int(raw.get("out_of_sample_count", 0) or 0) < 30:
            continue
        dimension = str(raw.get("dimension", ""))
        key = str(raw.get("key", ""))
        if dimension == "strategy" and key == card.primary_strategy_id:
            matched.append(raw)
        elif dimension == "factor" and key in signal_keys:
            matched.append(raw)
    disabled = [item for item in matched if str(item.get("action", "")) == "disable"]
    if disabled:
        reasons = [str(item.get("reason") or item.get("label") or item.get("key")) for item in disabled]
        reason = "walk-forward 停用门禁：" + "；".join(reasons)
        return RecommendationGateDecision(
            action="disable",
            allowed=False,
            paper_candidate_eligible=False,
            score_multiplier=0.0,
            reason=reason,
            reasons=reasons,
            sources=["walk_forward"],
        )
    reduced = [
        item
        for item in matched
        if str(item.get("action", "")) in {"reduce", "throttle"}
    ]
    if reduced:
        reasons = [str(item.get("reason") or item.get("label") or item.get("key")) for item in reduced]
        return RecommendationGateDecision(
            action="throttle",
            allowed=True,
            paper_candidate_eligible=True,
            score_multiplier=1.0,
            reason="walk-forward 限流门禁：" + "；".join(reasons),
            reasons=reasons,
            sources=["walk_forward"],
        )
    return None


def _enforce_card_gate(card: OpportunityCard, audit: CardStrategyGovernance) -> None:
    decision = audit.gate_decision
    marker = (
        f"最终策略门禁[{FINAL_RECOMMENDATION_POLICY_VERSION}:"
        f"{audit.strategy_id or 'unassigned'}:{audit.policy_version}:{decision.action}]"
    )
    if any(note.startswith(marker) for note in card.calibration_notes):
        return
    if decision.action == "throttle":
        _apply_score_multiplier(card, decision.score_multiplier)
        _add_quality_check(card, status="warn", detail=decision.reason, impact=-0.08)
    elif decision.action == "disable":
        _block_card(card, decision.reason, score_cap=0.0)
    elif decision.action == "observe":
        if decision.paper_candidate_eligible:
            _mark_shadow_observation(card, decision.reason)
        else:
            _block_card(card, decision.reason, score_cap=0.35)
    card.calibration_notes.append(f"{marker} {decision.reason}")


def _apply_score_multiplier(card: OpportunityCard, multiplier: float) -> None:
    if multiplier >= 1.0:
        return
    card.rank_score = round(card.rank_score * multiplier, 4)
    card.dynamic_score = card.rank_score
    if card.recommendation_score is not None:
        card.recommendation_score.final_score = card.rank_score
        card.recommendation_score.summary = (
            f"推荐分 {card.rank_score:.0%}：策略治理限流权重 {multiplier:.0%}。"
        )
    if card.decision is not None:
        card.decision.suggested_risk_pct = round(
            card.decision.suggested_risk_pct * multiplier,
            4,
        )
        card.decision.max_position_pct = round(
            card.decision.max_position_pct * multiplier,
            4,
        )
    if card.pre_trade_risk is not None:
        card.pre_trade_risk.risk_budget_pct = round(
            card.pre_trade_risk.risk_budget_pct * multiplier,
            4,
        )
        card.pre_trade_risk.max_position_pct = round(
            card.pre_trade_risk.max_position_pct * multiplier,
            4,
        )


def _mark_shadow_observation(card: OpportunityCard, reason: str) -> None:
    _add_quality_check(card, status="warn", detail=reason, impact=0.0)
    check = PreTradeRiskCheck(
        code="final_recommendation_policy_gate",
        severity="warn",
        title="影子策略验证",
        message=reason,
        action="允许模拟盘继续记录；正式准入前不作为确认买点。",
    )
    if card.pre_trade_risk is None:
        card.pre_trade_risk = PreTradeRiskProfile(
            status="shadow",
            label="仅模拟验证",
            can_buy=False,
            can_size_up=False,
            risk_budget_pct=0.0,
            max_position_pct=0.0,
            next_action=check.action,
            summary=reason,
            checks=[check],
        )
    else:
        card.pre_trade_risk.status = "shadow"
        card.pre_trade_risk.label = "仅模拟验证"
        card.pre_trade_risk.can_buy = False
        card.pre_trade_risk.can_size_up = False
        card.pre_trade_risk.risk_budget_pct = 0.0
        card.pre_trade_risk.max_position_pct = 0.0
        card.pre_trade_risk.next_action = check.action
        card.pre_trade_risk.summary = reason
        card.pre_trade_risk.checks = [
            item
            for item in card.pre_trade_risk.checks
            if item.code != check.code
        ] + [check]
    if card.decision is not None:
        card.decision.action = "watch_trigger"
        card.decision.action_label = "影子验证"
        card.decision.risk_status = "shadow"
        card.decision.suggested_risk_pct = 0.0
        card.decision.max_position_pct = 0.0
    if card.recommendation_score is not None:
        card.recommendation_score.tier = "shadow_validation"
        card.recommendation_score.summary = (
            f"推荐分 {card.rank_score:.0%}：仅用于影子验证，尚未通过样本外准入。"
        )


def _block_card(card: OpportunityCard, reason: str, *, score_cap: float) -> None:
    card.rank_score = round(min(card.rank_score, score_cap), 4)
    card.dynamic_score = card.rank_score
    if card.recommendation_score is not None:
        card.recommendation_score.final_score = card.rank_score
        card.recommendation_score.tier = "risk_filtered"
        card.recommendation_score.summary = f"推荐分 {card.rank_score:.0%}：{reason}"
    _add_quality_check(card, status="block", detail=reason, impact=-0.35)
    check = PreTradeRiskCheck(
        code="final_recommendation_policy_gate",
        severity="block",
        title="策略治理门禁",
        message=reason,
        action="策略重新准入或解除停用前，不进入买入与模拟盘候选。",
    )
    if card.pre_trade_risk is None:
        card.pre_trade_risk = PreTradeRiskProfile(
            status="blocked",
            label="不可买",
            can_buy=False,
            can_size_up=False,
            risk_budget_pct=0.0,
            max_position_pct=0.0,
            next_action=check.action,
            summary=reason,
            checks=[check],
        )
    else:
        card.pre_trade_risk.status = "blocked"
        card.pre_trade_risk.label = "不可买"
        card.pre_trade_risk.can_buy = False
        card.pre_trade_risk.can_size_up = False
        card.pre_trade_risk.risk_budget_pct = 0.0
        card.pre_trade_risk.max_position_pct = 0.0
        card.pre_trade_risk.next_action = check.action
        card.pre_trade_risk.summary = reason
        card.pre_trade_risk.checks = [
            item
            for item in card.pre_trade_risk.checks
            if item.code != check.code
        ] + [check]
    if card.decision is not None:
        card.decision.action = "avoid"
        card.decision.action_label = "暂不买"
        card.decision.risk_status = "blocked"
        card.decision.suggested_risk_pct = 0.0
        card.decision.max_position_pct = 0.0
        veto = RiskVeto(
            code="final_recommendation_policy_gate",
            severity="block",
            title="策略治理门禁",
            message=reason,
        )
        card.decision.risk_vetoes = [
            item for item in card.decision.risk_vetoes if item.code != veto.code
        ] + [veto]


def _add_quality_check(
    card: OpportunityCard,
    *,
    status: str,
    detail: str,
    impact: float,
) -> None:
    if card.recommendation_quality is None:
        return
    check = RecommendationQualityCheck(
        code="final_recommendation_policy_gate",
        status=status,
        label="策略治理门禁",
        detail=detail,
        score_impact=impact,
    )
    checks = [
        item
        for item in card.recommendation_quality.checks
        if item.code != check.code
    ] + [check]
    card.recommendation_quality.checks = checks
    card.recommendation_quality.block_count = sum(
        1 for item in checks if item.status == "block"
    )
    card.recommendation_quality.warn_count = sum(
        1 for item in checks if item.status == "warn"
    )
    card.recommendation_quality.pass_count = sum(
        1 for item in checks if item.status == "pass"
    )
    if status == "block":
        card.recommendation_quality.score = round(
            min(card.recommendation_quality.score, 0.25),
            4,
        )
        card.recommendation_quality.tier = "risk_filtered"
        card.recommendation_quality.summary = "风险过滤：策略治理门禁未通过。"


def _attach_governance_explanation(
    card: OpportunityCard,
    audit: CardStrategyGovernance,
) -> None:
    explanation = build_confidence_explanation(card)
    decision = audit.gate_decision
    driver = ConfidenceDriver(
        label="策略治理",
        value=(
            f"strategy_version={audit.strategy_version}; state={audit.state}; "
            f"policy={audit.policy_version}; gate_decision={decision.action}; "
            f"reason={decision.reason}"
        ),
        impact=(
            "negative"
            if decision.action in {"disable", "observe", "block"}
            else "neutral"
            if decision.action == "throttle"
            else "positive"
        ),
        weight=decision.score_multiplier,
    )
    explanation.data_checks = [
        item for item in explanation.data_checks if item.label != driver.label
    ] + [driver]
    if decision.action in {"disable", "observe", "block"}:
        explanation.score = min(explanation.score, card.rank_score)
        explanation.label = "低可信"
    card.confidence_explanation = explanation


def _card_is_blocked(card: OpportunityCard) -> bool:
    if card.decision is not None and (
        card.decision.risk_status in {"blocked", "veto"}
        or card.decision.action in {"avoid", "blocked", "no_trade"}
    ):
        return True
    if card.pre_trade_risk is None or card.pre_trade_risk.can_buy:
        return False
    if card.pre_trade_risk.status == "shadow" and any(
        item.code == "final_recommendation_policy_gate" and item.severity == "warn"
        for item in card.pre_trade_risk.checks
    ):
        return False
    return True


def _throttle_multiplier(runtime: StrategyRuntimePolicy) -> float:
    base_weight = _float_value(runtime.policy.get("base_weight"), default=0.0)
    if base_weight > 0 and runtime.effective_weight > 0:
        return max(0.0, min(1.0, runtime.effective_weight / base_weight))
    breach_policy = runtime.policy.get("breach_policy")
    if isinstance(breach_policy, Mapping):
        configured = _float_value(breach_policy.get("throttle_multiplier"), default=0.5)
        return max(0.0, min(1.0, configured))
    return 0.5


def _card_signal_keys(card: OpportunityCard) -> list[str]:
    keys = list(card.factor_flags)
    if card.a_share_enhanced is not None:
        keys.extend(card.a_share_enhanced.signals)
    if card.primary_strategy_id:
        keys.append(card.primary_strategy_id)
    return sorted(set(key for key in keys if key))


def _repository_records(
    repo: object,
    method_name: str,
    **kwargs: object,
) -> list[object] | None:
    method = getattr(repo, method_name, None)
    if not callable(method):
        return None
    try:
        return list(method(**kwargs))
    except TypeError:
        compatible = {key: value for key, value in kwargs.items() if value is not None}
        try:
            return list(method(**compatible))
        except TypeError:
            try:
                return list(method())
            except Exception:
                return None
        except Exception:
            return None
    except Exception:
        return None


def _model_payload(value: object) -> dict[str, object]:
    if value is None:
        return {}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
        return payload if isinstance(payload, dict) else {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _record_value(value: object, key: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _enum_text(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower() or "unmanaged"


def _float_value(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "CardStrategyGovernance",
    "FINAL_RECOMMENDATION_POLICY_VERSION",
    "INITIAL_STRATEGY_POLICY_VERSION",
    "FinalRecommendationPolicyResult",
    "RecommendationGateDecision",
    "StrategyGovernanceContext",
    "StrategyRuntimePolicy",
    "apply_final_recommendation_policy",
    "build_strategy_governance_status",
    "governed_card_payloads",
    "load_latest_walk_forward_validation",
    "load_strategy_governance_context",
    "recommendation_policy_data_health",
]
