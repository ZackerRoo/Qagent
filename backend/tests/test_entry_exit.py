from decimal import Decimal

from qagent.cards.entry_exit import build_breakout_plan, build_pead_plan


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
    assert plan.scenario.downside_pct == -3.92
    assert plan.scenario.target_1_pct == 7.84
    assert plan.scenario.no_chase_pct == 3.92
    assert plan.scenario.summary == "At trigger, risk to stop is -3.92%; target 1 is +7.84%."


def test_pead_plan_uses_earnings_day_low_as_invalidation():
    plan = build_pead_plan(
        latest_close=Decimal("82.00"),
        earnings_day_low=Decimal("80.90"),
        earnings_day_high=Decimal("83.20"),
        atr=Decimal("2.05"),
    )

    assert plan.entry_plan.entry_type == "pead"
    assert plan.entry_plan.trigger_price == Decimal("83.20")
    assert plan.entry_plan.entry_zone_low == Decimal("80.90")
    assert plan.entry_plan.no_chase_above == Decimal("84.74")
    assert plan.exit_plan.initial_stop == Decimal("80.90")
    assert plan.exit_plan.target_1 == Decimal("87.80")
    assert plan.risk_reward == 2
    assert "earnings-day low" in plan.exit_plan.invalidation
