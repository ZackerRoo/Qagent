from pydantic import BaseModel, Field


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
