from __future__ import annotations

from pydantic import BaseModel, Field

from qagent.domain.models import PortfolioAllocation, PortfolioPlan


class PortfolioConstraintResult(BaseModel):
    """Auditable decision emitted for every input portfolio candidate."""

    candidate_index: int = Field(ge=0)
    candidate_id: str | None = None
    instrument_id: str
    action: str
    accepted: bool
    target_weight: float = Field(ge=0.0)
    risk_budget: float = Field(ge=0.0)
    constraint_codes: list[str] = Field(default_factory=list)
    requested_weight: float = 0.0
    requested_risk_budget: float = 0.0
    industry: str | None = None
    themes: list[str] = Field(default_factory=list)
    asset_type: str = "stock"
    market_state: str = "neutral"
    market_state_multiplier: float = Field(ge=0.0)


class PortfolioConstraintPolicyAudit(BaseModel):
    """Resolved policy snapshot used to produce constraint results."""

    admitted_actions: list[str] = Field(default_factory=list)
    observation_actions: list[str] = Field(default_factory=list)
    max_positions: int = Field(ge=0)
    max_single_position_pct: float = Field(ge=0.0)
    total_risk_budget_pct: float = Field(ge=0.0)
    effective_risk_budget_pct: float = Field(ge=0.0)
    min_cash_reserve_pct: float = Field(ge=0.0, le=100.0)
    max_invested_weight_pct: float = Field(ge=0.0, le=100.0)
    max_industry_positions: int | None = Field(default=None, ge=0)
    max_industry_weight_pct: float | None = Field(default=None, ge=0.0)
    max_same_theme_positions: int | None = Field(default=None, ge=0)
    max_theme_weight_pct: float | None = Field(default=None, ge=0.0)
    max_etf_overlap_positions: int | None = Field(default=None, ge=0)
    max_etf_overlap_weight_pct: float | None = Field(default=None, ge=0.0)
    market_state: str
    market_state_multiplier: float = Field(ge=0.0)


class ConstrainedPortfolioAllocation(PortfolioAllocation):
    """Backward-compatible allocation with its constraint decision attached."""

    accepted: bool
    target_weight: float = Field(ge=0.0)
    risk_budget: float = Field(ge=0.0)
    constraint_codes: list[str] = Field(default_factory=list)


class ConstrainedPortfolioPlan(PortfolioPlan):
    """Portfolio plan retaining legacy fields and complete constraint audit data."""

    allocations: list[ConstrainedPortfolioAllocation] = Field(default_factory=list)
    watchlist: list[ConstrainedPortfolioAllocation] = Field(default_factory=list)
    allocated_risk_budget_pct: float = Field(default=0.0, ge=0.0)
    cash_reserve_pct: float = Field(default=100.0, ge=0.0, le=100.0)
    constraint_blocked_count: int = Field(default=0, ge=0)
    constraint_policy: PortfolioConstraintPolicyAudit
    constraint_results: list[PortfolioConstraintResult] = Field(default_factory=list)


AuditablePortfolioAllocation = ConstrainedPortfolioAllocation
AuditablePortfolioPlan = ConstrainedPortfolioPlan
