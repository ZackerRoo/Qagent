from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from qagent.execution.models import OrderSide
from qagent.execution.paper_replay import ReplayEvidenceVerdict, replay_paper_evidence
from qagent.storage.paper import PaperReplayEvidenceAuditRecord


BUY_REPLAY_TARGET = 5
SELL_REPLAY_TARGET = 3


class PaperReplaySideProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target: int
    observed: int = 0
    matched: int = 0
    explained_difference: int = 0
    unknown: int = 0


class PaperExecutionReplayReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "paper-execution-replay-readiness-v1"
    generated_at: datetime
    buy: PaperReplaySideProgress
    sell: PaperReplaySideProgress
    unknown_count: int
    audit_build_failures: int
    progress_pct: float
    gate: Literal["collecting", "blocked", "ready_for_shadow"]
    automatic_promotion: Literal[False] = False
    paper_ledger_mutated: Literal[False] = False
    reason: str


def build_execution_replay_readiness(
    audit_records: list[PaperReplayEvidenceAuditRecord],
    *,
    generated_at: datetime | None = None,
) -> PaperExecutionReplayReadiness:
    counters = {
        OrderSide.BUY: {"observed": 0, "matched": 0, "explained": 0, "unknown": 0},
        OrderSide.SELL: {"observed": 0, "matched": 0, "explained": 0, "unknown": 0},
    }
    audit_build_failures = 0
    unclassified_unknown = 0

    for record in audit_records:
        if record.evidence is None:
            audit_build_failures += 1
            unclassified_unknown += 1
            continue
        side = record.evidence.order.side
        counters[side]["observed"] += 1
        try:
            report = replay_paper_evidence(record.evidence)
        except (ValueError, AssertionError):
            counters[side]["unknown"] += 1
            audit_build_failures += 1
            continue
        if report.verdict == ReplayEvidenceVerdict.MATCHED:
            counters[side]["matched"] += 1
        elif report.verdict == ReplayEvidenceVerdict.EXPLAINED_DIFFERENCE:
            counters[side]["explained"] += 1
        else:
            counters[side]["unknown"] += 1

    buy = PaperReplaySideProgress(
        target=BUY_REPLAY_TARGET,
        observed=counters[OrderSide.BUY]["observed"],
        matched=counters[OrderSide.BUY]["matched"],
        explained_difference=counters[OrderSide.BUY]["explained"],
        unknown=counters[OrderSide.BUY]["unknown"],
    )
    sell = PaperReplaySideProgress(
        target=SELL_REPLAY_TARGET,
        observed=counters[OrderSide.SELL]["observed"],
        matched=counters[OrderSide.SELL]["matched"],
        explained_difference=counters[OrderSide.SELL]["explained"],
        unknown=counters[OrderSide.SELL]["unknown"],
    )
    unknown_count = buy.unknown + sell.unknown + unclassified_unknown
    matched_toward_target = min(buy.matched, buy.target) + min(sell.matched, sell.target)
    progress_pct = round(100 * matched_toward_target / (buy.target + sell.target), 1)

    if unknown_count or audit_build_failures:
        gate = "blocked"
        reason = (
            f"发现 {unknown_count} 条未知或不可重放证据，其中 "
            f"{audit_build_failures} 条为证据构建/审计失败；保持只读阻断。"
        )
    elif buy.matched >= buy.target and sell.matched >= sell.target:
        gate = "ready_for_shadow"
        reason = "买卖两侧精确重放样本已达到只读 shadow 观察门槛；不会自动切换执行引擎。"
    elif buy.observed == 0 and sell.observed == 0:
        gate = "collecting"
        reason = "等待新成交自然积累精确重放证据；当前不影响唯一模拟盘。"
    else:
        gate = "collecting"
        reason = (
            f"正在自然积累精确重放样本：买入 matched {buy.matched}/{buy.target}，"
            f"卖出 matched {sell.matched}/{sell.target}；解释性差异不计入门槛。"
        )

    return PaperExecutionReplayReadiness(
        generated_at=generated_at or datetime.now(timezone.utc),
        buy=buy,
        sell=sell,
        unknown_count=unknown_count,
        audit_build_failures=audit_build_failures,
        progress_pct=progress_pct,
        gate=gate,
        reason=reason,
    )
