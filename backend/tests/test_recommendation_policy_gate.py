from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.db import create_session_factory, initialize_database
from qagent.factors.models import FactorExposure, FactorRanking
from qagent.monitoring.recommendation_calibration import (
    RecommendationCalibrationCenter,
    RecommendationSignalEffect,
)
from qagent.recommendations.governance import (
    StrategyGovernanceContext,
    StrategyRuntimePolicy,
    apply_final_recommendation_policy,
    governed_card_payloads,
    load_strategy_governance_context,
)
from qagent.recommendations.quality_gate import apply_recommendation_quality_gate
from qagent.recommendations.rotation import sort_recommendation_cards
from qagent.storage.repository import QagentRepository


def test_disabled_strategy_is_gated_before_ranking_and_paper_admission():
    disabled = _card("CN:688981", 0.91, "trend_momentum_stage2")
    admitted = _card("CN:600519", 0.64, "healthy_pullback")
    apply_recommendation_quality_gate([disabled, admitted])
    context = StrategyGovernanceContext(
        strategies={
            "trend_momentum_stage2": _runtime(
                "trend_momentum_stage2",
                state="disabled",
                strategy_version="trend-v3",
                policy_version="trend-policy-v4",
                effective_weight=0.0,
            ),
            "healthy_pullback": _runtime(
                "healthy_pullback",
                state="admitted",
                strategy_version="pullback-v2",
                policy_version="pullback-policy-v2",
                effective_weight=0.2,
            ),
        },
        source="test",
    )

    result = apply_final_recommendation_policy(
        [disabled, admitted],
        governance_context=context,
    )
    ranked = sort_recommendation_cards(result.cards)
    audit = next(item for item in result.audits if item.card_id == disabled.card_id)

    assert disabled.rank_score == 0
    assert ranked[0].card_id == admitted.card_id
    assert disabled.decision is not None
    assert disabled.decision.action == "avoid"
    assert disabled.pre_trade_risk is not None
    assert disabled.pre_trade_risk.can_buy is False
    assert audit.strategy_version == "trend-v3"
    assert audit.state == "disabled"
    assert audit.policy_version == "trend-policy-v4"
    assert audit.gate_decision.action == "disable"
    assert audit.gate_decision.paper_candidate_eligible is False
    assert any(
        "strategy_version=trend-v3" in item.value
        and "gate_decision=disable" in item.value
        for item in disabled.confidence_explanation.data_checks
    )

    payload = next(
        item
        for item in governed_card_payloads(result.cards, result.audits)
        if item["card_id"] == disabled.card_id
    )
    assert payload["data_health"]["strategy_version"] == "trend-v3"
    assert payload["data_health"]["strategy_state"] == "disabled"
    assert payload["data_health"]["strategy_policy"] == "trend-policy-v4"
    assert payload["data_health"]["strategy_gate_decision"] == "disable"


def test_throttled_strategy_is_scaled_once_even_when_final_policy_replays():
    card = _card("CN:000063", 0.80, "trend_momentum_stage2")
    apply_recommendation_quality_gate([card])
    context = StrategyGovernanceContext(
        strategies={
            "trend_momentum_stage2": _runtime(
                "trend_momentum_stage2",
                state="throttled",
                strategy_version="trend-v3",
                policy_version="trend-policy-v4",
                effective_weight=0.1,
            )
        },
        source="test",
    )
    before = card.rank_score

    first = apply_final_recommendation_policy([card], governance_context=context)
    after_first = card.rank_score
    second = apply_final_recommendation_policy([card], governance_context=context)

    assert after_first == pytest.approx(round(before * 0.5, 4))
    assert card.rank_score == after_first
    assert first.audits[0].gate_decision.action == "throttle"
    assert second.audits[0].gate_decision.score_multiplier == 0.5
    assert second.data_health["dynamic_calibration_passes"] == "1"
    assert sum(
        note.startswith("最终策略门禁[final-recommendation-policy-v1")
        for note in card.calibration_notes
    ) == 1


