from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable

from pydantic import BaseModel, Field

from qagent.domain.models import OpportunityCard, SignalAlertSuggestion
from qagent.market.instruments import format_instrument_label
from qagent.recommendations.signal_hub import build_signal_hub
from qagent.research.market_intelligence import MarketIntelligenceCenter
from qagent.strategies.models import StrategyHealth


class TodayActionItem(BaseModel):
    kind: str
    priority: str
    instrument_id: str | None = None
    instrument_label: str | None = None
    title: str
    action: str
    reason: str
    trigger_price: Decimal | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    no_chase_above: Decimal | None = None
    score: float | None = None
    expected_window: str | None = None


class AlertLoopItem(BaseModel):
    kind: str
    status: str
    instrument_id: str | None = None
    instrument_label: str | None = None
    title: str
    action: str
    rationale: str
    operator: str | None = None
    threshold: Decimal | None = None
    source_rule_id: str | None = None


class DataSourceUpgradeItem(BaseModel):
    area: str
    status: str
    priority: str
    title: str
    current_source: str
    recommended_source: str
    impact: str
    user_value: str


class StrategyEffectivenessItem(BaseModel):
    strategy_id: str
    name: str
    family: str
    readiness: str
    verdict: str
    sample_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    max_loss_10d: float | None = None
    weight_pct: float | None = None
    action: str


class ManualActionCenter(BaseModel):
    as_of: str
    headline: str
    today_actions: list[TodayActionItem] = Field(default_factory=list)
    alert_loop: list[AlertLoopItem] = Field(default_factory=list)
    data_source_roadmap: list[DataSourceUpgradeItem] = Field(default_factory=list)
    strategy_effectiveness: list[StrategyEffectivenessItem] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def build_manual_action_center(
    *,
    cards: list[OpportunityCard],
    market_intelligence: MarketIntelligenceCenter | dict[str, object] | None = None,
    strategy_health: list[StrategyHealth | dict[str, object]] | None = None,
    data_health: dict[str, str] | None = None,
    alert_rules: Iterable[object] | None = None,
) -> ManualActionCenter:
    center = _coerce_market_intelligence(market_intelligence)
    health = _coerce_strategy_health(strategy_health or [])
    health_data = data_health or {}
    sorted_cards = _sorted_cards(cards)
    today_actions = _today_actions(sorted_cards, center)
    alert_loop = _alert_loop(sorted_cards, center, alert_rules)
    data_source_roadmap = _data_source_roadmap(center, health_data)
    strategy_effectiveness = _strategy_effectiveness(health, center, sorted_cards)
    headline = _headline(today_actions, alert_loop, strategy_effectiveness)
    merged_health = {
        **health_data,
        "manual_action_cards": str(len(cards)),
        "manual_action_actions": str(len(today_actions)),
        "manual_action_alerts": str(len(alert_loop)),
        "manual_action_data_roadmap": str(len(data_source_roadmap)),
        "manual_action_strategies": str(len(strategy_effectiveness)),
    }
    return ManualActionCenter(
        as_of=date.today().isoformat(),
        headline=headline,
        today_actions=today_actions,
        alert_loop=alert_loop,
        data_source_roadmap=data_source_roadmap,
        strategy_effectiveness=strategy_effectiveness,
        data_health=merged_health,
    )


def _coerce_market_intelligence(
    value: MarketIntelligenceCenter | dict[str, object] | None,
) -> MarketIntelligenceCenter | None:
    if value is None:
        return None
    if isinstance(value, MarketIntelligenceCenter):
        return value
    if isinstance(value, dict):
        try:
            return MarketIntelligenceCenter.model_validate(value)
        except Exception:
            return None
    return None


def _coerce_strategy_health(
    values: list[StrategyHealth | dict[str, object]],
) -> list[StrategyHealth]:
    health: list[StrategyHealth] = []
    for value in values:
        if isinstance(value, StrategyHealth):
            health.append(value)
        elif isinstance(value, dict):
            try:
                health.append(StrategyHealth.model_validate(value))
            except Exception:
                continue
    return health


