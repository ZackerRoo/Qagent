from __future__ import annotations

import hashlib
import json
from datetime import date

from pydantic import BaseModel, Field


RANKING_V3_PROTOCOL_ID = "QAGENT-RANK-V3-20260726"
RANKING_V3_MODEL_VERSION = "point-in-time-net-excess-v3"
RANKING_V3_CANDIDATE_POOL_LIMIT = 50
RANKING_V3_MAX_POSITIONS = 5
RANKING_V3_MAX_PER_STRATEGY = 2
RANKING_V3_MAX_PER_INDUSTRY = 2
RANKING_V3_MAX_ETF_INDEX_OVERLAP = 1
RANKING_V3_EMBARGO_SESSIONS = 25
RANKING_V3_PRIOR_EXPERIMENT_COUNT = 9
RANKING_V3_HISTORICAL_AUDIT_START = date(2024, 7, 29)
RANKING_V3_HISTORICAL_AUDIT_END = date(2025, 12, 31)
RANKING_V3_PROSPECTIVE_SHADOW_START = date(2026, 7, 27)


class RankingV3Window(BaseModel):
    key: str
    label: str
    start_date: date
    end_date: date | None = None
    role: str


class RankingV3GateThresholds(BaseModel):
    minimum_rebalance_dates: int = 24
    minimum_completed_trades: int = 60
    minimum_profit_factor: float = 1.10
    minimum_positive_subperiods: int = 4
    required_subperiods: int = 5
    maximum_drawdown_pct: float = -15.0
    maximum_drawdown_degradation_pct: float = 2.0
    minimum_turnover_reduction_pct: float = 25.0
    maximum_holm_adjusted_p_value: float = 0.05
    minimum_deflated_sharpe_probability: float = 0.95
    maximum_probability_of_backtest_overfit: float = 0.20
    minimum_forward_shadow_sessions: int = 20
    minimum_forward_shadow_trades: int = 10
    maximum_forward_shadow_sessions: int = 40


class RankingV3Protocol(BaseModel):
    protocol_id: str = RANKING_V3_PROTOCOL_ID
    model_version: str = RANKING_V3_MODEL_VERSION
    protocol_digest: str
    frozen_on: date = date(2026, 7, 26)
    prospective_shadow_start: date = RANKING_V3_PROSPECTIVE_SHADOW_START
    candidate_pool_limit: int = RANKING_V3_CANDIDATE_POOL_LIMIT
    max_positions: int = RANKING_V3_MAX_POSITIONS
    max_per_strategy: int = RANKING_V3_MAX_PER_STRATEGY
    max_per_industry: int = RANKING_V3_MAX_PER_INDUSTRY
    max_etf_index_overlap: int = RANKING_V3_MAX_ETF_INDEX_OVERLAP
    embargo_sessions: int = RANKING_V3_EMBARGO_SESSIONS
    prior_experiment_count: int = RANKING_V3_PRIOR_EXPERIMENT_COUNT
    primary_metric: str = "paired_net_excess_return_vs_constraint_matched_baseline"
    training_evidence_rule: str = "outcome_available_at_strictly_before_decision_date"
    sample_unit: str = "independent_rebalance_date"
    historical_oos_label: str = "historical_reused_oos"
    official_recommendation_isolation: str = "shadow_only_until_all_gates_and_forward_validation_pass"
    windows: list[RankingV3Window] = Field(default_factory=list)
    thresholds: RankingV3GateThresholds = Field(default_factory=RankingV3GateThresholds)


def build_ranking_v3_protocol() -> RankingV3Protocol:
    stable_payload = {
        "protocol_id": RANKING_V3_PROTOCOL_ID,
        "model_version": RANKING_V3_MODEL_VERSION,
        "frozen_on": "2026-07-26",
        "prospective_shadow_start": "2026-07-27",
        "candidate_pool_limit": RANKING_V3_CANDIDATE_POOL_LIMIT,
        "max_positions": RANKING_V3_MAX_POSITIONS,
        "max_per_strategy": RANKING_V3_MAX_PER_STRATEGY,
        "max_per_industry": RANKING_V3_MAX_PER_INDUSTRY,
        "max_etf_index_overlap": RANKING_V3_MAX_ETF_INDEX_OVERLAP,
        "embargo_sessions": RANKING_V3_EMBARGO_SESSIONS,
        "prior_experiment_count": RANKING_V3_PRIOR_EXPERIMENT_COUNT,
        "primary_metric": "paired_net_excess_return_vs_constraint_matched_baseline",
        "training_evidence_rule": "outcome_available_at_strictly_before_decision_date",
        "sample_unit": "independent_rebalance_date",
        "historical_oos_label": "historical_reused_oos",
        "official_recommendation_isolation": (
            "shadow_only_until_all_gates_and_forward_validation_pass"
        ),
        "windows": [
            {
                "key": "train",
                "label": "训练期",
                "start_date": "2021-11-01",
                "end_date": "2023-06-30",
                "role": "model_development",
            },
            {
                "key": "validation",
                "label": "验证期",
                "start_date": "2023-07-31",
                "end_date": "2024-06-28",
                "role": "model_selection",
            },
            {
                "key": "historical_reused_oos",
                "label": "历史审计样本外",
                "start_date": RANKING_V3_HISTORICAL_AUDIT_START.isoformat(),
                "end_date": RANKING_V3_HISTORICAL_AUDIT_END.isoformat(),
                "role": "shadow_admission_only",
            },
            {
                "key": "prospective_shadow",
                "label": "前向影子验证",
                "start_date": RANKING_V3_PROSPECTIVE_SHADOW_START.isoformat(),
                "end_date": None,
                "role": "confirmatory_forward_validation",
            },
        ],
        "thresholds": RankingV3GateThresholds().model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        json.dumps(
            stable_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return RankingV3Protocol(
        **stable_payload,
        protocol_digest=digest,
    )
