from qagent.domain.models import OpportunityCard, PortfolioAllocation, PortfolioPlan


def build_portfolio_plan(
    cards: list[OpportunityCard],
    max_positions: int = 3,
    max_industry_positions: int = 2,
    total_risk_budget_pct: float = 3.0,
) -> PortfolioPlan:
    ranked = sorted(cards, key=lambda card: (card.rank_score, card.factor_score), reverse=True)
    allocations: list[PortfolioAllocation] = []
    watchlist: list[PortfolioAllocation] = []
    industry_counts: dict[str, int] = {}

    for card in ranked:
        allocation = _allocation_from_card(card)
        if _is_eligible(card) and len(allocations) < max_positions:
            industry = allocation.industry or "未分类"
            if industry_counts.get(industry, 0) < max_industry_positions:
                industry_counts[industry] = industry_counts.get(industry, 0) + 1
                allocations.append(allocation)
                continue
        watchlist.append(allocation)

    eligible_count = sum(1 for card in ranked if _is_eligible(card))
    allocated_weight = round(sum(item.weight_pct for item in allocations), 2)
    blocked_count = sum(1 for card in ranked if card.tradability and not card.tradability.can_open)
    return PortfolioPlan(
        max_positions=max_positions,
        total_risk_budget_pct=total_risk_budget_pct,
        allocated_weight_pct=allocated_weight,
        eligible_count=eligible_count,
        blocked_count=blocked_count,
        allocations=allocations,
        watchlist=watchlist[:8],
        rules=[
            f"最多同时新开 {max_positions} 只",
            f"单行业最多 {max_industry_positions} 只",
            "不可交易、接近涨停、ST/退市风险和低流动性默认不新开仓",
            "单笔风险预算来自机会卡 suggested_risk_pct",
        ],
        summary=_summary(allocations, eligible_count, blocked_count),
    )


def _is_eligible(card: OpportunityCard) -> bool:
    decision = card.decision
    if decision is None or decision.action == "avoid" or decision.risk_status == "blocked":
        return False
    if card.tradability is not None and not card.tradability.can_open:
        return False
    return True


def _allocation_from_card(card: OpportunityCard) -> PortfolioAllocation:
    decision = card.decision
    action = decision.action if decision else "watch_trigger"
    risk_budget = decision.suggested_risk_pct if decision else 0.0
    max_position = decision.max_position_pct if decision else 0.0
    if not _is_eligible(card):
        weight = 0.0
    else:
        weight = round(min(max_position, 100 / 3, 12.0), 2)
    industry = card.market_context.industry if card.market_context else None
    rationale = _rationale(card)
    return PortfolioAllocation(
        instrument_id=card.instrument_id,
        instrument_label=card.instrument_label,
        action=action,
        weight_pct=weight,
        risk_budget_pct=risk_budget,
        max_position_pct=max_position,
        industry=industry,
        rationale=rationale,
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
    allocations: list[PortfolioAllocation],
    eligible_count: int,
    blocked_count: int,
) -> str:
    if not allocations:
        return f"当前没有进入组合的新开仓标的；可候选 {eligible_count} 只，交易过滤阻断 {blocked_count} 只。"
    names = "、".join(item.instrument_label or item.instrument_id for item in allocations)
    return f"当前组合计划优先 {len(allocations)} 只：{names}；交易过滤阻断 {blocked_count} 只。"
