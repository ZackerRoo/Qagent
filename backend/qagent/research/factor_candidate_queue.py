from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from qagent.factors.research_contract import (
    EXPLICIT_SHADOW_CANDIDATE_IDS,
    FACTOR_RESEARCH_VERSION,
    FEATURE_COLUMNS,
)
from qagent.research.factor_shadow_outcomes import (
    FACTOR_SHADOW_HORIZONS,
    FACTOR_SHADOW_EXECUTION_HEAD_INDUSTRY_CAP,
    FACTOR_SHADOW_PROMOTION_MIN_MATURED_RUNS,
    FACTOR_SHADOW_PROMOTION_MIN_OUTCOME_COVERAGE,
    FACTOR_SHADOW_PROMOTION_MIN_SELECTION_LIFT_RATE,
    FACTOR_SHADOW_PROMOTION_MIN_SESSION_EDGE_RATE,
)


FACTOR_CANDIDATE_QUEUE_SCHEMA = "factor-candidate-shadow-queue-v1"

FactorCandidateFamily = Literal[
    "quality",
    "profitability_improvement",
    "capital_strength",
    "trend_health",
    "valuation_growth_match",
    "catalyst",
]
FactorCandidateState = Literal[
    "contract_available_for_shadow_design",
    "unavailable",
    "future_capability",
]


class FactorCandidateEvidencePolicy(BaseModel):
    evaluator_reference: str = (
        "qagent.research.factor_shadow_outcomes._assess_shadow_promotion"
    )
    gate_policy_completeness: Literal["partial"] = "partial"
    feature_set_version: str = FACTOR_RESEARCH_VERSION
    dataset_revision: str = "frozen_positive_revision"
    temporal_policy: str = "point_in_time_as_of_signal_date"
    validation_policy: str = "purged_train_validation_test_then_forward_shadow"
    cost_policy: str = "next_open_adjusted_cost_aware"
    required_horizons: tuple[int, ...] = FACTOR_SHADOW_HORIZONS
    minimum_matured_runs_per_horizon: int = FACTOR_SHADOW_PROMOTION_MIN_MATURED_RUNS
    minimum_outcome_coverage: float = FACTOR_SHADOW_PROMOTION_MIN_OUTCOME_COVERAGE
    minimum_session_edge_rate: float = FACTOR_SHADOW_PROMOTION_MIN_SESSION_EDGE_RATE
    median_session_net_excess_must_be_positive: Literal[True] = True
    challenger_rank_ic_must_exceed_baseline: Literal[True] = True
    minimum_selection_lift_rate: float = FACTOR_SHADOW_PROMOTION_MIN_SELECTION_LIFT_RATE
    median_selection_lift_must_be_positive: Literal[True] = True
    minimum_execution_head_paired_sessions: int = FACTOR_SHADOW_PROMOTION_MIN_MATURED_RUNS
    execution_head_all_matured_sessions_must_be_filled: Literal[True] = True
    execution_head_max_industry_positions: int = FACTOR_SHADOW_EXECUTION_HEAD_INDUSTRY_CAP
    minimum_execution_head_lift_rate: float = FACTOR_SHADOW_PROMOTION_MIN_SELECTION_LIFT_RATE
    median_execution_head_lift_must_be_positive: Literal[True] = True
    promotion_effect: Literal["manual_review_only"] = "manual_review_only"


class FactorCandidate(BaseModel):
    candidate_id: str
    family: FactorCandidateFamily
    label: str
    hypothesis: str
    state: FactorCandidateState
    availability_label: str
    required_features: tuple[str, ...]
    available_features: tuple[str, ...] = ()
    missing_features: tuple[str, ...] = ()
    data_basis: str
    limitation: str
    data_coverage_status: Literal["unverified"] = "unverified"
    experiment_start_allowed: Literal[False] = False
    evidence_policy: FactorCandidateEvidencePolicy = Field(
        default_factory=FactorCandidateEvidencePolicy
    )
    scope: Literal["research_shadow"] = "research_shadow"
    decision_weight: Literal[False] = False
    production_ranking_effect: Literal["none"] = "none"
    paper_order_effect: Literal["none"] = "none"


