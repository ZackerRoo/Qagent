from __future__ import annotations

from collections import Counter
from datetime import date

from pydantic import BaseModel, Field

from qagent.domain.models import OpportunityCard, PortfolioAllocation, PortfolioPlan
from qagent.monitoring.signal_monitor import SignalMonitorCenter
from qagent.research.market_intelligence import MarketIntelligenceCenter
from qagent.strategies.models import StrategyHealth


class StrategyCalibrationAction(BaseModel):
    strategy_id: str
    name: str
    family: str
    action: str
    weight_pct: float | None = None
    sample_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    max_loss_10d: float | None = None
    reason: str


class CalibrationPlaybook(BaseModel):
    summary: str
    raise_weight_count: int
    lower_weight_count: int
    collect_sample_count: int
    strategy_actions: list[StrategyCalibrationAction] = Field(default_factory=list)


class MarketExecutionPolicy(BaseModel):
    regime: str
    label: str
    risk_budget_multiplier: float
    execution_mode: str
    execution_rules: list[str] = Field(default_factory=list)
    preferred_setups: list[str] = Field(default_factory=list)
    avoid_setups: list[str] = Field(default_factory=list)
    summary: str


class PortfolioDecisionPolicy(BaseModel):
    summary: str
    target_positions: int
    suggested_positions: int
    allocated_weight_pct: float
    cash_reserve_pct: float
    max_single_position_pct: float
    total_risk_budget_pct: float
    concentration_warnings: list[str] = Field(default_factory=list)
    conflict_groups: list[str] = Field(default_factory=list)
    positions: list[PortfolioAllocation] = Field(default_factory=list)


class RecommendationDecisionExplanation(BaseModel):
    instrument_id: str
    instrument_label: str | None = None
    action: str
    why_recommended: str
    when_to_buy: str
    when_to_sell: str
    when_not_to_buy: str
    position_note: str
    validation_note: str
    alert_note: str


class ValidationPlaybook(BaseModel):
    summary: str
    linked_count: int
    primary_window: str
    required_metrics: list[str] = Field(default_factory=list)
    sample_notes: list[str] = Field(default_factory=list)


class AlertReadinessItem(BaseModel):
    kind: str
    title: str
    instrument_id: str | None = None
    instrument_label: str | None = None
    condition: str
    action: str
    readiness: str


class AlertReadinessPlaybook(BaseModel):
    summary: str
    total_alerts: int
    ready_count: int
    missing_count: int
    actions: list[AlertReadinessItem] = Field(default_factory=list)


class DecisionQualityCenter(BaseModel):
    as_of: date
    headline: str
    readiness_score: float = Field(ge=0, le=1)
    calibration: CalibrationPlaybook
    market_policy: MarketExecutionPolicy
    portfolio_policy: PortfolioDecisionPolicy
    explanation_cards: list[RecommendationDecisionExplanation] = Field(default_factory=list)
    validation_playbook: ValidationPlaybook
    alert_playbook: AlertReadinessPlaybook
    data_health: dict[str, str] = Field(default_factory=dict)


def build_decision_quality_center(
    *,
    cards: list[OpportunityCard],
    market_intelligence: MarketIntelligenceCenter | None = None,
    portfolio_plan: PortfolioPlan | None = None,
    signal_monitor: SignalMonitorCenter | None = None,
    strategy_health: list[StrategyHealth] | None = None,
    data_health: dict[str, str] | None = None,
    as_of: date | None = None,
) -> DecisionQualityCenter:
    health = strategy_health or []
    calibration = _build_calibration_playbook(market_intelligence, health)
    market_policy = _build_market_policy(market_intelligence)
    portfolio_policy = _build_portfolio_policy(cards, portfolio_plan)
    explanations = _build_explanations(cards, health, signal_monitor)
    validation = _build_validation_playbook(cards, health)
    alerts = _build_alert_playbook(cards, signal_monitor)
    readiness_score = _readiness_score(
        cards=cards,
        calibration=calibration,
        market_policy=market_policy,
        portfolio_policy=portfolio_policy,
        validation=validation,
        alerts=alerts,
    )
    headline = _headline(readiness_score, market_policy, portfolio_policy, calibration)
    return DecisionQualityCenter(
        as_of=as_of or date.today(),
        headline=headline,
        readiness_score=readiness_score,
        calibration=calibration,
        market_policy=market_policy,
        portfolio_policy=portfolio_policy,
        explanation_cards=explanations,
        validation_playbook=validation,
        alert_playbook=alerts,
        data_health={
            **(data_health or {}),
            "decision_quality_cards": str(len(cards)),
            "decision_quality_explanations": str(len(explanations)),
            "decision_quality_alerts": str(alerts.total_alerts),
            "decision_quality_readiness": f"{readiness_score:.2f}",
        },
    )


