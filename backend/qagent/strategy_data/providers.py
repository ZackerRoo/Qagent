from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import httpx
import pandas as pd

from qagent.config import Settings, get_settings
from qagent.strategy_data.models import AnnouncementEvent, EarningsEvent, FilingEvent


class StrategyDataProvider(Protocol):
    name: str
    last_errors: list[str]

    def get_earnings_events(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        ...

    def get_filings(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FilingEvent]:
        ...

    def get_announcements(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnnouncementEvent]:
        ...


class BaseStrategyDataProvider:
    name = "base_strategy_data"

    def __init__(self):
        self.last_errors: list[str] = []

    def get_earnings_events(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        return []

    def get_filings(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FilingEvent]:
        return []

    def get_announcements(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnnouncementEvent]:
        return []


class FixtureStrategyDataProvider(BaseStrategyDataProvider):
    name = "fixture_strategy_data"

    def __init__(self, fixture_dir: Path | None = None):
        super().__init__()
        self.fixture_dir = fixture_dir or Path(__file__).parents[2] / "tests" / "fixtures"

    def get_earnings_events(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        path = self.fixture_dir / "earnings_events.csv"
        if not path.exists():
            return []

        frame = pd.read_csv(path, parse_dates=["announcement_date"])
        frame["announcement_date"] = frame["announcement_date"].dt.date
        mask = (
            frame["instrument_id"].isin(instrument_ids)
            & (frame["announcement_date"] >= start)
            & (frame["announcement_date"] <= end)
        )
        events = []
        for row in frame.loc[mask].to_dict("records"):
            events.append(
                EarningsEvent(
                    instrument_id=str(row["instrument_id"]),
                    announcement_date=row["announcement_date"],
                    announcement_time=_normalize_announcement_time(row.get("announcement_time")),
                    actual_eps=_decimal_or_none(row.get("actual_eps")),
                    estimated_eps=_decimal_or_none(row.get("estimated_eps")),
                    actual_revenue=_decimal_or_none(row.get("actual_revenue")),
                    estimated_revenue=_decimal_or_none(row.get("estimated_revenue")),
                    guidance=_string_or_none(row.get("guidance")),
                    provider=self.name,
                )
            )
        return events


class EmptyStrategyDataProvider(BaseStrategyDataProvider):
    name = "empty_strategy_data"


class FmpStrategyDataProvider(BaseStrategyDataProvider):
    name = "fmp"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        base_url: str = "https://financialmodelingprep.com",
    ):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=12)

    def get_earnings_events(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        events: list[EarningsEvent] = []
        for instrument_id in _us_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            try:
                response = self.client.get(
                    f"{self.base_url}/stable/earnings-calendar",
                    params={
                        "symbol": symbol,
                        "from": start.isoformat(),
                        "to": end.isoformat(),
                        "apikey": self.api_key,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # pragma: no cover - exact httpx subclasses are not important here
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue

            rows = payload if isinstance(payload, list) else payload.get("historical", [])
            for row in rows:
                event = _fmp_earnings_event(instrument_id, row)
                if event and start <= event.announcement_date <= end:
                    events.append(event)
        return events


class FinnhubStrategyDataProvider(BaseStrategyDataProvider):
    name = "finnhub"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        base_url: str = "https://finnhub.io/api/v1",
    ):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=12)

    def get_earnings_events(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        events: list[EarningsEvent] = []
        for instrument_id in _us_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            try:
                response = self.client.get(
                    f"{self.base_url}/calendar/earnings",
                    params={
                        "symbol": symbol,
                        "from": start.isoformat(),
                        "to": end.isoformat(),
                        "token": self.api_key,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue

            for row in payload.get("earningsCalendar", []):
                event = _finnhub_earnings_event(instrument_id, row)
                if event and start <= event.announcement_date <= end:
                    events.append(event)
        return events


class SecEdgarStrategyDataProvider(BaseStrategyDataProvider):
    name = "sec_edgar"

    def __init__(
        self,
        cik_lookup: dict[str, str] | None = None,
        client: httpx.Client | None = None,
        user_agent: str = "Qagent research app contact@example.com",
        base_url: str = "https://data.sec.gov/submissions",
        ticker_lookup_url: str = "https://www.sec.gov/files/company_tickers.json",
    ):
        super().__init__()
        self.cik_lookup = cik_lookup or {}
        self.client = client or httpx.Client(
            timeout=12,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        )
        self.base_url = base_url.rstrip("/")
        self.ticker_lookup_url = ticker_lookup_url

    def get_filings(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FilingEvent]:
        filings: list[FilingEvent] = []
        for instrument_id in _us_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            raw_cik = self._resolve_cik(symbol)
            cik = _normalize_cik(raw_cik)
            if not cik:
                continue
            try:
                response = self.client.get(f"{self.base_url}/CIK{cik}.json")
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue
            filings.extend(_sec_filings(instrument_id, cik, payload, start, end))
        return filings

    def _resolve_cik(self, symbol: str) -> str | None:
        if symbol.isdigit():
            return symbol
        existing = self.cik_lookup.get(symbol)
        if existing:
            return existing
        try:
            response = self.client.get(self.ticker_lookup_url)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # pragma: no cover
            self.last_errors.append(f"ticker_lookup: {exc}")
            return None
        for item in payload.values():
            if str(item.get("ticker", "")).upper() == symbol.upper():
                cik = str(item.get("cik_str"))
                self.cik_lookup[symbol] = cik
                return cik
        return None


class CninfoAnnouncementProvider(BaseStrategyDataProvider):
    name = "cninfo"

    def __init__(
        self,
        client: httpx.Client | None = None,
        base_url: str = "https://www.cninfo.com.cn",
    ):
        super().__init__()
        self.client = client or httpx.Client(timeout=12)
        self.base_url = base_url.rstrip("/")

    def get_announcements(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnnouncementEvent]:
        announcements: list[AnnouncementEvent] = []
        for instrument_id in _cn_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            try:
                response = self.client.post(
                    f"{self.base_url}/new/hisAnnouncement/query",
                    data={
                        "stock": symbol,
                        "searchkey": "",
                        "plate": "szse" if symbol.startswith(("0", "3")) else "sse",
                        "category": "",
                        "trade": "",
                        "column": "szse" if symbol.startswith(("0", "3")) else "sse",
                        "columnTitle": "历史公告查询",
                        "pageNum": 1,
                        "pageSize": 30,
                        "tabName": "fulltext",
                        "seDate": f"{start.isoformat()}~{end.isoformat()}",
                    },
                    headers={"Referer": "https://www.cninfo.com.cn/"},
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue

            for row in payload.get("announcements", []):
                event = _cninfo_announcement(instrument_id, row, self.base_url)
                if event and start <= event.published_at <= end:
                    announcements.append(event)
        return announcements


class TushareStrategyDataProvider(BaseStrategyDataProvider):
    name = "tushare"

    def __init__(self, token: str):
        super().__init__()
        self.token = token


class CompositeStrategyDataProvider(BaseStrategyDataProvider):
    def __init__(self, providers: list[StrategyDataProvider]):
        super().__init__()
        self.providers = providers
        self.name = "composite_strategy_data(" + ",".join(provider.name for provider in providers) + ")"

    def get_earnings_events(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        return self._collect("get_earnings_events", instrument_ids, start, end)

    def get_filings(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FilingEvent]:
        return self._collect("get_filings", instrument_ids, start, end)

    def get_announcements(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnnouncementEvent]:
        return self._collect("get_announcements", instrument_ids, start, end)

    def _collect(self, method_name: str, instrument_ids: list[str], start: date, end: date):
        rows = []
        self.last_errors = []
        for provider in self.providers:
            try:
                rows.extend(getattr(provider, method_name)(instrument_ids, start, end))
            except Exception as exc:
                self.last_errors.append(f"{provider.name}.{method_name}: {exc}")
            self.last_errors.extend(getattr(provider, "last_errors", []))
        return rows


def build_strategy_data_provider(
    mode: str,
    settings: Settings | None = None,
) -> StrategyDataProvider:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "fixture":
        return FixtureStrategyDataProvider()

    settings = settings or get_settings()
    providers: list[StrategyDataProvider] = [SecEdgarStrategyDataProvider(user_agent=settings.sec_user_agent)]
    if settings.fmp_api_key:
        providers.append(FmpStrategyDataProvider(settings.fmp_api_key))
    if settings.finnhub_api_key:
        providers.append(FinnhubStrategyDataProvider(settings.finnhub_api_key))
    providers.append(CninfoAnnouncementProvider())
    if settings.tushare_token:
        providers.append(TushareStrategyDataProvider(settings.tushare_token))

    if normalized_mode in {"free", "development"}:
        return CompositeStrategyDataProvider(providers)
    return EmptyStrategyDataProvider()


def _fmp_earnings_event(instrument_id: str, row: dict[str, object]) -> EarningsEvent | None:
    event_date = _date_or_none(row.get("date") or row.get("fiscalDateEnding"))
    if event_date is None:
        return None
    return EarningsEvent(
        instrument_id=instrument_id,
        announcement_date=event_date,
        announcement_time=_normalize_announcement_time(row.get("time") or row.get("hour")),
        actual_eps=_decimal_or_none(row.get("epsActual") or row.get("actualEarningResult")),
        estimated_eps=_decimal_or_none(row.get("epsEstimated") or row.get("estimatedEarning")),
        actual_revenue=_decimal_or_none(row.get("revenueActual") or row.get("actualRevenue")),
        estimated_revenue=_decimal_or_none(row.get("revenueEstimated") or row.get("estimatedRevenue")),
        guidance=_string_or_none(row.get("guidance")),
        provider=FmpStrategyDataProvider.name,
    )


def _finnhub_earnings_event(instrument_id: str, row: dict[str, object]) -> EarningsEvent | None:
    event_date = _date_or_none(row.get("date"))
    if event_date is None:
        return None
    return EarningsEvent(
        instrument_id=instrument_id,
        announcement_date=event_date,
        announcement_time=_normalize_announcement_time(row.get("hour") or row.get("time")),
        actual_eps=_decimal_or_none(row.get("epsActual")),
        estimated_eps=_decimal_or_none(row.get("epsEstimate") or row.get("epsEstimated")),
        actual_revenue=_decimal_or_none(row.get("revenueActual")),
        estimated_revenue=_decimal_or_none(row.get("revenueEstimate") or row.get("revenueEstimated")),
        guidance=_string_or_none(row.get("guidance")),
        provider=FinnhubStrategyDataProvider.name,
    )


def _sec_filings(
    instrument_id: str,
    cik: str,
    payload: dict[str, object],
    start: date,
    end: date,
) -> list[FilingEvent]:
    recent = payload.get("filings", {}).get("recent", {})
    accession_numbers = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_documents = recent.get("primaryDocument", [])
    filings: list[FilingEvent] = []
    for index, accession_number in enumerate(accession_numbers):
        filing_date = _date_or_none(_at(filing_dates, index))
        if filing_date is None or not (start <= filing_date <= end):
            continue
        primary_document = _string_or_none(_at(primary_documents, index))
        accession_compact = str(accession_number).replace("-", "")
        filing_url = None
        if primary_document:
            filing_url = (
                "https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{accession_compact}/{primary_document}"
            )
        filings.append(
            FilingEvent(
                instrument_id=instrument_id,
                form=str(_at(forms, index) or ""),
                filing_date=filing_date,
                report_date=_date_or_none(_at(report_dates, index)),
                accession_number=str(accession_number),
                primary_document=primary_document,
                filing_url=filing_url,
                provider=SecEdgarStrategyDataProvider.name,
            )
        )
    return filings


def _cninfo_announcement(
    instrument_id: str,
    row: dict[str, object],
    base_url: str,
) -> AnnouncementEvent | None:
    published_at = _cninfo_date(row.get("announcementTime") or row.get("announcementDate"))
    title = _string_or_none(row.get("announcementTitle") or row.get("title"))
    if published_at is None or not title:
        return None
    adjunct_url = _string_or_none(row.get("adjunctUrl"))
    url = None
    if adjunct_url:
        url = f"{base_url}/{adjunct_url.lstrip('/')}"
    return AnnouncementEvent(
        instrument_id=instrument_id,
        title=title,
        published_at=published_at,
        url=url,
        category=_string_or_none(row.get("categoryName")),
        provider=CninfoAnnouncementProvider.name,
    )


def _us_instruments(instrument_ids: list[str]) -> list[str]:
    return [instrument_id for instrument_id in instrument_ids if instrument_id.startswith("US:")]


def _cn_instruments(instrument_ids: list[str]) -> list[str]:
    return [instrument_id for instrument_id in instrument_ids if instrument_id.startswith("CN:")]


def _raw_symbol(instrument_id: str) -> str:
    return instrument_id.split(":", 1)[1]


def _normalize_cik(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).zfill(10)


def _at(values: list[object], index: int) -> object | None:
    if index >= len(values):
        return None
    return values[index]


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or pd.isna(value) or value == "":
        return None
    return Decimal(str(value))


def _string_or_none(value: object) -> str | None:
    if value is None or pd.isna(value) or value == "":
        return None
    return str(value)


def _date_or_none(value: object) -> date | None:
    text = _string_or_none(value)
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _cninfo_date(value: object) -> date | None:
    if value is None or pd.isna(value) or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value) / 1000).date()
    return _date_or_none(value)


def _normalize_announcement_time(value: object) -> str:
    text = (_string_or_none(value) or "unknown").strip().lower()
    if text in {"bmo", "before market open", "pre-market", "pre market"}:
        return "bmo"
    if text in {"amc", "after market close", "post-market", "post market"}:
        return "amc"
    if text in {"intraday", "during market"}:
        return "intraday"
    return text
