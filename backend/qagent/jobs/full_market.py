from pydantic import BaseModel, Field

from qagent.db import create_session_factory, initialize_database
from qagent.domain.models import OpportunityCard
from qagent.jobs.daily_scan import DailyScanResult, ScanItem, run_daily_scan
from qagent.market.tradable import load_cn_tradable_instruments
from qagent.monitoring.signal_monitor import build_signal_monitor_center
from qagent.providers.factory import build_market_data_provider
from qagent.recommendations.portfolio import build_portfolio_plan
from qagent.recommendations.quality_gate import (
    apply_recommendation_quality_gate,
    recommendation_quality_data_health,
)
from qagent.recommendations.rotation import sort_recommendation_cards
from qagent.research.action_center import build_manual_action_center
from qagent.research.decision_quality import build_decision_quality_center
from qagent.research.market_intelligence import (
    apply_market_intelligence_to_cards,
    build_market_intelligence_center,
)
from qagent.research.operational_readiness import build_operational_readiness_center
from qagent.storage.repository import (
    QagentRepository,
    TradableCatalogSummary,
)
from qagent.strategies.models import StrategyHealth, StrategyHealthPoint
from qagent.strategy_data.providers import EmptyStrategyDataProvider


class TradableCatalogSyncResult(BaseModel):
    summary: TradableCatalogSummary
    data_health: dict[str, str] = Field(default_factory=dict)


class FullMarketScanResult(BaseModel):
    symbols: list[str]
    scan: DailyScanResult
    data_health: dict[str, str] = Field(default_factory=dict)


def sync_cn_tradable_catalog(
    repo: QagentRepository,
    include_full_etfs: bool = True,
    use_cache: bool = False,
) -> TradableCatalogSyncResult:
    catalog = load_cn_tradable_instruments(
        include_full_etfs=include_full_etfs,
        use_cache=use_cache,
    )
    summary = repo.replace_tradable_instruments(catalog.items, catalog.data_health)
    return TradableCatalogSyncResult(
        summary=summary,
        data_health={
            **catalog.data_health,
            "tradable_catalog": "sqlite",
            "tradable_synced": str(summary.total_count),
        },
    )


