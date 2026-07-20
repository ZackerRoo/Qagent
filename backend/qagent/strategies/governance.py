from __future__ import annotations

import hashlib
import json

from qagent.strategies.models import (
    AdmissionDecision,
    BreachAssessment,
    BreachSeverity,
    GateStatus,
    GovernanceAction,
    MetricGateCheck,
    OutOfSampleGateDecision,
    OutOfSampleMetrics,
    PolicyViolation,
    RollbackDecision,
    StateTransitionDecision,
    StrategyPolicy,
    StrategyState,
)


_ALLOWED_TRANSITIONS: dict[StrategyState, frozenset[StrategyState]] = {
    StrategyState.RESEARCH: frozenset({StrategyState.SHADOW, StrategyState.DISABLED}),
    StrategyState.SHADOW: frozenset(
        {StrategyState.RESEARCH, StrategyState.ADMITTED, StrategyState.DISABLED}
    ),
    StrategyState.ADMITTED: frozenset(
        {StrategyState.THROTTLED, StrategyState.DISABLED}
    ),
    StrategyState.THROTTLED: frozenset(
        {StrategyState.ADMITTED, StrategyState.DISABLED}
    ),
    StrategyState.DISABLED: frozenset({StrategyState.RESEARCH}),
}

_STATE_LABELS = {
    StrategyState.RESEARCH: "研究",
    StrategyState.SHADOW: "影子观察",
    StrategyState.ADMITTED: "已准入",
    StrategyState.THROTTLED: "已限流",
    StrategyState.DISABLED: "已禁用",
}


def decide_state_transition(
    current_state: StrategyState | str,
    target_state: StrategyState | str,
) -> StateTransitionDecision:
    """Return a structural state-machine decision without mutating policy state."""

    current = StrategyState(current_state)
    target = StrategyState(target_state)
    if current is target:
        return StateTransitionDecision(
            allowed=True,
            from_state=current,
            to_state=target,
            effective_state=current,
            reason=f"策略已处于{_STATE_LABELS[current]}状态，无需重复转换。",
        )
    allowed = target in _ALLOWED_TRANSITIONS[current]
    if allowed:
        reason = f"允许策略从{_STATE_LABELS[current]}状态转换为{_STATE_LABELS[target]}状态。"
    else:
        reason = (
            f"不允许策略从{_STATE_LABELS[current]}状态直接转换为"
            f"{_STATE_LABELS[target]}状态。"
        )
    return StateTransitionDecision(
        allowed=allowed,
        from_state=current,
        to_state=target,
        effective_state=target if allowed else current,
        reason=reason,
    )


def can_transition(
    current_state: StrategyState | str,
    target_state: StrategyState | str,
) -> bool:
    return decide_state_transition(current_state, target_state).allowed


