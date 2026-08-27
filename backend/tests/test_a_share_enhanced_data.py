from datetime import date

from qagent.domain.models import (
    AShareDragonTigerInsight,
    AShareEnhancedSnapshot,
    AShareFundFlowInsight,
    AShareLimitSentiment,
    AShareResearchCoverage,
    AShareRiskEventProfile,
)
from qagent.jobs.daily_scan import run_daily_scan
from qagent.jobs.full_market import enrich_full_market_visible_cards
from qagent.market.astock_enhanced import (
    AStockEnhancedDataProvider,
    EmptyAShareEnhancedDataProvider,
    build_a_share_enhanced_provider,
    summarize_a_share_enhanced_snapshots,
)
from qagent.market.fuyao_enhanced import FuyaoAShareEnhancedDataProvider
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.db import create_session_factory, initialize_database
from qagent.storage.astock_enhanced_cache import AShareEnhancedCacheRepository


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class FakeEastmoneyClient:
    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        params = params or {}
        if "fflow/daykline" in url:
            return FakeResponse(
                {
                    "data": {
                        "klines": [
                            "2026-06-20,1000000,0,0,300000,700000",
                            "2026-06-21,-200000,0,0,-100000,-100000",
                            "2026-06-22,1500000,0,0,500000,1000000",
                        ]
                    }
                }
            )
        if "push2ex.eastmoney.com/getTopicZTPool" in url:
            return FakeResponse(
                {
                    "data": {
                        "pool": [
                            {
                                "c": "000001",
                                "n": "平安银行",
                                "p": 12340,
                                "zdp": 10.0,
                                "amount": 900000000,
                                "ltsz": 20000000000,
                                "hs": 8.2,
                                "lbc": 2,
                                "fbt": 93001,
                                "lbt": 145501,
                                "fund": 280000000,
                                "zbc": 0,
                                "hybk": "银行",
                                "zttj": {"days": 3, "ct": 2},
                            },
                            {
                                "c": "000002",
                                "n": "测试股份",
                                "p": 10000,
                                "zdp": 10.0,
                                "amount": 100000000,
                                "ltsz": 1000000000,
                                "hs": 5.1,
                                "lbc": 1,
                                "fbt": 100001,
                                "lbt": 140001,
                                "fund": 50000000,
                                "zbc": 1,
                                "hybk": "地产",
                                "zttj": {"days": 1, "ct": 1},
                            },
                        ]
                    }
                }
            )
        if "push2ex.eastmoney.com/getTopicZBPool" in url:
            return FakeResponse({"data": {"pool": [{"c": "000003", "n": "炸板样例", "lbc": 1}]}})
        if "push2ex.eastmoney.com/getTopicDTPool" in url:
            return FakeResponse({"data": {"pool": []}})
        if "reportapi.eastmoney.com" in url:
            return FakeResponse(
                {
                    "data": [
                        {
                            "publishDate": "2026-06-20 00:00:00",
                            "title": "业绩拐点确认",
                            "orgSName": "测试证券",
                            "emRatingName": "买入",
                        }
                    ],
                    "TotalPage": 1,
                }
            )
        if params.get("reportName") == "RPT_DAILYBILLBOARD_DETAILSNEW":
            return FakeResponse(
                {
                    "result": {
                        "data": [
                            {
                                "TRADE_DATE": "2026-06-22 00:00:00",
                                "SECURITY_CODE": "000001",
                                "EXPLANATION": "日涨幅偏离值达7%",
                                "BILLBOARD_NET_AMT": 36000000,
                                "TURNOVERRATE": 12.5,
                            }
                        ]
                    }
                }
            )
        if params.get("reportName") in {"RPT_BILLBOARD_DAILYDETAILSBUY", "RPT_BILLBOARD_DAILYDETAILSSELL"}:
            return FakeResponse({"result": {"data": []}})
        if params.get("reportName") == "RPT_LIFT_STAGE":
            return FakeResponse(
                {
                    "result": {
                        "data": [
                            {
                                "FREE_DATE": "2026-07-10 00:00:00",
                                "LIMITED_STOCK_TYPE": "首发原股东限售股份",
                                "FREE_SHARES_NUM": 10000000,
                                "FREE_RATIO": 2.4,
                            }
                        ]
                    }
                }
            )
        if params.get("reportName") == "RPTA_WEB_RZRQ_GGMX":
            return FakeResponse(
                {
                    "result": {
                        "data": [
                            {"DATE": "2026-06-22", "RZRQYE": 1100000000, "RZYE": 900000000},
                            {"DATE": "2026-06-21", "RZRQYE": 1000000000, "RZYE": 850000000},
                        ]
                    }
                }
            )
        return FakeResponse({})


