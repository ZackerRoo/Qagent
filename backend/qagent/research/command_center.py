from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from statistics import mean

from pydantic import BaseModel, Field

from qagent.domain.models import OpportunityCard, PortfolioAllocation, PortfolioPlan
from qagent.market.rotation_radar import MarketRotationRadar
from qagent.recommendations.signal_hub import build_signal_hub
from qagent.strategies.models import StrategyHealth, StrategyHealthPoint


class PortfolioAdvisorPosition(BaseModel):
    instrument_id: str
    instrument_label: str | None = None
    action: str
    weight_pct: float
    risk_budget_pct: float
    reason: str


class PortfolioAdvisor(BaseModel):
    summary: str
    target_positions: int
    suggested_positions: int
    allocated_weight_pct: float
    cash_reserve_pct: float
    max_single_position_pct: float
    blocked_count: int
    concentration_warnings: list[str] = Field(default_factory=list)
    positions: list[PortfolioAdvisorPosition] = Field(default_factory=list)


class ValidationWindow(BaseModel):
    key: str
    label: str
    sample_count: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    max_loss_10d: float | None = None
    verdict: str


class WalkForwardValidation(BaseModel):
    summary: str
    windows: list[ValidationWindow] = Field(default_factory=list)
    out_of_sample: ValidationWindow | None = None
    caveats: list[str] = Field(default_factory=list)


class StrategyAttributionItem(BaseModel):
    strategy_id: str
    name: str
    family: str
    card_count: int
    contribution_pct: float
    avg_rank_score: float
    avg_trust_score: float | None = None
    validated_samples: int
    win_rate_10d: float | None = None
    avg_return_10d: float | None = None
    top_instruments: list[str] = Field(default_factory=list)


class StrategyAttribution(BaseModel):
    summary: str
    strategies: list[StrategyAttributionItem] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class RecommendationPoolQuality(BaseModel):
    summary: str
    total_cards: int
    actionable_count: int
    blocked_count: int
    risk_filtered_count: int
    data_caveats_count: int
    asset_mix: dict[str, int] = Field(default_factory=dict)
    top_theme: str | None = None
    top_theme_share_pct: float | None = None
    warnings: list[str] = Field(default_factory=list)


class AlertDigest(BaseModel):
    summary: str
    total_suggestions: int
    by_kind: dict[str, int] = Field(default_factory=dict)
    top_instruments: list[str] = Field(default_factory=list)


class DailyResearchSummary(BaseModel):
    headline: str
    watch_themes: list[str] = Field(default_factory=list)
    top_opportunities: list[str] = Field(default_factory=list)
    avoid_list: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class ResearchCommandCenter(BaseModel):
    as_of: str
    portfolio_advisor: PortfolioAdvisor
    walk_forward_validation: WalkForwardValidation
    strategy_attribution: StrategyAttribution
    recommendation_pool_quality: RecommendationPoolQuality
    alert_digest: AlertDigest
    daily_research_summary: DailyResearchSummary
    data_health: dict[str, str] = Field(default_factory=dict)


def build_research_command_center(
    cards: list[OpportunityCard],
    portfolio_plan: PortfolioPlan | dict[str, object] | None = None,
    rotation_radar: MarketRotationRadar | dict[str, object] | None = None,
    strategy_health: list[StrategyHealth | dict[str, object]] | None = None,
    data_health: dict[str, str] | None = None,
) -> ResearchCommandCenter:
    plan = _coerce_portfolio_plan(portfolio_plan)
    health = _coerce_strategy_health(strategy_health or [])
    rotation = _coerce_rotation_radar(rotation_radar)
    portfolio = _build_portfolio_advisor(cards, plan)
    validation = _build_walk_forward_validation(health)
    attribution = _build_strategy_attribution(cards, health)
    pool = _build_pool_quality(cards, rotation)
    alerts = _build_alert_digest(cards)
    summary = _build_daily_summary(cards, rotation, validation, alerts)
    merged_health = {
        **(data_health or {}),
        "research_center_cards": str(len(cards)),
        "research_center_strategies": str(len(attribution.strategies)),
        "research_center_alert_suggestions": str(alerts.total_suggestions),
    }
    return ResearchCommandCenter(
        as_of=date.today().isoformat(),
        portfolio_advisor=portfolio,
        walk_forward_validation=validation,
        strategy_attribution=attribution,
        recommendation_pool_quality=pool,
        alert_digest=alerts,
        daily_research_summary=summary,
        data_health=merged_health,
    )


