from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import httpx
import pandas as pd

from qagent.config import Settings, get_settings
from qagent.strategy_data.models import (
    AnalystInsight,
    AnnouncementEvent,
    EarningsEvent,
    FilingEvent,
    FundamentalSnapshot,
)


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

    def get_fundamentals(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FundamentalSnapshot]:
        ...

    def get_analyst_insights(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnalystInsight]:
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

    def get_fundamentals(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FundamentalSnapshot]:
        return []

    def get_analyst_insights(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnalystInsight]:
        return []


class FixtureStrategyDataProvider(BaseStrategyDataProvider):
    name = "fixture_strategy_data"

    def __init__(self, fixture_dir: Path | None = None):
        super().__init__()
        self.fixture_dir = fixture_dir or Path(__file__).parents[1] / "providers" / "fixture_data"

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

    def get_fundamentals(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FundamentalSnapshot]:
        path = self.fixture_dir / "fundamental_snapshots.csv"
        if not path.exists():
            return []

        frame = pd.read_csv(path, parse_dates=["as_of_date"])
        frame["as_of_date"] = frame["as_of_date"].dt.date
        mask = (
            frame["instrument_id"].isin(instrument_ids)
            & (frame["as_of_date"] >= start)
            & (frame["as_of_date"] <= end)
        )
        snapshots = []
        for row in frame.loc[mask].to_dict("records"):
            snapshots.append(
                FundamentalSnapshot(
                    instrument_id=str(row["instrument_id"]),
                    as_of_date=row["as_of_date"],
                    revenue_growth_pct=_decimal_or_none(row.get("revenue_growth_pct")),
                    earnings_growth_pct=_decimal_or_none(row.get("earnings_growth_pct")),
                    gross_margin_pct=_decimal_or_none(row.get("gross_margin_pct")),
                    operating_margin_pct=_decimal_or_none(row.get("operating_margin_pct")),
                    net_margin_pct=_decimal_or_none(row.get("net_margin_pct")),
                    return_on_equity_pct=_decimal_or_none(row.get("return_on_equity_pct")),
                    market_cap=_decimal_or_none(row.get("market_cap")),
                    pe_ratio=_decimal_or_none(row.get("pe_ratio")),
                    forward_pe=_decimal_or_none(row.get("forward_pe")),
                    peg_ratio=_decimal_or_none(row.get("peg_ratio")),
                    price_to_sales=_decimal_or_none(row.get("price_to_sales")),
                    provider=_string_or_none(row.get("provider")) or self.name,
                )
            )
        return snapshots

    def get_analyst_insights(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnalystInsight]:
        path = self.fixture_dir / "analyst_insights.csv"
        if not path.exists():
            return []

        frame = pd.read_csv(path, parse_dates=["as_of_date"])
        frame["as_of_date"] = frame["as_of_date"].dt.date
        mask = (
            frame["instrument_id"].isin(instrument_ids)
            & (frame["as_of_date"] >= start)
            & (frame["as_of_date"] <= end)
        )
        insights = []
        for row in frame.loc[mask].to_dict("records"):
            insights.append(
                AnalystInsight(
                    instrument_id=str(row["instrument_id"]),
                    as_of_date=row["as_of_date"],
                    revision_date=_date_or_none(row.get("revision_date")),
                    current_eps_estimate=_decimal_or_none(row.get("current_eps_estimate")),
                    prior_eps_estimate=_decimal_or_none(row.get("prior_eps_estimate")),
                    current_revenue_estimate=_decimal_or_none(row.get("current_revenue_estimate")),
                    prior_revenue_estimate=_decimal_or_none(row.get("prior_revenue_estimate")),
                    target_price=_decimal_or_none(row.get("target_price")),
                    prior_target_price=_decimal_or_none(row.get("prior_target_price")),
                    current_price=_decimal_or_none(row.get("current_price")),
                    buy_count=_int_or_zero(row.get("buy_count")),
                    hold_count=_int_or_zero(row.get("hold_count")),
                    sell_count=_int_or_zero(row.get("sell_count")),
                    strong_buy_count=_int_or_zero(row.get("strong_buy_count")),
                    strong_sell_count=_int_or_zero(row.get("strong_sell_count")),
                    provider=_string_or_none(row.get("provider")) or self.name,
                )
            )
        return insights


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

    def get_fundamentals(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FundamentalSnapshot]:
        snapshots: list[FundamentalSnapshot] = []
        for instrument_id in _us_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            try:
                key_metrics = self._get_rows(
                    "/stable/key-metrics",
                    {"symbol": symbol, "apikey": self.api_key},
                )
                growth_metrics = self._get_rows(
                    "/stable/financial-growth",
                    {"symbol": symbol, "apikey": self.api_key},
                )
                ratio_metrics = self._get_rows(
                    "/stable/ratios",
                    {"symbol": symbol, "apikey": self.api_key},
                )
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue

            snapshot = _fmp_fundamental_snapshot(
                instrument_id,
                _first_row(key_metrics),
                _first_row(growth_metrics),
                _first_row(ratio_metrics),
                end,
            )
            if snapshot and start <= snapshot.as_of_date <= end:
                snapshots.append(snapshot)
        return snapshots

    def get_analyst_insights(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnalystInsight]:
        insights: list[AnalystInsight] = []
        for instrument_id in _us_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            try:
                estimates = self._get_rows(
                    "/stable/analyst-estimates",
                    {
                        "symbol": symbol,
                        "period": "quarter",
                        "page": 0,
                        "limit": 2,
                        "apikey": self.api_key,
                    },
                )
                targets = self._get_rows(
                    "/stable/price-target-summary",
                    {"symbol": symbol, "apikey": self.api_key},
                )
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue

            insight = _fmp_analyst_insight(
                instrument_id,
                estimates,
                _first_row(targets),
                end,
            )
            if insight and start <= insight.as_of_date <= end:
                insights.append(insight)
        return insights

    def _get_rows(self, path: str, params: dict[str, object]) -> list[dict[str, object]]:
        response = self.client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            rows = payload.get("data") or payload.get("historical") or []
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
            return [payload]
        return []


