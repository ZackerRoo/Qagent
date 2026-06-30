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


class UserAcceptanceCheck(BaseModel):
    key: str
    title: str
    status: str
    score: float = Field(ge=0.0, le=1.0)
    evidence: str
    action: str


class UserAcceptanceAudit(BaseModel):
    verdict: str
    readiness_score: float = Field(ge=0.0, le=1.0)
    checks: list[UserAcceptanceCheck] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class RankingCalibrationDiagnostic(BaseModel):
    key: str
    title: str
    status: str
    metric: str
    evidence: str
    action: str


class RankingCalibrationAudit(BaseModel):
    summary: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    diagnostics: list[RankingCalibrationDiagnostic] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    weight_guidance: dict[str, str] = Field(default_factory=dict)


class DataReliabilityCheck(BaseModel):
    key: str
    label: str
    status: str
    source: str
    evidence: str
    action: str


class DataReliabilityAudit(BaseModel):
    summary: str
    score: float = Field(ge=0.0, le=1.0)
    ready_count: int
    partial_count: int
    missing_count: int
    checks: list[DataReliabilityCheck] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class ResearchCommandCenter(BaseModel):
    as_of: str
    portfolio_advisor: PortfolioAdvisor
    walk_forward_validation: WalkForwardValidation
    strategy_attribution: StrategyAttribution
    recommendation_pool_quality: RecommendationPoolQuality
    alert_digest: AlertDigest
    daily_research_summary: DailyResearchSummary
    user_acceptance_audit: UserAcceptanceAudit
    ranking_calibration_audit: RankingCalibrationAudit
    data_reliability_audit: DataReliabilityAudit
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
    data_audit = _build_data_reliability_audit(data_health or {}, cards)
    acceptance = _build_user_acceptance_audit(
        cards=cards,
        validation=validation,
        alerts=alerts,
        pool=pool,
        data_reliability=data_audit,
    )
    ranking_audit = _build_ranking_calibration_audit(
        cards=cards,
        pool=pool,
        attribution=attribution,
    )
    merged_health = {
        **(data_health or {}),
        "research_center_cards": str(len(cards)),
        "research_center_strategies": str(len(attribution.strategies)),
        "research_center_alert_suggestions": str(alerts.total_suggestions),
        "research_acceptance_score": f"{acceptance.readiness_score:.2f}",
        "research_ranking_calibration_score": f"{ranking_audit.confidence_score:.2f}",
        "research_data_reliability_score": f"{data_audit.score:.2f}",
    }
    return ResearchCommandCenter(
        as_of=date.today().isoformat(),
        portfolio_advisor=portfolio,
        walk_forward_validation=validation,
        strategy_attribution=attribution,
        recommendation_pool_quality=pool,
        alert_digest=alerts,
        daily_research_summary=summary,
        user_acceptance_audit=acceptance,
        ranking_calibration_audit=ranking_audit,
        data_reliability_audit=data_audit,
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


def _build_user_acceptance_audit(
    *,
    cards: list[OpportunityCard],
    validation: WalkForwardValidation,
    alerts: AlertDigest,
    pool: RecommendationPoolQuality,
    data_reliability: DataReliabilityAudit,
) -> UserAcceptanceAudit:
    actionable = [card for card in cards if not _is_blocked(card)]
    leader = actionable[0] if actionable else (cards[0] if cards else None)
    checks = [
        _acceptance_check(
            "opportunity_selection",
            "能不能找到机会",
            1.0 if len(cards) >= 20 and actionable else 0.65 if cards else 0.0,
            f"当前候选 {len(cards)} 个，可行动 {len(actionable)} 个。",
            "先用全 A 后台扫描扩充候选池，再看机会列表前 20 个。",
        ),
        _acceptance_check(
            "top_recommendation_explanation",
            "能不能看懂为什么推荐",
            _leader_explanation_score(leader),
            _leader_explanation_evidence(leader),
            "首选卡必须同时给出中文名称、推荐理由、概率校准和风险解释。",
        ),
        _acceptance_check(
            "buy_sell_plan",
            "能不能知道怎么买卖",
            _trade_plan_score(leader),
            _trade_plan_evidence(leader),
            "买点、止损、目标价和禁追位缺一项都只能观察。",
        ),
        _acceptance_check(
            "backtest_or_followthrough",
            "能不能验证过去是否有效",
            _validation_score(validation),
            validation.summary,
            "在回测页复核当前推荐 Top N，并持续看推荐闭环 30/60/90 天表现。",
        ),
        _acceptance_check(
            "paper_and_alert_loop",
            "能不能落到模拟盘和提醒",
            1.0 if alerts.total_suggestions >= max(1, len(actionable)) else 0.62 if alerts.total_suggestions else 0.25,
            alerts.summary,
            "把前 3-5 个候选加入模拟盘，并保存买点、止损、目标和变弱提醒。",
        ),
        _acceptance_check(
            "data_realism",
            "数据是否足够真实",
            data_reliability.score,
            data_reliability.summary,
            "优先补齐复权、停牌、涨跌停、ST、行业和指数成分数据。",
        ),
    ]
    score = mean(check.score for check in checks) if checks else 0.0
    blockers = [check.title for check in checks if check.status == "block"]
    next_actions = [check.action for check in checks if check.status != "pass"][:5]
    if not next_actions:
        next_actions = [
            "按推荐列表前 3-5 个候选做模拟盘验证。",
            "每天复盘推荐闭环，保留有效策略、降权失效策略。",
        ]
    verdict = "可进入日常使用"
    if score < 0.45 or blockers:
        verdict = "暂不适合直接使用"
    elif score < 0.72:
        verdict = "可小仓验证"
    return UserAcceptanceAudit(
        verdict=verdict,
        readiness_score=round(score, 4),
        checks=checks,
        blockers=blockers,
        next_actions=next_actions,
    )


def _build_ranking_calibration_audit(
    *,
    cards: list[OpportunityCard],
    pool: RecommendationPoolQuality,
    attribution: StrategyAttribution,
) -> RankingCalibrationAudit:
    diagnostics = [
        _rank_probability_alignment(cards),
        _ranking_concentration(pool, attribution),
        _ranking_coverage(cards),
        _ranking_score_spread(cards),
        _probability_coverage(cards),
    ]
    confidence = mean(_diagnostic_score(item.status) for item in diagnostics) if diagnostics else 0.0
    actions = [item.action for item in diagnostics if item.status != "pass"][:5]
    if not actions:
        actions = ["保持当前排序框架，继续用模拟盘和闭环结果校准权重。"]
    weak = [item for item in attribution.strategies if (item.win_rate_10d or 0) < 45 or (item.avg_return_10d or 0) < 0]
    strong = [item for item in attribution.strategies if (item.win_rate_10d or 0) >= 56 and (item.avg_return_10d or 0) > 0]
    guidance = {
        "raise": "、".join(item.name for item in strong[:3]) or "暂无明确加权策略",
        "lower": "、".join(item.name for item in weak[:3]) or "暂无明确降权策略",
        "observe": "优先观察分数高但概率偏低、主题过度集中的候选。",
    }
    summary = (
        f"排序验收分 {confidence:.0%}："
        f"{sum(1 for item in diagnostics if item.status == 'pass')} 项通过，"
        f"{sum(1 for item in diagnostics if item.status == 'warn')} 项需观察，"
        f"{sum(1 for item in diagnostics if item.status == 'block')} 项阻断。"
    )
    return RankingCalibrationAudit(
        summary=summary,
        confidence_score=round(confidence, 4),
        diagnostics=diagnostics,
        suggested_actions=actions,
        weight_guidance=guidance,
    )


def _build_data_reliability_audit(
    data_health: dict[str, str],
    cards: list[OpportunityCard],
) -> DataReliabilityAudit:
    provider = data_health.get("provider", "unknown")
    checks = [
        _data_check(
            "provider",
            "行情源",
            "pass" if provider not in {"fixture", "unknown"} else "warn",
            provider,
            f"当前 provider={provider}。",
            "开发样例可用于功能测试，真实使用优先切到免费/正式行情源。",
        ),
        _data_check(
            "market_cache",
            "SQLite 行情缓存",
            "pass" if data_health.get("market_cache") == "enabled" else "warn",
            data_health.get("market_cache", "missing"),
            "用于避免每次全量扫描都重新拉数据。",
            "确保刷新快照和后台扫描会写入 SQLite。",
        ),
        _data_check(
            "a_share_readiness",
            "A 股数据完整度",
            _score_status(_health_float(data_health, "a_share_data_readiness_score")),
            data_health.get("a_share_data_readiness_score", "-"),
            f"当前 A 股数据体检分 {data_health.get('a_share_data_readiness_score', '-')}。",
            "低于 0.70 时不要把结果当成可直接交易信号。",
        ),
        _data_check(
            "a_share_price_limit",
            "涨跌停/交易约束",
            _ready_status(data_health.get("a_share_price_limit")),
            data_health.get("a_share_price_limit", "missing"),
            "影响能不能买入、是否追高、是否一字板不可成交。",
            "必须持续校验涨跌停、停牌、权限和 T+1 约束。",
        ),
        _data_check(
            "a_share_liquidity",
            "流动性",
            _ready_status(data_health.get("a_share_liquidity")),
            data_health.get("a_share_liquidity", "missing"),
            "成交额/成交量会影响滑点和能否退出。",
            "流动性 partial 或 missing 时下调仓位和排序。",
        ),
        _data_check(
            "a_share_announcements",
            "公告/财报/事件",
            _ready_status(data_health.get("a_share_announcements")),
            data_health.get("a_share_announcements", "missing"),
            "缺公告会让事件驱动和财务传导假设失真。",
            "正式使用前补公告、财报、龙虎榜和资金流。",
        ),
        _data_check(
            "probability_calibration",
            "概率校准覆盖",
            _coverage_status(data_health.get("probability_calibration_cards"), len(cards)),
            data_health.get("probability_calibration_cards", "0"),
            "每张推荐卡都应有胜率估计和排序调权。",
            "如果覆盖不足，先刷新扫描或清理旧缓存水合结果。",
        ),
    ]
    ready = sum(1 for check in checks if check.status == "pass")
    partial = sum(1 for check in checks if check.status == "warn")
    missing = sum(1 for check in checks if check.status == "block")
    score = mean(_diagnostic_score(check.status) for check in checks) if checks else 0.0
    gaps = [check.action for check in checks if check.status != "pass"][:6]
    summary = f"数据可靠性 {score:.0%}：可用 {ready} 项，部分可用 {partial} 项，缺口 {missing} 项。"
    return DataReliabilityAudit(
        summary=summary,
        score=round(score, 4),
        ready_count=ready,
        partial_count=partial,
        missing_count=missing,
        checks=checks,
        gaps=gaps,
    )


def _acceptance_check(
    key: str,
    title: str,
    score: float,
    evidence: str,
    action: str,
) -> UserAcceptanceCheck:
    clamped = max(0.0, min(1.0, score))
    if clamped >= 0.78:
        status = "pass"
    elif clamped >= 0.45:
        status = "warn"
    else:
        status = "block"
    return UserAcceptanceCheck(
        key=key,
        title=title,
        status=status,
        score=round(clamped, 4),
        evidence=evidence,
        action=action,
    )


def _leader_explanation_score(card: OpportunityCard | None) -> float:
    if card is None:
        return 0.0
    score = 0.0
    if card.instrument_label:
        score += 0.18
    if card.recommendation_summary and card.recommendation_summary.headline:
        score += 0.2
    if card.probability_forecast:
        score += 0.24
    if card.recommendation_quality:
        score += 0.18
    if card.confidence_explanation:
        score += 0.1
    if card.rank_reasons:
        score += 0.1
    return min(1.0, score)


def _leader_explanation_evidence(card: OpportunityCard | None) -> str:
    if card is None:
        return "当前没有首选推荐。"
    parts = [_label(card.instrument_id, card.instrument_label)]
    if card.probability_forecast:
        parts.append(f"10日胜率估计 {card.probability_forecast.win_probability_10d:.0%}")
    if card.recommendation_quality:
        parts.append(f"推荐质量 {card.recommendation_quality.score:.0%}")
    return "；".join(parts)


def _trade_plan_score(card: OpportunityCard | None) -> float:
    if card is None:
        return 0.0
    fields = [
        card.entry_plan.trigger_price,
        card.entry_plan.no_chase_above,
        card.exit_plan.initial_stop,
        card.exit_plan.target_1,
    ]
    return sum(value is not None for value in fields) / len(fields)


def _trade_plan_evidence(card: OpportunityCard | None) -> str:
    if card is None:
        return "当前没有可评估交易计划。"
    return (
        f"触发 {card.entry_plan.trigger_price or '-'}，"
        f"止损 {card.exit_plan.initial_stop or '-'}，"
        f"目标 {card.exit_plan.target_1 or '-'}，"
        f"禁追 {card.entry_plan.no_chase_above or '-'}。"
    )


def _validation_score(validation: WalkForwardValidation) -> float:
    window = validation.out_of_sample
    if window is None or window.sample_count <= 0:
        return 0.0
    if window.sample_count >= 20 and (window.avg_return_10d or 0) > 0:
        return 1.0
    if window.sample_count >= 10:
        return 0.72
    return 0.5


def _rank_probability_alignment(cards: list[OpportunityCard]) -> RankingCalibrationDiagnostic:
    ranked = sorted(cards, key=lambda card: card.rank_score, reverse=True)[:20]
    forecasted = [card for card in ranked if card.probability_forecast is not None]
    mismatches = [
        card
        for card in forecasted
        if card.rank_score >= 0.68
        and (
            card.probability_forecast.win_probability_10d < 0.5
            or card.probability_forecast.expected_return_10d < 0
        )
    ]
    rate = len(mismatches) / len(forecasted) if forecasted else 1.0
    if not forecasted:
        status = "block"
    elif rate <= 0.15:
        status = "pass"
    elif rate <= 0.35:
        status = "warn"
    else:
        status = "block"
    return RankingCalibrationDiagnostic(
        key="rank_probability_alignment",
        title="排序和概率是否一致",
        status=status,
        metric=f"{len(mismatches)}/{len(forecasted)}",
        evidence=f"前 20 个已校准候选中，{len(mismatches)} 个高排序但胜率/期望收益偏弱。",
        action="高排序但概率偏弱的票要继续降权，或要求更强买点确认后再进入可买池。",
    )


def _ranking_concentration(
    pool: RecommendationPoolQuality,
    attribution: StrategyAttribution,
) -> RankingCalibrationDiagnostic:
    top_strategy = attribution.strategies[0] if attribution.strategies else None
    strategy_share = top_strategy.contribution_pct if top_strategy else 0.0
    theme_share = pool.top_theme_share_pct or 0.0
    max_share = max(strategy_share, theme_share)
    status = "pass" if max_share < 45 else "warn" if max_share < 65 else "block"
    leader = top_strategy.name if strategy_share >= theme_share and top_strategy else pool.top_theme or "-"
    return RankingCalibrationDiagnostic(
        key="concentration_control",
        title="推荐是否过度集中",
        status=status,
        metric=f"{max_share:.1f}%",
        evidence=f"最高集中来源 {leader}，占比 {max_share:.1f}%。",
        action="如果集中度过高，限制同策略/同主题连续上榜数量，引入 ETF 和不同板块候选。",
    )


def _ranking_coverage(cards: list[OpportunityCard]) -> RankingCalibrationDiagnostic:
    asset_types = {card.asset_type.lower() for card in cards}
    has_etf = any(card.asset_type.upper() == "ETF" or card.opportunity_bucket == "etf_index" for card in cards)
    has_stock = "stock" in asset_types
    status = "pass" if has_etf and has_stock else "warn" if cards else "block"
    return RankingCalibrationDiagnostic(
        key="market_coverage",
        title="股票/ETF/主题是否进入统一池",
        status=status,
        metric=f"股票 {sum(card.asset_type.lower() == 'stock' for card in cards)} / ETF {sum(card.asset_type.upper() == 'ETF' or card.opportunity_bucket == 'etf_index' for card in cards)}",
        evidence="统一池需要同时允许个股、ETF、指数工具和主题候选参与排序。",
        action="如果 ETF 或主题候选缺失，先检查全 A 综合池和指数/ETF 补充池是否写入。",
    )


def _ranking_score_spread(cards: list[OpportunityCard]) -> RankingCalibrationDiagnostic:
    ranked = sorted(cards, key=lambda card: card.rank_score, reverse=True)
    if len(ranked) < 5:
        return RankingCalibrationDiagnostic(
            key="score_spread",
            title="排序分差是否足够",
            status="warn",
            metric=f"{len(ranked)} 个",
            evidence="候选太少，无法判断分数分层。",
            action="扩大扫描范围后再判断排序分层。",
        )
    spread = ranked[0].rank_score - ranked[min(9, len(ranked) - 1)].rank_score
    status = "pass" if spread >= 0.05 else "warn"
    return RankingCalibrationDiagnostic(
        key="score_spread",
        title="排序分差是否能区分强弱",
        status=status,
        metric=f"{spread:.1%}",
        evidence=f"首位和第 {min(10, len(ranked))} 位排序分差 {spread:.1%}。",
        action="分差太小时，提高概率、质量和风险项权重，避免推荐看起来都差不多。",
    )


def _probability_coverage(cards: list[OpportunityCard]) -> RankingCalibrationDiagnostic:
    coverage = sum(card.probability_forecast is not None for card in cards)
    rate = coverage / len(cards) if cards else 0.0
    status = "pass" if rate >= 0.9 else "warn" if rate >= 0.5 else "block"
    return RankingCalibrationDiagnostic(
        key="probability_coverage",
        title="概率校准是否覆盖推荐池",
        status=status,
        metric=f"{coverage}/{len(cards)}",
        evidence=f"{coverage} 个候选有 5/10/20 日胜率估计。",
        action="概率覆盖不足时，先刷新扫描或对旧缓存补水合，再允许排序出推荐。",
    )


def _data_check(
    key: str,
    label: str,
    status: str,
    source: str,
    evidence: str,
    action: str,
) -> DataReliabilityCheck:
    return DataReliabilityCheck(
        key=key,
        label=label,
        status=status,
        source=source,
        evidence=evidence,
        action=action,
    )


def _diagnostic_score(status: str) -> float:
    return {"pass": 1.0, "warn": 0.58, "block": 0.18}.get(status, 0.4)


def _score_status(value: float | None) -> str:
    if value is None:
        return "block"
    if value >= 0.7:
        return "pass"
    if value >= 0.45:
        return "warn"
    return "block"


def _ready_status(value: str | None) -> str:
    if value == "ready":
        return "pass"
    if value == "partial":
        return "warn"
    return "block"


def _coverage_status(value: str | None, total: int) -> str:
    count = _int_text(value)
    if total <= 0:
        return "block"
    rate = count / total
    if rate >= 0.9:
        return "pass"
    if rate >= 0.5:
        return "warn"
    return "block"


def _health_float(data_health: dict[str, str], key: str) -> float | None:
    try:
        return float(str(data_health.get(key, "")).strip())
    except (TypeError, ValueError):
        return None


def _int_text(value: str | None) -> int:
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0


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
