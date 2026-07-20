from datetime import date
from decimal import Decimal

import pandas as pd

from qagent.backtesting.engine import BacktestSignal
from qagent.backtesting.execution import (
    HistoricalExecutionRule,
    calculate_round_trip_fees,
    execute_daily_bar_order,
    execution_rules_from_historical,
    round_order_quantity,
)
from qagent.backtesting.portfolio import _candidate_from_signal, _size_trade
from qagent.execution import OrderSide, OrderType, is_tick_aligned
from qagent.historical_evidence.models import HistoricalFeeRule


def _fee(side: str, *, security_type: str = "stock") -> HistoricalFeeRule:
    return HistoricalFeeRule(
        fee_schedule_version="a-share-fees-v1",
        fee_rule_key=f"cn-{security_type}",
        effective_from=date(2023, 8, 28),
        effective_to=date(2025, 12, 31),
        side=side,
        security_type=security_type,
        exchange="ALL",
        commission_bps="3",
        minimum_commission="5",
        stamp_duty_bps="5" if side == "sell" and security_type == "stock" else "0",
        transfer_fee_bps="0.1" if security_type == "stock" else "0",
    )


def _rule(
    *,
    settlement_days: int = 1,
    minimum: int = 100,
    step: int = 100,
) -> HistoricalExecutionRule:
    return HistoricalExecutionRule(
        instrument_id="CN:000001",
        trade_date=date(2025, 1, 2),
        limit_pct="10",
        minimum_order_quantity=minimum,
        quantity_step=step,
        settlement_days=settlement_days,
        ipo_no_limit_sessions=0,
        buy_fee=_fee("buy"),
        sell_fee=_fee("sell"),
        rule_set_version="a-share-rules-v1",
        fee_schedule_version="a-share-fees-v1",
    )


class StaticResolver:
    def __init__(self, rule):
        self.rule = rule

    def resolve(self, instrument_id, trade_date, *, is_st=False):
        return self.rule.model_copy(
            update={"instrument_id": instrument_id, "trade_date": trade_date}
        )


def _signal():
    return BacktestSignal(
        snapshot_id="versioned-rule-test",
        instrument_id="CN:000001",
        signal_date=date(2025, 1, 1),
        primary_strategy_id="trend_momentum_stage2",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal("10.50"),
        initial_stop=Decimal("10.00"),
        target_1=Decimal("11.00"),
        outcome_status="pending",
    )


def _bars():
    return pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 1),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 2),
                "open": 10.5,
                "high": 11.2,
                "low": 10.4,
                "close": 10.8,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 3),
                "open": 10.8,
                "high": 11.2,
                "low": 10.7,
                "close": 11,
                "volume": 1_000_000,
            },
        ]
    )


def test_versioned_quantity_rules_support_main_board_and_bse_steps():
    assert round_order_quantity(Decimal("250"), _rule()) == Decimal("200")
    assert round_order_quantity(
        Decimal("137.9"), _rule(minimum=100, step=1)
    ) == Decimal("137")
    assert round_order_quantity(Decimal("99"), _rule(minimum=100, step=1)) == 0


def test_versioned_round_trip_fees_apply_minimum_stamp_and_transfer_fees():
    fees = calculate_round_trip_fees(
        _rule(),
        entry_value=Decimal("10000"),
        exit_value=Decimal("10000"),
    )

    assert fees == Decimal("15.20")


def test_versioned_settlement_rule_distinguishes_t0_and_t1_exits():
    t0 = _candidate_from_signal(
        _signal(),
        _bars(),
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
        execution_rule_resolver=StaticResolver(_rule(settlement_days=0)),
    )
    t1 = _candidate_from_signal(
        _signal(),
        _bars(),
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
        execution_rule_resolver=StaticResolver(_rule(settlement_days=1)),
    )

    assert t0 is not None and t0.exit_date == date(2025, 1, 2)
    assert t1 is not None and t1.exit_date == date(2025, 1, 3)