class AlphaVantageStrategyDataProvider(BaseStrategyDataProvider):
    name = "alpha_vantage"

    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        base_url: str = "https://www.alphavantage.co/query",
    ):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
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
                payload = self._get_payload({"function": "EARNINGS", "symbol": symbol})
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue
            for row in payload.get("quarterlyEarnings", []):
                if not isinstance(row, dict):
                    continue
                event = _alpha_vantage_earnings_event(instrument_id, row)
                if event and start <= event.announcement_date <= end:
                    events.append(event)
        return events

    def get_fundamentals(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FundamentalSnapshot]:
        snapshots: list[FundamentalSnapshot] = []
        for instrument_id in _us_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            try:
                payload = self._get_payload({"function": "OVERVIEW", "symbol": symbol})
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue
            snapshot = _alpha_vantage_fundamental_snapshot(instrument_id, payload, end)
            if snapshot and start <= snapshot.as_of_date <= end:
                snapshots.append(snapshot)
        return snapshots

    def get_analyst_insights(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnalystInsight]:
        insights: list[AnalystInsight] = []
        for instrument_id in _us_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            try:
                payload = self._get_payload({"function": "OVERVIEW", "symbol": symbol})
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue
            insight = _alpha_vantage_analyst_insight(instrument_id, payload, end)
            if insight and start <= insight.as_of_date <= end:
                insights.append(insight)
        return insights

    def _get_payload(self, params: dict[str, object]) -> dict[str, object]:
        response = self.client.get(self.base_url, params={**params, "apikey": self.api_key})
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


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

    def get_fundamentals(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FundamentalSnapshot]:
        snapshots: list[FundamentalSnapshot] = []
        for instrument_id in _us_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            try:
                response = self.client.get(
                    f"{self.base_url}/stock/metric",
                    params={"symbol": symbol, "metric": "all", "token": self.api_key},
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue
            snapshot = _finnhub_fundamental_snapshot(instrument_id, payload, end)
            if snapshot and start <= snapshot.as_of_date <= end:
                snapshots.append(snapshot)
        return snapshots

    def get_analyst_insights(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnalystInsight]:
        insights: list[AnalystInsight] = []
        for instrument_id in _us_instruments(instrument_ids):
            symbol = _raw_symbol(instrument_id)
            try:
                response = self.client.get(
                    f"{self.base_url}/stock/recommendation",
                    params={"symbol": symbol, "token": self.api_key},
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # pragma: no cover
                self.last_errors.append(f"{instrument_id}: {exc}")
                continue
            rows = payload if isinstance(payload, list) else []
            insight = _finnhub_analyst_insight(instrument_id, rows, end)
            if insight and start <= insight.as_of_date <= end:
                insights.append(insight)
        return insights


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

    def get_fundamentals(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FundamentalSnapshot]:
        return self._collect("get_fundamentals", instrument_ids, start, end)

    def get_analyst_insights(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[AnalystInsight]:
        return self._collect("get_analyst_insights", instrument_ids, start, end)

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
    if settings.alpha_vantage_api_key:
        providers.append(AlphaVantageStrategyDataProvider(settings.alpha_vantage_api_key))
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


def _alpha_vantage_earnings_event(
    instrument_id: str,
    row: dict[str, object],
) -> EarningsEvent | None:
    event_date = _date_or_none(row.get("reportedDate") or row.get("fiscalDateEnding"))
    if event_date is None:
        return None
    return EarningsEvent(
        instrument_id=instrument_id,
        announcement_date=event_date,
        announcement_time=_normalize_announcement_time(row.get("reportTime") or row.get("time")),
        actual_eps=_decimal_or_none(row.get("reportedEPS")),
        estimated_eps=_decimal_or_none(row.get("estimatedEPS")),
        provider=AlphaVantageStrategyDataProvider.name,
    )


def _alpha_vantage_fundamental_snapshot(
    instrument_id: str,
    payload: dict[str, object],
    fallback_date: date,
) -> FundamentalSnapshot | None:
    if not payload:
        return None
    as_of_date = _date_or_none(payload.get("LatestQuarter")) or fallback_date
    gross_margin = _ratio_to_pct(payload.get("GrossMargin"))
    if gross_margin is None:
        gross_margin = _divide_to_pct(payload.get("GrossProfitTTM"), payload.get("RevenueTTM"))
    snapshot = FundamentalSnapshot(
        instrument_id=instrument_id,
        as_of_date=as_of_date,
        revenue_growth_pct=_ratio_to_pct(payload.get("QuarterlyRevenueGrowthYOY")),
        earnings_growth_pct=_ratio_to_pct(payload.get("QuarterlyEarningsGrowthYOY")),
        gross_margin_pct=gross_margin,
        operating_margin_pct=_ratio_to_pct(payload.get("OperatingMarginTTM")),
        net_margin_pct=_ratio_to_pct(payload.get("ProfitMargin")),
        return_on_equity_pct=_ratio_to_pct(payload.get("ReturnOnEquityTTM")),
        market_cap=_decimal_or_none(payload.get("MarketCapitalization")),
        pe_ratio=_decimal_or_none(payload.get("PERatio") or payload.get("TrailingPE")),
        forward_pe=_decimal_or_none(payload.get("ForwardPE")),
        peg_ratio=_decimal_or_none(payload.get("PEGRatio")),
        price_to_sales=_decimal_or_none(payload.get("PriceToSalesRatioTTM")),
        provider=AlphaVantageStrategyDataProvider.name,
    )
    if not snapshot.has_growth_inputs and not snapshot.has_valuation_inputs:
        return None
    return snapshot


def _alpha_vantage_analyst_insight(
    instrument_id: str,
    payload: dict[str, object],
    fallback_date: date,
) -> AnalystInsight | None:
    if not payload:
        return None
    insight = AnalystInsight(
        instrument_id=instrument_id,
        as_of_date=_date_or_none(payload.get("LatestQuarter")) or fallback_date,
        target_price=_decimal_or_none(payload.get("AnalystTargetPrice")),
        current_price=_decimal_or_none(payload.get("Price") or payload.get("CurrentPrice")),
        strong_buy_count=_int_or_zero(payload.get("AnalystRatingStrongBuy")),
        buy_count=_int_or_zero(payload.get("AnalystRatingBuy")),
        hold_count=_int_or_zero(payload.get("AnalystRatingHold")),
        sell_count=_int_or_zero(payload.get("AnalystRatingSell")),
        strong_sell_count=_int_or_zero(payload.get("AnalystRatingStrongSell")),
        provider=AlphaVantageStrategyDataProvider.name,
    )
    if insight.target_price is None and insight.total_ratings == 0:
        return None
    return insight


def _finnhub_fundamental_snapshot(
    instrument_id: str,
    payload: dict[str, object],
    fallback_date: date,
) -> FundamentalSnapshot | None:
    metric = payload.get("metric", {})
    if not isinstance(metric, dict) or not metric:
        return None
    return FundamentalSnapshot(
        instrument_id=instrument_id,
        as_of_date=_date_or_none(metric.get("asOfDate")) or fallback_date,
        revenue_growth_pct=_ratio_to_pct(metric.get("revenueGrowthTTMYoy")),
        earnings_growth_pct=_ratio_to_pct(metric.get("epsGrowthTTMYoy")),
        gross_margin_pct=_ratio_to_pct(metric.get("grossMarginTTM")),
        operating_margin_pct=_ratio_to_pct(metric.get("operatingMarginTTM")),
        net_margin_pct=_ratio_to_pct(metric.get("netProfitMarginTTM")),
        return_on_equity_pct=_ratio_to_pct(metric.get("roeTTM")),
        market_cap=_finnhub_market_cap(metric.get("marketCapitalization")),
        pe_ratio=_decimal_or_none(metric.get("peTTM") or metric.get("peNormalizedAnnual")),
        forward_pe=_decimal_or_none(metric.get("forwardPE")),
        peg_ratio=_decimal_or_none(metric.get("pegRatio")),
        price_to_sales=_decimal_or_none(metric.get("psTTM")),
        provider=FinnhubStrategyDataProvider.name,
    )


def _fmp_fundamental_snapshot(
    instrument_id: str,
    key_metrics: dict[str, object],
    growth_metrics: dict[str, object],
    ratio_metrics: dict[str, object],
    fallback_date: date,
) -> FundamentalSnapshot | None:
    if not key_metrics and not growth_metrics and not ratio_metrics:
        return None
    as_of_date = (
        _date_or_none(key_metrics.get("date"))
        or _date_or_none(growth_metrics.get("date"))
        or _date_or_none(ratio_metrics.get("date"))
        or fallback_date
    )
    return FundamentalSnapshot(
        instrument_id=instrument_id,
        as_of_date=as_of_date,
        revenue_growth_pct=_ratio_to_pct(
            _first_present(
                growth_metrics.get("revenueGrowth"),
                growth_metrics.get("revenueGrowthTTM"),
                growth_metrics.get("growthRevenue"),
            )
        ),
        earnings_growth_pct=_ratio_to_pct(
            _first_present(
                growth_metrics.get("epsgrowth"),
                growth_metrics.get("epsGrowth"),
                growth_metrics.get("growthEPS"),
            )
        ),
        gross_margin_pct=_ratio_to_pct(
            _first_present(ratio_metrics.get("grossProfitMargin"), growth_metrics.get("grossProfitMargin"))
        ),
        operating_margin_pct=_ratio_to_pct(
            _first_present(ratio_metrics.get("operatingProfitMargin"), ratio_metrics.get("operatingMargin"))
        ),
        net_margin_pct=_ratio_to_pct(
            _first_present(ratio_metrics.get("netProfitMargin"), ratio_metrics.get("netIncomeMargin"))
        ),
        return_on_equity_pct=_ratio_to_pct(ratio_metrics.get("returnOnEquity")),
        market_cap=_decimal_or_none(key_metrics.get("marketCap") or key_metrics.get("marketCapTTM")),
        pe_ratio=_decimal_or_none(key_metrics.get("peRatio") or key_metrics.get("peRatioTTM")),
        forward_pe=_decimal_or_none(key_metrics.get("forwardPE")),
        peg_ratio=_decimal_or_none(key_metrics.get("pegRatio") or key_metrics.get("pegRatioTTM")),
        price_to_sales=_decimal_or_none(
            key_metrics.get("priceToSalesRatio") or key_metrics.get("priceToSalesRatioTTM")
        ),
        provider=FmpStrategyDataProvider.name,
    )


def _fmp_analyst_insight(
    instrument_id: str,
    estimate_rows: list[dict[str, object]],
    target_row: dict[str, object],
    fallback_date: date,
) -> AnalystInsight | None:
    if not estimate_rows and not target_row:
        return None
    current = _first_row(estimate_rows)
    prior = estimate_rows[1] if len(estimate_rows) > 1 else {}
    as_of_date = _date_or_none(current.get("date") or current.get("fiscalDateEnding")) or fallback_date
    has_prior = bool(prior)
    return AnalystInsight(
        instrument_id=instrument_id,
        as_of_date=as_of_date,
        revision_date=as_of_date if has_prior else None,
        current_eps_estimate=_decimal_or_none(
            _first_present(current.get("estimatedEpsAvg"), current.get("epsAvg"), current.get("eps"))
        ),
        prior_eps_estimate=_decimal_or_none(
            _first_present(prior.get("estimatedEpsAvg"), prior.get("epsAvg"), prior.get("eps"))
        ),
        current_revenue_estimate=_decimal_or_none(
            _first_present(
                current.get("estimatedRevenueAvg"),
                current.get("revenueAvg"),
                current.get("revenue"),
            )
        ),
        prior_revenue_estimate=_decimal_or_none(
            _first_present(prior.get("estimatedRevenueAvg"), prior.get("revenueAvg"), prior.get("revenue"))
        ),
        target_price=_decimal_or_none(
            _first_present(
                target_row.get("targetMean"),
                target_row.get("targetConsensus"),
                target_row.get("targetMedian"),
                target_row.get("priceTargetAverage"),
            )
        ),
        provider=FmpStrategyDataProvider.name,
    )


def _finnhub_analyst_insight(
    instrument_id: str,
    rows: list[object],
    fallback_date: date,
) -> AnalystInsight | None:
    dict_rows = [row for row in rows if isinstance(row, dict)]
    if not dict_rows:
        return None
    latest = dict_rows[0]
    as_of_date = _date_or_none(latest.get("period")) or fallback_date
    insight = AnalystInsight(
        instrument_id=instrument_id,
        as_of_date=as_of_date,
        strong_buy_count=_int_or_zero(latest.get("strongBuy")),
        buy_count=_int_or_zero(latest.get("buy")),
        hold_count=_int_or_zero(latest.get("hold")),
        sell_count=_int_or_zero(latest.get("sell")),
        strong_sell_count=_int_or_zero(latest.get("strongSell")),
        provider=FinnhubStrategyDataProvider.name,
    )
    if insight.total_ratings == 0:
        return None
    return insight


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


def _first_row(rows: list[dict[str, object]]) -> dict[str, object]:
    return rows[0] if rows else {}


def _first_present(*values: object) -> object | None:
    for value in values:
        if _decimal_or_none(value) is not None:
            return value
    return None


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"none", "nan", "null", "-", "n/a"}:
        return None
    return Decimal(text)


def _int_or_zero(value: object) -> int:
    if value is None or pd.isna(value) or value == "":
        return 0
    return int(Decimal(str(value)))


def _ratio_to_pct(value: object) -> Decimal | None:
    number = _decimal_or_none(value)
    if number is None:
        return None
    if abs(number) <= Decimal("3"):
        return number * Decimal("100")
    return number


def _divide_to_pct(numerator: object, denominator: object) -> Decimal | None:
    top = _decimal_or_none(numerator)
    bottom = _decimal_or_none(denominator)
    if top is None or bottom is None or bottom == 0:
        return None
    return top / bottom * Decimal("100")


def _finnhub_market_cap(value: object) -> Decimal | None:
    market_cap = _decimal_or_none(value)
    if market_cap is None:
        return None
    if market_cap < Decimal("1000000"):
        return market_cap * Decimal("1000000")
    return market_cap


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