def strategy_policy_digest(policy: StrategyPolicy) -> str:
    """Build a stable digest for audit records and reproducibility checks."""

    payload = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evaluate_out_of_sample_gate(
    policy: StrategyPolicy,
    metrics: OutOfSampleMetrics,
) -> OutOfSampleGateDecision:
    gate = policy.oos_gate
    checks = [
        _count_check(
            key="sample_count",
            label="样本外交易数",
            actual=metrics.sample_count,
            minimum=gate.min_sample_count,
        ),
        _count_check(
            key="cluster_count",
            label="独立调仓期数",
            actual=metrics.cluster_count,
            minimum=gate.min_cluster_count,
        ),
        _minimum_check(
            key="mean_return_pct",
            label="样本外平均收益",
            actual=metrics.mean_return_pct,
            threshold=gate.min_mean_return_pct,
            strict=True,
            unit="%",
        ),
        _minimum_check(
            key="confidence_low_pct",
            label="收益置信区间下界",
            actual=metrics.confidence_low_pct,
            threshold=gate.min_confidence_low_pct,
            strict=True,
            unit="%",
        ),
        _maximum_check(
            key="positive_edge_p_value",
            label="正向优势 p 值",
            actual=metrics.positive_edge_p_value,
            threshold=gate.max_positive_edge_p_value,
        ),
        _maximum_check(
            key="false_discovery_rate",
            label="多重检验 FDR",
            actual=metrics.false_discovery_rate,
            threshold=gate.max_false_discovery_rate,
        ),
    ]
    if gate.min_benchmark_excess_return_pct is not None:
        checks.append(
            _minimum_check(
                key="benchmark_excess_return_pct",
                label="可交易基准超额收益",
                actual=metrics.benchmark_excess_return_pct,
                threshold=gate.min_benchmark_excess_return_pct,
                strict=True,
                unit="%",
            )
        )
    if gate.min_cost_stress_return_pct is not None:
        checks.append(
            _minimum_check(
                key="cost_stress_return_pct",
                label="压力成本后收益",
                actual=metrics.cost_stress_return_pct,
                threshold=gate.min_cost_stress_return_pct,
                strict=True,
                unit="%",
            )
        )
    if gate.drawdown_floor_pct is not None:
        checks.append(
            _minimum_check(
                key="max_drawdown_pct",
                label="最大回撤",
                actual=metrics.max_drawdown_pct,
                threshold=gate.drawdown_floor_pct,
                strict=False,
                unit="%",
            )
        )
    if gate.min_win_rate is not None:
        checks.append(
            _minimum_check(
                key="win_rate",
                label="样本外胜率",
                actual=metrics.win_rate,
                threshold=gate.min_win_rate,
                strict=False,
            )
        )
    if gate.min_profit_factor is not None:
        checks.append(
            _minimum_check(
                key="profit_factor",
                label="样本外盈亏比",
                actual=metrics.profit_factor,
                threshold=gate.min_profit_factor,
                strict=False,
            )
        )
    if gate.min_regime_pass_ratio is not None:
        checks.append(
            _minimum_check(
                key="regime_pass_ratio",
                label="市场环境通过比例",
                actual=metrics.regime_pass_ratio,
                threshold=gate.min_regime_pass_ratio,
                strict=False,
            )
        )
    if gate.max_turnover_pct is not None:
        checks.append(
            _maximum_check(
                key="turnover_pct",
                label="换手率",
                actual=metrics.turnover_pct,
                threshold=gate.max_turnover_pct,
                unit="%",
            )
        )

    status = _overall_gate_status(checks)
    failed = [check.reason for check in checks if check.status is not GateStatus.PASS]
    if status is GateStatus.PASS:
        reason = (
            f"策略 {policy.strategy_id} 的样本外证据通过政策 "
            f"{policy.policy_version} 全部门禁。"
        )
    elif status is GateStatus.INSUFFICIENT:
        reason = "样本外证据不足：" + "；".join(failed)
    else:
        reason = "样本外门禁未通过：" + "；".join(failed)
    return OutOfSampleGateDecision(
        strategy_id=policy.strategy_id,
        policy_version=policy.policy_version,
        status=status,
        passed=status is GateStatus.PASS,
        checks=tuple(checks),
        reason=reason,
    )


def evaluate_oos_gate(
    metrics: OutOfSampleMetrics,
    policy: StrategyPolicy,
) -> OutOfSampleGateDecision:
    """Metrics-first convenience wrapper for callers that build evidence first."""

    return evaluate_out_of_sample_gate(policy, metrics)


def decide_admission(
    policy: StrategyPolicy,
    metrics: OutOfSampleMetrics,
    *,
    current_state: StrategyState | str | None = None,
) -> AdmissionDecision:
    state = StrategyState(current_state) if current_state is not None else policy.state
    gate = evaluate_out_of_sample_gate(policy, metrics)
    eligible_states = {StrategyState.SHADOW, StrategyState.THROTTLED, StrategyState.ADMITTED}
    if state not in eligible_states:
        if state is StrategyState.RESEARCH:
            reason = "研究态策略必须先进入影子观察，不能直接准入。"
        else:
            reason = "已禁用策略必须以新政策版本回到研究态后重新验证。"
        return AdmissionDecision(
            strategy_id=policy.strategy_id,
            policy_version=policy.policy_version,
            admitted=False,
            from_state=state,
            to_state=state,
            gate=gate,
            reason=reason,
        )
    if not gate.passed:
        return AdmissionDecision(
            strategy_id=policy.strategy_id,
            policy_version=policy.policy_version,
            admitted=False,
            from_state=state,
            to_state=state,
            gate=gate,
            reason=gate.reason,
        )
    transition = decide_state_transition(state, StrategyState.ADMITTED)
    return AdmissionDecision(
        strategy_id=policy.strategy_id,
        policy_version=policy.policy_version,
        admitted=transition.allowed,
        from_state=state,
        to_state=transition.effective_state,
        gate=gate,
        reason=(
            f"策略 {policy.strategy_id} 通过样本外门禁，允许进入已准入状态。"
            if transition.allowed
            else transition.reason
        ),
    )