def _coerce_portfolio_plan(value: PortfolioPlan | dict[str, object] | None) -> PortfolioPlan | None:
    if value is None:
        return None
    if isinstance(value, PortfolioPlan):
        return value
    if isinstance(value, dict):
        try:
            return PortfolioPlan.model_validate(value)
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


def _coerce_rotation_radar(
    value: MarketRotationRadar | dict[str, object] | None,
) -> MarketRotationRadar | None:
    if value is None:
        return None
    if isinstance(value, MarketRotationRadar):
        return value
    if isinstance(value, dict):
        try:
            return MarketRotationRadar.model_validate(value)
        except Exception:
            return None
    return None


def _build_portfolio_advisor(
    cards: list[OpportunityCard],
    plan: PortfolioPlan | None,
) -> PortfolioAdvisor:
    if plan is None:
        plan = _fallback_portfolio_plan(cards)
    positions = [
        PortfolioAdvisorPosition(
            instrument_id=item.instrument_id,
            instrument_label=item.instrument_label,
            action=item.action,
            weight_pct=round(item.weight_pct, 2),
            risk_budget_pct=round(item.risk_budget_pct, 2),
            reason=item.rationale,
        )
        for item in plan.allocations
        if item.weight_pct > 0
    ]
    max_single = max((item.weight_pct for item in positions), default=0.0)
    warnings = _portfolio_concentration_warnings(plan.allocations)
    if plan.eligible_count > plan.max_positions:
        warnings.append(f"候选多于计划仓位，先按排名只取前 {plan.max_positions} 只。")
    summary = plan.summary
    if positions:
        names = "、".join(_label(item.instrument_id, item.instrument_label) for item in positions[:3])
        summary = f"建议先用 {len(positions)} 个仓位验证：{names}；保留现金应对回撤或二次确认。"
    return PortfolioAdvisor(
        summary=summary,
        target_positions=plan.max_positions,
        suggested_positions=len(positions),
        allocated_weight_pct=round(plan.allocated_weight_pct, 2),
        cash_reserve_pct=round(max(0.0, 100.0 - plan.allocated_weight_pct), 2),
        max_single_position_pct=round(max_single, 2),
        blocked_count=plan.blocked_count,
        concentration_warnings=warnings[:5],
        positions=positions[:8],
    )


def _fallback_portfolio_plan(cards: list[OpportunityCard]) -> PortfolioPlan:
    allocations: list[PortfolioAllocation] = []
    for card in cards[:3]:
        decision = card.decision
        allocations.append(
            PortfolioAllocation(
                instrument_id=card.instrument_id,
                instrument_label=card.instrument_label,
                action=decision.action if decision else "watch_trigger",
                weight_pct=0.0 if decision and decision.risk_status == "blocked" else 8.0,
                risk_budget_pct=decision.suggested_risk_pct if decision else 0.5,
                max_position_pct=decision.max_position_pct if decision else 8.0,
                industry=card.market_context.industry if card.market_context else None,
                rationale=f"排序分 {round(card.rank_score * 100)}",
            )
        )
    allocated = sum(item.weight_pct for item in allocations)
    return PortfolioPlan(
        max_positions=3,
        total_risk_budget_pct=3.0,
        allocated_weight_pct=round(allocated, 2),
        eligible_count=sum(1 for card in cards if not _is_blocked(card)),
        blocked_count=sum(1 for card in cards if _is_blocked(card)),
        allocations=allocations,
        watchlist=[],
        rules=["默认单票不超过 8%，风险阻断不新开仓。"],
        summary="根据当前机会卡生成临时组合建议。",
    )


def _portfolio_concentration_warnings(allocations: list[PortfolioAllocation]) -> list[str]:
    industries = Counter(item.industry or "未分类" for item in allocations if item.weight_pct > 0)
    warnings: list[str] = []
    for industry, count in industries.items():
        if count >= 2:
            warnings.append(f"{industry} 方向已有 {count} 个仓位，注意同向波动。")
    return warnings