def _sorted_cards(cards: list[OpportunityCard]) -> list[OpportunityCard]:
    return sorted(
        cards,
        key=lambda card: (
            card.dynamic_score
            if card.dynamic_score is not None
            else card.rank_score * 0.65 + card.factor_score * 0.35
        ),
        reverse=True,
    )


def _today_actions(
    cards: list[OpportunityCard],
    center: MarketIntelligenceCenter | None,
) -> list[TodayActionItem]:
    actions: list[TodayActionItem] = []
    regime = center.market_environment.regime if center else "unknown"
    risk_off = regime in {"risk_off", "thin"}
    for card in cards[:12]:
        decision = card.decision
        risk_status = decision.risk_status if decision else "unknown"
        action = decision.action if decision else "watch"
        if risk_status == "blocked" or action == "avoid":
            if len(actions) < 5:
                actions.append(_avoid_action(card, regime))
            continue
        if len(actions) >= 6:
            break
        if action in {"candidate_entry", "watch_trigger", "wait_pullback", "watch"}:
            actions.append(_entry_action(card, regime, risk_off))

    if center and center.market_environment.warnings:
        actions.append(
            TodayActionItem(
                kind="market_check",
                priority="medium",
                title="先确认市场环境",
                action="盘前先看指数、成交额和涨跌家数，市场走弱时降低仓位。",
                reason=center.market_environment.summary,
                score=center.market_environment.score,
                expected_window="盘前/盘中",
            )
        )
    return actions[:7]


def _entry_action(card: OpportunityCard, regime: str, risk_off: bool) -> TodayActionItem:
    decision = card.decision
    trigger_price = decision.trigger_price if decision and decision.trigger_price else card.entry_plan.trigger_price
    initial_stop = decision.initial_stop if decision and decision.initial_stop else card.exit_plan.initial_stop
    target_1 = decision.target_1 if decision and decision.target_1 else card.exit_plan.target_1
    no_chase = (
        decision.no_chase_above if decision and decision.no_chase_above else card.entry_plan.no_chase_above
    )
    score = _score(card)
    priority = "high" if score >= 0.72 and not risk_off else "medium"
    label = _label(card)
    if risk_off:
        action = f"{label} 只观察，不追高；等触发价和市场环境同时改善再处理。"
        kind = "wait_trigger"
    elif trigger_price is not None:
        action = f"{label} 等待触发价 {trigger_price}，放量确认后再考虑小仓验证。"
        kind = "buy_watch"
    else:
        action = f"{label} 先放入观察池，等价格结构和成交确认后再处理。"
        kind = "review"
    reason = _action_reason(card, regime)
    return TodayActionItem(
        kind=kind,
        priority=priority,
        instrument_id=card.instrument_id,
        instrument_label=card.instrument_label or format_instrument_label(card.instrument_id),
        title=f"{label}：{_action_label(card)}",
        action=action,
        reason=reason,
        trigger_price=trigger_price,
        initial_stop=initial_stop,
        target_1=target_1,
        no_chase_above=no_chase,
        score=round(score, 4),
        expected_window=decision.horizon if decision else "5-20d",
    )


def _avoid_action(card: OpportunityCard, regime: str) -> TodayActionItem:
    label = _label(card)
    reason = "；".join(
        item.message for item in (card.decision.risk_vetoes if card.decision else [])[:2]
    )
    if not reason:
        reason = f"市场环境 {regime} 或交易约束导致当前不适合新开仓。"
    return TodayActionItem(
        kind="avoid",
        priority="high",
        instrument_id=card.instrument_id,
        instrument_label=card.instrument_label or format_instrument_label(card.instrument_id),
        title=f"{label}：暂不买",
        action="不加入今日买入候选，除非风险阻断解除后重新扫描。",
        reason=reason,
        trigger_price=card.entry_plan.trigger_price,
        initial_stop=card.exit_plan.initial_stop,
        target_1=card.exit_plan.target_1,
        no_chase_above=card.entry_plan.no_chase_above,
        score=round(_score(card), 4),
        expected_window="今日不处理",
    )