class FactorCandidateQueue(BaseModel):
    schema_version: str = FACTOR_CANDIDATE_QUEUE_SCHEMA
    feature_set_version: str = FACTOR_RESEARCH_VERSION
    candidates: list[FactorCandidate]
    warnings: list[str] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)
    scope: Literal["research_shadow"] = "research_shadow"
    decision_weight: Literal[False] = False
    production_ranking_effect: Literal["none"] = "none"
    paper_order_effect: Literal["none"] = "none"

    def contract_available_candidates(self) -> list[FactorCandidate]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.state == "contract_available_for_shadow_design"
        ]


class _CandidateDefinition(BaseModel):
    candidate_id: str
    family: FactorCandidateFamily
    label: str
    hypothesis: str
    required_features: tuple[str, ...]
    data_basis: str
    limitation: str
    future_capability: bool = False


_CANDIDATE_DEFINITIONS: tuple[_CandidateDefinition, ...] = (
    _CandidateDefinition(
        candidate_id="quality-profitability-level-v1",
        family="quality",
        label="Quality and profitability level",
        hypothesis="Higher ROE and gross margin identify durable operating quality.",
        required_features=("return_on_equity", "gross_margin"),
        data_basis="Point-in-time fundamental snapshots already feed the frozen factor dataset.",
        limitation="Measures reported profitability level, not accounting-quality or cash-flow quality.",
    ),
    _CandidateDefinition(
        candidate_id="profitability-growth-confirmation-v1",
        family="profitability_improvement",
        label="Profitability growth confirmation",
        hypothesis="Concurrent earnings and revenue growth is stronger than either growth signal alone.",
        required_features=("earnings_growth", "revenue_growth"),
        data_basis="Point-in-time earnings-growth and revenue-growth fields exist in the factor dataset.",
        limitation="Growth rates do not prove sequential margin expansion or estimate revisions.",
    ),
    _CandidateDefinition(
        candidate_id="turnover-volume-strength-v1",
        family="capital_strength",
        label="Turnover and volume strength proxy",
        hypothesis="Sustained turnover with recent volume expansion can confirm participation strength.",
        required_features=("turnover_log_20", "volume_ratio_5_20"),
        data_basis="Adjusted bar history provides point-in-time turnover and volume features.",
        limitation="This is a trading-activity proxy, not main-fund or northbound net flow.",
    ),
    _CandidateDefinition(
        candidate_id="trend-health-composite-v1",
        family="trend_health",
        label="Trend health composite",
        hypothesis="Persistent trends with controlled downside risk are healthier than raw momentum.",
        required_features=(
            "momentum_20",
            "trend_slope_60",
            "trend_r2_60",
            "downside_risk_60",
            "max_drawdown_60",
        ),
        data_basis="Adjusted close history already supplies momentum, regression and downside features.",
        limitation="Price-derived health does not establish a fundamental catalyst.",
    ),
    _CandidateDefinition(
        candidate_id="valuation-growth-fit-v1",
        family="valuation_growth_match",
        label="Valuation and growth fit",
        hypothesis="Earnings yield is more informative when revenue and earnings growth agree.",
        required_features=("earnings_yield", "earnings_growth", "revenue_growth"),
        data_basis="The frozen research contract combines point-in-time PE-derived yield and growth.",
        limitation="Does not use latest-only PEG as historical evidence or infer a missing valuation.",
    ),
    _CandidateDefinition(
        candidate_id="point-in-time-catalyst-v1",
        family="catalyst",
        label="Point-in-time catalyst confirmation",
        hypothesis="A dated catalyst may improve the timing of otherwise qualified signals.",
        required_features=("point_in_time_catalyst_event",),
        data_basis="No catalyst feature is present in the frozen factor research contract.",
        limitation="Requires archived, deduplicated, point-in-time event data before any experiment.",
        future_capability=True,
    ),
)