def _build_walk_forward_validation(health: list[StrategyHealth]) -> WalkForwardValidation:
    points = _health_points(health)
    caveats: list[str] = []
    if len(points) >= 3:
        windows = _split_validation_windows(points)
    else:
        windows = _synthetic_validation_windows(health)
        caveats.append("样本窗口不足，当前样本外结论来自策略健康摘要近似。")
    out_of_sample = next((item for item in windows if item.key == "out_of_sample"), None)
    if not windows:
        caveats.append("没有策略健康样本，回测页需要先跑历史验证。")
    summary = "暂无足够历史验证样本。"
    if out_of_sample:
        avg_text = _pct_text(out_of_sample.avg_return_10d)
        win_text = _pct_text(out_of_sample.win_rate_10d)
        summary = f"样本外窗口 {out_of_sample.label}：胜率 {win_text}，10日均值 {avg_text}。"
    return WalkForwardValidation(
        summary=summary,
        windows=windows,
        out_of_sample=out_of_sample,
        caveats=caveats,
    )


def _health_points(health: list[StrategyHealth]) -> list[StrategyHealthPoint]:
    points: list[StrategyHealthPoint] = []
    for item in health:
        points.extend(item.curve)
    return [point for point in points if point.sample_count > 0]


def _split_validation_windows(points: list[StrategyHealthPoint]) -> list[ValidationWindow]:
    ordered = points
    total = len(ordered)
    train_end = max(1, round(total * 0.5))
    validation_end = max(train_end + 1, round(total * 0.75))
    groups = [
        ("train", "训练期", ordered[:train_end]),
        ("validation", "验证期", ordered[train_end:validation_end]),
        ("out_of_sample", "样本外", ordered[validation_end:]),
    ]
    return [_window_from_points(key, label, group) for key, label, group in groups if group]


def _synthetic_validation_windows(health: list[StrategyHealth]) -> list[ValidationWindow]:
    if not health:
        return []
    points = [
        StrategyHealthPoint(
            label=item.name,
            sample_count=item.sample_count,
            win_rate_10d=item.win_rate_10d,
            avg_return_10d=item.avg_return_10d,
            avg_return_20d=item.avg_return_20d,
            max_loss_10d=item.max_loss_10d,
        )
        for item in health
        if item.sample_count > 0
    ]
    if not points:
        return []
    aggregate = _window_from_points("out_of_sample", "策略健康摘要", points)
    return [
        _copy_window(aggregate, "train", "历史训练"),
        _copy_window(aggregate, "validation", "最近验证"),
        aggregate,
    ]


def _window_from_points(
    key: str,
    label: str,
    points: list[StrategyHealthPoint],
) -> ValidationWindow:
    sample_count = sum(point.sample_count for point in points)
    win_rate = _weighted_metric(points, "win_rate_10d")
    avg_return = _weighted_metric(points, "avg_return_10d")
    avg_return_20d = _weighted_metric(points, "avg_return_20d")
    losses = [point.max_loss_10d for point in points if point.max_loss_10d is not None]
    max_loss = min(losses) if losses else None
    return ValidationWindow(
        key=key,
        label=label,
        sample_count=sample_count,
        win_rate_10d=_round_or_none(win_rate),
        avg_return_10d=_round_or_none(avg_return),
        avg_return_20d=_round_or_none(avg_return_20d),
        max_loss_10d=_round_or_none(max_loss),
        verdict=_validation_verdict(sample_count, win_rate, avg_return, max_loss),
    )


def _copy_window(window: ValidationWindow, key: str, label: str) -> ValidationWindow:
    return ValidationWindow(
        key=key,
        label=label,
        sample_count=window.sample_count,
        win_rate_10d=window.win_rate_10d,
        avg_return_10d=window.avg_return_10d,
        avg_return_20d=window.avg_return_20d,
        max_loss_10d=window.max_loss_10d,
        verdict=window.verdict,
    )


def _weighted_metric(points: list[StrategyHealthPoint], field: str) -> float | None:
    numerator = 0.0
    denominator = 0
    for point in points:
        value = getattr(point, field)
        if value is None:
            continue
        numerator += float(value) * point.sample_count
        denominator += point.sample_count
    return numerator / denominator if denominator else None


def _validation_verdict(
    sample_count: int,
    win_rate: float | None,
    avg_return: float | None,
    max_loss: float | None,
) -> str:
    if sample_count < 10:
        return "样本偏少"
    if win_rate is not None and win_rate >= 58 and (avg_return or 0) > 0:
        return "有效"
    if max_loss is not None and max_loss <= -10:
        return "回撤偏大"
    if avg_return is not None and avg_return <= 0:
        return "收益偏弱"
    return "观察"


