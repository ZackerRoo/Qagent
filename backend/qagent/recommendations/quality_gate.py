from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

from qagent.domain.models import (
    OpportunityCard,
    PositionScenario,
    PreTradeRiskCheck,
    PreTradeRiskProfile,
    RecommendationScoreBreakdown,
    RecommendationScoreComponent,
    RecommendationQualityCheck,
    RecommendationQualityProfile,
    RiskVeto,
)


def apply_recommendation_quality_gate(cards: list[OpportunityCard]) -> list[OpportunityCard]:
    for card in cards:
        original_rank_score = card.rank_score
        profile = build_recommendation_quality_profile(card)
        card.recommendation_quality = profile
        card.rank_score = _adjusted_rank_score(card, profile)
        card.dynamic_score = card.rank_score
        card.quality_score = profile.score
        card.recommendation_score = build_recommendation_score_breakdown(
            card,
            profile,
            original_rank_score,
        )
        card.pre_trade_risk = build_pre_trade_risk_profile(card, profile)
        card.position_scenario = build_position_scenario(card)
        _append_quality_notes(card, profile)
        _apply_blocking_decision(card, profile)
    return cards


def recommendation_quality_data_health(cards: list[OpportunityCard]) -> dict[str, str]:
    profiles = [card.recommendation_quality for card in cards if card.recommendation_quality]
    tier_counts: dict[str, int] = {}
    for profile in profiles:
        tier_counts[profile.tier] = tier_counts.get(profile.tier, 0) + 1
    blocked = sum(profile.block_count > 0 for profile in profiles)
    warned = sum(profile.warn_count > 0 for profile in profiles)
    return {
        "recommendation_quality_cards": str(len(profiles)),
        "recommendation_quality_blocked": str(blocked),
        "recommendation_quality_warned": str(warned),
        **{
            f"recommendation_quality_tier_{tier}": str(count)
            for tier, count in sorted(tier_counts.items())
        },
    }


def build_recommendation_quality_profile(card: OpportunityCard) -> RecommendationQualityProfile:
    checks = _checks(card)
    pass_count = sum(1 for check in checks if check.status == "pass")
    warn_count = sum(1 for check in checks if check.status == "warn")
    block_count = sum(1 for check in checks if check.status == "block")
    score = _quality_score(card, checks)
    tier = _tier(score, warn_count, block_count)
    return RecommendationQualityProfile(
        score=round(score, 4),
        tier=tier,
        summary=_summary(tier, score, warn_count, block_count),
        pass_count=pass_count,
        warn_count=warn_count,
        block_count=block_count,
        checks=checks,
    )


def build_recommendation_score_breakdown(
    card: OpportunityCard,
    profile: RecommendationQualityProfile,
    original_rank_score: float,
) -> RecommendationScoreBreakdown:
    components = _score_components(card, profile)
    positive_contribution = sum(component.contribution for component in components)
    weighted_score = _clamp(positive_contribution)
    penalty_score = round(sum(max(0.0, -check.score_impact) for check in profile.checks), 4)
    return RecommendationScoreBreakdown(
        final_score=card.rank_score,
        original_rank_score=round(original_rank_score, 4),
        quality_score=profile.score,
        weighted_score=round(weighted_score, 4),
        penalty_score=penalty_score,
        tier=profile.tier,
        summary=(
            f"推荐分 {card.rank_score:.0%}：质量 {profile.score:.0%}，"
            f"原始排序 {original_rank_score:.0%}，扣分项 {penalty_score:.0%}。"
        ),
        components=components,
    )


def build_pre_trade_risk_profile(
    card: OpportunityCard,
    profile: RecommendationQualityProfile,
) -> PreTradeRiskProfile:
    checks = _pre_trade_checks(card, profile)
    has_block = any(check.severity == "block" for check in checks)
    has_warning = any(check.severity == "warning" for check in checks)
    if has_block:
        status = "blocked"
        label = "不可买"
        can_buy = False
    elif has_warning:
        status = "warning"
        label = "先确认"
        can_buy = True
    else:
        status = "clear"
        label = "可小仓验证"
        can_buy = True

    decision = card.decision
    risk_budget = decision.suggested_risk_pct if decision and can_buy else 0.0
    max_position = decision.max_position_pct if decision and can_buy else 0.0
    return PreTradeRiskProfile(
        status=status,
        label=label,
        can_buy=can_buy,
        can_size_up=status == "clear" and profile.tier in {"high_quality", "quality_candidate"},
        risk_budget_pct=round(risk_budget, 2),
        max_position_pct=round(max_position, 2),
        next_action=_pre_trade_next_action(status, checks),
        summary=_pre_trade_summary(status, checks, risk_budget, max_position),
        checks=checks,
    )


