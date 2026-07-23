from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from time import sleep

import pandas as pd
from pydantic import BaseModel, Field

from qagent.backtesting.a_share_rules import (
    BrokerFeeRequest,
    build_instrument_rule_metadata_schedule,
    load_a_share_rule_schedule,
)
from qagent.historical_evidence.models import (
    HistoricalEvidenceBundle,
    HistoricalIndexCoverageStats,
    HistoricalInstrumentProfile,
    HistoricalLifecycleManifest,
    HistoricalReplayBar,
    HistoricalTradabilityPoint,
    normalize_historical_security_type,
)
from qagent.historical_evidence.providers import (
    INDEX_QUERIES,
    REQUIRED_BENCHMARK_IDS,
    HistoricalEvidenceProvider,
    historical_snapshot_dates,
)
from qagent.market.calendars import trading_sessions_in_range
from qagent.providers.base import MarketDataProvider
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.replay_evidence import (
    ReplayEvidenceRepository,
    ReplayEvidenceUnavailable,
)
from qagent.storage.repository import HistoricalBackfillJobRecord, QagentRepository
from qagent.strategy_data.providers import StrategyDataProvider


class HistoricalInstrumentCoverage(BaseModel):
    instrument_id: str
    asset_type: str
    expected_sessions: int
    bar_rows: int
    bar_coverage_ratio: float
    adjusted_rows: int
    adjustment_coverage_ratio: float | None
    adjustment_types: list[str] = Field(default_factory=list)
    source_providers: list[str] = Field(default_factory=list)
    first_trade_date: date | None
    last_trade_date: date | None
    fundamental_rows: int
    first_fundamental_date: date | None
    last_fundamental_date: date | None
    universe_snapshot_rows: int
    first_universe_date: date | None
    last_universe_date: date | None
    tradability_rows: int = 0
    tradability_coverage_ratio: float = 0.0
    first_tradability_date: date | None = None
    last_tradability_date: date | None = None
    suspended_rows: int = 0
    st_rows: int = 0
    profile_rows: int = 0
    listing_date: date | None = None
    delisting_date: date | None = None
    listing_status: str | None = None
    industry_rows: int = 0
    industries: list[str] = Field(default_factory=list)
    benchmark_membership_rows: int = 0
    benchmark_ids: list[str] = Field(default_factory=list)
    status: str
    issues: list[str] = Field(default_factory=list)


class HistoricalCoverageSummary(BaseModel):
    total_instruments: int
    ready_instruments: int
    partial_instruments: int
    missing_instruments: int
    bar_ready_instruments: int
    adjusted_ready_instruments: int
    fundamental_ready_instruments: int
    universe_ready_instruments: int
    tradability_ready_instruments: int
    profile_ready_instruments: int
    industry_ready_instruments: int
    benchmark_snapshot_rows: int
    benchmark_ready_snapshots: int
    benchmark_failed_snapshots: int
    benchmark_coverage_ratio: float
    average_bar_coverage_ratio: float
    average_adjustment_coverage_ratio: float | None


class HistoricalCoverageManifest(BaseModel):
    provider_mode: str
    start_date: date
    end_date: date
    generated_at: datetime
    summary: HistoricalCoverageSummary
    instruments: list[HistoricalInstrumentCoverage]
    data_health: dict[str, str] = Field(default_factory=dict)


class HistoricalBackfillResult(BaseModel):
    job: HistoricalBackfillJobRecord
    manifest: HistoricalCoverageManifest


class HistoricalBackfillFailed(RuntimeError):
    def __init__(self, result: HistoricalBackfillResult, message: str):
        super().__init__(message)
        self.result = result


