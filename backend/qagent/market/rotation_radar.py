from collections import defaultdict
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.domain.models import OpportunityCard, SectorStrength
from qagent.recommendations.rotation import BUCKET_LABELS


ACTIONABLE_ACTIONS = {"candidate_entry", "watch_trigger", "hold_add"}
WATCH_ACTIONS = {"wait_pullback", "watch_trigger"}

THEME_PRIORITY = {
    "存储芯片": 0.09,
    "半导体": 0.08,
    "AI算力供应链": 0.075,
    "科创板": 0.07,
    "硬科技": 0.06,
    "ETF/指数工具": 0.055,
    "HBM": 0.05,
    "国产替代": 0.04,
    "成长股": 0.035,
}

CATEGORY_PRIORITY = {
    "theme": 4,
    "industry": 3,
    "etf": 2,
    "index": 1,
}


class RotationThemeLeader(BaseModel):
    instrument_id: str
    instrument_label: str | None = None
    score: float
    action: str
    action_label: str
    risk_status: str
    bucket: str
    trigger_price: str | None = None


class RotationTheme(BaseModel):
    name: str
    category: str
    score: float = Field(ge=0.0, le=1.0)
    momentum_score: float = Field(ge=0.0, le=1.0)
    breadth_score: float = Field(ge=0.0, le=1.0)
    opportunity_count: int
    actionable_count: int
    blocked_count: int
    etf_count: int
    leaders: list[RotationThemeLeader] = Field(default_factory=list)
    stance: str
    summary: str
    tags: list[str] = Field(default_factory=list)


class MarketRotationRadar(BaseModel):
    as_of: str
    themes: list[RotationTheme]
    data_health: dict[str, str] = Field(default_factory=dict)


def build_rotation_radar(
    cards: list[OpportunityCard],
    sector_strength: list[SectorStrength] | None = None,
    limit: int = 10,
) -> MarketRotationRadar:
    cn_cards = [card for card in cards if card.market.value == "CN"]
    grouped: dict[str, _RotationBucket] = {}
    for card in cn_cards:
        for name, category in _rotation_keys(card):
            bucket = grouped.setdefault(name, _RotationBucket(name=name, category=category))
            bucket.add(card, category)

    sector_scores = {item.industry: item.score for item in sector_strength or []}
    themes = [
        bucket.to_theme(sector_scores.get(bucket.name))
        for bucket in grouped.values()
        if bucket.opportunity_count > 0
    ]
    themes = sorted(
        themes,
        key=lambda item: (
            item.score,
            THEME_PRIORITY.get(item.name, 0.0),
            item.actionable_count,
            -item.blocked_count,
            item.opportunity_count,
        ),
        reverse=True,
    )[:limit]

    return MarketRotationRadar(
        as_of=date.today().isoformat(),
        themes=themes,
        data_health={
            "rotation_cards": str(len(cn_cards)),
            "rotation_themes": str(len(grouped)),
        },
    )


class _RotationBucket:
    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.cards: list[OpportunityCard] = []
        self.categories: set[str] = {category}

    @property
    def opportunity_count(self) -> int:
        return len(self.cards)

    def add(self, card: OpportunityCard, category: str) -> None:
        if card.instrument_id in {item.instrument_id for item in self.cards}:
            self.categories.add(category)
            self.category = _best_category(self.categories)
            return
        self.cards.append(card)
        self.categories.add(category)
        self.category = _best_category(self.categories)

    def to_theme(self, sector_score: float | None) -> RotationTheme:
        qualities = [_card_quality(card) for card in self.cards]
        momentum_score = _clamp(sum(qualities) / len(qualities)) if qualities else 0.0
        actionable_count = sum(1 for card in self.cards if _is_actionable(card))
        watch_count = sum(1 for card in self.cards if _is_watchable(card))
        blocked_count = sum(1 for card in self.cards if _is_blocked(card))
        etf_count = sum(1 for card in self.cards if card.asset_type == "ETF")
        breadth_score = _clamp(
            min(0.55, len(self.cards) * 0.11)
            + min(0.22, actionable_count * 0.08)
            + min(0.12, etf_count * 0.06)
            + min(0.11, watch_count * 0.035)
        )
        actionable_ratio = actionable_count / len(self.cards) if self.cards else 0.0
        blocked_ratio = blocked_count / len(self.cards) if self.cards else 0.0
        sector_component = (sector_score or momentum_score) * 0.12
        score = _clamp(
            momentum_score * 0.58
            + breadth_score * 0.18
            + actionable_ratio * 0.12
            + sector_component
            + THEME_PRIORITY.get(self.name, 0.0)
            - blocked_ratio * 0.34
        )
        leaders = [_leader(card) for card in sorted(self.cards, key=_card_quality, reverse=True)[:3]]
        return RotationTheme(
            name=self.name,
            category=self.category,
            score=round(score, 4),
            momentum_score=round(momentum_score, 4),
            breadth_score=round(breadth_score, 4),
            opportunity_count=len(self.cards),
            actionable_count=actionable_count,
            blocked_count=blocked_count,
            etf_count=etf_count,
            leaders=leaders,
            stance=_stance(score, actionable_count, blocked_count, len(self.cards)),
            summary=_summary(self.name, len(self.cards), actionable_count, blocked_count, leaders),
            tags=_tags(self.name, self.category, etf_count, blocked_count),
        )


