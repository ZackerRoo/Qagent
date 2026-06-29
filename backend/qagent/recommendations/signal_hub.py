from decimal import Decimal

from qagent.domain.models import (
    OpportunityCard,
    RecommendationTimelineEvent,
    SignalAlertSuggestion,
    SignalHub,
    SignalHubComponent,
    SimilarSignalValidation,
)


def build_signal_hub(
    card: OpportunityCard,
    rotation_score: float | None = None,
    rotation_name: str | None = None,
) -> SignalHub:
    components = _components(card, rotation_score)
    trust_score = _weighted_score(components)
    validation = _similar_validation(card)
    timeline = _timeline(card)
    alerts = _alert_suggestions(card)
    return SignalHub(
        trust_score=round(trust_score, 4),
        label=_trust_label(trust_score),
        verdict=_verdict(card, trust_score, validation),
        rotation_context=rotation_name or _fallback_rotation_context(card),
        next_action=_next_action(card),
        components=components,
        similar_validation=validation,
        timeline=timeline,
        alert_suggestions=alerts,
    )


def _components(card: OpportunityCard, rotation_score: float | None) -> list[SignalHubComponent]:
    calibration = card.strategy_calibration
    history_score = _history_score(card)
    risk_score = _risk_score(card)
    execution_score = card.decision.components.execution_quality if card.decision else 0.5
    rotation = rotation_score if rotation_score is not None else _fallback_rotation_score(card)
    return [
        SignalHubComponent(
            key="rotation",
            label="方向强度",
            score=round(_clamp(rotation), 4),
            status=_status(rotation),
            detail=_rotation_detail(card, rotation),
        ),
        SignalHubComponent(
            key="strategy",
            label="策略质量",
            score=round(_clamp(card.strategy_score), 4),
            status=_status(card.strategy_score),
            detail=f"主策略 {card.primary_strategy_id or '未识别'}，策略分 {card.strategy_score:.2f}。",
        ),
        SignalHubComponent(
            key="factor",
            label="因子排名",
            score=round(_clamp(card.factor_score), 4),
            status=_status(card.factor_score),
            detail=_factor_detail(card),
        ),
        SignalHubComponent(
            key="history",
            label="相似历史",
            score=round(history_score, 4),
            status=calibration.readiness if calibration else "missing_data",
            detail=calibration.message if calibration else "暂无相似信号验证样本。",
        ),
        SignalHubComponent(
            key="risk",
            label="风险过滤",
            score=round(risk_score, 4),
            status=card.decision.risk_status if card.decision else "unknown",
            detail=_risk_detail(card),
        ),
        SignalHubComponent(
            key="execution",
            label="买卖执行",
            score=round(_clamp(execution_score), 4),
            status=card.decision.action if card.decision else "watch",
            detail=_execution_detail(card),
        ),
    ]


def _weighted_score(components: list[SignalHubComponent]) -> float:
    weights = {
        "rotation": 0.18,
        "strategy": 0.2,
        "factor": 0.14,
        "history": 0.18,
        "risk": 0.18,
        "execution": 0.12,
    }
    return _clamp(sum(component.score * weights[component.key] for component in components))


def _similar_validation(card: OpportunityCard) -> SimilarSignalValidation:
    calibration = card.strategy_calibration
    if calibration is None:
        return SimilarSignalValidation(
            readiness="missing_data",
            sample_count=0,
            verdict="样本不足",
            summary="还没有足够历史样本验证这个策略，先按观察信号处理。",
        )
    verdict = _history_verdict(calibration.readiness, calibration.win_rate_10d)
    win_text = "-" if calibration.win_rate_10d is None else f"{calibration.win_rate_10d:.1f}%"
    avg_text = "-" if calibration.avg_return_10d is None else f"{calibration.avg_return_10d:+.2f}%"
    return SimilarSignalValidation(
        readiness=calibration.readiness,
        sample_count=calibration.sample_count,
        win_rate_10d=calibration.win_rate_10d,
        avg_return_10d=calibration.avg_return_10d,
        avg_return_20d=calibration.avg_return_20d,
        max_loss_10d=calibration.max_loss_10d,
        verdict=verdict,
        summary=f"相似信号样本 {calibration.sample_count} 个，10日胜率 {win_text}，10日均值 {avg_text}。",
    )


def _timeline(card: OpportunityCard) -> list[RecommendationTimelineEvent]:
    action = card.decision.action if card.decision else "watch"
    risk_status = card.decision.risk_status if card.decision else "unknown"
    blocked = risk_status == "blocked" or action == "avoid"
    return [
        RecommendationTimelineEvent(
            key="signal_created",
            label="形成推荐",
            status="done",
            severity="info",
            detail=f"系统生成 {card.instrument_label or card.instrument_id} 的机会卡。",
        ),
        RecommendationTimelineEvent(
            key="entry_trigger",
            label="等待买点触发",
            status="blocked" if blocked else "current",
            severity="risk" if blocked else "watch",
            detail=_entry_detail(card, blocked),
        ),
        RecommendationTimelineEvent(
            key="stop_guard",
            label="止损保护",
            status="pending",
            severity="risk",
            detail=f"跌破 {card.exit_plan.initial_stop or '-'} 视为计划失效。",
        ),
        RecommendationTimelineEvent(
            key="target_1",
            label="目标一验证",
            status="pending",
            severity="good",
            detail=f"接近 {card.exit_plan.target_1 or '-'} 后分批止盈或上移止损。",
        ),
        RecommendationTimelineEvent(
            key="time_stop",
            label="时间止损",
            status="pending",
            severity="info",
            detail=card.exit_plan.time_stop,
        ),
    ]


