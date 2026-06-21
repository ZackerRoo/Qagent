from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel

from qagent.domain.models import EntryPlan, ExitPlan


class TradePlan(BaseModel):
    entry_plan: EntryPlan
    exit_plan: ExitPlan
    risk_reward: float


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _risk_reward(trigger: Decimal, stop: Decimal, target: Decimal) -> float:
    risk = trigger - stop
    if risk <= 0:
        return 0.0
    return round(float((target - trigger) / risk), 4)


def build_breakout_plan(latest_close: Decimal, pivot: Decimal, atr: Decimal) -> TradePlan:
    trigger = _money(pivot)
    stop = _money(trigger - atr)
    target_1 = _money(trigger + atr * Decimal("2"))
    target_2 = _money(trigger + atr * Decimal("3"))
    no_chase = _money(trigger + atr)
    entry_zone_low = _money(min(latest_close, trigger))

    return TradePlan(
        entry_plan=EntryPlan(
            entry_type="breakout",
            trigger_price=trigger,
            entry_zone_low=entry_zone_low,
            entry_zone_high=trigger,
            confirmation="Price breaks above pivot with volume confirmation.",
            no_chase_above=no_chase,
        ),
        exit_plan=ExitPlan(
            initial_stop=stop,
            invalidation="Breakout fails back below pivot/support with weak follow-through.",
            target_1=target_1,
            target_2=target_2,
            trailing_rule="After target 1, trail below 10EMA or prior swing low.",
            time_stop="Review if no follow-through within 20 trading days.",
        ),
        risk_reward=_risk_reward(trigger, stop, target_1),
    )


def build_pullback_plan(latest_close: Decimal, support: Decimal, atr: Decimal) -> TradePlan:
    trigger = _money(latest_close)
    stop = _money(support - atr)
    target_1 = _money(trigger + atr * Decimal("2"))
    target_2 = _money(trigger + atr * Decimal("3"))

    return TradePlan(
        entry_plan=EntryPlan(
            entry_type="pullback",
            trigger_price=trigger,
            entry_zone_low=_money(support),
            entry_zone_high=trigger,
            confirmation="Price holds support and reclaims short-term strength.",
            no_chase_above=_money(trigger + atr),
        ),
        exit_plan=ExitPlan(
            initial_stop=stop,
            invalidation="Pullback loses support or closes below the rising 50DMA.",
            target_1=target_1,
            target_2=target_2,
            trailing_rule="Trail below 20DMA after the first target.",
            time_stop="Review if setup stalls for 20 trading days.",
        ),
        risk_reward=_risk_reward(trigger, stop, target_1),
    )