def _build_strategy_attribution(
    cards: list[OpportunityCard],
    health: list[StrategyHealth],
) -> StrategyAttribution:
    health_by_id = {item.strategy_id: item for item in health}
    grouped: dict[str, list[OpportunityCard]] = defaultdict(list)
    for card in cards:
        grouped[card.primary_strategy_id or "unclassified"].append(card)
    total_score = sum(max(0.01, card.rank_score) for card in cards) or 1.0
    strategies: list[StrategyAttributionItem] = []
    for strategy_id, items in grouped.items():
        strategy_health = health_by_id.get(strategy_id)
        name, family = _strategy_identity(items[0], strategy_health, strategy_id)
        contribution = sum(max(0.01, card.rank_score) for card in items) / total_score * 100
        hubs = [_signal_hub(card) for card in items]
        trusts = [hub.trust_score for hub in hubs if hub is not None]
        calibrations = [card.strategy_calibration for card in items if card.strategy_calibration]
        samples = max(
            [strategy_health.sample_count if strategy_health else 0]
            + [item.sample_count for item in calibrations],
        )
        win_rate = strategy_health.win_rate_10d if strategy_health else _avg_calibration(calibrations)
        avg_return = (
            strategy_health.avg_return_10d
            if strategy_health
            else _avg_calibration(calibrations, "avg_return_10d")
        )
        strategies.append(
            StrategyAttributionItem(
                strategy_id=strategy_id,
                name=name,
                family=family,
                card_count=len(items),
                contribution_pct=round(contribution, 2),
                avg_rank_score=round(mean(card.rank_score for card in items), 4),
                avg_trust_score=round(mean(trusts), 4) if trusts else None,
                validated_samples=samples,
                win_rate_10d=_round_or_none(win_rate),
                avg_return_10d=_round_or_none(avg_return),
                top_instruments=[
                    _label(card.instrument_id, card.instrument_label)
                    for card in sorted(items, key=lambda card: card.rank_score, reverse=True)[:3]
                ],
            )
        )
    strategies.sort(key=lambda item: (item.contribution_pct, item.avg_rank_score), reverse=True)
    caveats = []
    if any(item.validated_samples < 10 for item in strategies):
        caveats.append("部分策略历史样本不足，建议先用模拟盘跟踪。")
    summary = "暂无策略归因。"
    if strategies:
        top = strategies[0]
        summary = f"推荐主要来自 {top.name}，贡献 {top.contribution_pct:.1f}%，覆盖 {top.card_count} 个机会。"
    return StrategyAttribution(summary=summary, strategies=strategies, caveats=caveats)


def _strategy_identity(
    card: OpportunityCard,
    health: StrategyHealth | None,
    strategy_id: str,
) -> tuple[str, str]:
    if health:
        return health.name, health.family
    for evaluation in card.strategy_evaluations:
        if evaluation.strategy_id == strategy_id:
            return evaluation.name, evaluation.family
    return strategy_id, "unknown"


def _avg_calibration(calibrations, field: str = "win_rate_10d") -> float | None:
    values = [getattr(item, field) for item in calibrations if getattr(item, field) is not None]
    return mean(values) if values else None


def _build_pool_quality(
    cards: list[OpportunityCard],
    rotation: MarketRotationRadar | None,
) -> RecommendationPoolQuality:
    actionable = [card for card in cards if not _is_blocked(card)]
    blocked_count = len(cards) - len(actionable)
    risk_filtered = sum(1 for card in cards if card.opportunity_bucket == "risk_filtered")
    mix = {
        "stock": sum(1 for card in cards if card.asset_type.lower() == "stock"),
        "etf": sum(
            1
            for card in cards
            if card.asset_type.upper() == "ETF" or card.opportunity_bucket == "etf_index"
        ),
        "other": sum(
            1
            for card in cards
            if card.asset_type.lower() not in {"stock", "etf"}
            and card.opportunity_bucket != "etf_index"
        ),
    }
    warnings: list[str] = []
    if cards and len(cards) < 10:
        warnings.append("当前候选池偏小，建议用全市场后台扫描扩大样本。")
    if cards and blocked_count / len(cards) >= 0.35:
        warnings.append("风险阻断比例偏高，追涨和不可交易约束较多。")
    if mix["etf"] == 0:
        warnings.append("候选池没有 ETF/指数工具，组合对冲和方向验证不足。")
    top_theme, top_share = _top_theme_share(rotation, cards)
    if top_share is not None and top_share >= 45:
        warnings.append(f"{top_theme} 集中度 {top_share:.1f}%，注意主题拥挤。")
    caveats = sum(len(card.data_caveats) for card in cards)
    summary = f"当前池 {len(cards)} 个机会，可行动 {len(actionable)} 个，风险阻断 {blocked_count} 个。"
    return RecommendationPoolQuality(
        summary=summary,
        total_cards=len(cards),
        actionable_count=len(actionable),
        blocked_count=blocked_count,
        risk_filtered_count=risk_filtered,
        data_caveats_count=caveats,
        asset_mix=mix,
        top_theme=top_theme,
        top_theme_share_pct=top_share,
        warnings=warnings[:6],
    )


