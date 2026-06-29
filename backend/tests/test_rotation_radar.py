from datetime import date, timedelta

import pandas as pd

from qagent.cards.factor_watch import build_factor_watch_card
from qagent.factors.models import FactorExposure, FactorRanking
from qagent.market.rotation_radar import build_rotation_radar


def test_rotation_radar_surfaces_theme_index_and_etf_layers():
    memory = _card("CN:688008", "澜起科技 688008.SH", 0.86, 1)
    foundry = _card("CN:688981", "中芯国际 688981.SH", 0.82, 2)
    etf = _card("CN:588000", "科创50ETF 588000.SH", 0.77, 5)
    bank = _card("CN:000001", "平安银行 000001.SZ", 0.9, 3)

    radar = build_rotation_radar([memory, foundry, etf, bank], limit=8)

    names = [item.name for item in radar.themes]
    assert "存储芯片" in names
    assert "半导体" in names
    assert "科创板" in names
    assert "ETF/指数工具" in names
    assert radar.themes[0].score >= radar.themes[-1].score
    assert any(
        leader.instrument_label == "科创50ETF 588000.SH"
        for theme in radar.themes
        for leader in theme.leaders
    )


def test_rotation_radar_counts_risk_blocked_cards_without_letting_them_dominate():
    blocked = _card("CN:000063", "中兴通讯 000063.SZ", 0.98, 1)
    clear = _card("CN:688008", "澜起科技 688008.SH", 0.74, 4)
    assert blocked.decision is not None
    blocked.decision.risk_status = "blocked"
    blocked.decision.action = "avoid"
    blocked.opportunity_bucket = "risk_filtered"

    radar = build_rotation_radar([blocked, clear], limit=5)

    blocked_themes = [theme for theme in radar.themes if theme.blocked_count > 0]
    assert blocked_themes
    assert radar.themes[0].name == "存储芯片"
    assert all(theme.score < 0.9 for theme in blocked_themes)


def _card(
    instrument_id: str,
    label: str,
    factor_score: float,
    rank: int,
):
    card = build_factor_watch_card(
        instrument_id,
        _bars(instrument_id),
        _ranking(instrument_id, label, factor_score, rank),
    )
    assert card is not None
    return card


def _ranking(
    instrument_id: str,
    label: str,
    factor_score: float,
    rank: int,
) -> FactorRanking:
    return FactorRanking(
        instrument_id=instrument_id,
        instrument_label=label,
        factor_score=factor_score,
        factor_rank=rank,
        percentile=0.92,
        momentum_score=factor_score,
        trend_quality_score=0.78,
        liquidity_score=0.84,
        low_risk_score=0.7,
        reversal_score=0.58,
        execution_penalty=0.0,
        data_completeness=1.0,
        factor_exposures=[
            FactorExposure(
                factor_id="momentum",
                label="Momentum",
                raw_value=0.12,
                score=factor_score,
                weight=0.3,
                explanation="Momentum contribution.",
            )
        ],
        flags=[],
        missing_data=[],
    )


def _bars(instrument_id: str) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(80):
        close = 10 + index * 0.05
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close - 0.03,
                "high": close + 0.1,
                "low": close - 0.12,
                "close": close,
                "volume": 1_500_000 + index * 2_000,
                "provider": "fixture",
            }
        )
    return pd.DataFrame(rows)
