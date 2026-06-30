from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.domain.models import OpportunityCard


class SignalMonitorItem(BaseModel):
    instrument_id: str
    instrument_label: str | None = None
    state: str
    severity: str
    action: str
    reason: str
    latest_close: Decimal | None = None
    latest_high: Decimal | None = None
    latest_low: Decimal | None = None
    trigger_price: Decimal | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    no_chase_above: Decimal | None = None
    distance_to_trigger_pct: float | None = None
    distance_to_stop_pct: float | None = None
    distance_to_target_pct: float | None = None
    rank_score: float
    risk_status: str | None = None


class SignalMonitorCenter(BaseModel):
    as_of: date
    headline: str
    total: int
    triggered_count: int
    stop_breached_count: int
    near_target_count: int
    target_reached_count: int
    weakened_count: int
    items: list[SignalMonitorItem] = Field(default_factory=list)
    action_queue: list[SignalMonitorItem] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def build_signal_monitor_center(
    cards: list[OpportunityCard],
    *,
    bars_by_instrument: dict[str, object],
    as_of: date | None = None,
    action_limit: int = 12,
) -> SignalMonitorCenter:
    monitor_date = as_of or date.today()
    items = [
        _monitor_item(card, bars_by_instrument.get(card.instrument_id))
        for card in cards
    ]
    triggered = sum(item.state == "entry_triggered" for item in items)
    stop_breached = sum(item.state == "stop_breached" for item in items)
    near_target = sum(item.state == "near_target" for item in items)
    target_reached = sum(item.state == "target_reached" for item in items)
    weakened = sum(item.state == "recommendation_weakened" for item in items)
    queue = sorted(items, key=_action_rank)[:action_limit]
    return SignalMonitorCenter(
        as_of=monitor_date,
        headline=_headline(len(items), triggered, stop_breached, near_target, target_reached, weakened),
        total=len(items),
        triggered_count=triggered,
        stop_breached_count=stop_breached,
        near_target_count=near_target,
        target_reached_count=target_reached,
        weakened_count=weakened,
        items=items,
        action_queue=queue,
        data_health={
            "signal_monitor_total": str(len(items)),
            "signal_monitor_triggered": str(triggered),
            "signal_monitor_stop_breached": str(stop_breached),
            "signal_monitor_near_target": str(near_target),
            "signal_monitor_target_reached": str(target_reached),
            "signal_monitor_weakened": str(weakened),
        },
    )


def _monitor_item(card: OpportunityCard, bars) -> SignalMonitorItem:
    latest = _latest_prices(bars)
    latest_close = latest["close"]
    latest_high = latest["high"]
    latest_low = latest["low"]
    trigger = card.entry_plan.trigger_price
    stop = card.exit_plan.initial_stop
    target = card.exit_plan.target_1
    no_chase = card.entry_plan.no_chase_above
    state = _state(card, latest_close, latest_high, latest_low, trigger, stop, target)
    severity, action, reason = _state_message(state, card, latest_close, trigger, stop, target)
    return SignalMonitorItem(
        instrument_id=card.instrument_id,
        instrument_label=card.instrument_label,
        state=state,
        severity=severity,
        action=action,
        reason=reason,
        latest_close=latest_close,
        latest_high=latest_high,
        latest_low=latest_low,
        trigger_price=trigger,
        initial_stop=stop,
        target_1=target,
        no_chase_above=no_chase,
        distance_to_trigger_pct=_distance_pct(latest_close, trigger),
        distance_to_stop_pct=_distance_pct(latest_close, stop),
        distance_to_target_pct=_distance_pct(latest_close, target),
        rank_score=card.rank_score,
        risk_status=card.decision.risk_status if card.decision else None,
    )