def test_astock_enhanced_provider_builds_snapshot_from_reference_endpoints():
    provider = AStockEnhancedDataProvider(
        client=FakeEastmoneyClient(),
        min_request_interval_seconds=0,
    )

    snapshots = provider.get_snapshots(["CN:000001"], as_of=date(2026, 6, 22))

    snapshot = snapshots["CN:000001"]
    assert snapshot.status == "ready"
    assert snapshot.fund_flow.trend == "positive"
    assert snapshot.fund_flow.main_net_inflow_20d > 0
    assert snapshot.dragon_tiger.recent_records == 1
    assert snapshot.dragon_tiger.latest_net_buy_wan == 3600
    assert snapshot.limit_sentiment.member_status == "limit_up"
    assert snapshot.limit_sentiment.limit_up_count == 2
    assert snapshot.risk_events.upcoming_lockup_count == 1
    assert snapshot.research_coverage.report_count == 1
    assert snapshot.score > 0.5
    assert provider.last_errors == []


def test_astock_enhanced_provider_uses_sqlite_cache(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'enhanced-cache.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    initialize_database(database_url)
    cache = AShareEnhancedCacheRepository(create_session_factory(database_url))
    client = FakeEastmoneyClient()
    provider = AStockEnhancedDataProvider(
        client=client,
        cache=cache,
        min_request_interval_seconds=0,
    )

    first = provider.get_snapshots(["CN:000001"], as_of=date(2026, 6, 22))
    calls_after_first = client.calls
    second = provider.get_snapshots(["CN:000001"], as_of=date(2026, 6, 22))

    assert first["CN:000001"].summary == second["CN:000001"].summary
    assert calls_after_first > 0
    assert client.calls == calls_after_first
    assert provider.cache_hits == 1
    assert provider.cache_misses == 1


class FakeEnhancedProvider:
    name = "fake_astock_enhanced"
    last_errors: list[str] = []

    def get_snapshots(self, instrument_ids, as_of):
        return {
            "CN:000001": AShareEnhancedSnapshot(
                status="ready",
                score=0.82,
                provider=self.name,
                as_of=as_of,
                fund_flow=AShareFundFlowInsight(
                    trend="positive",
                    score=0.82,
                    lookback_days=20,
                    main_net_inflow_20d=180000000,
                    latest_main_net_inflow=30000000,
                    summary="近20日主力净流入1.80亿，资金面支持。",
                ),
                dragon_tiger=AShareDragonTigerInsight(
                    score=0.7,
                    recent_records=1,
                    latest_date=as_of,
                    latest_reason="日涨幅偏离值达7%",
                    latest_net_buy_wan=2500,
                    institution_net_buy_wan=800,
                    summary="近30日龙虎榜1次，最近净买入2500.0万。",
                ),
                limit_sentiment=AShareLimitSentiment(
                    score=0.76,
                    date=as_of,
                    limit_up_count=60,
                    break_board_count=18,
                    limit_down_count=5,
                    break_rate_pct=23.1,
                    max_height=4,
                    member_status="limit_up",
                    member_reason="AI 银行科技",
                    summary="涨停60家，炸板率23.1%，最高4连板；该股在涨停池。",
                ),
                risk_events=AShareRiskEventProfile(
                    score=0.9,
                    upcoming_lockup_count=0,
                    warnings=[],
                    summary="未发现近期解禁或融资融券异常风险。",
                ),
                research_coverage=AShareResearchCoverage(
                    score=0.7,
                    report_count=2,
                    latest_report_date=as_of,
                    latest_title="银行科技投入改善",
                    latest_rating="买入",
                    summary="近端研报2篇，最新评级买入。",
                ),
                signals=["fund_flow_positive", "dragon_tiger_net_buy", "limit_up_member"],
                warnings=[],
                summary="资金流、龙虎榜和涨停情绪共同确认。",
            )
        }


def test_daily_scan_attaches_a_share_enhanced_data_to_recommendations():
    result = run_daily_scan(
        instrument_ids=["CN:000001"],
        provider=FixtureMarketDataProvider(),
        end=date(2026, 3, 20),
        a_share_enhanced_provider=FakeEnhancedProvider(),
        a_share_enhanced_top_n=5,
    )

    card = result.cards[0]
    assert card.a_share_enhanced is not None
    assert card.a_share_enhanced.fund_flow.trend == "positive"
    assert any("资金流确认" in reason for reason in card.rank_reasons)
    assert any("龙虎榜确认" in reason for reason in card.rank_reasons)
    assert any("涨停情绪" in reason for reason in card.rank_reasons)
    assert "fund_flow_positive" in card.factor_flags
    assert result.data_health["a_share_enhanced_provider"] == "fake_astock_enhanced"
    assert result.data_health["a_share_enhanced_snapshots"] == "1"
    assert result.data_health["a_share_enhanced_fund_flow_positive"] == "1"
    assert result.data_health["a_share_enhanced_decision_weight_applied"] == "false"


class FakeFuyaoEnhancedClient:
    def __init__(self, *, fail_dragon_tiger: bool = False):
        self.fail_dragon_tiger = fail_dragon_tiger
        self.dragon_calls = 0
        self.limit_calls = 0

    def get_dragon_tiger(self, *, trade_date):
        self.dragon_calls += 1
        if self.fail_dragon_tiger:
            raise RuntimeError("dragon source unavailable")
        return {
            "trade_date": trade_date.isoformat(),
            "stock_items": [
                {
                    "thscode": "000001.SZ",
                    "net_value": 36_000_000,
                    "org_net_value": 8_000_000,
                    "limit_reason": "日涨幅偏离值达7%",
                }
            ],
        }

    def get_limit_up_pool(self, *, trade_date, page, size):
        self.limit_calls += 1
        assert page == 1
        assert size == 200
        return {
            "timestamp": int(trade_date.strftime("%Y%m%d")),
            "pagination": {"total": 1, "pages": 1, "page": 1, "size": 200},
            "item": [
                {
                    "thscode": "600000.SH",
                    "continue_day_cnt": 2,
                    "limit_up_reason": "银行",
                }
            ],
        }


def test_fuyao_enhanced_provider_populates_and_reuses_card_cache(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'fuyao-enhanced.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    initialize_database(database_url)
    cache = AShareEnhancedCacheRepository(create_session_factory(database_url))
    client = FakeFuyaoEnhancedClient()
    provider = FuyaoAShareEnhancedDataProvider(client, cache=cache)

    first = provider.get_snapshots(
        ["CN:000001", "CN:600000"],
        as_of=date(2026, 7, 1),
    )
    second = provider.get_snapshots(
        ["CN:000001", "CN:600000"],
        as_of=date(2026, 7, 1),
    )

    assert set(first) == {"CN:000001", "CN:600000"}
    assert first["CN:000001"].dragon_tiger.latest_net_buy_wan == 3600
    assert first["CN:600000"].limit_sentiment.member_status == "limit_up"
    assert first["CN:000001"].fund_flow.trend == "unsupported"
    assert second["CN:000001"].summary == first["CN:000001"].summary
    assert client.dragon_calls == 1
    assert client.limit_calls == 1
    assert provider.cache_hits == 2
    assert provider.cache_misses == 2


def test_fuyao_enhanced_provider_isolates_partial_source_failure():
    provider = FuyaoAShareEnhancedDataProvider(
        FakeFuyaoEnhancedClient(fail_dragon_tiger=True)
    )

    snapshots = provider.get_snapshots(["CN:600000"], as_of=date(2026, 7, 1))

    snapshot = snapshots["CN:600000"]
    assert snapshot.status == "partial"
    assert snapshot.limit_sentiment.member_status == "limit_up"
    assert snapshot.dragon_tiger.recent_records == 0
    assert provider.capability_status["dragon_tiger"] == "error"
    assert provider.capability_status["limit_sentiment"] == "ready"
    assert "dragon source unavailable" in provider.last_errors[0]


class PaginatedFuyaoEnhancedClient(FakeFuyaoEnhancedClient):
    def __init__(self, *, fail_second_page: bool = False):
        super().__init__()
        self.fail_second_page = fail_second_page
        self.limit_pages: list[int] = []

    def get_dragon_tiger(self, *, trade_date):
        del trade_date
        return {"stock_items": []}

    def get_limit_up_pool(self, *, trade_date, page, size):
        del trade_date
        assert size == 200
        self.limit_pages.append(page)
        if page == 2 and self.fail_second_page:
            raise RuntimeError("page 2 unavailable")
        rows = (
            [{"thscode": f"{100000 + index:06d}.SZ"} for index in range(200)]
            if page == 1
            else [
                {
                    "thscode": "600000.SH",
                    "continue_day_cnt": 3,
                    "limit_up_reason": "第二页命中",
                }
            ]
        )
        return {
            "pagination": {"total": 201, "pages": 2, "page": page, "size": 200},
            "item": rows,
        }


def test_fuyao_enhanced_provider_fetches_second_limit_pool_page_before_negative_result():
    client = PaginatedFuyaoEnhancedClient()
    provider = FuyaoAShareEnhancedDataProvider(client)

    snapshots = provider.get_snapshots(["CN:600000"], as_of=date(2026, 7, 1))
    health = summarize_a_share_enhanced_snapshots(snapshots, provider, 1)

    assert client.limit_pages == [1, 2]
    assert snapshots["CN:600000"].limit_sentiment.member_status == "limit_up"
    assert snapshots["CN:600000"].limit_sentiment.member_reason == "第二页命中"
    assert health["a_share_enhanced_limit_sentiment_pages_succeeded"] == "2"
    assert health["a_share_enhanced_limit_sentiment_rows"] == "201"
    assert health["a_share_enhanced_limit_sentiment_coverage"] == "1.000000"
    assert health["a_share_enhanced_limit_sentiment_complete"] == "true"


def test_fuyao_enhanced_provider_does_not_emit_false_negative_after_page_failure():
    client = PaginatedFuyaoEnhancedClient(fail_second_page=True)
    provider = FuyaoAShareEnhancedDataProvider(client)

    snapshots = provider.get_snapshots(["CN:600000"], as_of=date(2026, 7, 1))
    health = summarize_a_share_enhanced_snapshots(snapshots, provider, 1)

    insight = snapshots["CN:600000"].limit_sentiment
    assert client.limit_pages == [1, 2]
    assert insight.member_status == "unknown_due_to_partial_source"
    assert "无法判断" in insight.summary
    assert provider.capability_status["limit_sentiment"] == "partial_page_error"
    assert health["a_share_enhanced_limit_sentiment_pages_requested"] == "2"
    assert health["a_share_enhanced_limit_sentiment_pages_succeeded"] == "1"
    assert health["a_share_enhanced_limit_sentiment_coverage"] == "0.995025"
    assert health["a_share_enhanced_limit_sentiment_complete"] == "false"


def test_enrich_full_market_visible_cards_is_post_rank_and_score_neutral():
    scan = run_daily_scan(
        instrument_ids=["CN:000001"],
        provider=FixtureMarketDataProvider(),
        end=date(2026, 3, 20),
        a_share_enhanced_provider=EmptyAShareEnhancedDataProvider(),
    )
    card = scan.cards[0]
    rank_score = card.rank_score
    quality_score = card.quality_score

    health = enrich_full_market_visible_cards(
        [card],
        provider_mode="free",
        market_provider_name="free",
        as_of=date(2026, 3, 20),
        enhanced_provider=FakeEnhancedProvider(),
    )

    assert card.a_share_enhanced is not None
    assert card.rank_score == rank_score
    assert card.quality_score == quality_score
    assert health["a_share_enhanced_scope"] == (
        "full_market_visible_cards_after_global_ranking"
    )
    assert health["a_share_enhanced_coverage"] == "1.000000"
    assert health["a_share_enhanced_fail_open"] == "true"


def test_enrich_full_market_visible_cards_fails_open_without_touching_scores():
    scan = run_daily_scan(
        instrument_ids=["CN:000001"],
        provider=FixtureMarketDataProvider(),
        end=date(2026, 3, 20),
        a_share_enhanced_provider=EmptyAShareEnhancedDataProvider(),
    )
    card = scan.cards[0]
    original_scores = (card.rank_score, card.quality_score)

    class FailingEnhancedProvider:
        name = "failing_enhanced"
        last_errors: list[str] = []

        def get_snapshots(self, instrument_ids, as_of):
            del instrument_ids, as_of
            raise RuntimeError("enhancement unavailable")

    health = enrich_full_market_visible_cards(
        [card],
        provider_mode="free",
        market_provider_name="free",
        as_of=date(2026, 3, 20),
        enhanced_provider=FailingEnhancedProvider(),
    )

    assert card.a_share_enhanced is None
    assert (card.rank_score, card.quality_score) == original_scores
    assert health["a_share_enhanced_snapshots"] == "0"
    assert health["a_share_enhanced_error_count"] == "1"
    assert health["a_share_enhanced_fail_open"] == "true"


def test_enhanced_provider_factory_routes_to_fuyao_when_configured(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'fuyao-enhanced-factory.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    monkeypatch.setenv("QAGENT_FUYAO_API_KEY", "secret-key")

    provider = build_a_share_enhanced_provider("free", "free")

    assert isinstance(provider, FuyaoAShareEnhancedDataProvider)
    assert provider.name == "fuyao_official_enhanced"