def run_historical_backfill(
    *,
    repo: QagentRepository,
    cache: MarketDataCacheRepository,
    provider: MarketDataProvider,
    strategy_provider: StrategyDataProvider | None,
    provider_mode: str,
    instrument_ids: list[str],
    start: date,
    end: date,
    job_id: str | None = None,
    universe_as_of: date | None = None,
    historical_evidence_provider: HistoricalEvidenceProvider | None = None,
    scope: str = "symbols",
    batch_size: int = 100,
    broker_fee_request: BrokerFeeRequest | None = None,
) -> HistoricalBackfillResult:
    if start > end:
        raise ValueError("start must be on or before end")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"symbols", "full-a-share"}:
        raise ValueError("scope must be symbols or full-a-share")
    mode = provider_mode.strip().lower()
    replay_repo = ReplayEvidenceRepository(repo.session_factory, mode)
    inventory_profiles: list[HistoricalInstrumentProfile] = []
    provider_manifest = None
    if normalized_scope == "full-a-share":
        if historical_evidence_provider is None or not all(
            hasattr(historical_evidence_provider, name)
            for name in ("list_historical_instruments", "get_lifecycle_manifest")
        ):
            raise ValueError("full-a-share scope requires historical inventory support")
        current_revision = replay_repo.current_revision()
        if current_revision > 0:
            try:
                cached_inventory = replay_repo.lifecycle_inventory(current_revision)
                if cached_inventory and all(
                    item.snapshot_date >= end for item in cached_inventory
                ):
                    inventory_profiles = cached_inventory
            except ReplayEvidenceUnavailable:
                inventory_profiles = []
        if not inventory_profiles:
            inventory_profiles = historical_evidence_provider.list_historical_instruments(end)
            provider_manifest = historical_evidence_provider.get_lifecycle_manifest()
            if provider_manifest.status != "ready" or not inventory_profiles:
                inventory_profiles = replay_repo.recoverable_lifecycle_profiles(end)
            if not inventory_profiles:
                raise ReplayEvidenceUnavailable(
                    "full A-share historical inventory is not ready: "
                    f"{provider_manifest.error or provider_manifest.status}"
                )
        instrument_ids = [
            item.instrument_id
            for item in inventory_profiles
            if (item.listing_date is None or item.listing_date <= end)
            and (item.delisting_date is None or item.delisting_date >= start)
        ]
    symbols = sorted(set(instrument_ids))
    if not symbols:
        raise ValueError("instrument_ids cannot be empty")
    inventory_profile_by_id = {
        item.instrument_id: item for item in inventory_profiles
    }
    active_price_ranges = {
        instrument_id: (
            max(
                start,
                inventory_profile_by_id[instrument_id].listing_date or start,
            )
            if instrument_id in inventory_profile_by_id
            else start,
            min(
                end,
                inventory_profile_by_id[instrument_id].delisting_date or end,
            )
            if instrument_id in inventory_profile_by_id
            else end,
        )
        for instrument_id in symbols
    }
    job = repo.get_historical_backfill_job(job_id) if job_id else None
    if job is None:
        job = repo.create_historical_backfill_job(
            mode,
            symbols,
            start,
            end,
            data_health={
                "backfill_scope": normalized_scope,
                "backfill_batch_size": str(batch_size),
                "backfill_phase": "queued",
            },
        )
    elif (
        job.provider,
        sorted(set(job.symbols)),
        job.start_date,
        job.end_date,
    ) != (
        mode,
        symbols,
        start,
        end,
    ) and not (
        normalized_scope == "full-a-share"
        and job.provider == mode
        and not job.symbols
        and job.start_date == start
        and job.end_date == end
    ):
        raise ValueError("resume job scope does not match requested backfill")

    if job.symbols != symbols or job.total_symbols != len(symbols):
        updated = repo.update_historical_backfill_job(
            job.job_id,
            symbols=symbols,
            total_symbols=len(symbols),
        )
        if updated is None:
            raise RuntimeError(f"historical backfill job disappeared: {job.job_id}")
        job = updated

    repo.capture_tradable_universe_snapshot(universe_as_of or date.today())
    inventory_rows = 0
    inventory_recovered = False
    benchmark_rows = 0
    replay_rows = 0
    resume_requested = (
        job.status == "running"
        or job.data_health.get("backfill_resume_requested", "false").lower() == "true"
    )
    listing_aware_checkpoint = (
        job.data_health.get("backfill_price_range_semantics") == "listing_aware_v1"
    )
    initial_health = dict(job.data_health)
    initial_health.update(
        {
            "backfill_scope": normalized_scope,
            "backfill_batch_size": str(batch_size),
            "backfill_price_network_batch_size": str(min(batch_size, 5)),
            "backfill_phase": "inventory",
            "backfill_resume_requested": "false",
        }
    )
    if not resume_requested:
        initial_health["backfill_price_range_semantics"] = "listing_aware_v1"
    repo.update_historical_backfill_job(
        job.job_id,
        status="running",
        data_health=initial_health,
    )
    processed = min(job.processed_symbols, len(symbols)) if resume_requested else 0
    succeeded = min(job.succeeded_symbols, processed) if resume_requested else 0
    failed = min(job.failed_symbols, processed) if resume_requested else 0
    cache_reused = int(job.data_health.get("backfill_price_cache_reused", "0") or 0)
    network_succeeded = int(
        job.data_health.get("backfill_price_network_succeeded", "0") or 0
    )
    retryable_failed = int(
        job.data_health.get("backfill_price_retryable_failed", "0") or 0
    )
    permanent_failed = int(
        job.data_health.get("backfill_price_permanent_failed", "0") or 0
    )
    permanent_symbols = [
        item.strip()
        for item in job.data_health.get(
            "backfill_price_permanent_symbols",
            "",
        ).split(",")
        if item.strip()
    ]
    retryable_symbols = _restored_retryable_symbols(job)
    if resume_requested:
        processed_prefix = set(symbols[:processed])
        retryable_symbols = [
            item for item in retryable_symbols if item in processed_prefix
        ]
        permanent_symbols = [
            item for item in permanent_symbols if item in processed_prefix
        ]
    if resume_requested and not listing_aware_checkpoint:
        ready_prefix = 0
        replay_instruments = replay_repo.replay_instrument_ids(
            symbols[:processed],
            start,
            end,
            replay_repo.current_revision(),
        )
        for instrument_id in symbols[:processed]:
            symbol_start, symbol_end = active_price_ranges[instrument_id]
            if cache.has_usable_coverage(
                mode,
                instrument_id,
                symbol_start,
                symbol_end,
                require_adjusted=_requires_adjustment(instrument_id),
                minimum_session_coverage=0.95,
            ):
                if instrument_id not in replay_instruments:
                    replay_rows += _persist_replay_frame(
                        replay_repo,
                        cache.load_daily_bars(
                            mode,
                            [instrument_id],
                            symbol_start,
                            symbol_end,
                        ),
                    )
                ready_prefix += 1
                if instrument_id in retryable_symbols:
                    retryable_symbols.remove(instrument_id)
                if instrument_id in permanent_symbols:
                    permanent_symbols.remove(instrument_id)
                continue
            if (
                instrument_id not in retryable_symbols
                and instrument_id not in permanent_symbols
            ):
                retryable_symbols.append(instrument_id)
        succeeded = ready_prefix
        failed = processed - succeeded
        retryable_failed = max(retryable_failed, len(retryable_symbols))
        permanent_failed = len(permanent_symbols)
        _update_backfill_health(
            repo,
            job.job_id,
            backfill_price_range_semantics="listing_aware_v1",
            backfill_price_retryable_failed=str(retryable_failed),
            backfill_price_retryable_symbols=",".join(retryable_symbols),
            backfill_price_retry_unresolved=str(len(retryable_symbols)),
            backfill_price_permanent_failed=str(permanent_failed),
            backfill_price_permanent_symbols=",".join(permanent_symbols),
        )
        repo.update_historical_backfill_job(
            job.job_id,
            succeeded_symbols=succeeded,
            failed_symbols=failed,
        )
    retry_attempted = int(
        job.data_health.get("backfill_price_retry_attempted", "0") or 0
    )
    retry_recovered = int(
        job.data_health.get("backfill_price_retry_recovered", "0") or 0
    )
    rows_written = job.rows_written if resume_requested else 0
    errors: list[str] = list(job.errors[-100:]) if resume_requested else []
    rule_rows = 0
    fee_rule_rows = 0
    instrument_rule_rows = 0
    corporate_action_rows = 0
    corporate_action_coverage_rows = 0
    terminal_settlement_rows = 0
    corporate_action_health: dict[str, str] = {}

    try:
        if historical_evidence_provider is not None and all(
            hasattr(historical_evidence_provider, name)
            for name in ("list_historical_instruments", "get_lifecycle_manifest")
        ):
            inventory_reused = False
            current_revision = replay_repo.current_revision()
            if current_revision > 0:
                try:
                    existing_inventory = replay_repo.lifecycle_inventory(
                        current_revision
                    )
                    inventory_reused = bool(existing_inventory) and all(
                        item.snapshot_date >= end for item in existing_inventory
                    )
                except ReplayEvidenceUnavailable:
                    inventory_reused = False
            if not inventory_reused:
                profiles = inventory_profiles or (
                    historical_evidence_provider.list_historical_instruments(end)
                )
                provider_manifest = provider_manifest or (
                    historical_evidence_provider.get_lifecycle_manifest()
                )
                if provider_manifest.status != "ready" or not profiles:
                    recovered_profiles = (
                        replay_repo.recoverable_lifecycle_profiles(end)
                    )
                    if recovered_profiles:
                        profiles = recovered_profiles
                        inventory_recovered = True
                        if provider_manifest.error:
                            errors.append(
                                f"{provider_manifest.error}; recovered lifecycle "
                                "identity from validated BaoStock cache"
                            )
                inventory_profiles = profiles
                inventory_revision = replay_repo.current_revision() + 1
                inventory_rows = replay_repo.upsert_lifecycle_inventory(
                    profiles,
                    HistoricalLifecycleManifest(
                        provider_mode=mode,
                        source_revision=inventory_revision,
                        status=(
                            "ready" if inventory_recovered else provider_manifest.status
                        ),
                        expected_count=(
                            len(profiles)
                            if inventory_recovered
                            else provider_manifest.expected_count
                        ),
                        stored_count=0,
                        effective_through=(
                            end
                            if inventory_recovered
                            else provider_manifest.effective_through
                        ),
                        error=None if inventory_recovered else provider_manifest.error,
                        fetched_at=(
                            datetime.now(timezone.utc)
                            if inventory_recovered
                            else provider_manifest.fetched_at
                        ),
                    ),
                )
                if provider_manifest.error and not inventory_recovered:
                    errors.append(provider_manifest.error)
            else:
                inventory_profiles = existing_inventory

        _set_backfill_phase(repo, job.job_id, "trading_rules")
        schedule = load_a_share_rule_schedule()
        fees = schedule.fee_rules(
            broker_fee_request
            or BrokerFeeRequest(commission_bps="3", minimum_commission="5")
        )
        rule_rows = replay_repo.upsert_trading_rules(schedule.trading_rules)
        fee_rule_rows = replay_repo.upsert_fee_rules(fees)
        profile_by_id = {item.instrument_id: item for item in inventory_profiles}
        metadata = []
        for instrument_id in symbols:
            profile = profile_by_id.get(instrument_id)
            canonical_type = (
                normalize_historical_security_type(profile.security_type)
                if profile is not None
                else None
            ) or _asset_type(instrument_id, None)
            canonical_profile = HistoricalInstrumentProfile(
                instrument_id=instrument_id,
                snapshot_date=end,
                listing_date=profile.listing_date if profile is not None else None,
                delisting_date=profile.delisting_date if profile is not None else None,
                security_type=canonical_type,
                listing_status=(
                    profile.listing_status if profile is not None else "active"
                ),
                provider=profile.provider if profile is not None else "symbol_scope",
            )
            for item in build_instrument_rule_metadata_schedule(
                canonical_profile,
                start=start,
                end=end,
                schedule=schedule,
            ):
                metadata.append(item.model_copy(update={"provider_mode": mode}))
        instrument_rule_rows = replay_repo.upsert_instrument_rule_metadata(metadata)

        if historical_evidence_provider is not None and hasattr(
            historical_evidence_provider, "get_corporate_actions"
        ):
            _set_backfill_phase(repo, job.job_id, "corporate_actions")
            action_scope_symbols = symbols
            if normalized_scope == "full-a-share" and len(symbols) > 500:
                action_scope_symbols = [
                    item.instrument_id
                    for item in inventory_profiles
                    if item.instrument_id in symbols
                    and item.delisting_date is not None
                    and start <= item.delisting_date <= end
                ]
            current_revision = replay_repo.current_revision()
            existing_action_coverage = replay_repo.action_coverage(
                action_scope_symbols,
                start,
                end,
                current_revision,
            )
            pending_action_symbols = [
                item
                for item in action_scope_symbols
                if item not in existing_action_coverage
            ]
            action_status_counts = {
                "ready": 0,
                "ready_none": 0,
                "partial": 0,
                "unsupported": 0,
            }
            for item in existing_action_coverage.values():
                action_status_counts[item.status] += 1
            for offset in range(0, len(pending_action_symbols), batch_size):
                batch_symbols = pending_action_symbols[offset : offset + batch_size]
                action_batch = historical_evidence_provider.get_corporate_actions(
                    batch_symbols,
                    start,
                    end,
                )
                if action_batch.actions:
                    revision = replay_repo.current_revision() + 1
                    corporate_action_rows += replay_repo.upsert_corporate_actions(
                        [
                            item.model_copy(update={"dataset_revision": revision})
                            for item in action_batch.actions
                        ],
                        revision=revision,
                    )
                if action_batch.coverage:
                    revision = replay_repo.current_revision() + 1
                    corporate_action_coverage_rows += (
                        replay_repo.upsert_action_coverage(
                            action_batch.coverage,
                            revision=revision,
                        )
                    )
                if action_batch.terminal_settlements:
                    revision = replay_repo.current_revision() + 1
                    terminal_settlement_rows += (
                        replay_repo.upsert_terminal_settlements(
                            [
                                item.model_copy(update={"dataset_revision": revision})
                                for item in action_batch.terminal_settlements
                            ],
                            revision=revision,
                        )
                    )
                errors.extend(action_batch.errors[:50])
                for item in action_batch.coverage:
                    action_status_counts[item.status] += 1
            corporate_action_health = {
                "corporate_action_instruments": str(len(action_scope_symbols)),
                "corporate_action_cache_reused": str(
                    len(action_scope_symbols) - len(pending_action_symbols)
                ),
                "corporate_action_deferred_instruments": str(
                    len(symbols) - len(action_scope_symbols)
                ),
                "corporate_action_scope": (
                    "delisted_in_window"
                    if normalized_scope == "full-a-share" and len(symbols) > 500
                    else "requested_symbols"
                ),
                "corporate_action_ready": str(
                    action_status_counts["ready"]
                    + action_status_counts["ready_none"]
                ),
                "corporate_action_partial": str(action_status_counts["partial"]),
                "corporate_action_unsupported": str(
                    action_status_counts["unsupported"]
                ),
            }

        _set_backfill_phase(repo, job.job_id, "terminal_settlements")
        unresolved_terminal = [
            item.instrument_id
            for item in inventory_profiles
            if item.delisting_date is not None
            and start <= item.delisting_date <= end
            and not replay_repo.terminal_settlements(
                [item.instrument_id],
                start,
                end,
                replay_repo.current_revision(),
            )
        ]
        corporate_action_health["terminal_settlement_unresolved"] = str(
            len(unresolved_terminal)
        )
        if unresolved_terminal:
            if normalized_scope == "full-a-share":
                errors.append(
                    f"{len(unresolved_terminal)} delisted instruments have unresolved "
                    "terminal settlements"
                )
            else:
                errors.extend(
                    f"{instrument_id}: terminal settlement is unresolved"
                    for instrument_id in unresolved_terminal
                )

        _set_backfill_phase(repo, job.job_id, "replay_prices")
        for instrument_id, bars, provider_errors, cache_hit in _historical_price_batches(
            provider=provider,
            cache=cache,
            provider_mode=mode,
            instrument_ids=symbols[processed:],
            start=start,
            end=end,
            batch_size=batch_size,
            active_ranges=active_price_ranges,
        ):
            if cache_hit:
                replay_rows += _persist_replay_frame(replay_repo, bars)
                processed += 1
                succeeded += 1
                cache_reused += 1
                repo.update_historical_backfill_job(
                    job.job_id,
                    processed_symbols=processed,
                    succeeded_symbols=succeeded,
                    failed_symbols=failed,
                    rows_written=rows_written,
                    current_instrument=instrument_id,
                    errors=errors,
                )
                _checkpoint_price_backfill_health(
                    repo=repo,
                    job_id=job.job_id,
                    processed=processed,
                    total=len(symbols),
                    batch_size=batch_size,
                    cache_reused=cache_reused,
                    network_succeeded=network_succeeded,
                    retryable_failed=retryable_failed,
                    permanent_failed=permanent_failed,
                    retryable_symbols=retryable_symbols,
                    permanent_symbols=permanent_symbols,
                )
                continue

            symbol_start, symbol_end = active_price_ranges[instrument_id]
            saved = cache.save_daily_bars(mode, bars)
            cache.record_coverage(
                mode,
                instrument_id,
                symbol_start,
                symbol_end,
                saved,
            )
            replay_rows += _persist_replay_frame(replay_repo, bars)
            rows_written += saved
            processed += 1
            price_ready = cache.has_usable_coverage(
                mode,
                instrument_id,
                symbol_start,
                symbol_end,
                require_adjusted=_requires_adjustment(instrument_id),
                minimum_session_coverage=0.95,
            )
            if price_ready:
                succeeded += 1
                network_succeeded += 1
            else:
                failed += 1
                detail = (
                    provider_errors[-1]
                    if provider_errors
                    else (
                        "price coverage incomplete"
                        if saved > 0
                        else "no daily bars returned"
                    )
                )
                errors.append(f"{instrument_id}: {detail}")
                if _is_retryable_provider_failure(provider_errors):
                    retryable_failed += 1
                    if instrument_id not in retryable_symbols:
                        retryable_symbols.append(instrument_id)
                else:
                    permanent_failed += 1
                    if instrument_id not in permanent_symbols:
                        permanent_symbols.append(instrument_id)
            repo.update_historical_backfill_job(
                job.job_id,
                processed_symbols=processed,
                succeeded_symbols=succeeded,
                failed_symbols=failed,
                rows_written=rows_written,
                current_instrument=instrument_id,
                errors=errors[-100:],
            )
            _checkpoint_price_backfill_health(
                repo=repo,
                job_id=job.job_id,
                processed=processed,
                total=len(symbols),
                batch_size=batch_size,
                cache_reused=cache_reused,
                network_succeeded=network_succeeded,
                retryable_failed=retryable_failed,
                permanent_failed=permanent_failed,
                retryable_symbols=retryable_symbols,
                permanent_symbols=permanent_symbols,
            )

        if retryable_symbols:
            _set_backfill_phase(repo, job.job_id, "price_retry")
            pending_retry_symbols = list(dict.fromkeys(retryable_symbols))
            for retry_index, instrument_id in enumerate(
                pending_retry_symbols,
                start=1,
            ):
                retry_attempted += 1
                symbol_start, symbol_end = active_price_ranges[instrument_id]
                bars, provider_errors = _fetch_uncached_daily_bars(
                    provider,
                    instrument_id,
                    symbol_start,
                    symbol_end,
                )
                saved = cache.save_daily_bars(mode, bars)
                cache.record_coverage(
                    mode,
                    instrument_id,
                    symbol_start,
                    symbol_end,
                    saved,
                )
                replay_rows += _persist_replay_frame(replay_repo, bars)
                rows_written += saved
                price_ready = cache.has_usable_coverage(
                    mode,
                    instrument_id,
                    symbol_start,
                    symbol_end,
                    require_adjusted=_requires_adjustment(instrument_id),
                    minimum_session_coverage=0.95,
                )
                if price_ready:
                    retry_recovered += 1
                    network_succeeded += 1
                    succeeded += 1
                    failed = max(failed - 1, 0)
                    retryable_symbols.remove(instrument_id)
                    if instrument_id in permanent_symbols:
                        permanent_symbols.remove(instrument_id)
                        permanent_failed = max(permanent_failed - 1, 0)
                    errors = _without_instrument_errors(errors, instrument_id)
                else:
                    detail = (
                        provider_errors[-1]
                        if provider_errors
                        else "no daily bars returned after deferred retry"
                    )
                    errors = _without_instrument_errors(errors, instrument_id)
                    errors.append(f"{instrument_id}: {detail}")
                    if not _is_retryable_provider_failure(provider_errors):
                        retryable_symbols.remove(instrument_id)
                        permanent_failed += 1
                        if instrument_id not in permanent_symbols:
                            permanent_symbols.append(instrument_id)
                _update_backfill_health(
                    repo,
                    job.job_id,
                    backfill_price_retry_mode="deferred_transient_failures",
                    backfill_price_retry_attempted=str(retry_attempted),
                    backfill_price_retry_recovered=str(retry_recovered),
                    backfill_price_retry_unresolved=str(len(retryable_symbols)),
                    backfill_price_permanent_failed=str(permanent_failed),
                    backfill_price_permanent_symbols=",".join(permanent_symbols),
                    backfill_price_retryable_symbols=",".join(retryable_symbols),
                    backfill_price_retry_progress=(
                        f"{retry_index}/{len(pending_retry_symbols)}"
                    ),
                )
                repo.update_historical_backfill_job(
                    job.job_id,
                    succeeded_symbols=min(succeeded, len(symbols)),
                    failed_symbols=failed,
                    rows_written=rows_written,
                    current_instrument=instrument_id,
                    errors=errors[-100:],
                )

        if historical_evidence_provider is not None and hasattr(
            historical_evidence_provider,
            "get_benchmark_series",
        ):
            _set_backfill_phase(repo, job.job_id, "benchmark_prices")
            benchmark_ids = [
                benchmark_id
                for benchmark_id in REQUIRED_BENCHMARK_IDS
                if not cache.has_usable_coverage(
                    mode,
                    benchmark_id,
                    start,
                    end,
                    require_adjusted=False,
                    minimum_session_coverage=0.95,
                )
            ]
            benchmark_series = (
                historical_evidence_provider.get_benchmark_series(
                    benchmark_ids,
                    start,
                    end,
                )
                if benchmark_ids
                else {}
            )
            missing_benchmark_ids = [
                item for item in benchmark_ids if item not in benchmark_series
            ]
            errors.extend(
                f"{item}: no benchmark bars returned"
                for item in missing_benchmark_ids
            )
            errors.extend(
                str(error)
                for error in getattr(historical_evidence_provider, "last_errors", [])
                if str(error) not in errors
            )
            for benchmark_id, frame in benchmark_series.items():
                if not isinstance(frame, pd.DataFrame) or frame.empty:
                    errors.append(f"{benchmark_id}: no benchmark bars returned")
                    continue
                normalized = frame.copy()
                if "instrument_id" not in normalized.columns:
                    normalized["instrument_id"] = benchmark_id
                saved = cache.save_daily_bars(mode, normalized)
                cache.record_coverage(mode, benchmark_id, start, end, saved)
                benchmark_rows += saved
                replay_rows += _persist_replay_frame(replay_repo, normalized)
            reused_benchmark_ids = [
                item for item in REQUIRED_BENCHMARK_IDS if item not in benchmark_ids
            ]
            if reused_benchmark_ids:
                replay_rows += _persist_replay_frame(
                    replay_repo,
                    cache.load_daily_bars(
                        mode,
                        reused_benchmark_ids,
                        start,
                        end,
                    ),
                )

        fundamental_rows = 0
        fundamental_cache_reused = 0
        if strategy_provider is not None:
            _set_backfill_phase(repo, job.job_id, "fundamentals")
            fundamental_start = start - timedelta(days=400)
            stock_symbols = [
                symbol for symbol in symbols if _asset_type(symbol, None) == "stock"
            ]
            existing_fundamentals = repo.fundamental_snapshot_stats(
                mode,
                stock_symbols,
                end,
            )
            fundamental_symbols = []
            for symbol in stock_symbols:
                count, first_date, last_date = existing_fundamentals.get(
                    symbol,
                    (0, None, None),
                )
                profile = inventory_profile_by_id.get(symbol)
                if _fundamental_history_is_usable(
                    count=count,
                    first_date=first_date,
                    last_date=last_date,
                    start=start,
                    end=end,
                    listing_date=profile.listing_date if profile else None,
                    delisting_date=profile.delisting_date if profile else None,
                ):
                    fundamental_cache_reused += 1
                else:
                    fundamental_symbols.append(symbol)
            if fundamental_symbols:
                fundamental_batches = (
                    [fundamental_symbols]
                    if normalized_scope == "symbols"
                    else [
                        fundamental_symbols[offset : offset + batch_size]
                        for offset in range(0, len(fundamental_symbols), batch_size)
                    ]
                )
                fundamental_processed = fundamental_cache_reused
                for batch_symbols in fundamental_batches:
                    try:
                        if hasattr(
                            strategy_provider,
                            "get_fundamentals_from_cached_bars",
                        ):
                            cached_bars = cache.load_daily_bars(
                                mode,
                                batch_symbols,
                                fundamental_start,
                                end,
                            )
                            fundamentals = (
                                strategy_provider.get_fundamentals_from_cached_bars(
                                    batch_symbols,
                                    fundamental_start,
                                    end,
                                    cached_bars,
                                )
                            )
                        else:
                            fundamentals = strategy_provider.get_fundamentals(
                                batch_symbols,
                                fundamental_start,
                                end,
                            )
                        fundamental_rows += repo.upsert_fundamental_snapshots(
                            mode,
                            fundamentals,
                        )
                        errors.extend(
                            getattr(strategy_provider, "last_errors", [])[-20:]
                        )
                    except Exception as exc:
                        if normalized_scope == "symbols":
                            raise
                        errors.append(
                            f"fundamental batch {batch_symbols[0]}..{batch_symbols[-1]}: {exc}"
                        )
                    fundamental_processed += len(batch_symbols)
                    _update_backfill_health(
                        repo,
                        job.job_id,
                        backfill_fundamental_processed=str(fundamental_processed),
                        backfill_fundamental_total=str(len(stock_symbols)),
                    )
            else:
                _update_backfill_health(
                    repo,
                    job.job_id,
                    backfill_fundamental_processed=str(len(stock_symbols)),
                    backfill_fundamental_total=str(len(stock_symbols)),
                )
            final_fundamental_stats = repo.fundamental_snapshot_stats(
                mode,
                stock_symbols,
                end,
            )
            fundamental_ready = 0
            fundamental_missing: list[str] = []
            for symbol in stock_symbols:
                count, first_date, last_date = final_fundamental_stats.get(
                    symbol,
                    (0, None, None),
                )
                profile = inventory_profile_by_id.get(symbol)
                if _fundamental_history_is_usable(
                    count=count,
                    first_date=first_date,
                    last_date=last_date,
                    start=start,
                    end=end,
                    listing_date=profile.listing_date if profile else None,
                    delisting_date=profile.delisting_date if profile else None,
                ):
                    fundamental_ready += 1
                else:
                    fundamental_missing.append(symbol)
            _update_backfill_health(
                repo,
                job.job_id,
                backfill_fundamental_source=getattr(
                    strategy_provider,
                    "historical_source_name",
                    getattr(strategy_provider, "name", "unknown"),
                ),
                backfill_fundamental_point_in_time=(
                    "conservative_disclosure_deadline"
                ),
                backfill_fundamental_ready=str(fundamental_ready),
                backfill_fundamental_missing=str(len(fundamental_missing)),
                backfill_fundamental_missing_symbols=",".join(
                    fundamental_missing[:200]
                ),
            )

        evidence_data_health: dict[str, str] = {}
        evidence_counts = {
            "tradability": 0,
            "profiles": 0,
            "industries": 0,
            "index_snapshots": 0,
            "index_memberships": 0,
            "universe_snapshots": 0,
        }
        if historical_evidence_provider is not None:
            _set_backfill_phase(repo, job.job_id, "historical_evidence")
            reuse_historical_evidence = _historical_evidence_cache_is_usable(
                repo=repo,
                provider_mode=mode,
                instrument_ids=symbols,
                start=start,
                end=end,
            )
            if reuse_historical_evidence:
                evidence_bundle = HistoricalEvidenceBundle(
                    data_health={"historical_evidence_cache": "reused"}
                )
                _update_backfill_health(
                    repo,
                    job.job_id,
                    backfill_evidence_processed=str(len(symbols)),
                    backfill_evidence_total=str(len(symbols)),
                )
            elif normalized_scope == "full-a-share" and all(
                hasattr(historical_evidence_provider, method)
                for method in ("get_tradability_evidence", "get_reference_evidence")
            ):
                evidence_bundle = historical_evidence_provider.get_reference_evidence(
                    symbols,
                    start,
                    end,
                )
                if not evidence_bundle.profiles:
                    evidence_bundle.profiles = inventory_profiles
                existing_evidence = repo.historical_evidence_stats(
                    mode,
                    symbols,
                    start,
                    end,
                )
                profile_by_id = {item.instrument_id: item for item in inventory_profiles}
                pending_tradability: list[str] = []
                for symbol in symbols:
                    if _asset_type(symbol, None) != "stock":
                        continue
                    profile = profile_by_id.get(symbol)
                    symbol_start = max(
                        start,
                        profile.listing_date if profile and profile.listing_date else start,
                    )
                    symbol_end = min(
                        end,
                        profile.delisting_date if profile and profile.delisting_date else end,
                    )
                    expected = len(trading_sessions_in_range(symbol_start, symbol_end))
                    if _ratio(existing_evidence[symbol].tradability_rows, expected) < 0.95:
                        pending_tradability.append(symbol)
                evidence_processed = len(symbols) - len(pending_tradability)
                for offset in range(0, len(pending_tradability), batch_size):
                    batch_symbols = pending_tradability[offset : offset + batch_size]
                    batch_bundle = historical_evidence_provider.get_tradability_evidence(
                        batch_symbols,
                        start,
                        end,
                    )
                    batch_counts = repo.upsert_historical_evidence(mode, batch_bundle)
                    for key, value in batch_counts.items():
                        evidence_counts[key] += value
                    errors.extend(batch_bundle.errors[-20:])
                    evidence_processed += len(batch_symbols)
                    _update_backfill_health(
                        repo,
                        job.job_id,
                        backfill_evidence_processed=str(evidence_processed),
                        backfill_evidence_total=str(len(symbols)),
                    )
            else:
                evidence_bundle = historical_evidence_provider.get_evidence(
                    symbols,
                    start,
                    end,
                )
            _infer_etf_tradability_from_cached_bars(
                cache=cache,
                provider_mode=mode,
                instrument_ids=symbols,
                start=start,
                end=end,
                bundle=evidence_bundle,
            )
            bundle_counts = repo.upsert_historical_evidence(mode, evidence_bundle)
            for key, value in bundle_counts.items():
                evidence_counts[key] += value
            evidence_counts["universe_snapshots"] = 0
            if not reuse_historical_evidence:
                evidence_counts["universe_snapshots"] = (
                    repo.upsert_historical_universe_snapshots(
                        evidence_bundle.profiles,
                        [start, *historical_snapshot_dates(start, end)],
                    )
                )
            errors.extend(evidence_bundle.errors[:50])
            evidence_data_health.update(evidence_bundle.data_health)
            for key, value in evidence_counts.items():
                evidence_data_health[f"historical_evidence_{key}"] = str(value)

        _set_backfill_phase(repo, job.job_id, "replay_coverage")
        manifest = build_historical_coverage_manifest(
            repo=repo,
            cache=cache,
            provider_mode=mode,
            instrument_ids=symbols,
            start=start,
            end=end,
        )
        incomplete_price = [
            item
            for item in manifest.instruments
            if item.bar_coverage_ratio < 0.95
            or (
                _requires_adjustment(item.instrument_id)
                and (item.adjustment_coverage_ratio or 0) < 0.95
            )
        ]
        incomplete_ids = {item.instrument_id for item in incomplete_price}
        for item in incomplete_price:
            if any(error.startswith(f"{item.instrument_id}:") for error in errors):
                continue
            errors.append(
                f"{item.instrument_id}: price coverage incomplete "
                f"(bars={item.bar_coverage_ratio:.1%}, "
                f"adjusted={(item.adjustment_coverage_ratio or 0):.1%})"
            )
        processed = len(symbols)
        succeeded = len(symbols) - len(incomplete_ids)
        failed = len(incomplete_ids)
        terminal_status = (
            "succeeded"
            if failed == 0 and not errors
            else "succeeded_with_errors"
        )
        current_job = repo.get_historical_backfill_job(job.job_id)
        checkpoint_health = dict(current_job.data_health) if current_job else {}
        data_health = {
            **checkpoint_health,
            **manifest.data_health,
            **evidence_data_health,
            **corporate_action_health,
            "backfill_phase": "complete",
            "backfill_cache_reused": str(cache_reused),
            "backfill_price_retry_mode": "missing_only",
            "backfill_price_cache_reused": str(cache_reused),
            "backfill_price_network_succeeded": str(network_succeeded),
            "backfill_price_retryable_failed": str(retryable_failed),
            "backfill_price_permanent_failed": str(permanent_failed),
            "backfill_price_permanent_symbols": ",".join(permanent_symbols),
            "backfill_price_retry_attempted": str(retry_attempted),
            "backfill_price_retry_recovered": str(retry_recovered),
            "backfill_price_retry_unresolved": str(len(retryable_symbols)),
            "backfill_price_retryable_symbols": ",".join(retryable_symbols),
            "backfill_price_remaining": "0",
            "backfill_rows_written": str(rows_written),
            "backfill_inventory_rows": str(inventory_rows),
            "backfill_inventory_recovered": str(inventory_recovered).lower(),
            "backfill_replay_rows": str(replay_rows),
            "backfill_benchmark_rows": str(benchmark_rows),
            "backfill_fundamental_rows": str(fundamental_rows),
            "backfill_fundamental_cache_reused": str(
                fundamental_cache_reused
            ),
            "backfill_scope": normalized_scope,
            "backfill_batch_size": str(batch_size),
            "backfill_rule_rows": str(rule_rows),
            "backfill_fee_rule_rows": str(fee_rule_rows),
            "backfill_instrument_rule_rows": str(instrument_rule_rows),
            "backfill_corporate_action_rows": str(corporate_action_rows),
            "backfill_corporate_action_coverage_rows": str(
                corporate_action_coverage_rows
            ),
            "backfill_terminal_settlement_rows": str(terminal_settlement_rows),
            **{
                f"backfill_evidence_{key}": str(value)
                for key, value in evidence_counts.items()
            },
        }
        completed = repo.update_historical_backfill_job(
            job.job_id,
            status=terminal_status,
            processed_symbols=processed,
            succeeded_symbols=succeeded,
            failed_symbols=failed,
            rows_written=rows_written,
            fundamental_rows_written=fundamental_rows,
            errors=errors[-100:],
            data_health=data_health,
        )
        if completed is None:
            raise RuntimeError(f"historical backfill job disappeared: {job.job_id}")
        return HistoricalBackfillResult(job=completed, manifest=manifest)
    except Exception as exc:
        failed_job = repo.get_historical_backfill_job(job.job_id)
        failed_health = dict(failed_job.data_health) if failed_job is not None else {}
        failed_health["backfill_phase"] = "failed"
        failed_job = repo.update_historical_backfill_job(
            job.job_id,
            status="failed",
            processed_symbols=processed,
            succeeded_symbols=succeeded,
            failed_symbols=max(failed, processed - succeeded),
            rows_written=rows_written,
            errors=[*errors[-99:], str(exc)],
            data_health=failed_health,
        )
        if failed_job is None:
            raise RuntimeError(
                f"historical backfill job disappeared: {job.job_id}"
            ) from exc
        manifest = build_historical_coverage_manifest(
            repo=repo,
            cache=cache,
            provider_mode=mode,
            instrument_ids=symbols,
            start=start,
            end=end,
        )
        manifest.data_health.update(
            {
                "backfill_job_status": "failed",
                "backfill_phase": "failed",
                "backfill_error": str(exc),
            }
        )
        raise HistoricalBackfillFailed(
            HistoricalBackfillResult(job=failed_job, manifest=manifest),
            str(exc),
        ) from exc