def build_position_scenario(card: OpportunityCard) -> PositionScenario:
    decision = card.decision
    entry = card.entry_plan.trigger_price
    stop = card.exit_plan.initial_stop
    target_1 = card.exit_plan.target_1
    target_2 = card.exit_plan.target_2
    risk_pct = decision.suggested_risk_pct if decision else 0.0
    position_pct = decision.max_position_pct if decision else 0.0
    planned_loss_pct = _pct_change(entry, stop)
    target_1_gain_pct = _pct_change(entry, target_1)
    target_2_gain_pct = _pct_change(entry, target_2)
    account_drawdown = _account_impact(position_pct, planned_loss_pct)
    account_gain_1 = _account_impact(position_pct, target_1_gain_pct)
    account_gain_2 = (
        _account_impact(position_pct, target_2_gain_pct)
        if target_2_gain_pct is not None
        else None
    )
    position_value = _money(Decimal("100000") * Decimal(str(position_pct)) / Decimal("100"))
    min_lot = card.trading_constraints.min_lot if card.trading_constraints else None
    min_lot_cash = _money(entry * Decimal(min_lot)) if entry is not None and min_lot else None
    shares = _shares_for_budget(position_value, entry, min_lot)
    return PositionScenario(
        entry_price=entry,
        stop_price=stop,
        target_1_price=target_1,
        target_2_price=target_2,
        suggested_risk_pct=round(risk_pct, 2),
        suggested_position_pct=round(position_pct, 2),
        position_value_per_100k=position_value,
        shares_per_100k=shares,
        min_lot=min_lot,
        min_lot_cash=min_lot_cash,
        planned_loss_pct=planned_loss_pct,
        target_1_gain_pct=target_1_gain_pct,
        target_2_gain_pct=target_2_gain_pct,
        account_drawdown_if_stopped_pct=account_drawdown,
        account_gain_at_target_1_pct=account_gain_1,
        account_gain_at_target_2_pct=account_gain_2,
        risk_reward=card.risk_reward,
        summary=_position_summary(
            position_pct,
            account_drawdown,
            account_gain_1,
            min_lot_cash,
            shares,
        ),
    )


def _checks(card: OpportunityCard) -> list[RecommendationQualityCheck]:
    checks = [
        _data_check(card),
        _liquidity_check(card),
        _tradability_check(card),
        _execution_check(card),
        _overextension_check(card),
        _volatility_check(card),
        _strategy_history_check(card),
        _risk_reward_check(card),
        _a_share_factor_balance_check(card),
    ]
    return checks


def _data_check(card: OpportunityCard) -> RecommendationQualityCheck:
    missing = len(card.data_caveats)
    if "insufficient_history" in card.factor_flags:
        return _check(
            "data_history",
            "warn",
            "历史数据不足",
            "价格历史不足，均线和回测稳定性需要打折。",
            -0.08,
        )
    if missing >= 3:
        return _check(
            "data_caveats",
            "warn",
            "数据 caveat 较多",
            "行情或策略输入有多项缺口，推荐分需要折扣。",
            -0.06,
        )
    return _check("data_ready", "pass", "数据可用", "基础行情和策略输入可用于排序。", 0.04)


def _liquidity_check(card: OpportunityCard) -> RecommendationQualityCheck:
    if "low_liquidity" in card.factor_flags:
        return _check(
            "low_liquidity",
            "block",
            "流动性不足",
            "成交活跃度过低，容易买不进、卖不出或滑点过大。",
            -0.24,
        )
    if card.tradability is not None and card.tradability.score < 0.45:
        return _check(
            "weak_tradability",
            "block",
            "可交易性不足",
            card.tradability.summary,
            -0.20,
        )
    if card.tradability is not None and card.tradability.score < 0.65:
        return _check("thin_liquidity", "warn", "流动性一般", card.tradability.summary, -0.08)
    return _check("liquidity_ready", "pass", "流动性可接受", "成交活跃度可支持小仓验证。", 0.06)


