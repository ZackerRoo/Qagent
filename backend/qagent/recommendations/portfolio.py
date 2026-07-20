from __future__ import annotations

from qagent.domain.models import OpportunityCard
from qagent.portfolio.constraints import (
    PortfolioCandidate,
    PortfolioConstraintCode,
    PortfolioConstraintConfig,
    PortfolioConstraintEngine,
)
from qagent.recommendations.models import (
    ConstrainedPortfolioAllocation,
    ConstrainedPortfolioPlan,
    PortfolioConstraintResult,
)


def build_portfolio_plan(
    cards: list[OpportunityCard],
    max_positions: int = 3,
    max_industry_positions: int | None = 2,
    total_risk_budget_pct: float = 3.0,
    *,
    max_single_position_pct: float = 12.0,
    min_cash_reserve_pct: float = 50.0,
    max_industry_weight_pct: float | None = 24.0,
    max_same_theme_positions: int | None = 2,
    max_theme_weight_pct: float | None = 24.0,
    max_etf_overlap_positions: int | None = 1,
    max_etf_overlap_weight_pct: float | None = 12.0,
    market_state: str = "neutral",
    market_regime: str | None = None,
    market_state_multiplier: float | None = None,
    risk_budget_multiplier: float | None = None,
) -> ConstrainedPortfolioPlan:
    resolved_market_state = market_regime or market_state
    resolved_multiplier = (
        market_state_multiplier if market_state_multiplier is not None else risk_budget_multiplier
    )
    config = PortfolioConstraintConfig(
        max_positions=max_positions,
        max_single_position_pct=max_single_position_pct,
        total_risk_budget_pct=total_risk_budget_pct,
        min_cash_reserve_pct=min_cash_reserve_pct,
        max_industry_positions=max_industry_positions,
        max_industry_weight_pct=max_industry_weight_pct,
        max_same_theme_positions=max_same_theme_positions,
        max_theme_weight_pct=max_theme_weight_pct,
        max_etf_overlap_positions=max_etf_overlap_positions,
        max_etf_overlap_weight_pct=max_etf_overlap_weight_pct,
    )
    engine = PortfolioConstraintEngine(config)
    candidates = [_candidate_from_card(card) for card in cards]
    results = engine.evaluate(
        candidates,
        market_state=resolved_market_state,
        market_state_multiplier=resolved_multiplier,
    )
    allocations: list[ConstrainedPortfolioAllocation] = []
    watchlist: list[ConstrainedPortfolioAllocation] = []
    for result in results:
        allocation = _allocation_from_result(cards[result.candidate_index], result)
        if result.accepted:
            allocations.append(allocation)
        else:
            watchlist.append(allocation)

    eligible_count = sum(1 for card in cards if _is_eligible(card))
    allocated_weight = round(sum(item.weight_pct for item in allocations), 2)
    allocated_risk = round(sum(item.risk_budget_pct for item in allocations), 2)
    blocked_count = sum(1 for card in cards if card.tradability and not card.tradability.can_open)
    constraint_blocked_count = sum(1 for result in results if not result.accepted)
    return ConstrainedPortfolioPlan(
        max_positions=max_positions,
        total_risk_budget_pct=total_risk_budget_pct,
        allocated_weight_pct=allocated_weight,
        eligible_count=eligible_count,
        blocked_count=blocked_count,
        allocations=allocations,
        watchlist=watchlist[:8],
        rules=_rules(config, resolved_market_state),
        summary=_summary(allocations, eligible_count, constraint_blocked_count),
        allocated_risk_budget_pct=allocated_risk,
        cash_reserve_pct=round(max(0.0, 100.0 - allocated_weight), 2),
        constraint_blocked_count=constraint_blocked_count,
        constraint_policy=engine.policy_audit(
            market_state=resolved_market_state,
            market_state_multiplier=resolved_multiplier,
        ),
        constraint_results=results,
    )


def _is_eligible(card: OpportunityCard) -> bool:
    decision = card.decision
    if decision is None or decision.action == "avoid" or decision.risk_status == "blocked":
        return False
    if card.tradability is not None and not card.tradability.can_open:
        return False
    return True