def _set_backfill_phase(
    repo: QagentRepository,
    job_id: str,
    phase: str,
) -> None:
    job = repo.get_historical_backfill_job(job_id)
    data_health = dict(job.data_health) if job is not None else {}
    data_health["backfill_phase"] = phase
    repo.update_historical_backfill_job(job_id, data_health=data_health)


def _update_backfill_health(
    repo: QagentRepository,
    job_id: str,
    **values: str,
) -> None:
    job = repo.get_historical_backfill_job(job_id)
    data_health = dict(job.data_health) if job is not None else {}
    data_health.update(values)
    repo.update_historical_backfill_job(job_id, data_health=data_health)


def _checkpoint_price_backfill_health(
    *,
    repo: QagentRepository,
    job_id: str,
    processed: int,
    total: int,
    batch_size: int,
    cache_reused: int,
    network_succeeded: int,
    retryable_failed: int,
    permanent_failed: int,
    retryable_symbols: list[str],
    permanent_symbols: list[str],
) -> None:
    if processed < total and processed % max(1, batch_size) != 0:
        return
    _update_backfill_health(
        repo,
        job_id,
        backfill_price_retry_mode="missing_only",
        backfill_price_cache_reused=str(cache_reused),
        backfill_price_network_succeeded=str(network_succeeded),
        backfill_price_retryable_failed=str(retryable_failed),
        backfill_price_permanent_failed=str(permanent_failed),
        backfill_price_permanent_symbols=",".join(permanent_symbols),
        backfill_price_retry_unresolved=str(len(retryable_symbols)),
        backfill_price_retryable_symbols=",".join(retryable_symbols),
        backfill_price_remaining=str(max(total - processed, 0)),
    )