def _tradability_check(card: OpportunityCard) -> RecommendationQualityCheck:
    if card.trading_status is not None and not card.trading_status.can_buy:
        return _check(
            f"trading_status_{card.trading_status.status}",
            "block" if not card.trading_status.can_sell else "warn",
            card.trading_status.label,
            " ".join(card.trading_status.notes),
            -0.18,
        )
    if card.tradability is not None and not card.tradability.can_open:
        return _check(
            f"tradability_{card.tradability.status}",
            "block",
            card.tradability.label,
            card.tradability.summary,
            -0.20,
        )
    return _check("can_open", "pass", "交易约束通过", "当前没有发现硬性不可买约束。", 0.05)


def _execution_check(card: OpportunityCard) -> RecommendationQualityCheck:
    missing = [
        name
        for name, value in {
            "触发价": card.entry_plan.trigger_price,
            "止损": card.exit_plan.initial_stop,
            "目标一": card.exit_plan.target_1,
            "禁追价": card.entry_plan.no_chase_above,
        }.items()
        if value is None
    ]
    if missing:
        return _check(
            "incomplete_trade_plan",
            "block",
            "买卖计划不完整",
            f"缺少 {'、'.join(missing)}，不能作为可买推荐。",
            -0.22,
        )
    return _check("trade_plan_ready", "pass", "买卖计划完整", "触发、止损、目标和禁追价都已生成。", 0.06)


def _overextension_check(card: OpportunityCard) -> RecommendationQualityCheck:
    if "overextended" in card.factor_flags or card.scenario.no_chase_pct < 2:
        status = "block" if card.scenario.no_chase_pct < 1 else "warn"
        return _check(
            "overextended",
            status,
            "不追高",
            "价格距离禁追位太近或已经偏离短期均线，等待回踩比追买更合适。",
            -0.16 if status == "warn" else -0.24,
        )
    return _check("not_overextended", "pass", "未明显追高", "价格还没有严重偏离短期支撑。", 0.04)


def _volatility_check(card: OpportunityCard) -> RecommendationQualityCheck:
    if "high_volatility" in card.factor_flags:
        return _check(
            "high_volatility",
            "warn",
            "波动偏高",
            "近期波动较大，止损容易被噪音触发，仓位要下调。",
            -0.10,
        )
    return _check("volatility_ok", "pass", "波动可控", "短期波动没有触发风险降权。", 0.04)


def _strategy_history_check(card: OpportunityCard) -> RecommendationQualityCheck:
    calibration = card.strategy_calibration
    if calibration is None or calibration.sample_count <= 0:
        return _check(
            "strategy_missing_history",
            "warn",
            "策略样本不足",
            "没有足够历史样本，推荐只能按观察信号处理。",
            -0.08,
        )
    if calibration.sample_count < 10:
        return _check(
            "strategy_limited_sample",
            "warn",
            "策略样本偏少",
            "历史样本不足 10 个，不能重仓依赖这个信号。",
            -0.06,
        )
    if (calibration.win_rate_10d or 0) < 45 or (calibration.avg_return_10d or 0) < -1:
        return _check(
            "strategy_recent_weak",
            "block",
            "策略近期偏弱",
            "类似信号近期胜率或均值收益偏弱，先从可买池剔除。",
            -0.22,
        )
    if (calibration.win_rate_10d or 0) >= 55 and (calibration.avg_return_10d or 0) > 0:
        return _check(
            "strategy_validated",
            "pass",
            "策略验证支持",
            "类似信号历史胜率和均值收益支持当前推荐。",
            0.08,
        )
    return _check("strategy_neutral", "warn", "策略表现中性", "历史表现没有明显优势，降低排序权重。", -0.04)


def _risk_reward_check(card: OpportunityCard) -> RecommendationQualityCheck:
    if card.risk_reward is None:
        return _check("risk_reward_missing", "block", "缺少盈亏比", "无法判断收益空间是否覆盖风险。", -0.18)
    if card.risk_reward < 1.3:
        return _check("risk_reward_poor", "block", "盈亏比不足", "目标收益相对止损空间不足。", -0.22)
    if card.risk_reward < 1.8:
        return _check("risk_reward_thin", "warn", "盈亏比一般", "收益空间偏薄，只适合观察或低仓位。", -0.08)
    return _check("risk_reward_ok", "pass", "盈亏比可接受", "目标和止损之间有基本收益空间。", 0.06)