def build_factor_candidate_queue(
    available_features: tuple[str, ...] | list[str] | set[str] = FEATURE_COLUMNS,
) -> FactorCandidateQueue:
    """Enumerate data-gated factor ideas without running or activating them.

    The result is a preregistration queue only. It performs no storage access,
    scoring, ranking, paper-trading mutation or automatic promotion.
    """

    available = frozenset(available_features)
    candidates: list[FactorCandidate] = []
    for definition in _CANDIDATE_DEFINITIONS:
        present = tuple(feature for feature in definition.required_features if feature in available)
        missing = tuple(feature for feature in definition.required_features if feature not in available)
        if definition.future_capability:
            state: FactorCandidateState = "future_capability"
            present = ()
            missing = definition.required_features
        elif missing:
            state = "unavailable"
        else:
            state = "contract_available_for_shadow_design"
        availability_label = {
            "contract_available_for_shadow_design": (
                "Contract available for shadow design; coverage unverified; experiment blocked"
            ),
            "unavailable": "Required contract fields unavailable; experiment blocked",
            "future_capability": "Future capability; experiment blocked",
        }[state]
        candidates.append(
            FactorCandidate(
                candidate_id=definition.candidate_id,
                family=definition.family,
                label=definition.label,
                hypothesis=definition.hypothesis,
                state=state,
                availability_label=availability_label,
                required_features=definition.required_features,
                available_features=present,
                missing_features=missing,
                data_basis=definition.data_basis,
                limitation=definition.limitation,
            )
        )

    counts = {
        state: sum(candidate.state == state for candidate in candidates)
        for state in (
            "contract_available_for_shadow_design",
            "unavailable",
            "future_capability",
        )
    }
    return FactorCandidateQueue(
        candidates=candidates,
        warnings=[
            "候选队列只用于研究影子实验，不能改变生产排序、模拟盘权重或下单。",
            "contract available 只允许设计实验；覆盖率未验证，不能据此启动实验。",
            "资金强度仅使用成交额与成交量代理；催化剂数据能力当前不可用。",
        ],
        data_health={
            "factor_candidate_queue_scope": "research_shadow",
            "factor_candidate_queue_source": "frozen_feature_contract",
            "factor_candidate_queue_candidates": str(len(candidates)),
            "factor_candidate_queue_contract_available": str(
                counts["contract_available_for_shadow_design"]
            ),
            "factor_candidate_queue_unavailable": str(counts["unavailable"]),
            "factor_candidate_queue_future_capability": str(counts["future_capability"]),
            "factor_candidate_queue_data_coverage_status": "unverified",
            "factor_candidate_queue_experiment_start_allowed": "false",
            "factor_candidate_queue_gate_policy_completeness": "partial",
            "factor_candidate_queue_decision_weight": "false",
            "factor_candidate_queue_paper_isolation": "true",
            "factor_candidate_queue_order_effect": "none",
        },
    )


def get_explicit_shadow_candidate(candidate_id: str) -> FactorCandidate:
    """Resolve the small, server-owned candidate allowlist.

    Feature lists are deliberately never accepted from an API caller. A queue
    entry can describe future research without becoming executable here.
    """

    normalized = candidate_id.strip()
    candidate = next(
        (
            item
            for item in build_factor_candidate_queue().candidates
            if item.candidate_id == normalized
        ),
        None,
    )
    if candidate is None:
        raise ValueError(f"unknown factor shadow candidate {normalized!r}")
    if normalized not in EXPLICIT_SHADOW_CANDIDATE_IDS:
        raise ValueError(f"factor shadow candidate {normalized!r} is not approved for launch")
    if candidate.state != "contract_available_for_shadow_design":
        raise ValueError(f"factor shadow candidate {normalized!r} is not contract-available")
    return candidate