def _alert_loop(
    cards: list[OpportunityCard],
    center: MarketIntelligenceCenter | None,
    alert_rules: Iterable[object] | None,
) -> list[AlertLoopItem]:
    existing = _existing_alert_rule_keys(alert_rules)
    rows: list[AlertLoopItem] = []
    for card in cards[:5]:
        suggestions = _alert_suggestions(card)
        for suggestion in suggestions:
            key = (suggestion.instrument_id, suggestion.kind, suggestion.operator, str(suggestion.threshold))
            rows.append(
                AlertLoopItem(
                    kind=suggestion.kind,
                    status="ready" if key in existing else "suggested",
                    instrument_id=suggestion.instrument_id,
                    instrument_label=card.instrument_label or format_instrument_label(card.instrument_id),
                    title=_alert_title(suggestion.kind, card),
                    action=_alert_action(suggestion),
                    rationale=suggestion.rationale,
                    operator=suggestion.operator,
                    threshold=suggestion.threshold,
                    source_rule_id=suggestion.rule_id,
                )
            )
    if center and center.market_environment.regime in {"risk_off", "thin"}:
        rows.insert(
            0,
            AlertLoopItem(
                kind="market_regime",
                status="manual",
                title="市场环境提醒",
                action="指数跌破关键均线或涨跌家数继续恶化时，暂停新开仓。",
                rationale=center.market_environment.summary,
            ),
        )
    return rows[:18]


def _alert_suggestions(card: OpportunityCard) -> list[SignalAlertSuggestion]:
    if card.signal_hub:
        return card.signal_hub.alert_suggestions
    try:
        return build_signal_hub(card).alert_suggestions
    except Exception:
        return []


def _existing_alert_rule_keys(alert_rules: Iterable[object] | None) -> set[tuple[str, str, str, str]]:
    keys: set[tuple[str, str, str, str]] = set()
    if alert_rules is None:
        return keys
    for rule in alert_rules:
        instrument_id = str(getattr(rule, "instrument_id", ""))
        kind = str(getattr(rule, "kind", ""))
        operator = str(getattr(rule, "operator", ""))
        threshold = str(getattr(rule, "threshold", ""))
        if instrument_id and kind and operator and threshold:
            keys.add((instrument_id, kind, operator, threshold))
    return keys