def _a_share_factor_balance_check(card: OpportunityCard) -> RecommendationQualityCheck:
    if card.market.value != "CN":
        return _check("factor_balance", "pass", "因子结构可用", "非 A 股按通用因子结构处理。", 0.03)
    momentum_heavy = card.factor_score >= 0.75 and card.factor_percentile >= 0.70
    defensive_weak = _score_attr(card, "low_risk_score") < 0.45
    liquidity_weak = _score_attr(card, "liquidity_score") < 0.45
    if momentum_heavy and (defensive_weak or liquidity_weak):
        return _check(
            "a_share_momentum_unbalanced",
            "warn",
            "A股因子不均衡",
            "A 股不能只看动量，低风险或流动性不足会降低推荐可信度。",
            -0.12,
        )
    return _check(
        "a_share_factor_balanced",
        "pass",
        "A股因子结构均衡",
        "趋势、低风险和流动性没有明显短板。",
        0.06,
    )


def _quality_score(
    card: OpportunityCard,
    checks: list[RecommendationQualityCheck],
) -> float:
    if card.market.value == "CN":
        base = (
            _score_attr(card, "momentum_score") * 0.14
            + _score_attr(card, "trend_quality_score") * 0.20
            + _score_attr(card, "low_risk_score") * 0.26
            + _score_attr(card, "liquidity_score") * 0.16
            + _strategy_score(card) * 0.12
            + _risk_reward_score(card) * 0.08
            + _market_context_score(card) * 0.04
        )
    else:
        base = (
            _score_attr(card, "momentum_score") * 0.22
            + _score_attr(card, "trend_quality_score") * 0.22
            + _score_attr(card, "low_risk_score") * 0.18
            + _score_attr(card, "liquidity_score") * 0.14
            + _strategy_score(card) * 0.14
            + _risk_reward_score(card) * 0.07
            + _market_context_score(card) * 0.03
        )
    score = base + sum(check.score_impact for check in checks)
    return _clamp(score)


def _score_components(
    card: OpportunityCard,
    profile: RecommendationQualityProfile,
) -> list[RecommendationScoreComponent]:
    weights = _quality_weights(card)
    specs = [
        ("factor_momentum", "动量因子", _score_attr(card, "momentum_score"), weights["momentum"], "趋势弹性和相对强度。"),
        ("trend_quality", "趋势质量", _score_attr(card, "trend_quality_score"), weights["trend"], "均线结构和突破质量。"),
        ("low_risk", "低风险因子", _score_attr(card, "low_risk_score"), weights["low_risk"], "回撤、波动和防守质量。"),
        ("liquidity", "流动性", _score_attr(card, "liquidity_score"), weights["liquidity"], "成交活跃度和可交易性。"),
        ("strategy_validation", "策略验证", _strategy_score(card), weights["strategy"], "类似信号历史胜率、均值收益和样本数。"),
        ("risk_reward", "盈亏比", _risk_reward_score(card), weights["risk_reward"], "目标收益相对止损空间。"),
        ("market_context", "市场主题", _market_context_score(card), weights["market"], "行业、主题和指数成分支持。"),
    ]
    components = [
        _score_component(key, label, score, weight, detail)
        for key, label, score, weight, detail in specs
    ]
    penalty = sum(max(0.0, -check.score_impact) for check in profile.checks)
    components.append(
        RecommendationScoreComponent(
            key="quality_penalties",
            label="质量扣分",
            score=round(_clamp(1 - penalty), 4),
            weight=0.0,
            contribution=0.0,
            status="risk" if penalty >= 0.2 else "warning" if penalty > 0 else "pass",
            detail=f"质量门扣分 {penalty:.0%}，硬阻断 {profile.block_count}，警告 {profile.warn_count}。",
        )
    )
    return components


def _quality_weights(card: OpportunityCard) -> dict[str, float]:
    if card.market.value == "CN":
        return {
            "momentum": 0.14,
            "trend": 0.20,
            "low_risk": 0.26,
            "liquidity": 0.16,
            "strategy": 0.12,
            "risk_reward": 0.08,
            "market": 0.04,
        }
    return {
        "momentum": 0.22,
        "trend": 0.22,
        "low_risk": 0.18,
        "liquidity": 0.14,
        "strategy": 0.14,
        "risk_reward": 0.07,
        "market": 0.03,
    }