def _build_calibration_playbook(
    market_intelligence: MarketIntelligenceCenter | None,
    health: list[StrategyHealth],
) -> CalibrationPlaybook:
    health_by_id = {item.strategy_id: item for item in health}
    scheduler_weights = (
        market_intelligence.strategy_scheduler.weights
        if market_intelligence is not None
        else []
    )
    actions: list[StrategyCalibrationAction] = []
    if scheduler_weights:
        for weight in scheduler_weights[:8]:
            item = health_by_id.get(weight.strategy_id)
            actions.append(
                _strategy_action(
                    strategy_id=weight.strategy_id,
                    name=weight.name,
                    family=weight.family,
                    weight_pct=weight.weight_pct,
                    health=item,
                    avoided=(
                        market_intelligence is not None
                        and weight.family in market_intelligence.strategy_scheduler.avoided_families
                    ),
                )
            )
    else:
        for item in health[:8]:
            actions.append(
                _strategy_action(
                    strategy_id=item.strategy_id,
                    name=item.name,
                    family=item.family,
                    weight_pct=None,
                    health=item,
                    avoided=False,
                )
            )

    raise_count = sum(item.action == "提高权重" for item in actions)
    lower_count = sum(item.action == "降低权重" for item in actions)
    collect_count = sum(item.action == "收集样本" for item in actions)
    summary = (
        f"策略校准：提高 {raise_count} 个，降低 {lower_count} 个，"
        f"继续收集样本 {collect_count} 个。"
    )
    if not actions:
        summary = "策略校准：暂无历史样本，先用模拟盘和回测收集表现。"
    return CalibrationPlaybook(
        summary=summary,
        raise_weight_count=raise_count,
        lower_weight_count=lower_count,
        collect_sample_count=collect_count,
        strategy_actions=actions,
    )


def _strategy_action(
    *,
    strategy_id: str,
    name: str,
    family: str,
    weight_pct: float | None,
    health: StrategyHealth | None,
    avoided: bool,
) -> StrategyCalibrationAction:
    sample_count = health.sample_count if health else 0
    win_rate = health.win_rate_10d if health else None
    avg_return = health.avg_return_10d if health else None
    max_loss = health.max_loss_10d if health else None
    if sample_count < 10:
        action = "收集样本"
        reason = "历史样本不足，先保留观察权重，用模拟盘验证。"
    elif avoided or (avg_return is not None and avg_return < 0) or (max_loss is not None and max_loss <= -8):
        action = "降低权重"
        reason = "近期收益或回撤不达标，或当前市场环境不匹配。"
    elif (win_rate is not None and win_rate >= 55) and (avg_return is None or avg_return >= 0):
        action = "提高权重"
        reason = "历史胜率和 10 日均值支持，当前可作为优先策略。"
    else:
        action = "保持权重"
        reason = "表现没有明显失效，但还不足以主动加权。"
    return StrategyCalibrationAction(
        strategy_id=strategy_id,
        name=name,
        family=family,
        action=action,
        weight_pct=weight_pct,
        sample_count=sample_count,
        win_rate_10d=win_rate,
        avg_return_10d=avg_return,
        max_loss_10d=max_loss,
        reason=reason,
    )


def _build_market_policy(
    market_intelligence: MarketIntelligenceCenter | None,
) -> MarketExecutionPolicy:
    if market_intelligence is None:
        return MarketExecutionPolicy(
            regime="unknown",
            label="市场环境待确认",
            risk_budget_multiplier=0.6,
            execution_mode="防守观察",
            execution_rules=["先刷新今日扫描和市场环境，再决定是否执行推荐。"],
            preferred_setups=["低仓位模拟盘"],
            avoid_setups=["无验证追涨"],
            summary="缺少市场环境，默认降低仓位并只做验证。",
        )

    environment = market_intelligence.market_environment
    scheduler = market_intelligence.strategy_scheduler
    label = _regime_label(environment.regime)
    preferred = scheduler.preferred_families or _default_preferred(environment.regime)
    avoid = scheduler.avoided_families or _default_avoids(environment.regime)
    rules = list(scheduler.rules)
    rules.extend(_regime_rules(environment.regime))
    return MarketExecutionPolicy(
        regime=environment.regime,
        label=label,
        risk_budget_multiplier=environment.risk_budget_multiplier,
        execution_mode=scheduler.mode,
        execution_rules=_dedupe(rules)[:6],
        preferred_setups=preferred[:5],
        avoid_setups=avoid[:5],
        summary=f"{label}：{environment.summary} 执行模式为{scheduler.mode}。",
    )


