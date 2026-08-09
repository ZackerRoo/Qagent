from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.execution.models import OrderSide
from qagent.execution.rules import is_tick_aligned
from qagent.storage.paper import (
    PaperAccountSettings,
    PaperExecutionLegFacts,
    PaperTradeRecord,
)


class PaperExecutionAuditCheck(BaseModel):
    key: str
    label: str
    status: str
    applicable_trades: int = 0
    audited_trades: int = 0
    violations: int = 0
    detail: str


class PaperExecutionRuleAudit(BaseModel):
    schema_version: str = "paper-execution-rule-audit-v1"
    generated_at: datetime
    account_id: str
    session_id: str
    total_trades: int
    entered_trades: int
    execution_fact_trades: int
    legacy_unverified_trades: int
    verdict: str
    checks: list[PaperExecutionAuditCheck] = Field(default_factory=list)


def build_paper_execution_rule_audit(
    trades: list[PaperTradeRecord],
    account: PaperAccountSettings,
    *,
    generated_at: datetime | None = None,
) -> PaperExecutionRuleAudit:
    entered = [trade for trade in trades if trade.entry_date is not None]
    evidenced = [trade for trade in entered if trade.execution_facts is not None]
    legacy_unverified = len(entered) - len(evidenced)

    frozen_fact_violations = 0
    lot_tick_cash_violations = 0
    t_plus_one_applicable = 0
    t_plus_one_audited = 0
    t_plus_one_violations = 0
    for trade in evidenced:
        facts = trade.execution_facts
        if facts is None:
            continue
        if (
            trade.entry_date != facts.entry.trade_date
            or trade.entry_price != facts.entry.price
            or (facts.exit is not None and trade.exit_date != facts.exit.trade_date)
            or (facts.exit is not None and trade.exit_price != facts.exit.price)
        ):
            frozen_fact_violations += 1
        if _execution_leg_violates_contract(
            facts.entry, facts.rules.lot_size, facts.rules.tick_size
        ):
            lot_tick_cash_violations += 1
        if facts.exit is not None:
            if _execution_leg_violates_contract(
                facts.exit,
                facts.rules.lot_size,
                facts.rules.tick_size,
            ):
                lot_tick_cash_violations += 1
            if facts.rules.settlement_days == 1:
                t_plus_one_applicable += 1
                t_plus_one_audited += 1
                if facts.exit.trade_date <= facts.entry.trade_date:
                    t_plus_one_violations += 1

    checks = [
        PaperExecutionAuditCheck(
            key="immutable_execution_facts",
            label="不可变成交事实",
            status=_coverage_status(len(entered), len(evidenced), frozen_fact_violations),
            applicable_trades=len(entered),
            audited_trades=len(evidenced),
            violations=frozen_fact_violations,
            detail="成交后冻结规则版本、成交日期、价格、数量、费用和滑点；旧记录没有事实快照时标记为未核验。",
        ),
        PaperExecutionAuditCheck(
            key="lot_tick_and_cash",
            label="整手、最小价位与现金流",
            status=_evidence_status(len(evidenced), lot_tick_cash_violations),
            applicable_trades=len(evidenced),
            audited_trades=len(evidenced),
            violations=lot_tick_cash_violations,
            detail="逐笔核对数量是否满足冻结整手、价格是否对齐最小价位，以及费用后的买卖现金流。",
        ),
        PaperExecutionAuditCheck(
            key="t_plus_one",
            label="A 股 T+1",
            status=_evidence_status(t_plus_one_applicable, t_plus_one_violations),
            applicable_trades=t_plus_one_applicable,
            audited_trades=t_plus_one_audited,
            violations=t_plus_one_violations,
            detail="仅核验冻结规则 settlement_days=1 且已有卖出事实的交易；同日卖出计为违规。",
        ),
        PaperExecutionAuditCheck(
            key="tradability_guards",
            label="停牌与一字涨跌停",
            status="engine_enforced",
            detail="统一撮合器在生成成交前检查 suspended 和 one_price_limit，命中时延期而不是制造成交。",
        ),
        PaperExecutionAuditCheck(
            key="liquidity_participation",
            label="成交量约束",
            status="engine_enforced",
            detail="统一撮合器按冻结 volume_participation_rate 限制成交量，不足一个整手时不成交。",
        ),
        PaperExecutionAuditCheck(
            key="cost_and_slippage",
            label="成本与滑点",
            status="configured",
            detail=(
                f"账户费用 {account.transaction_cost_bps} bps，滑点 {account.slippage_bps} bps；"
                "每次成交把实际费用和滑点写入不可变成交事实。"
            ),
        ),
    ]
    if any(check.violations > 0 for check in checks):
        verdict = "fail"
    elif not entered:
        verdict = "building_sample"
    elif legacy_unverified > 0:
        verdict = "partial"
    else:
        verdict = "pass"
    return PaperExecutionRuleAudit(
        generated_at=generated_at or datetime.now(timezone.utc),
        account_id=account.account_id,
        session_id=account.session_id,
        total_trades=len(trades),
        entered_trades=len(entered),
        execution_fact_trades=len(evidenced),
        legacy_unverified_trades=legacy_unverified,
        verdict=verdict,
        checks=checks,
    )


def _execution_leg_violates_contract(
    leg: PaperExecutionLegFacts,
    lot_size: int,
    tick_size: Decimal,
) -> bool:
    if leg.quantity % lot_size != 0 or not is_tick_aligned(leg.price, tick_size):
        return True
    if leg.gross_amount != leg.price * leg.quantity:
        return True
    expected_cash_flow = (
        -(leg.gross_amount + leg.total_fees)
        if leg.side == OrderSide.BUY
        else leg.gross_amount - leg.total_fees
    )
    return leg.cash_flow != expected_cash_flow


def _coverage_status(applicable: int, audited: int, violations: int) -> str:
    if violations > 0:
        return "fail"
    if applicable == 0:
        return "not_applicable"
    if audited < applicable:
        return "partial"
    return "pass"


def _evidence_status(applicable: int, violations: int) -> str:
    if violations > 0:
        return "fail"
    if applicable == 0:
        return "not_applicable"
    return "pass"
