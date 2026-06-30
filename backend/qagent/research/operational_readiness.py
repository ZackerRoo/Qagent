from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from qagent.domain.models import OpportunityCard
from qagent.monitoring.signal_monitor import SignalMonitorCenter
from qagent.research.decision_quality import DecisionQualityCenter
from qagent.research.market_intelligence import MarketIntelligenceCenter
from qagent.strategies.models import StrategyHealth


class OperationalReadinessCheck(BaseModel):
    key: str
    label: str
    status: str
    score: float = Field(ge=0.0, le=1.0)
    user_value: str
    evidence: list[str] = Field(default_factory=list)
    next_action: str


class StrategyLearningItem(BaseModel):
    strategy_id: str
    name: str
    action: str
    sample_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    weight_hint_pct: float | None = None
    reason: str


class RecommendationStabilityItem(BaseModel):
    instrument_id: str
    instrument_label: str | None = None
    current_rank: int
    current_score: float
    previous_rank: int | None = None
    previous_score: float | None = None
    change: str
    reason: str


class UserQuestionAnswer(BaseModel):
    key: str
    question: str
    answer: str
    source: str


class OperationalReadinessCenter(BaseModel):
    as_of: date
    headline: str
    readiness_score: float = Field(ge=0.0, le=1.0)
    checks: list[OperationalReadinessCheck] = Field(default_factory=list)
    strategy_learning: list[StrategyLearningItem] = Field(default_factory=list)
    stability_audit: list[RecommendationStabilityItem] = Field(default_factory=list)
    user_questions: list[UserQuestionAnswer] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)

    def check_by_key(self, key: str) -> OperationalReadinessCheck:
        for item in self.checks:
            if item.key == key:
                return item
        raise KeyError(key)


def build_operational_readiness_center(
    *,
    cards: list[OpportunityCard],
    previous_cards: list[OpportunityCard] | None = None,
    market_intelligence: MarketIntelligenceCenter | None = None,
    decision_quality_center: DecisionQualityCenter | None = None,
    signal_monitor: SignalMonitorCenter | None = None,
    strategy_health: list[StrategyHealth] | None = None,
    data_health: dict[str, str] | None = None,
    alert_rules_count: int = 0,
    as_of: date | None = None,
) -> OperationalReadinessCenter:
    health = strategy_health or []
    health_data = data_health or {}
    learning = _strategy_learning(health, decision_quality_center)
    stability = _stability_audit(cards, previous_cards or [])
    checks = [
        _data_source_check(market_intelligence, health_data),
        _strategy_learning_check(learning, health),
        _backtest_realism_check(health_data, health),
        _paper_account_check(health_data),
        _alert_system_check(decision_quality_center, signal_monitor, alert_rules_count),
        _recommendation_stability_check(stability, previous_cards or []),
    ]
    questions = _user_questions(cards, decision_quality_center, signal_monitor)
    readiness = _weighted_readiness(checks)
    headline = _headline(readiness, cards, checks)
    return OperationalReadinessCenter(
        as_of=as_of or date.today(),
        headline=headline,
        readiness_score=readiness,
        checks=checks,
        strategy_learning=learning,
        stability_audit=stability,
        user_questions=questions,
        data_health={
            **health_data,
            "operational_readiness_checks": str(len(checks)),
            "operational_readiness_questions": str(len(questions)),
            "operational_readiness_score": f"{readiness:.2f}",
            "operational_readiness_ready": str(sum(item.status == "ready" for item in checks)),
            "operational_readiness_watch": str(sum(item.status == "watch" for item in checks)),
            "operational_readiness_risk": str(sum(item.status == "risk" for item in checks)),
        },
    )