def _build_portfolio_policy(
    cards: list[OpportunityCard],
    portfolio_plan: PortfolioPlan | None,
) -> PortfolioDecisionPolicy:
    plan = portfolio_plan or _fallback_plan(cards)
    positions = [item for item in plan.allocations if item.weight_pct > 0]
    max_single = max((item.weight_pct for item in positions), default=0.0)
    conflicts = _conflict_groups(positions)
    warnings = list(plan.rules[:2])
    warnings.extend(f"{group} 同向仓位偏多，避免一次性买满。" for group in conflicts)
    return PortfolioDecisionPolicy(
        summary=plan.summary,
        target_positions=plan.max_positions,
        suggested_positions=len(plan.allocations),
        allocated_weight_pct=round(plan.allocated_weight_pct, 2),
        cash_reserve_pct=round(max(0.0, 100.0 - plan.allocated_weight_pct), 2),
        max_single_position_pct=round(max_single, 2),
        total_risk_budget_pct=plan.total_risk_budget_pct,
        concentration_warnings=_dedupe(warnings)[:6],
        conflict_groups=conflicts,
        positions=plan.allocations[:8],
    )


def _build_explanations(
    cards: list[OpportunityCard],
    health: list[StrategyHealth],
    signal_monitor: SignalMonitorCenter | None,
) -> list[RecommendationDecisionExplanation]:
    health_by_id = {item.strategy_id: item for item in health}
    monitor_by_id = {
        item.instrument_id: item
        for item in (signal_monitor.items if signal_monitor is not None else [])
    }
    explanations: list[RecommendationDecisionExplanation] = []
    for card in cards[:8]:
        action = card.decision.action_label if card.decision else "观察"
        health_item = health_by_id.get(card.primary_strategy_id or "")
        monitor = monitor_by_id.get(card.instrument_id)
        explanations.append(
            RecommendationDecisionExplanation(
                instrument_id=card.instrument_id,
                instrument_label=card.instrument_label,
                action=action,
                why_recommended=_why_recommended(card),
                when_to_buy=_when_to_buy(card, monitor),
                when_to_sell=_when_to_sell(card, monitor),
                when_not_to_buy=_when_not_to_buy(card, monitor),
                position_note=_position_note(card),
                validation_note=_validation_note(card, health_item),
                alert_note=_alert_note(card),
            )
        )
    return explanations


def _build_validation_playbook(
    cards: list[OpportunityCard],
    health: list[StrategyHealth],
) -> ValidationPlaybook:
    linked = [
        card
        for card in cards
        if card.strategy_calibration is not None and card.strategy_calibration.sample_count > 0
    ]
    best_health = sorted(
        health,
        key=lambda item: (
            item.sample_count,
            item.win_rate_10d or 0,
            item.avg_return_10d or 0,
        ),
        reverse=True,
    )
    primary_window = "样本外"
    sample_notes: list[str] = []
    for item in best_health[:4]:
        sample_notes.append(
            f"{item.name}：样本 {item.sample_count}，10日胜率 {_fmt_pct(item.win_rate_10d)}，10日均值 {_fmt_signed(item.avg_return_10d)}。"
        )
    if not sample_notes:
        sample_notes.append("暂无策略健康样本，先运行回测或把推荐加入模拟盘。")
    return ValidationPlaybook(
        summary=f"当前 {len(linked)} 条推荐已关联历史策略样本，优先用回测和模拟盘验证后再扩大仓位。",
        linked_count=len(linked),
        primary_window=primary_window,
        required_metrics=[
            "1/3/5/10/20日收益",
            "胜率与平均收益",
            "最大回撤与最大连亏",
            "相对指数超额收益",
            "触发率、止损率、目标达成率",
        ],
        sample_notes=sample_notes[:6],
    )


