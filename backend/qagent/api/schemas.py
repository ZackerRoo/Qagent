from pydantic import BaseModel


class AgentQueryRequest(BaseModel):
    question: str
    instrument_id: str | None = None


class AgentQueryResponse(BaseModel):
    answer: str
