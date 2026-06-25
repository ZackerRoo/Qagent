from pydantic import BaseModel, Field


class FactorExposure(BaseModel):
    factor_id: str
    label: str
    raw_value: float | None = None
    score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0)
    explanation: str


class FactorRanking(BaseModel):
    instrument_id: str
    instrument_label: str | None = None
    factor_score: float = Field(ge=0.0, le=1.0)
    factor_rank: int
    percentile: float = Field(ge=0.0, le=1.0)
    momentum_score: float = Field(ge=0.0, le=1.0)
    trend_quality_score: float = Field(ge=0.0, le=1.0)
    liquidity_score: float = Field(ge=0.0, le=1.0)
    low_risk_score: float = Field(ge=0.0, le=1.0)
    reversal_score: float = Field(ge=0.0, le=1.0)
    execution_penalty: float = Field(ge=0.0, le=1.0)
    data_completeness: float = Field(ge=0.0, le=1.0)
    factor_exposures: list[FactorExposure]
    flags: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
