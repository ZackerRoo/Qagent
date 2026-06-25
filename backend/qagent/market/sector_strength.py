from collections import defaultdict

from qagent.domain.models import OpportunityCard, SectorMove, SectorStrength


def build_sector_strength(
    cards: list[OpportunityCard],
    bars_by_instrument: dict[str, object],
) -> list[SectorStrength]:
    grouped: dict[str, list[OpportunityCard]] = defaultdict(list)
    for card in cards:
        if card.market.value != "CN" or card.market_context is None:
            continue
        grouped[card.market_context.industry].append(card)

    sectors = [
        _build_sector(industry, industry_cards, bars_by_instrument)
        for industry, industry_cards in grouped.items()
    ]
    return sorted(
        [sector for sector in sectors if sector is not None],
        key=lambda item: item.score,
        reverse=True,
    )


def _build_sector(
    industry: str,
    cards: list[OpportunityCard],
    bars_by_instrument: dict[str, object],
) -> SectorStrength | None:
    moves: list[tuple[OpportunityCard, float, str | None, int]] = []
    themes = []
    for card in cards:
        bars = bars_by_instrument.get(card.instrument_id)
        move = _latest_move(bars)
        if move is None:
            continue
        change_pct, latest_close, volume = move
        moves.append((card, change_pct, latest_close, volume))
        if card.market_context:
            themes.extend(card.market_context.themes)

    if not moves:
        return None

    avg_change = round(sum(move[1] for move in moves) / len(moves), 2)
    advance_ratio = round(sum(1 for move in moves if move[1] > 0) / len(moves) * 100, 2)
    total_volume = sum(move[3] for move in moves)
    score = round(_clamp((avg_change + 5) / 10 * 0.6 + advance_ratio / 100 * 0.4), 4)
    leaders = sorted(moves, key=lambda item: item[1], reverse=True)[:3]
    laggards = sorted(moves, key=lambda item: item[1])[:3]
    symbols = [card.instrument_id for card, _, _, _ in moves]
    summary = (
        f"{industry}板块样本{len(moves)}只，平均涨跌幅{avg_change:+.2f}%，"
        f"上涨占比{advance_ratio:.0f}%。"
    )
    return SectorStrength(
        industry=industry,
        themes=sorted(set(themes))[:6],
        symbols=symbols,
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


def _sector_move(item: tuple[OpportunityCard, float, str | None, int]) -> SectorMove:
    card, change_pct, latest_close, _ = item
    return SectorMove(
        instrument_id=card.instrument_id,
        instrument_label=card.instrument_label,
        change_pct=change_pct,
        latest_close=latest_close,
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
