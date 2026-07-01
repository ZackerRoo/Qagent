from datetime import date
from decimal import Decimal

import httpx

from qagent.config import Settings
from qagent.strategy_data import providers as strategy_providers
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


def test_alpha_vantage_provider_normalizes_company_overview_fundamentals():
    client = httpx.Client(
        transport=_json_transport(
            {
                "Symbol": "AAPL",
                "LatestQuarter": "2026-03-31",
                "QuarterlyRevenueGrowthYOY": "0.325",
                "QuarterlyEarningsGrowthYOY": "0.412",
                "GrossProfitTTM": "88000000000",
                "RevenueTTM": "129411764706",
                "ProfitMargin": "0.18",
                "OperatingMarginTTM": "0.245",
                "ReturnOnEquityTTM": "0.29",
                "MarketCapitalization": "8500000000",
                "PERatio": "34.0",
                "ForwardPE": "28.0",
                "PEGRatio": "0.95",
                "PriceToSalesRatioTTM": "7.5",
                "AnalystTargetPrice": "64.0",
                "AnalystRatingStrongBuy": "6",
                "AnalystRatingBuy": "14",
                "AnalystRatingHold": "4",
                "AnalystRatingSell": "1",
                "AnalystRatingStrongSell": "0",
            }
        )
    )
    provider = strategy_providers.AlphaVantageStrategyDataProvider(api_key="demo", client=client)

    snapshots = provider.get_fundamentals(["US:AAPL"], date(2026, 3, 1), date(2026, 4, 1))
    insights = provider.get_analyst_insights(["US:AAPL"], date(2026, 3, 1), date(2026, 4, 1))

    assert len(snapshots) == 1
    assert snapshots[0].instrument_id == "US:AAPL"
    assert snapshots[0].as_of_date == date(2026, 3, 31)
    assert snapshots[0].revenue_growth_pct == Decimal("32.500")
    assert snapshots[0].earnings_growth_pct == Decimal("41.200")
    assert snapshots[0].gross_margin_pct.quantize(Decimal("0.01")) == Decimal("68.00")
    assert snapshots[0].net_margin_pct == Decimal("18.00")
    assert snapshots[0].pe_ratio == Decimal("34.0")
    assert snapshots[0].peg_ratio == Decimal("0.95")
    assert snapshots[0].provider == "alpha_vantage"
    assert len(insights) == 1
    assert insights[0].target_price == Decimal("64.0")
    assert insights[0].strong_buy_count == 6
    assert insights[0].buy_count == 14
    assert insights[0].bullish_rating_ratio == Decimal("0.8")


def test_alpha_vantage_provider_normalizes_earnings_history_response():
    client = httpx.Client(
        transport=_json_transport(
            {
                "symbol": "AAPL",
                "quarterlyEarnings": [
                    {
                        "reportedDate": "2026-03-31",
                        "reportedEPS": "1.34",
                        "estimatedEPS": "1.05",
                        "surprisePercentage": "27.6",
                    }
                ],
            }
        )
    )
    provider = strategy_providers.AlphaVantageStrategyDataProvider(api_key="demo", client=client)

    events = provider.get_earnings_events(["US:AAPL"], date(2026, 3, 1), date(2026, 4, 1))

    assert len(events) == 1
    assert events[0].instrument_id == "US:AAPL"
    assert events[0].announcement_date == date(2026, 3, 31)
    assert events[0].actual_eps == Decimal("1.34")
    assert events[0].estimated_eps == Decimal("1.05")
    assert events[0].provider == "alpha_vantage"


def test_finnhub_provider_normalizes_basic_financials_response():
    client = httpx.Client(
        transport=_json_transport(
            {
                "metric": {
                    "revenueGrowthTTMYoy": 0.325,
                    "epsGrowthTTMYoy": 0.412,
                    "grossMarginTTM": 0.68,
                    "operatingMarginTTM": 0.245,
                    "netProfitMarginTTM": 0.18,
                    "roeTTM": 0.29,
                    "marketCapitalization": 8500,
                    "peTTM": 34,
                    "forwardPE": 28,
                    "pegRatio": 0.95,
                    "psTTM": 7.5,
                }
            }
        )
    )
    provider = FinnhubStrategyDataProvider(api_key="demo", client=client)

    snapshots = provider.get_fundamentals(["US:AAPL"], date(2026, 3, 1), date(2026, 4, 1))

    assert len(snapshots) == 1
    assert snapshots[0].revenue_growth_pct == Decimal("32.500")
    assert snapshots[0].net_margin_pct == Decimal("18.00")
    assert snapshots[0].market_cap == Decimal("8500000000")
    assert snapshots[0].forward_pe == Decimal("28")
    assert snapshots[0].provider == "finnhub"