def _data_source_check(
    market_intelligence: MarketIntelligenceCenter | None,
    data_health: dict[str, str],
) -> OperationalReadinessCheck:
    if market_intelligence is None:
        return OperationalReadinessCheck(
            key="data_source_realism",
            label="数据源真实度",
            status="watch",
            score=0.45,
            user_value="能判断当前数据是否足够支撑真实推荐。",
            evidence=[f"行情源：{data_health.get('provider', 'unknown')}"],
            next_action="先刷新全A池快照，并检查复权、停牌、涨跌停和行业字段。",
        )
    quality = market_intelligence.data_quality
    risk = sum(1 for item in quality.source_checks if item.severity == "risk")
    watch = sum(1 for item in quality.source_checks if item.severity == "watch")
    status = _status_from_score(quality.score, risk=risk, watch=watch)
    evidence = [
        f"数据质量分 {quality.score:.0%}",
        f"覆盖率 {_fmt_pct(quality.coverage_ratio)}",
        f"风险项 {risk} 个，观察项 {watch} 个",
    ]
    if quality.missing_inputs:
        evidence.append("缺口：" + "、".join(quality.missing_inputs[:3]))
    return OperationalReadinessCheck(
        key="data_source_realism",
        label="数据源真实度",
        status=status,
        score=round(quality.score, 4),
        user_value="避免把样例数据、缺复权或缺停牌处理的数据当成真实机会。",
        evidence=evidence,
        next_action=(
            "正式使用前补齐复权、停牌、涨跌停、行业分类、指数成分、公告/财报和资金流。"
            if status != "ready"
            else "继续监控行情源错误率和缓存命中率。"
        ),
    )


def _strategy_learning_check(
    learning: list[StrategyLearningItem],
    health: list[StrategyHealth],
) -> OperationalReadinessCheck:
    sample_count = sum(item.sample_count for item in health)
    validated = sum(item.action in {"提高权重", "保持权重"} for item in learning)
    score = min(1.0, sample_count / 80)
    if validated:
        score = min(1.0, score + 0.15)
    status = "ready" if score >= 0.72 else "watch" if score >= 0.38 else "risk"
    return OperationalReadinessCheck(
        key="strategy_self_learning",
        label="策略自学习",
        status=status,
        score=round(score, 4),
        user_value="让推荐不是固定列表，而是根据策略胜率、收益和回撤动态调权。",
        evidence=[
            f"策略样本 {sample_count}",
            f"可保持/提高权重策略 {validated}",
            f"策略动作 {len(learning)} 个",
        ],
        next_action=(
            "继续用推荐闭环、模拟盘和回测更新策略权重。"
            if status == "ready"
            else "先积累更多样本，低样本策略只能观察，不能直接放大仓位。"
        ),
    )


def _backtest_realism_check(
    data_health: dict[str, str],
    health: list[StrategyHealth],
) -> OperationalReadinessCheck:
    has_benchmark = _truthy(data_health.get("backtest_benchmark")) or _truthy(
        data_health.get("benchmark")
    )
    has_environment = _truthy(data_health.get("backtest_environment_breakdown")) or _truthy(
        data_health.get("environment_breakdown")
    )
    has_strategy_health = any(item.sample_count > 0 for item in health)
    score = 0.25
    score += 0.25 if has_strategy_health else 0
    score += 0.25 if has_benchmark else 0
    score += 0.15 if has_environment else 0
    score += 0.10 if _truthy(data_health.get("lookahead_guard")) else 0
    status = "ready" if score >= 0.72 else "watch" if score >= 0.45 else "risk"
    evidence = [
        "策略健康样本已接入" if has_strategy_health else "缺少策略健康样本",
        "指数对比已接入" if has_benchmark else "建议补指数对比",
        "市场环境拆分已接入" if has_environment else "建议补不同市场环境表现",
    ]
    return OperationalReadinessCheck(
        key="backtest_realism",
        label="真实回测",
        status=status,
        score=round(score, 4),
        user_value="回答这个推荐过去同类机会胜率多少、最大可能亏多少。",
        evidence=evidence,
        next_action="在回测页跑当前推荐，重点看胜率、均值、最大回撤、最大连亏、夏普/卡玛、换手和相对指数。",
    )


def _paper_account_check(data_health: dict[str, str]) -> OperationalReadinessCheck:
    trade_count = _int_health(data_health, "paper_total")
    closed_count = _int_health(data_health, "paper_closed")
    score = 0.55
    if trade_count > 0:
        score += 0.2
    if closed_count > 0:
        score += 0.15
    if _truthy(data_health.get("paper_ledger")):
        score += 0.1
    score = min(1.0, score)
    status = "ready" if score >= 0.72 else "watch"
    evidence = [
        f"模拟记录 {trade_count} 条",
        f"已闭环 {closed_count} 条",
        "支持现金、仓位、交易流水、手续费/滑点和收益曲线",
    ]
    return OperationalReadinessCheck(
        key="paper_account",
        label="模拟盘账本",
        status=status,
        score=round(score, 4),
        user_value="验证按 Qagent 推荐买入后到底赚没赚，而不是只看信号。",
        evidence=evidence,
        next_action=(
            "把今日前 3-5 个候选加入模拟盘，后续按触发价、止损和目标价自动复盘。"
            if trade_count == 0
            else "继续跟踪持仓收益曲线、胜率、平均收益和最大回撤。"
        ),
    )