def _alert_suggestions(card: OpportunityCard) -> list[SignalAlertSuggestion]:
    rows = [
        ("entry_trigger", ">=", card.entry_plan.trigger_price, "价格达到计划触发位，检查是否满足买点。"),
        ("stop_guard", "<=", card.exit_plan.initial_stop, "价格跌破止损位，检查是否退出或取消计划。"),
        ("target_1_reached", ">=", card.exit_plan.target_1, "价格接近第一目标，检查是否分批止盈。"),
        (
            "signal_weakened",
            "<=",
            _signal_weaken_threshold(card),
            "价格回落到信号转弱线，检查推荐是否降级。",
        ),
    ]
    suggestions: list[SignalAlertSuggestion] = []
    seen: set[tuple[str, Decimal]] = set()
    symbol = card.instrument_id.replace(":", "-")
    for kind, operator, threshold, rationale in rows:
        if threshold is None:
            continue
        key = (kind, threshold)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(
            SignalAlertSuggestion(
                rule_id=f"{kind}-{symbol}-{str(threshold).replace('.', '_')}",
                instrument_id=card.instrument_id,
                kind=kind,
                operator=operator,
                threshold=threshold,
                rationale=rationale,
            )
        )
    return suggestions


def _history_score(card: OpportunityCard) -> float:
    calibration = card.strategy_calibration
    if calibration is None or calibration.sample_count <= 0:
        return 0.35
    win = (calibration.win_rate_10d or 50.0) / 100
    avg = 0.5 + (calibration.avg_return_10d or 0.0) / 20
    sample = min(1.0, calibration.sample_count / 30)
    readiness_boost = 0.12 if calibration.readiness == "validated" else 0.0
    return _clamp(win * 0.45 + avg * 0.3 + sample * 0.25 + readiness_boost)


def _risk_score(card: OpportunityCard) -> float:
    if not card.decision:
        return 0.5
    if card.decision.risk_status == "blocked" or card.decision.action == "avoid":
        return 0.18
    if card.decision.risk_status == "warning":
        return 0.55
    if card.tradability and not card.tradability.can_open:
        return 0.25
    return 0.88


def _fallback_rotation_score(card: OpportunityCard) -> float:
    if card.opportunity_bucket == "theme_growth":
        return 0.74
    if card.opportunity_bucket == "etf_index":
        return 0.72
    if card.market_context and card.market_context.themes:
        return 0.64
    return 0.5


def _fallback_rotation_context(card: OpportunityCard) -> str | None:
    if not card.market_context:
        return None
    return (card.market_context.themes or [card.market_context.industry])[0]


def _signal_weaken_threshold(card: OpportunityCard) -> Decimal | None:
    if card.exit_plan.initial_stop is not None:
        return card.exit_plan.initial_stop
    if card.entry_plan.trigger_price is None:
        return None
    return (card.entry_plan.trigger_price * Decimal("0.98")).quantize(Decimal("0.01"))


def _rotation_detail(card: OpportunityCard, rotation_score: float) -> str:
    context = _fallback_rotation_context(card) or "未归类方向"
    return f"{context}方向强度 {rotation_score:.2f}，用于判断这不是孤立个股信号。"


def _factor_detail(card: OpportunityCard) -> str:
    rank = "-" if card.factor_rank is None else str(card.factor_rank)
    return f"因子分 {card.factor_score:.2f}，当前排序 {rank}。"


def _risk_detail(card: OpportunityCard) -> str:
    if card.decision and card.decision.risk_vetoes:
        first = card.decision.risk_vetoes[0]
        return f"{first.title}：{first.message}"
    if card.tradability:
        return card.tradability.summary
    return "暂无硬性风险否决。"


def _execution_detail(card: OpportunityCard) -> str:
    return (
        f"触发 {card.entry_plan.trigger_price or '-'}，"
        f"止损 {card.exit_plan.initial_stop or '-'}，"
        f"目标 {card.exit_plan.target_1 or '-'}。"
    )


def _entry_detail(card: OpportunityCard, blocked: bool) -> str:
    if blocked:
        return "当前被风险过滤，先不进入买入执行。"
    return f"等待价格触发 {card.entry_plan.trigger_price or '-'}，且不追高于 {card.entry_plan.no_chase_above or '-'}。"


def _history_verdict(readiness: str, win_rate: float | None) -> str:
    if readiness == "validated" and (win_rate or 0) >= 55:
        return "历史有效"
    if readiness in {"limited_sample", "validated"}:
        return "样本可参考"
    return "样本不足"


def _trust_label(score: float) -> str:
    if score >= 0.72:
        return "高可信"
    if score >= 0.52:
        return "中等可信"
    return "低可信"


def _status(score: float | None) -> str:
    value = score or 0.0
    if value >= 0.72:
        return "strong"
    if value >= 0.52:
        return "watch"
    return "weak"


def _verdict(
    card: OpportunityCard,
    trust_score: float,
    validation: SimilarSignalValidation,
) -> str:
    if card.decision and card.decision.risk_status == "blocked":
        return "信号质量不差，但当前被风险规则挡住，先观察。"
    if trust_score >= 0.72 and validation.sample_count > 0:
        return "方向、策略、历史验证和执行条件同时支持，可进入重点跟踪。"
    if trust_score >= 0.52:
        return "信号具备研究价值，但需要等待买点或更多验证。"
    return "当前证据不足，不适合作为优先交易候选。"


def _next_action(card: OpportunityCard) -> str:
    if card.decision and card.decision.risk_status == "blocked":
        return "先等待风险解除，再重新评估。"
    if card.decision and card.decision.action == "candidate_entry":
        return "检查触发价、成交确认和仓位上限。"
    return "加入观察，等待触发价和方向强度继续确认。"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