def test_shadow_strategy_is_observation_only_but_remains_paper_eligible():
    card = _card("CN:000063", 0.80, "trend_momentum_stage2")
    apply_recommendation_quality_gate([card])
    context = StrategyGovernanceContext(
        strategies={
            "trend_momentum_stage2": _runtime(
                "trend_momentum_stage2",
                state="shadow",
                strategy_version="trend-v3",
                policy_version="trend-policy-v4",
                effective_weight=0.0,
            )
        },
        source="test",
    )
    before = card.rank_score

    first = apply_final_recommendation_policy([card], governance_context=context)
    second = apply_final_recommendation_policy([card], governance_context=context)

    assert card.rank_score == before
    assert first.audits[0].gate_decision.action == "observe"
    assert first.audits[0].gate_decision.allowed is False
    assert first.audits[0].gate_decision.paper_candidate_eligible is True
    assert second.audits[0].gate_decision.paper_candidate_eligible is True
    assert card.decision is not None
    assert card.decision.action == "watch_trigger"
    assert card.decision.risk_status == "shadow"
    assert card.pre_trade_risk is not None
    assert card.pre_trade_risk.can_buy is False
    assert card.pre_trade_risk.label == "仅模拟验证"
    assert sum("仅作为观察信号" in note for note in card.calibration_notes) == 1


def test_loading_empty_repository_initializes_versioned_shadow_policies(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'governance-defaults.db'}"
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))

    context = load_strategy_governance_context(repo)

    assert context.source == "strategy_governance_repository"
    assert "trend_momentum_stage2" in context.strategies
    assert "factor_rotation_watch" in context.strategies
    assert all(runtime.state == "shadow" for runtime in context.strategies.values())
    assert all(
        runtime.policy_version == "a-share-shadow-policy-v1"
        for runtime in context.strategies.values()
    )
    assert all(
        runtime.strategy_version == "builtin-registry-v1"
        for runtime in context.strategies.values()
    )


def test_walk_forward_disable_is_final_and_idempotent():
    card = _card("CN:002747", 0.86, "breakout_volume_confirmation")
    apply_recommendation_quality_gate([card])
    context = StrategyGovernanceContext(
        strategies={
            "breakout_volume_confirmation": _runtime(
                "breakout_volume_confirmation",
                state="admitted",
                strategy_version="breakout-v2",
                policy_version="breakout-policy-v2",
                effective_weight=0.2,
            )
        },
        source="test",
    )
    validation = {
        "status": "rejected",
        "strategies": [
            {
                "dimension": "strategy",
                "key": "breakout_volume_confirmation",
                "label": "放量突破",
                "out_of_sample_count": 42,
                "action": "disable",
                "suggested_weight_delta": -0.10,
                "reason": "样本外聚类收益显著为负。",
            }
        ],
        "factors": [],
    }

    first = apply_final_recommendation_policy(
        [card],
        walk_forward_validation=validation,
        governance_context=context,
    )
    second = apply_final_recommendation_policy(
        [card],
        walk_forward_validation=validation,
        governance_context=context,
    )

    assert card.rank_score == 0
    assert first.audits[0].gate_decision.action == "disable"
    assert "walk_forward" in first.audits[0].gate_decision.sources
    assert second.audits[0].gate_decision.paper_candidate_eligible is False
    assert sum("样本外门禁" in note for note in card.calibration_notes) == 1


def test_walk_forward_factor_gate_matches_high_factor_exposure():
    card = _card("CN:002747", 0.86, "factor_rotation_watch")
    card.factor_flags = []
    card.factor_exposures = [
        FactorExposure(
            factor_id="size",
            label="市值",
            raw_value=1_000_000_000,
            score=0.82,
            weight=0.10,
            explanation="小市值暴露较高。",
        )
    ]
    apply_recommendation_quality_gate([card])
    validation = {
        "status": "rejected",
        "strategies": [],
        "factors": [
            {
                "dimension": "factor",
                "key": "size",
                "label": "市值",
                "out_of_sample_count": 71,
                "action": "disable",
                "suggested_weight_delta": -0.10,
                "reason": "样本外日期聚类结果显著为负。",
            }
        ],
    }

    result = apply_final_recommendation_policy(
        [card],
        walk_forward_validation=validation,
    )

    assert result.audits[0].gate_decision.action == "disable"
    assert result.audits[0].gate_decision.paper_candidate_eligible is False
    assert card.rank_score == 0
    assert any("样本外门禁" in note for note in card.calibration_notes)