def _candidate_from_card(card: OpportunityCard) -> PortfolioCandidate:
    decision = card.decision
    action = decision.action if decision else "watch_trigger"
    risk_budget = decision.suggested_risk_pct if decision else 0.0
    max_position = decision.max_position_pct if decision else 0.0
    hard_codes: list[str] = []
    if decision is not None and decision.risk_status == "blocked":
        hard_codes.append(PortfolioConstraintCode.RISK_BLOCKED.value)
    if card.tradability is not None and not card.tradability.can_open:
        hard_codes.append(PortfolioConstraintCode.TRADABILITY_BLOCKED.value)
    if card.trading_status is not None and not card.trading_status.can_buy:
        hard_codes.append(PortfolioConstraintCode.TRADING_STATUS_BLOCKED.value)
    if card.pre_trade_risk is not None and not card.pre_trade_risk.can_buy:
        hard_codes.append(PortfolioConstraintCode.PRE_TRADE_RISK_BLOCKED.value)
    if card.data_quality_audit is not None and not card.data_quality_audit.can_recommend:
        hard_codes.append(PortfolioConstraintCode.DATA_QUALITY_BLOCKED.value)
    context = card.market_context
    themes = tuple(context.themes) if context else ()
    overlap_keys = tuple((*themes, *(context.index_memberships if context else [])))
    return PortfolioCandidate(
        candidate_id=card.card_id,
        instrument_id=card.instrument_id,
        action=action,
        requested_weight=max_position,
        requested_risk_budget=risk_budget,
        max_position_pct=max_position,
        industry=context.industry if context else None,
        themes=themes,
        asset_type=card.asset_type,
        etf_overlap_keys=overlap_keys,
        hard_constraint_codes=tuple(dict.fromkeys(hard_codes)),
        priority=card.rank_score,
        secondary_priority=card.factor_score,
    )


def _allocation_from_result(
    card: OpportunityCard,
    result: PortfolioConstraintResult,
) -> ConstrainedPortfolioAllocation:
    decision = card.decision
    max_position = decision.max_position_pct if decision else 0.0
    industry = card.market_context.industry if card.market_context else None
    return ConstrainedPortfolioAllocation(
        instrument_id=card.instrument_id,
        instrument_label=card.instrument_label,
        action=result.action,
        weight_pct=result.target_weight,
        risk_budget_pct=result.risk_budget,
        max_position_pct=max_position,
        industry=industry,
        rationale=_rationale(card),
        accepted=result.accepted,
        target_weight=result.target_weight,
        risk_budget=result.risk_budget,
        constraint_codes=result.constraint_codes,
    )


def _rationale(card: OpportunityCard) -> str:
    parts = []
    if card.recommendation_summary:
        parts.append(card.recommendation_summary.stance)
    if card.tradability:
        parts.append(card.tradability.label)
    if card.market_context:
        parts.append(card.market_context.industry)
    parts.append(f"排序分 {round(card.rank_score * 100)}")
    return "；".join(parts)


def _summary(
    allocations: list[ConstrainedPortfolioAllocation],
    eligible_count: int,
    blocked_count: int,
) -> str:
    if not allocations:
        return f"当前没有进入组合的新开仓标的；可跟踪候选 {eligible_count} 只，约束未接纳 {blocked_count} 只。"
    names = "、".join(item.instrument_label or item.instrument_id for item in allocations)
    return f"当前组合计划优先 {len(allocations)} 只：{names}；约束未接纳 {blocked_count} 只。"


def _rules(config: PortfolioConstraintConfig, market_state: str) -> list[str]:
    return [
        "仅 candidate_entry 动作可进入组合，观察动作权重固定为 0",
        f"最多同时新开 {config.max_positions} 只，单标的不超过 {config.max_single_position_pct:.2f}%",
        f"总风险预算不超过 {config.total_risk_budget_pct:.2f}%",
        f"现金至少保留 {config.min_cash_reserve_pct:.2f}%",
        f"单行业最多 {config.max_industry_positions} 只，并执行行业/主题权重上限",
        "不可交易、接近涨停、ST/退市风险和低流动性默认不新开仓",
        "重叠 ETF 与同主题持仓执行独立集中度约束",
        f"市场状态 {market_state or 'neutral'} 通过确定性乘数调整仓位和风险预算",
    ]