def evaluate_policy_breach(
    policy: StrategyPolicy,
    metrics: OutOfSampleMetrics,
) -> BreachAssessment:
    gate = policy.oos_gate
    thresholds = policy.breach_policy
    gate_decision = evaluate_out_of_sample_gate(policy, metrics)
    violations: list[PolicyViolation] = []

    if (
        metrics.sample_count >= gate.min_sample_count
        and metrics.cluster_count >= gate.min_cluster_count
        and metrics.confidence_high_pct is not None
        and metrics.negative_edge_p_value is not None
        and metrics.confidence_high_pct < 0
        and metrics.negative_edge_p_value <= thresholds.hard_negative_edge_p_value
    ):
        violations.append(
            PolicyViolation(
                code="significant_negative_edge",
                severity=BreachSeverity.HARD,
                actual=metrics.confidence_high_pct,
                threshold=0.0,
                reason=(
                    "样本外收益置信区间上界低于 0，且负向检验达到显著水平"
                    f"（p={_format_number(metrics.negative_edge_p_value)}）。"
                ),
            )
        )
    if metrics.consecutive_failed_windows >= thresholds.hard_consecutive_failed_windows:
        violations.append(
            PolicyViolation(
                code="consecutive_failed_windows",
                severity=BreachSeverity.HARD,
                actual=metrics.consecutive_failed_windows,
                threshold=thresholds.hard_consecutive_failed_windows,
                reason=(
                    f"连续失败窗口达到 {metrics.consecutive_failed_windows} 个，"
                    f"触发硬阈值 {thresholds.hard_consecutive_failed_windows} 个。"
                ),
            )
        )

    _append_floor_violation(
        violations,
        code="mean_return_pct",
        label="样本外平均收益",
        actual=metrics.mean_return_pct,
        soft_floor=thresholds.soft_mean_return_floor_pct,
        hard_floor=thresholds.hard_mean_return_floor_pct,
    )
    _append_floor_violation(
        violations,
        code="cost_stress_return_pct",
        label="压力成本后收益",
        actual=metrics.cost_stress_return_pct,
        soft_floor=thresholds.soft_cost_stress_floor_pct,
        hard_floor=thresholds.hard_cost_stress_floor_pct,
    )
    _append_floor_violation(
        violations,
        code="max_drawdown_pct",
        label="最大回撤",
        actual=metrics.max_drawdown_pct,
        soft_floor=thresholds.soft_drawdown_floor_pct,
        hard_floor=thresholds.hard_drawdown_floor_pct,
    )

    existing_codes = {item.code for item in violations}
    soft_checks = (
        (
            "confidence_low_pct",
            "收益置信区间下界不再高于准入阈值。",
            metrics.confidence_low_pct,
            gate.min_confidence_low_pct,
            lambda actual, threshold: actual <= threshold,
        ),
        (
            "positive_edge_p_value",
            "正向优势 p 值超过政策上限。",
            metrics.positive_edge_p_value,
            gate.max_positive_edge_p_value,
            lambda actual, threshold: actual > threshold,
        ),
        (
            "false_discovery_rate",
            "多重检验 FDR 超过政策上限。",
            metrics.false_discovery_rate,
            gate.max_false_discovery_rate,
            lambda actual, threshold: actual > threshold,
        ),
        (
            "benchmark_excess_return_pct",
            "可交易基准超额收益不再高于准入阈值。",
            metrics.benchmark_excess_return_pct,
            gate.min_benchmark_excess_return_pct,
            lambda actual, threshold: actual <= threshold,
        ),
        (
            "win_rate",
            "样本外胜率低于政策下限。",
            metrics.win_rate,
            gate.min_win_rate,
            lambda actual, threshold: actual < threshold,
        ),
        (
            "profit_factor",
            "样本外盈亏比低于政策下限。",
            metrics.profit_factor,
            gate.min_profit_factor,
            lambda actual, threshold: actual < threshold,
        ),
        (
            "regime_pass_ratio",
            "市场环境通过比例低于政策下限。",
            metrics.regime_pass_ratio,
            gate.min_regime_pass_ratio,
            lambda actual, threshold: actual < threshold,
        ),
        (
            "turnover_pct",
            "换手率超过政策上限。",
            metrics.turnover_pct,
            gate.max_turnover_pct,
            lambda actual, threshold: actual > threshold,
        ),
    )
    for code, reason, actual, threshold, failed in soft_checks:
        if (
            code not in existing_codes
            and actual is not None
            and threshold is not None
            and failed(actual, threshold)
        ):
            violations.append(
                PolicyViolation(
                    code=code,
                    severity=BreachSeverity.SOFT,
                    actual=actual,
                    threshold=threshold,
                    reason=reason,
                )
            )

    if any(item.severity is BreachSeverity.HARD for item in violations):
        severity = BreachSeverity.HARD
    elif violations:
        severity = BreachSeverity.SOFT
    else:
        severity = BreachSeverity.NONE
    evaluable = bool(violations) or gate_decision.status is not GateStatus.INSUFFICIENT
    if severity is BreachSeverity.HARD:
        reason = "检测到硬违约：" + " ".join(
            item.reason for item in violations if item.severity is BreachSeverity.HARD
        )
    elif severity is BreachSeverity.SOFT:
        reason = "检测到软违约：" + " ".join(item.reason for item in violations)
    elif not evaluable:
        reason = "样本外证据不足或指标不完整，暂不认定违约，也不恢复权重。"
    else:
        reason = "未检测到软违约或硬违约，维持当前治理状态。"
    return BreachAssessment(
        severity=severity,
        evaluable=evaluable,
        violations=tuple(violations),
        reason=reason,
    )


