from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from qagent.db import create_session_factory, initialize_database
from qagent.research.fuyao_theme_strength import capture_fuyao_theme_strength
from qagent.storage.tables import FuyaoResearchSnapshotRow


class StubThemeClient:
    last_request = None

    def __init__(self) -> None:
        self.calls = 0
        self.multiplier = 1.0

    def get_index_catalog(self, tag: str):
        self.calls += 1
        if tag == "industry":
            return {
                "item": [
                    {"thscode": "881101.TI", "name": "农业"},
                    {"thscode": "881102.TI", "name": "制造"},
                ]
            }
        return {
            "item": [
                {"thscode": "885001.TI", "name": "人工智能"},
                {"thscode": "885002.TI", "name": "机器人"},
            ]
        }

    def get_index_snapshot_data(self, thscodes: list[str]):
        self.calls += 1
        base = {
            "000300.SH": (4000.0, 1.0),
            "881101.TI": (1000.0, 2.0),
            "881102.TI": (1200.0, -1.0),
            "885001.TI": (900.0, 3.0),
            "885002.TI": (800.0, 0.5),
        }
        return {
            "item": [
                {
                    "thscode": thscode,
                    "last_price": base[thscode][0] * self.multiplier,
                    "price_change_ratio_pct": base[thscode][1],
                }
                for thscode in thscodes
            ]
        }

    def get_index_constituents(self, thscode: str):
        self.calls += 1
        return {
            "item": [
                {"thscode": "600519.SH", "name": "贵州茅台"},
                {"thscode": "000001.SZ", "name": "平安银行"},
            ]
        }


def test_theme_strength_persists_daily_snapshot_and_reuses_it(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fuyao-theme-strength.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    client = StubThemeClient()

    captured = capture_fuyao_theme_strength(
        session_factory,
        client=client,
        trade_date=date(2026, 8, 20),
        leading_theme_limit=2,
    )
    reused = capture_fuyao_theme_strength(
        session_factory,
        client=client,
        trade_date=date(2026, 8, 20),
        leading_theme_limit=2,
        reuse_existing=True,
    )

    report = captured.response["sections"]["theme_strength"]
    assert captured.status == "recorded"
    assert captured.response["classification"] == "research_only"
    assert captured.response["decision_weight_applied"] is False
    assert captured.response["paper_order_side_effect"] is False
    assert report["coverage"] == 1.0
    assert report["catalog_count"] == 4
    assert report["leading_themes"][0]["name"] == "人工智能"
    assert report["leading_themes"][0]["relative_1d_pct"] == 2.0
    assert report["leading_themes"][0]["constituent_count"] == 2
    assert report["leading_themes"][0]["constituents"][0]["instrument_id"] == "CN:600519"
    assert reused.status == "existing"
    assert client.calls == 5
    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(FuyaoResearchSnapshotRow))
    assert count == 1


def test_theme_strength_calculates_relative_return_from_prior_snapshots(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fuyao-theme-history.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    client = StubThemeClient()
    for offset in range(5):
        client.multiplier = 1.0 + offset * 0.01
        capture_fuyao_theme_strength(
            session_factory,
            client=client,
            trade_date=date(2026, 8, 3 + offset),
            leading_theme_limit=1,
        )

    client.multiplier = 1.08
    captured = capture_fuyao_theme_strength(
        session_factory,
        client=client,
        trade_date=date(2026, 8, 8),
        leading_theme_limit=1,
    )
    themes = captured.response["sections"]["theme_strength"]["themes"]
    ai = next(item for item in themes if item["thscode"] == "885001.TI")

    assert ai["relative_5d_pct"] == 0.0
