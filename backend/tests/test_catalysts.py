from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

from qagent.app import create_app
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.models import NewsItem
from qagent.catalysts.providers import FreeCatalystProvider


def test_free_catalyst_provider_normalizes_yfinance_news(monkeypatch):
    class FakeSearch:
        def __init__(self, query, max_results, news_count, raise_errors):
            assert query == "AAPL"
            assert news_count == 2
            self.news = [
                {
                    "title": "Apple supplier wins new AI server order",
                    "publisher": "Wire",
                    "link": "https://example.com/aapl-ai-order",
                    "providerPublishTime": 1_767_273_600,
                }
            ]

    monkeypatch.setattr("qagent.catalysts.providers.yf.Search", FakeSearch)

    provider = FreeCatalystProvider()
    news = provider.get_news(["US:AAPL"], limit=2)

    assert news[0].instrument_id == "US:AAPL"
    assert news[0].title == "Apple supplier wins new AI server order"
    assert news[0].publisher == "Wire"
    assert news[0].source == "yfinance"


def test_free_catalyst_provider_normalizes_akshare_news(monkeypatch):
    def fake_stock_news_em(symbol):
        assert symbol == "000001"
        return pd.DataFrame(
            {
                "新闻标题": ["平安银行订单增长"],
                "文章来源": ["东方财富"],
                "发布时间": ["2026-06-20 09:30:00"],
                "新闻链接": ["https://example.com/000001"],
            }
        )

    fake_ak = SimpleNamespace(stock_news_em=fake_stock_news_em)
    monkeypatch.setattr("qagent.catalysts.providers.ak", fake_ak)

    provider = FreeCatalystProvider()
    news = provider.get_news(["CN:000001"], limit=2)

    assert news[0].instrument_id == "CN:000001"
    assert news[0].title == "平安银行订单增长"
    assert news[0].source == "akshare"


def test_catalyst_hypotheses_map_news_to_verification_path():
    item = NewsItem(
        news_id="n1",
        instrument_id="US:AAPL",
        title="Apple supplier wins new AI server order",
        publisher="Wire",
        published_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        url="https://example.com/aapl-ai-order",
        source="fixture",
    )

    hypotheses = build_catalyst_hypotheses([item])

    assert hypotheses[0].instrument_id == "US:AAPL"
    assert hypotheses[0].catalyst_type == "demand"
    assert "orders" in hypotheses[0].verification_path.lower()


def test_catalyst_hypotheses_do_not_treat_ai_mention_as_demand_by_itself():
    item = NewsItem(
        news_id="n2",
        instrument_id="US:AAPL",
        title="Apple CEO says AI tools should serve products",
        publisher="Wire",
        published_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        url="https://example.com/aapl-ai",
        source="fixture",
    )

    hypotheses = build_catalyst_hypotheses([item])

    assert hypotheses[0].catalyst_type == "general"


def test_catalysts_endpoint_returns_news_and_hypotheses(monkeypatch):
    news_item = NewsItem(
        news_id="n1",
        instrument_id="US:AAPL",
        title="Apple supplier wins new AI server order",
        publisher="Wire",
        published_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        url="https://example.com/aapl-ai-order",
        source="fixture",
    )

    class FakeProvider:
        last_errors: list[str] = []

        def get_news(self, instrument_ids, limit):
            assert instrument_ids == ["US:AAPL"]
            assert limit == 3
            return [news_item]

    monkeypatch.setattr("qagent.api.routes.FreeCatalystProvider", FakeProvider)

    client = TestClient(create_app())
    response = client.get("/api/catalysts?symbols=US:AAPL&limit=3")

    assert response.status_code == 200
    body = response.json()
    assert body["news"][0]["instrument_id"] == "US:AAPL"
    assert body["hypotheses"][0]["catalyst_type"] == "demand"
    assert body["data_health"]["news"] == "1"
