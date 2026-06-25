from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class PositionInput(BaseModel):
    instrument_id: str
    shares: Decimal
    entry_price: Decimal
    entry_date: date
    strategy_tag: str | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    target_2: Decimal | None = None
    thesis: str | None = None


class PositionRisk(BaseModel):
    instrument_id: str
    current_price: Decimal
    unrealized_return_pct: float
    stop_distance_pct: float | None = None
    target_1_distance_pct: float | None = None
    status: str
    action: str
    action_label: str
    severity: str
    should_exit: bool = False
    holding_days: int | None = None
    management_note: str
    next_check: str
    flags: list[str] = Field(default_factory=list)


def unrealized_return_pct(entry_price: Decimal, current_price: Decimal) -> float:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    return round(float((current_price / entry_price - Decimal("1")) * Decimal("100")), 4)


def analyze_position_risk(
    position: PositionInput,
    current_price: Decimal | str,
    current_date: date | None = None,
    max_holding_days: int = 20,
) -> PositionRisk:
    price = Decimal(str(current_price)).quantize(Decimal("0.01"))
    stop_distance = _stop_distance_pct(position.initial_stop, price)
    target_distance = _target_distance_pct(position.target_1, price)
    return_pct = unrealized_return_pct(position.entry_price, price)
    holding_days = _holding_days(position.entry_date, current_date)
    status = _risk_status(
        current_price=price,
        initial_stop=position.initial_stop,
        target_1=position.target_1,
        stop_distance_pct=stop_distance,
        target_1_distance_pct=target_distance,
        unrealized_return_pct=return_pct,
        holding_days=holding_days,
        max_holding_days=max_holding_days,
    )
    management = _management_plan(status, stop_distance, target_distance, holding_days)
    return PositionRisk(
        instrument_id=position.instrument_id,
        current_price=price,
        unrealized_return_pct=return_pct,
        stop_distance_pct=stop_distance,
        target_1_distance_pct=target_distance,
        status=status,
        action=management["action"],
        action_label=management["action_label"],
        severity=management["severity"],
        should_exit=bool(management["should_exit"]),
        holding_days=holding_days,
        management_note=management["management_note"],
        next_check=management["next_check"],
        flags=management["flags"],
    )


def _stop_distance_pct(level: Decimal | None, current_price: Decimal) -> float | None:
    if level is None or current_price <= 0:
        return None
    return round(float(((current_price - level) / current_price) * Decimal("100")), 4)


def _target_distance_pct(level: Decimal | None, current_price: Decimal) -> float | None:
    if level is None or current_price <= 0:
        return None
    return round(float(((level - current_price) / current_price) * Decimal("100")), 4)


def _risk_status(
    current_price: Decimal,
    initial_stop: Decimal | None,
    target_1: Decimal | None,
    stop_distance_pct: float | None,
    target_1_distance_pct: float | None,
    unrealized_return_pct: float,
    holding_days: int | None,
    max_holding_days: int,
) -> str:
    if initial_stop is not None and current_price <= initial_stop:
        return "stop_breached"
    if target_1 is not None and current_price >= target_1:
        return "target_reached"
    if target_1_distance_pct is not None and 0 <= target_1_distance_pct <= 2:
        return "near_target"
    if stop_distance_pct is not None and 0 <= stop_distance_pct <= 2:
        return "near_stop"
    if (
        holding_days is not None
        and holding_days >= max_holding_days
        and unrealized_return_pct < 2
    ):
        return "time_exit_watch"
    return "inside_plan"


def _holding_days(entry_date: date, current_date: date | None) -> int | None:
    if current_date is None:
        return None
    return max((current_date - entry_date).days, 0)


def _management_plan(
    status: str,
    stop_distance_pct: float | None,
    target_1_distance_pct: float | None,
    holding_days: int | None,
) -> dict[str, object]:
    if status == "stop_breached":
        return {
            "action": "stop_loss",
            "action_label": "执行止损",
            "severity": "block",
            "should_exit": True,
            "management_note": "价格已跌破止损/失效位，优先按交易纪律执行止损，不再加仓摊低成本。",
            "next_check": "确认是否已按计划退出，并记录滑点与失效原因。",
            "flags": ["stop_breached", "exit_required"],
        }
    if status == "target_reached":
        return {
            "action": "take_profit",
            "action_label": "止盈或上移止损",
            "severity": "success",
            "should_exit": False,
            "management_note": "价格已到达第一目标位，按计划考虑分批止盈，或把止损上移保护利润。",
            "next_check": "检查目标位成交、剩余仓位和新的跟踪止损。",
            "flags": ["target_reached", "profit_protection"],
        }
    if status == "near_target":
        target_gap = f"{target_1_distance_pct:.2f}%" if target_1_distance_pct is not None else "较近"
        return {
            "action": "trim_or_raise_stop",
            "action_label": "减仓或上移止损",
            "severity": "warning",
            "should_exit": False,
            "management_note": f"价格接近目标位，距离约 {target_gap}，可以准备分批减仓或上移止损，避免盈利回吐。",
            "next_check": "观察是否有效触及目标位，以及放量冲高后是否回落。",
            "flags": ["near_target", "profit_protection"],
        }
    if status == "near_stop":
        stop_gap = f"{stop_distance_pct:.2f}%" if stop_distance_pct is not None else "较近"
        return {
            "action": "reduce_risk",
            "action_label": "降低风险",
            "severity": "warning",
            "should_exit": False,
            "management_note": f"价格接近止损位，距离约 {stop_gap}，不要加仓，先准备执行失效处理。",
            "next_check": "检查是否收盘跌破止损位，或是否出现反包修复。",
            "flags": ["near_stop", "risk_elevated"],
        }
    if status == "time_exit_watch":
        days = f"{holding_days} 天" if holding_days is not None else "一段时间"
        return {
            "action": "time_exit",
            "action_label": "时间退出观察",
            "severity": "warning",
            "should_exit": False,
            "management_note": f"持仓已超过 {days} 且收益进展有限，进入时间退出观察；若没有重新放量或趋势修复，应考虑退出或换到更强机会。",
            "next_check": "重新验证原始买入逻辑、量价延续和机会成本。",
            "flags": ["time_exit_watch", "stalled_trade"],
        }
    return {
        "action": "hold",
        "action_label": "继续持有",
        "severity": "clear",
        "should_exit": False,
        "management_note": "价格仍在交易计划内，继续持有并跟踪止损位、目标位和量价变化。",
        "next_check": "每日检查是否接近止损、目标或出现原始逻辑失效。",
        "flags": ["inside_plan"],
    }