def _rotation_keys(card: OpportunityCard) -> list[tuple[str, str]]:
    context = card.market_context
    values: list[tuple[str, str]] = []
    if card.asset_type == "ETF" or card.opportunity_bucket == "etf_index":
        values.append(("ETF/指数工具", "etf"))
    if context is None:
        return values
    values.append((context.industry, "industry"))
    values.extend((theme, "theme") for theme in context.themes)
    values.extend((_normalize_index_name(index), "index") for index in context.index_memberships)
    return _dedupe_keys(values)


def _normalize_index_name(name: str) -> str:
    text = name.strip()
    if "科创" in text:
        return "科创板"
    if "创业" in text:
        return "创业板"
    if "沪深300" in text:
        return "沪深300"
    if "中证500" in text:
        return "中证500"
    if "中证1000" in text:
        return "中证1000"
    if "ETF" in text.upper():
        return "ETF/指数工具"
    return text


def _best_category(categories: set[str]) -> str:
    return max(categories, key=lambda item: CATEGORY_PRIORITY.get(item, 0))


def _card_quality(card: OpportunityCard) -> float:
    conviction = card.decision.conviction_score if card.decision else 0.0
    quality = (
        card.rank_score * 0.4
        + card.factor_score * 0.3
        + card.strategy_score * 0.15
        + conviction * 0.15
    )
    if _is_blocked(card):
        quality *= 0.55
    elif card.opportunity_bucket in {"etf_index", "theme_growth"}:
        quality += 0.03
    return _clamp(quality)


def _is_actionable(card: OpportunityCard) -> bool:
    action = card.decision.action if card.decision else ""
    return action in ACTIONABLE_ACTIONS and not _is_blocked(card)


def _is_watchable(card: OpportunityCard) -> bool:
    action = card.decision.action if card.decision else ""
    return action in WATCH_ACTIONS and not _is_blocked(card)


def _is_blocked(card: OpportunityCard) -> bool:
    if card.opportunity_bucket == "risk_filtered":
        return True
    if not card.decision:
        return False
    return card.decision.risk_status == "blocked" or card.decision.action == "avoid"


def _leader(card: OpportunityCard) -> RotationThemeLeader:
    decision = card.decision
    return RotationThemeLeader(
        instrument_id=card.instrument_id,
        instrument_label=card.instrument_label,
        score=round(_card_quality(card), 4),
        action=decision.action if decision else "watch",
        action_label=decision.action_label if decision else "观察",
        risk_status=decision.risk_status if decision else "clear",
        bucket=BUCKET_LABELS.get(card.opportunity_bucket, card.opportunity_bucket),
        trigger_price=_decimal_text(
            decision.trigger_price if decision and decision.trigger_price is not None else card.entry_plan.trigger_price
        ),
    )


def _stance(score: float, actionable_count: int, blocked_count: int, count: int) -> str:
    blocked_ratio = blocked_count / count if count else 0.0
    if blocked_ratio >= 0.6:
        return "风险过滤"
    if score >= 0.72 and actionable_count > 0:
        return "进攻"
    if score >= 0.58:
        return "可关注"
    if score >= 0.42:
        return "等回踩"
    return "弱观察"


def _summary(
    name: str,
    count: int,
    actionable_count: int,
    blocked_count: int,
    leaders: list[RotationThemeLeader],
) -> str:
    leader = leaders[0].instrument_label or leaders[0].instrument_id if leaders else "暂无代表标的"
    parts = [
        f"{name}方向有{count}个候选",
        f"{actionable_count}个可行动",
        f"代表标的：{leader}",
    ]
    if blocked_count:
        parts.append(f"{blocked_count}个被风险过滤")
    return "；".join(parts)


def _tags(name: str, category: str, etf_count: int, blocked_count: int) -> list[str]:
    tags = [category]
    if name in THEME_PRIORITY:
        tags.append("重点方向")
    if etf_count:
        tags.append("含ETF")
    if blocked_count:
        tags.append("有风险过滤")
    return tags


def _dedupe_keys(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for name, category in values:
        text = str(name).strip()
        if not text:
            continue
        grouped[text].add(category)
    return [(name, _best_category(categories)) for name, categories in grouped.items()]


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
