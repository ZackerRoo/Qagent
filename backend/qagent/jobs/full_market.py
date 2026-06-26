from pydantic import BaseModel, Field

from qagent.jobs.daily_scan import DailyScanResult, run_daily_scan
from qagent.market.tradable import load_cn_tradable_instruments
from qagent.providers.factory import build_market_data_provider
from qagent.storage.repository import (
    QagentRepository,
    TradableCatalogSummary,
)
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
