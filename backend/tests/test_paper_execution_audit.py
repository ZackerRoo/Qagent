from datetime import date, datetime, timezone
from decimal import Decimal

from qagent.execution.models import AShareExecutionRules, OrderSide
from qagent.paper_trading.execution_audit import build_paper_execution_rule_audit
from qagent.storage.paper import (
    PaperAccountSettings,
    PaperExecutionFacts,
    PaperExecutionLegFacts,
    PaperTradeRecord,
)


def _account() -> PaperAccountSettings:
    return PaperAccountSettings(
        account_id="paper-default",
        session_id="session-1",
        label="research",
        status="active",
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("10"),
        max_positions=10,
        transaction_cost_bps=Decimal("8"),
        slippage_bps=Decimal("5"),
        take_profit_pct=Decimal("100"),
        started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _leg(side: OrderSide, trade_date: date, price: str, cash_flow: str):
    return PaperExecutionLegFacts(
        market_event_id=f"event-{side}-{trade_date}",
        side=side,
        trade_date=trade_date,
        base_price=Decimal(price),
        price=Decimal(price),
        quantity=100,
        gross_amount=Decimal(price) * 100,
        commission=Decimal("1"),
        slippage=Decimal("0"),
        cash_flow=Decimal(cash_flow),
    )


def _trade(*, exit_date: date, with_facts: bool = True) -> PaperTradeRecord:
    entry_date = date(2026, 8, 3)
    entry = _leg(OrderSide.BUY, entry_date, "10.00", "-1001.00")
    exit_leg = _leg(OrderSide.SELL, exit_date, "11.00", "1099.00")
    facts = (
        PaperExecutionFacts(
            allocation=Decimal("10000"),
            rules=AShareExecutionRules(settlement_days=1),
            entry=entry,
            exit=exit_leg,
        )
        if with_facts
        else None
    )
    return PaperTradeRecord(
        trade_id=f"trade-{exit_date}-{with_facts}",
        source_snapshot_id="snapshot-1",
        provider="free",
        instrument_id="CN:600000",
        strategy_id="trend",
        status="time_exit",
        signal_date=date(2026, 8, 2),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        rank_score=Decimal("0.8"),
        entry_date=entry_date,
        entry_price=Decimal("10"),
        exit_date=exit_date,
        exit_price=Decimal("11"),
        latest_date=exit_date,
        latest_price=Decimal("11"),
        unrealized_return_pct=None,
        realized_return_pct=10,
        holding_days=1,
        notes="",
        execution_facts=facts,
    )


def test_execution_audit_passes_complete_t_plus_one_facts():
    report = build_paper_execution_rule_audit(
        [_trade(exit_date=date(2026, 8, 4))],
        _account(),
        generated_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )

    assert report.verdict == "pass"
    assert report.execution_fact_trades == 1
    assert report.legacy_unverified_trades == 0
    assert {check.key: check.status for check in report.checks}["t_plus_one"] == "pass"


def test_execution_audit_marks_legacy_records_partial():
    report = build_paper_execution_rule_audit(
        [_trade(exit_date=date(2026, 8, 4), with_facts=False)],
        _account(),
    )

    assert report.verdict == "partial"
    assert report.legacy_unverified_trades == 1
    facts = next(check for check in report.checks if check.key == "immutable_execution_facts")
    assert facts.status == "partial"


def test_execution_audit_rejects_same_day_t_plus_one_exit():
    report = build_paper_execution_rule_audit(
        [_trade(exit_date=date(2026, 8, 3))],
        _account(),
    )

    assert report.verdict == "fail"
    t_plus_one = next(check for check in report.checks if check.key == "t_plus_one")
    assert t_plus_one.violations == 1
    assert t_plus_one.status == "fail"