def test_missing_governance_repository_surface_keeps_legacy_response_compatible():
    card = _card("US:TEST", 0.73, "factor_rotation_watch")
    before = card.rank_score

    result = apply_final_recommendation_policy([card])

    assert card.rank_score == before
    assert result.audits[0].strategy_version == "legacy"
    assert result.audits[0].state == "unmanaged"
    assert result.audits[0].policy_version == "legacy"
    assert result.audits[0].gate_decision.action == "allow"
    assert result.data_health["strategy_governance_allowed"] == "1"


def test_feedback_and_paper_dynamic_calibration_each_apply_only_once():
    card = _card("CN:688981", 0.72, "trend_momentum_stage2")
    center = RecommendationCalibrationCenter(
        as_of=date(2026, 7, 17),
        headline="闭环反馈可用",
        verdict="继续观察",
        reliability_score=0.7,
        baseline_win_rate_10d=0.5,
        baseline_avg_return_10d=0.2,
        score_bands=[],
        signal_effects=[
            RecommendationSignalEffect(
                signal_key="trend_momentum_stage2",
                label="趋势动量",
                sample_count=8,
                completed_count=6,
                win_rate_10d=0.62,
                avg_return_10d=1.8,
                baseline_avg_return_10d=0.2,
                lift_vs_baseline_10d=1.6,
                reliability_score=0.72,
                weight_action="提高",
                suggested_weight_delta=0.04,
                reason="历史推荐后表现较好。",
            )
        ],
        data_health={
            "recommendation_calibration_scope": "ranking_v3_production",
            "recommendation_calibration_fail_closed": "true",
            "recommendation_calibration_source_official": "8",
        },
    )
    paper_report = SimpleNamespace(
        risk_gate=SimpleNamespace(can_add_entries=True),
        data_health={
            "paper_reporting_scope": "ranking_v3_production",
            "paper_reporting_fail_closed": "true",
            "paper_reporting_official": "5",
        },
        failure_attribution=[
            SimpleNamespace(
                dimension="strategy",
                key="trend_momentum_stage2",
                label="趋势动量",
                verdict="drag",
                evaluated_trades=5,
                total_return_pct=-3.0,
                win_rate=0.2,
                stopped_trades=3,
                target_hit_trades=0,
            )
        ],
    )

    apply_final_recommendation_policy(
        [card],
        recommendation_feedback_center=center,
        paper_report=paper_report,
    )
    after_first = card.rank_score
    apply_final_recommendation_policy(
        [card],
        recommendation_feedback_center=center,
        paper_report=paper_report,
    )

    assert card.rank_score == after_first
    assert sum("推荐反馈校准" in note for note in card.calibration_notes) == 1
    assert sum("模拟盘反馈降权" in note for note in card.calibration_notes) == 1


