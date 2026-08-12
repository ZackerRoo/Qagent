from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from qagent.db import create_session_factory, initialize_database
from qagent.research.fuyao_market_sentiment import (
    build_fuyao_market_sentiment,
    capture_fuyao_market_research,
)
from qagent.storage.tables import FuyaoResearchSnapshotRow


def _market_sections() -> dict[str, object]:
    return {
        "limit_up_pool": {
            "timestamp": "2026-08-12T15:01:00+08:00",
            "pagination": {"total": 3, "pages": 2, "size": 2, "page": 1},
            "item": [
                {
                    "thscode": "000001.SZ",
                    "name": "平安银行",
                    "continue_day_cnt": 2,
                    "limit_up_reason": "银行+金融科技",
                },
                {
                    "thscode": "600519.SH",
                    "name": "贵州茅台",
                    "continue_day_cnt": 1,
                    "limit_up_reason": "白酒",
                },
            ],
        },
        "limit_up_ladder": {
            "item": [
                {
                    "date": "2026-08-12",
                    "boards": {
                        "3": [
                            {
                                "thscode": "000001.SZ",
                                "name": "平安银行",
                                "board_num": 3,
                            }
                        ]
                    },
                }
            ]
        },
        "hot_stock_list": {
            "item": [
                {"thscode": "000001.SZ", "name": "平安银行", "rank": 1},
                {"thscode": "600519.SH", "name": "贵州茅台", "rank": 8},
            ]
        },
        "skyrocket_list": {
            "item": [
                {"thscode": "000001.SZ", "name": "平安银行", "rank": 2}
            ]
        },
        "anomaly_analysis": {
            "item": [
                {
                    "thscode": "000001.SZ",
                    "name": "平安银行",
                    "keyword_list": ["银行", "资金异动"],
                }
            ]
        },
        "dragon_tiger": {
            "stock_items": [
                {
                    "thscode": "000001.SZ",
                    "name": "平安银行",
                    "hot_rank": 4,
                    "concept_list": [{"name": "大金融"}],
                    "limit_reason": "银行+机构净买",
                }
            ],
            "hot_money_items": [],
        },
    }


class StubMarketClient:
    last_request = None

    def __init__(self) -> None:
        self.calls = 0

    def _section(self, name: str) -> dict[str, object]:
        self.calls += 1
        value = _market_sections()[name]
        assert isinstance(value, dict)
        return value

    def get_limit_up_pool(self, **kwargs):
        return self._section("limit_up_pool")

    def get_limit_up_ladder(self):
        return self._section("limit_up_ladder")

    def get_hot_stock_list(self, **kwargs):
        return self._section("hot_stock_list")

    def get_skyrocket_list(self, **kwargs):
        return self._section("skyrocket_list")

    def get_anomaly_analysis(self):
        return self._section("anomaly_analysis")

    def get_dragon_tiger(self, **kwargs):
        return self._section("dragon_tiger")


class RecoveringMarketClient(StubMarketClient):
    def __init__(self) -> None:
        super().__init__()
        self.fail_dragon_tiger = True

    def get_dragon_tiger(self, **kwargs):
        if self.fail_dragon_tiger:
            self.calls += 1
            self.fail_dragon_tiger = False
            raise ValueError("temporary dragon-tiger failure")
        return super().get_dragon_tiger(**kwargs)


def test_market_sentiment_merges_special_data_without_decision_side_effects():
    sentiment = build_fuyao_market_sentiment(
        _market_sections(),
        trade_date=date(2026, 8, 12),
    )

    assert sentiment.section_coverage == 1.0
    assert sentiment.limit_up_count == 3
    assert sentiment.max_board_count == 3
    assert sentiment.decision_weight_applied is False
    assert sentiment.paper_order_side_effect is False
    assert sentiment.classification == "research_only"
    assert sentiment.source_timestamps == ["2026-08-12T15:01:00+08:00"]
    assert sentiment.leaders[0].instrument_id == "CN:000001"
    assert sentiment.leaders[0].dragon_tiger is True
    assert sentiment.leaders[0].hot_rank == 1
    assert sentiment.leaders[0].themes == [
        "大金融",
        "机构净买",
        "资金异动",
        "金融科技",
        "银行",
    ]
    assert sentiment.top_themes[0].name == "银行"


def test_market_capture_persists_once_and_reuses_the_daily_snapshot(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fuyao-market.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    client = StubMarketClient()

    captured = capture_fuyao_market_research(
        session_factory,
        client=client,
        trade_date=date(2026, 8, 12),
    )
    reused = capture_fuyao_market_research(
        session_factory,
        client=client,
        trade_date=date(2026, 8, 12),
        reuse_existing=True,
    )

    assert captured.status == "recorded"
    assert captured.response["classification"] == "research_only"
    assert captured.response["decision_weight_applied"] is False
    assert captured.response["paper_order_side_effect"] is False
    assert captured.snapshot is not None
    assert reused.status == "existing"
    assert reused.snapshot is not None
    assert reused.snapshot.snapshot_id == captured.snapshot.snapshot_id
    assert client.calls == 6
    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(FuyaoResearchSnapshotRow))
    assert count == 1


def test_market_capture_retries_a_partial_daily_snapshot(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fuyao-market-retry.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    client = RecoveringMarketClient()

    partial = capture_fuyao_market_research(
        session_factory,
        client=client,
        trade_date=date(2026, 8, 12),
    )
    recovered = capture_fuyao_market_research(
        session_factory,
        client=client,
        trade_date=date(2026, 8, 12),
        reuse_existing=True,
    )
    reused = capture_fuyao_market_research(
        session_factory,
        client=client,
        trade_date=date(2026, 8, 12),
        reuse_existing=True,
    )

    assert partial.response["status"] == "partial"
    assert recovered.status == "recorded"
    assert recovered.response["status"] == "ready"
    assert recovered.snapshot is not None
    assert partial.snapshot is not None
    assert recovered.snapshot.snapshot_id != partial.snapshot.snapshot_id
    assert reused.status == "existing"
    assert reused.response["status"] == "ready"
    assert client.calls == 12
    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(FuyaoResearchSnapshotRow))
    assert count == 2
