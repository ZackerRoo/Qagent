from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class EarningsEvent(BaseModel):
    instrument_id: str
    announcement_date: date
    announcement_time: str
    actual_eps: Decimal | None = None
    estimated_eps: Decimal | None = None
    actual_revenue: Decimal | None = None
    estimated_revenue: Decimal | None = None
    guidance: str | None = None
    provider: str = "unknown"

    @property
    def has_pead_inputs(self) -> bool:
        return (
            self.actual_eps is not None
            and self.estimated_eps is not None
            and self.actual_revenue is not None
            and self.estimated_revenue is not None
            and self.announcement_time in {"bmo", "amc", "intraday"}
        )


class FilingEvent(BaseModel):
    instrument_id: str
    form: str
    filing_date: date
    accession_number: str
    report_date: date | None = None
    primary_document: str | None = None
    filing_url: str | None = None
    provider: str = "unknown"


class AnnouncementEvent(BaseModel):
    instrument_id: str
    title: str
    published_at: date
    url: str | None = None
    category: str | None = None
    provider: str = "unknown"