def _restored_retryable_symbols(job: HistoricalBackfillJobRecord) -> list[str]:
    persisted = [
        item.strip()
        for item in job.data_health.get(
            "backfill_price_retryable_symbols",
            "",
        ).split(",")
        if item.strip()
    ]
    for error in job.errors:
        instrument_id, separator, detail = error.partition(": ")
        if (
            separator
            and instrument_id.startswith("CN:")
            and _is_retryable_provider_failure([detail])
            and instrument_id not in persisted
        ):
            persisted.append(instrument_id)
    return persisted


def _without_instrument_errors(errors: list[str], instrument_id: str) -> list[str]:
    prefix = f"{instrument_id}:"
    return [error for error in errors if not error.startswith(prefix)]


def _is_retryable_provider_failure(errors: list[str]) -> bool:
    detail = " ".join(errors).lower()
    if not detail:
        return False
    return any(
        token in detail
        for token in (
            "aborted",
            "circuit",
            "connection",
            "deadline",
            "disconnect",
            "disconnected",
            "fallback",
            "login failed",
            "network",
            "codec",
            "rate limit",
            "remote",
            "skipped after",
            "source unavailable",
            "temporary",
            "temporarily",
            "timed out",
            "timeout",
            "too many requests",
            "接收数据异常",
            "网络",
        )
    )