def test_versioned_no_limit_rule_does_not_apply_legacy_symbol_limit():
    bars = _bars()
    bars.loc[bars["trade_date"] == date(2025, 1, 2), ["high", "close"]] = [
        15.0,
        15.0,
    ]
    rule = _rule(settlement_days=0).model_copy(
        update={"ipo_no_limit_sessions": 5, "listing_date": date(2025, 1, 2)}
    )

    candidate = _candidate_from_signal(
        _signal().model_copy(update={"trigger_price": Decimal("12")}),
        bars,
        slippage_bps=Decimal("0"),
        max_entry_wait_days=1,
        max_holding_days=2,
        execution_rule_resolver=StaticResolver(rule),
    )

    assert candidate is not None
    assert candidate.entry_date == date(2025, 1, 2)


def test_portfolio_trade_uses_versioned_order_step_and_fee_schedule():
    candidate = _candidate_from_signal(
        _signal(),
        _bars(),
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
        execution_rule_resolver=StaticResolver(
            _rule(settlement_days=1, minimum=100, step=1)
        ),
    )
    trade = _size_trade(
        candidate,
        equity=Decimal("100000"),
        risk_per_trade_pct=Decimal("1"),
        max_positions=5,
        transaction_cost_bps=Decimal("99"),
    )

    assert trade is not None
    assert trade.shares % 1 == 0
    assert trade.shares % 100 != 0
    assert trade.costs < Decimal("100")


def test_daily_bar_adapter_uses_unified_gap_tick_lot_fee_and_blocking_rules():
    rules = execution_rules_from_historical(
        _rule(),
        side=OrderSide.BUY,
        slippage_bps=Decimal("10"),
    )
    previous = {"trade_date": date(2025, 1, 1), "close": Decimal("10")}
    gap = {
        "trade_date": date(2025, 1, 2),
        "open": Decimal("10.50"),
        "high": Decimal("10.60"),
        "low": Decimal("10.40"),
        "close": Decimal("10.55"),
        "volume": 1_000,
    }

    fill = execute_daily_bar_order(
        instrument_id="CN:000001",
        row=gap,
        previous=previous,
        side=OrderSide.BUY,
        quantity=100,
        order_type=OrderType.STOP,
        stop_price=Decimal("10.00"),
        rules=rules,
    )

    assert fill is not None
    assert fill.base_price == Decimal("10.50")
    assert fill.price == Decimal("10.52")
    assert is_tick_aligned(fill.price, rules.tick_size)
    assert fill.total_fees == Decimal("5.01")
    assert (
        execute_daily_bar_order(
            instrument_id="CN:000001",
            row=gap,
            previous=previous,
            side=OrderSide.BUY,
            quantity=150,
            order_type=OrderType.STOP,
            stop_price=Decimal("10.00"),
            rules=rules,
        )
        is None
    )

    for blocked in (
        {**gap, "is_suspended": True},
        {**gap, "volume": 0},
        {
            **gap,
            "open": Decimal("11.00"),
            "high": Decimal("11.00"),
            "low": Decimal("11.00"),
            "close": Decimal("11.00"),
        },
    ):
        assert (
            execute_daily_bar_order(
                instrument_id="CN:000001",
                row=blocked,
                previous=previous,
                side=OrderSide.BUY,
                quantity=100,
                order_type=OrderType.STOP,
                stop_price=Decimal("10.00"),
                rules=rules,
            )
            is None
        )


def test_triggered_stop_waits_through_untradable_limit_down_session():
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 1),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 2),
                "open": 10.4,
                "high": 10.6,
                "low": 10.3,
                "close": 10.4,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 3),
                "open": 9.36,
                "high": 9.36,
                "low": 9.36,
                "close": 9.36,
                "volume": 1_000_000,
            },
            {
                "instrument_id": "CN:000001",
                "trade_date": date(2025, 1, 6),
                "open": 9.0,
                "high": 9.5,
                "low": 8.9,
                "close": 9.2,
                "volume": 1_000_000,
            },
        ]
    )

    candidate = _candidate_from_signal(
        _signal().model_copy(
            update={"target_1": Decimal("20"), "initial_stop": Decimal("10")}
        ),
        bars,
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
        execution_rule_resolver=StaticResolver(_rule(settlement_days=1)),
    )

    assert candidate is not None
    assert candidate.exit_reason == "stopped"
    assert candidate.exit_date == date(2025, 1, 6)
    assert candidate.exit_price == Decimal("9.00")