def _state(
    card: OpportunityCard,
    latest_close: Decimal | None,
    latest_high: Decimal | None,
    latest_low: Decimal | None,
    trigger: Decimal | None,
    stop: Decimal | None,
    target: Decimal | None,
) -> str:
    if stop is not None and latest_low is not None and latest_low <= stop:
        return "stop_breached"
    if target is not None and latest_high is not None and latest_high >= target:
        return "target_reached"
    if _is_weakened(card, latest_close, stop):
        return "recommendation_weakened"
    if target is not None and latest_close is not None:
        distance_to_target = _distance_pct(latest_close, target)
        if distance_to_target is not None and 0 <= distance_to_target <= 2.0:
            return "near_target"
    if trigger is not None and latest_high is not None and latest_high >= trigger:
        return "entry_triggered"
    return "watching"


def _state_message(
    state: str,
    card: OpportunityCard,
    latest_close: Decimal | None,
    trigger: Decimal | None,
    stop: Decimal | None,
    target: Decimal | None,
) -> tuple[str, str, str]:
    if state == "stop_breached":
        return (
            "block",
            "跌破止损，取消买入或按计划退出。",
            f"最新价格已触及止损 {stop or '-'}，这条推荐的风险假设失效。",
        )
    if state == "target_reached":
        return (
            "positive",
            "接近或到达目标，考虑分批止盈或上移止损。",
            f"价格已经触及目标一 {target or '-'}，优先保护已兑现收益。",
        )
    if state == "recommendation_weakened":
        return (
            "warning",
            "推荐变弱，降级观察，暂不加仓。",
            f"排序分 {card.rank_score:.0%} 或价格结构转弱，先降低同类信号权重。",
        )
    if state == "near_target":
        return (
            "warning",
            "接近目标，准备分批止盈计划。",
            f"距离目标一 {target or '-'} 已很近，不适合继续追高。",
        )
    if state == "entry_triggered":
        return (
            "positive",
            "买点触发，检查量能和风控后再执行。",
            f"盘中高点已触及触发价 {trigger or '-'}，需要确认没有高于禁追位。",
        )
    return (
        "watch",
        "继续观察，等待触发价确认。",
        f"最新价 {latest_close or '-'} 尚未触发买点或风险线。",
    )


def _latest_prices(bars) -> dict[str, Decimal | None]:
    if bars is None or getattr(bars, "empty", True):
        return {"close": None, "high": None, "low": None}
    latest = bars.sort_values("trade_date").iloc[-1]
    return {
        "close": _decimal_or_none(latest.get("close")),
        "high": _decimal_or_none(latest.get("high")),
        "low": _decimal_or_none(latest.get("low")),
    }


def _is_weakened(
    card: OpportunityCard,
    latest_close: Decimal | None,
    stop: Decimal | None,
) -> bool:
    if card.rank_score < 0.45:
        return True
    if card.decision and card.decision.risk_status == "blocked":
        return True
    if latest_close is not None and stop is not None and latest_close < stop * Decimal("1.02"):
        return True
    return False


def _distance_pct(from_price: Decimal | None, to_price: Decimal | None) -> float | None:
    if from_price is None or to_price is None or from_price <= 0:
        return None
    return round(float((to_price - from_price) / from_price * Decimal("100")), 4)


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _action_rank(item: SignalMonitorItem) -> tuple[int, float]:
    severity_rank = {
        "block": 0,
        "warning": 1,
        "positive": 2,
        "watch": 3,
    }
    state_rank = {
        "stop_breached": 0,
        "recommendation_weakened": 1,
        "target_reached": 2,
        "near_target": 3,
        "entry_triggered": 4,
        "watching": 5,
    }
    return (
        severity_rank.get(item.severity, 9) * 10 + state_rank.get(item.state, 9),
        -item.rank_score,
    )


def _headline(
    total: int,
    triggered: int,
    stop_breached: int,
    near_target: int,
    target_reached: int,
    weakened: int,
) -> str:
    if total == 0:
        return "暂无可监控推荐，先完成一次机会扫描。"
    return (
        f"监控 {total} 条推荐：触发 {triggered}，止损 {stop_breached}，"
        f"接近目标 {near_target}，目标达成 {target_reached}，变弱 {weakened}。"
    )