def _score_component(
    key: str,
    label: str,
    score: float,
    weight: float,
    detail: str,
) -> RecommendationScoreComponent:
    return RecommendationScoreComponent(
        key=key,
        label=label,
        score=round(_clamp(score), 4),
        weight=round(weight, 4),
        contribution=round(_clamp(score) * weight, 4),
        status=_component_status(score),
        detail=detail,
    )


def _component_status(score: float) -> str:
    if score >= 0.72:
        return "pass"
    if score >= 0.52:
        return "warning"
    return "risk"


def _pre_trade_checks(
    card: OpportunityCard,
    profile: RecommendationQualityProfile,
) -> list[PreTradeRiskCheck]:
    checks: list[PreTradeRiskCheck] = []
    for check in profile.checks:
        if check.status not in {"warn", "block"}:
            continue
        checks.append(
            PreTradeRiskCheck(
                code=check.code,
                severity="block" if check.status == "block" else "warning",
                title=check.label,
                message=check.detail,
                action="先排除该风险后再考虑买入。" if check.status == "block" else "确认风险可接受后只做低仓验证。",
            )
        )
    if card.decision is not None:
        for veto in card.decision.risk_vetoes:
            checks.append(
                PreTradeRiskCheck(
                    code=veto.code,
                    severity=veto.severity,
                    title=veto.title,
                    message=veto.message,
                    action="触发硬风控，暂不买入。" if veto.severity == "block" else "只观察或等待更干净买点。",
                )
            )
    if card.trading_constraints is not None:
        for constraint in card.trading_constraints.constraints:
            if constraint.severity == "info":
                continue
            checks.append(
                PreTradeRiskCheck(
                    code=constraint.code,
                    severity=constraint.severity,
                    title=constraint.title,
                    message=constraint.message,
                    action="先确认权限/交易规则，再考虑下单。",
                )
            )
    return _dedupe_pre_trade_checks(checks)


def _dedupe_pre_trade_checks(checks: list[PreTradeRiskCheck]) -> list[PreTradeRiskCheck]:
    seen: set[str] = set()
    deduped: list[PreTradeRiskCheck] = []
    severity_rank = {"block": 0, "warning": 1, "risk": 2, "info": 3}
    for check in sorted(checks, key=lambda item: severity_rank.get(item.severity, 4)):
        if check.code in seen:
            continue
        seen.add(check.code)
        deduped.append(check)
    return deduped


def _pre_trade_next_action(status: str, checks: list[PreTradeRiskCheck]) -> str:
    if status == "blocked":
        return "不要买入；先处理硬阻断，或换到无硬风险的候选。"
    if any("permission" in check.code for check in checks):
        return "先确认交易权限，再按触发价和止损做小仓验证。"
    if status == "warning":
        return "等待触发价确认，只用小仓验证，不追高。"
    return "只在触发价确认后按计划小仓买入，并同步设置止损提醒。"


def _pre_trade_summary(
    status: str,
    checks: list[PreTradeRiskCheck],
    risk_budget: float,
    max_position: float,
) -> str:
    block_count = sum(1 for check in checks if check.severity == "block")
    warning_count = sum(1 for check in checks if check.severity == "warning")
    if status == "blocked":
        return f"买前风控：{block_count} 个硬阻断，当前不进入买入候选。"
    if status == "warning":
        return f"买前风控：{warning_count} 个待确认项，建议风险预算 {risk_budget:.2f}%，仓位上限 {max_position:.2f}%。"
    return f"买前风控：未见硬阻断，建议风险预算 {risk_budget:.2f}%，仓位上限 {max_position:.2f}%。"


def _pct_change(entry: Decimal | None, target: Decimal | None) -> float | None:
    if entry is None or target is None or entry <= 0:
        return None
    return round(float((target - entry) / entry * Decimal("100")), 4)


def _account_impact(position_pct: float, price_move_pct: float | None) -> float:
    if price_move_pct is None:
        return 0.0
    return round(abs(position_pct * price_move_pct / 100), 4)


def _shares_for_budget(
    position_value: Decimal | None,
    entry: Decimal | None,
    min_lot: int | None,
) -> int | None:
    if position_value is None or entry is None or entry <= 0:
        return None
    raw_shares = int((position_value / entry).to_integral_value(rounding=ROUND_FLOOR))
    if min_lot and min_lot > 0:
        return raw_shares // min_lot * min_lot
    return raw_shares