def build_full_market_symbols(
    repo: QagentRepository,
    max_symbols: int = 300,
    include_etfs: bool = True,
) -> list[str]:
    max_count = max(max_symbols, 0)
    if max_count == 0:
        return []

    stocks = repo.list_tradable_instruments(asset_types={"stock"}, limit=max_count)
    if not include_etfs:
        return [item.instrument_id for item in stocks[:max_count]]

    etfs = repo.list_tradable_instruments(asset_types={"etf"}, limit=max_count)
    if not stocks:
        return [item.instrument_id for item in etfs[:max_count]]
    if not etfs:
        return [item.instrument_id for item in stocks[:max_count]]
    if max_count == 1:
        return [stocks[0].instrument_id]

    etf_quota = min(len(etfs), max(1, max_count // 5))
    stock_quota = min(len(stocks), max_count - etf_quota)
    if stock_quota == 0:
        stock_quota = 1
        etf_quota = min(len(etfs), max_count - stock_quota)

    remaining = max_count - stock_quota - etf_quota
    if remaining > 0:
        extra_stocks = min(len(stocks) - stock_quota, remaining)
        stock_quota += extra_stocks
        remaining -= extra_stocks
    if remaining > 0:
        etf_quota += min(len(etfs) - etf_quota, remaining)

    return [
        item.instrument_id
        for item in [*stocks[:stock_quota], *etfs[:etf_quota]]
    ]


def build_full_market_batch_symbols(
    repo: QagentRepository,
    include_etfs: bool = True,
    max_symbols: int | None = None,
) -> list[str]:
    asset_types = {"stock", "etf"} if include_etfs else {"stock"}
    limit = max_symbols if max_symbols is not None else 20_000
    instruments = repo.list_tradable_instruments(asset_types=asset_types, limit=limit)
    return [item.instrument_id for item in instruments]


def run_full_market_scan(
    repo: QagentRepository,
    provider_mode: str = "free",
    max_symbols: int = 300,
    include_etfs: bool = True,
    sync_if_empty: bool = True,
) -> FullMarketScanResult:
    summary = repo.tradable_catalog_summary()
    sync_health: dict[str, str] = {}
    if sync_if_empty and summary.total_count == 0:
        sync_result = sync_cn_tradable_catalog(repo=repo)
        summary = sync_result.summary
        sync_health = sync_result.data_health
    symbols = build_full_market_symbols(
        repo=repo,
        max_symbols=max_symbols,
        include_etfs=include_etfs,
    )
    provider = build_market_data_provider(provider_mode)
    scan = run_daily_scan(
        symbols,
        provider,
        mode=provider_mode,
        strategy_data_provider=EmptyStrategyDataProvider(),
    )
    scan.data_health.update(
        {
            "full_market_catalog": "sqlite",
            "full_market_catalog_total": str(summary.total_count),
            "full_market_requested": str(len(symbols)),
            "full_market_include_etfs": str(include_etfs).lower(),
        }
    )
    scan.data_health.update(sync_health)
    return FullMarketScanResult(
        symbols=symbols,
        scan=scan,
        data_health=scan.data_health,
    )


def run_full_market_batch_scan_job(job_id: str, top_cards_limit: int = 200) -> None:
    repo = _repo()
    job = repo.get_full_market_scan_job(job_id)
    if job is None:
        return
    provider = build_market_data_provider(job.provider)
    all_cards: list[OpportunityCard] = []
    all_items: list[ScanItem] = []
    strategy_health_batches: list[list[StrategyHealth]] = []
    aggregate_health: dict[str, str] = {
        "provider": job.provider,
        "full_market_scan_mode": "batch",
        "full_market_total_symbols": str(job.total_symbols),
        "full_market_batch_size": str(job.batch_size),
        "full_market_batches": str(job.total_batches),
        "full_market_include_etfs": str(job.include_etfs).lower(),
    }
    scanned_symbols = 0
    completed_batches = 0
    error_count = 0

    repo.update_full_market_scan_job(
        job_id,
        status="running",
        message=f"Starting full-market scan: {job.total_symbols} symbols",
        data_health=aggregate_health,
    )

    for batch in _chunks(job.symbols, job.batch_size):
        completed_batches += 1
        try:
            scan = run_daily_scan(
                batch,
                provider,
                mode=job.provider,
                strategy_data_provider=EmptyStrategyDataProvider(),
            )
            repo.save_scan_run(
                provider=job.provider,
                mode=f"batch:{job_id}",
                symbols=batch,
                result=scan,
            )
            all_cards.extend(scan.cards)
            all_items.extend(scan.items)
            strategy_health_batches.append(scan.strategy_health)
            _merge_health(aggregate_health, scan.data_health)
            error_count += _int_health(scan.data_health, "scan_errors")
        except Exception as exc:
            error_count += len(batch)
            aggregate_health[f"batch_{completed_batches}_error"] = str(exc)[:500]
        scanned_symbols += len(batch)
        repo.update_full_market_scan_job(
            job_id,
            status="running",
            scanned_symbols=scanned_symbols,
            completed_batches=completed_batches,
            cards=len(all_cards),
            errors=error_count,
            message=f"Completed batch {completed_batches}/{job.total_batches}",
            data_health=aggregate_health,
        )

    strategy_health = _merge_strategy_health(strategy_health_batches)
    market_intelligence = build_market_intelligence_center(
        cards=all_cards,
        items=all_items,
        bars_by_instrument={},
        strategy_health=strategy_health,
        data_health=aggregate_health,
    )
    apply_market_intelligence_to_cards(all_cards, market_intelligence)
    apply_recommendation_quality_gate(all_cards)
    ranked_cards = sort_recommendation_cards(all_cards)
    visible_cards = ranked_cards[:top_cards_limit]
    visible_items = _visible_rejected_items(all_items, limit=500)
    portfolio_plan = build_portfolio_plan(visible_cards)
    cache_key = _full_market_batch_cache_key(job.provider, job.include_etfs)
    payload_data_health = {
        **aggregate_health,
        **market_intelligence.data_health,
        **recommendation_quality_data_health(visible_cards),
        "scan_result_cache": "full_market_batch",
        "scan_result_cache_key": cache_key,
        "full_market_cards_total": str(len(ranked_cards)),
        "full_market_cards_returned": str(len(visible_cards)),
        "full_market_rejected_items": str(
            len([item for item in all_items if _is_rejected_item(item)])
        ),
        "full_market_items_returned": str(len(visible_items)),
        "scanned": str(scanned_symbols),
        "cards": str(len(visible_cards)),
    }
    manual_action_center = build_manual_action_center(
        cards=visible_cards,
        market_intelligence=market_intelligence,
        strategy_health=strategy_health,
        data_health=payload_data_health,
    )
    payload_data_health.update(manual_action_center.data_health)
    signal_monitor = build_signal_monitor_center(
        visible_cards,
        bars_by_instrument={},
    )
    payload_data_health.update(signal_monitor.data_health)
    decision_quality_center = build_decision_quality_center(
        cards=visible_cards,
        market_intelligence=market_intelligence,
        portfolio_plan=portfolio_plan,
        signal_monitor=signal_monitor,
        strategy_health=strategy_health,
        data_health=payload_data_health,
    )
    payload_data_health.update(decision_quality_center.data_health)
    operational_readiness_center = build_operational_readiness_center(
        cards=visible_cards,
        market_intelligence=market_intelligence,
        decision_quality_center=decision_quality_center,
        signal_monitor=signal_monitor,
        strategy_health=strategy_health,
        data_health=payload_data_health,
    )
    payload_data_health.update(operational_readiness_center.data_health)
    payload = {
        "symbols": job.symbols,
        "cards": [card.model_dump(mode="json") for card in visible_cards],
        "items": [item.model_dump(mode="json") for item in visible_items],
        "strategy_health": [item.model_dump(mode="json") for item in strategy_health],
        "factor_rankings": [],
        "sector_strength": [],
        "portfolio_plan": portfolio_plan.model_dump(mode="json"),
        "market_intelligence": market_intelligence.model_dump(mode="json"),
        "manual_action_center": manual_action_center.model_dump(mode="json"),
        "signal_monitor": signal_monitor.model_dump(mode="json"),
        "decision_quality_center": decision_quality_center.model_dump(mode="json"),
        "operational_readiness_center": operational_readiness_center.model_dump(mode="json"),
        "data_health": payload_data_health,
    }
    repo.save_scan_result_cache(
        cache_key=cache_key,
        provider=job.provider,
        mode="full_market_batch",
        symbols=job.symbols,
        payload=payload,
    )
    repo.update_full_market_scan_job(
        job_id,
        status="succeeded",
        scanned_symbols=scanned_symbols,
        completed_batches=job.total_batches,
        cards=len(visible_cards),
        errors=error_count,
        message="Full-market batch scan complete",
        data_health=payload["data_health"],
        result_cache_key=cache_key,
    )


def full_market_batch_cache_key(provider: str, include_etfs: bool = True) -> str:
    return _full_market_batch_cache_key(provider, include_etfs)


def _repo() -> QagentRepository:
    initialize_database()
    return QagentRepository(create_session_factory())


def _visible_rejected_items(items: list[ScanItem], limit: int = 500) -> list[ScanItem]:
    rejected = [item for item in items if _is_rejected_item(item)]
    rejected.sort(
        key=lambda item: (
            _rejection_status_rank(item.status),
            item.rejection_score or 0,
            item.factor_score or 0,
        ),
        reverse=True,
    )
    return rejected[:limit]


def _is_rejected_item(item: ScanItem) -> bool:
    return item.status in {"no_data", "no_setup", "data_error"}


def _rejection_status_rank(status: str) -> int:
    return {"data_error": 3, "no_data": 2, "no_setup": 1}.get(status, 0)


def _chunks(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _merge_health(target: dict[str, str], source: dict[str, str]) -> None:
    for key, value in source.items():
        current = target.get(key)
        if current is not None and str(current).isdigit() and str(value).isdigit():
            target[key] = str(int(current) + int(str(value)))
        elif key == "errors" and current:
            target[key] = f"{current} | {value}"
        else:
            target[key] = str(value)


def _merge_strategy_health(batches: list[list[StrategyHealth]]) -> list[StrategyHealth]:
    grouped: dict[str, list[StrategyHealth]] = {}
    for batch in batches:
        for item in batch:
            grouped.setdefault(item.strategy_id, []).append(item)

    merged: list[StrategyHealth] = []
    for strategy_id, items in grouped.items():
        sample_count = sum(item.sample_count for item in items)
        win_rate = _weighted_average(
            [(item.win_rate_10d, item.sample_count) for item in items]
        )
        avg_10d = _weighted_average(
            [(item.avg_return_10d, item.sample_count) for item in items]
        )
        avg_20d = _weighted_average(
            [(item.avg_return_20d, item.sample_count) for item in items]
        )
        max_losses = [
            item.max_loss_10d
            for item in items
            if item.max_loss_10d is not None
        ]
        missing_data = sorted({value for item in items for value in item.missing_data})
        merged.append(
            StrategyHealth(
                strategy_id=strategy_id,
                name=items[0].name,
                family=items[0].family,
                readiness=_merged_readiness(items, sample_count, win_rate, avg_10d),
                sample_count=sample_count,
                win_rate_10d=win_rate,
                avg_return_10d=avg_10d,
                avg_return_20d=avg_20d,
                max_loss_10d=min(max_losses) if max_losses else None,
                missing_data=missing_data,
                curve=_merge_strategy_curve(items),
            )
        )
    return sorted(merged, key=lambda item: item.strategy_id)


def _merge_strategy_curve(items: list[StrategyHealth]) -> list[StrategyHealthPoint]:
    grouped: dict[str, list[StrategyHealthPoint]] = {}
    for item in items:
        for point in item.curve:
            grouped.setdefault(point.label, []).append(point)

    curve: list[StrategyHealthPoint] = []
    for label in sorted(grouped):
        points = grouped[label]
        sample_count = sum(point.sample_count for point in points)
        win_rate = _weighted_average(
            [(point.win_rate_10d, point.sample_count) for point in points]
        )
        avg_10d = _weighted_average(
            [(point.avg_return_10d, point.sample_count) for point in points]
        )
        avg_20d = _weighted_average(
            [(point.avg_return_20d, point.sample_count) for point in points]
        )
        max_losses = [
            point.max_loss_10d
            for point in points
            if point.max_loss_10d is not None
        ]
        curve.append(
            StrategyHealthPoint(
                label=label,
                sample_count=sample_count,
                win_rate_10d=win_rate,
                avg_return_10d=avg_10d,
                avg_return_20d=avg_20d,
                max_loss_10d=min(max_losses) if max_losses else None,
            )
        )
    return curve


def _weighted_average(values: list[tuple[float | None, int]]) -> float | None:
    weighted_sum = 0.0
    total_weight = 0
    for value, weight in values:
        if value is None or weight <= 0:
            continue
        weighted_sum += value * weight
        total_weight += weight
    if total_weight == 0:
        return None
    return round(weighted_sum / total_weight, 2)


def _merged_readiness(
    items: list[StrategyHealth],
    sample_count: int,
    win_rate_10d: float | None,
    avg_return_10d: float | None,
) -> str:
    if sample_count == 0:
        if all(item.missing_data for item in items):
            return "missing_data"
        return "insufficient_history"
    if sample_count < 20:
        return "limited_sample"
    if (win_rate_10d or 0) >= 55 and (avg_return_10d or 0) > 0:
        return "validated"
    return "watch"


def _int_health(source: dict[str, str], key: str) -> int:
    try:
        return int(str(source.get(key, "0")))
    except ValueError:
        return 0


def _full_market_batch_cache_key(provider: str, include_etfs: bool) -> str:
    return f"full_market_batch:{provider.strip().lower()}:{str(include_etfs).lower()}"