def _fundamental_history_is_usable(
    *,
    count: int,
    first_date: date | None,
    last_date: date | None,
    start: date,
    end: date,
    listing_date: date | None,
    delisting_date: date | None,
) -> bool:
    if count <= 0 or first_date is None or last_date is None:
        return False
    active_start = max(start, listing_date or start)
    active_end = min(end, delisting_date or end)
    if active_end < active_start:
        return True
    first_cutoff = start if listing_date is None or listing_date <= start else active_end
    freshness_cutoff = active_end - timedelta(days=180)
    return first_date <= first_cutoff and last_date >= freshness_cutoff


def _historical_evidence_cache_is_usable(
    *,
    repo: QagentRepository,
    provider_mode: str,
    instrument_ids: list[str],
    start: date,
    end: date,
) -> bool:
    snapshots = historical_snapshot_dates(start, end)
    expected_index_snapshots = len(snapshots) * len(INDEX_QUERIES)
    index_stats = repo.historical_index_snapshot_stats(provider_mode, start, end)
    if (
        expected_index_snapshots > 0
        and index_stats.ready_snapshots < expected_index_snapshots
    ):
        return False
    replay_repo = ReplayEvidenceRepository(repo.session_factory, provider_mode)
    revision = replay_repo.current_revision()
    if revision > 0:
        try:
            replay_repo.memberships_as_of(instrument_ids[:1], end, revision)
        except ReplayEvidenceUnavailable:
            # A previous interrupted reference-data write can leave a ready
            # snapshot whose declared membership count does not match storage.
            # Force a fresh reference snapshot instead of reusing corrupt data.
            return False
    evidence = repo.historical_evidence_stats(
        provider_mode,
        instrument_ids,
        start,
        end,
    )
    universes = repo.tradable_universe_snapshot_stats(
        instrument_ids,
        start,
        end,
    )
    expected_sessions = len(trading_sessions_in_range(start, end))
    first_snapshot = snapshots[0] if snapshots else end
    for instrument_id in instrument_ids:
        item = evidence[instrument_id]
        if item.profile_rows == 0:
            return False
        universe_count, first_universe, _ = universes.get(
            instrument_id,
            (0, None, None),
        )
        if universe_count == 0:
            return False
        if (
            (item.listing_date is None or item.listing_date <= start)
            and (first_universe is None or first_universe > start)
        ):
            return False
        if _asset_type(instrument_id, None) != "stock":
            continue
        if _ratio(item.tradability_rows, expected_sessions) < 0.95:
            return False
        if (
            item.industry_rows == 0
            or item.first_industry_date is None
            or item.first_industry_date > first_snapshot
        ):
            return False
    return True


