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


class FundamentalSnapshot(BaseModel):
    instrument_id: str
    as_of_date: date
    revenue_growth_pct: Decimal | None = None
    earnings_growth_pct: Decimal | None = None
    gross_margin_pct: Decimal | None = None
    operating_margin_pct: Decimal | None = None
    net_margin_pct: Decimal | None = None
    return_on_equity_pct: Decimal | None = None
    market_cap: Decimal | None = None
    pe_ratio: Decimal | None = None
    forward_pe: Decimal | None = None
    peg_ratio: Decimal | None = None
    price_to_sales: Decimal | None = None
    provider: str = "unknown"

    @property
    def has_growth_inputs(self) -> bool:
        return self.revenue_growth_pct is not None or self.earnings_growth_pct is not None

    @property
    def has_valuation_inputs(self) -> bool:
        return self.pe_ratio is not None or self.forward_pe is not None or self.peg_ratio is not None


class AnalystInsight(BaseModel):
    instrument_id: str
    as_of_date: date
    revision_date: date | None = None
    current_eps_estimate: Decimal | None = None
    prior_eps_estimate: Decimal | None = None
    current_revenue_estimate: Decimal | None = None
    prior_revenue_estimate: Decimal | None = None
    target_price: Decimal | None = None
    prior_target_price: Decimal | None = None
    current_price: Decimal | None = None
    buy_count: int = 0
    hold_count: int = 0
    sell_count: int = 0
    strong_buy_count: int = 0
    strong_sell_count: int = 0
    provider: str = "unknown"

    @property
    def target_upside_pct(self) -> Decimal | None:
        if self.target_price is None or self.current_price is None or self.current_price == 0:
            return None
        return ((self.target_price / self.current_price) - Decimal("1")) * Decimal("100")

    @property
    def total_ratings(self) -> int:
        return (
            self.strong_buy_count
            + self.buy_count
            + self.hold_count
            + self.sell_count
            + self.strong_sell_count
        )

    @property
    def bullish_rating_ratio(self) -> Decimal | None:
        total = self.total_ratings
        if total == 0:
            return None
        bullish = self.strong_buy_count + self.buy_count
        return Decimal(bullish) / Decimal(total)

    @property
    def eps_revision_pct(self) -> Decimal | None:
        return _revision_pct(self.current_eps_estimate, self.prior_eps_estimate)

    @property
    def revenue_revision_pct(self) -> Decimal | None:
        return _revision_pct(self.current_revenue_estimate, self.prior_revenue_estimate)

    @property
    def target_revision_pct(self) -> Decimal | None:
        return _revision_pct(self.target_price, self.prior_target_price)

    @property
    def has_revision_inputs(self) -> bool:
        return self.revision_date is not None and any(
            value is not None
            for value in [self.eps_revision_pct, self.revenue_revision_pct, self.target_revision_pct]
        )


def _revision_pct(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    if current is None or prior is None or prior == 0:
        return None
    return ((current / abs(prior)) - Decimal("1")) * Decimal("100")
