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
    assert hypotheses[0].source == "fixture"
    assert hypotheses[0].observed_facts == [
        "Source headline: Apple supplier wins new AI server order"
    ]
    assert hypotheses[0].beneficiary_chain[0].benefit_order == "unverified"
    assert hypotheses[0].financial_transmission[0].line_item == "orders, backlog, and revenue"
    assert hypotheses[0].priced_in_assessment == "unknown_without_price_and_consensus_context"
    assert hypotheses[0].invalidation_triggers
    assert hypotheses[0].decision_effect == "none"


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
    assert hypotheses[0].financial_transmission[0].line_item == "unidentified"
    assert hypotheses[0].research_status == "hypothesis_only"


def test_catalyst_hypotheses_separate_policy_fact_from_beneficiary_inference():
    item = NewsItem(
        news_id="n3",
        instrument_id="CN:000001",
        title="新政策规划提出设备更新补贴",
        publisher="Fixture",
        published_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        url="https://example.com/policy",
        source="fixture",
    )

    hypothesis = build_catalyst_hypotheses([item])[0]

    assert hypothesis.catalyst_type == "policy"
    assert hypothesis.observed_facts == ["Source headline: 新政策规划提出设备更新补贴"]
    assert hypothesis.beneficiary_chain[0].chain_role == "named_instrument"
    assert hypothesis.beneficiary_chain[0].benefit_order == "unverified"
    assert "primary policy document" in hypothesis.evidence_to_watch
    assert hypothesis.risks
    assert hypothesis.invalidation_triggers


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
    assert body["data_health"]["catalyst_research_contract"] == "serenity-alpha-hypothesis-v1"
    assert body["data_health"]["catalyst_decision_effect"] == "none"
