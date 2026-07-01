from datetime import date

import pandas as pd

from qagent.domain.models import OpportunityCard, StrategyCalibration
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.sector_strength import build_sector_strength
from qagent.market.tradability import evaluate_tradability
from qagent.market.trading_status import evaluate_trading_status
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.recommendations.cn_execution import build_trading_constraints


def test_trading_status_marks_limit_up_as_not_buyable():
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 3, 30),
                "close": 10.0,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 3, 31),
                "close": 11.0,
                "volume": 2_000_000,
            },
        ]
    )

    status = evaluate_trading_status(
        "CN:000001",
        bars,
        build_trading_constraints("CN:000001", "平安银行 000001"),
    )

    assert status.status == "limit_up"
    assert status.can_buy is False
    assert status.can_sell is True
    assert status.change_pct == 10.0
    assert status.limit_up_price == "11.00"


def test_daily_scan_adds_sector_strength_and_strategy_calibration():
    result = run_daily_scan(
        instrument_ids=["US:TEST", "CN:000001"],
        provider=FixtureMarketDataProvider(),
    )

    assert result.sector_strength
    assert any(item.industry == "银行" for item in result.sector_strength)
    assert all(card.strategy_calibration for card in result.cards)
    assert any("策略校准" in reason for card in result.cards for reason in card.rank_reasons)
    assert result.market_intelligence is not None
    assert result.market_intelligence.data_quality.summary
    assert result.market_intelligence.market_environment.summary
    assert result.market_intelligence.strategy_scheduler.weights
    assert result.market_intelligence.event_hypotheses.summary
    assert all(card.dynamic_score is not None for card in result.cards)
    assert any(card.calibration_notes for card in result.cards)
    assert all(card.recommendation_quality for card in result.cards)
    assert any(
        card.recommendation_quality.tier in {"high_quality", "quality_candidate", "watchlist", "risk_filtered"}
        for card in result.cards
        if card.recommendation_quality
    )
    assert "recommendation_quality_cards" in result.data_health


def test_sector_strength_groups_cards_by_cn_industry():
    result = run_daily_scan(
        instrument_ids=["CN:000001"],
        provider=FixtureMarketDataProvider(),
    )

    bars = FixtureMarketDataProvider().get_daily_bars(
        ["CN:000001"],
        date(2026, 1, 1),
        date(2026, 12, 31),
    )
    sectors = build_sector_strength(result.cards, {"CN:000001": bars})

    assert sectors[0].industry == "银行"
    assert sectors[0].leaders[0].instrument_id == "CN:000001"
    assert "银行" in sectors[0].summary


def test_strategy_calibration_model_is_card_serializable():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
    )
    card = OpportunityCard.model_validate(result.cards[0].model_dump())
    card.strategy_calibration = StrategyCalibration(
        strategy_id="pead_earnings_drift",
        readiness="validated",
        sample_count=5,
        win_rate_10d=60.0,
        avg_return_10d=2.4,
        avg_return_20d=4.1,
        max_loss_10d=-3.0,
        message="策略校准：样本5个，10日胜率60.00%。",
    )

    payload = card.model_dump(mode="json")

    assert payload["strategy_calibration"]["strategy_id"] == "pead_earnings_drift"
    assert payload["strategy_calibration"]["message"].startswith("策略校准")


def test_tradability_blocks_low_liquidity_and_risk_warning_names():
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 3, 30),
                "close": 10.0,
                "volume": 100,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 3, 31),
                "close": 10.1,
                "volume": 100,
            },
        ]
    )
    constraints = build_trading_constraints("CN:000001", "*ST测试 000001")
    trading_status = evaluate_trading_status("CN:000001", bars, constraints)

    assessment = evaluate_tradability(
        "CN:000001",
        "*ST测试 000001",
        bars,
        trading_status,
        constraints,
    )

    assert assessment.can_open is False
    assert assessment.status == "blocked"
    assert {check.code for check in assessment.checks}.issuperset(
        {"risk_warning_name", "low_liquidity"}
    )


def test_daily_scan_returns_portfolio_plan_and_tradability():
    result = run_daily_scan(
        instrument_ids=["CN:000001"],
        provider=FixtureMarketDataProvider(),
        end=date(2026, 3, 20),
    )

    assert result.portfolio_plan.eligible_count >= 1
    assert result.portfolio_plan.allocations
    assert result.portfolio_plan.allocations[0].instrument_id == "CN:000001"
    assert result.portfolio_plan.allocations[0].weight_pct > 0
    assert result.cards[0].tradability is not None
    assert result.cards[0].tradability.can_open is True
    assert result.items[0].tradability is not None
