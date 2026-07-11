from qagent.data_management import HistoricalBackfillResult, run_historical_backfill
from qagent.db import create_session_factory, initialize_database
from qagent.historical_evidence.providers import (
    build_historical_evidence_provider,
    build_historical_fundamental_provider,
)
from qagent.providers.factory import build_market_data_provider
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.repository import QagentRepository
from qagent.strategy_data.providers import build_strategy_data_provider


def run_historical_backfill_job(job_id: str) -> HistoricalBackfillResult:
    initialize_database()
    session_factory = create_session_factory()
    repo = QagentRepository(session_factory)
    job = repo.get_historical_backfill_job(job_id)
    if job is None:
        raise ValueError(f"historical backfill job not found: {job_id}")
    return run_historical_backfill(
        repo=repo,
        cache=MarketDataCacheRepository(session_factory),
        provider=build_market_data_provider(job.provider),
        strategy_provider=(
            build_historical_fundamental_provider(job.provider)
            or build_strategy_data_provider(job.provider)
        ),
        provider_mode=job.provider,
        instrument_ids=job.symbols,
        start=job.start_date,
        end=job.end_date,
        job_id=job.job_id,
        historical_evidence_provider=build_historical_evidence_provider(job.provider),
    )