def _data_source_roadmap(
    center: MarketIntelligenceCenter | None,
    data_health: dict[str, str],
) -> list[DataSourceUpgradeItem]:
    quality = center.data_quality if center else None
    provider = data_health.get("provider", "free")
    rows = [
        _roadmap_item(
            "adjusted_price",
            "复权行情",
            quality.adjustment_status if quality else "unknown",
            "high",
            provider,
            "AkShare/东方财富复权行情，后续可换 Wind/Tushare Pro",
            "回测和均线信号需要复权，否则除权后会误判趋势和止损。",
            "让推荐和回测更接近真实可交易收益。",
        ),
        _roadmap_item(
            "suspension",
            "停牌状态",
            quality.suspension_status if quality else "unknown",
            "high",
            provider,
            "交易所停复牌、AkShare 停牌列表或付费行情源",
            "避免推荐无法买入、无法卖出的股票。",
            "减少点开后才发现不能交易的情况。",
        ),
        _roadmap_item(
            "limit_status",
            "涨跌停/交易约束",
            quality.limit_status if quality else "unknown",
            "high",
            provider,
            "交易所涨跌停价、实时盘口或高频快照",
            "A 股买点必须知道是否涨停、跌停、临停或权限受限。",
            "提醒用户能不能买、能不能卖，而不是只给分数。",
        ),
        _roadmap_item(
            "index_membership",
            "指数成分",
            "partial" if quality else "unknown",
            "medium",
            provider,
            "中证指数/交易所成分、ETF 持仓文件",
            "科创50、沪深300、半导体 ETF 需要知道成分和权重。",
            "用户选择全 A 时自然包含 ETF、指数和主题方向。",
        ),
        _roadmap_item(
            "industry",
            "行业/主题分类",
            quality.industry_status if quality else "partial",
            "medium",
            provider,
            "申万/中信行业、概念板块、ETF 主题标签",
            "板块轮动和同向风险需要可靠分类。",
            "能解释为什么芯片、存储、机器人等方向进入或退出机会池。",
        ),
        _roadmap_item(
            "fund_flow",
            "资金流",
            "missing",
            "high",
            provider,
            "东方财富资金流、同花顺热度、北向/主力资金",
            "短线机会需要确认资金是否真的流入。",
            "减少只因价格形态好看而追高的误报。",
        ),
        _roadmap_item(
            "dragon_tiger",
            "龙虎榜",
            "missing",
            "medium",
            provider,
            "交易所龙虎榜、东方财富龙虎榜",
            "强势题材需要识别游资席位和异常换手。",
            "帮助用户判断强势股是接力机会还是情绪过热。",
        ),
        _roadmap_item(
            "announcements",
            "公告/财报事件",
            _event_status(data_health, "strategy_announcements"),
            "medium",
            data_health.get("strategy_data_provider", provider),
            "交易所公告、巨潮资讯、公司财报结构化数据",
            "推荐上涨原因需要公告、业绩和订单验证。",
            "把消息转成可验证的财务传导和风险条件。",
        ),
        _roadmap_item(
            "financials",
            "财务因子",
            _event_status(data_health, "strategy_fundamentals"),
            "medium",
            data_health.get("strategy_data_provider", provider),
            "财报三表、估值、盈利预测、机构一致预期",
            "成长股和高估值股票需要验证利润兑现能力。",
            "避免只看走势，不知道估值有没有透支。",
        ),
    ]
    return rows


def _roadmap_item(
    area: str,
    title: str,
    status: str,
    default_priority: str,
    current_source: str,
    recommended_source: str,
    impact: str,
    user_value: str,
) -> DataSourceUpgradeItem:
    priority = default_priority
    if status == "ready":
        priority = "low"
    elif status in {"unknown", "missing"} and default_priority == "medium":
        priority = "medium"
    return DataSourceUpgradeItem(
        area=area,
        status=status,
        priority=priority,
        title=title,
        current_source=current_source,
        recommended_source=recommended_source,
        impact=impact,
        user_value=user_value,
    )


def _event_status(data_health: dict[str, str], key: str) -> str:
    try:
        return "partial" if int(data_health.get(key, "0")) > 0 else "missing"
    except ValueError:
        return "missing"


def _strategy_effectiveness(
    health: list[StrategyHealth],
    center: MarketIntelligenceCenter | None,
    cards: list[OpportunityCard],
) -> list[StrategyEffectivenessItem]:
    weight_by_strategy = {}
    if center:
        weight_by_strategy = {
            item.strategy_id: item.weight_pct
            for item in center.strategy_scheduler.weights
        }
    if not health:
        health = _fallback_strategy_health(cards)
    rows: list[StrategyEffectivenessItem] = []
    for item in health:
        rows.append(
            StrategyEffectivenessItem(
                strategy_id=item.strategy_id,
                name=item.name,
                family=item.family,
                readiness=item.readiness,
                verdict=_strategy_verdict(item),
                sample_count=item.sample_count,
                win_rate_10d=item.win_rate_10d,
                avg_return_10d=item.avg_return_10d,
                avg_return_20d=item.avg_return_20d,
                max_loss_10d=item.max_loss_10d,
                weight_pct=weight_by_strategy.get(item.strategy_id),
                action=_strategy_action(item),
            )
        )
    rows.sort(key=lambda item: (item.weight_pct or 0, item.sample_count), reverse=True)
    return rows[:8]