def decide_rollback(
    policy: StrategyPolicy,
    metrics: OutOfSampleMetrics,
    *,
    current_state: StrategyState | str | None = None,
    rollback_policy_version: str | None = None,
) -> RollbackDecision:
    state = StrategyState(current_state) if current_state is not None else policy.state
    breach = evaluate_policy_breach(policy, metrics)
    requested_rollback = (
        rollback_policy_version
        if rollback_policy_version is not None
        else policy.rollback_policy_version
    )
    if requested_rollback is not None:
        requested_rollback = requested_rollback.strip()
        if not requested_rollback:
            raise ValueError("rollback_policy_version must not be blank")
        if requested_rollback == policy.policy_version:
            raise ValueError("rollback policy must differ from current policy")

    if breach.severity is BreachSeverity.HARD:
        rollback_required = requested_rollback is not None
        return RollbackDecision(
            strategy_id=policy.strategy_id,
            current_policy_version=policy.policy_version,
            action=(GovernanceAction.ROLLBACK if rollback_required else GovernanceAction.DISABLE),
            from_state=state,
            to_state=StrategyState.DISABLED,
            breach=breach,
            rollback_required=rollback_required,
            disable_current_policy=True,
            rollback_to_policy_version=requested_rollback,
            effective_weight=0.0,
            reason=(
                f"硬违约已触发：禁用当前政策 {policy.policy_version}，并请求回滚到政策 "
                f"{requested_rollback}；回滚版本需重新从研究态验证。"
                if rollback_required
                else f"硬违约已触发：禁用当前政策 {policy.policy_version}，当前无可用回滚版本。"
            ),
        )

    if breach.severity is BreachSeverity.SOFT and state in {
        StrategyState.ADMITTED,
        StrategyState.THROTTLED,
    }:
        return RollbackDecision(
            strategy_id=policy.strategy_id,
            current_policy_version=policy.policy_version,
            action=GovernanceAction.THROTTLE,
            from_state=state,
            to_state=StrategyState.THROTTLED,
            breach=breach,
            rollback_required=False,
            disable_current_policy=False,
            effective_weight=_throttled_weight(policy),
            reason=(
                f"软违约已触发：策略进入限流状态，权重从 "
                f"{_format_number(policy.base_weight)} 下调至 "
                f"{_format_number(_throttled_weight(policy))}。"
            ),
        )

    if breach.severity is BreachSeverity.SOFT:
        return RollbackDecision(
            strategy_id=policy.strategy_id,
            current_policy_version=policy.policy_version,
            action=GovernanceAction.HOLD,
            from_state=state,
            to_state=state,
            breach=breach,
            rollback_required=False,
            disable_current_policy=False,
            effective_weight=0.0,
            reason="策略尚未准入，软违约仅阻止准入并保持零生效权重。",
        )

    return RollbackDecision(
        strategy_id=policy.strategy_id,
        current_policy_version=policy.policy_version,
        action=GovernanceAction.HOLD,
        from_state=state,
        to_state=state,
        breach=breach,
        rollback_required=False,
        disable_current_policy=False,
        effective_weight=_effective_weight(policy, state),
        reason=breach.reason,
    )


def _count_check(
    *,
    key: str,
    label: str,
    actual: int,
    minimum: int,
) -> MetricGateCheck:
    passed = actual >= minimum
    return MetricGateCheck(
        key=key,
        status=GateStatus.PASS if passed else GateStatus.INSUFFICIENT,
        passed=passed,
        actual=actual,
        requirement=f">= {minimum}",
        reason=(
            f"{label} {actual}，达到至少 {minimum}。"
            if passed
            else f"{label}仅 {actual}，至少需要 {minimum}。"
        ),
    )


