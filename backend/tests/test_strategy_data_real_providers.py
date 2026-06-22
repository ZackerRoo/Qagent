from datetime import date
from decimal import Decimal

import httpx

from qagent.config import Settings
from qagent.strategy_data.providers import (
    CninfoAnnouncementProvider,
    CompositeStrategyDataProvider,
    FmpStrategyDataProvider,
    FinnhubStrategyDataProvider,
    SecEdgarStrategyDataProvider,
    build_strategy_data_provider,
)


def _json_transport(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    return httpx.MockTransport(handler)


def test_fmp_provider_normalizes_earnings_calendar_response():
    client = httpx.Client(
        transport=_json_transport(
            [
                {
                    "symbol": "AAPL",
                    "date": "2026-01-28",
                    "epsActual": 2.1,
                    "epsEstimated": 1.95,
                    "revenueActual": 124000000000,
                    "revenueEstimated": 121000000000,
                    "time": "amc",
                }
            ]
        )
    )
    provider = FmpStrategyDataProvider(api_key="demo", client=client)

    events = provider.get_earnings_events(["US:AAPL"], date(2026, 1, 1), date(2026, 2, 1))

    assert len(events) == 1
    assert events[0].instrument_id == "US:AAPL"
    assert events[0].announcement_date == date(2026, 1, 28)
    assert events[0].actual_eps == Decimal("2.1")
    assert events[0].estimated_eps == Decimal("1.95")
    assert events[0].actual_revenue == Decimal("124000000000")
    assert events[0].estimated_revenue == Decimal("121000000000")
    assert events[0].provider == "fmp"


def test_finnhub_provider_normalizes_earnings_calendar_response():
    client = httpx.Client(
        transport=_json_transport(
            {
                "earningsCalendar": [
                    {
                        "symbol": "MSFT",
                        "date": "2026-01-30",
                        "epsActual": 3.4,
                        "epsEstimate": 3.1,
                        "revenueActual": 72000000000,
                        "revenueEstimate": 70000000000,
                        "hour": "bmo",
                    }
                ]
            }
        )
    )
    provider = FinnhubStrategyDataProvider(api_key="demo", client=client)

    events = provider.get_earnings_events(["US:MSFT"], date(2026, 1, 1), date(2026, 2, 1))

    assert len(events) == 1
    assert events[0].instrument_id == "US:MSFT"
    assert events[0].announcement_time == "bmo"
    assert events[0].estimated_eps == Decimal("3.1")
    assert events[0].estimated_revenue == Decimal("70000000000")
    assert events[0].provider == "finnhub"


def test_sec_provider_normalizes_recent_filings_response():
    client = httpx.Client(
        transport=_json_transport(
            {
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000320193-26-000010"],
                        "form": ["10-Q"],
                        "filingDate": ["2026-01-31"],
                        "reportDate": ["2025-12-31"],
                        "primaryDocument": ["aapl-20251231.htm"],
                    }
                }
            }
        )
    )
    provider = SecEdgarStrategyDataProvider(
        cik_lookup={"AAPL": "0000320193"},
        client=client,
        user_agent="qagent-test contact@example.com",
    )

    filings = provider.get_filings(["US:AAPL"], date(2026, 1, 1), date(2026, 2, 1))

    assert len(filings) == 1
    assert filings[0].instrument_id == "US:AAPL"
    assert filings[0].form == "10-Q"
    assert filings[0].accession_number == "0000320193-26-000010"
    assert filings[0].filing_url.endswith("/000032019326000010/aapl-20251231.htm")
    assert filings[0].provider == "sec_edgar"


def test_sec_provider_resolves_ticker_to_cik_when_lookup_not_supplied():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/company_tickers.json"):
            return httpx.Response(
                200,
                json={"0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000320193-26-000010"],
                        "form": ["10-Q"],
                        "filingDate": ["2026-01-31"],
                        "reportDate": ["2025-12-31"],
                        "primaryDocument": ["aapl-20251231.htm"],
                    }
                }
            },
            request=request,
        )

    provider = SecEdgarStrategyDataProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        user_agent="qagent-test contact@example.com",
    )

    filings = provider.get_filings(["US:AAPL"], date(2026, 1, 1), date(2026, 2, 1))

    assert len(filings) == 1
    assert filings[0].instrument_id == "US:AAPL"


def test_cninfo_provider_normalizes_announcement_response():
    client = httpx.Client(
        transport=_json_transport(
            {
                "announcements": [
                    {
                        "secCode": "000001",
                        "announcementTitle": "2025年度报告",
                        "announcementTime": 1767225600000,
                        "adjunctUrl": "finalpage/2026-01-01/abc.PDF",
                    }
                ]
            }
        )
    )
    provider = CninfoAnnouncementProvider(client=client)

    announcements = provider.get_announcements(
        ["CN:000001"], date(2026, 1, 1), date(2026, 1, 31)
    )

    assert len(announcements) == 1
    assert announcements[0].instrument_id == "CN:000001"
    assert announcements[0].title == "2025年度报告"
    assert announcements[0].published_at == date(2026, 1, 1)
    assert announcements[0].url.endswith("finalpage/2026-01-01/abc.PDF")
    assert announcements[0].provider == "cninfo"


def test_composite_strategy_data_provider_merges_sources_and_errors():
    class WorkingProvider:
        name = "working"
        last_errors: list[str] = []

        def get_earnings_events(self, instrument_ids, start, end):
            return []

        def get_filings(self, instrument_ids, start, end):
            return []

        def get_announcements(self, instrument_ids, start, end):
            return []

    class FailingProvider:
        name = "failing"
        last_errors: list[str] = []

        def get_earnings_events(self, instrument_ids, start, end):
            raise RuntimeError("upstream down")

        def get_filings(self, instrument_ids, start, end):
            return []

        def get_announcements(self, instrument_ids, start, end):
            return []

    provider = CompositeStrategyDataProvider([FailingProvider(), WorkingProvider()])

    assert provider.get_earnings_events(["US:AAPL"], date(2026, 1, 1), date(2026, 1, 2)) == []
    assert provider.last_errors == ["failing.get_earnings_events: upstream down"]


def test_build_strategy_data_provider_uses_configured_real_sources():
    provider = build_strategy_data_provider(
        "free",
        settings=Settings(
            fmp_api_key="fmp-key",
            finnhub_api_key="finnhub-key",
            tushare_token="tushare-token",
            sec_user_agent="qagent-test contact@example.com",
        ),
    )

    assert "fmp" in provider.name
    assert "finnhub" in provider.name
    assert "sec_edgar" in provider.name
    assert "cninfo" in provider.name
    assert "tushare" in provider.name