def _position_summary(
    position_pct: float,
    account_drawdown: float,
    account_gain_1: float,
    min_lot_cash: Decimal | None,
    shares: int | None,
) -> str:
    lot_text = f"，一手约 {min_lot_cash} 元" if min_lot_cash is not None else ""
    share_text = f"，约 {shares} 股" if shares else "，按风控预算可能不足一手"
    return (
        f"以10万元账户测算：仓位上限 {position_pct:.2f}%{share_text}{lot_text}；"
        f"若触发止损约回撤 {account_drawdown:.2f}%，到目标一约贡献 {account_gain_1:.2f}%。"
    )


def _money(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Decimal("0.01"))


def _adjusted_rank_score(card: OpportunityCard, profile: RecommendationQualityProfile) -> float:
    score = card.rank_score * 0.55 + profile.score * 0.45
    score -= profile.warn_count * 0.025
    if profile.block_count:
        score = min(score, 0.42)
    return round(_clamp(score), 4)


def _tier(score: float, warn_count: int, block_count: int) -> str:
    if block_count:
        return "risk_filtered"
    if score >= 0.76 and warn_count == 0:
        return "high_quality"
    if score >= 0.68:
        return "quality_candidate"
    if score >= 0.56:
        return "watchlist"
    return "low_quality"


def _summary(tier: str, score: float, warn_count: int, block_count: int) -> str:
    labels = {
        "high_quality": "高质量候选",
        "quality_candidate": "质量候选",
        "watchlist": "观察池",
        "low_quality": "低质量信号",
        "risk_filtered": "风险过滤",
    }
    if block_count:
        return f"{labels[tier]}：{block_count} 个硬阻断，暂不进入买入候选。"
    if warn_count:
        return f"{labels[tier]}：质量分 {score:.0%}，有 {warn_count} 个降权项。"
    return f"{labels[tier]}：质量分 {score:.0%}，通过主要质量检查。"


def _apply_blocking_decision(
    card: OpportunityCard,
    profile: RecommendationQualityProfile,
) -> None:
    if profile.block_count <= 0 or card.decision is None:
        return
    card.decision.action = "avoid"
    card.decision.action_label = "Avoid for now"
    card.decision.risk_status = "blocked"
    existing = {veto.code for veto in card.decision.risk_vetoes}
    for check in profile.checks:
        if check.status != "block" or check.code in existing:
            continue
        card.decision.risk_vetoes.append(
            RiskVeto(
                code=check.code,
                severity="block",
                title=check.label,
                message=check.detail,
            )
        )


def _append_quality_notes(card: OpportunityCard, profile: RecommendationQualityProfile) -> None:
    note = f"推荐质量：{profile.summary}"
    if note not in card.rank_reasons:
        card.rank_reasons.append(note)
    top_checks = [
        f"{check.label}: {check.detail}"
        for check in profile.checks
        if check.status in {"warn", "block"}
    ][:3]
    for detail in top_checks:
        text = f"质量扣分：{detail}"
        if text not in card.calibration_notes:
            card.calibration_notes.append(text)


def _score_attr(card: OpportunityCard, attr: str) -> float:
    for exposure in card.factor_exposures:
        if exposure.factor_id == attr.replace("_score", ""):
            return exposure.score
    return float(getattr(card, attr, 0.5) or 0.5)


def _strategy_score(card: OpportunityCard) -> float:
    calibration = card.strategy_calibration
    if calibration is None or calibration.sample_count <= 0:
        return 0.42
    win = (calibration.win_rate_10d or 50.0) / 100
    avg = 0.5 + (calibration.avg_return_10d or 0) / 20
    sample = min(1.0, calibration.sample_count / 30)
    return _clamp(win * 0.45 + avg * 0.25 + sample * 0.30)


def _risk_reward_score(card: OpportunityCard) -> float:
    if card.risk_reward is None:
        return 0.0
    return _clamp(card.risk_reward / 3)


def _market_context_score(card: OpportunityCard) -> float:
    if card.market_context is None:
        return 0.45
    theme_bonus = min(0.2, len(card.market_context.themes) * 0.04)
    index_bonus = min(0.1, len(card.market_context.index_memberships) * 0.025)
    return _clamp(0.58 + theme_bonus + index_bonus)


def _check(
    code: str,
    status: str,
    label: str,
    detail: str,
    score_impact: float,
) -> RecommendationQualityCheck:
    return RecommendationQualityCheck(
        code=code,
        status=status,
        label=label,
        detail=detail,
        score_impact=score_impact,
    )


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
