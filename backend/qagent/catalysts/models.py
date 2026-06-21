from datetime import datetime

from pydantic import BaseModel


class NewsItem(BaseModel):
    news_id: str
    instrument_id: str
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    url: str | None = None
    source: str


class CatalystHypothesis(BaseModel):
    instrument_id: str
    news_id: str
    title: str
    catalyst_type: str
    investment_hypothesis: str
    verification_path: str
    confidence: float
