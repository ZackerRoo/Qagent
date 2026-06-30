from collections import Counter, defaultdict
from statistics import mean

import pandas as pd
from pydantic import BaseModel, Field

from qagent.domain.models import OpportunityCard
from qagent.strategies.models import StrategyHealth


class DataSourceQualityCheck(BaseModel):
    area: str
    label: str
    status: str
    severity: str
    coverage_ratio: float | None = None
    current_source: str | None = None
    impact: str
    recommended_action: str


class DataQualityCenter(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    adjustment_status: str
    suspension_status: str
    limit_status: str
    industry_status: str
    cache_status: str
    coverage_ratio: float | None = None
    source_checks: list[DataSourceQualityCheck] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str


class MarketBreadth(BaseModel):
    sample_count: int
    advance_count: int
    decline_count: int
    advance_ratio: float | None = None
    avg_change_pct: float | None = None
    median_change_pct: float | None = None
    limit_up_count: int = 0
    limit_down_count: int = 0


class MarketEnvironmentCenter(BaseModel):
    regime: str
    score: float = Field(ge=0.0, le=1.0)
    risk_budget_multiplier: float
    trend_status: str
    liquidity_status: str
    breadth: MarketBreadth
    top_themes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str


class StrategyWeight(BaseModel):
    strategy_id: str
    name: str
    family: str
    weight_pct: float
    reason: str


class StrategySchedulerCenter(BaseModel):
    mode: str
    weights: list[StrategyWeight] = Field(default_factory=list)
    preferred_families: list[str] = Field(default_factory=list)
    avoided_families: list[str] = Field(default_factory=list)
    risk_budget_multiplier: float
    rules: list[str] = Field(default_factory=list)
    summary: str


class RecommendationCalibrationCenter(BaseModel):
    summary: str
    score_multiplier: float
    promoted_count: int
    demoted_count: int
    rules_applied: list[str] = Field(default_factory=list)


class EventHypothesis(BaseModel):
    theme: str
    catalyst_type: str
    direction: str
    confidence: float = Field(ge=0.0, le=1.0)
    affected_instruments: list[str] = Field(default_factory=list)
    verification_path: list[str] = Field(default_factory=list)
    summary: str


class EventHypothesisCenter(BaseModel):
    summary: str
    hypotheses: list[EventHypothesis] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MarketIntelligenceCenter(BaseModel):
    data_quality: DataQualityCenter
    market_environment: MarketEnvironmentCenter
    strategy_scheduler: StrategySchedulerCenter
    recommendation_calibration: RecommendationCalibrationCenter
    event_hypotheses: EventHypothesisCenter
    data_health: dict[str, str] = Field(default_factory=dict)


def build_market_intelligence_center(
    *,
    cards: list[OpportunityCard],
    items: list[object],
    bars_by_instrument: dict[str, pd.DataFrame] | None = None,
    strategy_health: list[StrategyHealth] | None = None,
    data_health: dict[str, str] | None = None,
) -> MarketIntelligenceCenter:
    bars = bars_by_instrument or {}
    health = strategy_health or []
    health_data = data_health or {}
    data_quality = _build_data_quality(cards, items, bars, health_data)
    environment = _build_market_environment(cards, bars)
    scheduler = _build_strategy_scheduler(environment, health, cards)
    event_hypotheses = _build_event_hypotheses(cards, health_data)
    calibration = _build_calibration_center(
        cards,
        data_quality,
        environment,
        scheduler,
        event_hypotheses,
    )
    return MarketIntelligenceCenter(
        data_quality=data_quality,
        market_environment=environment,
        strategy_scheduler=scheduler,
        recommendation_calibration=calibration,
        event_hypotheses=event_hypotheses,
        data_health={
            **health_data,
            "market_intelligence_cards": str(len(cards)),
            "market_intelligence_items": str(len(items)),
            "market_intelligence_bars": str(len(bars)),
            "market_intelligence_regime": environment.regime,
            "market_intelligence_data_score": f"{data_quality.score:.2f}",
            "data_source_checks": str(len(data_quality.source_checks)),
            "data_source_ready": str(
                sum(1 for item in data_quality.source_checks if item.severity == "ok")
            ),
            "data_source_missing": str(
                sum(1 for item in data_quality.source_checks if item.severity == "risk")
            ),
        },
    )


def apply_market_intelligence_to_cards(
    cards: list[OpportunityCard],
    center: MarketIntelligenceCenter,
) -> list[OpportunityCard]:
    if not cards:
        return cards
    theme_support = _theme_support(center.event_hypotheses.hypotheses)
    scheduler_support = {
        item.strategy_id: item.weight_pct / 100 for item in center.strategy_scheduler.weights
    }
    for card in cards:
        quality = _card_quality_score(card, center.data_quality)
        market_fit = _card_market_fit_score(card, center.market_environment, theme_support)
        strategy_fit = scheduler_support.get(card.primary_strategy_id or "", 0.5)
        event_fit = _card_event_score(card, theme_support)
        dynamic_score = _clamp(
            card.rank_score * 0.48
            + quality * 0.16
            + market_fit * 0.16
            + strategy_fit * 0.12
            + event_fit * 0.08
            - _card_risk_penalty(card),
            0,
            1,
        )
        original = card.rank_score
        card.quality_score = round(quality, 4)
        card.market_fit_score = round(market_fit, 4)
        card.dynamic_score = round(dynamic_score, 4)
        card.rank_score = round(dynamic_score, 4)
        notes = _card_calibration_notes(card, original, center)
        card.calibration_notes = notes
        card.rank_reasons.extend(notes)
    return cards


def _build_data_quality(
    cards: list[OpportunityCard],
    items: list[object],
    bars_by_instrument: dict[str, pd.DataFrame],
    data_health: dict[str, str],
) -> DataQualityCenter:
    warnings: list[str] = []
    missing_inputs: list[str] = []
    provider = data_health.get("provider", "unknown")
    expected = max(
        _int_health(data_health, "scanned"),
        _int_health(data_health, "full_market_requested"),
        len(cards) + len(items),
        1,
    )
    available = len([frame for frame in bars_by_instrument.values() if not frame.empty])
    if not bars_by_instrument and cards:
        available = len(cards)
    coverage_ratio = round(min(1.0, available / expected), 4)
    caveats = [caveat for card in cards for caveat in card.data_caveats]
    if any("fixture" in caveat for caveat in caveats):
        missing_inputs.append("真实行情替代 fixture 样例")
    if not any("adjust" in str(caveat).lower() or "复权" in str(caveat) for caveat in caveats):
        missing_inputs.append("复权价格字段")
    if _int_health(data_health, "provider_error_count") > 0 or data_health.get("errors"):
        warnings.append("行情源存在错误或降级，推荐分数需要折扣。")
    if provider == "free":
        warnings.append("免费数据覆盖可用于开发验证，正式使用前需要复权、停牌和公告源补强。")

    adjustment_status = "ready" if "adjusted_bars" in data_health else "unknown"
    if provider == "free":
        adjustment_status = "partial"
    suspension_status = "ready" if data_health.get("suspension_flags") else "unknown"
    limit_status = "ready" if any(card.trading_status for card in cards) else "unknown"
    industry_status = "ready" if any(card.market_context for card in cards) else "partial"
    cache_status = "enabled" if data_health.get("market_cache") == "enabled" else "unknown"
    source_checks = _build_source_quality_checks(
        cards=cards,
        bars_by_instrument=bars_by_instrument,
        data_health=data_health,
        provider=provider,
        adjustment_status=adjustment_status,
        suspension_status=suspension_status,
        limit_status=limit_status,
        industry_status=industry_status,
        coverage_ratio=coverage_ratio,
    )
    score = _clamp(
        0.36
        + coverage_ratio * 0.24
        + (0.12 if limit_status == "ready" else 0.04)
        + (0.12 if industry_status == "ready" else 0.06)
        + (0.08 if cache_status == "enabled" else 0.02)
        + (0.08 if adjustment_status in {"ready", "partial"} else 0.02)
        - min(0.18, len(warnings) * 0.04),
        0,
        1,
    )
    summary = (
        f"数据质量 {score:.0%}：覆盖 {coverage_ratio:.0%}，"
        f"复权 {adjustment_status}，涨跌停 {limit_status}，行业 {industry_status}。"
    )
    return DataQualityCenter(
        score=round(score, 4),
        adjustment_status=adjustment_status,
        suspension_status=suspension_status,
        limit_status=limit_status,
        industry_status=industry_status,
        cache_status=cache_status,
        coverage_ratio=coverage_ratio,
        source_checks=source_checks,
        missing_inputs=missing_inputs[:8],
        warnings=warnings[:8],
        summary=summary,
    )


def _build_source_quality_checks(
    *,
    cards: list[OpportunityCard],
    bars_by_instrument: dict[str, pd.DataFrame],
    data_health: dict[str, str],
    provider: str,
    adjustment_status: str,
    suspension_status: str,
    limit_status: str,
    industry_status: str,
    coverage_ratio: float,
) -> list[DataSourceQualityCheck]:
    liquidity_coverage = _liquidity_coverage(bars_by_instrument, max(len(cards), 1))
    has_etf_or_index = any(
        card.asset_type.upper() == "ETF" or card.opportunity_bucket == "etf_index"
        for card in cards
    )
    has_index_membership = any(
        card.market_context and card.market_context.index_memberships for card in cards
    )
    index_status = (
        "ready"
        if data_health.get("index_constituents") or has_index_membership
        else "partial"
        if has_etf_or_index
        else "unknown"
    )
    fund_flow_status = "ready" if _has_any_health(data_health, "fund_flow", "money_flow") else "missing"
    dragon_tiger_status = "ready" if _has_any_health(data_health, "dragon_tiger", "l2_rank") else "missing"
    announcements_status = (
        "ready"
        if _int_health(data_health, "strategy_announcements") > 0
        else "partial"
        if _int_health(data_health, "strategy_fundamentals") > 0
        else "missing"
    )
    liquidity_status = "ready" if liquidity_coverage >= 0.85 else "partial" if liquidity_coverage else "missing"

    checks = [
        _source_check(
            "adjusted_price",
            "复权价格",
            adjustment_status,
            coverage_ratio,
            provider,
            "不复权会扭曲均线、突破和回测收益。",
            "正式使用前接入前复权/后复权日线，并在扫描缓存里标记复权类型。",
        ),
        _source_check(
            "suspension",
            "停复牌",
            suspension_status,
            None,
            provider,
            "停牌或复牌缺口会让触发价和止损判断失真。",
            "补充停牌日历；停牌、刚复牌和长期无成交标的默认降权。",
        ),
        _source_check(
            "price_limit",
            "涨跌停",
            limit_status,
            None,
            provider,
            "涨停不可追、跌停不可卖会影响推荐可执行性。",
            "接入涨跌停价和封单状态；涨停附近只提醒不追价。",
        ),
        _source_check(
            "industry",
            "行业/概念",
            industry_status,
            None,
            provider,
            "行业缺失会影响主题轮动、分散度和同向风险控制。",
            "补全行业、概念和指数成分映射，避免推荐集中在隐藏同一方向。",
        ),
        _source_check(
            "liquidity",
            "成交额/流动性",
            liquidity_status,
            liquidity_coverage,
            provider,
            "流动性差会放大滑点，回测收益也更难真实成交。",
            "用成交额、换手率和近 20 日成交稳定性做硬过滤。",
        ),
        _source_check(
            "index_constituents",
            "ETF/指数成分",
            index_status,
            None,
            provider,
            "ETF 和指数机会需要知道跟踪对象，否则主题解释不完整。",
            "补齐 ETF 跟踪指数、指数成分和成分权重。",
        ),
        _source_check(
            "fund_flow",
            "资金流",
            fund_flow_status,
            None,
            provider,
            "缺少资金流时，放量突破只能看到量价，无法确认主动资金方向。",
            "补充主力净流入、北向/融资余额或同类免费可替代资金字段。",
        ),
        _source_check(
            "dragon_tiger",
            "龙虎榜",
            dragon_tiger_status,
            None,
            provider,
            "强势股短线异动需要龙虎榜验证席位性质和一日游风险。",
            "补充龙虎榜、异常波动公告和席位标签，作为短线追踪而非硬推荐。",
        ),
        _source_check(
            "announcements",
            "公告/财报",
            announcements_status,
            None,
            provider,
            "缺少公告和财报会让事件催化只停留在价格层面。",
            "接入公告、业绩预告和财报摘要，用来验证推荐背后的真实财务传导。",
        ),
    ]
    return checks


def _source_check(
    area: str,
    label: str,
    status: str,
    coverage_ratio: float | None,
    current_source: str,
    impact: str,
    recommended_action: str,
) -> DataSourceQualityCheck:
    return DataSourceQualityCheck(
        area=area,
        label=label,
        status=status,
        severity=_source_severity(status),
        coverage_ratio=coverage_ratio,
        current_source=current_source,
        impact=impact,
        recommended_action=recommended_action,
    )


def _source_severity(status: str) -> str:
    normalized = status.lower()
    if normalized in {"ready", "enabled"}:
        return "ok"
    if normalized in {"partial", "unknown"}:
        return "watch"
    return "risk"


def _liquidity_coverage(
    bars_by_instrument: dict[str, pd.DataFrame],
    expected_count: int,
) -> float:
    if expected_count <= 0:
        return 0
    usable = 0
    for frame in bars_by_instrument.values():
        if frame.empty or "volume" not in frame.columns:
            continue
        volume = pd.to_numeric(frame["volume"], errors="coerce")
        if volume.notna().any() and float(volume.fillna(0).tail(20).mean()) > 0:
            usable += 1
    return round(min(1.0, usable / expected_count), 4)


def _has_any_health(data_health: dict[str, str], *keys: str) -> bool:
    return any(bool(data_health.get(key)) for key in keys)


def _build_market_environment(
    cards: list[OpportunityCard],
    bars_by_instrument: dict[str, pd.DataFrame],
) -> MarketEnvironmentCenter:
    changes = [_latest_change_pct(frame) for frame in bars_by_instrument.values() if not frame.empty]
    changes = [value for value in changes if value is not None]
    if not changes:
        changes = [_card_proxy_change(card) for card in cards]
    changes = [value for value in changes if value is not None]
    advance_count = sum(1 for value in changes if value > 0)
    decline_count = sum(1 for value in changes if value < 0)
    sample_count = len(changes)
    advance_ratio = round(advance_count / sample_count, 4) if sample_count else None
    avg_change = round(mean(changes), 4) if changes else None
    median_change = round(float(pd.Series(changes).median()), 4) if changes else None
    limit_up_count = sum(1 for card in cards if card.trading_status and card.trading_status.status == "limit_up")
    limit_down_count = sum(
        1 for card in cards if card.trading_status and card.trading_status.status == "limit_down"
    )
    score = _environment_score(advance_ratio, avg_change, cards)
    regime = _regime(score, sample_count)
    top_themes = _top_themes(cards)
    warnings: list[str] = []
    if sample_count < 5:
        warnings.append("市场环境样本较少，建议结合指数和成交额二次确认。")
    if limit_down_count:
        warnings.append(f"样本内有 {limit_down_count} 个接近跌停，先降低追涨策略权重。")
    trend_status = "uptrend" if score >= 0.62 else "downtrend" if score < 0.42 else "range"
    liquidity_status = _liquidity_status(bars_by_instrument)
    risk_multiplier = 1.1 if regime == "risk_on" else 1.0 if regime == "constructive" else 0.75 if regime == "mixed" else 0.5
    summary = (
        f"市场状态 {regime}：上涨占比 {_format_rate(advance_ratio)}，"
        f"样本均涨跌 {avg_change:+.2f}%。" if avg_change is not None
        else f"市场状态 {regime}：缺少足够涨跌样本。"
    )
    return MarketEnvironmentCenter(
        regime=regime,
        score=round(score, 4),
        risk_budget_multiplier=round(risk_multiplier, 2),
        trend_status=trend_status,
        liquidity_status=liquidity_status,
        breadth=MarketBreadth(
            sample_count=sample_count,
            advance_count=advance_count,
            decline_count=decline_count,
            advance_ratio=advance_ratio,
            avg_change_pct=avg_change,
            median_change_pct=median_change,
            limit_up_count=limit_up_count,
            limit_down_count=limit_down_count,
        ),
        top_themes=top_themes,
        warnings=warnings[:6],
        summary=summary,
    )


def _build_strategy_scheduler(
    environment: MarketEnvironmentCenter,
    strategy_health: list[StrategyHealth],
    cards: list[OpportunityCard],
) -> StrategySchedulerCenter:
    grouped = _strategy_candidates(strategy_health, cards)
    if not grouped:
        grouped = {
            "factor_rotation_watch": ("Factor rotation watch", "factor_rotation", 1, 0.5),
            "healthy_pullback": ("Healthy pullback", "technical_pullback", 1, 0.45),
        }
    raw_weights: dict[str, tuple[str, str, float]] = {}
    for strategy_id, (name, family, count, quality) in grouped.items():
        regime_boost = _family_regime_boost(family, environment.regime)
        raw_weights[strategy_id] = (name, family, max(0.05, count * (0.45 + quality) * regime_boost))
    total = sum(value[2] for value in raw_weights.values()) or 1
    weights = [
        StrategyWeight(
            strategy_id=strategy_id,
            name=name,
            family=family,
            weight_pct=round(raw / total * 100, 2),
            reason=_scheduler_reason(family, environment.regime, raw / total),
        )
        for strategy_id, (name, family, raw) in raw_weights.items()
    ]
    weights.sort(key=lambda item: item.weight_pct, reverse=True)
    preferred = [item.family for item in weights[:3]]
    avoided = ["high_beta_breakout"] if environment.regime in {"risk_off", "thin"} else []
    mode = "进攻" if environment.regime == "risk_on" else "均衡" if environment.regime in {"constructive", "mixed"} else "防守"
    rules = [
        f"{mode}模式：总风险预算乘数 {environment.risk_budget_multiplier:.2f}。",
        "策略权重会反向影响推荐分数，近期验证差或环境不匹配的策略自动降权。",
    ]
    if avoided:
        rules.append("弱环境下减少高波动突破追涨，优先 ETF、回调和低风险形态。")
    return StrategySchedulerCenter(
        mode=mode,
        weights=weights[:8],
        preferred_families=list(dict.fromkeys(preferred)),
        avoided_families=avoided,
        risk_budget_multiplier=environment.risk_budget_multiplier,
        rules=rules,
        summary=f"{mode}调度：优先 {weights[0].name if weights else '暂无策略'}，根据市场状态动态调仓。",
    )


def _build_calibration_center(
    cards: list[OpportunityCard],
    data_quality: DataQualityCenter,
    environment: MarketEnvironmentCenter,
    scheduler: StrategySchedulerCenter,
    events: EventHypothesisCenter,
) -> RecommendationCalibrationCenter:
    base_multiplier = _clamp(
        0.55 + data_quality.score * 0.2 + environment.score * 0.2 + scheduler.risk_budget_multiplier * 0.05,
        0.45,
        1.15,
    )
    promoted = sum(1 for card in cards if _card_has_supported_theme(card, events.hypotheses))
    demoted = sum(
        1
        for card in cards
        if card.decision and card.decision.risk_status == "blocked"
        or len(card.data_caveats) >= 2
    )
    rules = [
        "数据质量低于 70% 时，所有推荐自动压低动态分。",
        "市场环境偏弱时，突破追涨和高波动信号降权。",
        "行业/主题与事件假设一致时，小幅提高跟踪优先级。",
    ]
    if data_quality.warnings:
        rules.append("存在数据警告，推荐只能作为观察和模拟盘验证。")
    summary = (
        f"动态校准乘数 {base_multiplier:.2f}，事件支持 {promoted} 个，"
        f"风险或数据降权 {demoted} 个。"
    )
    return RecommendationCalibrationCenter(
        summary=summary,
        score_multiplier=round(base_multiplier, 4),
        promoted_count=promoted,
        demoted_count=demoted,
        rules_applied=rules,
    )


def _build_event_hypotheses(
    cards: list[OpportunityCard],
    data_health: dict[str, str],
) -> EventHypothesisCenter:
    theme_counts: Counter[str] = Counter()
    instruments_by_theme: dict[str, list[str]] = defaultdict(list)
    for card in cards:
        for theme in _card_themes(card):
            theme_counts[theme] += 1
            instruments_by_theme[theme].append(_label(card))

    data_sources: list[str] = []
    if _int_health(data_health, "strategy_announcements") > 0:
        data_sources.append("公告")
    if _int_health(data_health, "strategy_fundamentals") > 0:
        data_sources.append("财报/基本面")
    if _int_health(data_health, "strategy_analyst_insights") > 0:
        data_sources.append("分析师预期")
    if not data_sources:
        data_sources.append("价格/主题代理")

    hypotheses: list[EventHypothesis] = []
    for theme, count in theme_counts.most_common(4):
        catalyst_type = _catalyst_type(theme, data_sources)
        confidence = _clamp(0.42 + min(count, 5) * 0.08 + (0.08 if "公告" in data_sources else 0), 0, 0.9)
        hypotheses.append(
            EventHypothesis(
                theme=theme,
                catalyst_type=catalyst_type,
                direction="positive" if count >= 2 else "watch",
                confidence=round(confidence, 4),
                affected_instruments=instruments_by_theme[theme][:6],
                verification_path=[
                    "后续 3-10 个交易日是否触发买点",
                    "成交额和相对强度是否继续扩散",
                    "公告/财报/订单是否能进入收入或毛利率验证",
                ],
                summary=f"{theme} 聚集 {count} 个候选，作为事件假设跟踪，不直接等同买入结论。",
            )
        )
    warnings = [] if len(data_sources) > 1 else ["当前事件假设主要来自主题和价格代理，正式使用应补公告、资金流和财报源。"]
    summary = (
        f"识别 {len(hypotheses)} 条事件/主题假设，数据源：{'、'.join(data_sources)}。"
        if hypotheses
        else "暂无可聚合事件假设。"
    )
    return EventHypothesisCenter(
        summary=summary,
        hypotheses=hypotheses,
        data_sources=data_sources,
        warnings=warnings,
    )


def _card_quality_score(card: OpportunityCard, data_quality: DataQualityCenter) -> float:
    score = data_quality.score
    score -= min(0.18, len(card.data_caveats) * 0.04)
    if card.tradability and not card.tradability.can_open:
        score -= 0.18
    if card.trading_status and not card.trading_status.can_buy:
        score -= 0.12
    if card.market_context:
        score += 0.04
    if card.strategy_calibration:
        if card.strategy_calibration.readiness in {"limited_sample", "insufficient_history"}:
            score -= 0.08
        if card.strategy_calibration.avg_return_10d is not None and card.strategy_calibration.avg_return_10d < 0:
            score -= 0.08
        if card.strategy_calibration.max_loss_10d is not None and card.strategy_calibration.max_loss_10d <= -8:
            score -= 0.06
    return _clamp(score, 0, 1)


def _card_market_fit_score(
    card: OpportunityCard,
    environment: MarketEnvironmentCenter,
    theme_support: dict[str, float],
) -> float:
    score = 0.45 + environment.score * 0.28 + (card.factor_score or 0) * 0.17
    if card.market_context:
        score += max((theme_support.get(theme, 0) for theme in card.market_context.themes), default=0) * 0.1
    if environment.regime in {"risk_off", "thin"} and card.risk_reward and card.risk_reward < 2:
        score -= 0.08
    return _clamp(score, 0, 1)


def _card_event_score(card: OpportunityCard, theme_support: dict[str, float]) -> float:
    themes = _card_themes(card)
    if not themes:
        return 0.45
    return _clamp(0.35 + max((theme_support.get(theme, 0) for theme in themes), default=0), 0, 1)


def _card_calibration_notes(
    card: OpportunityCard,
    original_score: float,
    center: MarketIntelligenceCenter,
) -> list[str]:
    delta = card.rank_score - original_score
    direction = "上调" if delta > 0.005 else "下调" if delta < -0.005 else "维持"
    notes = [
        (
            f"动态校准：{direction} {delta:+.2f}，"
            f"数据 {card.quality_score:.0%}，市场匹配 {card.market_fit_score:.0%}。"
        )
    ]
    if center.market_environment.regime in {"risk_off", "thin"}:
        notes.append("市场环境偏弱，优先等待触发确认并降低仓位。")
    if _card_has_supported_theme(card, center.event_hypotheses.hypotheses):
        notes.append("事件假设与主题匹配，加入后续验证路径。")
    if card.data_caveats:
        notes.append("存在数据限制，先用模拟盘验证信号有效性。")
    return notes[:4]


def _card_risk_penalty(card: OpportunityCard) -> float:
    penalty = min(0.08, len(card.data_caveats) * 0.025)
    if card.strategy_calibration:
        if card.strategy_calibration.readiness in {"limited_sample", "insufficient_history"}:
            penalty += 0.04
        if card.strategy_calibration.avg_return_10d is not None and card.strategy_calibration.avg_return_10d < 0:
            penalty += 0.04
        if card.strategy_calibration.max_loss_10d is not None and card.strategy_calibration.max_loss_10d <= -8:
            penalty += 0.03
    if card.decision and card.decision.risk_status == "blocked":
        penalty += 0.08
    return min(0.22, penalty)


def _theme_support(hypotheses: list[EventHypothesis]) -> dict[str, float]:
    return {item.theme: item.confidence for item in hypotheses}


def _card_has_supported_theme(card: OpportunityCard, hypotheses: list[EventHypothesis]) -> bool:
    supported = {item.theme for item in hypotheses if item.confidence >= 0.5}
    return any(theme in supported for theme in _card_themes(card))


def _strategy_candidates(
    health: list[StrategyHealth],
    cards: list[OpportunityCard],
) -> dict[str, tuple[str, str, int, float]]:
    counts = Counter(card.primary_strategy_id for card in cards if card.primary_strategy_id)
    result: dict[str, tuple[str, str, int, float]] = {}
    for item in health:
        quality = 0.5
        if item.win_rate_10d is not None:
            quality += (item.win_rate_10d - 50) / 100
        if item.avg_return_10d is not None:
            quality += item.avg_return_10d / 30
        if item.readiness == "validated":
            quality += 0.12
        elif item.readiness in {"limited_sample", "insufficient_history"}:
            quality -= 0.08
        result[item.strategy_id] = (
            item.name,
            item.family,
            max(1, counts.get(item.strategy_id, 0)),
            _clamp(quality, 0.1, 1),
        )
    for strategy_id, count in counts.items():
        if strategy_id not in result:
            result[strategy_id] = (strategy_id, "unclassified", count, 0.5)
    return result


def _family_regime_boost(family: str, regime: str) -> float:
    text = family.lower()
    if regime in {"risk_on", "constructive"}:
        if "breakout" in text or "momentum" in text or "factor" in text:
            return 1.18
        return 1.0
    if regime in {"risk_off", "thin"}:
        if "pullback" in text or "valuation" in text or "factor" in text:
            return 1.05
        if "breakout" in text or "momentum" in text:
            return 0.78
    return 1.0


def _scheduler_reason(family: str, regime: str, weight: float) -> str:
    if weight >= 0.35:
        return f"{regime} 环境下 {family} 权重最高，优先筛选同类信号。"
    return f"{family} 保持观察权重，等待市场和历史验证确认。"


def _latest_change_pct(frame: pd.DataFrame) -> float | None:
    if frame.empty or "close" not in frame.columns:
        return None
    ordered = frame.sort_values("trade_date")
    if len(ordered) < 2:
        return None
    previous = float(ordered.iloc[-2]["close"])
    latest = float(ordered.iloc[-1]["close"])
    if previous == 0:
        return None
    return round((latest / previous - 1) * 100, 4)


def _card_proxy_change(card: OpportunityCard) -> float | None:
    if card.trading_status and card.trading_status.change_pct is not None:
        return card.trading_status.change_pct
    return (card.factor_score - 0.5) * 2 if card.factor_score is not None else None


def _environment_score(
    advance_ratio: float | None,
    avg_change: float | None,
    cards: list[OpportunityCard],
) -> float:
    ratio_score = advance_ratio if advance_ratio is not None else 0.5
    change_score = _clamp(0.5 + (_coalesce(avg_change, 0) / 5), 0, 1)
    actionable = sum(
        1 for card in cards if not card.decision or card.decision.risk_status != "blocked"
    )
    actionable_score = actionable / len(cards) if cards else 0.5
    return _clamp(ratio_score * 0.38 + change_score * 0.32 + actionable_score * 0.3, 0, 1)


def _regime(score: float, sample_count: int) -> str:
    if sample_count <= 1:
        return "thin"
    if score >= 0.72:
        return "risk_on"
    if score >= 0.58:
        return "constructive"
    if score >= 0.42:
        return "mixed"
    return "risk_off"


def _liquidity_status(bars_by_instrument: dict[str, pd.DataFrame]) -> str:
    volumes = []
    for frame in bars_by_instrument.values():
        if not frame.empty and "volume" in frame.columns:
            volumes.extend(float(value) for value in frame.tail(20)["volume"].dropna().tolist())
    if not volumes:
        return "unknown"
    avg_volume = mean(volumes)
    if avg_volume >= 1_000_000:
        return "normal"
    if avg_volume >= 200_000:
        return "thin"
    return "weak"


def _top_themes(cards: list[OpportunityCard]) -> list[str]:
    counts = Counter(theme for card in cards for theme in _card_themes(card))
    return [theme for theme, _ in counts.most_common(5)]


def _card_themes(card: OpportunityCard) -> list[str]:
    themes = list(card.opportunity_tags)
    if card.market_context:
        themes.extend(card.market_context.themes)
        themes.append(card.market_context.industry)
    return [theme for theme in themes if theme]


def _catalyst_type(theme: str, data_sources: list[str]) -> str:
    if "公告" in data_sources:
        return "announcement"
    if "财报/基本面" in data_sources:
        return "earnings"
    if any(token in theme for token in ["AI", "芯片", "算力", "半导体", "存储"]):
        return "supply_chain"
    if "ETF" in theme or "指数" in theme:
        return "market_flow"
    return "theme_momentum"


def _label(card: OpportunityCard) -> str:
    return card.instrument_label or card.instrument_id


def _int_health(data_health: dict[str, str], key: str) -> int:
    try:
        return int(str(data_health.get(key, "0")))
    except ValueError:
        return 0


def _format_rate(value: float | None) -> str:
    if value is None:
        return "暂无"
    return f"{value * 100:.0f}%"


def _coalesce(value: float | None, default: float) -> float:
    return value if value is not None else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