def build_historical_coverage_manifest(
    *,
    repo: QagentRepository,
    cache: MarketDataCacheRepository,
    provider_mode: str,
    instrument_ids: list[str],
    start: date,
    end: date,
) -> HistoricalCoverageManifest:
    mode = provider_mode.strip().lower()
    symbols = sorted(set(instrument_ids))
    sessions = trading_sessions_in_range(start, end)
    expected_sessions = len(sessions)
    benchmark_bars = cache.load_daily_bars(
        mode,
        list(REQUIRED_BENCHMARK_IDS),
        start,
        end,
    )
    fundamentals = repo.fundamental_snapshot_stats(mode, symbols, end)
    universes = repo.tradable_universe_snapshot_stats(symbols, start, end)
    evidence = repo.historical_evidence_stats(mode, symbols, start, end)
    index_evidence = repo.historical_index_snapshot_stats(mode, start, end)
    catalog = {
        item.instrument_id: item
        for item in repo.list_tradable_instruments(limit=max(len(symbols) * 2, 10_000))
        if item.instrument_id in symbols
    }
    coverage: list[HistoricalInstrumentCoverage] = []

    for instrument_id in symbols:
        evidence_item = evidence[instrument_id]
        active_dates = {
            session
            for session in sessions
            if (evidence_item.listing_date is None or session >= evidence_item.listing_date)
            and (
                evidence_item.delisting_date is None
                or session <= evidence_item.delisting_date
            )
        }
        instrument_expected_sessions = len(active_dates)
        group = cache.load_daily_bars(
            mode,
            [instrument_id],
            start,
            end,
        )
        valid_group = group[group["trade_date"].isin(active_dates)]
        trade_dates = sorted(set(valid_group["trade_date"].tolist()))
        bar_rows = len(trade_dates)
        bar_ratio = _ratio(bar_rows, instrument_expected_sessions)
        adjusted_rows = _adjusted_session_count(valid_group)
        adjustment_ratio = _ratio(adjusted_rows, bar_rows) if bar_rows else None
        asset_type = _asset_type(instrument_id, catalog.get(instrument_id))
        fundamental_count, first_fundamental, last_fundamental = fundamentals.get(
            instrument_id,
            (0, None, None),
        )
        universe_count, first_universe, last_universe = universes.get(
            instrument_id,
            (0, None, None),
        )
        issues = _coverage_issues(
            instrument_id=instrument_id,
            asset_type=asset_type,
            bar_ratio=bar_ratio,
            adjustment_ratio=adjustment_ratio,
            fundamental_count=fundamental_count,
            first_fundamental=first_fundamental,
            first_universe=first_universe,
            start=start,
            unexpected_rows=max(len(group) - len(valid_group), 0),
            expected_sessions=instrument_expected_sessions,
            evidence_item=evidence_item,
        )
        status = "missing" if bar_rows == 0 else ("ready" if not issues else "partial")
        coverage.append(
            HistoricalInstrumentCoverage(
                instrument_id=instrument_id,
                asset_type=asset_type,
                expected_sessions=instrument_expected_sessions,
                bar_rows=bar_rows,
                bar_coverage_ratio=bar_ratio,
                adjusted_rows=adjusted_rows,
                adjustment_coverage_ratio=adjustment_ratio,
                adjustment_types=sorted(
                    {
                        str(value)
                        for value in valid_group.get("adjustment_type", pd.Series(dtype=str))
                        .dropna()
                        .tolist()
                        if str(value)
                    }
                ),
                source_providers=sorted(
                    {
                        str(value)
                        for value in valid_group.get("provider", pd.Series(dtype=str))
                        .dropna()
                        .tolist()
                        if str(value)
                    }
                ),
                first_trade_date=trade_dates[0] if trade_dates else None,
                last_trade_date=trade_dates[-1] if trade_dates else None,
                fundamental_rows=fundamental_count,
                first_fundamental_date=first_fundamental,
                last_fundamental_date=last_fundamental,
                universe_snapshot_rows=universe_count,
                first_universe_date=first_universe,
                last_universe_date=last_universe,
                tradability_rows=evidence_item.tradability_rows,
                tradability_coverage_ratio=_ratio(
                    evidence_item.tradability_rows,
                    instrument_expected_sessions,
                ),
                first_tradability_date=evidence_item.first_tradability_date,
                last_tradability_date=evidence_item.last_tradability_date,
                suspended_rows=evidence_item.suspended_rows,
                st_rows=evidence_item.st_rows,
                profile_rows=evidence_item.profile_rows,
                listing_date=evidence_item.listing_date,
                delisting_date=evidence_item.delisting_date,
                listing_status=evidence_item.listing_status,
                industry_rows=evidence_item.industry_rows,
                industries=evidence_item.industries,
                benchmark_membership_rows=evidence_item.benchmark_membership_rows,
                benchmark_ids=evidence_item.benchmark_ids,
                status=status,
                issues=issues,
            )
        )

    expected_index_snapshots = len(historical_snapshot_dates(start, end)) * len(
        INDEX_QUERIES
    )
    summary = _coverage_summary(
        coverage,
        index_evidence=index_evidence,
        expected_index_snapshots=expected_index_snapshots,
    )
    benchmark_price_rows = len(benchmark_bars)
    benchmark_price_ready = sum(
        _ratio(
            len(
                set(
                    benchmark_bars.loc[
                        benchmark_bars["instrument_id"].eq(benchmark_id),
                        "trade_date",
                    ].tolist()
                )
            ),
            expected_sessions,
        )
        >= 0.95
        for benchmark_id in REQUIRED_BENCHMARK_IDS
    )
    return HistoricalCoverageManifest(
        provider_mode=mode,
        start_date=start,
        end_date=end,
        generated_at=datetime.now(timezone.utc),
        summary=summary,
        instruments=coverage,
        data_health={
            "historical_manifest": "ready",
            "historical_expected_sessions": str(expected_sessions),
            "historical_instruments": str(len(symbols)),
            "historical_ready_instruments": str(summary.ready_instruments),
            "historical_partial_instruments": str(summary.partial_instruments),
            "historical_missing_instruments": str(summary.missing_instruments),
            "historical_bar_coverage": f"{summary.average_bar_coverage_ratio:.4f}",
            "historical_adjustment_coverage": (
                f"{summary.average_adjustment_coverage_ratio:.4f}"
                if summary.average_adjustment_coverage_ratio is not None
                else "missing"
            ),
            "historical_tradability_ready": str(
                summary.tradability_ready_instruments
            ),
            "historical_profile_ready": str(summary.profile_ready_instruments),
            "historical_industry_ready": str(summary.industry_ready_instruments),
            "historical_benchmark_snapshots": str(summary.benchmark_snapshot_rows),
            "historical_benchmark_ready": str(summary.benchmark_ready_snapshots),
            "historical_benchmark_failed": str(summary.benchmark_failed_snapshots),
            "historical_benchmark_coverage": f"{summary.benchmark_coverage_ratio:.4f}",
            "historical_benchmark_price_rows": str(benchmark_price_rows),
            "historical_benchmark_price_ready": (
                f"{benchmark_price_ready}/{len(REQUIRED_BENCHMARK_IDS)}"
            ),
            "historical_benchmark_price_coverage": (
                f"{benchmark_price_ready / len(REQUIRED_BENCHMARK_IDS):.4f}"
            ),
        },
    )


