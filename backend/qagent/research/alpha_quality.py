from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from qagent.domain.models import OpportunityCard
from qagent.market.rotation_radar import MarketRotationRadar, RotationTheme
from qagent.strategies.models import StrategyHealth


class BuyabilityGate(BaseModel):
    verdict: str
    should_buy_today: bool
    min_rank_score: float = Field(ge=0.0, le=1.0)
    min_quality_score: float = Field(ge=0.0, le=1.0)
    allowed_actions: list[str] = Field(default_factory=list)
    reason: str
    checks: list[str] = Field(default_factory=list)


class CurrentLeaderReview(BaseModel):
    instrument_id: str
    instrument_label: str
    verdict: str
    score_summary: str
    strategy_score_text: str
    why_it_is_top: list[str] = Field(default_factory=list)
    buy_discipline: str
    invalidation_rules: list[str] = Field(default_factory=list)
    next_observation: str


class StrategyTuningRule(BaseModel):
    strategy_id: str
    name: str
    action: str
    weight_multiplier: float = Field(ge=0.0, le=2.0)
    current_candidates: int
    sample_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    max_loss_10d: float | None = None
    evidence: str


class ThemeConfirmation(BaseModel):
    name: str
    category: str
    action: str
    score: float = Field(ge=0.0, le=1.0)
    opportunity_count: int
    actionable_count: int
    leader_labels: list[str] = Field(default_factory=list)
    evidence: str


class AlphaQualityCenter(BaseModel):
    as_of: date
    headline: str
    alpha_score: float = Field(ge=0.0, le=1.0)
    confidence_level: str
    buyability_gate: BuyabilityGate
    current_leader: CurrentLeaderReview
    strategy_tuning: list[StrategyTuningRule] = Field(default_factory=list)
    theme_confirmation: list[ThemeConfirmation] = Field(default_factory=list)
    data_health: dict[str, str] = Field(default_factory=dict)


def build_alpha_quality_center(
    *,
    cards: list[OpportunityCard],
    rotation_radar: MarketRotationRadar | None = None,
    strategy_health: list[StrategyHealth] | None = None,
    data_health: dict[str, str] | None = None,
    as_of: date | None = None,
) -> AlphaQualityCenter:
    health = strategy_health or []
    health_data = data_health or {}
    leader = cards[0] if cards else None
    gate = _buyability_gate(leader, health_data)
    tuning = _strategy_tuning(cards, health)
    themes = _theme_confirmation(rotation_radar)
    review = _leader_review(leader, gate, themes)
    alpha_score = _alpha_score(gate, review, tuning, themes)
    return AlphaQualityCenter(
        as_of=as_of or date.today(),
        headline=_headline(alpha_score, review, gate, themes),
        alpha_score=alpha_score,
        confidence_level=_confidence_level(alpha_score),
        buyability_gate=gate,
        current_leader=review,
        strategy_tuning=tuning,
        theme_confirmation=themes,
        data_health={
            **health_data,
            "alpha_quality_cards": str(len(cards)),
            "alpha_quality_strategies": str(len(tuning)),
            "alpha_quality_themes": str(len(themes)),
            "alpha_quality_score": f"{alpha_score:.2f}",
        },
    )


