import pytest
from pydantic import ValidationError

from qagent.strategies import (
    BreachPolicy,
    BreachSeverity,
    GateStatus,
    GovernanceAction,
    OutOfSampleGatePolicy,
    OutOfSampleMetrics,
    StrategyDefinition,
    StrategyPolicy,
    StrategyState,
    can_transition,
    decide_admission,
    decide_rollback,
    decide_state_transition,
    evaluate_out_of_sample_gate,
    evaluate_policy_breach,
    strategy_policy_digest,
)


def _policy(state: StrategyState = StrategyState.SHADOW) -> StrategyPolicy:
    return StrategyPolicy(
        strategy_id="trend_momentum_stage2",
        policy_version="trend-policy-v2",
        strategy_version="trend-v3",
        factor_version="factor-v4",
        parameter_version="params-v2",
        universe_version="cn-equity-v3",
        data_revision=17,
        state=state,
        base_weight=0.20,
        rollback_policy_version="trend-policy-v1",
        oos_gate=OutOfSampleGatePolicy(
            min_win_rate=0.50,
            min_profit_factor=1.10,
            min_regime_pass_ratio=0.60,
            max_turnover_pct=120.0,
        ),
        breach_policy=BreachPolicy(throttle_multiplier=0.50),
    )


def _passing_metrics() -> OutOfSampleMetrics:
    return OutOfSampleMetrics(
        sample_count=48,
        cluster_count=16,
        mean_return_pct=1.25,
        confidence_low_pct=0.30,
        confidence_high_pct=2.10,
        positive_edge_p_value=0.02,
        negative_edge_p_value=0.98,
        false_discovery_rate=0.08,
        benchmark_excess_return_pct=3.2,
        cost_stress_return_pct=0.65,
        max_drawdown_pct=-9.5,
        win_rate=0.58,
        profit_factor=1.45,
        regime_pass_ratio=0.75,
        turnover_pct=80.0,
    )


def test_state_machine_allows_only_declared_transitions():
    allowed = {
        (StrategyState.RESEARCH, StrategyState.SHADOW),
        (StrategyState.RESEARCH, StrategyState.DISABLED),
        (StrategyState.SHADOW, StrategyState.RESEARCH),
        (StrategyState.SHADOW, StrategyState.ADMITTED),
        (StrategyState.SHADOW, StrategyState.DISABLED),
        (StrategyState.ADMITTED, StrategyState.THROTTLED),
        (StrategyState.ADMITTED, StrategyState.DISABLED),
        (StrategyState.THROTTLED, StrategyState.ADMITTED),
        (StrategyState.THROTTLED, StrategyState.DISABLED),
        (StrategyState.DISABLED, StrategyState.RESEARCH),
    }

    for current in StrategyState:
        for target in StrategyState:
            expected = current is target or (current, target) in allowed
            decision = decide_state_transition(current, target)
            assert decision.allowed is expected
            assert decision.effective_state is (target if expected else current)
            assert can_transition(current, target) is expected
            assert decision.reason

    rejected = decide_state_transition("research", "admitted")
    assert rejected.effective_state is StrategyState.RESEARCH
    assert "不允许" in rejected.reason


def test_policy_is_versioned_immutable_and_has_stable_digest():
    policy = _policy()
    same_policy = StrategyPolicy.model_validate(policy.model_dump())

    assert policy.policy_version == "trend-policy-v2"
    assert policy.strategy_version == "trend-v3"
    assert policy.factor_version == "factor-v4"
    assert policy.parameter_version == "params-v2"
    assert policy.universe_version == "cn-equity-v3"
    assert policy.data_revision == 17
    assert strategy_policy_digest(policy) == strategy_policy_digest(same_policy)
    assert strategy_policy_digest(policy) != strategy_policy_digest(
        policy.model_copy(update={"policy_version": "trend-policy-v3"})
    )
    with pytest.raises(ValidationError):
        policy.base_weight = 0.9


