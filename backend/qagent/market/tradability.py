from decimal import Decimal, ROUND_HALF_UP

from qagent.domain.models import (
    TradabilityAssessment,
    TradabilityCheck,
    TradingConstraintProfile,
    TradingStatus,
)


def evaluate_tradability(
    instrument_id: str,
    instrument_label: str | None,
    bars,
    trading_status: TradingStatus | None,
    constraints: TradingConstraintProfile | None,
    min_avg_amount: Decimal = Decimal("10000000"),
    min_avg_volume: int = 100_000,
) -> TradabilityAssessment:
    checks: list[TradabilityCheck] = []
    if bars.empty:
        checks.append(
            TradabilityCheck(
                code="no_daily_bars",
                severity="block",
                title="无日线行情",
                message="数据源没有返回日线，不能判断是否可开仓。",
            )
        )
        return _assessment(checks, None, None)

    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    window = ordered.tail(20)
    avg_volume = int(round(float(window["volume"].mean()))) if "volume" in window else None
    amounts = [
        Decimal(str(row["close"])) * Decimal(str(row["volume"]))
        for _, row in window.iterrows()
        if Decimal(str(row["close"])) > 0
    ]
    avg_amount = sum(amounts, Decimal("0")) / Decimal(len(amounts)) if amounts else None

    label = instrument_label or instrument_id
    if _looks_risk_warning(label):
        checks.append(
            TradabilityCheck(
                code="risk_warning_name",
                severity="block",
                title="风险警示名称",
                message="名称包含 ST、*ST 或退市风险特征，默认不纳入新开仓。",
            )
        )
    if trading_status and not trading_status.can_buy:
        checks.append(
            TradabilityCheck(
                code=f"trading_status_{trading_status.status}",
                severity="block",
                title=trading_status.label,
                message=" ".join(trading_status.notes),
            )
        )
    if avg_volume is not None and avg_volume < min_avg_volume:
        checks.append(
            TradabilityCheck(
                code="low_volume",
                severity="block",
                title="成交量不足",
                message=f"20日平均成交量约 {avg_volume}，低于开仓过滤阈值 {min_avg_volume}。",
            )
        )
    if avg_amount is not None and avg_amount < min_avg_amount:
        checks.append(
            TradabilityCheck(
                code="low_liquidity",
                severity="block",
                title="成交额不足",
                message=f"20日平均成交额约 {_money(avg_amount)}，低于开仓过滤阈值 {_money(min_avg_amount)}。",
            )
        )
    if not _has_recent_volume(ordered):
        checks.append(
            TradabilityCheck(
                code="no_recent_volume",
                severity="block",
                title="疑似停牌或无成交",
                message="最新交易日成交量为 0，默认不新开仓。",
            )
        )
    if constraints and constraints.permission_required:
        checks.append(
            TradabilityCheck(
                code="permission_required",
                severity="warning",
                title="权限确认",
                message=f"{constraints.board}通常需要开通对应交易权限，执行前需要确认账户权限。",
            )
        )

    return _assessment(checks, avg_volume, avg_amount)


def _assessment(
    checks: list[TradabilityCheck],
    avg_volume: int | None,
    avg_amount: Decimal | None,
) -> TradabilityAssessment:
    block_count = sum(1 for check in checks if check.severity == "block")
    warning_count = sum(1 for check in checks if check.severity == "warning")
    score = max(0.0, min(1.0, 1.0 - block_count * 0.45 - warning_count * 0.12))
    can_open = block_count == 0
    if block_count:
        status = "blocked"
        label = "不可开仓"
    elif warning_count:
        status = "watch"
        label = "需确认"
    else:
        status = "clear"
        label = "可交易"
    summary = _summary(label, checks)
    return TradabilityAssessment(
        status=status,
        label=label,
        score=round(score, 4),
        can_open=can_open,
        can_hold=True,
        avg_volume_20d=avg_volume,
        avg_amount_20d=_money(avg_amount) if avg_amount is not None else None,
        checks=checks,
        summary=summary,
    )


def _summary(label: str, checks: list[TradabilityCheck]) -> str:
    if not checks:
        return "可交易性过滤通过：未发现风险警示、涨跌停追买、成交量或成交额硬性阻断。"
    reasons = "；".join(check.title for check in checks[:4])
    return f"{label}：{reasons}。"


def _looks_risk_warning(label: str) -> bool:
    normalized = label.upper().replace(" ", "")
    return (
        normalized.startswith("*ST")
        or normalized.startswith("ST")
        or "退" in normalized[:8]
        or "退市" in normalized
    )


def _has_recent_volume(bars) -> bool:
    latest = bars.iloc[-1]
    if "volume" not in bars.columns:
        return True
    return int(latest["volume"]) > 0


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