def test_fmp_provider_normalizes_key_metrics_response():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stable/key-metrics"):
            return httpx.Response(
                200,
                json=[
                    {
                        "date": "2026-03-31",
                        "marketCap": 8500000000,
                        "peRatio": 34,
                        "pegRatio": 0.95,
                        "priceToSalesRatio": 7.5,
                    }
                ],
                request=request,
            )
        return httpx.Response(
            200,
            json=[
                {
                    "date": "2026-03-31",
                    "revenueGrowth": 0.325,
                    "epsgrowth": 0.412,
                    "grossProfitMargin": 0.68,
                    "operatingIncomeGrowth": 0.245,
                    "netIncomeGrowth": 0.18,
                }
            ],
            request=request,
        )

    provider = FmpStrategyDataProvider(
        api_key="demo",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    snapshots = provider.get_fundamentals(["US:AAPL"], date(2026, 3, 1), date(2026, 4, 1))

    assert len(snapshots) == 1
    assert snapshots[0].as_of_date == date(2026, 3, 31)
    assert snapshots[0].revenue_growth_pct == Decimal("32.500")
    assert snapshots[0].pe_ratio == Decimal("34")
    assert snapshots[0].peg_ratio == Decimal("0.95")
    assert snapshots[0].provider == "fmp"


def test_fmp_provider_normalizes_analyst_estimate_revisions():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stable/analyst-estimates"):
            return httpx.Response(
                200,
                json=[
                    {
                        "date": "2026-03-31",
                        "estimatedEpsAvg": 1.35,
                        "estimatedRevenueAvg": 152000000,
                    },
                    {
                        "date": "2025-12-31",
                        "estimatedEpsAvg": 1.10,
                        "estimatedRevenueAvg": 140000000,
                    },
                ],
                request=request,
            )
        return httpx.Response(
            200,
            json=[{"targetMean": 64, "targetLow": 48, "targetHigh": 72}],
            request=request,
        )

    provider = FmpStrategyDataProvider(
        api_key="demo",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    insights = provider.get_analyst_insights(["US:AAPL"], date(2026, 3, 1), date(2026, 4, 1))

    assert len(insights) == 1
    assert insights[0].revision_date == date(2026, 3, 31)
    assert insights[0].current_eps_estimate == Decimal("1.35")
    assert insights[0].prior_eps_estimate == Decimal("1.1")
    assert insights[0].eps_revision_pct > Decimal("20")
    assert insights[0].current_revenue_estimate == Decimal("152000000")
    assert insights[0].prior_revenue_estimate == Decimal("140000000")
    assert insights[0].target_price == Decimal("64")
    assert insights[0].has_revision_inputs is True
    assert insights[0].provider == "fmp"


def test_finnhub_provider_normalizes_recommendation_trends():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stock/recommendation"):
            return httpx.Response(
                200,
                json=[
                    {
                        "period": "2026-03-01",
                        "strongBuy": 6,
                        "buy": 14,
                        "hold": 4,
                        "sell": 1,
                        "strongSell": 0,
                    }
                ],
                request=request,
            )
        return httpx.Response(200, json={"metric": {}}, request=request)

    provider = FinnhubStrategyDataProvider(
        api_key="demo",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    insights = provider.get_analyst_insights(["US:AAPL"], date(2026, 3, 1), date(2026, 4, 1))

    assert len(insights) == 1
    assert insights[0].as_of_date == date(2026, 3, 1)
    assert insights[0].strong_buy_count == 6
    assert insights[0].buy_count == 14
    assert insights[0].bullish_rating_ratio == Decimal("0.8")
    assert insights[0].provider == "finnhub"


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

        def get_fundamentals(self, instrument_ids, start, end):
            return []

        def get_analyst_insights(self, instrument_ids, start, end):
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

        def get_fundamentals(self, instrument_ids, start, end):
            return []

        def get_analyst_insights(self, instrument_ids, start, end):
            return []

    provider = CompositeStrategyDataProvider([FailingProvider(), WorkingProvider()])

    assert provider.get_earnings_events(["US:AAPL"], date(2026, 1, 1), date(2026, 1, 2)) == []
    assert provider.last_errors == ["failing.get_earnings_events: upstream down"]


def test_build_strategy_data_provider_uses_configured_real_sources():
    provider = build_strategy_data_provider(
        "free",
        settings=Settings(
            alpha_vantage_api_key="alpha-key",
            fmp_api_key="fmp-key",
            finnhub_api_key="finnhub-key",
            tushare_token="tushare-token",
            sec_user_agent="qagent-test contact@example.com",
        ),
    )

    assert "alpha_vantage" in provider.name
    assert "fmp" in provider.name
    assert "finnhub" in provider.name
    assert "sec_edgar" in provider.name
    assert "cninfo" in provider.name
    assert "tushare" in provider.name
