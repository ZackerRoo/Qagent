from qagent.domain.models import (
    ConfidenceDriver,
    ConfidenceExplanation,
    ExecutionPlanSummary,
    OpportunityCard,
)
from qagent.recommendations.summary import ACTION_LABELS_ZH


def build_confidence_explanation(card: OpportunityCard) -> ConfidenceExplanation:
    decision = card.decision
    score = decision.conviction_score if decision else card.rank_score
    label = _confidence_label(score)

    positives = _positive_drivers(card)
    risks = _risk_drivers(card)
    data_checks = _data_checks(card)

    return ConfidenceExplanation(
        score=score,
        label=label,
        summary=_confidence_summary(label, card, positives, risks),
        positive_drivers=positives,
        risk_drivers=risks,
        data_checks=data_checks,
    )


def build_execution_plan(card: OpportunityCard) -> ExecutionPlanSummary:
    decision = card.decision
    action = decision.action if decision else "pending"
    risk_pct = decision.suggested_risk_pct if decision else 0.0
    max_position_pct = decision.max_position_pct if decision else 0.0

    return ExecutionPlanSummary(
        action=action,
        action_label=ACTION_LABELS_ZH.get(action, "观察"),
        buy_zone=_buy_zone(card),
        sell_plan=_sell_plan(card),
        risk_plan=_risk_plan(card),
        position_plan=_position_plan(card, risk_pct, max_position_pct),
        invalidation=card.exit_plan.invalidation,
        next_checklist=_next_checklist(card),
    )


def _positive_drivers(card: OpportunityCard) -> list[ConfidenceDriver]:
    drivers: list[ConfidenceDriver] = []
    if card.primary_strategy_id:
        drivers.append(
            ConfidenceDriver(
                label="主策略",
                value=_strategy_label(card),
                impact="positive",
                weight=round(card.strategy_score, 4),
            )
        )
    if card.rank_score > 0:
        drivers.append(
            ConfidenceDriver(
                label="综合排序",
                value=f"{card.rank_score * 100:.1f} 分",
                impact="positive",
                weight=round(card.rank_score, 4),
            )
        )
    if card.factor_score > 0:
        drivers.append(
            ConfidenceDriver(
                label="因子强度",
                value=f"{card.factor_score * 100:.1f} 分",
                impact="positive",
                weight=round(card.factor_score, 4),
            )
        )
    if card.risk_reward is not None and card.risk_reward >= 1.5:
        drivers.append(
            ConfidenceDriver(
                label="盈亏比",
                value=f"{card.risk_reward:.2f}",
                impact="positive",
                weight=min(round(card.risk_reward / 3, 4), 1.0),
            )
        )
    for reason in card.rank_reasons[:3]:
        drivers.append(
            ConfidenceDriver(
                label="排序理由",
                value=reason,
                impact="positive",
            )
        )
    return _dedupe_drivers(drivers)


def _risk_drivers(card: OpportunityCard) -> list[ConfidenceDriver]:
    drivers: list[ConfidenceDriver] = []
    if card.decision:
        for veto in card.decision.risk_vetoes[:4]:
            drivers.append(
                ConfidenceDriver(
                    label=veto.title,
                    value=veto.message,
                    impact=_impact_from_severity(veto.severity),
                )
            )
    if card.trading_status and not card.trading_status.can_buy:
        drivers.append(
            ConfidenceDriver(
                label=card.trading_status.label,
                value="；".join(card.trading_status.notes) or "当前买入状态受限",
                impact=_impact_from_severity(card.trading_status.severity),
            )
        )
    if card.tradability and not card.tradability.can_open:
        drivers.append(
            ConfidenceDriver(
                label=card.tradability.label,
                value=card.tradability.summary,
                impact="negative",
                weight=card.tradability.score,
            )
        )
    if card.trading_constraints:
        for item in card.trading_constraints.constraints[:3]:
            drivers.append(
                ConfidenceDriver(
                    label=item.title,
                    value=item.message,
                    impact=_impact_from_severity(item.severity),
                )
            )
    if not drivers and card.risk_reward is not None:
        drivers.append(
            ConfidenceDriver(
                label="风险收益",
                value=f"当前盈亏比约 {card.risk_reward:.2f}，仍需等待触发价和成交确认。",
                impact="neutral",
            )
        )
    return _dedupe_drivers(drivers)


