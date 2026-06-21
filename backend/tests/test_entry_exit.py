from decimal import Decimal

from qagent.cards.entry_exit import build_breakout_plan


def test_breakout_plan_has_trigger_stop_target_and_no_chase():
    plan = build_breakout_plan(
        latest_close=Decimal("100"),
        pivot=Decimal("102"),
        atr=Decimal("4"),
    )
    assert plan.entry_plan.trigger_price == Decimal("102")
    assert plan.entry_plan.no_chase_above == Decimal("106")
    assert plan.exit_plan.initial_stop == Decimal("98")
    assert plan.exit_plan.target_1 == Decimal("110")
    assert plan.risk_reward >= 2