def test_governance_ignores_empty_official_and_legacy_feedback_sources():
    card = _card("CN:688981", 0.72, "trend_momentum_stage2")
    before = card.rank_score
    effect = RecommendationSignalEffect(
        signal_key="trend_momentum_stage2",
        label="趋势动量",
        sample_count=100,
        completed_count=100,
        win_rate_10d=0.0,
        avg_return_10d=-20.0,
        baseline_avg_return_10d=1.0,
        lift_vs_baseline_10d=-21.0,
        reliability_score=1.0,
        weight_action="降低",
        suggested_weight_delta=-0.08,
        reason="legacy poison",
    )
    empty_official = RecommendationCalibrationCenter(
        as_of=date(2026, 7, 17),
        headline="无正式样本",
        verdict="样本不足",
        reliability_score=0.0,
        signal_effects=[effect],
        data_health={
            "recommendation_calibration_scope": "ranking_v3_production",
            "recommendation_calibration_fail_closed": "true",
            "recommendation_calibration_source_official": "0",
        },
    )
    legacy_report = SimpleNamespace(
        risk_gate=SimpleNamespace(can_add_entries=False),
        failure_attribution=[
            SimpleNamespace(
                dimension="strategy",
                key="trend_momentum_stage2",
                label="趋势动量",
                verdict="drag",
                evaluated_trades=100,
                total_return_pct=-50.0,
                win_rate=0.0,
                stopped_trades=100,
                target_hit_trades=0,
            )
        ],
        data_health={
            "paper_reporting_scope": "legacy_only",
            "paper_reporting_fail_closed": "true",
            "paper_reporting_official": "0",
        },
    )

    result = apply_final_recommendation_policy(
        [card],
        recommendation_feedback_center=empty_official,
        paper_report=legacy_report,
    )

    assert card.rank_score == before
    assert not any("推荐反馈" in note for note in card.calibration_notes)
    assert not any("模拟盘反馈" in note for note in card.calibration_notes)
    assert result.data_health["recommendation_feedback_scope"] == "no_official_samples"
    assert result.data_health["paper_feedback_scope"] == "no_official_samples"


def test_governance_context_accepts_mapping_records_from_compatibility_repository():
    class CompatibilityRepository:
        def list_strategy_states(self):
            return [
                {
                    "strategy_id": "trend_momentum_stage2",
                    "state": "throttled",
                    "current_deployment_id": "deployment-v2",
                    "current_policy_version": "policy-v2",
                    "effective_weight": 0.1,
                }
            ]

        def list_policy_deployments(self):
            return [
                {
                    "deployment_id": "deployment-v2",
                    "strategy_id": "trend_momentum_stage2",
                    "policy_version": "policy-v2",
                    "strategy_version": "strategy-v2",
                    "policy": {"base_weight": 0.2},
                }
            ]

    context = load_strategy_governance_context(CompatibilityRepository())

    runtime = context.strategies["trend_momentum_stage2"]
    assert runtime.strategy_version == "strategy-v2"
    assert runtime.state == "throttled"
    assert runtime.policy_version == "policy-v2"
    assert runtime.effective_weight == 0.1


def _runtime(
    strategy_id: str,
    *,
    state: str,
    strategy_version: str,
    policy_version: str,
    effective_weight: float,
) -> StrategyRuntimePolicy:
    return StrategyRuntimePolicy(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        state=state,
        policy_version=policy_version,
        effective_weight=effective_weight,
        policy={
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "policy_version": policy_version,
            "base_weight": 0.2,
            "breach_policy": {"throttle_multiplier": 0.5},
        },
    )


def _card(instrument_id: str, score: float, strategy_id: str):
    card = build_factor_watch_card(
        instrument_id,
        _bars(instrument_id),
        FactorRanking(
            instrument_id=instrument_id,
            instrument_label=instrument_id,
            factor_score=score,
            factor_rank=1,
            percentile=score,
            momentum_score=score,
            trend_quality_score=max(0.1, score - 0.04),
            liquidity_score=0.82,
            low_risk_score=0.68,
            reversal_score=0.52,
            execution_penalty=0.0,
            data_completeness=0.9,
            factor_exposures=[],
            flags=[strategy_id],
            missing_data=[],
        ),
    )
    assert card is not None
    card.primary_strategy_id = strategy_id
    card.factor_flags = [strategy_id]
    card.rank_score = score
    return card


def _bars(instrument_id: str) -> pd.DataFrame:
    start = date(2026, 1, 1)
    return pd.DataFrame(
        [
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": 20 + index * 0.08 - 0.05,
                "high": 20 + index * 0.08 + 0.14,
                "low": 20 + index * 0.08 - 0.14,
                "close": 20 + index * 0.08,
                "volume": 2_100_000 + index * 3_500,
                "provider": "fixture",
            }
            for index in range(100)
        ]
    )