def _build_alert_playbook(
    cards: list[OpportunityCard],
    signal_monitor: SignalMonitorCenter | None,
) -> AlertReadinessPlaybook:
    actions: list[AlertReadinessItem] = []
    for card in cards[:8]:
        actions.extend(_alert_items_for_card(card))
    if signal_monitor is not None:
        for item in signal_monitor.action_queue[:4]:
            actions.append(
                AlertReadinessItem(
                    kind="signal_monitor",
                    title=item.action,
                    instrument_id=item.instrument_id,
                    instrument_label=item.instrument_label,
                    condition=item.reason,
                    action="先处理监控队列里的风险或触发事件。",
                    readiness="ready",
                )
            )
    ready_count = sum(item.readiness == "ready" for item in actions)
    missing_count = len(actions) - ready_count
    return AlertReadinessPlaybook(
        summary=f"可落地 {len(actions)} 条提醒/监控动作，其中 {ready_count} 条条件明确。",
        total_alerts=len(actions),
        ready_count=ready_count,
        missing_count=missing_count,
        actions=actions[:16],
    )


def _alert_items_for_card(card: OpportunityCard) -> list[AlertReadinessItem]:
    rows = [
        ("entry_trigger", "买点触发", ">=", card.entry_plan.trigger_price, "价格触发买点后再检查量能和禁追位。"),
        ("stop_guard", "跌破止损", "<=", card.exit_plan.initial_stop, "跌破止损代表假设失效，取消买入或退出。"),
        ("target_1_reached", "接近目标", ">=", card.exit_plan.target_1, "接近目标后准备分批止盈或上移止损。"),
        ("signal_weakened", "推荐变弱", "<", card.exit_plan.initial_stop, "价格结构变弱时降低权重。"),
    ]
    result: list[AlertReadinessItem] = []
    for kind, title, operator, threshold, action in rows:
        if threshold is None:
            continue
        result.append(
            AlertReadinessItem(
                kind=kind,
                title=title,
                instrument_id=card.instrument_id,
                instrument_label=card.instrument_label,
                condition=f"{operator} {threshold}",
                action=action,
                readiness="ready",
            )
        )
    return result


def _readiness_score(
    *,
    cards: list[OpportunityCard],
    calibration: CalibrationPlaybook,
    market_policy: MarketExecutionPolicy,
    portfolio_policy: PortfolioDecisionPolicy,
    validation: ValidationPlaybook,
    alerts: AlertReadinessPlaybook,
) -> float:
    if not cards:
        return 0
    actionable = sum(
        1
        for card in cards
        if card.decision is not None and card.decision.risk_status != "blocked"
    )
    actionable_ratio = actionable / len(cards)
    validation_ratio = min(1.0, validation.linked_count / max(1, len(cards)))
    alert_ratio = min(1.0, alerts.ready_count / max(1, len(cards) * 3))
    market_component = min(1.0, market_policy.risk_budget_multiplier)
    portfolio_component = 1.0 if portfolio_policy.positions else 0.4
    calibration_component = 0.7 + min(0.2, calibration.raise_weight_count * 0.05) - min(
        0.2,
        calibration.lower_weight_count * 0.04,
    )
    score = (
        actionable_ratio * 0.25
        + validation_ratio * 0.2
        + alert_ratio * 0.18
        + market_component * 0.17
        + portfolio_component * 0.12
        + calibration_component * 0.08
    )
    return round(max(0.0, min(1.0, score)), 4)


def _headline(
    readiness_score: float,
    market_policy: MarketExecutionPolicy,
    portfolio_policy: PortfolioDecisionPolicy,
    calibration: CalibrationPlaybook,
) -> str:
    if readiness_score >= 0.72:
        prefix = "今日可执行度较高"
    elif readiness_score >= 0.5:
        prefix = "今日适合小仓位验证"
    else:
        prefix = "今日以观察和模拟验证为主"
    return (
        f"{prefix}：市场为{market_policy.label}，组合建议 "
        f"{portfolio_policy.suggested_positions}/{portfolio_policy.target_positions} 个仓位，"
        f"{calibration.summary}"
    )


def _fallback_plan(cards: list[OpportunityCard]) -> PortfolioPlan:
    allocations = [
        PortfolioAllocation(
            instrument_id=card.instrument_id,
            instrument_label=card.instrument_label,
            action=card.decision.action if card.decision else "watch_trigger",
            weight_pct=0.0,
            risk_budget_pct=0.0,
            max_position_pct=0.0,
            industry=card.market_context.industry if card.market_context else None,
            rationale="缺少组合计划，先观察。",
        )
        for card in cards[:3]
    ]
    return PortfolioPlan(
        max_positions=3,
        total_risk_budget_pct=0.0,
        allocated_weight_pct=0.0,
        eligible_count=0,
        blocked_count=0,
        allocations=allocations,
        watchlist=[],
        rules=["缺少组合计划时不建议新开仓。"],
        summary="缺少组合计划，先观察。",
    )