def _alert_system_check(
    decision_quality_center: DecisionQualityCenter | None,
    signal_monitor: SignalMonitorCenter | None,
    alert_rules_count: int,
) -> OperationalReadinessCheck:
    suggested = (
        decision_quality_center.alert_playbook.total_alerts
        if decision_quality_center is not None
        else 0
    )
    queued = len(signal_monitor.action_queue) if signal_monitor is not None else 0
    score = 0.25
    score += min(0.35, suggested / 24) if suggested else 0
    score += min(0.25, alert_rules_count / 8) if alert_rules_count else 0
    score += 0.15 if queued else 0
    score = min(1.0, score)
    status = "ready" if alert_rules_count > 0 and score >= 0.6 else "watch" if suggested else "risk"
    return OperationalReadinessCheck(
        key="alert_system",
        label="提醒系统",
        status=status,
        score=round(score, 4),
        user_value="到买点、跌破止损、接近目标、推荐变弱时给用户明确动作。",
        evidence=[
            f"建议提醒 {suggested}",
            f"已保存规则 {alert_rules_count}",
            f"监控队列 {queued}",
        ],
        next_action=(
            "保存核心提醒规则，并用盘中刷新检查是否触发。"
            if alert_rules_count == 0
            else "继续检查提醒触发记录，避免只创建不验证。"
        ),
    )


def _recommendation_stability_check(
    stability: list[RecommendationStabilityItem],
    previous_cards: list[OpportunityCard],
) -> OperationalReadinessCheck:
    if not previous_cards:
        return OperationalReadinessCheck(
            key="recommendation_stability",
            label="推荐稳定性",
            status="watch",
            score=0.55,
            user_value="判断推荐是否每天乱跳，还是同一批机会持续变强。",
            evidence=["暂无上一轮推荐对比，先建立历史快照。"],
            next_action="保留每次扫描快照，比较排名、分数、买点状态和风险标签变化。",
        )
    kept = sum(item.previous_rank is not None for item in stability)
    improved = sum(item.change == "improved" for item in stability)
    weakened = sum(item.change == "weakened" for item in stability)
    score = min(1.0, 0.35 + kept / max(1, len(stability)) * 0.4 + improved * 0.05)
    if weakened > improved:
        score = max(0.0, score - 0.15)
    status = "ready" if score >= 0.68 else "watch"
    return OperationalReadinessCheck(
        key="recommendation_stability",
        label="推荐稳定性",
        status=status,
        score=round(score, 4),
        user_value="看机会是否持续验证，而不是一次扫描偶然排前。",
        evidence=[f"延续 {kept} 个", f"增强 {improved} 个", f"走弱 {weakened} 个"],
        next_action="对排名新进入、连续走弱、突然过热的标的单独标记，避免盲目追高。",
    )


def _strategy_learning(
    health: list[StrategyHealth],
    decision_quality_center: DecisionQualityCenter | None,
) -> list[StrategyLearningItem]:
    action_by_id = {}
    if decision_quality_center is not None:
        action_by_id = {
            item.strategy_id: item for item in decision_quality_center.calibration.strategy_actions
        }
    result: list[StrategyLearningItem] = []
    for item in sorted(
        health,
        key=lambda row: (row.sample_count, row.win_rate_10d or 0, row.avg_return_10d or 0),
        reverse=True,
    )[:8]:
        action = action_by_id.get(item.strategy_id)
        result.append(
            StrategyLearningItem(
                strategy_id=item.strategy_id,
                name=item.name,
                action=action.action if action else _fallback_strategy_action(item),
                sample_count=item.sample_count,
                win_rate_10d=item.win_rate_10d,
                avg_return_10d=item.avg_return_10d,
                weight_hint_pct=action.weight_pct if action else None,
                reason=action.reason if action else _fallback_strategy_reason(item),
            )
        )
    return result