def _top_theme_share(
    rotation: MarketRotationRadar | None,
    cards: list[OpportunityCard],
) -> tuple[str | None, float | None]:
    if not rotation or not rotation.themes or not cards:
        return None, None
    top = max(rotation.themes, key=lambda item: item.opportunity_count)
    return top.name, round(top.opportunity_count / len(cards) * 100, 2)


def _build_alert_digest(cards: list[OpportunityCard]) -> AlertDigest:
    by_kind: Counter[str] = Counter()
    by_instrument: Counter[str] = Counter()
    for card in cards:
        hub = _signal_hub(card)
        if hub is None:
            continue
        for alert in hub.alert_suggestions:
            by_kind[alert.kind] += 1
            by_instrument[_label(card.instrument_id, card.instrument_label)] += 1
    total = sum(by_kind.values())
    summary = "暂无可保存提醒。"
    if total:
        top_kind = by_kind.most_common(1)[0][0]
        summary = f"可生成 {total} 条提醒，重点覆盖 {top_kind}、买点和止损。"
    return AlertDigest(
        summary=summary,
        total_suggestions=total,
        by_kind=dict(sorted(by_kind.items())),
        top_instruments=[name for name, _ in by_instrument.most_common(5)],
    )


def _build_daily_summary(
    cards: list[OpportunityCard],
    rotation: MarketRotationRadar | None,
    validation: WalkForwardValidation,
    alerts: AlertDigest,
) -> DailyResearchSummary:
    actionable = [card for card in cards if not _is_blocked(card)]
    top_cards = sorted(actionable, key=lambda card: card.rank_score, reverse=True)[:3]
    blocked = sorted(
        [card for card in cards if _is_blocked(card)],
        key=lambda card: card.rank_score,
        reverse=True,
    )[:3]
    watch_themes = [theme.name for theme in (rotation.themes[:3] if rotation else [])]
    top_names = [_label(card.instrument_id, card.instrument_label) for card in top_cards]
    avoid_names = [_label(card.instrument_id, card.instrument_label) for card in blocked]
    headline = "暂无明确机会，先等待全市场扫描或历史验证。"
    if top_names:
        headline = f"今日先看 {top_names[0]}，同时跟踪 {len(top_names)} 个备选机会。"
    actions: list[str] = []
    if top_cards:
        first = top_cards[0]
        actions.append(
            f"先检查 {top_names[0]} 是否触发买点 {first.entry_plan.trigger_price or '-'}。"
        )
    if watch_themes:
        actions.append(f"观察 {watch_themes[0]} 方向是否继续扩散，避免只看单只股票。")
    if validation.out_of_sample:
        actions.append(f"用样本外结果复核策略：{validation.out_of_sample.verdict}。")
    if alerts.total_suggestions:
        actions.append("把买点、止损、目标价和信号转弱提醒保存到提醒页。")
    if avoid_names:
        actions.append(f"{avoid_names[0]} 已进入风险过滤，暂不追买。")
    return DailyResearchSummary(
        headline=headline,
        watch_themes=watch_themes,
        top_opportunities=top_names,
        avoid_list=avoid_names,
        next_actions=actions[:6],
    )


def _signal_hub(card: OpportunityCard):
    if card.signal_hub is not None:
        return card.signal_hub
    try:
        return build_signal_hub(card)
    except Exception:
        return None


def _is_blocked(card: OpportunityCard) -> bool:
    if card.opportunity_bucket == "risk_filtered":
        return True
    if card.tradability is not None and not card.tradability.can_open:
        return True
    if card.decision is None:
        return False
    return card.decision.risk_status == "blocked" or card.decision.action == "avoid"


def _label(instrument_id: str, label: str | None) -> str:
    return label or instrument_id


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _pct_text(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}%"