def _buyability_gate(
    leader: OpportunityCard | None,
    data_health: dict[str, str],
) -> BuyabilityGate:
    min_rank = 0.68
    min_quality = 0.7
    readiness = _float_health(data_health, "operational_readiness_score")
    if readiness is not None and readiness < 0.55:
        min_rank += 0.04
        min_quality += 0.04
    allowed = ["candidate_entry", "watch_trigger"]
    if leader is None:
        return BuyabilityGate(
            verdict="暂不参与",
            should_buy_today=False,
            min_rank_score=min_rank,
            min_quality_score=min_quality,
            allowed_actions=allowed,
            reason="当前没有候选推荐，先刷新全市场扫描。",
            checks=["没有可评估的首选标的。"],
        )

    action = leader.decision.action if leader.decision else "watch"
    risk_status = leader.decision.risk_status if leader.decision else "warning"
    rank_ok = leader.rank_score >= min_rank
    quality_score = _quality_score(leader)
    quality_ok = quality_score >= min_quality
    risk_ok = risk_status != "blocked" and action != "avoid"
    should_buy = bool(rank_ok and quality_ok and risk_ok and action == "candidate_entry")
    if should_buy:
        verdict = "可小仓位验证"
    elif rank_ok and quality_ok and risk_ok and action == "watch_trigger":
        verdict = "等待触发"
    else:
        verdict = "暂不参与"
    checks = [
        f"排序分 {_fmt_score(leader.rank_score)} / 门槛 {_fmt_score(min_rank)}",
        f"质量分 {_fmt_score(quality_score)} / 门槛 {_fmt_score(min_quality)}",
        f"动作 {_action_label(action)}，风险 {_risk_label(risk_status)}",
    ]
    reason = (
        "只有触发价、质量分、排序分和风险状态同时达标，才允许从观察进入模拟/小仓位。"
        if verdict != "可小仓位验证"
        else "首选标的通过排序、质量、动作和风险门槛，可以先按计划小仓位验证。"
    )
    return BuyabilityGate(
        verdict=verdict,
        should_buy_today=should_buy,
        min_rank_score=round(min_rank, 4),
        min_quality_score=round(min_quality, 4),
        allowed_actions=allowed,
        reason=reason,
        checks=checks,
    )


def _leader_review(
    leader: OpportunityCard | None,
    gate: BuyabilityGate,
    themes: list[ThemeConfirmation],
) -> CurrentLeaderReview:
    if leader is None:
        return CurrentLeaderReview(
            instrument_id="-",
            instrument_label="暂无首选",
            verdict="暂不参与",
            score_summary="没有候选标的。",
            strategy_score_text="策略分待验证。",
            why_it_is_top=["当前扫描没有生成可排序机会。"],
            buy_discipline="先刷新全 A 扫描，再看是否出现可验证机会。",
            invalidation_rules=["没有买点就不执行。"],
            next_observation="等待下一次扫描。",
        )
    summary = leader.recommendation_summary.headline if leader.recommendation_summary else leader.thesis
    theme_names = [theme.name for theme in themes[:3]]
    why = [summary]
    if leader.rank_reasons:
        why.extend(leader.rank_reasons[:2])
    if theme_names:
        why.append("当前较强主题：" + "、".join(theme_names))
    action = leader.decision.action if leader.decision else "watch"
    return CurrentLeaderReview(
        instrument_id=leader.instrument_id,
        instrument_label=leader.instrument_label or leader.instrument_id,
        verdict=gate.verdict,
        score_summary=(
            f"排序分 {_fmt_score(leader.rank_score)}，质量分 {_fmt_score(_quality_score(leader))}，"
            f"因子分 {_fmt_score(leader.factor_score)}。"
        ),
        strategy_score_text=(
            f"策略分 {_fmt_score(leader.strategy_score)}，主策略 {_strategy_label(leader.primary_strategy_id)}。"
        ),
        why_it_is_top=_dedupe(why)[:5],
        buy_discipline=_buy_discipline(leader, action),
        invalidation_rules=_invalidation_rules(leader),
        next_observation=_next_observation(leader, action),
    )