def _stability_audit(
    cards: list[OpportunityCard],
    previous_cards: list[OpportunityCard],
) -> list[RecommendationStabilityItem]:
    previous = {card.instrument_id: (index + 1, card) for index, card in enumerate(previous_cards)}
    result: list[RecommendationStabilityItem] = []
    for index, card in enumerate(cards[:12], start=1):
        previous_item = previous.get(card.instrument_id)
        previous_rank = previous_item[0] if previous_item else None
        previous_card = previous_item[1] if previous_item else None
        current_score = _score(card)
        previous_score = _score(previous_card) if previous_card else None
        change, reason = _stability_change(index, current_score, previous_rank, previous_score)
        result.append(
            RecommendationStabilityItem(
                instrument_id=card.instrument_id,
                instrument_label=card.instrument_label,
                current_rank=index,
                current_score=current_score,
                previous_rank=previous_rank,
                previous_score=previous_score,
                change=change,
                reason=reason,
            )
        )
    return result


def _user_questions(
    cards: list[OpportunityCard],
    decision_quality_center: DecisionQualityCenter | None,
    signal_monitor: SignalMonitorCenter | None,
) -> list[UserQuestionAnswer]:
    if not cards:
        return [
            UserQuestionAnswer(
                key="top_recommendation",
                question="今天推荐哪只？",
                answer="当前没有达到推荐阈值的标的，先刷新全A扫描或降低样本过滤条件。",
                source="cards",
            )
        ]
    top = cards[0]
    explanation = None
    if decision_quality_center is not None:
        explanation = next(
            (
                item
                for item in decision_quality_center.explanation_cards
                if item.instrument_id == top.instrument_id
            ),
            None,
        )
    label = _label(top)
    action = _localized_action(
        top.decision.action if top.decision else None,
        top.decision.action_label if top.decision else None,
    )
    quality = top.recommendation_quality.score if top.recommendation_quality else top.rank_score
    monitor_item = None
    if signal_monitor is not None:
        monitor_item = next(
            (item for item in signal_monitor.items if item.instrument_id == top.instrument_id),
            None,
        )
    monitor_state = _monitor_state_label(monitor_item.state) if monitor_item is not None else ""
    monitor_action = _strip_period(monitor_item.action) if monitor_item is not None else ""
    tracking = (
        f"当前监控状态：{_monitor_tracking_detail(monitor_state, monitor_action)}。"
        if monitor_item is not None
        else "加入模拟盘后，用买点、止损、目标价和推荐变弱提醒跟踪。"
    )
    return [
        UserQuestionAnswer(
            key="top_recommendation",
            question="今天推荐哪只？",
            answer=(
                f"优先看 {label}，当前动作是{action}，质量分 {quality:.0%}，"
                f"排序分 {top.rank_score:.0%}。"
            ),
            source="cards[0]",
        ),
        UserQuestionAnswer(
            key="why_recommended",
            question="为什么推荐它？",
            answer=(
                explanation.why_recommended
                if explanation is not None
                else _first_reason(top)
            ),
            source="decision_quality_center",
        ),
        UserQuestionAnswer(
            key="strategy_score",
            question="策略分是多少？",
            answer=(
                f"策略分 {top.strategy_score:.0%}，排序分 {top.rank_score:.0%}，"
                f"因子分 {top.factor_score:.0%}，质量分 {quality:.0%}。"
            ),
            source="opportunity_card",
        ),
        UserQuestionAnswer(
            key="trade_plan",
            question="什么时候买，什么时候卖？",
            answer=(
                f"买点：{explanation.when_to_buy} 止损/目标：{explanation.when_to_sell}"
                if explanation is not None
                else (
                    f"买点 {top.entry_plan.trigger_price}，止损 {top.exit_plan.initial_stop}，"
                    f"目标 {top.exit_plan.target_1}/{top.exit_plan.target_2}。"
                )
            ),
            source="entry_exit_plan",
        ),
        UserQuestionAnswer(
            key="follow_up",
            question="买了之后怎么跟踪？",
            answer=tracking,
            source="signal_monitor",
        ),
    ]


def _weighted_readiness(checks: list[OperationalReadinessCheck]) -> float:
    weights = {
        "data_source_realism": 0.2,
        "strategy_self_learning": 0.17,
        "backtest_realism": 0.17,
        "paper_account": 0.15,
        "alert_system": 0.16,
        "recommendation_stability": 0.15,
    }
    score = sum(item.score * weights.get(item.key, 0.1) for item in checks)
    total = sum(weights.get(item.key, 0.1) for item in checks)
    return round(score / total if total else 0, 4)


