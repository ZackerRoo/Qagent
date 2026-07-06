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

CN_BENCHMARK_PROXIES: dict[str, tuple[str, ...]] = {
    "CN:000300.IDX": ("CN:510300", "CN:159919"),
    "CN:000905.IDX": ("CN:510500", "CN:159922"),
    "CN:399006.IDX": ("CN:159915", "CN:159949"),
    "CN:000688.IDX": ("CN:588000", "CN:588080"),
}


def benchmark_ids() -> list[str]:
    return [item.benchmark_id for item in CN_BENCHMARKS]


def benchmark_proxy_ids() -> list[str]:
    return sorted({proxy_id for proxy_ids in CN_BENCHMARK_PROXIES.values() for proxy_id in proxy_ids})


def apply_benchmark_comparisons(
    cards: list[OpportunityCard],
    *,
    provider: MarketDataProvider,
    bars_by_instrument: dict[str, pd.DataFrame],
    start: date,
    end: date,
    lookback_rows: int = 20,
) -> dict[str, str]:
    benchmark_bars = load_benchmark_bars(provider, start, end)
    benchmark_frames = benchmark_frames_from_bars(benchmark_bars)
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
    benchmark_bars = load_benchmark_bars(provider, start, end)
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
    benchmark_frames = benchmark_frames_from_bars(benchmark_bars)
    for definition in CN_BENCHMARKS:
        benchmark_return = _period_return_pct(
            benchmark_frames.get(definition.benchmark_id, pd.DataFrame()),
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


def load_benchmark_bars(
    provider: MarketDataProvider,
    start: date,
    end: date,
) -> pd.DataFrame:
    ids = benchmark_ids()
    direct = _load_benchmark_bars(provider, ids, start, end)
    missing_ids = [benchmark_id for benchmark_id in ids if _bars_for(direct, benchmark_id).empty]
    if not missing_ids:
        return direct
    proxy_ids = sorted(
        {
            proxy_id
            for benchmark_id in missing_ids
            for proxy_id in CN_BENCHMARK_PROXIES.get(benchmark_id, ())
        }
    )
    proxy_bars = _load_benchmark_bars(provider, proxy_ids, start, end) if proxy_ids else pd.DataFrame()
    proxy_frames = _proxy_frames_from_bars(proxy_bars, missing_ids)
    frames = [direct] if not direct.empty else []
    frames.extend(frame for frame in proxy_frames.values() if not frame.empty)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def benchmark_frames_from_bars(benchmark_bars: pd.DataFrame) -> dict[str, pd.DataFrame]:
    direct_frames = {
        benchmark_id: _bars_for(benchmark_bars, benchmark_id)
        for benchmark_id in benchmark_ids()
    }
    missing_ids = [benchmark_id for benchmark_id, frame in direct_frames.items() if frame.empty]
    if not missing_ids:
        return direct_frames
    proxy_frames = _proxy_frames_from_bars(benchmark_bars, missing_ids)
    for benchmark_id, frame in proxy_frames.items():
        if not frame.empty:
            direct_frames[benchmark_id] = frame
    return direct_frames


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


def _proxy_frames_from_bars(
    frame: pd.DataFrame,
    benchmark_ids_to_fill: list[str],
) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    if frame.empty or "instrument_id" not in frame.columns:
        return frames
    for benchmark_id in benchmark_ids_to_fill:
        for proxy_id in CN_BENCHMARK_PROXIES.get(benchmark_id, ()):
            proxy_frame = _bars_for(frame, proxy_id)
            if proxy_frame.empty:
                continue
            normalized = proxy_frame.copy()
            normalized["benchmark_proxy_id"] = proxy_id
            normalized["instrument_id"] = benchmark_id
            if "provider" in normalized.columns:
                normalized["provider"] = normalized["provider"].astype(str) + f":proxy:{proxy_id}"
            else:
                normalized["provider"] = f"proxy:{proxy_id}"
            frames[benchmark_id] = normalized
            break
    return frames


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
