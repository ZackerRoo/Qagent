from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class StrategyDefinition(BaseModel):
    strategy_id: str
    name: str
    family: str
    role: str
    horizon: str
    description: str
    required_data: list[str]
    optional_data: list[str] = Field(default_factory=list)
    free_data_ready: bool = True
    invalidation_template: str


class StrategyEvaluation(BaseModel):
    strategy_id: str
    name: str
    family: str
    role: str
    status: str
    score: float = Field(ge=0.0, le=1.0)
    horizon: str
    preconditions: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    confirmations: list[str] = Field(default_factory=list)
    invalidation: str
    evidence: dict[str, object] = Field(default_factory=dict)
    score_components: dict[str, float] = Field(default_factory=dict)
    missing_data: list[str] = Field(default_factory=list)
    data_requirements: list[str] = Field(default_factory=list)


class StrategyHealthPoint(BaseModel):
    label: str
    sample_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    max_loss_10d: float | None = None


class StrategyHealth(BaseModel):
    strategy_id: str
    name: str
    family: str
    readiness: str
    sample_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    max_loss_10d: float | None = None
    missing_data: list[str] = Field(default_factory=list)
    curve: list[StrategyHealthPoint] = Field(default_factory=list)


class StrategyState(StrEnum):
    RESEARCH = "research"
    SHADOW = "shadow"
    ADMITTED = "admitted"
    THROTTLED = "throttled"
    DISABLED = "disabled"


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT = "insufficient"


class BreachSeverity(StrEnum):
    NONE = "none"
    SOFT = "soft"
    HARD = "hard"


class GovernanceAction(StrEnum):
    HOLD = "hold"
    THROTTLE = "throttle"
    DISABLE = "disable"
    ROLLBACK = "rollback"


class OutOfSampleMetrics(BaseModel):
    """Point-in-time metrics consumed by strategy governance.

    Percentage fields use percentage points, matching the walk-forward models
    (for example ``1.2`` means 1.2% and drawdowns are negative).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    mean_return_pct: float | None = Field(default=None, allow_inf_nan=False)
    confidence_low_pct: float | None = Field(default=None, allow_inf_nan=False)
    confidence_high_pct: float | None = Field(default=None, allow_inf_nan=False)
    positive_edge_p_value: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    negative_edge_p_value: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    false_discovery_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    benchmark_excess_return_pct: float | None = Field(default=None, allow_inf_nan=False)
    cost_stress_return_pct: float | None = Field(default=None, allow_inf_nan=False)
    max_drawdown_pct: float | None = Field(default=None, allow_inf_nan=False)
    win_rate: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    profit_factor: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    regime_pass_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    turnover_pct: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    consecutive_failed_windows: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_confidence_interval(self) -> Self:
        if (
            self.confidence_low_pct is not None
            and self.confidence_high_pct is not None
            and self.confidence_low_pct > self.confidence_high_pct
        ):
            raise ValueError("confidence_low_pct must not exceed confidence_high_pct")
        return self

    @property
    def out_of_sample_count(self) -> int:
        return self.sample_count

    @property
    def statistical_cluster_count(self) -> int:
        return self.cluster_count

    @property
    def average_return_pct(self) -> float | None:
        return self.mean_return_pct


class OutOfSampleGatePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_sample_count: int = Field(default=30, gt=0)
    min_cluster_count: int = Field(default=10, gt=0)
    min_mean_return_pct: float = Field(default=0.0, allow_inf_nan=False)
    min_confidence_low_pct: float = Field(default=0.0, allow_inf_nan=False)
    max_positive_edge_p_value: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    max_false_discovery_rate: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    min_benchmark_excess_return_pct: float | None = Field(
        default=0.0,
        allow_inf_nan=False,
    )
    min_cost_stress_return_pct: float | None = Field(default=0.0, allow_inf_nan=False)
    drawdown_floor_pct: float | None = Field(default=-15.0, allow_inf_nan=False)
    min_win_rate: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    min_profit_factor: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    min_regime_pass_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    max_turnover_pct: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)


class BreachPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    soft_mean_return_floor_pct: float = Field(default=0.0, allow_inf_nan=False)
    hard_mean_return_floor_pct: float = Field(default=-1.0, allow_inf_nan=False)
    soft_cost_stress_floor_pct: float = Field(default=0.0, allow_inf_nan=False)
    hard_cost_stress_floor_pct: float = Field(default=-2.0, allow_inf_nan=False)
    soft_drawdown_floor_pct: float = Field(default=-15.0, allow_inf_nan=False)
    hard_drawdown_floor_pct: float = Field(default=-25.0, allow_inf_nan=False)
    hard_negative_edge_p_value: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    hard_consecutive_failed_windows: int = Field(default=2, gt=0)
    throttle_multiplier: float = Field(default=0.5, ge=0.0, lt=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> Self:
        ordered_pairs = (
            (
                self.hard_mean_return_floor_pct,
                self.soft_mean_return_floor_pct,
                "mean return",
            ),
            (
                self.hard_cost_stress_floor_pct,
                self.soft_cost_stress_floor_pct,
                "cost stress return",
            ),
            (
                self.hard_drawdown_floor_pct,
                self.soft_drawdown_floor_pct,
                "drawdown",
            ),
        )
        for hard, soft, label in ordered_pairs:
            if hard > soft:
                raise ValueError(f"hard {label} floor must not exceed soft floor")
        return self


class StrategyPolicy(BaseModel):
    """Immutable, fully versioned policy snapshot for one strategy."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    strategy_id: str = Field(min_length=1)
    policy_version: str = Field(
        min_length=1,
        validation_alias=AliasChoices("policy_version", "version"),
    )
    strategy_version: str = Field(default="legacy", min_length=1)
    factor_version: str = Field(default="legacy", min_length=1)
    parameter_version: str = Field(default="legacy", min_length=1)
    universe_version: str = Field(default="legacy", min_length=1)
    data_revision: int | str = "unversioned"
    state: StrategyState = StrategyState.RESEARCH
    base_weight: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
        validation_alias=AliasChoices("base_weight", "weight"),
    )
    rollback_policy_version: str | None = Field(
        default=None,
        validation_alias=AliasChoices("rollback_policy_version", "previous_policy_version"),
    )
    oos_gate: OutOfSampleGatePolicy = Field(default_factory=OutOfSampleGatePolicy)
    breach_policy: BreachPolicy = Field(default_factory=BreachPolicy)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        version_fields = (
            "strategy_id",
            "policy_version",
            "strategy_version",
            "factor_version",
            "parameter_version",
            "universe_version",
        )
        for field_name in version_fields:
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.rollback_policy_version is not None:
            rollback_version = self.rollback_policy_version.strip()
            if not rollback_version:
                raise ValueError("rollback_policy_version must not be blank")
            if rollback_version == self.policy_version:
                raise ValueError("rollback policy must differ from current policy")
        return self

    @property
    def version(self) -> str:
        return self.policy_version

    @property
    def weight(self) -> float:
        return self.base_weight

    @property
    def previous_policy_version(self) -> str | None:
        return self.rollback_policy_version


class StateTransitionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    from_state: StrategyState
    to_state: StrategyState
    effective_state: StrategyState
    reason: str

    @property
    def current_state(self) -> StrategyState:
        return self.from_state

    @property
    def target_state(self) -> StrategyState:
        return self.to_state

    @property
    def next_state(self) -> StrategyState:
        return self.effective_state


class MetricGateCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    status: GateStatus
    passed: bool
    actual: float | int | None
    requirement: str
    reason: str


class OutOfSampleGateDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    policy_version: str
    status: GateStatus
    passed: bool
    checks: tuple[MetricGateCheck, ...]
    reason: str


class AdmissionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    policy_version: str
    admitted: bool
    from_state: StrategyState
    to_state: StrategyState
    gate: OutOfSampleGateDecision
    reason: str

    @property
    def approved(self) -> bool:
        return self.admitted

    @property
    def target_state(self) -> StrategyState:
        return self.to_state


class PolicyViolation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    severity: BreachSeverity
    actual: float | int
    threshold: float | int
    reason: str


class BreachAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: BreachSeverity
    evaluable: bool
    violations: tuple[PolicyViolation, ...]
    reason: str

    @property
    def breached(self) -> bool:
        return self.severity is not BreachSeverity.NONE

    @property
    def level(self) -> BreachSeverity:
        return self.severity


class RollbackDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    current_policy_version: str
    action: GovernanceAction
    from_state: StrategyState
    to_state: StrategyState
    breach: BreachAssessment
    rollback_required: bool
    disable_current_policy: bool
    rollback_to_policy_version: str | None = None
    effective_weight: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    reason: str

    @property
    def target_state(self) -> StrategyState:
        return self.to_state

    @property
    def target_policy_version(self) -> str | None:
        return self.rollback_to_policy_version


StrategyGovernanceState = StrategyState
StrategyLifecycleState = StrategyState
VersionedStrategyPolicy = StrategyPolicy
OOSMetrics = OutOfSampleMetrics