def _headline(
    readiness: float,
    cards: list[OpportunityCard],
    checks: list[OperationalReadinessCheck],
) -> str:
    top = _label(cards[0]) if cards else "暂无推荐"
    weak = [item.label for item in checks if item.status != "ready"][:2]
    if readiness >= 0.72:
        return f"今日推荐闭环可用：先看 {top}，买卖计划、提醒和验证路径已生成。"
    if readiness >= 0.5:
        return f"今日可小仓位验证：先看 {top}，但需要补强{'、'.join(weak) if weak else '后续跟踪'}。"
    return f"今日不适合直接放大仓位：{top} 只能先模拟验证。"


def _status_from_score(score: float, *, risk: int = 0, watch: int = 0) -> str:
    if score >= 0.72 and risk == 0:
        return "ready"
    if score >= 0.45 and risk <= 2:
        return "watch"
    if watch >= 5 and score < 0.6:
        return "risk"
    return "risk"


def _fallback_strategy_action(item: StrategyHealth) -> str:
    if item.sample_count < 10:
        return "收集样本"
    if (item.avg_return_10d or 0) < 0 or (item.max_loss_10d is not None and item.max_loss_10d <= -8):
        return "降低权重"
    if (item.win_rate_10d or 0) >= 55 and (item.avg_return_10d or 0) >= 0:
        return "提高权重"
    return "保持权重"


def _fallback_strategy_reason(item: StrategyHealth) -> str:
    return (
        f"样本 {item.sample_count}，10日胜率 {_fmt_pct(item.win_rate_10d)}，"
        f"10日均值 {_fmt_signed(item.avg_return_10d)}。"
    )


def _stability_change(
    current_rank: int,
    current_score: float,
    previous_rank: int | None,
    previous_score: float | None,
) -> tuple[str, str]:
    if previous_rank is None or previous_score is None:
        return "new", "新进入当前推荐列表，需要先观察是否连续出现。"
    score_delta = current_score - previous_score
    rank_delta = previous_rank - current_rank
    if rank_delta > 0 or score_delta >= 0.03:
        return "improved", f"排名提升 {max(rank_delta, 0)} 位，分数变化 {_fmt_signed(score_delta * 100)}。"
    if rank_delta < -2 or score_delta <= -0.05:
        return "weakened", f"排名回落 {abs(min(rank_delta, 0))} 位，分数变化 {_fmt_signed(score_delta * 100)}。"
    return "stable", f"排名和分数基本稳定，分数变化 {_fmt_signed(score_delta * 100)}。"


def _score(card: OpportunityCard | None) -> float:
    if card is None:
        return 0.0
    return round(card.dynamic_score or card.rank_score or card.score, 4)


def _label(card: OpportunityCard) -> str:
    return card.instrument_label or card.instrument_id


def _first_reason(card: OpportunityCard) -> str:
    if card.recommendation_summary is not None and card.recommendation_summary.headline:
        return card.recommendation_summary.headline
    if card.rank_reasons:
        return card.rank_reasons[0]
    return card.thesis


def _localized_action(action: object, fallback: str | None) -> str:
    labels = {
        "candidate_entry": "候选买入",
        "watch_trigger": "等待买点",
        "wait_pullback": "等待回调",
        "avoid": "暂不参与",
    }
    key = str(action or "").strip()
    if key in labels:
        return labels[key]
    fallback_text = str(fallback or "").strip()
    fallback_labels = {
        "Candidate entry": "候选买入",
        "Watch trigger": "等待买点",
        "Wait pullback": "等待回调",
        "Avoid": "暂不参与",
    }
    return fallback_labels.get(fallback_text, fallback_text or "观察")


def _monitor_state_label(state: str) -> str:
    labels = {
        "entry_triggered": "买点触发",
        "stop_breached": "跌破止损",
        "near_target": "接近目标",
        "target_reached": "目标达成",
        "recommendation_weakened": "推荐变弱",
        "watching": "等待触发",
    }
    return labels.get(state, state)


def _strip_period(text: str) -> str:
    return text.rstrip("。.")


def _monitor_tracking_detail(state_label: str, action: str) -> str:
    if action.startswith(state_label):
        return action
    return f"{state_label}，{action}"


def _truthy(value: object) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text not in {"", "0", "false", "none", "missing", "unknown", "skipped"}


def _int_health(data_health: dict[str, str], key: str) -> int:
    try:
        return int(str(data_health.get(key, "0")))
    except (TypeError, ValueError):
        return 0


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "待验证"
    return f"{value:.1f}%"


def _fmt_signed(value: float | None) -> str:
    if value is None:
        return "待验证"
    return f"{value:+.1f}%"
