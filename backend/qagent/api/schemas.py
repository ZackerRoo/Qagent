from pydantic import BaseModel


class AgentQueryRequest(BaseModel):
    question: str
    instrument_id: str | None = None
    provider: str = "fixture"
    symbols: str | None = None


class AgentQueryResponse(BaseModel):
    answer: str


class AlertEvaluationRequest(BaseModel):
    prices: dict[str, str]
