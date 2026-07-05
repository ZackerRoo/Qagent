from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from qagent.domain.enums import Market
from qagent.domain.models import (
    BenchmarkComparison,
    BenchmarkComparisonItem,
    OpportunityCard,
)
from qagent.providers.base import MarketDataProvider


@dataclass(frozen=True)
class BenchmarkDefinition:
    benchmark_id: str
    name: str
    keywords: tuple[str, ...] = ()


CN_BENCHMARKS = (
    BenchmarkDefinition("CN:000300.IDX", "沪深300", ("沪深300", "大盘", "蓝筹")),
    BenchmarkDefinition("CN:000905.IDX", "中证500", ("中证500", "中盘")),
    BenchmarkDefinition("CN:399006.IDX", "创业板指", ("创业板", "创业板50")),
    BenchmarkDefinition("CN:000688.IDX", "科创50", ("科创", "科创50", "半导体", "芯片")),
)


def apply_benchmark_comparisons(
    cards: list[OpportunityCard],
    *,
    provider: MarketDataProvider,
    bars_by_instrument: dict[str, pd.DataFrame],
    start: date,
    end: date,
    lookback_rows: int = 20,
) -> dict[str, str]:
    benchmark_ids = [item.benchmark_id for item in CN_BENCHMARKS]
    benchmark_bars = _load_benchmark_bars(provider, benchmark_ids, start, end)
    benchmark_frames = {
        benchmark_id: _bars_for(benchmark_bars, benchmark_id)
        for benchmark_id in benchmark_ids
    }
    applied = 0
    missing = 0
    for card in cards:
        if card.market != Market.CN:
            continue
        instrument_bars = bars_by_instrument.get(card.instrument_id, pd.DataFrame())
        comparison = build_benchmark_comparison_for_card(
            card,
            instrument_bars=instrument_bars,
            benchmark_frames=benchmark_frames,
            lookback_rows=lookback_rows,
        )
        if comparison is None:
            missing += 1
            continue
        card.benchmark_comparison = comparison
        applied += 1
    return {
        "benchmark_comparison_cards": str(applied),
        "benchmark_comparison_missing_cards": str(missing),
        "benchmark_comparison_benchmarks": str(
            sum(1 for frame in benchmark_frames.values() if not frame.empty)
        ),
    }


def build_benchmark_comparison_for_card(
    card: OpportunityCard,
    *,
    instrument_bars: pd.DataFrame,
    benchmark_frames: dict[str, pd.DataFrame],
    lookback_rows: int = 20,
) -> BenchmarkComparison | None:
    instrument_return = _period_return_pct(instrument_bars, lookback_rows)
    if instrument_return is None:
        return None
    items: list[BenchmarkComparisonItem] = []
    for definition in CN_BENCHMARKS:
        benchmark_return = _period_return_pct(
            benchmark_frames.get(definition.benchmark_id, pd.DataFrame()),
            lookback_rows,
        )
        benchmark_start, benchmark_end = _period_bounds(
            benchmark_frames.get(definition.benchmark_id, pd.DataFrame()),
            lookback_rows,
        )
        if benchmark_return is None:
            items.append(
                BenchmarkComparisonItem(
                    benchmark_id=definition.benchmark_id,
                    name=definition.name,
                    start_date=benchmark_start,
                    end_date=benchmark_end,
                    instrument_return_pct=instrument_return,
                    return_pct=None,
                    excess_return_pct=None,
                    verdict="missing",
                    summary=f"{definition.name} 基准数据不足，暂不能比较。",
                )
            )
            continue
        excess = round(instrument_return - benchmark_return, 4)
        verdict = _benchmark_verdict(excess)
        items.append(
            BenchmarkComparisonItem(
                benchmark_id=definition.benchmark_id,
                name=definition.name,
                start_date=benchmark_start,
                end_date=benchmark_end,
                instrument_return_pct=instrument_return,
                return_pct=benchmark_return,
                excess_return_pct=excess,
                verdict=verdict,
                summary=_benchmark_summary(definition.name, instrument_return, benchmark_return, excess),
            )
        )
    if not items:
        return None
    primary = _primary_benchmark(card, items)
    return BenchmarkComparison(
        primary=primary,
        items=items,
        summary=f"相对{primary.name}{_excess_text(primary.excess_return_pct)}。",
    )


