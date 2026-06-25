from decimal import Decimal, ROUND_HALF_UP

from qagent.domain.models import TradingConstraintProfile, TradingStatus


def evaluate_trading_status(
    instrument_id: str,
    bars,
    constraints: TradingConstraintProfile | None = None,
) -> TradingStatus:
    if bars.empty:
        return TradingStatus(
            status="no_data",
            label="无行情",
            severity="block",
            can_buy=False,
            can_sell=False,
            notes=["没有可用日线，不能判断当前是否适合买卖。"],
        )

    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    latest = ordered.iloc[-1]
    latest_close = Decimal(str(latest["close"]))
    if len(ordered) < 2:
        return TradingStatus(
            status="insufficient_history",
            label="历史不足",
            severity="warning",
            latest_close=_money(latest_close),
            can_buy=False,
            can_sell=False,
            notes=["只有一根K线，无法判断涨跌幅与交易状态。"],
        )

    previous = ordered.iloc[-2]
    previous_close = Decimal(str(previous["close"]))
    if previous_close <= 0:
        return TradingStatus(
            status="insufficient_history",
            label="前收盘异常",
            severity="warning",
            latest_close=_money(latest_close),
            previous_close=_money(previous_close),
            can_buy=False,
            can_sell=False,
            notes=["前收盘价异常，无法计算涨跌幅。"],
        )

    change_pct = round(float((latest_close / previous_close - Decimal("1")) * Decimal("100")), 2)
    limit_pct = constraints.price_limit_pct if constraints else None
    limit_up_price = _limit_price(previous_close, limit_pct, up=True)
    limit_down_price = _limit_price(previous_close, limit_pct, up=False)
    status = "normal"
    label = "正常交易"
    severity = "clear"
    can_buy = True
    can_sell = True
    notes = ["当前未触及常规涨跌停约束，可以按计划等待买点或执行风控。"]

    if limit_pct is not None:
        near_limit_up = latest_close >= Decimal(limit_up_price) * Decimal("0.995")
        near_limit_down = latest_close <= Decimal(limit_down_price) * Decimal("1.005")
        if change_pct >= limit_pct - 0.2 or near_limit_up:
            status = "limit_up"
            label = "接近涨停"
            severity = "warning"
            can_buy = False
            can_sell = True
            notes = ["接近或触及涨停，不建议追买；等待开板回落或次日重新评估。"]
        elif change_pct <= -limit_pct + 0.2 or near_limit_down:
            status = "limit_down"
            label = "接近跌停"
            severity = "block"
            can_buy = False
            can_sell = False
            notes = ["接近或触及跌停，流动性和卖出执行风险较高，默认不新开仓。"]

    if constraints and constraints.permission_required:
        notes.append(f"{constraints.board}通常需要开通对应交易权限。")

    if not instrument_id.startswith("CN:"):
        notes = ["非A股标的未使用A股涨跌停规则，仅保留行情状态参考。"]

    return TradingStatus(
        status=status,
        label=label,
        severity=severity,
        latest_close=_money(latest_close),
        previous_close=_money(previous_close),
        change_pct=change_pct,
        limit_up_price=limit_up_price,
        limit_down_price=limit_down_price,
        can_buy=can_buy,
        can_sell=can_sell,
        notes=notes,
    )


def _limit_price(previous_close: Decimal, limit_pct: int | None, *, up: bool) -> str | None:
    if limit_pct is None:
        return None
    multiplier = Decimal("1") + Decimal(limit_pct) / Decimal("100")
    if not up:
        multiplier = Decimal("1") - Decimal(limit_pct) / Decimal("100")
    return _money(previous_close * multiplier)


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