def _strategy_tuning(
    cards: list[OpportunityCard],
    health: list[StrategyHealth],
) -> list[StrategyTuningRule]:
    current_counts: dict[str, int] = {}
    for card in cards:
        strategy_id = card.primary_strategy_id or "unclassified"
        current_counts[strategy_id] = current_counts.get(strategy_id, 0) + 1
    if not health:
        health = [_fallback_health(strategy_id, count) for strategy_id, count in current_counts.items()]
    rows = []
    for item in sorted(health, key=lambda row: (current_counts.get(row.strategy_id, 0), row.sample_count), reverse=True)[:8]:
        action, multiplier, evidence = _strategy_action(item)
        rows.append(
            StrategyTuningRule(
                strategy_id=item.strategy_id,
                name=item.name,
                action=action,
                weight_multiplier=multiplier,
                current_candidates=current_counts.get(item.strategy_id, 0),
                sample_count=item.sample_count,
                win_rate_10d=item.win_rate_10d,
                avg_return_10d=item.avg_return_10d,
                max_loss_10d=item.max_loss_10d,
                evidence=evidence,
            )
        )
    return rows


def _theme_confirmation(
    rotation_radar: MarketRotationRadar | None,
) -> list[ThemeConfirmation]:
    if rotation_radar is None:
        return []
    result: list[ThemeConfirmation] = []
    for theme in rotation_radar.themes[:8]:
        result.append(_theme_row(theme))
    return result


def _theme_row(theme: RotationTheme) -> ThemeConfirmation:
    if theme.score >= 0.72 and theme.actionable_count > 0:
        action = "主线确认"
    elif theme.score >= 0.56:
        action = "观察轮动"
    else:
        action = "只做备选"
    leaders = [leader.instrument_label or leader.instrument_id for leader in theme.leaders[:3]]
    return ThemeConfirmation(
        name=theme.name,
        category=theme.category,
        action=action,
        score=theme.score,
        opportunity_count=theme.opportunity_count,
        actionable_count=theme.actionable_count,
        leader_labels=leaders,
        evidence=(
            f"{theme.summary} 动量 {_fmt_score(theme.momentum_score)}，"
            f"广度 {_fmt_score(theme.breadth_score)}。"
        ),
    )


def _alpha_score(
    gate: BuyabilityGate,
    review: CurrentLeaderReview,
    tuning: list[StrategyTuningRule],
    themes: list[ThemeConfirmation],
) -> float:
    gate_component = 0.72 if gate.verdict == "可小仓位验证" else 0.58 if gate.verdict == "等待触发" else 0.32
    strategy_component = _average([
        min(1.0, item.weight_multiplier / 1.25) for item in tuning[:5]
    ])
    theme_component = _average([item.score for item in themes[:5]])
    review_component = 0.65 if review.invalidation_rules else 0.45
    return round(
        _clamp(
            gate_component * 0.35
            + strategy_component * 0.22
            + theme_component * 0.23
            + review_component * 0.2
        ),
        4,
    )


def _headline(
    alpha_score: float,
    review: CurrentLeaderReview,
    gate: BuyabilityGate,
    themes: list[ThemeConfirmation],
) -> str:
    theme = themes[0].name if themes else "暂无主线"
    if alpha_score >= 0.72:
        prefix = "推荐质量较高"
    elif alpha_score >= 0.56:
        prefix = "推荐质量可验证"
    else:
        prefix = "推荐质量需要继续观察"
    return f"{prefix}：首选 {review.instrument_label}，结论为{gate.verdict}，当前主线参考 {theme}。"


def _confidence_level(score: float) -> str:
    if score >= 0.72:
        return "高"
    if score >= 0.56:
        return "中"
    return "低"


def _strategy_action(item: StrategyHealth) -> tuple[str, float, str]:
    if item.sample_count < 10:
        return (
            "收集样本",
            0.85,
            f"样本 {item.sample_count} 不足，先作为观察策略，不放大权重。",
        )
    if (item.avg_return_10d is not None and item.avg_return_10d < 0) or (
        item.max_loss_10d is not None and item.max_loss_10d <= -8
    ):
        return (
            "降权",
            0.72,
            f"10日均值 {_fmt_pct(item.avg_return_10d)}，最大亏损 {_fmt_pct(item.max_loss_10d)}，需要收紧。",
        )
    if (item.win_rate_10d or 0) >= 56 and (item.avg_return_10d or 0) >= 0:
        return (
            "加权",
            1.18,
            f"样本 {item.sample_count}，10日胜率 {_fmt_pct(item.win_rate_10d)}，均值 {_fmt_pct(item.avg_return_10d)}。",
        )
    return (
        "保持",
        1.0,
        f"样本 {item.sample_count}，表现没有明显失效，维持当前权重。",
    )