def _coverage_issues(
    *,
    instrument_id: str,
    asset_type: str,
    bar_ratio: float,
    adjustment_ratio: float | None,
    fundamental_count: int,
    first_fundamental: date | None,
    first_universe: date | None,
    start: date,
    unexpected_rows: int,
    expected_sessions: int,
    evidence_item,
) -> list[str]:
    issues: list[str] = []
    listed_after_start = bool(
        evidence_item.listing_date and evidence_item.listing_date > start
    )
    if bar_ratio < 0.95:
        issues.append("bar_coverage_below_95pct")
    if _requires_adjustment(instrument_id) and (adjustment_ratio or 0) < 0.95:
        issues.append("adjustment_coverage_below_95pct")
    if asset_type == "stock" and fundamental_count == 0:
        issues.append("fundamentals_missing")
    elif (
        asset_type == "stock"
        and not listed_after_start
        and (first_fundamental is None or first_fundamental > start)
    ):
        issues.append("fundamental_history_incomplete")
    if first_universe is None or (first_universe > start and not listed_after_start):
        issues.append("historical_universe_incomplete")
    if _ratio(evidence_item.tradability_rows, expected_sessions) < 0.95:
        issues.append("tradability_coverage_below_95pct")
    if evidence_item.profile_rows == 0:
        issues.append("instrument_profile_missing")
    if asset_type == "stock" and evidence_item.industry_rows == 0:
        issues.append("historical_industry_missing")
    if unexpected_rows:
        issues.append("non_session_bar_rows")
    return issues


def _coverage_summary(
    items: list[HistoricalInstrumentCoverage],
    *,
    index_evidence: HistoricalIndexCoverageStats,
    expected_index_snapshots: int,
) -> HistoricalCoverageSummary:
    adjustment_ratios = [
        item.adjustment_coverage_ratio
        for item in items
        if item.adjustment_coverage_ratio is not None
    ]
    return HistoricalCoverageSummary(
        total_instruments=len(items),
        ready_instruments=sum(item.status == "ready" for item in items),
        partial_instruments=sum(item.status == "partial" for item in items),
        missing_instruments=sum(item.status == "missing" for item in items),
        bar_ready_instruments=sum(item.bar_coverage_ratio >= 0.95 for item in items),
        adjusted_ready_instruments=sum(
            (item.adjustment_coverage_ratio or 0) >= 0.95 for item in items
        ),
        fundamental_ready_instruments=sum(item.fundamental_rows > 0 for item in items),
        universe_ready_instruments=sum(
            "historical_universe_incomplete" not in item.issues for item in items
        ),
        tradability_ready_instruments=sum(
            item.tradability_coverage_ratio >= 0.95 for item in items
        ),
        profile_ready_instruments=sum(item.profile_rows > 0 for item in items),
        industry_ready_instruments=sum(
            item.asset_type != "stock" or item.industry_rows > 0 for item in items
        ),
        benchmark_snapshot_rows=index_evidence.total_snapshots,
        benchmark_ready_snapshots=index_evidence.ready_snapshots,
        benchmark_failed_snapshots=index_evidence.failed_snapshots,
        benchmark_coverage_ratio=_ratio(
            index_evidence.ready_snapshots,
            expected_index_snapshots,
        ),
        average_bar_coverage_ratio=_average(
            [item.bar_coverage_ratio for item in items]
        ),
        average_adjustment_coverage_ratio=(
            _average(adjustment_ratios) if adjustment_ratios else None
        ),
    )


def _adjusted_session_count(frame: pd.DataFrame) -> int:
    if frame.empty or "adjusted_close" not in frame.columns:
        return 0
    adjusted = frame[
        frame["adjusted_close"].notna()
        & frame.get("adjustment_type", pd.Series(index=frame.index, dtype=object)).notna()
    ]
    return len(set(adjusted["trade_date"].tolist()))


def _asset_type(instrument_id: str, catalog_item) -> str:
    if catalog_item is not None:
        return catalog_item.asset_type
    symbol = instrument_id.split(":", 1)[-1]
    if symbol.endswith(".IDX"):
        return "index"
    if symbol.startswith(("15", "16", "51", "52", "56", "58")):
        return "etf"
    return "stock"


def _requires_adjustment(instrument_id: str) -> bool:
    return instrument_id.startswith("CN:") and not instrument_id.endswith(".IDX")


def _persist_replay_frame(
    repository: ReplayEvidenceRepository,
    frame: pd.DataFrame,
) -> int:
    if frame.empty:
        return 0
    revision = repository.current_revision()
    bars = _replay_bars_from_frame(frame, repository.provider_mode, revision + 1)
    if not bars:
        return 0
    instrument_ids = sorted({item.instrument_id for item in bars})
    start = min(item.trade_date for item in bars)
    end = max(item.trade_date for item in bars)
    existing = repository.replay_bars(instrument_ids, start, end, revision)
    if _replay_bar_semantics(existing) == _replay_bar_semantics(bars):
        return 0
    repository.upsert_replay_bars(bars, revision=revision + 1)
    return len(bars)


