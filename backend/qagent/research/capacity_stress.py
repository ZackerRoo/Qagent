from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import math
import re

from pydantic import BaseModel, Field

from qagent.storage.paper import PaperAccountSettings, PaperTradeRecord, PaperTradeSourceContext


CAPACITY_STRESS_SCHEMA_VERSION = "a-share-capacity-stress-v1"
EXECUTION_PARTICIPATION_LIMIT = Decimal("0.10")
REVIEW_PARTICIPATION_THRESHOLD = Decimal("0.01")


class CapacityStressHolding(BaseModel):
    trade_id: str
    instrument_id: str
    instrument_label: str | None = None
    asset_type: str = "unknown"
    status: str
    allocation: Decimal
    avg_amount_20d: Decimal | None = None
    participation_rate_pct: float | None = None
    estimated_impact_bps: float | None = None
    capacity_status: str
    note: str


class CapacityStressReport(BaseModel):
    as_of: date
    scope: str = "research_only"
    model_version: str = CAPACITY_STRESS_SCHEMA_VERSION
    headline: str
    active_holdings: int
    holdings_with_adv: int
    aggregate_allocation: Decimal
    weighted_participation_rate_pct: float | None = None
    weighted_estimated_impact_bps: float | None = None
    review_count: int
    blocked_count: int
    holdings: list[CapacityStressHolding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def build_capacity_stress_report(
    *,
    account: PaperAccountSettings,
    trades: list[PaperTradeRecord],
    source_contexts: dict[str, PaperTradeSourceContext],
) -> CapacityStressReport:
    """Estimate capacity pressure from immutable source-card 20-day turnover.

    This is deliberately research-only. It makes the existing 10% A-share
    volume-participation contract observable without changing admissions,
    fills, or portfolio sizing.
    """

    active = [trade for trade in trades if trade.status in {"pending", "open"}]
    base_allocation = account.initial_capital * account.allocation_per_trade_pct / Decimal("100")
    holdings = [
        _holding(
            trade=trade,
            context=source_contexts.get(trade.trade_id),
            base_allocation=base_allocation,
        )
        for trade in active
    ]
    holdings.sort(key=lambda item: (item.capacity_status != "exceeds_execution_limit", item.instrument_id))
    covered = [item for item in holdings if item.participation_rate_pct is not None]
    aggregate = sum((item.allocation for item in holdings), Decimal("0"))
    weighted_participation = _weighted_mean(
        [(item.allocation, item.participation_rate_pct) for item in covered]
    )
    weighted_impact = _weighted_mean(
        [(item.allocation, item.estimated_impact_bps) for item in covered]
    )
    review_count = sum(item.capacity_status == "review" for item in holdings)
    blocked_count = sum(item.capacity_status == "exceeds_execution_limit" for item in holdings)
    as_of = max(
        (trade.latest_date or trade.entry_date or trade.signal_date for trade in active),
        default=account.started_at.date(),
    )
    warnings = [
        "容量结果仅为研究诊断，不会改变模拟盘入场、仓位或成交。",
        "冲击成本为基于 20 日成交额的平方根代理；真实成交仍以冻结的 A 股执行规则与价格为准。",
    ]
    if len(covered) < len(holdings):
        warnings.append("部分活动仓位缺少冻结时的 20 日成交额，未对这些标的估算参与率。")
    headline = (
        "活动仓位均在冻结成交额的执行参与率内。"
        if holdings and blocked_count == 0
        else "存在超过执行参与率上限的研究压力项。"
        if blocked_count
        else "暂无活动模拟仓位可进行容量估算。"
    )
    return CapacityStressReport(
        as_of=as_of,
        headline=headline,
        active_holdings=len(holdings),
        holdings_with_adv=len(covered),
        aggregate_allocation=aggregate.quantize(Decimal("0.01")),
        weighted_participation_rate_pct=weighted_participation,
        weighted_estimated_impact_bps=weighted_impact,
        review_count=review_count,
        blocked_count=blocked_count,
        holdings=holdings,
        warnings=warnings,
        data_health={
            "capacity_stress_scope": "research_only",
            "capacity_stress_model": CAPACITY_STRESS_SCHEMA_VERSION,
            "capacity_stress_source": "frozen_source_context_tradability_avg_amount_20d",
            "capacity_stress_execution_participation_limit_pct": str(
                float(EXECUTION_PARTICIPATION_LIMIT * 100)
            ),
            "capacity_stress_active_holdings": str(len(holdings)),
            "capacity_stress_adv_coverage": str(len(covered)),
            "capacity_stress_does_not_change_execution": "true",
        },
    )


def _holding(
    *,
    trade: PaperTradeRecord,
    context: PaperTradeSourceContext | None,
    base_allocation: Decimal,
) -> CapacityStressHolding:
    allocation = (
        trade.execution_facts.allocation
        if trade.execution_facts is not None
        else base_allocation * (trade.allocation_multiplier or Decimal("1"))
    ).quantize(Decimal("0.01"))
    card = context.card if context is not None else {}
    tradability = card.get("tradability") if isinstance(card, dict) else None
    values = tradability if isinstance(tradability, dict) else {}
    average_amount = _parse_money(values.get("avg_amount_20d"))
    label = _as_text(card.get("instrument_label")) or _as_text(card.get("name"))
    asset_type = _as_text(card.get("asset_type")) or "unknown"
    if average_amount is None or average_amount <= 0:
        return CapacityStressHolding(
            trade_id=trade.trade_id,
            instrument_id=trade.instrument_id,
            instrument_label=label,
            asset_type=asset_type,
            status=trade.status,
            allocation=allocation,
            capacity_status="missing_adv",
            note="冻结来源卡未提供可解析的 20 日成交额。",
        )
    participation = allocation / average_amount
    participation_pct = round(float(participation * 100), 6)
    impact_bps = round(10 * math.sqrt(float(participation / Decimal("0.01"))), 4)
    if participation > EXECUTION_PARTICIPATION_LIMIT:
        status = "exceeds_execution_limit"
        note = "研究估算超过执行规则的单日 10% 成交额参与率。"
    elif participation >= REVIEW_PARTICIPATION_THRESHOLD:
        status = "review"
        note = "接近执行容量，建议在研究中关注冲击成本与成交额变化。"
    else:
        status = "within_limit"
        note = "低于研究审查阈值与执行参与率上限。"
    return CapacityStressHolding(
        trade_id=trade.trade_id,
        instrument_id=trade.instrument_id,
        instrument_label=label,
        asset_type=asset_type,
        status=trade.status,
        allocation=allocation,
        avg_amount_20d=average_amount,
        participation_rate_pct=participation_pct,
        estimated_impact_bps=impact_bps,
        capacity_status=status,
        note=note,
    )


def _weighted_mean(values: list[tuple[Decimal, float | None]]) -> float | None:
    valid = [(weight, value) for weight, value in values if value is not None and weight > 0]
    if not valid:
        return None
    total_weight = sum((weight for weight, _ in valid), Decimal("0"))
    return round(sum(float(weight) * float(value) for weight, value in valid) / float(total_weight), 6)


def _parse_money(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            parsed = Decimal(str(value))
            return parsed if parsed.is_finite() else None
        except InvalidOperation:
            return None
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace(",", "").replace("¥", "").replace("元", "")
    match = re.fullmatch(r"([+-]?[0-9]+(?:\.[0-9]+)?)\s*([万亿kKmMbB]?)", normalized)
    if match is None:
        return None
    try:
        number = Decimal(match.group(1))
    except InvalidOperation:
        return None
    multiplier = {
        "": Decimal("1"),
        "万": Decimal("10000"),
        "亿": Decimal("100000000"),
        "k": Decimal("1000"),
        "K": Decimal("1000"),
        "m": Decimal("1000000"),
        "M": Decimal("1000000"),
        "b": Decimal("1000000000"),
        "B": Decimal("1000000000"),
    }[match.group(2)]
    parsed = number * multiplier
    return parsed if parsed.is_finite() and parsed > 0 else None


def _as_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