def _data_checks(card: OpportunityCard) -> list[ConfidenceDriver]:
    drivers: list[ConfidenceDriver] = []
    if card.decision:
        components = card.decision.components
        drivers.extend(
            [
                ConfidenceDriver(
                    label="策略质量",
                    value=f"{components.strategy_quality * 100:.1f} 分",
                    impact=_impact_from_score(components.strategy_quality),
                    weight=components.strategy_quality,
                ),
                ConfidenceDriver(
                    label="数据质量",
                    value=f"{components.data_quality * 100:.1f} 分",
                    impact=_impact_from_score(components.data_quality),
                    weight=components.data_quality,
                ),
                ConfidenceDriver(
                    label="执行质量",
                    value=f"{components.execution_quality * 100:.1f} 分",
                    impact=_impact_from_score(components.execution_quality),
                    weight=components.execution_quality,
                ),
            ]
        )
    if card.strategy_calibration:
        calibration = card.strategy_calibration
        drivers.append(
            ConfidenceDriver(
                label="历史校准",
                value=(
                    f"{calibration.sample_count} 个样本，"
                    f"10日胜率 {_fmt_pct(calibration.win_rate_10d)}，"
                    f"10日均收益 {_fmt_pct(calibration.avg_return_10d)}"
                ),
                impact=_impact_from_readiness(calibration.readiness),
            )
        )
    for caveat in card.data_caveats[:3]:
        drivers.append(
            ConfidenceDriver(
                label="数据提示",
                value=caveat,
                impact="neutral",
            )
        )
    if card.tradability:
        drivers.append(
            ConfidenceDriver(
                label="可交易性",
                value=card.tradability.summary,
                impact=_impact_from_score(card.tradability.score),
                weight=card.tradability.score,
            )
        )
    return _dedupe_drivers(drivers)


def _buy_zone(card: OpportunityCard) -> str:
    trigger = _price(card.entry_plan.trigger_price)
    no_chase = _price(card.entry_plan.no_chase_above)
    low = _price(card.entry_plan.entry_zone_low)
    high = _price(card.entry_plan.entry_zone_high)
    if low != "-" and high != "-":
        return f"买入观察区间 {low}-{high}；确认价 {trigger}；高于 {no_chase} 不追。"
    return f"收盘或盘中确认站上 {trigger} 后再考虑；高于 {no_chase} 不追。"


def _sell_plan(card: OpportunityCard) -> str:
    stop = _price(card.exit_plan.initial_stop)
    target_1 = _price(card.exit_plan.target_1)
    target_2 = _price(card.exit_plan.target_2)
    if target_2 != "-":
        return f"跌破 {stop} 退出；接近 {target_1} 先分批止盈，强势延伸看 {target_2}。"
    return f"跌破 {stop} 退出；接近 {target_1} 分批止盈或上移止损。"


def _risk_plan(card: OpportunityCard) -> str:
    if card.decision and card.decision.risk_vetoes:
        titles = "、".join(veto.title for veto in card.decision.risk_vetoes[:3])
        return f"先处理风险项：{titles}；未消除前降级为观察。"
    return "先设止损再下单；若触发价、止损价或成交确认缺失，默认不新开仓。"


def _position_plan(card: OpportunityCard, risk_pct: float, max_position_pct: float) -> str:
    if risk_pct <= 0:
        return "当前不建议新开仓，保留观察。"
    lot_note = "A股按100股整数倍测算" if card.market.value == "CN" else "按账户交易规则测算"
    return f"单笔风险预算 {risk_pct:.2f}%，最大仓位 {max_position_pct:.2f}%；{lot_note}。"


def _next_checklist(card: OpportunityCard) -> list[str]:
    checks: list[str] = []
    if card.decision:
        checks.extend(card.decision.verification_checks[:3])
        checks.extend(card.decision.failure_conditions[:2])
    if card.trading_status and not card.trading_status.can_buy:
        checks.append("先确认交易状态恢复正常。")
    if card.tradability and not card.tradability.can_open:
        checks.append("先解决可交易性阻断项。")
    if card.market_context:
        checks.append(f"复核是否仍跟随{card.market_context.industry}方向。")
    return _dedupe_items(checks)


def _confidence_label(score: float) -> str:
    if score >= 0.72:
        return "高可信"
    if score >= 0.5:
        return "中等可信"
    return "低可信"


def _confidence_summary(
    label: str,
    card: OpportunityCard,
    positives: list[ConfidenceDriver],
    risks: list[ConfidenceDriver],
) -> str:
    label_name = card.instrument_label or card.instrument_id
    positive = positives[0].value if positives else "信号仍在形成"
    risk = risks[0].label if risks else "暂无硬性风险项"
    return f"{label_name} 当前为{label}机会；主要支持来自 {positive}，主要约束是 {risk}。"


def _strategy_label(card: OpportunityCard) -> str:
    evaluation = next(
        (
            item
            for item in card.strategy_evaluations
            if item.strategy_id == card.primary_strategy_id
        ),
        None,
    )
    if evaluation is None:
        return card.primary_strategy_id or "综合信号"
    return f"{evaluation.name}（{evaluation.status}）"


def _impact_from_severity(severity: str) -> str:
    if severity in {"block", "warning", "error"}:
        return "negative"
    if severity in {"info", "neutral"}:
        return "neutral"
    return "positive"


def _impact_from_score(score: float) -> str:
    if score >= 0.7:
        return "positive"
    if score >= 0.45:
        return "neutral"
    return "negative"


def _impact_from_readiness(readiness: str) -> str:
    if readiness == "validated":
        return "positive"
    if readiness in {"watch", "limited_sample"}:
        return "neutral"
    return "negative"


def _fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}%"


def _price(value) -> str:
    return "-" if value is None else str(value)


def _dedupe_drivers(items: list[ConfidenceDriver]) -> list[ConfidenceDriver]:
    result: list[ConfidenceDriver] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.label, item.value)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_items(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