def test_policy_accepts_compatibility_aliases_for_version_and_weight():
    policy = StrategyPolicy(
        strategy_id="healthy_pullback",
        version="pullback-v2",
        weight=0.15,
        previous_policy_version="pullback-v1",
    )

    assert policy.policy_version == "pullback-v2"
    assert policy.version == "pullback-v2"
    assert policy.base_weight == 0.15
    assert policy.weight == 0.15
    assert policy.rollback_policy_version == "pullback-v1"


def test_out_of_sample_gate_passes_with_complete_positive_evidence_in_fixed_order():
    decision = evaluate_out_of_sample_gate(_policy(), _passing_metrics())

    assert decision.status is GateStatus.PASS
    assert decision.passed is True
    assert [check.key for check in decision.checks] == [
        "sample_count",
        "cluster_count",
        "mean_return_pct",
        "confidence_low_pct",
        "positive_edge_p_value",
        "false_discovery_rate",
        "benchmark_excess_return_pct",
        "cost_stress_return_pct",
        "max_drawdown_pct",
        "win_rate",
        "profit_factor",
        "regime_pass_ratio",
        "turnover_pct",
    ]
    assert all(check.passed for check in decision.checks)
    assert "通过" in decision.reason
    assert decision == evaluate_out_of_sample_gate(_policy(), _passing_metrics())


def test_out_of_sample_gate_distinguishes_insufficient_evidence_from_failure():
    insufficient = _passing_metrics().model_copy(update={"sample_count": 29})
    failed = _passing_metrics().model_copy(update={"confidence_low_pct": -0.1})
    missing = _passing_metrics().model_copy(update={"cost_stress_return_pct": None})

    insufficient_decision = evaluate_out_of_sample_gate(_policy(), insufficient)
    failed_decision = evaluate_out_of_sample_gate(_policy(), failed)
    missing_decision = evaluate_out_of_sample_gate(_policy(), missing)

    assert insufficient_decision.status is GateStatus.INSUFFICIENT
    assert "至少需要 30" in insufficient_decision.reason
    assert failed_decision.status is GateStatus.FAIL
    assert "未通过" in failed_decision.reason
    assert missing_decision.status is GateStatus.INSUFFICIENT
    assert "压力成本后收益缺失" in missing_decision.reason


def test_admission_requires_shadow_stage_and_all_oos_gates():
    admitted = decide_admission(_policy(StrategyState.SHADOW), _passing_metrics())
    research = decide_admission(_policy(StrategyState.RESEARCH), _passing_metrics())
    denied = decide_admission(
        _policy(StrategyState.SHADOW),
        _passing_metrics().model_copy(update={"false_discovery_rate": 0.11}),
    )

    assert admitted.admitted is True
    assert admitted.to_state is StrategyState.ADMITTED
    assert "允许进入已准入状态" in admitted.reason
    assert research.admitted is False
    assert research.to_state is StrategyState.RESEARCH
    assert "先进入影子观察" in research.reason
    assert denied.admitted is False
    assert denied.to_state is StrategyState.SHADOW
    assert denied.gate.status is GateStatus.FAIL


def test_throttled_strategy_can_recover_only_after_gate_passes():
    recovered = decide_admission(_policy(StrategyState.THROTTLED), _passing_metrics())
    still_throttled = decide_admission(
        _policy(StrategyState.THROTTLED),
        _passing_metrics().model_copy(update={"sample_count": 20}),
    )

    assert recovered.admitted is True
    assert recovered.to_state is StrategyState.ADMITTED
    assert still_throttled.admitted is False
    assert still_throttled.to_state is StrategyState.THROTTLED


def test_soft_breach_throttles_admitted_strategy_without_version_rollback():
    soft_metrics = _passing_metrics().model_copy(
        update={
            "mean_return_pct": -0.2,
            "confidence_low_pct": -0.5,
            "positive_edge_p_value": 0.20,
            "false_discovery_rate": 0.25,
            "benchmark_excess_return_pct": -0.1,
            "cost_stress_return_pct": -0.5,
            "max_drawdown_pct": -18.0,
        }
    )
    policy = _policy(StrategyState.ADMITTED)

    breach = evaluate_policy_breach(policy, soft_metrics)
    decision = decide_rollback(policy, soft_metrics)

    assert breach.severity is BreachSeverity.SOFT
    assert all(item.severity is BreachSeverity.SOFT for item in breach.violations)
    assert "软违约" in breach.reason
    assert decision.action is GovernanceAction.THROTTLE
    assert decision.to_state is StrategyState.THROTTLED
    assert decision.effective_weight == 0.10
    assert decision.rollback_required is False
    assert decision.disable_current_policy is False
    assert "权重" in decision.reason