def _fallback_strategy_health(cards: list[OpportunityCard]) -> list[StrategyHealth]:
    grouped: dict[str, list[OpportunityCard]] = {}
    for card in cards:
        if card.primary_strategy_id:
            grouped.setdefault(card.primary_strategy_id, []).append(card)
    rows: list[StrategyHealth] = []
    for strategy_id, items in grouped.items():
        calibration = next((card.strategy_calibration for card in items if card.strategy_calibration), None)
        rows.append(
            StrategyHealth(
                strategy_id=strategy_id,
                name=strategy_id.replace("_", " ").title(),
                family=strategy_id.split("_")[0] if "_" in strategy_id else "unknown",
                readiness=calibration.readiness if calibration else "missing_data",
                sample_count=calibration.sample_count if calibration else 0,
                win_rate_10d=calibration.win_rate_10d if calibration else None,
                avg_return_10d=calibration.avg_return_10d if calibration else None,
                avg_return_20d=calibration.avg_return_20d if calibration else None,
                max_loss_10d=calibration.max_loss_10d if calibration else None,
            )
        )
    return rows


def _strategy_verdict(item: StrategyHealth) -> str:
    if item.sample_count < 10:
        return "样本不足，先小仓或只观察。"
    if (item.win_rate_10d or 0) >= 58 and (item.avg_return_10d or 0) > 0:
        return "历史样本支持，可以保留正常权重。"
    if (item.avg_return_10d or 0) < 0 or (item.max_loss_10d or 0) <= -10:
        return "近期表现偏弱，需要降权或等待再验证。"
    return "表现中性，保持观察权重。"


def _strategy_action(item: StrategyHealth) -> str:
    if item.sample_count < 10:
        return "collect_sample"
    if (item.win_rate_10d or 0) >= 60 and (item.avg_return_10d or 0) >= 1:
        return "raise_weight"
    if (item.avg_return_10d or 0) < 0 or (item.max_loss_10d or 0) <= -10:
        return "lower_weight"
    return "keep_weight"


def _headline(
    actions: list[TodayActionItem],
    alerts: list[AlertLoopItem],
    strategies: list[StrategyEffectivenessItem],
) -> str:
    high_actions = sum(1 for item in actions if item.priority == "high")
    weak_strategies = sum(1 for item in strategies if item.action == "lower_weight")
    return (
        f"今日有 {len(actions)} 条手动操作项，其中 {high_actions} 条高优先级；"
        f"{len(alerts)} 条提醒建议；{weak_strategies} 个策略需要降权观察。"
    )


def _score(card: OpportunityCard) -> float:
    return card.dynamic_score if card.dynamic_score is not None else card.rank_score


def _label(card: OpportunityCard) -> str:
    return card.instrument_label or format_instrument_label(card.instrument_id) or card.instrument_id


def _action_label(card: OpportunityCard) -> str:
    if card.decision and card.decision.action_label:
        return card.decision.action_label
    if card.recommendation_summary:
        return card.recommendation_summary.stance
    return "观察候选"


def _action_reason(card: OpportunityCard, regime: str) -> str:
    reasons = []
    if card.rank_reasons:
        reasons.append("；".join(card.rank_reasons[:2]))
    if card.market_context:
        reasons.append(card.market_context.summary)
    if card.strategy_calibration:
        win = "-" if card.strategy_calibration.win_rate_10d is None else f"{card.strategy_calibration.win_rate_10d:.1f}%"
        avg = "-" if card.strategy_calibration.avg_return_10d is None else f"{card.strategy_calibration.avg_return_10d:+.2f}%"
        reasons.append(f"相似样本 {card.strategy_calibration.sample_count} 个，10日胜率 {win}，均值 {avg}")
    reasons.append(f"当前市场环境：{regime}")
    return "。".join(reasons)


def _alert_title(kind: str, card: OpportunityCard) -> str:
    labels = {
        "entry_trigger": "买点触发",
        "stop_guard": "止损保护",
        "target_1_reached": "目标一",
        "signal_weakened": "信号转弱",
    }
    return f"{_label(card)}：{labels.get(kind, kind)}"


def _alert_action(suggestion: SignalAlertSuggestion) -> str:
    symbol = ">=" if suggestion.operator == ">=" else "<="
    return f"价格 {symbol} {suggestion.threshold} 时检查计划是否仍然成立。"
