from datetime import datetime

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    news_id: str
    instrument_id: str
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    url: str | None = None
    source: str


class CatalystBeneficiaryLink(BaseModel):
    name: str
    chain_role: str
    benefit_order: str
    demand_driver: str
    evidence_required: str


class CatalystFinancialTransmission(BaseModel):
    line_item: str
    mechanism: str
    margin_effect: str
    reporting_lag: str
    confidence: float


class CatalystHypothesis(BaseModel):
    instrument_id: str
    news_id: str
    title: str
    catalyst_type: str
    investment_hypothesis: str
    verification_path: str
    confidence: float
    source: str = "unknown"
    published_at: datetime | None = None
    observed_facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    demand_translation: str = "Demand translation is not established."
    beneficiary_chain: list[CatalystBeneficiaryLink] = Field(default_factory=list)
    financial_transmission: list[CatalystFinancialTransmission] = Field(default_factory=list)
    priced_in_assessment: str = "unknown_without_price_and_consensus_context"
    evidence_to_watch: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    invalidation_triggers: list[str] = Field(default_factory=list)
    research_status: str = "hypothesis_only"
    decision_effect: str = "none"