def test_hard_breach_disables_current_version_and_requests_configured_rollback():
    hard_metrics = _passing_metrics().model_copy(
        update={
            "mean_return_pct": -2.0,
            "confidence_low_pct": -2.8,
            "confidence_high_pct": -1.2,
            "positive_edge_p_value": 0.99,
            "negative_edge_p_value": 0.01,
            "false_discovery_rate": 0.50,
            "benchmark_excess_return_pct": -4.0,
            "cost_stress_return_pct": -3.0,
            "max_drawdown_pct": -30.0,
            "consecutive_failed_windows": 2,
        }
    )
    policy = _policy(StrategyState.ADMITTED)

    breach = evaluate_policy_breach(policy, hard_metrics)
    decision = decide_rollback(policy, hard_metrics)

    assert breach.severity is BreachSeverity.HARD
    assert {item.code for item in breach.violations}.issuperset(
        {
            "significant_negative_edge",
            "consecutive_failed_windows",
            "mean_return_pct",
            "cost_stress_return_pct",
            "max_drawdown_pct",
        }
    )
    assert "硬违约" in breach.reason
    assert decision.action is GovernanceAction.ROLLBACK
    assert decision.to_state is StrategyState.DISABLED
    assert decision.effective_weight == 0.0
    assert decision.disable_current_policy is True
    assert decision.rollback_required is True
    assert decision.rollback_to_policy_version == "trend-policy-v1"
    assert "回滚" in decision.reason


def test_hard_breach_without_previous_version_stays_disabled_for_research():
    policy = _policy(StrategyState.ADMITTED).model_copy(
        update={"rollback_policy_version": None}
    )
    metrics = _passing_metrics().model_copy(update={"max_drawdown_pct": -26.0})

    decision = decide_rollback(policy, metrics)

    assert decision.action is GovernanceAction.DISABLE
    assert decision.to_state is StrategyState.DISABLED
    assert decision.rollback_required is False
    assert decision.rollback_to_policy_version is None
    assert "无可用回滚版本" in decision.reason


def test_insufficient_metrics_do_not_fabricate_a_breach_or_restore_weight():
    policy = _policy(StrategyState.THROTTLED)
    metrics = OutOfSampleMetrics(sample_count=8, cluster_count=3)

    breach = evaluate_policy_breach(policy, metrics)
    decision = decide_rollback(policy, metrics)

    assert breach.severity is BreachSeverity.NONE
    assert breach.evaluable is False
    assert breach.violations == ()
    assert "证据不足" in breach.reason
    assert decision.action is GovernanceAction.HOLD
    assert decision.to_state is StrategyState.THROTTLED
    assert decision.effective_weight == 0.10


def test_governance_models_reject_ambiguous_policy_and_metrics():
    with pytest.raises(ValidationError):
        StrategyPolicy(
            strategy_id="trend",
            policy_version="v1",
            rollback_policy_version="v1",
        )
    with pytest.raises(ValidationError):
        OutOfSampleMetrics(
            sample_count=30,
            cluster_count=10,
            confidence_low_pct=1.0,
            confidence_high_pct=0.5,
        )
    with pytest.raises(ValidationError):
        BreachPolicy(
            soft_drawdown_floor_pct=-20.0,
            hard_drawdown_floor_pct=-10.0,
        )


def test_existing_strategy_definition_constructor_remains_compatible():
    definition = StrategyDefinition(
        strategy_id="legacy",
        name="旧策略",
        family="trend",
        role="primary",
        horizon="10d",
        description="兼容性测试",
        required_data=["daily_ohlcv"],
        invalidation_template="趋势失效",
    )

    assert definition.optional_data == []
    assert definition.free_data_ready is True
