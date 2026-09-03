from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, Field

from qagent.backtesting.baseline_challenger import (
    BASELINE_CHALLENGER_VERSION,
    MIN_BASELINE_TRAINING_SAMPLES,
    BaselineCandidate,
    BaselineDecision,
)
from qagent.db import create_session_factory, initialize_database
from qagent.domain.models import OpportunityCard, SectorStrength
from qagent.factors.engine import build_factor_feature_snapshot, rerank_factor_rankings
from qagent.factors.models import FactorRanking
from qagent.features import FeatureSnapshot, feature_snapshot_data_health
from qagent.historical_evidence.providers import REQUIRED_BENCHMARK_IDS
from qagent.jobs.daily_scan import DailyScanResult, ScanItem, run_daily_scan
from qagent.market.a_share_state import (
    AShareMarketState,
    AShareStateObservation,
    AShareStateSnapshot,
    advance_a_share_state,
)
from qagent.market.benchmark_trend import (
    BenchmarkTrendSnapshot,
    benchmark_trend_data_health,
    build_benchmark_trend_snapshot,
)
from qagent.market.benchmarks import CN_BENCHMARKS
from qagent.market.calendars import trading_sessions_in_range
from qagent.market.astock_enhanced import (
    AShareEnhancedProvider,
    EmptyAShareEnhancedDataProvider,
    apply_a_share_enhanced_to_cards,
    build_a_share_enhanced_provider,
    summarize_a_share_enhanced_snapshots,
)
from qagent.market.tradable import load_cn_tradable_instruments
from qagent.monitoring.drift import (
    DriftSnapshotMetadata,
    compare_feature_snapshots,
)
from qagent.monitoring.signal_monitor import build_signal_monitor_center
from qagent.paper_trading.engine import (
    build_paper_daily_report,
    build_paper_ledger,
    build_paper_validation,
)
from qagent.providers.factory import build_market_data_provider
from qagent.providers.fuyao import reset_fuyao_telemetry
from qagent.recommendations.portfolio import build_portfolio_plan
from qagent.recommendations.brief import apply_recommendation_briefs
from qagent.recommendations.feedback import (
    authenticated_ranking_v3_paper_trade_ids,
    build_recent_recommendation_feedback_center,
    recommendation_feedback_data_health,
)
from qagent.recommendations.governance import (
    CardStrategyGovernance,
    apply_final_recommendation_policy,
    governed_card_payloads,
    load_latest_walk_forward_validation,
    load_strategy_governance_context,
    recommendation_policy_data_health,
)
from qagent.recommendations.probability import probability_calibration_data_health
from qagent.recommendations.quality_gate import (
    recommendation_quality_data_health,
    recommendation_score_weights,
)
from qagent.recommendations.rotation import sort_recommendation_cards
from qagent.recommendations.strategy_configuration import (
    build_paper_strategy_configuration,
)
from qagent.recommendations.selection import (
    select_strategy_diversified,
    strategy_concentration,
)
from qagent.research.action_center import build_manual_action_center
from qagent.research.decision_quality import build_decision_quality_center
from qagent.research.market_intelligence import (
    build_market_intelligence_center,
)
from qagent.research.operational_readiness import build_operational_readiness_center
from qagent.research.factor_shadow import score_factor_shadow_runs_with_legacy_retirement
from qagent.research.paper_calibration_shadow import (
    PaperCalibrationShadowReport,
    build_paper_calibration_shadow_report,
)
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.storage.repository import (
    paper_model_cohort_from_data_health,
    QagentRepository,
    TradableCatalogSummary,
)
from qagent.storage.paper import PaperTradingRepository
from qagent.strategies.models import StrategyHealth, StrategyHealthPoint
from qagent.strategy_data.providers import StoredFundamentalStrategyDataProvider


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
    previous = repo.tradable_catalog_summary()
    catalog = load_cn_tradable_instruments(
        include_full_etfs=include_full_etfs,
        use_cache=use_cache,
    )
    rejection_reasons = _tradable_catalog_sync_rejection_reasons(
        previous,
        catalog,
        include_full_etfs=include_full_etfs,
    )
    if rejection_reasons:
        return TradableCatalogSyncResult(
            summary=previous,
            data_health={
                **catalog.data_health,
                "tradable_catalog": "sqlite",
                "tradable_sync_status": "retained_previous",
                "tradable_sync_rejection_reasons": " | ".join(rejection_reasons),
                "tradable_sync_previous_total": str(previous.total_count),
                "tradable_sync_candidate_total": str(len(catalog.items)),
                "tradable_synced": "0",
            },
        )
    summary = repo.replace_tradable_instruments(catalog.items, catalog.data_health)
    return TradableCatalogSyncResult(
        summary=summary,
        data_health={
            **catalog.data_health,
            "tradable_catalog": "sqlite",
            "tradable_sync_status": "updated",
            "tradable_sync_previous_total": str(previous.total_count),
            "tradable_sync_candidate_total": str(len(catalog.items)),
            "tradable_synced": str(summary.total_count),
        },
    )


def _tradable_catalog_sync_rejection_reasons(
    previous: TradableCatalogSummary,
    catalog,
    *,
    include_full_etfs: bool,
) -> list[str]:
    if previous.total_count == 0:
        return []

    stock_count = sum(item.asset_type == "stock" for item in catalog.items)
    etf_count = sum(item.asset_type == "etf" for item in catalog.items)
    reasons: list[str] = []
    if catalog.data_health.get("tradable_stock_source_status") != "live":
        reasons.append("stock_source_not_live")
    if include_full_etfs and catalog.data_health.get("tradable_etf_source_status") != "live":
        reasons.append("etf_source_not_live")
    if previous.stock_count >= 1_000 and stock_count < max(1_000, int(previous.stock_count * 0.8)):
        reasons.append(f"stock_coverage_drop:{previous.stock_count}->{stock_count}")
    if (
        include_full_etfs
        and previous.etf_count >= 50
        and etf_count < max(50, int(previous.etf_count * 0.6))
    ):
        reasons.append(f"etf_coverage_drop:{previous.etf_count}->{etf_count}")
    return reasons


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

    return [item.instrument_id for item in [*stocks[:stock_quota], *etfs[:etf_quota]]]


def build_full_market_batch_symbols(
    repo: QagentRepository,
    include_etfs: bool = True,
    max_symbols: int | None = None,
) -> list[str]:
    asset_types = {"stock", "etf"} if include_etfs else {"stock"}
    limit = max_symbols if max_symbols is not None else 20_000
    instruments = repo.list_tradable_instruments(asset_types=asset_types, limit=limit)
    return [item.instrument_id for item in instruments]


def enrich_full_market_visible_cards(
    cards: list[OpportunityCard],
    *,
    provider_mode: str,
    market_provider_name: str,
    as_of: date,
    enhanced_provider: AShareEnhancedProvider | None = None,
) -> dict[str, str]:
    """Attach research-only enhancement after global ranking without changing card scores."""

    provider = enhanced_provider or build_a_share_enhanced_provider(
        provider_mode,
        market_provider_name,
    )
    instrument_ids = [
        card.instrument_id
        for card in cards
        if card.instrument_id.startswith("CN:") and card.asset_type.lower() == "stock"
    ]
    if not instrument_ids:
        return summarize_a_share_enhanced_snapshots({}, provider, 0)
    try:
        snapshots = provider.get_snapshots(instrument_ids, as_of=as_of)
    except Exception as exc:  # pragma: no cover - finalization fail-open guard
        provider.last_errors.append(f"full_market_visible_enhancement: {exc}")
        snapshots = {}
    apply_a_share_enhanced_to_cards(cards, snapshots)
    health = summarize_a_share_enhanced_snapshots(
        snapshots,
        provider,
        len(instrument_ids),
    )
    health.update(
        {
            "a_share_enhanced_scope": "full_market_visible_cards_after_global_ranking",
            "a_share_enhanced_fail_open": "true",
        }
    )
    return health


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
    feedback_center = build_recent_recommendation_feedback_center(
        repo=repo,
        provider=provider_mode,
        market_provider=provider,
    )
    governance_context, walk_forward_validation, paper_report = _final_policy_inputs(
        repo,
        provider_mode,
    )
    session_factory = create_session_factory()
    replay_evidence = ReplayEvidenceRepository(session_factory, provider_mode)
    fundamental_as_of = _latest_completed_a_share_session() or date.today()
    fundamental_revision = replay_evidence.current_revision()
    stored_fundamentals = replay_evidence.fundamentals_as_of(
        symbols,
        fundamental_as_of,
        fundamental_revision,
    )
    scan = run_daily_scan(
        symbols,
        provider,
        mode=provider_mode,
        strategy_data_provider=StoredFundamentalStrategyDataProvider(
            list(stored_fundamentals.values())
        ),
        recommendation_feedback_center=feedback_center,
        paper_trading_report=paper_report,
        walk_forward_validation=walk_forward_validation,
        strategy_governance_context=governance_context,
    )
    scan.data_health.update(
        {
            "full_market_catalog": "sqlite",
            "full_market_catalog_total": str(summary.total_count),
            "full_market_requested": str(len(symbols)),
            "full_market_include_etfs": str(include_etfs).lower(),
            "full_market_fundamental_source": "sqlite_point_in_time",
            "full_market_fundamental_as_of": fundamental_as_of.isoformat(),
            "full_market_fundamental_dataset_revision": str(fundamental_revision),
            "full_market_fundamental_instruments": str(len(stored_fundamentals)),
        }
    )
    scan.data_health.update(sync_health)
    return FullMarketScanResult(
        symbols=symbols,
        scan=scan,
        data_health=scan.data_health,
    )