def _conflict_groups(positions: list[PortfolioAllocation]) -> list[str]:
    counts = Counter(item.industry or "未分类" for item in positions if item.weight_pct > 0)
    return [industry for industry, count in counts.items() if count >= 2]


def _why_recommended(card: OpportunityCard) -> str:
    label = card.instrument_label or card.instrument_id
    strategy = card.primary_strategy_id or "综合信号"
    quality = (
        f"推荐质量 {card.recommendation_quality.score:.0%}"
        if card.recommendation_quality
        else f"排序分 {card.rank_score:.0%}"
    )
    context = card.market_context.summary if card.market_context else card.rotation_note or "方向待确认"
    return f"{label} 的支持来自 {strategy}、{quality} 和 {context}。"


def _when_to_buy(card: OpportunityCard, monitor) -> str:
    trigger = card.entry_plan.trigger_price
    no_chase = card.entry_plan.no_chase_above
    if monitor is not None and monitor.state == "entry_triggered":
        return f"买点已触发，复核量能后执行；高于 {no_chase} 不追。"
    return f"等待价格站上触发价 {trigger}，且没有高于禁追位 {no_chase}。"


def _when_to_sell(card: OpportunityCard, monitor) -> str:
    stop = card.exit_plan.initial_stop
    target = card.exit_plan.target_1
    if monitor is not None and monitor.state in {"target_reached", "near_target"}:
        return f"已经接近或触及目标 {target}，优先分批止盈并上移止损。"
    return f"跌破 {stop} 退出；接近 {target} 后分批止盈。"


def _when_not_to_buy(card: OpportunityCard, monitor) -> str:
    if card.decision and card.decision.risk_status == "blocked":
        return "风险阻断，当前不买，只保留观察。"
    if card.tradability and not card.tradability.can_open:
        return card.tradability.summary
    if monitor is not None and monitor.state in {"stop_breached", "recommendation_weakened"}:
        return monitor.action
    return "不满足触发价、流动性、止损空间或交易权限时不买。"


def _position_note(card: OpportunityCard) -> str:
    if card.position_scenario:
        return card.position_scenario.summary
    if card.decision:
        return f"单笔风险预算 {card.decision.suggested_risk_pct:.2f}%，最大仓位 {card.decision.max_position_pct:.2f}%。"
    return "缺少仓位测算，默认不新开仓。"


def _validation_note(card: OpportunityCard, health: StrategyHealth | None) -> str:
    if card.strategy_calibration:
        return card.strategy_calibration.message
    if health:
        return (
            f"{health.name} 样本 {health.sample_count}，"
            f"10日胜率 {_fmt_pct(health.win_rate_10d)}。"
        )
    return "暂无相似历史样本，先加入模拟盘验证。"


def _alert_note(card: OpportunityCard) -> str:
    count = len(_alert_items_for_card(card))
    return f"可创建 {count} 条提醒：买点、止损、目标和转弱。"


def _regime_label(regime: str) -> str:
    labels = {
        "risk_on": "趋势进攻",
        "constructive": "结构性机会",
        "mixed": "震荡均衡",
        "risk_off": "防守降仓",
        "thin": "样本不足",
        "unknown": "环境待确认",
    }
    return labels.get(regime, regime)


def _default_preferred(regime: str) -> list[str]:
    if regime in {"risk_on", "constructive"}:
        return ["趋势突破", "强势回踩", "因子轮动"]
    if regime in {"risk_off", "thin"}:
        return ["ETF", "低仓位验证", "防守观察"]
    return ["均衡组合", "回调确认", "主题扩散"]


def _default_avoids(regime: str) -> list[str]:
    if regime in {"risk_off", "thin"}:
        return ["高位追涨", "低流动性小票", "未触发买点"]
    if regime == "mixed":
        return ["满仓单押", "忽略止损"]
    return ["超过禁追位买入"]


def _regime_rules(regime: str) -> list[str]:
    if regime in {"risk_on", "constructive"}:
        return ["允许按触发价分批执行，但仍保留止损和禁追位。"]
    if regime in {"risk_off", "thin"}:
        return ["单笔仓位减半，未触发买点不参与。", "优先 ETF 或等待市场重新转强。"]
    return ["震荡环境先小仓位验证，不同时买入高度同向标的。"]


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "暂无"
    return f"{value:.1f}%"


def _fmt_signed(value: float | None) -> str:
    if value is None:
        return "暂无"
    return f"{value:+.2f}%"


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
