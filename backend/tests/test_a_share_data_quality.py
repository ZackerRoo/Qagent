from datetime import date

import pandas as pd

from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.cn_context import build_market_context
from qagent.market.data_quality import assess_instrument_data_quality
from qagent.market.tradability import evaluate_tradability
from qagent.market.trading_status import evaluate_trading_status
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.recommendations.cn_execution import build_trading_constraints


def test_a_share_data_quality_blocks_risk_warning_limit_down_and_no_volume():
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 3, 30),
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "volume": 200,
                "provider": "free_cn",
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2026, 3, 31),
                "open": 9.0,
                "high": 9.0,
                "low": 9.0,
                "close": 9.0,
                "volume": 0,
                "provider": "free_cn",
            },
        ]
    )
    label = "*ST测试 000001.SZ"
    constraints = build_trading_constraints("CN:000001", label)
    trading_status = evaluate_trading_status("CN:000001", bars, constraints)
    tradability = evaluate_tradability("CN:000001", label, bars, trading_status, constraints)

    audit = assess_instrument_data_quality(
        "CN:000001",
        label,
        bars,
        trading_status=trading_status,
        tradability=tradability,
        market_context=build_market_context("CN:000001", label),
        adjusted_price_ready=False,
    )

    codes = {issue.code for issue in audit.issues}
    assert audit.status == "blocked"
    assert audit.can_recommend is False
    assert audit.score < 0.4
    assert {
        "risk_warning_name",
        "trading_status_limit_down",
        "no_recent_volume",
        "low_liquidity",
        "missing_adjusted_price",
    }.issubset(codes)
    assert audit.summary.startswith("数据质量阻断")


def test_daily_scan_attaches_a_share_data_quality_audit_to_cards_and_health():
    result = run_daily_scan(
        instrument_ids=["CN:000001"],
        provider=FixtureMarketDataProvider(),
        end=date(2026, 3, 20),
    )

    assert result.cards
    assert result.cards[0].data_quality_audit is not None
    assert result.cards[0].data_quality_audit.status in {"ready", "watch", "blocked"}
    assert any("数据质量" in reason for reason in result.cards[0].rank_reasons)
    assert result.data_health["a_share_quality_audited"] == "1"
    assert result.data_health["a_share_quality_score"]
    assert result.data_health["a_share_quality_ready"] in {"0", "1"}
    assert result.items[0].data_quality_audit is not None