def _replay_bars_from_frame(
    frame: pd.DataFrame,
    provider_mode: str,
    revision: int,
) -> list[HistoricalReplayBar]:
    fetched_at = datetime.now(timezone.utc)
    result: list[HistoricalReplayBar] = []
    for row in frame.to_dict(orient="records"):
        raw_open = _decimal_value(row.get("open"), scale=8)
        raw_high = _decimal_value(row.get("high"), scale=8)
        raw_low = _decimal_value(row.get("low"), scale=8)
        raw_close = _decimal_value(row.get("close"), scale=8)
        volume = _decimal_value(row.get("volume"), scale=4)
        if None in (raw_open, raw_high, raw_low, raw_close, volume):
            continue
        adjusted_close = _decimal_value(row.get("adjusted_close"), scale=8)
        factor = _decimal_value(row.get("adjustment_factor"), scale=12)
        if factor is None and adjusted_close is not None and raw_close != 0:
            factor = (adjusted_close / raw_close).quantize(Decimal("0.000000000001"))

        def adjusted_price(name: str, raw_value: Decimal) -> Decimal | None:
            explicit = _decimal_value(row.get(name), scale=8)
            if explicit is not None:
                return explicit
            if factor is None or adjusted_close is None:
                return None
            return (raw_value * factor).quantize(Decimal("0.00000001"))

        instrument_id = str(row.get("instrument_id") or "").strip()
        trade_date = pd.to_datetime(row.get("trade_date"), errors="coerce")
        if not instrument_id or pd.isna(trade_date):
            continue
        result.append(
            HistoricalReplayBar(
                provider_mode=provider_mode,
                instrument_id=instrument_id,
                trade_date=trade_date.date(),
                raw_open=raw_open,
                raw_high=raw_high,
                raw_low=raw_low,
                raw_close=raw_close,
                adjusted_open=adjusted_price("adjusted_open", raw_open),
                adjusted_high=adjusted_price("adjusted_high", raw_high),
                adjusted_low=adjusted_price("adjusted_low", raw_low),
                adjusted_close=adjusted_close,
                volume=volume,
                turnover=_decimal_value(row.get("turnover"), scale=4),
                adjustment_factor=factor,
                adjustment_mode=str(row.get("adjustment_type") or "unadjusted"),
                source_provider=str(row.get("provider") or provider_mode).strip().lower(),
                dataset_revision=revision,
                fetched_at=fetched_at,
            )
        )
    return result


def _replay_bar_semantics(bars: list[HistoricalReplayBar]) -> list[dict[str, object]]:
    return sorted(
        (
            item.model_dump(exclude={"dataset_revision", "fetched_at"})
            for item in bars
        ),
        key=lambda item: (
            str(item["instrument_id"]),
            str(item["trade_date"]),
            str(item["source_provider"]),
        ),
    )


def _decimal_value(value: object, *, scale: int) -> Decimal | None:
    try:
        if value is None or pd.isna(value):
            return None
        decimal_value = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    if not decimal_value.is_finite():
        return None
    return decimal_value.quantize(Decimal(1).scaleb(-scale))


def _historical_price_batches(
    *,
    provider: MarketDataProvider,
    cache: MarketDataCacheRepository,
    provider_mode: str,
    instrument_ids: list[str],
    start: date,
    end: date,
    batch_size: int,
    active_ranges: dict[str, tuple[date, date]],
):
    source = getattr(provider, "provider", provider)
    batch_getter = getattr(source, "get_historical_daily_bars", None)
    network_batch_size = min(max(1, batch_size), 5)
    for offset in range(0, len(instrument_ids), network_batch_size):
        batch = instrument_ids[offset : offset + network_batch_size]
        cached: dict[str, pd.DataFrame] = {}
        missing: list[str] = []
        for instrument_id in batch:
            symbol_start, symbol_end = active_ranges.get(
                instrument_id,
                (start, end),
            )
            if cache.has_usable_coverage(
                provider_mode,
                instrument_id,
                symbol_start,
                symbol_end,
                require_adjusted=_requires_adjustment(instrument_id),
                minimum_session_coverage=0.95,
            ):
                cached[instrument_id] = cache.load_daily_bars(
                    provider_mode,
                    [instrument_id],
                    symbol_start,
                    symbol_end,
                )
            else:
                missing.append(instrument_id)

        fetched = pd.DataFrame()
        batch_errors: list[str] = []
        if missing and batch_getter is not None:
            try:
                fetched = batch_getter(missing, start, end)
                batch_errors = list(getattr(source, "last_errors", []))
            except Exception as exc:
                batch_errors = [
                    f"{instrument_id}: historical batch: {exc}"
                    for instrument_id in missing
                ]

        for instrument_id in batch:
            if instrument_id in cached:
                yield instrument_id, cached[instrument_id], [], True
                continue
            symbol_start, symbol_end = active_ranges.get(
                instrument_id,
                (start, end),
            )
            if batch_getter is None:
                bars, provider_errors = _fetch_uncached_daily_bars(
                    provider,
                    instrument_id,
                    symbol_start,
                    symbol_end,
                )
            else:
                bars = (
                    fetched.loc[fetched["instrument_id"].eq(instrument_id)].copy()
                    if not fetched.empty and "instrument_id" in fetched.columns
                    else pd.DataFrame()
                )
                provider_errors = _errors_for_instrument(
                    batch_errors,
                    instrument_id,
                )
                if bars.empty and not provider_errors:
                    provider_errors = [
                        "batch provider returned no rows; retry with provider fallback"
                    ]
            yield instrument_id, bars, provider_errors, False


def _errors_for_instrument(errors: list[str], instrument_id: str) -> list[str]:
    prefix = f"{instrument_id}:"
    return [
        error[len(prefix) :].strip()
        for error in errors
        if error.startswith(prefix)
    ]


def _fetch_uncached_daily_bars(
    provider: MarketDataProvider,
    instrument_id: str,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, list[str]]:
    # A backfill reaches this path only after rejecting the current cache span.
    # Bypass a cache decorator so stale unadjusted rows cannot mask a required refresh.
    source = getattr(provider, "provider", provider)
    latest_errors: list[str] = []
    bars = pd.DataFrame()
    maximum_attempts = 4
    for attempt in range(1, maximum_attempts + 1):
        bars = source.get_daily_bars([instrument_id], start, end)
        latest_errors = list(getattr(source, "last_errors", []))
        if _bar_result_covers_requested_sessions(
            bars,
            instrument_id,
            start,
            end,
        ):
            return bars, latest_errors
        if bars.empty and not latest_errors:
            return bars, latest_errors
        if attempt < maximum_attempts:
            retry_after = _provider_retry_delay_seconds(source, instrument_id)
            sleep(max(0.2 * attempt, retry_after + 0.05))
    return bars, latest_errors


def _provider_retry_delay_seconds(source: object, instrument_id: str) -> float:
    retry_after = getattr(source, "source_circuit_retry_after_seconds", None)
    if retry_after is None:
        return 0.0
    try:
        return min(max(float(retry_after(instrument_id)), 0.0), 2.0)
    except (TypeError, ValueError):
        return 0.0


def _bar_result_covers_requested_sessions(
    bars: pd.DataFrame,
    instrument_id: str,
    start: date,
    end: date,
) -> bool:
    if bars.empty:
        return False
    if not instrument_id.startswith("CN:"):
        return True
    expected_sessions = len(trading_sessions_in_range(start, end))
    if expected_sessions <= 0:
        return True
    rows = bars[bars["instrument_id"].eq(instrument_id)]
    session_rows = len(set(rows["trade_date"].tolist()))
    if session_rows / expected_sessions < 0.95:
        return False
    if not _requires_adjustment(instrument_id):
        return True
    return _adjusted_session_count(rows) / expected_sessions >= 0.95


def _infer_etf_tradability_from_cached_bars(
    *,
    cache: MarketDataCacheRepository,
    provider_mode: str,
    instrument_ids: list[str],
    start: date,
    end: date,
    bundle: HistoricalEvidenceBundle,
) -> None:
    etf_ids = [
        instrument_id
        for instrument_id in instrument_ids
        if _asset_type(instrument_id, None) == "etf"
    ]
    if not etf_ids:
        return
    bars = cache.load_daily_bars(provider_mode, etf_ids, start, end)
    if bars.empty:
        return
    existing = {
        (point.instrument_id, point.trade_date)
        for point in bundle.tradability
    }
    for row in bars.itertuples(index=False):
        key = (row.instrument_id, row.trade_date)
        if key in existing:
            continue
        bundle.tradability.append(
            HistoricalTradabilityPoint(
                instrument_id=row.instrument_id,
                trade_date=row.trade_date,
                trading_status="trading",
                is_st=False,
                provider="market_bar_inference",
            )
        )
        existing.add(key)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(min(numerator / denominator, 1.0), 4)


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0