def _fallback_health(strategy_id: str, count: int) -> StrategyHealth:
    return StrategyHealth(
        strategy_id=strategy_id,
        name=strategy_id,
        family="current_candidates",
        readiness="limited_sample",
        sample_count=count,
        win_rate_10d=None,
        avg_return_10d=None,
        avg_return_20d=None,
        max_loss_10d=None,
        curve=[],
    )


def _buy_discipline(card: OpportunityCard, action: str) -> str:
    if action == "candidate_entry":
        return (
            f"只按触发价 {card.entry_plan.trigger_price} 附近执行，"
            f"跌破 {card.exit_plan.initial_stop} 退出，接近 {card.exit_plan.target_1} 分批止盈。"
        )
    if action == "watch_trigger":
        return (
            f"当前等待买点，只有价格触发 {card.entry_plan.trigger_price} 且未追高时才进入模拟/小仓位。"
        )
    return "当前不是可买动作，先观察，不把它当作买入建议。"


def _invalidation_rules(card: OpportunityCard) -> list[str]:
    rules = [
        f"跌破初始止损 {card.exit_plan.initial_stop}，推荐假设失效。",
        f"高于禁追位 {card.entry_plan.no_chase_above}，不追高买入。",
    ]
    if card.decision and card.decision.failure_conditions:
        rules.extend(card.decision.failure_conditions[:3])
    return _dedupe(rules)[:5]


def _next_observation(card: OpportunityCard, action: str) -> str:
    if action == "candidate_entry":
        return "下一步看买点触发后 1/3/5 日是否继续强于同主题和指数。"
    if action == "watch_trigger":
        return "下一步看是否放量站上触发价；不触发则不执行。"
    return "下一步先看风险状态是否解除。"


def _quality_score(card: OpportunityCard) -> float:
    if card.recommendation_quality is not None:
        return card.recommendation_quality.score
    if card.quality_score is not None:
        return card.quality_score
    return card.rank_score


def _float_health(data_health: dict[str, str], key: str) -> float | None:
    try:
        return float(str(data_health.get(key, "")).strip())
    except (TypeError, ValueError):
        return None


def _fmt_score(value: float | None) -> str:
    if value is None:
        return "待验证"
    return f"{value:.0%}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "待验证"
    return f"{value:.1f}%"


def _action_label(action: str) -> str:
    labels = {
        "candidate_entry": "候选买入",
        "watch_trigger": "等待买点",
        "wait_pullback": "等待回调",
        "avoid": "暂不参与",
    }
    return labels.get(action, action)


def _risk_label(status: str) -> str:
    labels = {"clear": "清晰", "warning": "预警", "blocked": "阻断"}
    return labels.get(status, status)


def _strategy_label(strategy_id: str | None) -> str:
    labels = {
        "trend_momentum_stage2": "二阶段趋势动量",
        "breakout_volume_confirmation": "放量突破确认",
        "healthy_trend_pullback": "健康回调",
        "gf_dma_health_index": "GF-DMA 趋势健康",
        "short_squeeze_risk_monitor": "逼空风险监控",
        "catalyst_financial_transmission": "催化到财务传导",
        "pead_earnings_drift": "财报后漂移",
        "tam_adj_peg": "TAM 调整 PEG",
        "intrinsic_growth_mispricing": "内生成长错配",
        "factor_rotation_watch": "因子轮动观察",
    }
    return labels.get(strategy_id or "", strategy_id or "未分类")


def _average(values: list[float]) -> float:
    if not values:
        return 0.45
    return sum(values) / len(values)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result
