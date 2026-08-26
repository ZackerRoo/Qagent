from datetime import date, datetime, timezone
from decimal import Decimal

from qagent.research.capacity_stress import build_capacity_stress_report
from qagent.storage.paper import (
    PaperAccountSettings,
    PaperTradeRecord,
    PaperTradeSourceContext,
)


def _account() -> PaperAccountSettings:
    return PaperAccountSettings(
        account_id="paper-account",
        session_id="paper-session",
        label="Research",
        status="active",
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("10"),
        max_positions=10,
        transaction_cost_bps=Decimal("5"),
        slippage_bps=Decimal("5"),
        take_profit_pct=Decimal("50"),
        started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _trade(trade_id: str, instrument_id: str, *, status: str = "open") -> PaperTradeRecord:
    return PaperTradeRecord(
        trade_id=trade_id,
        source_snapshot_id=f"snapshot-{trade_id}",
        provider="free",
        instrument_id=instrument_id,
        strategy_id="quality_value",
        status=status,
        signal_date=date(2026, 8, 3),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        rank_score=Decimal("0.8"),
        entry_date=date(2026, 8, 4) if status == "open" else None,
        entry_price=Decimal("10") if status == "open" else None,
        exit_date=None,
        exit_price=None,
        latest_date=date(2026, 8, 5),
        latest_price=Decimal("10.2"),
        unrealized_return_pct=2.0,
        realized_return_pct=None,
        holding_days=1,
        notes="",
    )


def _context(trade: PaperTradeRecord, average_amount: str | None) -> PaperTradeSourceContext:
    return PaperTradeSourceContext(
        source_snapshot_id=trade.source_snapshot_id,
        created_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        signal_date=trade.signal_date,
        card={
            "instrument_label": "测试标的",
            "asset_type": "stock",
            "tradability": {"avg_amount_20d": average_amount},
        },
    )


def test_capacity_stress_uses_frozen_average_turnover_without_changing_execution():
    trade = _trade("one", "000001.SZ")
    report = build_capacity_stress_report(
        account=_account(),
        trades=[trade],
        source_contexts={trade.trade_id: _context(trade, "100万")},
    )

    holding = report.holdings[0]
    assert report.scope == "research_only"
    assert holding.allocation == Decimal("10000.00")
    assert holding.avg_amount_20d == Decimal("1000000")
    assert holding.participation_rate_pct == 1.0
    assert holding.estimated_impact_bps == 10.0
    assert holding.capacity_status == "review"
    assert report.data_health["capacity_stress_does_not_change_execution"] == "true"


def test_capacity_stress_marks_missing_turnover_and_over_limit_separately():
    missing = _trade("missing", "000001.SZ")
    over_limit = _trade("over-limit", "000002.SZ", status="pending")
    closed = _trade("closed", "000003.SZ", status="time_exit")
    report = build_capacity_stress_report(
        account=_account(),
        trades=[missing, over_limit, closed],
        source_contexts={
            missing.trade_id: _context(missing, None),
            over_limit.trade_id: _context(over_limit, "5万"),
            closed.trade_id: _context(closed, "100万"),
        },
    )

    by_trade = {item.trade_id: item for item in report.holdings}
    assert report.active_holdings == 2
    assert by_trade[missing.trade_id].capacity_status == "missing_adv"
    assert by_trade[over_limit.trade_id].capacity_status == "exceeds_execution_limit"
    assert report.blocked_count == 1
    assert report.holdings_with_adv == 1
