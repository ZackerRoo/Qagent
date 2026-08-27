from datetime import date

from qagent.data_management import HistoricalBackfillResult, run_historical_backfill
from qagent.config import get_settings
from qagent.db import create_session_factory, initialize_database
from qagent.historical_evidence.providers import (
    build_historical_evidence_provider,
    build_historical_fundamental_provider,
)
from qagent.providers.factory import build_market_data_provider
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.repository import QagentRepository
from qagent.strategy_data.providers import (
    CurrentSnapshotOverlayStrategyDataProvider,
    build_strategy_data_provider,
)


def run_historical_backfill_job(job_id: str) -> HistoricalBackfillResult:
    initialize_database()
    session_factory = create_session_factory()
    repo = QagentRepository(session_factory)
    job = repo.get_historical_backfill_job(job_id)
    if job is None:
        raise ValueError(f"historical backfill job not found: {job_id}")
    scope = job.data_health.get("backfill_scope", "symbols")
    batch_size = int(job.data_health.get("backfill_batch_size", "100") or 100)
    historical_fundamentals = build_historical_fundamental_provider(job.provider)
    settings = get_settings()
    if settings.fuyao_api_key and job.start_date <= date.today() <= job.end_date:
        current_strategy_provider = build_strategy_data_provider(
            job.provider,
            settings,
            include_fuyao_current_snapshot=True,
        )
        strategy_provider = (
            CurrentSnapshotOverlayStrategyDataProvider(
                historical_fundamentals,
                current_strategy_provider,
            )
            if historical_fundamentals is not None
            else current_strategy_provider
        )
    else:
        strategy_provider = historical_fundamentals or build_strategy_data_provider(job.provider)
    return run_historical_backfill(
        repo=repo,
        cache=MarketDataCacheRepository(session_factory),
        provider=build_market_data_provider(job.provider),
        strategy_provider=strategy_provider,
        provider_mode=job.provider,
        instrument_ids=job.symbols,
        start=job.start_date,
        end=job.end_date,
        job_id=job.job_id,
        historical_evidence_provider=build_historical_evidence_provider(job.provider),
        scope=scope,
        batch_size=batch_size,
    )