def benchmark_items_for_return(
    *,
    provider: MarketDataProvider,
    start: date,
    end: date,
    base_return_pct: float,
    lookback_rows: int = 20,
) -> list[dict[str, float | str | None]]:
    benchmark_ids = [item.benchmark_id for item in CN_BENCHMARKS]
    benchmark_bars = _load_benchmark_bars(provider, benchmark_ids, start, end)
    return benchmark_items_for_return_from_bars(
        benchmark_bars=benchmark_bars,
        base_return_pct=base_return_pct,
        lookback_rows=lookback_rows,
    )


def benchmark_items_for_return_from_bars(
    *,
    benchmark_bars: pd.DataFrame,
    base_return_pct: float,
    lookback_rows: int = 20,
) -> list[dict[str, float | str | None]]:
    rows: list[dict[str, float | str | None]] = []
    for definition in CN_BENCHMARKS:
        benchmark_return = _period_return_pct(
            _bars_for(benchmark_bars, definition.benchmark_id),
            lookback_rows,
        )
        excess = None if benchmark_return is None else round(base_return_pct - benchmark_return, 4)
        rows.append(
            {
                "benchmark_id": definition.benchmark_id,
                "name": definition.name,
                "return_pct": benchmark_return,
                "excess_return_pct": excess,
            }
        )
    return rows


def _load_benchmark_bars(
    provider: MarketDataProvider,
    benchmark_ids: list[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    try:
        return provider.get_daily_bars(benchmark_ids, start, end)
    except Exception:
        return pd.DataFrame()


def _bars_for(frame: pd.DataFrame, instrument_id: str) -> pd.DataFrame:
    if frame.empty or "instrument_id" not in frame.columns:
        return pd.DataFrame()
    return frame.loc[frame["instrument_id"] == instrument_id].copy()


def _period_return_pct(frame: pd.DataFrame, lookback_rows: int) -> float | None:
    if frame.empty or "close" not in frame.columns:
        return None
    ordered = frame.sort_values("trade_date").tail(max(lookback_rows, 2))
    closes = pd.to_numeric(ordered["close"], errors="coerce").dropna()
    if len(closes) < 2:
        return None
    first = float(closes.iloc[0])
    last = float(closes.iloc[-1])
    if first == 0:
        return None
    return round((last / first - 1) * 100, 4)


def _period_bounds(frame: pd.DataFrame, lookback_rows: int) -> tuple[date | None, date | None]:
    if frame.empty or "trade_date" not in frame.columns:
        return None, None
    ordered = frame.sort_values("trade_date").tail(max(lookback_rows, 2))
    if ordered.empty:
        return None, None
    values = pd.to_datetime(ordered["trade_date"], errors="coerce").dt.date.dropna()
    if values.empty:
        return None, None
    return values.iloc[0], values.iloc[-1]


def _primary_benchmark(
    card: OpportunityCard,
    items: list[BenchmarkComparisonItem],
) -> BenchmarkComparisonItem:
    context_text = " ".join(
        [
            card.market_context.industry if card.market_context else "",
            " ".join(card.market_context.themes if card.market_context else []),
            " ".join(card.market_context.index_memberships if card.market_context else []),
            " ".join(card.opportunity_tags),
            card.opportunity_bucket,
        ]
    )
    for definition in reversed(CN_BENCHMARKS):
        if any(keyword in context_text for keyword in definition.keywords):
            matched = next((item for item in items if item.benchmark_id == definition.benchmark_id), None)
            if matched is not None:
                return matched
    return items[0]


def _benchmark_verdict(excess_return_pct: float) -> str:
    if excess_return_pct >= 3:
        return "outperforming"
    if excess_return_pct <= -3:
        return "underperforming"
    return "in_line"


def _benchmark_summary(
    name: str,
    instrument_return_pct: float,
    benchmark_return_pct: float,
    excess_return_pct: float,
) -> str:
    return (
        f"近20日标的 {instrument_return_pct:+.2f}%，{name} {benchmark_return_pct:+.2f}%，"
        f"超额 {excess_return_pct:+.2f}%。"
    )


def _excess_text(value: float | None) -> str:
    if value is None:
        return "暂无可比基准数据"
    if value > 0:
        return f"超额 {value:+.2f}%"
    return f"落后 {value:+.2f}%"
