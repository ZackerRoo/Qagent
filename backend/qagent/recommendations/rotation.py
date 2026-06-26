from qagent.domain.models import OpportunityCard


SPECIAL_THEMES = {
    "AI算力供应链",
    "半导体",
    "存储芯片",
    "HBM",
    "国产替代",
    "硬科技",
    "科创板",
    "成长股",
}

BUCKET_LABELS = {
    "today_action": "今日可行动",
    "etf_index": "ETF/指数工具",
    "theme_growth": "主题成长",
    "wait_pullback": "等待回踩",
    "stock_momentum": "趋势候选",
    "risk_filtered": "风险过滤",
}


def classify_opportunity(card: OpportunityCard) -> OpportunityCard:
    card.asset_type = _asset_type(card)
    card.opportunity_bucket = _bucket(card)
    card.opportunity_tags = _tags(card)
    card.rotation_note = _rotation_note(card)
    return card


def sort_recommendation_cards(cards: list[OpportunityCard | None]) -> list[OpportunityCard]:
    remaining = [classify_opportunity(card) for card in cards if card is not None]
    ordered: list[OpportunityCard] = []
    while remaining:
        selected = max(remaining, key=lambda card: _adjusted_priority(card, ordered))
        selected.rotation_note = _rotation_note(selected, ordered)
        ordered.append(selected)
        remaining.remove(selected)
    return ordered


def _asset_type(card: OpportunityCard) -> str:
    if card.market.value == "CN":
        symbol = card.instrument_id.split(":", 1)[1]
        board = card.trading_constraints.board if card.trading_constraints else ""
        if board == "ETF" or symbol.startswith(("15", "16", "51", "52", "56", "58")):
            return "ETF"
    return "stock"


def _bucket(card: OpportunityCard) -> str:
    action = card.decision.action if card.decision else ""
    risk_status = card.decision.risk_status if card.decision else "clear"
    if risk_status == "blocked" or action == "avoid":
        return "risk_filtered"
    if _asset_type(card) == "ETF":
        return "etf_index"
    themes = set(card.market_context.themes if card.market_context else [])
    industry = card.market_context.industry if card.market_context else ""
    if themes.intersection(SPECIAL_THEMES) or industry in SPECIAL_THEMES:
        return "theme_growth"
    if action == "candidate_entry":
        return "today_action"
    if action == "wait_pullback":
        return "wait_pullback"
    return "stock_momentum"


def _tags(card: OpportunityCard) -> list[str]:
    tags: list[str] = [BUCKET_LABELS.get(card.opportunity_bucket, "候选")]
    if card.asset_type == "ETF":
        tags.append("ETF")
    if card.market_context:
        tags.append(card.market_context.industry)
        tags.extend(card.market_context.themes[:3])
        tags.extend(card.market_context.index_memberships[:2])
    if card.primary_strategy_id == "factor_rotation_watch":
        tags.append("因子观察")
    return _dedupe(tags)


def _adjusted_priority(card: OpportunityCard, selected: list[OpportunityCard]) -> float:
    conviction = card.decision.conviction_score if card.decision else 0.0
    base = card.rank_score * 0.45 + card.factor_score * 0.35 + conviction * 0.2
    bucket_boost = {
        "today_action": 0.08,
        "etf_index": 0.1,
        "theme_growth": 0.09,
        "wait_pullback": 0.04,
        "stock_momentum": 0.03,
        "risk_filtered": -0.22,
    }.get(card.opportunity_bucket, 0.0)
    coverage_boost = 0.0
    selected_buckets = {item.opportunity_bucket for item in selected}
    if card.opportunity_bucket in {"etf_index", "theme_growth"} and card.opportunity_bucket not in selected_buckets:
        coverage_boost += 0.18
    bucket_penalty = sum(
        1 for item in selected if item.opportunity_bucket == card.opportunity_bucket
    ) * 0.06
    industry = card.market_context.industry if card.market_context else ""
    industry_penalty = sum(
        1
        for item in selected
        if industry and item.market_context and item.market_context.industry == industry
    ) * 0.05
    return round(base + bucket_boost + coverage_boost - bucket_penalty - industry_penalty, 6)


def _rotation_note(card: OpportunityCard, selected: list[OpportunityCard] | None = None) -> str:
    label = BUCKET_LABELS.get(card.opportunity_bucket, "候选")
    parts = [label]
    if card.primary_strategy_id == "factor_rotation_watch":
        parts.append("因子排名靠前但仍需等待买点触发")
    if selected and card.opportunity_bucket in {"etf_index", "theme_growth"}:
        parts.append("用于避免推荐列表只集中在少数高分个股")
    if card.market_context:
        parts.append(card.market_context.summary)
    return "；".join(_dedupe(parts))


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result
