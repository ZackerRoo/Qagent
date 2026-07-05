from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from qagent.domain.models import MarketContext, OpportunityCard, SectorMove, SectorStrength
from qagent.market.cn_context import build_market_context
from qagent.market.instruments import format_instrument_label


@dataclass
class _StrengthCandidate:
    instrument_id: str
    instrument_label: str | None
    context: MarketContext
    rank_score: float = 0.0
    factor_score: float = 0.0
    status: str = "watch"
    themes: list[str] = field(default_factory=list)


def build_sector_strength(
    cards: list[OpportunityCard],
    bars_by_instrument: dict[str, object],
    items: list[object] | None = None,
) -> list[SectorStrength]:
    candidates = _candidates(cards, items or [])
    grouped: dict[str, list[_StrengthCandidate]] = defaultdict(list)
    categories: dict[str, str] = {}
    for candidate in candidates:
        keys = _group_keys(candidate)
        for name, category in keys:
            if candidate.instrument_id in {item.instrument_id for item in grouped[name]}:
                categories[name] = _best_category(categories.get(name), category)
                continue
            grouped[name].append(candidate)
            categories[name] = _best_category(categories.get(name), category)

    sectors = [
        _build_sector(name, categories[name], group, bars_by_instrument)
        for name, group in grouped.items()
    ]
    return sorted(
        [sector for sector in sectors if sector is not None],
        key=lambda item: (item.score, item.sample_count, item.advance_ratio),
        reverse=True,
    )


def _candidates(cards: list[OpportunityCard], items: list[object]) -> list[_StrengthCandidate]:
    result: list[_StrengthCandidate] = []
    seen: set[str] = set()
    for card in cards:
        if card.market.value != "CN":
            continue
        context = card.market_context or build_market_context(card.instrument_id, card.instrument_label)
        if context is None:
            continue
        result.append(
            _StrengthCandidate(
                instrument_id=card.instrument_id,
                instrument_label=card.instrument_label,
                context=context,
                rank_score=card.rank_score,
                factor_score=card.factor_score,
                status=card.status.value,
                themes=list(card.opportunity_tags),
            )
        )
        seen.add(card.instrument_id)
    for item in items:
        instrument_id = str(getattr(item, "instrument_id", ""))
        if not instrument_id.startswith("CN:") or instrument_id in seen:
            continue
        label = getattr(item, "instrument_label", None) or format_instrument_label(instrument_id)
        context = build_market_context(instrument_id, label)
        if context is None:
            continue
        result.append(
            _StrengthCandidate(
                instrument_id=instrument_id,
                instrument_label=label,
                context=context,
                factor_score=float(getattr(item, "factor_score", 0) or 0),
                status=str(getattr(item, "status", "watch")),
            )
        )
    return result


def _group_keys(candidate: _StrengthCandidate) -> list[tuple[str, str]]:
    context = candidate.context
    keys: list[tuple[str, str]] = [(context.industry, "industry")]
    keys.extend((theme, "theme") for theme in context.themes)
    keys.extend((_normalize_index(index), "index") for index in context.index_memberships)
    umbrella = _umbrella_theme(context.industry, context.themes)
    if umbrella:
        keys.append((umbrella, "theme"))
    return _dedupe_keys(keys)


def _build_sector(
    name: str,
    category: str,
    candidates: list[_StrengthCandidate],
    bars_by_instrument: dict[str, object],
) -> SectorStrength | None:
    moves: list[tuple[_StrengthCandidate, float, str | None, int]] = []
    themes = []
    for candidate in candidates:
        bars = bars_by_instrument.get(candidate.instrument_id)
        move = _latest_move(bars)
        if move is None:
            continue
        change_pct, latest_close, volume = move
        moves.append((candidate, change_pct, latest_close, volume))
        themes.extend(candidate.context.themes)
        themes.extend(candidate.themes)

    if not moves:
        return None

    avg_change = round(sum(move[1] for move in moves) / len(moves), 2)
    advance_ratio = round(sum(1 for move in moves if move[1] > 0) / len(moves) * 100, 2)
    total_volume = sum(move[3] for move in moves)
    score = round(
        _clamp(
            (avg_change + 5) / 10 * 0.46
            + advance_ratio / 100 * 0.32
            + min(0.16, len(moves) * 0.04)
            + _category_boost(category)
            + _candidate_quality(moves) * 0.06
        ),
        4,
    )
    leaders = sorted(moves, key=lambda item: item[1], reverse=True)[:3]
    laggards = sorted(moves, key=lambda item: item[1])[:3]
    symbols = [candidate.instrument_id for candidate, _, _, _ in moves]
    label = "主题" if category == "theme" else "行业" if category == "industry" else "指数"
    summary = (
        f"{name}{label}样本{len(moves)}只，平均涨跌幅{avg_change:+.2f}%，"
        f"上涨占比{advance_ratio:.0f}%。"
    )
    return SectorStrength(
        industry=name,
        category=category,
        themes=sorted(set(themes))[:6],
        symbols=symbols,
        sample_count=len(moves),
        avg_change_pct=avg_change,
        advance_ratio=advance_ratio,
        total_volume=total_volume,
        score=score,
        leaders=[_sector_move(item) for item in leaders],
        laggards=[_sector_move(item) for item in laggards],
        summary=summary,
    )


def _latest_move(bars) -> tuple[float, str, int] | None:
    if bars is None or bars.empty or len(bars) < 2:
        return None
    ordered = bars.sort_values("trade_date")
    latest = ordered.iloc[-1]
    previous = ordered.iloc[-2]
    previous_close = float(previous["close"])
    if previous_close <= 0:
        return None
    change_pct = round((float(latest["close"]) / previous_close - 1) * 100, 2)
    return change_pct, f"{float(latest['close']):.2f}", int(latest.get("volume", 0))


def _sector_move(item: tuple[_StrengthCandidate, float, str | None, int]) -> SectorMove:
    candidate, change_pct, latest_close, _ = item
    return SectorMove(
        instrument_id=candidate.instrument_id,
        instrument_label=candidate.instrument_label,
        change_pct=change_pct,
        latest_close=latest_close,
    )


def _candidate_quality(moves: list[tuple[_StrengthCandidate, float, str | None, int]]) -> float:
    values = [
        max(candidate.rank_score, candidate.factor_score)
        for candidate, _, _, _ in moves
        if max(candidate.rank_score, candidate.factor_score) > 0
    ]
    if not values:
        return 0.5
    return sum(values) / len(values)


def _category_boost(category: str) -> float:
    return {"theme": 0.04, "industry": 0.025, "index": 0.015}.get(category, 0.0)


def _best_category(current: str | None, candidate: str) -> str:
    priority = {"theme": 3, "industry": 2, "index": 1}
    if current is None:
        return candidate
    return candidate if priority.get(candidate, 0) > priority.get(current, 0) else current


def _umbrella_theme(industry: str, themes: list[str]) -> str | None:
    text = " ".join([industry, *themes])
    if any(token in text for token in ["半导体", "芯片", "晶圆", "封测", "存储", "HBM"]):
        return "半导体"
    if any(token in text for token in ["AI", "算力", "光模块", "CPO", "服务器"]):
        return "AI算力供应链"
    return None


def _normalize_index(name: str) -> str:
    text = name.strip()
    if "科创" in text:
        return "科创板"
    if "创业" in text:
        return "创业板"
    return text


def _dedupe_keys(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, category in items:
        normalized = str(name).strip()
        if not normalized:
            continue
        key = (normalized, category)
        if key not in seen:
            result.append(key)
            seen.add(key)
    return result


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