def _minimum_check(
    *,
    key: str,
    label: str,
    actual: float | None,
    threshold: float,
    strict: bool,
    unit: str = "",
) -> MetricGateCheck:
    operator = ">" if strict else ">="
    requirement = f"{operator} {_format_number(threshold)}{unit}"
    if actual is None:
        return MetricGateCheck(
            key=key,
            status=GateStatus.INSUFFICIENT,
            passed=False,
            actual=None,
            requirement=requirement,
            reason=f"{label}缺失，无法验证门槛 {requirement}。",
        )
    passed = actual > threshold if strict else actual >= threshold
    return MetricGateCheck(
        key=key,
        status=GateStatus.PASS if passed else GateStatus.FAIL,
        passed=passed,
        actual=actual,
        requirement=requirement,
        reason=(
            f"{label} {_format_number(actual)}{unit}，通过门槛 {requirement}。"
            if passed
            else f"{label} {_format_number(actual)}{unit}，未通过门槛 {requirement}。"
        ),
    )


def _maximum_check(
    *,
    key: str,
    label: str,
    actual: float | None,
    threshold: float,
    unit: str = "",
) -> MetricGateCheck:
    requirement = f"<= {_format_number(threshold)}{unit}"
    if actual is None:
        return MetricGateCheck(
            key=key,
            status=GateStatus.INSUFFICIENT,
            passed=False,
            actual=None,
            requirement=requirement,
            reason=f"{label}缺失，无法验证门槛 {requirement}。",
        )
    passed = actual <= threshold
    return MetricGateCheck(
        key=key,
        status=GateStatus.PASS if passed else GateStatus.FAIL,
        passed=passed,
        actual=actual,
        requirement=requirement,
        reason=(
            f"{label} {_format_number(actual)}{unit}，通过门槛 {requirement}。"
            if passed
            else f"{label} {_format_number(actual)}{unit}，未通过门槛 {requirement}。"
        ),
    )


def _overall_gate_status(checks: list[MetricGateCheck]) -> GateStatus:
    if any(check.status is GateStatus.INSUFFICIENT for check in checks):
        return GateStatus.INSUFFICIENT
    if any(check.status is GateStatus.FAIL for check in checks):
        return GateStatus.FAIL
    return GateStatus.PASS


def _append_floor_violation(
    violations: list[PolicyViolation],
    *,
    code: str,
    label: str,
    actual: float | None,
    soft_floor: float,
    hard_floor: float,
) -> None:
    if actual is None:
        return
    if actual <= hard_floor:
        violations.append(
            PolicyViolation(
                code=code,
                severity=BreachSeverity.HARD,
                actual=actual,
                threshold=hard_floor,
                reason=(
                    f"{label} {_format_number(actual)}%，跌破硬阈值 "
                    f"{_format_number(hard_floor)}%。"
                ),
            )
        )
    elif actual <= soft_floor:
        violations.append(
            PolicyViolation(
                code=code,
                severity=BreachSeverity.SOFT,
                actual=actual,
                threshold=soft_floor,
                reason=(
                    f"{label} {_format_number(actual)}%，跌破软阈值 "
                    f"{_format_number(soft_floor)}%。"
                ),
            )
        )


def _effective_weight(policy: StrategyPolicy, state: StrategyState) -> float:
    if state is StrategyState.ADMITTED:
        return policy.base_weight
    if state is StrategyState.THROTTLED:
        return _throttled_weight(policy)
    return 0.0


def _throttled_weight(policy: StrategyPolicy) -> float:
    return round(policy.base_weight * policy.breach_policy.throttle_multiplier, 10)


def _format_number(value: float | int) -> str:
    return format(value, ".10g")


transition_strategy_state = decide_state_transition
is_transition_allowed = can_transition
policy_digest = strategy_policy_digest
evaluate_admission = decide_admission
classify_breach = evaluate_policy_breach
decide_policy_rollback = decide_rollback


__all__ = [
    "can_transition",
    "classify_breach",
    "decide_admission",
    "decide_policy_rollback",
    "decide_rollback",
    "decide_state_transition",
    "evaluate_admission",
    "evaluate_oos_gate",
    "evaluate_out_of_sample_gate",
    "evaluate_policy_breach",
    "is_transition_allowed",
    "policy_digest",
    "strategy_policy_digest",
    "transition_strategy_state",
]