def run_full_market_batch_scan_job(job_id: str, top_cards_limit: int = 200) -> None:
    scan_started_at = datetime.now(timezone.utc)
    repo = _repo()
    job = repo.get_full_market_scan_job(job_id)
    if job is None:
        return
    if job.data_health.get("automatic_scan_aborted") == "true":
        if job.status in {"queued", "running"}:
            repo.update_full_market_scan_job(
                job_id,
                status="failed",
                message="Full-market scan stopped before execution",
                data_health=job.data_health,
            )
        return
    repo.update_full_market_scan_job(
        job_id,
        status="running",
        message="Preparing full-market scan inputs",
        data_health={
            **job.data_health,
            "full_market_worker_phase": "preparing_inputs",
        },
    )
    provider = build_market_data_provider(job.provider)
    session_factory = create_session_factory()
    replay_evidence = ReplayEvidenceRepository(session_factory, job.provider)
    fundamental_as_of = _frozen_full_market_fundamental_as_of(job.data_health)
    fundamental_revision = _frozen_full_market_fundamental_revision(
        job.data_health,
        replay_evidence.current_revision(),
    )
    feedback_center = build_recent_recommendation_feedback_center(
        repo=repo,
        provider=job.provider,
        market_provider=provider,
    )
    governance_context, walk_forward_validation, paper_report = _final_policy_inputs(
        repo,
        job.provider,
    )
    all_cards: list[OpportunityCard] = []
    all_items: list[ScanItem] = []
    all_factor_rankings: list[FactorRanking] = []
    feature_dataset_revisions: set[str] = set()
    sector_strength_batches: list[SectorStrength] = []
    strategy_health_batches: list[list[StrategyHealth]] = []
    all_governance: list[CardStrategyGovernance] = []
    aggregate_health: dict[str, str] = {
        **job.data_health,
        "provider": job.provider,
        "full_market_scan_mode": "full_market_batch",
        "full_market_total_symbols": str(job.total_symbols),
        "full_market_batch_size": str(job.batch_size),
        "full_market_batches": str(job.total_batches),
        "full_market_total_batches": str(job.total_batches),
        "full_market_scanned_symbols": "0",
        "full_market_completed_batches": "0",
        "full_market_error_count": "0",
        "full_market_batches_complete": "false",
        "full_market_scan_complete": "false",
        "full_market_include_etfs": str(job.include_etfs).lower(),
        "full_market_fundamental_source": "sqlite_point_in_time",
        "full_market_fundamental_as_of": fundamental_as_of.isoformat(),
        "full_market_fundamental_dataset_revision": str(fundamental_revision),
    }
    scanned_symbols = 0
    completed_batches = 0
    error_count = 0
    scan_end = _frozen_full_market_scan_end(job.data_health, job.created_at)
    aggregate_health["full_market_expected_trade_date"] = scan_end.isoformat()

    if (
        job.completed_batches > 0
        and _load_full_market_batch_checkpoint(
            repo,
            job_id=job.job_id,
            batch_index=1,
            symbols=job.symbols[: job.batch_size],
        )
        is None
    ):
        aggregate_health.update(
            {
                "full_market_restart_recovery": "restart_from_batch_1",
                "full_market_restart_reason": "legacy_job_without_batch_checkpoints",
            }
        )
        job = (
            repo.update_full_market_scan_job(
                job_id,
                status="queued",
                scanned_symbols=0,
                completed_batches=0,
                cards=0,
                errors=0,
                message="Restart recovery: rebuilding from batch 1",
                data_health=aggregate_health,
            )
            or job
        )

    repo.update_full_market_scan_job(
        job_id,
        status="running",
        message=f"Starting full-market scan: {job.total_symbols} symbols",
        data_health=aggregate_health,
    )

    restored_batches = 0
    for batch_index, batch in enumerate(_chunks(job.symbols, job.batch_size), start=1):
        current = repo.get_full_market_scan_job(job_id)
        if current is not None and current.data_health.get("automatic_scan_aborted") == "true":
            repo.update_full_market_scan_job(
                job_id,
                status="failed",
                message="Full-market scan stopped at batch boundary",
                data_health=current.data_health,
            )
            return
        completed_batches = batch_index
        checkpoint = _load_full_market_batch_checkpoint(
            repo,
            job_id=job.job_id,
            batch_index=batch_index,
            symbols=batch,
        )
        scan = checkpoint[0] if checkpoint is not None else None
        batch_errors = checkpoint[1] if checkpoint is not None else 0
        batch_error_message = checkpoint[2] if checkpoint is not None else None
        if checkpoint is not None:
            restored_batches += 1
            aggregate_health["full_market_restart_recovery"] = "batch_checkpoint_resume"
            aggregate_health["full_market_checkpoint_batches_restored"] = str(restored_batches)
        else:
            try:
                reset_fuyao_telemetry(provider)
                stored_fundamentals = replay_evidence.fundamentals_as_of(
                    batch,
                    fundamental_as_of,
                    fundamental_revision,
                )
                prefetch = getattr(provider, "prefetch_daily_bars", None)
                if callable(prefetch):
                    try:
                        prefetch(
                            batch,
                            start=date(2026, 1, 1),
                            end=scan_end,
                            repair_recent_tail=_full_market_tail_is_settled(scan_end),
                        )
                    except Exception as exc:
                        aggregate_health[f"batch_{batch_index}_prefetch_error"] = str(exc)[:500]
                scan = run_daily_scan(
                    batch,
                    provider,
                    mode=job.provider,
                    strategy_data_provider=StoredFundamentalStrategyDataProvider(
                        list(stored_fundamentals.values())
                    ),
                    # Full-market enhancement is applied once after global ranking so
                    # the returned Top N, rather than arbitrary batch-local cards, is enriched.
                    a_share_enhanced_provider=EmptyAShareEnhancedDataProvider(),
                    recommendation_feedback_center=feedback_center,
                    paper_trading_report=paper_report,
                    walk_forward_validation=walk_forward_validation,
                    strategy_governance_context=governance_context,
                    reset_market_provider_telemetry=False,
                    start=date(2026, 1, 1),
                    end=scan_end,
                )
            except Exception as exc:
                batch_errors = len(batch)
                batch_error_message = str(exc)[:500]
            _save_full_market_batch_checkpoint(
                repo,
                job_id=job.job_id,
                provider=job.provider,
                batch_index=batch_index,
                symbols=batch,
                scan=scan,
                error_count=batch_errors,
                error_message=batch_error_message,
            )

        if scan is not None:
            all_cards.extend(scan.cards)
            all_items.extend(scan.items)
            all_factor_rankings.extend(scan.factor_rankings)
            feature_dataset_revisions.update(_scan_dataset_revisions(scan.data_health))
            sector_strength_batches.extend(scan.sector_strength)
            strategy_health_batches.append(scan.strategy_health)
            all_governance.extend(scan.strategy_governance)
            _merge_health(aggregate_health, scan.data_health)
            error_count += _int_health(scan.data_health, "scan_errors")
        if batch_errors:
            error_count += batch_errors
            aggregate_health[f"batch_{batch_index}_error"] = (
                batch_error_message or "batch checkpoint recorded an unknown error"
            )
        scanned_symbols += len(batch)
        aggregate_health.update(
            {
                "full_market_scanned_symbols": str(scanned_symbols),
                "full_market_completed_batches": str(completed_batches),
                "full_market_error_count": str(error_count),
                "full_market_batches_complete": str(completed_batches == job.total_batches).lower(),
                "full_market_scan_complete": "false",
            }
        )
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

    finalizing_started_at = datetime.now(timezone.utc)
    aggregate_health.update(
        {
            "full_market_worker_phase": "finalizing",
            "full_market_finalizing_started_at": finalizing_started_at.isoformat(),
        }
    )
    repo.update_full_market_scan_job(
        job_id,
        status="running",
        scanned_symbols=scanned_symbols,
        completed_batches=completed_batches,
        cards=len(all_cards),
        errors=error_count,
        message="Finalizing full-market rankings and recommendation policy",
        data_health=aggregate_health,
    )

    research_universe = sorted({item.instrument_id for item in all_factor_rankings})
    global_shadow_rankings = rerank_factor_rankings(
        all_factor_rankings,
        instrument_ids=research_universe,
    )
    card_universe = sorted({card.instrument_id for card in all_cards})
    global_factor_rankings = rerank_factor_rankings(
        all_factor_rankings,
        instrument_ids=card_universe,
    )
    _apply_global_factor_rankings(all_cards, all_items, global_factor_rankings)
    final_governance_context, final_walk_forward_validation, _ = _final_policy_inputs(
        repo,
        job.provider,
    )
    final_policy = apply_final_recommendation_policy(
        all_cards,
        walk_forward_validation=final_walk_forward_validation,
        governance_context=final_governance_context,
    )
    all_cards = final_policy.cards
    all_governance = final_policy.audits
    aggregate_health.update(final_policy.data_health)
    feature_as_of_cap = _latest_completed_a_share_session() if job.provider == "free" else None
    future_trade_dates_ignored = _future_trade_date_count(
        all_items,
        instrument_ids=set(card_universe),
        after=feature_as_of_cap,
    )
    feature_as_of = _feature_snapshot_as_of(
        all_items,
        instrument_ids=set(card_universe),
        fallback=job.created_at.date(),
        not_after=feature_as_of_cap,
    )
    feature_snapshot = build_factor_feature_snapshot(
        global_factor_rankings,
        as_of=feature_as_of,
        dataset_revision=_feature_dataset_revision(
            feature_dataset_revisions,
            provider=job.provider,
            as_of=feature_as_of,
        ),
        instrument_ids=card_universe,
    )
    aggregate_health.update(feature_snapshot_data_health(feature_snapshot))
    fundamental_feature_rows = sum(
        any(
            ranking.research_features.get(feature) is not None
            for feature in (
                "earnings_yield",
                "return_on_equity",
                "gross_margin",
                "revenue_growth",
                "earnings_growth",
            )
        )
        for ranking in global_shadow_rankings
    )
    aggregate_health.update(
        {
            "factor_rankings": str(len(global_factor_rankings)),
            "factor_shadow_universe_rankings": str(len(global_shadow_rankings)),
            "factor_ranking_scope": "full_card_universe",
            "factor_ranking_normalization": "global_second_pass",
            "factor_ranking_tie_breaker": "instrument_id_asc",
            "dynamic_calibration_merge_policy": (
                "preserve_batch_calibration_reconcile_latest_governance"
            ),
            "full_market_final_policy_reconciled": "true",
            "full_market_feature_as_of_cap": (
                feature_as_of_cap.isoformat() if feature_as_of_cap is not None else "none"
            ),
            "full_market_future_trade_dates_ignored": str(future_trade_dates_ignored),
            "factor_research_fundamental_rows": str(fundamental_feature_rows),
            "factor_research_fundamental_coverage": (
                f"{fundamental_feature_rows / len(global_shadow_rankings):.6f}"
                if global_shadow_rankings
                else "0.000000"
            ),
        }
    )
    for key in [item for item in aggregate_health if item.startswith("factor_shadow_")]:
        aggregate_health.pop(key)
    factor_shadow = None
    try:
        stock_ids = {
            item.instrument_id
            for item in repo.list_tradable_instruments(
                asset_types={"stock"},
                limit=20_000,
            )
        }
        factor_shadow_result = score_factor_shadow_runs_with_legacy_retirement(
            create_session_factory(),
            provider_mode=job.provider,
            scan_job_id=job.job_id,
            signal_date=feature_as_of,
            rankings=global_shadow_rankings,
            stock_ids=stock_ids,
        )
        aggregate_health.update(factor_shadow_result.data_health)
        factor_shadow = factor_shadow_result.model_dump(mode="json")
    except Exception as exc:
        aggregate_health.update(
            {
                "factor_shadow_status": "error",
                "factor_shadow_error": str(exc)[:500],
                "factor_shadow_paper_isolation": "true",
            }
        )

    cache_key = _full_market_batch_cache_key(job.provider, job.include_etfs)
    previous_cache = repo.get_recent_scan_result_cache(
        cache_key=cache_key,
        max_age=timedelta(days=90),
    )
    previous_payload = previous_cache.payload if previous_cache is not None else {}
    feature_drift = _feature_drift_payload(
        previous_payload,
        feature_snapshot,
        current_metadata=_feature_drift_metadata(
            global_factor_rankings,
            all_cards,
            all_items,
            provider=job.provider,
        ),
    )
    aggregate_health.update(_feature_drift_data_health(feature_drift))

    strategy_health = _merge_strategy_health(strategy_health_batches)
    market_intelligence = build_market_intelligence_center(
        cards=all_cards,
        items=all_items,
        bars_by_instrument={},
        strategy_health=strategy_health,
        data_health=aggregate_health,
    )
    market_state = _build_a_share_market_state(
        previous_payload,
        market_intelligence=market_intelligence,
        as_of=feature_as_of,
        expected_count=len(card_universe),
    )
    aggregate_health.update(_a_share_market_state_data_health(market_state))
    benchmark_trend, benchmark_trend_error = _load_benchmark_trend(
        provider,
        as_of=feature_as_of,
    )
    aggregate_health.update(benchmark_trend_data_health(benchmark_trend))
    if benchmark_trend_error:
        aggregate_health["benchmark_trend_error"] = benchmark_trend_error
    ranked_cards = sort_recommendation_cards(sorted(all_cards, key=lambda card: card.instrument_id))
    diversified_head = select_strategy_diversified(
        ranked_cards,
        limit=10,
        max_per_strategy=2,
    )
    diversified_ids = {card.card_id for card in diversified_head}
    ranked_cards = [
        *diversified_head,
        *(card for card in ranked_cards if card.card_id not in diversified_ids),
    ]
    dominant_strategy, dominant_count, dominant_share = strategy_concentration(diversified_head)
    aggregate_health.update(
        {
            "strategy_diversification_limit": "2",
            "strategy_diversified_head_count": str(len(diversified_head)),
            "strategy_diversified_dominant_strategy": dominant_strategy or "",
            "strategy_diversified_dominant_count": str(dominant_count),
            "strategy_diversified_dominant_share": f"{dominant_share:.4f}",
        }
    )
    visible_cards = ranked_cards[:top_cards_limit]
    visible_enhancement_health = enrich_full_market_visible_cards(
        visible_cards,
        provider_mode=job.provider,
        market_provider_name=provider.name,
        as_of=scan_end,
    )
    aggregate_health.update(visible_enhancement_health)
    brief_health = apply_recommendation_briefs(all_cards)
    visible_card_ids = {card.card_id for card in visible_cards}
    visible_governance = [audit for audit in all_governance if audit.card_id in visible_card_ids]
    visible_items = _visible_rejected_items(all_items, limit=500)
    visible_card_instruments = {card.instrument_id for card in visible_cards}
    snapshot_items = [item for item in all_items if item.instrument_id in visible_card_instruments]
    sector_strength = _merge_sector_strength(sector_strength_batches)[:12]
    market_state_multiplier = min(
        _a_share_market_state_multiplier(market_state.state),
        1.0 if benchmark_trend.entry_allowed else 0.0,
    )
    portfolio_plan = build_portfolio_plan(
        diversified_head,
        market_state=market_state.state.value,
        market_state_multiplier=market_state_multiplier,
    )
    batches_complete = completed_batches == job.total_batches
    symbols_complete = scanned_symbols == job.total_symbols == len(job.symbols)
    scan_complete = batches_complete and symbols_complete and error_count == 0
    scan_completed_at = datetime.now(timezone.utc)
    payload_data_health = {
        **aggregate_health,
        **market_intelligence.data_health,
        # Batch scans intentionally disable per-batch enhancement. The final
        # visible-card pass is authoritative and must win over those aggregate
        # placeholder fields.
        **visible_enhancement_health,
        **recommendation_quality_data_health(visible_cards),
        **probability_calibration_data_health(visible_cards),
        **recommendation_feedback_data_health(visible_cards),
        **recommendation_policy_data_health(visible_governance),
        **brief_health,
        "scan_result_cache": "full_market_batch",
        "scan_result_cache_key": cache_key,
        "full_market_cards_total": str(len(ranked_cards)),
        "full_market_cards_returned": str(len(visible_cards)),
        "full_market_rejected_items": str(
            len([item for item in all_items if _is_rejected_item(item)])
        ),
        "full_market_items_returned": str(len(visible_items)),
        "full_market_snapshot_items": str(len(snapshot_items)),
        "full_market_scanned_symbols": str(scanned_symbols),
        "full_market_total_symbols": str(job.total_symbols),
        "full_market_completed_batches": str(completed_batches),
        "full_market_total_batches": str(job.total_batches),
        "full_market_error_count": str(error_count),
        "full_market_batches_complete": str(batches_complete).lower(),
        "full_market_scan_complete": str(scan_complete).lower(),
        "full_market_signal_date": feature_as_of.isoformat(),
        "full_market_scan_started_at": scan_started_at.isoformat(),
        "full_market_scan_completed_at": scan_completed_at.isoformat(),
        "full_market_scan_duration_seconds": (
            f"{(scan_completed_at - scan_started_at).total_seconds():.3f}"
        ),
        "sector_strength": str(len(sector_strength)),
        "scanned": str(scanned_symbols),
        "cards": str(len(visible_cards)),
    }
    asset_types_by_instrument = {
        instrument.instrument_id: instrument.asset_type
        for instrument in repo.list_tradable_instruments(limit=20_000)
    }
    payload_data_health.update(
        _market_data_reliability_health(
            all_items,
            expected_trade_date=scan_end,
            error_count=error_count,
            provider_error_count=_int_health(aggregate_health, "provider_error_count"),
            asset_types_by_instrument=asset_types_by_instrument,
        )
    )
    payload_data_health.update(
        _full_market_a_share_readiness_health(
            all_items,
            ranked_cards,
            aggregate_health,
            expected_trade_date=scan_end,
            asset_types_by_instrument=asset_types_by_instrument,
        )
    )
    paper_account = PaperTradingRepository(repo.session_factory).get_account_settings()
    paper_strategy_configuration, paper_strategy_configuration_digest = (
        build_paper_strategy_configuration(
            provider=job.provider,
            signal_date=feature_as_of,
            symbols=job.symbols,
            include_etfs=job.include_etfs,
            feature_set_version=str(payload_data_health.get("feature_set_version") or "unknown"),
            recommendation_policy=str(
                payload_data_health.get("recommendation_policy_entrypoint") or "unknown"
            ),
            calibration_merge_policy=str(
                payload_data_health.get("dynamic_calibration_merge_policy") or "unknown"
            ),
            quality_weights=recommendation_score_weights("CN"),
            governance_source=final_governance_context.source,
            governance_strategies=final_governance_context.strategies,
            account=paper_account,
        )
    )
    payload_data_health.update(
        {
            "paper_strategy_configuration_schema": paper_strategy_configuration["schema_version"],
            "paper_strategy_configuration_digest": paper_strategy_configuration_digest,
            "paper_strategy_configuration_json": json.dumps(
                paper_strategy_configuration,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
    )
    paper_model_cohort = paper_model_cohort_from_data_health(payload_data_health)
    if paper_model_cohort is not None:
        payload_data_health.update(
            {
                "paper_model_cohort_id": paper_model_cohort.cohort_id,
                "paper_model_cohort_feature_set_version": (paper_model_cohort.feature_set_version),
                "paper_model_cohort_recommendation_policy": (
                    paper_model_cohort.recommendation_policy_entrypoint
                ),
            }
        )
    paper_calibration_shadow = _safe_paper_calibration_shadow_payload(
        repo,
        provider=job.provider,
        cards=visible_cards,
        decision_date=feature_as_of,
        current_market_regime=market_state.state.value,
        current_cohort_id=(
            paper_model_cohort.cohort_id if paper_model_cohort is not None else None
        ),
    )
    shadow_health = paper_calibration_shadow.get("data_health")
    if isinstance(shadow_health, dict):
        payload_data_health.update(
            {
                str(key): str(value)
                for key, value in shadow_health.items()
                if str(key).startswith("paper_calibration_shadow_")
            }
        )
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
        "cards": governed_card_payloads(visible_cards, visible_governance),
        "items": [item.model_dump(mode="json") for item in visible_items],
        "strategy_health": [item.model_dump(mode="json") for item in strategy_health],
        "factor_rankings": [ranking.model_dump(mode="json") for ranking in global_factor_rankings],
        "sector_strength": [item.model_dump(mode="json") for item in sector_strength],
        "portfolio_plan": portfolio_plan.model_dump(mode="json"),
        "market_intelligence": market_intelligence.model_dump(mode="json"),
        "manual_action_center": manual_action_center.model_dump(mode="json"),
        "signal_monitor": signal_monitor.model_dump(mode="json"),
        "decision_quality_center": decision_quality_center.model_dump(mode="json"),
        "operational_readiness_center": operational_readiness_center.model_dump(mode="json"),
        "strategy_governance": [audit.model_dump(mode="json") for audit in visible_governance],
        "feature_snapshot": feature_snapshot.model_dump(mode="json"),
        "feature_drift": feature_drift,
        "a_share_market_state": market_state.model_dump(mode="json"),
        "benchmark_trend": benchmark_trend.model_dump(mode="json"),
        "factor_shadow": factor_shadow,
        "paper_calibration_shadow": paper_calibration_shadow,
        "data_health": payload_data_health,
    }
    repo.save_scan_run(
        provider=job.provider,
        mode="full_market_batch",
        symbols=job.symbols,
        result=DailyScanResult.model_validate(payload),
        snapshot_items=snapshot_items,
    )
    repo.save_scan_result_cache(
        cache_key=cache_key,
        provider=job.provider,
        mode="full_market_batch",
        symbols=job.symbols,
        payload=payload,
    )
    presentation_cache_key = full_market_batch_presentation_cache_key(
        job.provider,
        job.include_etfs,
    )
    repo.save_scan_result_cache(
        cache_key=presentation_cache_key,
        provider=job.provider,
        mode="full_market_batch_presentation",
        symbols=job.symbols,
        payload=build_full_market_batch_presentation_payload(payload),
    )
    final_status = "succeeded" if scan_complete else "failed"
    repo.update_full_market_scan_job(
        job_id,
        status=final_status,
        scanned_symbols=scanned_symbols,
        completed_batches=completed_batches,
        cards=len(visible_cards),
        errors=error_count,
        message=(
            "Full-market batch scan complete"
            if scan_complete
            else "Full-market batch scan incomplete"
        ),
        data_health=payload["data_health"],
        result_cache_key=cache_key,
    )
    if scan_complete:
        deleted_checkpoints = repo.delete_succeeded_full_market_scan_checkpoints(job_id)
        repo.update_full_market_scan_job(
            job_id,
            status=final_status,
            data_health={
                **payload["data_health"],
                "full_market_checkpoint_cleanup_rows": str(deleted_checkpoints),
                "full_market_presentation_cache_key": presentation_cache_key,
            },
        )


def full_market_batch_cache_key(provider: str, include_etfs: bool = True) -> str:
    return _full_market_batch_cache_key(provider, include_etfs)


def full_market_batch_presentation_cache_key(
    provider: str,
    include_etfs: bool = True,
) -> str:
    return f"full_market_batch_presentation:{provider.strip().lower()}:{str(include_etfs).lower()}"


def build_full_market_batch_presentation_payload(
    payload: dict[str, object],
    *,
    limit: int = 30,
) -> dict[str, object]:
    presentation = deepcopy(payload)
    limit_full_market_batch_payload(presentation, limit=limit)
    data_health = presentation.setdefault("data_health", {})
    if isinstance(data_health, dict):
        data_health["scan_result_cache"] = "full_market_batch_presentation"
        data_health["full_market_presentation_card_limit"] = str(limit)
    return presentation


def limit_full_market_batch_payload(payload: dict[str, object], *, limit: int) -> None:
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        return
    payload["cards"] = raw_cards[:limit]
    visible_card_ids = {
        str(card.get("card_id"))
        for card in payload["cards"]
        if isinstance(card, dict) and card.get("card_id")
    }
    visible_instrument_ids = {
        str(card.get("instrument_id"))
        for card in payload["cards"]
        if isinstance(card, dict) and card.get("instrument_id")
    }

    for key in ("items", "factor_rankings"):
        values = payload.get(key)
        if isinstance(values, list):
            payload[key] = [
                value
                for value in values
                if isinstance(value, dict)
                and str(value.get("instrument_id")) in visible_instrument_ids
            ]

    governance = payload.get("strategy_governance")
    if isinstance(governance, list):
        payload["strategy_governance"] = [
            value
            for value in governance
            if isinstance(value, dict) and str(value.get("card_id")) in visible_card_ids
        ]

    feature_snapshot = payload.get("feature_snapshot")
    if isinstance(feature_snapshot, dict):
        for key in ("raw_scores", "cross_sectional_scores"):
            values = feature_snapshot.get(key)
            if isinstance(values, dict):
                feature_snapshot[key] = {
                    instrument_id: value
                    for instrument_id, value in values.items()
                    if instrument_id in visible_instrument_ids
                }

    portfolio_plan = payload.get("portfolio_plan")
    if isinstance(portfolio_plan, dict):
        constraints = portfolio_plan.get("constraint_results")
        if isinstance(constraints, list):
            portfolio_plan["constraint_results"] = [
                value
                for value in constraints
                if isinstance(value, dict)
                and str(value.get("instrument_id")) in visible_instrument_ids
            ]

    data_health = payload.get("data_health")
    if isinstance(data_health, dict):
        _limit_full_market_data_health(data_health, visible_card_ids, limit)

    market_intelligence = payload.get("market_intelligence")
    if isinstance(market_intelligence, dict):
        intelligence_health = market_intelligence.get("data_health")
        if isinstance(intelligence_health, dict):
            _limit_full_market_data_health(intelligence_health, visible_card_ids, limit)

    for key in (
        "manual_action_center",
        "signal_monitor",
        "decision_quality_center",
        "operational_readiness_center",
        "alpha_quality_center",
        "research_center",
    ):
        payload.pop(key, None)


def _limit_full_market_data_health(
    data_health: dict[str, object],
    visible_card_ids: set[str],
    limit: int,
) -> None:
    gate_decisions = data_health.get("strategy_governance_gate_decisions")
    if isinstance(gate_decisions, str):
        try:
            parsed_gate_decisions = json.loads(gate_decisions)
        except (TypeError, ValueError):
            parsed_gate_decisions = None
        if isinstance(parsed_gate_decisions, dict):
            data_health["strategy_governance_gate_decisions"] = json.dumps(
                {
                    card_id: value
                    for card_id, value in parsed_gate_decisions.items()
                    if card_id in visible_card_ids
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
    for key in (
        "errors",
        "scan_error_samples",
        "a_share_enhanced_errors",
        "full_market_worker_error",
    ):
        value = data_health.get(key)
        if isinstance(value, str) and len(value) > 2_000:
            data_health[key] = f"{value[:2_000]}..."
    data_health["full_market_response_card_limit"] = str(limit)


def _full_market_batch_checkpoint_key(job_id: str, batch_index: int) -> str:
    return f"full_market_batch_checkpoint:{job_id}:{batch_index}"


def _save_full_market_batch_checkpoint(
    repo: QagentRepository,
    *,
    job_id: str,
    provider: str,
    batch_index: int,
    symbols: list[str],
    scan: DailyScanResult | None,
    error_count: int,
    error_message: str | None,
) -> None:
    repo.save_scan_result_cache(
        cache_key=_full_market_batch_checkpoint_key(job_id, batch_index),
        provider=provider,
        mode="full_market_batch_checkpoint",
        symbols=symbols,
        payload={
            "job_id": job_id,
            "batch_index": batch_index,
            "symbols": symbols,
            "scan": scan.model_dump(mode="json") if scan is not None else None,
            "error_count": error_count,
            "error_message": error_message,
        },
    )


def _load_full_market_batch_checkpoint(
    repo: QagentRepository,
    *,
    job_id: str,
    batch_index: int,
    symbols: list[str],
) -> tuple[DailyScanResult | None, int, str | None] | None:
    cached = repo.get_recent_scan_result_cache(
        cache_key=_full_market_batch_checkpoint_key(job_id, batch_index),
        max_age=timedelta(days=14),
    )
    if cached is None:
        return None
    payload = cached.payload
    if (
        payload.get("job_id") != job_id
        or payload.get("batch_index") != batch_index
        or payload.get("symbols") != symbols
    ):
        return None
    raw_scan = payload.get("scan")
    try:
        scan = DailyScanResult.model_validate(raw_scan) if isinstance(raw_scan, dict) else None
        error_count = int(payload.get("error_count", 0) or 0)
    except (TypeError, ValueError):
        return None
    error_message = payload.get("error_message")
    return scan, max(error_count, 0), str(error_message) if error_message else None


def _repo() -> QagentRepository:
    initialize_database()
    return QagentRepository(create_session_factory())


def _apply_global_factor_rankings(
    cards: list[OpportunityCard],
    items: list[ScanItem],
    rankings: list[FactorRanking],
) -> None:
    ranking_by_id = {ranking.instrument_id: ranking for ranking in rankings}
    for card in cards:
        ranking = ranking_by_id.get(card.instrument_id)
        card.rank_reasons = [
            reason
            for reason in card.rank_reasons
            if not reason.startswith(("factor flag: ", "因子排名第 "))
        ]
        if ranking is None:
            card.factor_score = 0.0
            card.factor_rank = None
            card.factor_percentile = 0.0
            card.factor_flags = []
            card.factor_exposures = []
            continue
        card.factor_score = ranking.factor_score
        card.factor_rank = ranking.factor_rank
        card.factor_percentile = ranking.percentile
        card.factor_flags = list(ranking.flags)
        card.factor_exposures = [
            exposure.model_copy(deep=True) for exposure in ranking.factor_exposures
        ]
        if card.primary_strategy_id == "factor_rotation_watch":
            card.rank_reasons.insert(0, f"因子排名第 {ranking.factor_rank}")
            _update_factor_watch_evidence(card, ranking)
        card.rank_reasons.extend(f"factor flag: {flag}" for flag in ranking.flags)

    for item in items:
        ranking = ranking_by_id.get(item.instrument_id)
        if ranking is None:
            item.factor_score = None
            item.factor_rank = None
            item.factor_flags = []
            continue
        item.factor_score = ranking.factor_score
        item.factor_rank = ranking.factor_rank
        item.factor_flags = list(ranking.flags)


def _update_factor_watch_evidence(
    card: OpportunityCard,
    ranking: FactorRanking,
) -> None:
    for evaluation in card.strategy_evaluations:
        if evaluation.strategy_id != "factor_rotation_watch":
            continue
        evaluation.evidence.update(
            {
                "factor_rank": ranking.factor_rank,
                "factor_score": ranking.factor_score,
                "percentile": ranking.percentile,
                "flags": list(ranking.flags),
            }
        )
        evaluation.score_components.update(
            {
                "factor_score": ranking.factor_score,
                "valuation": ranking.valuation_score,
                "size": ranking.size_score,
                "quality": ranking.quality_score,
                "trend_quality": ranking.trend_quality_score,
                "liquidity": ranking.liquidity_score,
                "low_risk": ranking.low_risk_score,
                "risk_filter": ranking.risk_filter_score,
            }
        )


def _feature_snapshot_as_of(
    items: list[ScanItem],
    *,
    instrument_ids: set[str],
    fallback: date,
    not_after: date | None = None,
) -> date:
    trade_dates = [
        item.latest_trade_date
        for item in items
        if item.instrument_id in instrument_ids
        and item.latest_trade_date is not None
        and (not_after is None or item.latest_trade_date <= not_after)
    ]
    effective_fallback = min(fallback, not_after) if not_after is not None else fallback
    return max(trade_dates, default=effective_fallback)


def _future_trade_date_count(
    items: list[ScanItem],
    *,
    instrument_ids: set[str],
    after: date | None,
) -> int:
    if after is None:
        return 0
    return sum(
        item.instrument_id in instrument_ids
        and item.latest_trade_date is not None
        and item.latest_trade_date > after
        for item in items
    )


def _latest_completed_a_share_session(now: datetime | None = None) -> date | None:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(ZoneInfo("Asia/Shanghai"))
    today = local_now.date()
    sessions = trading_sessions_in_range(today - timedelta(days=14), today)
    if not sessions:
        return None
    if sessions[-1] == today and local_now.timetz().replace(tzinfo=None) < time(hour=15, minute=30):
        sessions = sessions[:-1]
    return sessions[-1] if sessions else None


def _full_market_tail_is_settled(
    scan_end: date,
    now: datetime | None = None,
) -> bool:
    """Only query a snapshot for today's completed A-share session."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(ZoneInfo("Asia/Shanghai"))
    return scan_end == local_now.date() and local_now.timetz().replace(tzinfo=None) >= time(
        hour=15,
        minute=45,
    )


def _frozen_full_market_fundamental_as_of(data_health: dict[str, str]) -> date:
    raw = data_health.get("full_market_fundamental_as_of")
    if raw:
        return date.fromisoformat(raw)
    return _latest_completed_a_share_session() or date.today()


def _frozen_full_market_scan_end(
    data_health: dict[str, str],
    created_at: datetime,
) -> date:
    raw = data_health.get("full_market_expected_trade_date")
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return (
        _latest_completed_a_share_session()
        or created_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    )


def _frozen_full_market_fundamental_revision(
    data_health: dict[str, str],
    current_revision: int,
) -> int:
    raw = data_health.get("full_market_fundamental_dataset_revision")
    return int(raw) if raw else current_revision


def _feature_dataset_revision(
    revisions: set[str],
    *,
    provider: str,
    as_of: date,
) -> int | str:
    if len(revisions) == 1:
        return next(iter(revisions))
    if revisions:
        return f"mixed:{'|'.join(sorted(revisions))}"
    return f"{provider.strip().lower()}:{as_of.isoformat()}"


def _scan_dataset_revisions(data_health: dict[str, str]) -> set[str]:
    return {
        revision
        for key in ("dataset_revision", "market_dataset_revision", "replay_dataset_revision")
        if (revision := data_health.get(key))
    }


def _feature_drift_payload(
    previous_payload: dict[str, object],
    current_snapshot: FeatureSnapshot,
    *,
    current_metadata: DriftSnapshotMetadata,
) -> dict[str, object]:
    raw_previous = previous_payload.get("feature_snapshot")
    if not isinstance(raw_previous, dict):
        return {
            "status": "insufficient",
            "reason": "尚无同版本上一期特征快照，当前结果仅建立漂移基线。",
            "reference_version": None,
            "current_version": current_snapshot.feature_set_version,
            "auto_adjust_weights": False,
            "weight_action": "none",
        }
    try:
        previous_snapshot = FeatureSnapshot.model_validate(raw_previous)
        previous_metadata = _feature_drift_metadata_from_payload(previous_payload)
        report = compare_feature_snapshots(
            previous_snapshot,
            current_snapshot,
            reference_metadata=previous_metadata,
            current_metadata=current_metadata,
        )
    except (TypeError, ValueError):
        return {
            "status": "insufficient",
            "reason": "上一期特征快照无法按当前契约读取，未据此调整任何权重。",
            "reference_version": None,
            "current_version": current_snapshot.feature_set_version,
            "auto_adjust_weights": False,
            "weight_action": "none",
        }
    return report.model_dump(mode="json")


def _feature_drift_metadata(
    rankings: list[FactorRanking],
    cards: list[OpportunityCard],
    items: list[ScanItem],
    *,
    provider: str,
) -> DriftSnapshotMetadata:
    ordered = sorted(rankings, key=lambda item: (item.factor_rank, item.instrument_id))
    card_by_id = {card.instrument_id: card for card in cards}
    return DriftSnapshotMetadata(
        sources={ranking.instrument_id: provider for ranking in rankings},
        flags={ranking.instrument_id: tuple(ranking.flags) for ranking in rankings},
        top_n=tuple(ranking.instrument_id for ranking in ordered[:20]),
        industries={
            instrument_id: card.market_context.industry
            for instrument_id, card in card_by_id.items()
            if card.market_context is not None and card.market_context.industry
        },
        rejection_reasons={
            item.instrument_id: tuple(
                value for value in (item.status, item.rejection_category) if value
            )
            for item in items
            if _is_rejected_item(item)
        },
    )


def _feature_drift_metadata_from_payload(
    payload: dict[str, object],
) -> DriftSnapshotMetadata:
    raw_rankings = payload.get("factor_rankings")
    rankings: list[FactorRanking] = []
    if isinstance(raw_rankings, list):
        for value in raw_rankings:
            if not isinstance(value, dict):
                continue
            try:
                rankings.append(FactorRanking.model_validate(value))
            except ValueError:
                continue
    raw_cards = payload.get("cards")
    industries: dict[str, str] = {}
    if isinstance(raw_cards, list):
        for card in raw_cards:
            if not isinstance(card, dict):
                continue
            instrument_id = str(card.get("instrument_id") or "")
            context = card.get("market_context")
            if instrument_id and isinstance(context, dict) and context.get("industry"):
                industries[instrument_id] = str(context["industry"])
    raw_items = payload.get("items")
    rejections: dict[str, tuple[str, ...]] = {}
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            instrument_id = str(item.get("instrument_id") or "")
            values = tuple(
                str(value)
                for value in (item.get("status"), item.get("rejection_category"))
                if value
            )
            if instrument_id and values:
                rejections[instrument_id] = values
    provider = str(
        (payload.get("data_health") or {}).get("provider", "unknown")
        if isinstance(payload.get("data_health"), dict)
        else "unknown"
    )
    ordered = sorted(rankings, key=lambda item: (item.factor_rank, item.instrument_id))
    return DriftSnapshotMetadata(
        sources={ranking.instrument_id: provider for ranking in rankings},
        flags={ranking.instrument_id: tuple(ranking.flags) for ranking in rankings},
        top_n=tuple(ranking.instrument_id for ranking in ordered[:20]),
        industries=industries,
        rejection_reasons=rejections,
    )


def _feature_drift_data_health(payload: dict[str, object]) -> dict[str, str]:
    return {
        "feature_drift_status": str(payload.get("status", "insufficient")),
        "feature_drift_reason": str(payload.get("reason", "")),
        "feature_drift_auto_weight_change": str(
            bool(payload.get("auto_adjust_weights", False))
        ).lower(),
    }


def _build_a_share_market_state(
    previous_payload: dict[str, object],
    *,
    market_intelligence,
    as_of: date,
    expected_count: int,
) -> AShareStateSnapshot:
    environment = market_intelligence.market_environment
    observed_state = _observed_a_share_market_state(environment)
    breadth = environment.breadth
    expected = max(expected_count, breadth.sample_count, 1)
    missing_rate = round(max(0.0, 1.0 - min(1.0, breadth.sample_count / expected)), 4)
    confidence = round(
        min(
            0.95, 0.45 + min(0.35, breadth.sample_count / 100) + abs(environment.score - 0.5) * 0.3
        ),
        4,
    )
    observation = AShareStateObservation(
        as_of=as_of,
        state=observed_state,
        confidence=confidence,
        missing_rate=missing_rate,
        reason=(
            f"市场环境代理：regime={environment.regime}, score={environment.score:.2f}, "
            f"breadth={breadth.sample_count}"
        ),
    )
    raw_previous = previous_payload.get("a_share_market_state")
    previous = None
    if isinstance(raw_previous, dict):
        try:
            previous = AShareStateSnapshot.model_validate(raw_previous)
        except ValueError:
            previous = None
    if previous is not None and observation.as_of <= previous.as_of:
        return previous.model_copy(
            update={
                "observed_state": observation.state,
                "confidence": observation.confidence,
                "missing_rate": observation.missing_rate,
                "observation_reason": observation.reason,
            }
        )
    return advance_a_share_state(previous, observation)


def _observed_a_share_market_state(environment) -> AShareMarketState:
    breadth = environment.breadth
    if breadth.sample_count <= 0:
        return AShareMarketState.UNKNOWN
    downside_ratio = breadth.limit_down_count / max(breadth.sample_count, 1)
    if environment.score < 0.25 or downside_ratio >= 0.05:
        return AShareMarketState.STRESS
    if environment.regime in {"risk_off", "thin"} or environment.score < 0.42:
        return AShareMarketState.WEAK
    if environment.regime == "risk_on" or environment.score >= 0.68:
        return AShareMarketState.STRONG
    if environment.regime == "constructive" or environment.score >= 0.56:
        return AShareMarketState.CONSTRUCTIVE
    return AShareMarketState.MIXED


def _a_share_market_state_multiplier(state: AShareMarketState) -> float:
    return {
        AShareMarketState.STRONG: 1.1,
        AShareMarketState.CONSTRUCTIVE: 1.0,
        AShareMarketState.MIXED: 0.75,
        AShareMarketState.WEAK: 0.5,
        AShareMarketState.STRESS: 0.0,
        AShareMarketState.UNKNOWN: 0.5,
    }[state]


def _a_share_market_state_data_health(state: AShareStateSnapshot) -> dict[str, str]:
    return {
        "a_share_market_state": state.state.value,
        "a_share_market_state_observed": state.observed_state.value,
        "a_share_market_state_transition": state.transition_reason.value,
        "a_share_market_state_confidence": f"{state.confidence:.4f}",
        "a_share_market_state_missing_rate": f"{state.missing_rate:.4f}",
        "a_share_market_state_risk_multiplier": f"{_a_share_market_state_multiplier(state.state):.2f}",
    }


def _load_benchmark_trend(
    provider,
    *,
    as_of: date,
) -> tuple[BenchmarkTrendSnapshot, str | None]:
    try:
        bars = provider.get_daily_bars(
            list(REQUIRED_BENCHMARK_IDS),
            as_of - timedelta(days=200),
            as_of,
        )
        return build_benchmark_trend_snapshot(bars, as_of=as_of), None
    except Exception as exc:
        return (
            build_benchmark_trend_snapshot(None, as_of=as_of),
            str(exc)[:500],
        )


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


def _market_data_reliability_health(
    items: list[ScanItem],
    *,
    expected_trade_date: date,
    error_count: int,
    provider_error_count: int,
    asset_types_by_instrument: dict[str, str] | None = None,
) -> dict[str, str]:
    dated = [item for item in items if item.latest_trade_date is not None]
    current = sum(item.latest_trade_date == expected_trade_date for item in dated)
    stale = sum(item.latest_trade_date < expected_trade_date for item in dated)
    future = sum(item.latest_trade_date > expected_trade_date for item in dated)
    missing = len(items) - len(dated)
    coverage = current / len(items) if items else 0.0
    source_counts = Counter(item.provider or "missing" for item in items)
    current_source_counts = Counter(
        item.provider or "missing"
        for item in items
        if item.latest_trade_date == expected_trade_date
    )
    stale_source_counts = Counter(
        item.provider or "missing"
        for item in items
        if item.latest_trade_date is not None and item.latest_trade_date < expected_trade_date
    )
    missing_reason_counts = Counter(
        _market_data_problem_reason(item) for item in items if item.latest_trade_date is None
    )
    asset_types = asset_types_by_instrument or {}
    asset_type_counts = Counter(_asset_type(item, asset_types) for item in items)
    current_asset_type_counts = Counter(
        _asset_type(item, asset_types)
        for item in items
        if item.latest_trade_date == expected_trade_date
    )
    stale_asset_type_counts = Counter(
        _asset_type(item, asset_types)
        for item in items
        if item.latest_trade_date is not None and item.latest_trade_date < expected_trade_date
    )
    missing_asset_type_counts = Counter(
        _asset_type(item, asset_types) for item in items if item.latest_trade_date is None
    )
    stale_trade_dates = Counter(
        item.latest_trade_date
        for item in items
        if item.latest_trade_date is not None and item.latest_trade_date < expected_trade_date
    )
    stale_age_counts: Counter[str] = Counter()
    for latest_trade_date, count in stale_trade_dates.items():
        stale_age_counts[_market_data_stale_age_bucket(latest_trade_date, expected_trade_date)] += (
            count
        )
    problem_items = [item for item in items if item.latest_trade_date != expected_trade_date]
    problem_status_counts = Counter(item.status for item in problem_items)
    problem_samples = ",".join(
        item.instrument_id for item in sorted(problem_items, key=_market_data_problem_sort_key)[:12]
    )
    if error_count > 0 or not items or coverage < 0.80:
        state = "risk"
    elif provider_error_count > 0 or coverage < 0.98 or stale > 0 or missing > 0:
        state = "watch"
    else:
        state = "ready"
    return {
        "market_data_reliability_state": state,
        "market_data_expected_trade_date": expected_trade_date.isoformat(),
        "market_data_latest_session_current": str(current),
        "market_data_latest_session_stale": str(stale),
        "market_data_latest_session_missing": str(missing),
        "market_data_latest_session_future": str(future),
        "market_data_latest_session_coverage": f"{coverage:.6f}",
        "market_data_source_mix": _market_data_count_mix(source_counts),
        "market_data_current_source_mix": _market_data_count_mix(current_source_counts),
        "market_data_stale_source_mix": _market_data_count_mix(stale_source_counts),
        "market_data_stale_age_mix": _market_data_count_mix(stale_age_counts),
        "market_data_missing_reason_mix": _market_data_count_mix(missing_reason_counts),
        "market_data_asset_type_mix": _market_data_count_mix(asset_type_counts),
        "market_data_current_asset_type_mix": _market_data_count_mix(current_asset_type_counts),
        "market_data_stale_asset_type_mix": _market_data_count_mix(stale_asset_type_counts),
        "market_data_missing_asset_type_mix": _market_data_count_mix(missing_asset_type_counts),
        "market_data_problem_status_mix": _market_data_count_mix(problem_status_counts),
        "market_data_problem_samples": problem_samples,
        "market_data_recovery_action": _market_data_recovery_action(
            stale=stale,
            missing=missing,
            future=future,
            error_count=error_count,
            provider_error_count=provider_error_count,
        ),
    }


def _full_market_a_share_readiness_health(
    items: list[ScanItem],
    cards: list[OpportunityCard],
    aggregate_health: dict[str, str],
    *,
    expected_trade_date: date,
    asset_types_by_instrument: dict[str, str] | None = None,
) -> dict[str, str]:
    cn_items = [item for item in items if item.instrument_id.startswith("CN:")]
    cn_cards = [card for card in cards if card.instrument_id.startswith("CN:")]
    item_total = len(cn_items)
    card_total = len(cn_cards)
    dated = sum(item.latest_trade_date is not None for item in cn_items)
    current = sum(item.latest_trade_date == expected_trade_date for item in cn_items)
    stale = sum(
        item.latest_trade_date is not None and item.latest_trade_date < expected_trade_date
        for item in cn_items
    )
    missing = item_total - dated
    current_items = [item for item in cn_items if item.latest_trade_date == expected_trade_date]
    adjusted_items = [item for item in current_items if _valid_latest_adjusted_close(item)]
    adjusted = len(adjusted_items)
    adjusted_total = len(current_items)
    adjusted_missing_items = [
        item for item in current_items if not _valid_latest_adjusted_close(item)
    ]
    adjusted_source_counts = Counter(
        (item.provider or "unknown").strip() or "unknown" for item in adjusted_items
    )
    adjusted_missing_source_counts = Counter(
        (item.provider or "unknown").strip() or "unknown" for item in adjusted_missing_items
    )
    adjusted_type_counts = Counter(
        (item.latest_adjustment_type or "unknown").strip() or "unknown" for item in adjusted_items
    )
    asset_types = asset_types_by_instrument or {}
    current_stock_items = [
        item for item in current_items if _asset_type(item, asset_types) == "stock"
    ]
    current_etf_items = [item for item in current_items if _asset_type(item, asset_types) == "etf"]
    stock_adjusted_items = [
        item for item in current_stock_items if _valid_latest_adjusted_close(item)
    ]
    etf_raw_items = [item for item in current_etf_items if _valid_latest_close(item)]
    etf_total_return_adjusted_items = [
        item for item in current_etf_items if _valid_etf_total_return_adjusted_close(item)
    ]
    asset_price_statuses = {
        "a_share_stock_adjusted_price": _coverage_readiness_if_applicable(
            len(stock_adjusted_items), len(current_stock_items)
        ),
        "a_share_etf_raw_price": _coverage_readiness_if_applicable(
            len(etf_raw_items), len(current_etf_items)
        ),
        "a_share_etf_total_return_adjusted_price": _coverage_readiness_if_applicable(
            len(etf_total_return_adjusted_items), len(current_etf_items)
        ),
    }
    operational_price_covered = len(stock_adjusted_items) + len(etf_raw_items)
    operational_price_total = len(current_stock_items) + len(current_etf_items)
    operational_price_status = _coverage_readiness_if_applicable(
        operational_price_covered,
        operational_price_total,
    )
    suspension = min(_int_health(aggregate_health, "a_share_suspension_count"), item_total)
    price_limit = min(_int_health(aggregate_health, "a_share_price_limit_count"), item_total)
    liquidity = min(_int_health(aggregate_health, "a_share_liquidity_count"), item_total)
    turnover = min(_int_health(aggregate_health, "a_share_turnover_count"), item_total)
    industry = min(_int_health(aggregate_health, "a_share_industry_card_count"), card_total)
    index = min(
        _int_health(aggregate_health, "a_share_index_constituent_card_count"),
        card_total,
    )
    statuses = {
        "a_share_adjusted_price": _coverage_readiness(adjusted, adjusted_total),
        "a_share_suspension": _coverage_readiness(suspension, item_total),
        "a_share_price_limit": _coverage_readiness(price_limit, item_total),
        "a_share_industry": _coverage_readiness(industry, card_total),
        "a_share_liquidity": _coverage_readiness(liquidity, item_total),
        "a_share_turnover": _coverage_readiness(turnover, item_total),
        "a_share_index_constituents": _coverage_readiness(index, card_total),
        "a_share_fund_flow": "ready"
        if _int_health(aggregate_health, "fund_flow") > 0
        else "unsupported"
        if aggregate_health.get("a_share_enhanced_fund_flow_status") == "unsupported"
        else "missing",
        "a_share_announcements": (
            "ready"
            if _int_health(aggregate_health, "strategy_announcements") > 0
            else "unsupported"
            if aggregate_health.get("a_share_enhanced_announcements_status") == "unsupported"
            else "partial"
            if _int_health(aggregate_health, "strategy_fundamentals") > 0
            else "missing"
        ),
    }
    # Keep one price domain in the readiness average: adjusted prices are an
    # operational requirement for stocks, while a valid raw price is sufficient
    # for ETF scanning/execution. Strict ETF total-return-adjusted coverage is a
    # separately reported research limitation and must not gain an extra weight.
    scoring_statuses = dict(statuses)
    scoring_statuses["a_share_adjusted_price"] = operational_price_status
    scoring_values = [value for value in scoring_statuses.values() if value != "not_applicable"]
    score = (
        sum(_readiness_score(value) for value in scoring_values) / len(scoring_values)
        if scoring_values
        else 0.0
    )
    return {
        **statuses,
        **asset_price_statuses,
        "a_share_data_scope": "full_market_cn_universe",
        "a_share_data_readiness_score": f"{score:.2f}",
        "a_share_bars_coverage": f"{dated}/{item_total}",
        "a_share_current_bars_coverage": f"{current}/{item_total}",
        "a_share_stale_bars": str(stale),
        "a_share_missing_bars": str(missing),
        "a_share_current_bar_coverage_ratio": f"{current / item_total:.6f}"
        if item_total
        else "0.000000",
        "a_share_adjusted_price_coverage": f"{adjusted}/{adjusted_total}",
        "a_share_adjusted_price_missing": str(len(adjusted_missing_items)),
        "a_share_adjusted_price_missing_samples": ",".join(
            sorted(item.instrument_id for item in adjusted_missing_items)[:12]
        ),
        "a_share_adjusted_price_source_mix": _market_data_count_mix(adjusted_source_counts),
        "a_share_adjusted_price_missing_source_mix": _market_data_count_mix(
            adjusted_missing_source_counts
        ),
        "a_share_adjustment_type_mix": _market_data_count_mix(adjusted_type_counts),
        "a_share_adjusted_price_semantics": (
            "latest_expected_session_adjusted_close_finite_positive"
        ),
        "a_share_adjusted_price_scope": "legacy_all_cn_universe",
        "a_share_operational_price": operational_price_status,
        "a_share_operational_price_coverage": (
            f"{operational_price_covered}/{operational_price_total}"
        ),
        "a_share_operational_price_semantics": (
            "stock_adjusted_close_plus_etf_raw_close_on_latest_expected_session"
        ),
        "a_share_stock_adjusted_price_coverage": (
            f"{len(stock_adjusted_items)}/{len(current_stock_items)}"
        ),
        "a_share_stock_adjusted_price_missing": str(
            len(current_stock_items) - len(stock_adjusted_items)
        ),
        "a_share_stock_adjusted_price_semantics": (
            "stock_latest_expected_session_adjusted_close_finite_positive"
        ),
        "a_share_etf_raw_price_coverage": f"{len(etf_raw_items)}/{len(current_etf_items)}",
        "a_share_etf_raw_price_missing": str(len(current_etf_items) - len(etf_raw_items)),
        "a_share_etf_raw_price_semantics": (
            "etf_latest_expected_session_raw_close_finite_positive"
        ),
        "a_share_etf_total_return_adjusted_price_coverage": (
            f"{len(etf_total_return_adjusted_items)}/{len(current_etf_items)}"
        ),
        "a_share_etf_total_return_adjusted_price_missing": str(
            len(current_etf_items) - len(etf_total_return_adjusted_items)
        ),
        "a_share_etf_total_return_adjusted_price_source_mix": _market_data_count_mix(
            Counter(
                (item.provider or "unknown").strip() or "unknown"
                for item in etf_total_return_adjusted_items
            )
        ),
        "a_share_etf_total_return_adjusted_price_semantics": (
            "etf_latest_expected_session_adjusted_close_finite_positive_with_explicit_non_none_adjustment"
        ),
        "a_share_suspension_coverage": f"{suspension}/{item_total}",
        "a_share_price_limit_coverage": f"{price_limit}/{item_total}",
        "a_share_liquidity_coverage": f"{liquidity}/{item_total}",
        "a_share_turnover_coverage": f"{turnover}/{item_total}",
        "a_share_industry_card_coverage": f"{industry}/{card_total}",
        "a_share_index_constituent_card_coverage": f"{index}/{card_total}",
    }


def _coverage_readiness(covered: int, total: int) -> str:
    if total <= 0 or covered <= 0:
        return "missing"
    return "ready" if covered / total >= 0.98 else "partial"


def _coverage_readiness_if_applicable(covered: int, total: int) -> str:
    if total <= 0:
        return "not_applicable"
    return _coverage_readiness(covered, total)


def _valid_latest_adjusted_close(item: ScanItem) -> bool:
    return _valid_positive_price(item.latest_adjusted_close)


def _valid_latest_close(item: ScanItem) -> bool:
    return _valid_positive_price(item.latest_close)


def _valid_positive_price(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def _valid_etf_total_return_adjusted_close(item: ScanItem) -> bool:
    if not _valid_latest_adjusted_close(item):
        return False
    adjustment_type = (item.latest_adjustment_type or "").strip().lower()
    provider = (item.provider or "").strip().lower()
    return (
        adjustment_type not in {"", "none", "snapshot_qfq_anchor"}
        and provider != "fuyao_etf_unadjusted"
    )


def _readiness_score(status: str) -> float:
    if status == "ready":
        return 1.0
    if status == "partial":
        return 0.55
    return 0.0


def _asset_type(item: ScanItem, asset_types_by_instrument: dict[str, str]) -> str:
    value = asset_types_by_instrument.get(item.instrument_id, "unknown").strip().lower()
    return value or "unknown"


def _market_data_count_mix(counts: Counter[str]) -> str:
    return ",".join(
        f"{key}={count}"
        for key, count in sorted(
            counts.items(),
            key=lambda value: (-value[1], value[0]),
        )
    )


def _market_data_problem_reason(item: ScanItem) -> str:
    blocker = next(
        (candidate.code for candidate in item.blockers if candidate.severity == "block"),
        None,
    )
    return blocker or item.rejection_category or item.status or "unknown"


def _market_data_stale_age_bucket(latest: date, expected: date) -> str:
    try:
        sessions = trading_sessions_in_range(latest, expected)
        lag = max(len(sessions) - 1, 1)
    except ValueError:
        lag = max((expected - latest).days, 1)
    if lag <= 1:
        return "1_session"
    if lag <= 5:
        return "2_5_sessions"
    if lag <= 20:
        return "6_20_sessions"
    return "over_20_sessions"


def _market_data_problem_sort_key(item: ScanItem) -> tuple[int, date, str]:
    if item.latest_trade_date is None:
        return (0, date.min, item.instrument_id)
    return (1, item.latest_trade_date, item.instrument_id)


def _market_data_recovery_action(
    *,
    stale: int,
    missing: int,
    future: int,
    error_count: int,
    provider_error_count: int,
) -> str:
    if future:
        return "reject_future_data"
    if error_count:
        return "scan_error_retry"
    if stale and missing:
        return "quarantine_until_next_daily_scan"
    if stale:
        return "quarantine_until_next_daily_scan"
    if missing:
        return "quarantine_until_next_daily_scan"
    if provider_error_count:
        return "provider_backoff_retry"
    return "none"


def _merge_health(target: dict[str, str], source: dict[str, str]) -> None:
    source_error_kind = str(source.get("provider_error_kind", "none"))
    current_error_kind = str(target.get("provider_error_kind", "none"))
    if _provider_error_severity(source_error_kind) >= _provider_error_severity(current_error_kind):
        target["provider_error_kind"] = source_error_kind
        target["provider_error_code"] = str(source.get("provider_error_code", ""))
        target["provider_error_retryable"] = str(source.get("provider_error_retryable", "false"))
    for key, value in source.items():
        current = target.get(key)
        if key in {
            "provider_error_kind",
            "provider_error_code",
            "provider_error_retryable",
        }:
            continue
        if key == "dynamic_calibration_passes":
            target[key] = "1"
        elif key in {
            "fuyao_error_category_mix",
            "fuyao_degraded_snapshot_field_mix",
        }:
            target[key] = _merge_health_count_mixes(current, str(value))
        elif key == "fuyao_clients":
            target[key] = str(max(_safe_health_int(current), _safe_health_int(value)))
        elif key == "fuyao_telemetry":
            target[key] = _merge_provider_state(current, str(value))
        elif key == "fuyao_latency_ms_total":
            target[key] = f"{_safe_health_float(current) + _safe_health_float(value):.3f}"
        elif key == "fuyao_latency_ms_average":
            # Recomputed from the merged request and latency totals below.
            continue
        elif key in {
            "provider_circuit_capabilities",
            "provider_circuit_failures",
            "provider_circuit_successes",
            "provider_circuit_opened",
            "provider_circuit_half_open_probes",
            "provider_circuit_recoveries",
        }:
            target[key] = str(max(_safe_health_int(current), _safe_health_int(value)))
        elif key in {
            "provider_circuit_open_capabilities",
            "provider_circuit_half_open_capabilities",
        }:
            target[key] = str(value)
        elif key == "provider_circuit_retry_after_seconds":
            target[key] = f"{_safe_health_float(value):.3f}"
        elif current is not None and str(current).isdigit() and str(value).isdigit():
            target[key] = str(int(current) + int(str(value)))
        elif key == "errors" and current:
            target[key] = f"{current} | {value}"
        else:
            target[key] = str(value)
    if "fuyao_latency_ms_total" in target:
        requests = _safe_health_int(target.get("fuyao_requests"))
        total = _safe_health_float(target.get("fuyao_latency_ms_total"))
        target["fuyao_latency_ms_average"] = f"{total / requests:.3f}" if requests else "0.000"


def _merge_provider_state(current: str | None, value: str) -> str:
    severity = {"idle": 0, "ready": 1, "partial": 2, "error": 3}
    return max((current or "idle", value), key=lambda item: severity.get(item, 0))


def _provider_error_severity(value: str) -> int:
    return {
        "none": 0,
        "not_listed": 1,
        "unsupported": 2,
        "invalid_request": 3,
        "transport": 4,
        "dns": 5,
        "timeout": 6,
        "rate_limit": 7,
        "server": 8,
        "auth": 9,
    }.get(value, 0)


def _safe_health_int(value: object) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0


def _safe_health_float(value: object) -> float:
    try:
        return float(str(value or "0"))
    except ValueError:
        return 0.0


def _merge_health_count_mixes(current: str | None, value: str) -> str:
    counts: Counter[str] = Counter()
    for raw_mix in (current or "", value):
        for raw_item in raw_mix.split(","):
            key, separator, raw_count = raw_item.strip().rpartition("=")
            if not separator or not key:
                continue
            try:
                counts[key] += int(raw_count)
            except ValueError:
                continue
    return _market_data_count_mix(counts)


def _merge_strategy_health(batches: list[list[StrategyHealth]]) -> list[StrategyHealth]:
    grouped: dict[str, list[StrategyHealth]] = {}
    for batch in batches:
        for item in batch:
            grouped.setdefault(item.strategy_id, []).append(item)

    merged: list[StrategyHealth] = []
    for strategy_id, items in grouped.items():
        sample_count = sum(item.sample_count for item in items)
        win_rate = _weighted_average([(item.win_rate_10d, item.sample_count) for item in items])
        avg_10d = _weighted_average([(item.avg_return_10d, item.sample_count) for item in items])
        avg_20d = _weighted_average([(item.avg_return_20d, item.sample_count) for item in items])
        max_losses = [item.max_loss_10d for item in items if item.max_loss_10d is not None]
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


def _merge_sector_strength(items: list[SectorStrength]) -> list[SectorStrength]:
    grouped: dict[tuple[str, str], list[SectorStrength]] = {}
    for item in items:
        grouped.setdefault((item.category, item.industry), []).append(item)

    merged: list[SectorStrength] = []
    for group in grouped.values():
        sample_count = sum(max(item.sample_count, len(item.symbols), 1) for item in group)
        symbols = sorted({symbol for item in group for symbol in item.symbols})
        themes = sorted({theme for item in group for theme in item.themes})
        leaders = sorted(
            [leader for item in group for leader in item.leaders],
            key=lambda leader: leader.change_pct,
            reverse=True,
        )[:5]
        laggards = sorted(
            [laggard for item in group for laggard in item.laggards],
            key=lambda laggard: laggard.change_pct,
        )[:3]
        score = _weighted_average(
            [(item.score, max(item.sample_count, len(item.symbols), 1)) for item in group]
        )
        avg_change_pct = _weighted_average(
            [(item.avg_change_pct, max(item.sample_count, len(item.symbols), 1)) for item in group]
        )
        advance_ratio = _weighted_average(
            [(item.advance_ratio, max(item.sample_count, len(item.symbols), 1)) for item in group]
        )
        representative = max(group, key=lambda item: item.score)
        merged.append(
            SectorStrength(
                industry=representative.industry,
                category=representative.category,
                themes=themes[:8],
                symbols=symbols[:20],
                sample_count=sample_count,
                avg_change_pct=avg_change_pct,
                advance_ratio=advance_ratio,
                total_volume=sum(item.total_volume for item in group),
                score=round(score, 2),
                leaders=leaders,
                laggards=laggards,
                summary=representative.summary,
            )
        )
    return sorted(merged, key=lambda item: item.score, reverse=True)


def _merge_strategy_curve(items: list[StrategyHealth]) -> list[StrategyHealthPoint]:
    grouped: dict[str, list[StrategyHealthPoint]] = {}
    for item in items:
        for point in item.curve:
            grouped.setdefault(point.label, []).append(point)

    curve: list[StrategyHealthPoint] = []
    for label in sorted(grouped):
        points = grouped[label]
        sample_count = sum(point.sample_count for point in points)
        win_rate = _weighted_average([(point.win_rate_10d, point.sample_count) for point in points])
        avg_10d = _weighted_average(
            [(point.avg_return_10d, point.sample_count) for point in points]
        )
        avg_20d = _weighted_average(
            [(point.avg_return_20d, point.sample_count) for point in points]
        )
        max_losses = [point.max_loss_10d for point in points if point.max_loss_10d is not None]
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


def _final_policy_inputs(
    repo: QagentRepository,
    provider: str,
):
    return (
        load_strategy_governance_context(repo),
        load_latest_walk_forward_validation(repo, provider),
        _load_paper_feedback_report(repo, provider),
    )


def _load_paper_feedback_report(
    repo: QagentRepository,
    provider: str,
):
    try:
        paper_repo = PaperTradingRepository(repo.session_factory)
        trades = paper_repo.list_trades(limit=500, provider=provider.strip().lower())
        if not trades:
            return None
        authenticated_ids, authentication_health = authenticated_ranking_v3_paper_trade_ids(
            repo, trades
        )
        if not authenticated_ids:
            return None
        reporting_trades = [trade for trade in trades if trade.trade_id in authenticated_ids]
        account = paper_repo.get_account_settings()
        ledger = build_paper_ledger(
            trades,
            initial_capital=account.initial_capital,
            allocation_per_trade_pct=account.allocation_per_trade_pct,
            max_positions=account.max_positions,
            transaction_cost_bps=account.transaction_cost_bps,
            slippage_bps=account.slippage_bps,
            take_profit_pct=account.take_profit_pct,
            authenticated_trade_ids=authenticated_ids,
        )
        ledger.data_health.update(authentication_health)
        if ledger.data_health.get("paper_reporting_official") == "0":
            return None
        validation = build_paper_validation(trades, ledger)
        report_date = max(
            value
            for trade in reporting_trades
            for value in (
                trade.latest_date,
                trade.exit_date,
                trade.entry_date,
                trade.signal_date,
            )
            if value is not None
        )
        instrument_ids = {trade.instrument_id for trade in reporting_trades}
        asset_types = {
            item.instrument_id: item.asset_type
            for item in repo.list_tradable_instruments(limit=20_000)
            if item.instrument_id in instrument_ids
        }
        return build_paper_daily_report(
            trades=trades,
            ledger=ledger,
            validation=validation,
            as_of=report_date,
            asset_type_by_instrument=asset_types,
        )
    except Exception:
        return None


def _paper_calibration_shadow_payload(
    repo: QagentRepository,
    *,
    provider: str,
    cards: list[OpportunityCard],
    decision_date: date,
    current_market_regime: str,
    current_cohort_id: str | None,
) -> dict[str, object]:
    paper_repo = PaperTradingRepository(repo.session_factory)
    trades = paper_repo.list_trades(limit=5_000, provider=provider)
    cohort_records = repo.get_paper_model_cohorts_for_snapshots(
        [trade.source_snapshot_id for trade in trades]
    )
    cohort_id_by_snapshot = {
        snapshot_id: cohort.cohort_id if cohort is not None else None
        for snapshot_id, cohort in cohort_records.items()
    }
    current_trades = [
        trade
        for trade in trades
        if current_cohort_id is not None
        and cohort_id_by_snapshot.get(trade.source_snapshot_id) == current_cohort_id
    ]
    contexts = {
        trade.trade_id: context
        for trade in current_trades
        if (context := paper_repo.get_trade_source_context(trade.source_snapshot_id)) is not None
    }
    benchmark_id = CN_BENCHMARKS[0].benchmark_id
    eligible_dates = [
        value
        for trade in current_trades
        if trade.entry_date is not None
        and trade.exit_date is not None
        and trade.exit_date < decision_date
        for value in (trade.entry_date, trade.exit_date)
    ]
    benchmark_bars = (
        MarketDataCacheRepository(repo.session_factory).load_daily_bars(
            provider,
            [benchmark_id],
            min(eligible_dates),
            max(eligible_dates),
        )
        if eligible_dates
        else pd.DataFrame()
    )
    candidates = [
        BaselineCandidate(
            instrument_id=card.instrument_id,
            baseline_rank_score=float(card.rank_score),
            primary_strategy_id=card.primary_strategy_id,
            factor_signals=sorted(
                {
                    *card.factor_flags,
                    *(
                        exposure.factor_id
                        for exposure in card.factor_exposures
                        if exposure.score >= 0.65
                    ),
                }
            ),
            market_regime=current_market_regime,
            industry=(card.market_context.industry if card.market_context is not None else None),
            asset_type=card.asset_type,
        )
        for card in cards
    ]
    return build_paper_calibration_shadow_report(
        candidates=candidates,
        trades=trades,
        cohort_id_by_snapshot=cohort_id_by_snapshot,
        current_cohort_id=current_cohort_id,
        source_context_by_trade=contexts,
        benchmark_bars=benchmark_bars,
        decision_date=decision_date,
        current_market_regime=current_market_regime,
    ).model_dump(mode="json")


def _safe_paper_calibration_shadow_payload(
    repo: QagentRepository,
    *,
    provider: str,
    cards: list[OpportunityCard],
    decision_date: date,
    current_market_regime: str,
    current_cohort_id: str | None,
) -> dict[str, object]:
    try:
        return _paper_calibration_shadow_payload(
            repo,
            provider=provider,
            cards=cards,
            decision_date=decision_date,
            current_market_regime=current_market_regime,
            current_cohort_id=current_cohort_id,
        )
    except Exception:
        return PaperCalibrationShadowReport(
            model_version=BASELINE_CHALLENGER_VERSION,
            cohort_id=current_cohort_id,
            decision_date=decision_date,
            current_market_regime=current_market_regime,
            model_ready=False,
            minimum_training_samples=MIN_BASELINE_TRAINING_SAMPLES,
            current_cohort_trade_count=0,
            eligible_closed_trade_count=0,
            excluded_future_trade_count=0,
            benchmark_matched_trade_count=0,
            benchmark_missing_trade_count=0,
            reason="shadow_report_unavailable",
            decision=BaselineDecision(
                decision_date=decision_date,
                training_sample_count=0,
                model_ready=False,
            ),
            data_health={
                "paper_calibration_shadow_status": "unavailable",
                "paper_calibration_shadow_mode": "shadow_only",
                "paper_calibration_shadow_paper_write_effect": "none",
                "paper_calibration_shadow_selection_effect": "none",
                "paper_calibration_shadow_order_effect": "none",
                "paper_calibration_shadow_weight_effect": "none",
                "paper_calibration_shadow_training_samples": "0",
                "paper_calibration_shadow_excluded_future": "0",
                "paper_calibration_shadow_benchmark_missing": "0",
            },
        ).model_dump(mode="json")
