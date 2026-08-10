from copy import deepcopy
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import json
import math
from multiprocessing import get_context
from threading import Lock
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError

from qagent.agent.responder import answer_question
from qagent.api.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    AlertEvaluationRequest,
    FactorResearchExperimentRequest,
    PaperSessionStartRequest,
    PaperTradeFromOpportunityRequest,
    StrategyGovernanceResponse,
)
from qagent.backtesting.engine import run_historical_backtest
from qagent.backtesting.experiment import (
    WalkForwardExperimentManifest,
    build_walk_forward_experiment_manifest,
    record_walk_forward_runtime_revision,
    upgrade_walk_forward_execution_manifest,
    walk_forward_manifests_semantically_compatible,
    walk_forward_selection_manifests_semantically_compatible,
)
from qagent.backtesting.portfolio import run_portfolio_backtest
from qagent.backtesting.ranking_v3 import RankingV3FrozenScoringArtifact
from qagent.backtesting.ranking_v3_evidence import (
    RankingV3RepositoryEvidenceAuthority,
    ranking_v3_data_revision,
)
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardConflictError,
    RankingV3ForwardIdentity,
    RankingV3ForwardStateError,
    RankingV3ForwardValidator,
)
from qagent.backtesting.ranking_v3_production import (
    RankingV3ProductionIdentity,
    RankingV3ProductionIntegrityError,
)
from qagent.backtesting.ranking_v3_protocol import (
    RANKING_V3_REBALANCE_STEP_SESSIONS,
    RankingV3Protocol,
    build_ranking_v3_protocol,
    ranking_v3_protocol_digest_is_valid,
)
from qagent.backtesting.sensitivity import build_parameter_sensitivity
from qagent.backtesting.walk_forward import (
    MIN_FULL_MARKET_COVERAGE_RATIO,
    MIN_FUNDAMENTAL_COVERAGE_RATIO,
    WalkForwardProgress,
    WalkForwardSnapshot,
    run_full_market_walk_forward_selection,
)
from qagent.briefing.daily import DailyBrief, build_daily_brief
from qagent.briefing.export import render_daily_brief_markdown
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.providers import FreeCatalystProvider
from qagent.config import get_settings
from qagent.data_management import build_historical_coverage_manifest
from qagent.db import create_session_factory, initialize_database
from qagent.domain.models import OpportunityCard, PortfolioPlan, SectorStrength
from qagent.factors.backtest import run_factor_backtest, run_factor_diagnostics
from qagent.research.factor_experiments import (
    FactorResearchConfig,
    current_code_revision,
    execute_factor_research_experiment,
    resolved_config,
)
from qagent.research.factor_shadow_outcomes import (
    build_factor_shadow_evaluation,
    resolve_factor_shadow_outcomes,
)
from qagent.jobs.automation import run_research_automation
from qagent.jobs.automation_scheduler import (
    AutoProcessingCycleResult,
    AutoProcessingSettings,
    AutomationScheduler,
)
from qagent.jobs.daily_scan import DailyScanResult, run_daily_scan
from qagent.jobs.full_market import (
    build_full_market_batch_symbols,
    full_market_batch_cache_key,
    run_full_market_batch_scan_job,
    run_full_market_scan,
    sync_cn_tradable_catalog,
)
from qagent.jobs.historical_data import run_historical_backfill_job
from qagent.jobs.alert_runner import run_alert_rules
from qagent.jobs.intraday_check import evaluate_snapshot_alerts
from qagent.jobs.ranking_v3_forward import run_ranking_v3_forward_day
from qagent.jobs.ranking_v3_production import (
    RankingV3ProductionSnapshotUnavailable,
    run_ranking_v3_production_day,
)
from qagent.jobs.task_manager import TaskManager
from qagent.market.a_share_universe import (
    ResolvedSymbols,
    resolve_symbol_tokens,
)
from qagent.market.instruments import format_instrument_label
from qagent.market.instruments import market_symbol
from qagent.market.rotation_radar import MarketRotationRadar, build_rotation_radar
from qagent.market.tradable import search_cn_tradable_instruments
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.market.universes import UniverseCreate, builtin_universes, merge_universes
from qagent.market.indicators import add_moving_averages, add_volume_ratio, percent_distance
from qagent.market.calendars import trading_sessions_in_range
from qagent.market.benchmarks import (
    benchmark_ids,
    benchmark_proxy_ids,
    benchmark_frames_from_bars,
    build_benchmark_comparison_for_card,
    benchmark_items_for_return_from_bars,
)
from qagent.market.cn_context import infer_etf_exposure_group
from qagent.market.etf_exposure import EtfExposureService
from qagent.monitoring.outcomes import (
    OpportunityOutcome,
    compute_opportunity_outcome,
    diagnose_strategy_performance,
    summarize_recommendation_closure,
    summarize_strategy_performance,
)
from qagent.monitoring.followthrough import build_recommendation_followthrough_center
from qagent.monitoring.recommendation_calibration import (
    build_recommendation_calibration_center,
)
from qagent.monitoring.signal_monitor import SignalMonitorCenter, build_signal_monitor_center
from qagent.monitoring.portfolio import PositionInput, analyze_position_risk
from qagent.monitoring.lookthrough import (
    PortfolioLookThroughHolding,
    build_portfolio_lookthrough_risk,
)
from qagent.monitoring.alerts import AlertRule, suggest_alert_rules
from qagent.paper_trading.engine import (
    PaperDailyReport,
    build_paper_risk_gate_status,
    build_paper_daily_report,
    build_paper_ledger,
    build_paper_validation,
    paper_execution_data_health,
    paper_price_basis_gap_limit,
    paper_snapshot_price_basis_is_consistent,
    seed_paper_trades_from_snapshots,
    update_paper_trades,
    summarize_paper_trades,
)
from qagent.paper_trading.admission import evaluate_paper_snapshot_admission
from qagent.paper_trading.dual_track import (
    build_dual_track_report,
    select_daily_top_recommendations,
)
from qagent.paper_trading.execution_audit import (
    PaperExecutionRuleAudit,
    build_paper_execution_rule_audit,
)
from qagent.providers.factory import build_market_data_provider
from qagent.providers.status import build_provider_status
from qagent.recommendations.enrichment import enrich_opportunity_card
from qagent.recommendations.brief import apply_recommendation_briefs
from qagent.recommendations.feedback import (
    authenticated_ranking_v3_paper_trade_ids,
    authenticated_ranking_v3_snapshot_sources,
    build_recent_recommendation_feedback_center,
)
from qagent.recommendations.governance import (
    CardStrategyGovernance,
    apply_final_recommendation_policy,
    build_strategy_governance_status,
    governed_card_payloads,
    load_latest_walk_forward_validation,
    load_strategy_governance_context,
)
from qagent.recommendations.portfolio import build_portfolio_plan
from qagent.recommendations.probability import (
    apply_probability_calibration,
    probability_calibration_data_health,
)
from qagent.recommendations.quality_gate import (
    apply_recommendation_quality_gate,
    recommendation_quality_data_health,
)
from qagent.recommendations.rotation import sort_recommendation_cards
from qagent.recommendations.signal_hub import build_signal_hub
from qagent.research.action_center import build_manual_action_center
from qagent.research.alpha_quality import build_alpha_quality_center
from qagent.research.command_center import build_research_command_center
from qagent.research.decision_quality import build_decision_quality_center
from qagent.research.market_intelligence import MarketIntelligenceCenter
from qagent.research.market_intelligence import (
    apply_market_intelligence_to_cards,
    build_market_intelligence_center,
)
from qagent.research.operational_readiness import build_operational_readiness_center
from qagent.research.paper_forward_report import (
    build_paper_forward_comparison,
    build_paper_research_baseline_definition,
)
from qagent.storage.paper import (
    PaperAccountSettings,
    PaperTradeAdmissionProof,
    PaperTradeRecord,
    PaperTradingRepository,
)
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository
from qagent.storage.ranking_v3_production import RankingV3ProductionRepository
from qagent.storage.repository import (
    AlertRuleCreate,
    OpportunitySnapshotRecord,
    PaperModelCohortRecord,
    PositionCreate,
    QagentRepository,
    ScanCheckpointMaintenanceReport,
    ScanResultCacheRecord,
    WatchlistCreate,
    paper_model_cohort_from_data_health,
)
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.ranking_v4_forward_evidence import RankingV4EvidenceRepository
from qagent.storage.ranking_v4_prospective_release import (
    RankingV4ProspectiveReleaseRepository,
)
from qagent.storage.factor_research import FactorResearchRepository
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.strategy_data.models import FundamentalSnapshot
from qagent.strategy_data.providers import EmptyStrategyDataProvider, build_strategy_data_provider
from qagent.strategies.models import StrategyHealth

router = APIRouter()

PAPER_MAX_PER_INDUSTRY = 2
PAPER_RISK_OFF_POSITION_SIZE_MULTIPLIER = Decimal("0.35")
PAPER_CANDIDATE_POST_CLOSE_REFRESH_TIME = time(hour=15, minute=45)
PAPER_CANDIDATE_SETTLEMENT_RETRY_TIME = time(hour=18)
PAPER_CANDIDATE_MAX_REFRESH_ATTEMPTS = 2
_UNKNOWN_PAPER_INDUSTRIES = {
    "",
    "-",
    "unknown",
    "unclassified",
    "其他",
    "未知",
    "未知etf暴露",
}
_task_manager = TaskManager()
_task_executor = ThreadPoolExecutor(max_workers=2)
_history_task_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-backfill")
_factor_research_task_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="factor-research",
)
_historical_jobs_lock = Lock()
_submitted_historical_jobs: set[str] = set()
_full_market_jobs_lock = Lock()
_submitted_full_market_jobs: set[str] = set()
_full_market_task_executor: ProcessPoolExecutor | None = None
_latest_full_market_result_lock = Lock()
_latest_full_market_result_cache: dict[
    tuple[str, str, bool, int, int],
    tuple[float, dict[str, object]],
] = {}
_LATEST_FULL_MARKET_RESULT_CACHE_TTL_SECONDS = 10.0
_walk_forward_task_executor: ProcessPoolExecutor | None = None
_walk_forward_jobs_lock = Lock()
_submitted_walk_forward_jobs: set[str] = set()
_factor_research_jobs_lock = Lock()
_submitted_factor_research_jobs: set[str] = set()
_automation_scheduler = AutomationScheduler()
_etf_exposure_service = EtfExposureService()
_ranking_v3_forward_runtime_lock = Lock()
_ranking_v3_forward_runtime_status: dict[str, dict[str, object]] = {}


class _WalkForwardJobCancelled(RuntimeError):
    pass


def _walk_forward_executor() -> ProcessPoolExecutor:
    """Create the isolated worker only when a walk-forward job is submitted."""

    global _walk_forward_task_executor
    if _walk_forward_task_executor is None:
        _walk_forward_task_executor = ProcessPoolExecutor(
            max_workers=1,
            mp_context=get_context("spawn"),
        )
    return _walk_forward_task_executor


def _full_market_executor() -> ProcessPoolExecutor:
    """Keep long provider scans isolated from the API and scheduler process."""

    global _full_market_task_executor
    if _full_market_task_executor is None:
        _full_market_task_executor = ProcessPoolExecutor(
            max_workers=1,
            mp_context=get_context("spawn"),
        )
    return _full_market_task_executor


def _release_factor_research_submission(experiment_id: str) -> None:
    with _factor_research_jobs_lock:
        _submitted_factor_research_jobs.discard(experiment_id)


def _release_full_market_submission(job_id: str) -> None:
    with _full_market_jobs_lock:
        _submitted_full_market_jobs.discard(job_id)


def _terminate_full_market_executor() -> bool:
    """Terminate a stalled scan worker without taking down the API scheduler."""

    global _full_market_task_executor
    with _full_market_jobs_lock:
        executor = _full_market_task_executor
        _full_market_task_executor = None
        _submitted_full_market_jobs.clear()
    if executor is None:
        return False
    terminated = False
    for process in list(getattr(executor, "_processes", {}).values()):
        if process.is_alive():
            process.terminate()
            terminated = True
    shutdown = getattr(executor, "shutdown", None)
    if callable(shutdown):
        shutdown(wait=False, cancel_futures=True)
    return terminated


def _terminate_walk_forward_executor() -> bool:
    """Terminate the single isolated worker after its persisted job is cancelled."""

    global _walk_forward_task_executor
    with _walk_forward_jobs_lock:
        executor = _walk_forward_task_executor
        _walk_forward_task_executor = None
    if executor is None:
        return False
    terminated = False
    for process in list(getattr(executor, "_processes", {}).values()):
        if process.is_alive():
            process.terminate()
            terminated = True
    executor.shutdown(wait=False, cancel_futures=True)
    return terminated


def _submit_full_market_scan_job(job_id: str) -> bool:
    with _full_market_jobs_lock:
        if job_id in _submitted_full_market_jobs:
            return False
        _submitted_full_market_jobs.add(job_id)
    repo = _repo()
    job = repo.get_full_market_scan_job(job_id)
    if job is not None:
        repo.update_full_market_scan_job(
            job_id,
            data_health={
                **job.data_health,
                "full_market_worker_submitted": "true",
                "full_market_worker_submitted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    try:
        future = _full_market_executor().submit(
            _run_submitted_full_market_scan_job,
            job_id,
        )
        if future is not None and hasattr(future, "add_done_callback"):
            future.add_done_callback(
                lambda _future: _release_full_market_submission(job_id)
            )
    except Exception:
        with _full_market_jobs_lock:
            _submitted_full_market_jobs.discard(job_id)
        raise
    return True


def _run_submitted_full_market_scan_job(job_id: str) -> None:
    try:
        run_full_market_batch_scan_job(job_id)
    except Exception as exc:
        repo = _repo()
        job = repo.get_full_market_scan_job(job_id)
        if job is not None:
            repo.update_full_market_scan_job(
                job_id,
                status="failed",
                message=f"Full-market worker failed: {str(exc)[:400]}",
                data_health={
                    **job.data_health,
                    "full_market_worker_failed": "true",
                    "full_market_worker_error": str(exc)[:500],
                    "full_market_worker_failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
    finally:
        with _full_market_jobs_lock:
            _submitted_full_market_jobs.discard(job_id)


def restore_full_market_scan_job_from_storage() -> list[str]:
    repo = _repo()
    restored: list[str] = []
    for provider in ("free", "fixture"):
        job = repo.get_latest_full_market_scan_job(provider=provider)
        if job is None:
            continue
        if job.data_health.get("automatic_scan_aborted") == "true":
            if job.status in {"queued", "running"}:
                repo.update_full_market_scan_job(
                    job.job_id,
                    status="failed",
                    message="Aborted full-market scan was not restored",
                    data_health=job.data_health,
                )
            continue
        recoverable = job.status == "running" or (
            job.status == "queued" and job.data_health.get("full_market_worker_submitted") == "true"
        )
        if not recoverable:
            continue
        repo.update_full_market_scan_job(
            job.job_id,
            status="queued",
            message="Restoring full-market scan after service restart",
            data_health={
                **job.data_health,
                "full_market_restart_recovery": "queued_for_checkpoint_resume",
                "full_market_restart_recovery_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if _submit_full_market_scan_job(job.job_id):
            restored.append(job.job_id)
    return restored


def _signal_summary(card) -> str:
    return "; ".join(
        f"{signal.signal_type.value} {signal.direction.value} {signal.score:.2f}"
        for signal in card.signals[:4]
    )


def _strategy_summary(card) -> str:
    return "; ".join(
        f"{strategy.strategy_id} {strategy.status} {strategy.score:.2f}"
        for strategy in card.strategy_evaluations[:5]
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/provider-status")
def provider_status() -> dict[str, list[object]]:
    return {"providers": [status.model_dump(mode="json") for status in build_provider_status()]}


@router.get("/data-cache")
def data_cache(
    provider: str | None = None,
    instrument_id: str | None = None,
) -> dict[str, list[object]]:
    summaries = _market_cache_repo().list_summaries(
        provider_mode=provider.strip().lower() if provider else None,
        instrument_id=instrument_id.strip().upper() if instrument_id else None,
    )
    return {"summaries": [summary.model_dump(mode="json") for summary in summaries]}


@router.delete("/data-cache")
def clear_data_cache(
    provider: str | None = None,
    instrument_id: str | None = None,
) -> dict[str, int]:
    deleted = _market_cache_repo().delete(
        provider_mode=provider.strip().lower() if provider else None,
        instrument_id=instrument_id.strip().upper() if instrument_id else None,
    )
    return {"deleted": deleted}


@router.get(
    "/storage/full-market-checkpoints",
    response_model=ScanCheckpointMaintenanceReport,
)
def full_market_checkpoint_storage(
    retention_days: int = 14,
) -> ScanCheckpointMaintenanceReport:
    try:
        return _repo().maintain_full_market_scan_checkpoints(
            retention_days=retention_days,
            dry_run=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/storage/full-market-checkpoints/maintenance",
    response_model=ScanCheckpointMaintenanceReport,
)
def maintain_full_market_checkpoint_storage(
    retention_days: int = 14,
    dry_run: bool = True,
) -> ScanCheckpointMaintenanceReport:
    try:
        return _repo().maintain_full_market_scan_checkpoints(
            retention_days=retention_days,
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/historical-data/coverage")
def historical_data_coverage(
    start: date,
    end: date,
    provider: str = "free",
    symbols: str | None = None,
    max_symbols: int = 200,
    include_etfs: bool = True,
) -> dict[str, object]:
    mode = _validate_historical_data_params(provider, start, end, max_symbols)
    instrument_ids = _historical_data_symbols(
        mode,
        symbols,
        max_symbols=max_symbols,
        include_etfs=include_etfs,
    )
    manifest = build_historical_coverage_manifest(
        repo=_repo(),
        cache=_market_cache_repo(),
        provider_mode=mode,
        instrument_ids=instrument_ids,
        start=start,
        end=end,
    )
    return manifest.model_dump(mode="json")


def _walk_forward_status_lookup(repo: QagentRepository):
    def lookup(job_id: str) -> str | None:
        job = repo.get_walk_forward_job(job_id)
        return job.status if job is not None else None

    return lookup


@router.post("/walk-forward/runs")
def run_walk_forward(
    start: date,
    end: date,
    provider: str = "free",
    run_id: str | None = None,
    step_sessions: int = 10,
    lookback_days: int = 400,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if mode != "free":
        raise HTTPException(status_code=400, detail="walk-forward only supports free A-share data")
    if start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    if lookback_days <= 0:
        raise HTTPException(status_code=400, detail="lookback_days must be positive")
    if step_sessions != RANKING_V3_REBALANCE_STEP_SESSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "strict Ranking V3 validation requires "
                f"step_sessions={RANKING_V3_REBALANCE_STEP_SESSIONS}"
            ),
        )
    initialize_database()
    repo = _repo()
    repository = ReplayEvidenceRepository(
        repo.session_factory,
        mode,
        run_status_lookup=_walk_forward_status_lookup(repo),
    )
    owner_run_id = (
        run_id or f"walk-forward-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    )
    try:
        result = run_full_market_walk_forward_selection(
            repository,
            owner_run_id=owner_run_id,
            start=start,
            end=end,
            rebalance_step_sessions=step_sessions,
            lookback_days=lookback_days,
            snapshot_workers=get_settings().walk_forward_snapshot_workers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record = _repo().save_walk_forward_run(result)
    return record.model_dump(mode="json")


@router.post("/walk-forward/jobs")
def start_walk_forward_job(
    start: date,
    end: date,
    provider: str = "free",
    step_sessions: int = 10,
    lookback_days: int = 400,
) -> dict[str, object]:
    mode = _validate_walk_forward_params(
        provider,
        start,
        end,
        step_sessions,
        lookback_days,
    )
    return _walk_forward_job_payload(
        _create_or_get_walk_forward_job(
            repo=_repo(),
            provider=mode,
            start=start,
            end=end,
            step_sessions=step_sessions,
            lookback_days=lookback_days,
        )
    )


def _create_or_get_walk_forward_job(
    *,
    repo: QagentRepository,
    provider: str,
    start: date,
    end: date,
    step_sessions: int,
    lookback_days: int,
):
    replay_repository = ReplayEvidenceRepository(repo.session_factory, provider)
    revision = replay_repository.current_revision()
    if revision <= 0:
        raise HTTPException(status_code=400, detail="historical replay dataset is empty")
    sessions = trading_sessions_in_range(start, end)[::step_sessions]
    if not sessions:
        raise HTTPException(status_code=400, detail="validation range has no trading sessions")
    job_id = f"walk-forward-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    manifest = build_walk_forward_experiment_manifest(
        provider_mode=provider,
        dataset_revision=revision,
        start_date=start,
        end_date=end,
        rebalance_step_sessions=step_sessions,
        lookback_days=lookback_days,
    )
    active = next(
        (
            job
            for job in repo.list_walk_forward_jobs(provider=provider, limit=100)
            if job.status in {"queued", "running"}
            and job.dataset_revision == revision
            and job.start_date == start
            and job.end_date == end
            and job.rebalance_step_sessions == step_sessions
            and job.lookback_days == lookback_days
            and _walk_forward_manifest_payload_matches(
                job.experiment_manifest,
                manifest,
            )
        ),
        None,
    )
    if active is not None:
        return active
    job = repo.create_walk_forward_job(
        job_id=job_id,
        provider=provider,
        start=start,
        end=end,
        dataset_revision=revision,
        rebalance_step_sessions=step_sessions,
        lookback_days=lookback_days,
        total_snapshots=len(sessions),
        experiment_manifest=manifest.model_dump(mode="json"),
    )
    reusable_checkpoints = _reusable_walk_forward_checkpoints(
        repo,
        manifest=manifest,
        sessions=sessions,
    )
    if reusable_checkpoints:
        job = repo.update_walk_forward_job(
            job.job_id,
            processed_snapshots=len(reusable_checkpoints),
            current_date=date.fromisoformat(reusable_checkpoints[-1]["decision_date"]),
            checkpoints=reusable_checkpoints,
        )
    _submit_walk_forward_job(job.job_id)
    return job


def _reusable_walk_forward_checkpoints(
    repo: QagentRepository,
    *,
    manifest: WalkForwardExperimentManifest,
    sessions: list[date],
) -> list[dict[str, object]]:
    expected_dates = [item.isoformat() for item in sessions]
    candidates: list[list[dict[str, object]]] = []
    for job in repo.list_walk_forward_jobs(
        provider=manifest.provider_mode,
        limit=100,
    ):
        if (
            job.status != "succeeded"
            or not job.checkpoints
            or not _walk_forward_selection_manifest_payload_matches(
            job.experiment_manifest,
            manifest,
            )
        ):
            continue
        checkpoints = _ordered_reusable_checkpoint_prefix(
            job.checkpoints,
            expected_dates=expected_dates,
        )
        if checkpoints:
            candidates.append(checkpoints)
    try:
        completed_runs = repo.list_walk_forward_runs(
            provider=manifest.provider_mode,
            limit=20,
        )
    except ValueError:
        # A prior protocol can become intentionally unreadable after its
        # result schema changes. It is not valid checkpoint evidence for the
        # current protocol, so skip run reuse without weakening validation.
        completed_runs = []
    for run in completed_runs:
        if run.status != "succeeded":
            continue
        snapshots = run.payload.get("snapshots")
        if not isinstance(snapshots, list):
            continue
        stored_manifest = run.payload.get("experiment_manifest")
        if not isinstance(stored_manifest, dict) or not (
            run.dataset_revision == manifest.dataset_revision
            and run.start_date == manifest.start_date
            and run.end_date == manifest.end_date
            and run.rebalance_step_sessions == manifest.rebalance_step_sessions
            and run.lookback_days == manifest.lookback_days
            and _walk_forward_selection_manifest_payload_matches(
                stored_manifest,
                manifest,
            )
        ):
            continue
        checkpoints = _ordered_reusable_checkpoint_prefix(
            snapshots,
            expected_dates=expected_dates,
        )
        if checkpoints:
            candidates.append(checkpoints)
    return max(candidates, key=len, default=[])


def _ordered_reusable_checkpoint_prefix(
    payloads: list[dict[str, object]],
    *,
    expected_dates: list[str],
) -> list[dict[str, object]]:
    by_date = {
        item["decision_date"]: item
        for item in payloads
        if isinstance(item, dict) and isinstance(item.get("decision_date"), str)
    }
    checkpoints = [
        by_date[decision_date] for decision_date in expected_dates if decision_date in by_date
    ]
    checkpoint_dates = [item["decision_date"] for item in checkpoints]
    if checkpoint_dates != expected_dates[: len(checkpoint_dates)]:
        return []
    return checkpoints


def _walk_forward_run_matches_manifest(run, manifest: WalkForwardExperimentManifest) -> bool:
    stored_manifest = run.payload.get("experiment_manifest")
    if not isinstance(stored_manifest, dict):
        return False
    return bool(
        run.dataset_revision == manifest.dataset_revision
        and run.start_date == manifest.start_date
        and run.end_date == manifest.end_date
        and run.rebalance_step_sessions == manifest.rebalance_step_sessions
        and run.lookback_days == manifest.lookback_days
        and _walk_forward_manifest_payload_matches(stored_manifest, manifest)
    )


def _walk_forward_manifest_payload_matches(
    payload: dict[str, object],
    current: WalkForwardExperimentManifest,
) -> bool:
    try:
        stored = WalkForwardExperimentManifest.model_validate(payload)
    except (TypeError, ValueError):
        return False
    return walk_forward_manifests_semantically_compatible(stored, current)


def _walk_forward_selection_manifest_payload_matches(
    payload: dict[str, object],
    current: WalkForwardExperimentManifest,
) -> bool:
    try:
        stored = WalkForwardExperimentManifest.model_validate(payload)
    except (TypeError, ValueError):
        return False
    return walk_forward_selection_manifests_semantically_compatible(
        stored,
        current,
    )


@router.get("/walk-forward/jobs")
def list_walk_forward_jobs(
    provider: str = "free",
    limit: int = 20,
) -> dict[str, object]:
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    jobs = _repo().list_walk_forward_jobs(
        provider=provider.strip().lower(),
        limit=limit,
    )
    return {"jobs": [_walk_forward_job_payload(job) for job in jobs]}


@router.get("/walk-forward/jobs/latest")
def latest_walk_forward_job(provider: str = "free") -> dict[str, object]:
    jobs = _repo().list_walk_forward_jobs(
        provider=provider.strip().lower(),
        limit=1,
    )
    if not jobs:
        raise HTTPException(status_code=404, detail="walk-forward job not found")
    return _walk_forward_job_payload(jobs[0])


@router.get("/walk-forward/jobs/{job_id}")
def get_walk_forward_job(job_id: str) -> dict[str, object]:
    job = _repo().get_walk_forward_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="walk-forward job not found")
    return _walk_forward_job_payload(job)


@router.get("/ranking-v4/evidence/{epoch_id}")
def get_ranking_v4_evidence(epoch_id: str) -> dict[str, object]:
    snapshot = _ranking_v4_evidence_repo().load_snapshot(epoch_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="Ranking V4 evidence epoch not found",
        )
    definition = snapshot.definition
    latest_inventory = snapshot.inventories[-1] if snapshot.inventories else None
    latest_return = snapshot.return_records[-1] if snapshot.return_records else None
    latest_proof = snapshot.proofs[-1] if snapshot.proofs else None
    release_repository = _ranking_v4_release_repo()
    policy = release_repository.load_policy(definition.definition_digest)
    execution_summaries = release_repository.load_execution_summaries(
        definition.definition_digest
    )
    release_proofs = release_repository.load_release_proofs(
        definition.definition_digest
    )
    latest_release = release_proofs[-1] if release_proofs else None
    return {
        "epoch_id": definition.identity.epoch_id,
        "status": "frozen",
        "code_revision": definition.identity.code_revision,
        "protocol_digest": definition.identity.protocol_digest,
        "experiment_registry_digest": (
            definition.identity.experiment_registry_digest
        ),
        "dataset_revision": definition.identity.dataset_revision,
        "base_dataset_revision": definition.identity.dataset_revision,
        "latest_dataset_revision": (
            latest_return.dataset_revision
            if latest_return
            else definition.identity.dataset_revision
        ),
        "evidence_start_date": (
            definition.identity.evidence_start_date.isoformat()
        ),
        "definition_digest": definition.definition_digest,
        "inventory_count": len(snapshot.inventories),
        "latest_inventory_digest": (
            latest_inventory.inventory_digest if latest_inventory else None
        ),
        "common_date_count": len(snapshot.return_records),
        "latest_common_date": (
            latest_return.rebalance_date.isoformat() if latest_return else None
        ),
        "proof_count": len(snapshot.proofs),
        "latest_proof_digest": (
            latest_proof.proof_digest if latest_proof else None
        ),
        "release_policy_digest": policy.policy_digest if policy else None,
        "registered_checkpoints": (
            list(policy.checkpoint_common_date_counts) if policy else []
        ),
        "execution_summary_count": len(execution_summaries),
        "latest_execution_summary_digest": (
            execution_summaries[-1].summary_digest
            if execution_summaries
            else None
        ),
        "release_evaluation_count": len(release_proofs),
        "latest_release_proof_digest": (
            latest_release.release_proof_digest if latest_release else None
        ),
        "release_evaluation_status": (
            latest_release.evaluation_status if latest_release else None
        ),
        "release_scope": (
            latest_release.release_scope if latest_release else "shadow_only"
        ),
        "official_release_allowed": (
            latest_release.official_release_allowed if latest_release else False
        ),
    }


@router.post("/walk-forward/jobs/{job_id}/cancel")
def cancel_walk_forward_job(job_id: str) -> dict[str, object]:
    repo = _repo()
    job = repo.get_walk_forward_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="walk-forward job not found")
    if job.status == "cancelled":
        return _walk_forward_job_payload(job)
    if job.status not in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail=f"walk-forward job cannot be cancelled from {job.status}",
        )
    cancelled = repo.update_walk_forward_job(
        job_id,
        status="cancelled",
        phase="cancelled",
        error="validation cancelled before publication",
        finished_at=datetime.now(timezone.utc),
    )
    if job.status == "running":
        _terminate_walk_forward_executor()
    return _walk_forward_job_payload(cancelled)


@router.post("/walk-forward/jobs/{job_id}/retry")
def retry_walk_forward_job(job_id: str) -> dict[str, object]:
    repo = _repo()
    job = repo.get_walk_forward_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="walk-forward job not found")
    if job.status in {"queued", "running"}:
        _submit_walk_forward_job(job.job_id)
        return _walk_forward_job_payload(job)
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(
            status_code=409,
            detail=f"walk-forward job cannot resume from {job.status}",
        )
    revision = ReplayEvidenceRepository(
        repo.session_factory,
        job.provider,
    ).current_revision()
    if revision != job.dataset_revision:
        raise HTTPException(
            status_code=409,
            detail="historical dataset revision changed; start a new validation job",
        )
    current_manifest = build_walk_forward_experiment_manifest(
        provider_mode=job.provider,
        dataset_revision=job.dataset_revision,
        start_date=job.start_date,
        end_date=job.end_date,
        rebalance_step_sessions=job.rebalance_step_sessions,
        lookback_days=job.lookback_days,
    )
    try:
        stored_manifest = WalkForwardExperimentManifest.model_validate(job.experiment_manifest)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="walk-forward experiment definition changed; start a new validation job",
        ) from exc
    if walk_forward_manifests_semantically_compatible(
        stored_manifest,
        current_manifest,
    ):
        resumed_manifest = record_walk_forward_runtime_revision(
            stored_manifest,
            current_manifest,
        )
    elif job.checkpoints and walk_forward_selection_manifests_semantically_compatible(
        stored_manifest,
        current_manifest,
    ):
        resumed_manifest = upgrade_walk_forward_execution_manifest(
            stored_manifest,
            current_manifest,
        )
    else:
        raise HTTPException(
            status_code=409,
            detail="walk-forward experiment definition changed; start a new validation job",
        )
    resumed = repo.update_walk_forward_job(
        job.job_id,
        status="queued",
        phase="queued",
        experiment_manifest=resumed_manifest.model_dump(mode="json"),
        clear_terminal_state=True,
    )
    _submit_walk_forward_job(resumed.job_id)
    return _walk_forward_job_payload(resumed)


@router.get("/walk-forward/runs")
def list_walk_forward_runs(
    provider: str = "free",
    limit: int = 20,
) -> dict[str, object]:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be positive")
    records = _repo().list_walk_forward_runs(
        provider=provider.strip().lower(),
        limit=limit,
    )
    return {"runs": [record.model_dump(mode="json") for record in records]}


@router.get("/walk-forward/runs/latest")
def latest_walk_forward_run(provider: str = "free") -> dict[str, object]:
    records = _repo().list_walk_forward_runs(
        provider=provider.strip().lower(),
        limit=1,
    )
    if not records:
        raise HTTPException(status_code=404, detail="walk-forward run not found")
    return records[0].model_dump(mode="json")


@router.get("/walk-forward/runs/{run_id}")
def get_walk_forward_run(run_id: str) -> dict[str, object]:
    record = _repo().get_walk_forward_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="walk-forward run not found")
    return record.model_dump(mode="json")


@router.get("/ranking-v3/forward/state")
def ranking_v3_forward_state(
    run_id: str | None = None,
) -> dict[str, object]:
    repo = _repo()
    try:
        context = _ranking_v3_forward_context(repo, run_id=run_id)
        return _ranking_v3_forward_state_payload(repo, context)
    except (
        LookupError,
        RankingV3ForwardConflictError,
        RankingV3ForwardStateError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Ranking V3 forward ledger repository conflict",
        ) from exc


@router.post("/ranking-v3/forward/run-once")
def run_ranking_v3_forward_once(
    run_id: str | None = None,
    session_date: date | None = None,
) -> dict[str, object]:
    repo = _repo()
    try:
        context = _ranking_v3_forward_context(repo, run_id=run_id)
        if context is None:
            raise RankingV3ForwardStateError(
                "no successful Ranking V3 run is eligible for forward validation"
            )
        target_date = session_date or _a_share_today()
        processed = _run_ranking_v3_forward_catch_up(
            repo,
            build_market_data_provider("free"),
            context,
            through_date=target_date,
        )
        state = _ranking_v3_forward_state_payload(repo, context)
    except (
        LookupError,
        RankingV3ForwardConflictError,
        RankingV3ForwardStateError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Ranking V3 forward ledger repository conflict",
        ) from exc
    return {
        **state,
        "processed_session_count": len(processed),
        "processed_session_dates": [
            result.session_date.isoformat() for result in processed
        ],
    }


def _ranking_v3_forward_context(
    repo: QagentRepository,
    *,
    run_id: str | None = None,
) -> tuple[object, Mapping[str, object], RankingV3Protocol] | None:
    if run_id is not None:
        records = [repo.get_walk_forward_run(run_id)]
    else:
        active_run_id = _ranking_v3_bound_validation_run_id(repo)
        records = (
            [repo.get_walk_forward_run(active_run_id)]
            if active_run_id is not None
            else repo.list_walk_forward_runs(provider="free", limit=1)
        )
    explicit_error = "requested walk-forward run is not eligible for Ranking V3 forward validation"
    for record in records:
        if record is None or getattr(record, "status", None) != "succeeded":
            continue
        payload = getattr(record, "payload", None)
        ranking = payload.get("ranking_v3") if isinstance(payload, Mapping) else None
        if not isinstance(ranking, Mapping) or ranking.get("status") not in {
            "forward_validation_pending",
            "shadow_candidate",
        }:
            continue
        try:
            protocol_payload = ranking.get("protocol")
            if not isinstance(protocol_payload, Mapping):
                raise ValueError("Ranking V3 protocol is missing")
            protocol = RankingV3Protocol.model_validate(protocol_payload)
            if not ranking_v3_protocol_digest_is_valid(protocol):
                raise ValueError("Ranking V3 protocol digest is invalid")
            if ranking.get("model_version") != protocol.model_version:
                raise ValueError("Ranking V3 model version does not match its protocol")
            artifact_payload = ranking.get("forward_scoring_artifact")
            if not isinstance(artifact_payload, Mapping):
                raise ValueError("Ranking V3 frozen scoring artifact is missing")
            artifact = RankingV3FrozenScoringArtifact.model_validate(artifact_payload)
            if (
                artifact.stable_digest != ranking.get("forward_scoring_artifact_digest")
                or artifact.model_version != protocol.model_version
                or artifact.cutoff != protocol.prospective_shadow_start
                or not artifact.model_ready
            ):
                raise ValueError("Ranking V3 frozen scoring artifact is not release-ready")
        except (TypeError, ValueError):
            continue
        existing = RankingV3ForwardRepository(repo.session_factory).load_snapshot(
            RankingV3ForwardIdentity.from_protocol(protocol)
        )
        if (
            existing is not None
            and existing.ledger.data_revision != ranking_v3_data_revision(record)
        ):
            continue
        return record, ranking, protocol
    if run_id is not None:
        raise ValueError(explicit_error)
    return None


def _ranking_v3_bound_validation_run_id(repo: QagentRepository) -> str | None:
    """Return the server-bound run instead of silently falling back to old results."""

    try:
        protocol = build_ranking_v3_protocol()
        snapshot = RankingV3ForwardRepository(repo.session_factory).load_snapshot(
            RankingV3ForwardIdentity.from_protocol(protocol)
        )
    except (TypeError, ValueError):
        return None
    if snapshot is None:
        return None
    for evidence in reversed(snapshot.evidence):
        if evidence.evidence_kind not in {"historical_gates", "pbo"}:
            continue
        validation_run_id = evidence.payload.get("validation_run_id")
        if isinstance(validation_run_id, str) and validation_run_id.strip():
            return validation_run_id.strip()
    return None


def _ranking_v3_forward_state_payload(
    repo: QagentRepository,
    context: tuple[object, Mapping[str, object], RankingV3Protocol] | None,
) -> dict[str, object]:
    if context is None:
        return {
            "state": "idle",
            "status": "idle",
            "reason": "no eligible successful Ranking V3 validation run",
            "message": "no eligible successful Ranking V3 validation run",
            "validation_run_id": None,
            "protocol": None,
            "protocol_id": None,
            "model_version": None,
            "required_sessions": 0,
            "required_completed_trades": 0,
            "maximum_sessions": None,
            "phase": "idle",
            "collection_target_sessions": 0,
            "latest_session_date": None,
            "blocked_date": None,
            "blocked_code": None,
            "last_attempt_at": None,
            "error": None,
            "metrics": None,
            "evaluation": None,
            "release_proof_available": False,
            "release_proof_digest": None,
        }
    run, _, protocol = context
    store = RankingV3ForwardRepository(repo.session_factory)
    identity = RankingV3ForwardIdentity.from_protocol(protocol)
    snapshot = store.load_snapshot(identity)
    base: dict[str, object] = {
        "validation_run_id": getattr(run, "run_id"),
        "protocol": protocol.model_dump(mode="json"),
        "protocol_id": protocol.protocol_id,
        "model_version": protocol.model_version,
        "required_sessions": protocol.thresholds.minimum_forward_shadow_sessions,
        "collection_target_sessions": (
            protocol.thresholds.minimum_forward_shadow_sessions
        ),
        "required_completed_trades": protocol.thresholds.minimum_forward_shadow_trades,
        "maximum_sessions": protocol.thresholds.maximum_forward_shadow_sessions,
        "release_proof_available": False,
        "blocked_date": None,
        "blocked_code": None,
        "last_attempt_at": None,
        "error": None,
    }
    if snapshot is None:
        today = _a_share_today()
        payload = {
            **base,
            "state": (
                "waiting_start"
                if today < protocol.prospective_shadow_start
                else "ready"
            ),
            "status": "pending",
            "phase": "waiting_collection",
            "reason": (
                f"forward shadow starts on {protocol.prospective_shadow_start.isoformat()}"
                if today < protocol.prospective_shadow_start
                else "eligible protocol is ready for its first forward session"
            ),
            "message": (
                f"forward shadow starts on {protocol.prospective_shadow_start.isoformat()}"
                if today < protocol.prospective_shadow_start
                else "eligible protocol is ready for its first forward session"
            ),
            "evaluation": None,
            "metrics": None,
            "pending_candidate_count": 0,
            "latest_session_date": None,
            "release_proof_digest": None,
        }
        blocked = _ranking_v3_forward_runtime_status_for(protocol)
        if blocked is not None:
            payload.update(blocked)
        return payload
    validator = RankingV3ForwardValidator(
        store,
        protocol,
        evidence_authority=RankingV3RepositoryEvidenceAuthority(repo),
    )
    evaluation = validator.inspect()
    pending_count = sum(
        candidate.outcome_status == "pending" for candidate in snapshot.candidates
    )
    if evaluation.status in {"approved", "rejected"}:
        phase = evaluation.status
    elif (
        evaluation.metrics.session_count
        < protocol.thresholds.minimum_forward_shadow_sessions
    ):
        phase = "candidate_collection"
    else:
        phase = "liquidation"
    payload = {
        **base,
        "state": (
            "approved_proof_available"
            if evaluation.status == "approved"
            else "shadow_rejected"
            if evaluation.status == "rejected"
            else "shadow_unpublished"
        ),
        "status": evaluation.status,
        "phase": phase,
        "reason": evaluation.reasons[0] if evaluation.reasons else "",
        "message": evaluation.reasons[0] if evaluation.reasons else "",
        "evaluation": evaluation.model_dump(mode="json"),
        "metrics": evaluation.metrics.model_dump(mode="json"),
        "pending_candidate_count": pending_count,
        "candidate_count": len(snapshot.candidates),
        "latest_session_date": (
            snapshot.ledger.latest_session_date.isoformat()
            if snapshot.ledger.latest_session_date is not None
            else None
        ),
        "release_proof_available": evaluation.release_proof is not None,
        "release_proof_digest": (
            evaluation.release_proof.proof_digest
            if evaluation.release_proof is not None
            else None
        ),
        "production": _ranking_v3_production_state_payload(
            repo,
            validation_run_id=str(getattr(run, "run_id")),
            evaluation=evaluation,
        ),
    }
    blocked = _ranking_v3_forward_runtime_status_for(protocol)
    if blocked is not None and evaluation.status == "pending":
        payload.update(blocked)
    return payload


def _ranking_v3_production_state_payload(
    repo: QagentRepository,
    *,
    validation_run_id: str,
    evaluation,
) -> dict[str, object]:
    """Expose immutable production-batch readiness without creating new facts."""

    proof = evaluation.release_proof
    if evaluation.status != "approved" or proof is None:
        return {
            "state": "gated",
            "message": "forward release proof is not approved",
            "paper_admission_enforced": True,
            "target_session_date": None,
            "latest_session_date": None,
            "selected_count": 0,
            "batch_fact_digest": None,
            "identity_digest": None,
        }
    try:
        identity = RankingV3ProductionIdentity.from_release_proof(
            proof,
            validation_run_id=validation_run_id,
        )
        batches = RankingV3ProductionRepository(repo.session_factory).list_batches(
            identity,
            limit=1,
        )
    except (RankingV3ProductionIntegrityError, TypeError, ValueError) as exc:
        return {
            "state": "blocked",
            "message": f"production identity is invalid: {exc}",
            "paper_admission_enforced": True,
            "target_session_date": None,
            "latest_session_date": None,
            "selected_count": 0,
            "batch_fact_digest": None,
            "identity_digest": None,
        }

    today = _a_share_today()
    sessions = trading_sessions_in_range(today - timedelta(days=14), today)
    target_session = sessions[-1] if sessions else None
    if not batches:
        return {
            "state": "awaiting_full_market_scan",
            "message": "approved model is waiting for a complete full-market production scan",
            "paper_admission_enforced": True,
            "target_session_date": (
                target_session.isoformat() if target_session is not None else None
            ),
            "latest_session_date": None,
            "selected_count": 0,
            "batch_fact_digest": None,
            "identity_digest": identity.identity_digest,
        }

    latest = batches[0]
    is_current = target_session is not None and latest.session_date == target_session
    return {
        "state": "recorded" if is_current else "awaiting_current_session_scan",
        "message": (
            "current production batch is frozen and eligible for paper admission"
            if is_current
            else "approved model is waiting for the latest complete full-market scan"
        ),
        "paper_admission_enforced": True,
        "target_session_date": (
            target_session.isoformat() if target_session is not None else None
        ),
        "latest_session_date": latest.session_date.isoformat(),
        "selected_count": latest.selected_count,
        "batch_fact_digest": latest.fact_digest,
        "identity_digest": identity.identity_digest,
    }


def _ranking_v3_forward_runtime_key(protocol: RankingV3Protocol) -> str:
    return f"{protocol.protocol_id}:{protocol.model_version}"


def _ranking_v3_forward_runtime_status_for(
    protocol: RankingV3Protocol,
) -> dict[str, object] | None:
    with _ranking_v3_forward_runtime_lock:
        status = _ranking_v3_forward_runtime_status.get(
            _ranking_v3_forward_runtime_key(protocol)
        )
        return dict(status) if status is not None else None


def _set_ranking_v3_forward_waiting_snapshot(
    protocol: RankingV3Protocol,
    blocked_date: date,
) -> None:
    message = (
        f"daily opportunity snapshot for {blocked_date.isoformat()} is not available; "
        "forward validation is waiting and no session was fabricated"
    )
    with _ranking_v3_forward_runtime_lock:
        _ranking_v3_forward_runtime_status[
            _ranking_v3_forward_runtime_key(protocol)
        ] = {
            "state": "waiting_snapshot",
            "status": "pending",
            "phase": "waiting_snapshot",
            "reason": message,
            "message": message,
            "error": message,
            "blocked_date": blocked_date.isoformat(),
            "blocked_code": "daily_opportunity_snapshot_missing",
            "last_attempt_at": datetime.now(timezone.utc).isoformat(),
        }


def _clear_ranking_v3_forward_runtime_status(protocol: RankingV3Protocol) -> None:
    with _ranking_v3_forward_runtime_lock:
        _ranking_v3_forward_runtime_status.pop(
            _ranking_v3_forward_runtime_key(protocol),
            None,
        )


def _run_ranking_v3_forward_catch_up(
    repo: QagentRepository,
    provider,
    context: tuple[object, Mapping[str, object], RankingV3Protocol],
    *,
    through_date: date,
) -> list[object]:
    run, _, protocol = context
    today = _a_share_today()
    effective_through = min(through_date, today)
    available_sessions = trading_sessions_in_range(
        effective_through - timedelta(days=14),
        effective_through,
    )
    if effective_through == today and available_sessions[-1:] == [today]:
        available_sessions = available_sessions[:-1]
    if not available_sessions:
        return []
    latest_available = available_sessions[-1]
    if latest_available < protocol.prospective_shadow_start:
        return []
    store = RankingV3ForwardRepository(repo.session_factory)
    snapshot = store.load_snapshot(RankingV3ForwardIdentity.from_protocol(protocol))
    if snapshot is not None and snapshot.ledger.status != "pending":
        return []
    start_date = (
        snapshot.ledger.latest_session_date
        if snapshot is not None and snapshot.ledger.latest_session_date is not None
        else protocol.prospective_shadow_start
    )
    if start_date > latest_available:
        return []
    results = []
    for current_session in trading_sessions_in_range(start_date, latest_available):
        current_snapshot = store.load_snapshot(
            RankingV3ForwardIdentity.from_protocol(protocol)
        )
        sessions = (
            sorted(
                current_snapshot.sessions,
                key=lambda item: item.session_date,
            )
            if current_snapshot is not None
            else []
        )
        minimum_sessions = protocol.thresholds.minimum_forward_shadow_sessions
        collection_open = len(sessions) < minimum_sessions
        if not collection_open:
            collection_end = sessions[minimum_sessions - 1].session_date
            collection_open = current_session <= collection_end
        recorded_session_dates = {item.session_date for item in sessions}
        requires_candidate_snapshot = (
            collection_open and current_session not in recorded_session_dates
        )
        if requires_candidate_snapshot and not repo.list_top_daily_opportunity_snapshots(
            start=current_session,
            end=current_session,
            top_n=1,
            provider="free",
        ):
            _set_ranking_v3_forward_waiting_snapshot(protocol, current_session)
            break
        result = run_ranking_v3_forward_day(
            repo,
            provider,
            getattr(run, "run_id"),
            current_session,
        )
        results.append(result)
        _clear_ranking_v3_forward_runtime_status(protocol)
        if result.ledger_status in {"approved", "rejected"}:
            break
    return results


def restore_walk_forward_job_from_storage() -> str | None:
    active = [
        job
        for job in reversed(_repo().list_walk_forward_jobs(limit=100))
        if job.status in {"queued", "running"}
    ]
    if not active:
        return None
    for job in active:
        _submit_walk_forward_job(job.job_id)
    return active[0].job_id


def _validate_walk_forward_params(
    provider: str,
    start: date,
    end: date,
    step_sessions: int,
    lookback_days: int,
) -> str:
    mode = provider.strip().lower()
    if mode != "free":
        raise HTTPException(
            status_code=400,
            detail="walk-forward only supports free A-share data",
        )
    if start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    if lookback_days <= 0:
        raise HTTPException(
            status_code=400,
            detail="lookback_days must be positive",
        )
    if step_sessions != RANKING_V3_REBALANCE_STEP_SESSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "strict Ranking V3 validation requires "
                f"step_sessions={RANKING_V3_REBALANCE_STEP_SESSIONS}"
            ),
        )
    return mode


def _submit_walk_forward_job(job_id: str) -> None:
    with _walk_forward_jobs_lock:
        if job_id in _submitted_walk_forward_jobs:
            return
        _submitted_walk_forward_jobs.add(job_id)
    future = _walk_forward_executor().submit(_run_walk_forward_job_safely, job_id)
    if future is not None and hasattr(future, "add_done_callback"):
        future.add_done_callback(lambda _future: _release_walk_forward_submission(job_id))


def _release_walk_forward_submission(job_id: str) -> None:
    with _walk_forward_jobs_lock:
        _submitted_walk_forward_jobs.discard(job_id)


def _run_walk_forward_job_safely(job_id: str) -> None:
    repo = _repo()
    replay_repository: ReplayEvidenceRepository | None = None
    try:
        job = repo.get_walk_forward_job(job_id)
        if job is None:
            return
        replay_repository = ReplayEvidenceRepository(
            repo.session_factory,
            job.provider,
            run_status_lookup=_walk_forward_status_lookup(repo),
        )
        if replay_repository.current_revision() != job.dataset_revision:
            raise RuntimeError("historical dataset revision changed; start a new validation job")
        manifest = WalkForwardExperimentManifest.model_validate(job.experiment_manifest)
        current_manifest = build_walk_forward_experiment_manifest(
            provider_mode=job.provider,
            dataset_revision=job.dataset_revision,
            start_date=job.start_date,
            end_date=job.end_date,
            rebalance_step_sessions=job.rebalance_step_sessions,
            lookback_days=job.lookback_days,
        )
        if not walk_forward_manifests_semantically_compatible(
            manifest,
            current_manifest,
        ):
            raise RuntimeError(
                "walk-forward experiment definition changed; start a new validation job"
            )
        manifest = record_walk_forward_runtime_revision(
            manifest,
            current_manifest,
        )
        checkpoint_by_date = {item["decision_date"]: item for item in job.checkpoints}
        repo.update_walk_forward_job(
            job_id,
            status="running",
            phase="historical_replay",
            started_at=job.started_at or datetime.now(timezone.utc),
            experiment_manifest=manifest.model_dump(mode="json"),
        )

        def on_progress(progress: WalkForwardProgress) -> None:
            current = repo.get_walk_forward_job(job_id)
            if current is None or current.status == "cancelled":
                raise _WalkForwardJobCancelled(
                    f"walk-forward job {job_id} was cancelled before checkpoint publication"
                )
            if progress.snapshot is not None:
                snapshot_payload = progress.snapshot.model_dump(mode="json")
                checkpoint_by_date[snapshot_payload["decision_date"]] = snapshot_payload
            repo.update_walk_forward_job(
                job_id,
                status="running",
                phase=progress.phase,
                processed_snapshots=progress.processed_snapshots,
                current_date=progress.current_date,
                lease_maintenance_count=progress.lease_maintenance_count,
                lease_recovery_count=progress.lease_recovery_count,
                last_lease_heartbeat_at=progress.last_lease_heartbeat_at,
                checkpoints=list(checkpoint_by_date.values()),
            )

        def on_lease_maintenance(
            maintenance_count: int,
            recovery_count: int,
            heartbeat_at: datetime,
        ) -> None:
            repo.update_walk_forward_job(
                job_id,
                lease_maintenance_count=maintenance_count,
                lease_recovery_count=recovery_count,
                last_lease_heartbeat_at=heartbeat_at,
            )

        result = run_full_market_walk_forward_selection(
            replay_repository,
            owner_run_id=job_id,
            start=job.start_date,
            end=job.end_date,
            rebalance_step_sessions=job.rebalance_step_sessions,
            lookback_days=job.lookback_days,
            experiment_manifest=manifest,
            resume_snapshots=[WalkForwardSnapshot.model_validate(item) for item in job.checkpoints],
            progress_callback=on_progress,
            lease_maintenance_callback=on_lease_maintenance,
            initial_lease_maintenance_count=job.lease_maintenance_count,
            initial_lease_recovery_count=job.lease_recovery_count,
            initial_lease_heartbeat_at=job.last_lease_heartbeat_at,
            snapshot_workers=get_settings().walk_forward_snapshot_workers,
            cancellation_check=lambda: (
                (current_job := repo.get_walk_forward_job(job_id)) is None
                or current_job.status == "cancelled"
            ),
        )
        current = repo.get_walk_forward_job(job_id)
        if current is None or current.status == "cancelled":
            raise _WalkForwardJobCancelled(
                f"walk-forward job {job_id} was cancelled before result publication"
            )
        stored = repo.save_walk_forward_run(result)
        _reconcile_full_market_caches_after_walk_forward(
            repo,
            provider=job.provider,
            run_id=stored.run_id,
        )
        repo.update_walk_forward_job(
            job_id,
            status="succeeded",
            phase="completed",
            processed_snapshots=job.total_snapshots,
            result_run_id=stored.run_id,
            finished_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        try:
            current = repo.get_walk_forward_job(job_id)
        except ValueError as integrity_exc:
            repo.fail_walk_forward_job_integrity(
                job_id,
                error=f"checkpoint integrity validation failed: {integrity_exc}",
            )
            current = None
        if current is not None and current.status != "cancelled":
            repo.update_walk_forward_job(
                job_id,
                status="failed",
                phase="failed",
                error=str(exc),
                finished_at=datetime.now(timezone.utc),
            )
        if replay_repository is not None:
            try:
                replay_repository.for_owner(job_id).release_dataset_lease()
            except RuntimeError:
                pass
    finally:
        _release_walk_forward_submission(job_id)


def _walk_forward_job_payload(job) -> dict[str, object]:
    payload = job.model_dump(mode="json", exclude={"checkpoints"})
    payload["progress"] = job.progress
    payload["checkpoint_count"] = len(job.checkpoints)
    return payload


def _reconcile_full_market_caches_after_walk_forward(
    repo: QagentRepository,
    *,
    provider: str,
    run_id: str,
) -> int:
    validation = load_latest_walk_forward_validation(repo, provider)
    governance_context = load_strategy_governance_context(repo)
    reconciled = 0
    for include_etfs in (True, False):
        cached = repo.get_recent_scan_result_cache(
            cache_key=full_market_batch_cache_key(provider, include_etfs),
            max_age=timedelta(days=90),
        )
        if cached is None:
            continue
        payload = deepcopy(cached.payload)
        raw_cards = payload.get("cards")
        if not isinstance(raw_cards, list):
            continue
        cards = _cards_from_payload(raw_cards)
        if not cards:
            continue
        final_policy = apply_final_recommendation_policy(
            cards,
            walk_forward_validation=validation,
            governance_context=governance_context,
        )
        ranked_cards = sort_recommendation_cards(final_policy.cards)
        audits_by_card = {audit.card_id: audit for audit in final_policy.audits}
        ranked_audits = [
            audits_by_card[card.card_id] for card in ranked_cards if card.card_id in audits_by_card
        ]
        payload["cards"] = governed_card_payloads(ranked_cards, ranked_audits)
        payload["strategy_governance"] = [audit.model_dump(mode="json") for audit in ranked_audits]
        data_health = payload.setdefault("data_health", {})
        if not isinstance(data_health, dict):
            data_health = {}
            payload["data_health"] = data_health
        data_health.update(final_policy.data_health)
        data_health.update(
            {
                "walk_forward_cache_reconciled": "true",
                "walk_forward_cache_reconciled_run_id": run_id,
                "walk_forward_cache_market_data_created_at": (cached.created_at.isoformat()),
            }
        )
        if repo.update_scan_result_cache_payload(cached.cache_id, payload) is not None:
            reconciled += 1
    return reconciled


@router.post("/historical-data/backfill")
def start_historical_data_backfill(
    start: date,
    end: date,
    provider: str = "free",
    symbols: str | None = None,
    max_symbols: int = 200,
    include_etfs: bool = True,
    scope: str = "symbols",
    batch_size: int = 50,
    force_restart: bool = False,
    auto_validate: bool = True,
) -> dict[str, object]:
    mode = _validate_historical_data_params(provider, start, end, max_symbols)
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"symbols", "full-a-share"}:
        raise HTTPException(status_code=400, detail="scope must be symbols or full-a-share")
    if batch_size <= 0 or batch_size > 500:
        raise HTTPException(status_code=400, detail="batch_size must be between 1 and 500")
    if normalized_scope == "full-a-share" and symbols:
        raise HTTPException(
            status_code=400,
            detail="symbols cannot be combined with full-a-share scope",
        )
    repo = _repo()
    latest = repo.get_latest_historical_backfill_job(provider=mode)
    if latest and latest.status in {"queued", "running"}:
        active_scope = latest.data_health.get("backfill_scope", "symbols")
        if active_scope == normalized_scope and not force_restart:
            return _historical_backfill_job_payload(latest)
        raise HTTPException(
            status_code=409,
            detail=f"a {active_scope} historical backfill is already running",
        )
    instrument_ids = (
        []
        if normalized_scope == "full-a-share"
        else _historical_data_symbols(
            mode,
            symbols,
            max_symbols=max_symbols,
            include_etfs=include_etfs,
        )
    )
    job = repo.create_historical_backfill_job(
        mode,
        instrument_ids,
        start,
        end,
        data_health={
            "backfill_scope": normalized_scope,
            "backfill_batch_size": str(batch_size),
            "backfill_phase": "queued",
            "backfill_auto_validate": str(
                auto_validate and normalized_scope == "full-a-share"
            ).lower(),
        },
    )
    _submit_historical_backfill(job.job_id)
    return _historical_backfill_job_payload(job)


@router.get("/historical-data/backfill/latest")
def latest_historical_data_backfill(provider: str = "free") -> dict[str, object]:
    job = _repo().get_latest_historical_backfill_job(provider=provider.strip().lower())
    if job is None:
        raise HTTPException(status_code=404, detail="historical backfill job not found")
    return _historical_backfill_job_payload(job)


@router.get("/historical-data/backfill/{job_id}")
def historical_data_backfill_job(job_id: str) -> dict[str, object]:
    job = _repo().get_historical_backfill_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="historical backfill job not found")
    return _historical_backfill_job_payload(job)


@router.post("/historical-data/backfill/{job_id}/retry")
def retry_historical_data_backfill(job_id: str) -> dict[str, object]:
    repo = _repo()
    job = repo.get_historical_backfill_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="historical backfill job not found")
    if job.status in {"queued", "running"}:
        _submit_historical_backfill(job.job_id)
        return _historical_backfill_job_payload(job)
    validation_state = job.data_health.get("validation_pipeline_state")
    retryable_completed = job.status == "succeeded_with_errors" and validation_state in {
        "blocked_data_coverage",
        "failed",
    }
    if job.status not in {"failed", "cancelled"} and not retryable_completed:
        raise HTTPException(
            status_code=409,
            detail=f"historical backfill job cannot resume from {job.status}",
        )
    retry_count = int(job.data_health.get("backfill_resume_count", "0") or 0) + 1
    resumed = repo.update_historical_backfill_job(
        job.job_id,
        status="queued",
        failed_symbols=max(job.processed_symbols - job.succeeded_symbols, 0),
        data_health={
            **job.data_health,
            "backfill_phase": "queued",
            "backfill_resume_requested": "true",
            "backfill_resume_count": str(retry_count),
        },
    )
    if resumed is None:
        raise HTTPException(status_code=404, detail="historical backfill job not found")
    _submit_historical_backfill(resumed.job_id)
    return _historical_backfill_job_payload(resumed)


def restore_historical_backfill_from_storage() -> str | None:
    job = _repo().get_latest_historical_backfill_job()
    if job is None or job.status not in {"queued", "running"}:
        return None
    _submit_historical_backfill(job.job_id)
    return job.job_id


def _submit_historical_backfill(job_id: str) -> None:
    with _historical_jobs_lock:
        if job_id in _submitted_historical_jobs:
            return
        _submitted_historical_jobs.add(job_id)
    _history_task_executor.submit(_run_historical_backfill_safely, job_id)


def _run_historical_backfill_safely(job_id: str) -> None:
    try:
        result = run_historical_backfill_job(job_id)
        _continue_validation_pipeline(result)
    except Exception as exc:
        repo = _repo()
        job = repo.get_historical_backfill_job(job_id)
        if job is not None and job.status in {"queued", "running"}:
            repo.update_historical_backfill_job(
                job_id,
                status="failed",
                errors=[*job.errors[-99:], str(exc)],
            )
        elif job is not None:
            repo.update_historical_backfill_job(
                job_id,
                errors=[*job.errors[-99:], f"validation pipeline: {exc}"],
                data_health={
                    **job.data_health,
                    "validation_pipeline_state": "failed",
                    "validation_pipeline_error": str(exc),
                },
            )
    finally:
        with _historical_jobs_lock:
            _submitted_historical_jobs.discard(job_id)


def _continue_validation_pipeline(result) -> str:
    repo = _repo()
    job = repo.get_historical_backfill_job(result.job.job_id) or result.job
    default_enabled = job.data_health.get("backfill_scope") == "full-a-share"
    enabled = (
        job.data_health.get(
            "backfill_auto_validate",
            str(default_enabled).lower(),
        ).lower()
        == "true"
    )
    readiness = _historical_validation_readiness(
        result.manifest,
        start=job.start_date,
        backfill_status=job.status,
        backfill_errors=job.errors,
        backfill_data_health=job.data_health,
    )
    health = {
        **job.data_health,
        **readiness,
        "validation_pipeline_auto": str(enabled).lower(),
    }
    if not enabled:
        health["validation_pipeline_state"] = "manual"
        repo.update_historical_backfill_job(job.job_id, data_health=health)
        return "manual"
    if readiness["validation_pipeline_gate"] != "ready":
        health["validation_pipeline_state"] = "blocked_data_coverage"
        repo.update_historical_backfill_job(job.job_id, data_health=health)
        return "blocked_data_coverage"

    revision = ReplayEvidenceRepository(repo.session_factory, job.provider).current_revision()
    current_manifest = build_walk_forward_experiment_manifest(
        provider_mode=job.provider,
        dataset_revision=revision,
        start_date=job.start_date,
        end_date=job.end_date,
        rebalance_step_sessions=10,
        lookback_days=400,
    )
    latest_runs = repo.list_walk_forward_runs(provider=job.provider, limit=1)
    if latest_runs and _walk_forward_run_matches_manifest(
        latest_runs[0],
        current_manifest,
    ):
        health["validation_pipeline_state"] = "already_validated"
        health["validation_pipeline_walk_forward_run_id"] = latest_runs[0].run_id
        health["validation_pipeline_experiment_digest"] = current_manifest.experiment_digest
        repo.update_historical_backfill_job(job.job_id, data_health=health)
        return "already_validated"

    walk_job = _create_or_get_walk_forward_job(
        repo=repo,
        provider=job.provider,
        start=job.start_date,
        end=job.end_date,
        step_sessions=10,
        lookback_days=400,
    )
    health["validation_pipeline_state"] = "walk_forward_queued"
    health["validation_pipeline_walk_forward_job_id"] = walk_job.job_id
    health["validation_pipeline_dataset_revision"] = str(walk_job.dataset_revision)
    health["validation_pipeline_experiment_digest"] = walk_job.experiment_manifest.get(
        "experiment_digest", ""
    )
    repo.update_historical_backfill_job(job.job_id, data_health=health)
    return "walk_forward_queued"


def _historical_validation_readiness(
    manifest,
    *,
    start: date,
    backfill_status: str | None = None,
    backfill_errors: list[str] | tuple[str, ...] = (),
    backfill_data_health: Mapping[str, str] | None = None,
) -> dict[str, str]:
    instruments = list(manifest.instruments)
    stocks = [item for item in instruments if item.asset_type == "stock"]
    etfs = [item for item in instruments if item.asset_type == "etf"]
    adjusted = [item for item in instruments if item.asset_type in {"stock", "etf"}]
    total = len(instruments)
    stock_total = len(stocks)
    adjusted_total = len(adjusted)
    data_health = dict(backfill_data_health or {})
    data_health.update(getattr(manifest, "data_health", {}) or {})

    ratios = {
        "bars": _scope_ratio(
            sum(item.bar_coverage_ratio >= 0.95 for item in instruments),
            total,
        ),
        "adjusted": _scope_ratio(
            sum(
                (item.adjustment_coverage_ratio or 0) >= 0.95
                for item in adjusted
            ),
            adjusted_total,
            empty_scope_ready=total > 0,
        ),
        "tradability": _scope_ratio(
            sum(
                item.tradability_coverage_ratio >= 0.95
                for item in instruments
            ),
            total,
        ),
        "universe": _scope_ratio(
            sum(
                _historical_item_covers_start(item, "universe", start)
                for item in instruments
            ),
            total,
        ),
        "profile": _scope_ratio(
            sum(item.profile_rows > 0 for item in instruments),
            total,
        ),
        "fundamental": _scope_ratio(
            sum(
                _historical_item_covers_start(item, "fundamental", start)
                for item in stocks
            ),
            stock_total,
            empty_scope_ready=True,
        ),
        "industry": _scope_ratio(
            sum(getattr(item, "industry_rows", 0) > 0 for item in stocks),
            stock_total,
            empty_scope_ready=True,
        ),
    }
    benchmark_ready, benchmark_total = _fraction_value(
        data_health.get("historical_benchmark_price_ready", "0/0")
    )
    benchmark_ratio = (
        benchmark_ready / benchmark_total if benchmark_total > 0 else 0.0
    )
    index_ready = _integer_value(
        data_health.get("historical_benchmark_ready")
    )
    index_expected = _integer_value(
        data_health.get("historical_index_expected_snapshots")
    )
    index_ratio = index_ready / index_expected if index_expected > 0 else 0.0

    ready_counts = {
        "bars": sum(item.bar_coverage_ratio >= 0.95 for item in instruments),
        "adjusted": sum(
            (item.adjustment_coverage_ratio or 0) >= 0.95 for item in adjusted
        ),
        "tradability": sum(
            item.tradability_coverage_ratio >= 0.95 for item in instruments
        ),
        "universe": sum(
            _historical_item_covers_start(item, "universe", start)
            for item in instruments
        ),
        "profile": sum(item.profile_rows > 0 for item in instruments),
        "fundamental": sum(
            _historical_item_covers_start(item, "fundamental", start)
            for item in stocks
        ),
        "industry": sum(
            getattr(item, "industry_rows", 0) > 0 for item in stocks
        ),
    }
    denominators = {
        "bars": total,
        "adjusted": adjusted_total,
        "tradability": total,
        "universe": total,
        "profile": total,
        "fundamental": stock_total,
        "industry": stock_total,
    }
    requirements = {
        "bars": MIN_FULL_MARKET_COVERAGE_RATIO,
        "adjusted": MIN_FULL_MARKET_COVERAGE_RATIO,
        "tradability": MIN_FULL_MARKET_COVERAGE_RATIO,
        "universe": MIN_FULL_MARKET_COVERAGE_RATIO,
        "profile": MIN_FULL_MARKET_COVERAGE_RATIO,
        "fundamental": MIN_FUNDAMENTAL_COVERAGE_RATIO,
        "industry": MIN_FULL_MARKET_COVERAGE_RATIO,
    }
    blockers = [
        f"{key}<{minimum:.0%}"
        for key, minimum in requirements.items()
        if ratios[key] < minimum
    ]
    if index_ratio < 1:
        blockers.append("index<100%")
    if benchmark_ratio < 1:
        blockers.append("benchmarks<100%")

    reference_status = data_health.get(
        "historical_reference_request_status",
        "unreported",
    )
    if reference_status in {
        "unavailable",
        "failed",
        "empty",
        "succeeded_with_errors",
    }:
        blockers.append(f"reference_request={reference_status}")
    critical_reference_errors = _critical_historical_reference_errors(
        backfill_status=backfill_status,
        backfill_errors=backfill_errors,
        data_health=data_health,
    )
    if critical_reference_errors:
        blockers.append("critical_reference_errors")

    readiness = {
        "validation_pipeline_gate": "ready" if not blockers else "insufficient",
        "validation_pipeline_blockers": ",".join(dict.fromkeys(blockers)),
        "validation_pipeline_market_coverage": f"{ratios['bars']:.4f}",
        "validation_pipeline_bars_coverage": f"{ratios['bars']:.4f}",
        "validation_pipeline_adjusted_coverage": f"{ratios['adjusted']:.4f}",
        "validation_pipeline_tradability_coverage": f"{ratios['tradability']:.4f}",
        "validation_pipeline_universe_coverage": f"{ratios['universe']:.4f}",
        "validation_pipeline_profile_coverage": f"{ratios['profile']:.4f}",
        "validation_pipeline_fundamental_coverage": f"{ratios['fundamental']:.4f}",
        "validation_pipeline_industry_coverage": f"{ratios['industry']:.4f}",
        "validation_pipeline_index_coverage": f"{index_ratio:.4f}",
        "validation_pipeline_benchmark_coverage": f"{benchmark_ratio:.4f}",
        "validation_pipeline_reference_request_status": reference_status,
        "validation_pipeline_critical_reference_error_count": str(
            len(critical_reference_errors)
        ),
        "validation_pipeline_critical_reference_errors": " | ".join(
            critical_reference_errors[:10]
        ),
        "validation_pipeline_etf_constituent_gate": "not_required",
        "validation_pipeline_etf_constituent_requirement": (
            "optional_point_in_time_holdings_with_pairwise_correlation_fallback"
        ),
        "validation_pipeline_etf_constituent_scope": str(len(etfs)),
        "validation_pipeline_etf_constituent_coverage": "not_applicable",
        "validation_pipeline_index_ready": f"{index_ready}/{index_expected}",
        "validation_pipeline_benchmark_ready": (
            f"{benchmark_ready}/{benchmark_total}"
        ),
    }
    for key, minimum in requirements.items():
        denominator = denominators[key]
        readiness[f"validation_pipeline_{key}_gate"] = (
            "not_applicable"
            if denominator == 0 and key in {"fundamental", "industry"}
            else "ready"
            if ratios[key] >= minimum
            else "insufficient"
        )
        readiness[f"validation_pipeline_{key}_ready"] = (
            f"{ready_counts[key]}/{denominator}"
        )
        readiness[f"validation_pipeline_{key}_threshold"] = f"{minimum:.4f}"
    readiness["validation_pipeline_index_gate"] = (
        "ready" if index_ratio >= 1 else "insufficient"
    )
    readiness["validation_pipeline_index_threshold"] = "1.0000"
    readiness["validation_pipeline_benchmark_gate"] = (
        "ready" if benchmark_ratio >= 1 else "insufficient"
    )
    readiness["validation_pipeline_benchmark_threshold"] = "1.0000"
    return readiness


def _historical_item_covers_start(item, evidence: str, start: date) -> bool:
    rows_attribute = (
        "universe_snapshot_rows"
        if evidence == "universe"
        else f"{evidence}_rows"
    )
    rows = getattr(item, rows_attribute, 0)
    first_date = getattr(item, f"first_{evidence}_date", None)
    listing_date = getattr(item, "listing_date", None)
    return bool(
        rows > 0
        and first_date is not None
        and (
            (listing_date is not None and listing_date > start)
            or first_date <= start
        )
    )


def _scope_ratio(
    ready: int,
    total: int,
    *,
    empty_scope_ready: bool = False,
) -> float:
    if total <= 0:
        return 1.0 if empty_scope_ready else 0.0
    return ready / total


def _critical_historical_reference_errors(
    *,
    backfill_status: str | None,
    backfill_errors: list[str] | tuple[str, ...],
    data_health: Mapping[str, str],
) -> list[str]:
    critical: list[str] = []
    reference_status = data_health.get("historical_reference_request_status")
    if (
        backfill_status == "succeeded_with_errors"
        or reference_status in {"failed", "succeeded_with_errors"}
    ):
        markers = ("reference", "industry", "index", "benchmark")
        critical.extend(
            str(error)
            for error in backfill_errors
            if any(marker in str(error).lower() for marker in markers)
        )
        reference_errors = data_health.get("historical_reference_errors", "")
        critical.extend(
            error.strip()
            for error in reference_errors.split(" | ")
            if error.strip()
        )
        if (
            _integer_value(
                data_health.get("historical_reference_error_count")
            )
            > 0
            and not critical
        ):
            critical.append("historical reference request reported errors")
    return list(dict.fromkeys(critical))


def _integer_value(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fraction_value(value: str) -> tuple[int, int]:
    try:
        numerator, denominator = value.split("/", 1)
        return int(numerator), int(denominator)
    except (AttributeError, TypeError, ValueError):
        return 0, 0


def _parse_symbols(symbols: str | None, default_universe: list[str]) -> list[str]:
    if not symbols:
        return default_universe
    return [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]


def _resolve_symbols(provider_mode: str, symbols: str | None) -> ResolvedSymbols:
    mode = provider_mode.strip().lower()
    default_universe = DEFAULT_FREE_UNIVERSE if mode == "free" else DEFAULT_DEV_UNIVERSE
    parsed = _parse_symbols(symbols, default_universe)
    if mode == "free":
        return resolve_symbol_tokens(parsed)
    return ResolvedSymbols(symbols=parsed)


def _resolve_symbols_with_limit(
    provider_mode: str,
    symbols: str | None,
    scan_limit: int | None = None,
    include_supplements: bool = True,
) -> ResolvedSymbols:
    mode = provider_mode.strip().lower()
    limit = scan_limit
    if mode != "free" or not limit:
        return _resolve_symbols(mode, symbols)
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="scan_limit must be between 1 and 1000")
    parsed = _parse_symbols(symbols, DEFAULT_FREE_UNIVERSE)
    return resolve_symbol_tokens(
        parsed,
        limit=limit,
        include_supplements=include_supplements,
    )


def _scan(provider_mode: str = "fixture", symbols: str | None = None):
    mode = provider_mode.strip().lower()
    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    try:
        provider = build_market_data_provider(mode)
        repo = _repo()
        strategy_data_provider = EmptyStrategyDataProvider() if resolved.is_dynamic else None
        feedback_center = build_recent_recommendation_feedback_center(
            repo=repo,
            provider=mode,
            market_provider=provider,
        )
        result = run_daily_scan(
            instrument_ids,
            provider,
            mode=mode,
            strategy_data_provider=strategy_data_provider,
            recommendation_feedback_center=feedback_center,
            paper_trading_report=_latest_paper_feedback_report(mode),
            walk_forward_validation=load_latest_walk_forward_validation(repo, mode),
            strategy_governance_context=load_strategy_governance_context(repo),
        )
        invalidated = _paper_recent_invalidated_instruments(mode)
        if invalidated:
            original_count = len(result.cards)
            result.cards = [card for card in result.cards if card.instrument_id not in invalidated]
            result.data_health["paper_invalidated_cards_filtered"] = str(
                original_count - len(result.cards)
            )
        result.data_health.update(resolved.data_health)
        if resolved.is_dynamic:
            result.data_health["strategy_data_skipped"] = "true"
        return result, mode, instrument_ids
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _backtest_dates(mode: str, start: date | None, end: date | None) -> tuple[date, date]:
    end_date = end or (date(2026, 3, 20) if mode == "fixture" else date.today())
    start_date = start or (
        date(2026, 1, 15) if mode == "fixture" else end_date - timedelta(days=180)
    )
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    return start_date, end_date


def _factor_backtest_dates(mode: str, start: date | None, end: date | None) -> tuple[date, date]:
    if start or end:
        return _backtest_dates(mode, start, end)
    if mode == "fixture":
        return date(1900, 1, 1), date(2100, 1, 1)
    end_date = date.today()
    return end_date - timedelta(days=365), end_date


def _chart_dates(mode: str, days: int) -> tuple[date, date]:
    if mode == "fixture":
        return date(1900, 1, 1), date(2100, 1, 1)
    end_date = date.today()
    return end_date - timedelta(days=max(days * 3, 240)), end_date


def _normalize_chart_instrument(instrument_id: str) -> str:
    value = instrument_id.strip().upper()
    if value.startswith("CN:"):
        symbol = market_symbol(value)
        return f"CN:{symbol}" if len(symbol) == 6 and symbol.isdigit() else value
    if ":" in value:
        return value
    symbol = market_symbol(value)
    return f"CN:{symbol}" if len(symbol) == 6 and symbol.isdigit() else value


def _chart_bar(row) -> dict[str, object]:
    return {
        "trade_date": row["trade_date"].isoformat(),
        "open": _clean_float(row["open"]),
        "high": _clean_float(row["high"]),
        "low": _clean_float(row["low"]),
        "close": _clean_float(row["close"]),
        "volume": int(row["volume"]),
        "ma20": _clean_float(row.get("ma_20")),
        "ma50": _clean_float(row.get("ma_50")),
        "ma100": _clean_float(row.get("ma_100")),
        "ma200": _clean_float(row.get("ma_200")),
    }


def _chart_levels(card) -> dict[str, str | None]:
    if card is None:
        return {
            "trigger_price": None,
            "initial_stop": None,
            "target_1": None,
            "target_2": None,
            "no_chase_above": None,
        }
    return {
        "trigger_price": _decimal_text(card.entry_plan.trigger_price),
        "initial_stop": _decimal_text(card.exit_plan.initial_stop),
        "target_1": _decimal_text(card.exit_plan.target_1),
        "target_2": _decimal_text(card.exit_plan.target_2),
        "no_chase_above": _decimal_text(card.entry_plan.no_chase_above),
    }


def _radar_item(instrument_id: str, bars, card, scan_item) -> dict[str, object]:
    if bars.empty:
        return {
            "instrument_id": instrument_id,
            "instrument_label": format_instrument_label(instrument_id),
            "latest_trade_date": None,
            "latest_close": None,
            "previous_close": None,
            "change_pct": None,
            "volume_ratio": None,
            "signal": "no_setup",
            "severity": "info",
            "score": 0.0,
            "message": "No daily bars are available for the latest radar scan.",
            "action": "Skip until market data is available.",
            "distance_to_trigger_pct": None,
            "trigger_price": None,
            "initial_stop": None,
            "target_1": None,
            "no_chase_above": None,
        }

    enriched = add_volume_ratio(bars.sort_values("trade_date"), window=20)
    latest = enriched.iloc[-1]
    previous = enriched.iloc[-2] if len(enriched) >= 2 else None
    latest_close = Decimal(str(round(float(latest["close"]), 2)))
    previous_close = (
        Decimal(str(round(float(previous["close"]), 2))) if previous is not None else None
    )
    change_pct = (
        percent_distance(float(latest_close), float(previous_close))
        if previous_close not in {None, Decimal("0")}
        else None
    )
    volume_ratio = _clean_float(latest.get("volume_ratio"))

    if card is None:
        reason = (
            scan_item.reason if scan_item is not None else "Signal stack did not meet threshold."
        )
        return _radar_payload(
            instrument_id=instrument_id,
            latest=latest,
            latest_close=latest_close,
            previous_close=previous_close,
            change_pct=change_pct,
            volume_ratio=volume_ratio,
            signal="no_setup",
            severity="info",
            score=0.1,
            message=f"No recommendation yet: {reason}",
            action="Keep on watchlist; review blockers before considering entry.",
            card=None,
            distance_to_trigger_pct=None,
        )

    trigger = card.entry_plan.trigger_price
    stop = card.exit_plan.initial_stop
    target = card.exit_plan.target_1
    no_chase = card.entry_plan.no_chase_above
    distance_to_trigger_pct = (
        percent_distance(float(trigger), float(latest_close)) if trigger is not None else None
    )

    signal = "inside_plan"
    severity = "info"
    score = card.rank_score
    message = "Price remains inside the current research plan."
    action = "Track trigger, stop, target, and no-chase levels."

    if stop is not None and latest_close <= stop * Decimal("1.02"):
        signal = "near_stop"
        severity = "danger"
        score = 0.98
        message = "Latest price is close to or below the stop guard."
        action = "Do not add exposure; verify whether the setup is invalidated."
    elif target is not None and latest_close >= target * Decimal("0.98"):
        signal = "near_target"
        severity = "success"
        score = 0.92
        message = "Latest price is near the first target."
        action = "Follow the exit plan; consider partial profit or tighter trailing stop."
    elif no_chase is not None and latest_close > no_chase:
        signal = "overextended"
        severity = "warning"
        score = 0.9
        message = "Latest price is above the no-chase level."
        action = "Avoid chasing; wait for a new setup or pullback."
    elif trigger is not None and latest_close >= trigger and (volume_ratio or 0) >= 1.1:
        signal = "trigger_breakout"
        severity = "success"
        score = 0.88
        message = "Price has crossed the trigger with acceptable volume confirmation."
        action = "Check no-chase level and risk vetoes before treating it as actionable."
    elif distance_to_trigger_pct is not None and 0 <= distance_to_trigger_pct <= 3:
        signal = "approaching_trigger"
        severity = "watch"
        score = 0.82
        message = "Price is approaching the planned trigger."
        action = "Wait for trigger and volume confirmation; avoid early entry."
    elif volume_ratio is not None and volume_ratio >= 1.8:
        signal = "volume_surge"
        severity = "watch"
        score = 0.75
        message = "Volume is unusually high relative to recent history."
        action = "Check whether price confirms the strategy trigger."
    elif "overextended" in card.factor_flags:
        signal = "overextended"
        severity = "warning"
        score = 0.7
        message = "Factor model marks the setup as short-term overextended."
        action = "Wait for consolidation or pullback before considering entry."

    return _radar_payload(
        instrument_id=instrument_id,
        latest=latest,
        latest_close=latest_close,
        previous_close=previous_close,
        change_pct=change_pct,
        volume_ratio=volume_ratio,
        signal=signal,
        severity=severity,
        score=score,
        message=message,
        action=action,
        card=card,
        distance_to_trigger_pct=distance_to_trigger_pct,
    )


def _radar_payload(
    *,
    instrument_id: str,
    latest,
    latest_close: Decimal,
    previous_close: Decimal | None,
    change_pct: float | None,
    volume_ratio: float | None,
    signal: str,
    severity: str,
    score: float,
    message: str,
    action: str,
    card,
    distance_to_trigger_pct: float | None,
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "instrument_label": format_instrument_label(instrument_id),
        "latest_trade_date": latest["trade_date"].isoformat(),
        "latest_close": _decimal_text(latest_close),
        "previous_close": _decimal_text(previous_close),
        "change_pct": change_pct,
        "volume_ratio": volume_ratio,
        "signal": signal,
        "severity": severity,
        "score": round(float(score), 4),
        "message": message,
        "action": action,
        "distance_to_trigger_pct": distance_to_trigger_pct,
        "trigger_price": _decimal_text(card.entry_plan.trigger_price) if card else None,
        "initial_stop": _decimal_text(card.exit_plan.initial_stop) if card else None,
        "target_1": _decimal_text(card.exit_plan.target_1) if card else None,
        "no_chase_above": _decimal_text(card.entry_plan.no_chase_above) if card else None,
    }


def _radar_severity_rank(severity: str) -> int:
    return {"danger": 4, "success": 3, "warning": 2, "watch": 1, "info": 0}.get(severity, 0)


def _clean_float(value) -> float | None:
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except TypeError:
        return None
    return round(float(value), 4)


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _repo() -> QagentRepository:
    initialize_database()
    return QagentRepository(create_session_factory())


def _ranking_v4_evidence_repo() -> RankingV4EvidenceRepository:
    initialize_database()
    return RankingV4EvidenceRepository(create_session_factory())


def _ranking_v4_release_repo() -> RankingV4ProspectiveReleaseRepository:
    initialize_database()
    return RankingV4ProspectiveReleaseRepository(create_session_factory())


def _market_cache_repo() -> MarketDataCacheRepository:
    initialize_database()
    return MarketDataCacheRepository(create_session_factory())


def _factor_research_repo() -> FactorResearchRepository:
    initialize_database()
    return FactorResearchRepository(create_session_factory())


def _paper_repo() -> PaperTradingRepository:
    initialize_database()
    return PaperTradingRepository(create_session_factory())


def _scan_policy_kwargs(provider: str) -> dict[str, object]:
    repo = _repo()
    return {
        "paper_trading_report": _latest_paper_feedback_report(provider),
        "walk_forward_validation": load_latest_walk_forward_validation(repo, provider),
        "strategy_governance_context": load_strategy_governance_context(repo),
    }


@router.get("/opportunities")
def opportunities(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result, mode, instrument_ids = _scan(provider, symbols)
    _repo().save_scan_run(provider=mode, mode=mode, symbols=instrument_ids, result=result)
    payload = {
        "cards": governed_card_payloads(result.cards, result.strategy_governance),
        "items": [item.model_dump(mode="json") for item in result.items],
        "strategy_health": [item.model_dump(mode="json") for item in result.strategy_health],
        "factor_rankings": [item.model_dump(mode="json") for item in result.factor_rankings],
        "sector_strength": [item.model_dump(mode="json") for item in result.sector_strength],
        "rotation_radar": _rotation_radar_payload(result.cards, result.sector_strength),
        "portfolio_plan": result.portfolio_plan.model_dump(mode="json"),
        "market_intelligence": result.market_intelligence.model_dump(mode="json")
        if result.market_intelligence
        else None,
        "manual_action_center": result.manual_action_center.model_dump(mode="json")
        if result.manual_action_center
        else None,
        "signal_monitor": result.signal_monitor.model_dump(mode="json")
        if result.signal_monitor
        else None,
        "decision_quality_center": result.decision_quality_center.model_dump(mode="json")
        if result.decision_quality_center
        else None,
        "operational_readiness_center": result.operational_readiness_center.model_dump(mode="json")
        if result.operational_readiness_center
        else None,
        "strategy_governance": [
            audit.model_dump(mode="json") for audit in result.strategy_governance
        ],
        "data_health": result.data_health,
    }
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    if not _restore_governance_card_payload(payload):
        _attach_card_briefs_and_cached_benchmarks(payload, mode)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_live_paper_health_payload(payload)
    payload.pop("operational_readiness_center", None)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    _attach_live_paper_health_payload(payload)
    return payload


@router.get("/market-bars")
def market_bars(
    provider: str = "fixture",
    instrument_id: str = "US:TEST",
    days: int = 160,
) -> dict[str, object]:
    mode = provider.strip().lower()
    instrument = _normalize_chart_instrument(instrument_id)
    if days <= 0 or days > 500:
        raise HTTPException(status_code=400, detail="days must be between 1 and 500")
    try:
        market_provider = build_market_data_provider(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    start_date, end_date = _chart_dates(mode, days)
    try:
        bars = market_provider.get_daily_bars([instrument], start=start_date, end=end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if bars.empty:
        raise HTTPException(status_code=404, detail="market bars not found")
    enriched = add_moving_averages(bars.sort_values("trade_date"), windows=(20, 50, 100, 200))
    visible = enriched.tail(days)
    scan_result = run_daily_scan(
        [instrument],
        market_provider,
        mode=mode,
        **_scan_policy_kwargs(mode),
    )
    card = next((item for item in scan_result.cards if item.instrument_id == instrument), None)
    provider_errors = getattr(market_provider, "last_errors", [])
    data_health = {
        "provider": mode,
        "instrument": instrument,
        "bars": str(len(visible)),
    }
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return {
        "instrument_id": instrument,
        "bars": [_chart_bar(row) for _, row in visible.iterrows()],
        "levels": _chart_levels(card),
        "data_health": data_health,
    }


@router.get("/intraday-radar")
def intraday_radar(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    mode = provider.strip().lower()
    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    try:
        market_provider = build_market_data_provider(mode)
        scan_result = run_daily_scan(
            instrument_ids,
            market_provider,
            mode=mode,
            strategy_data_provider=EmptyStrategyDataProvider() if resolved.is_dynamic else None,
            **_scan_policy_kwargs(mode),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    start_date, end_date = _chart_dates(mode, 80)
    cards_by_id = {card.instrument_id: card for card in scan_result.cards}
    scan_items_by_id = {item.instrument_id: item for item in scan_result.items}
    radar_items = []
    for instrument in instrument_ids:
        bars = market_provider.get_daily_bars([instrument], start=start_date, end=end_date)
        radar_items.append(
            _radar_item(
                instrument, bars, cards_by_id.get(instrument), scan_items_by_id.get(instrument)
            )
        )
    radar_items.sort(
        key=lambda item: (_radar_severity_rank(item["severity"]), item["score"]), reverse=True
    )
    provider_errors = getattr(market_provider, "last_errors", [])
    data_health = {
        "provider": mode,
        "symbols": str(len(instrument_ids)),
        "radar_items": str(len(radar_items)),
    }
    data_health.update(resolved.data_health)
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return {"items": radar_items, "data_health": data_health}


@router.get("/factors")
def factors(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result, mode, instrument_ids = _scan(provider, symbols)
    return {
        "provider": mode,
        "symbols": instrument_ids,
        "rankings": [item.model_dump(mode="json") for item in result.factor_rankings],
        "data_health": result.data_health,
    }


@router.get("/factors/backtest")
def factor_backtest(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    forward_days: int = 20,
    step_days: int = 20,
    top_n: int = 3,
    scan_limit: int | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if forward_days <= 0 or forward_days > 120:
        raise HTTPException(status_code=400, detail="forward_days must be between 1 and 120")
    if step_days <= 0 or step_days > 120:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 120")
    if top_n <= 0 or top_n > 50:
        raise HTTPException(status_code=400, detail="top_n must be between 1 and 50")
    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=scan_limit is None,
    )
    instrument_ids = resolved.symbols
    start_date, end_date = _factor_backtest_dates(mode, start, end)
    market_provider = build_market_data_provider(mode)
    bars = market_provider.get_daily_bars(instrument_ids, start_date, end_date)
    live_fundamentals: list[FundamentalSnapshot] = []
    fundamental_errors: list[str] = []
    try:
        live_fundamentals = build_strategy_data_provider(mode).get_fundamentals(
            instrument_ids,
            start=start_date,
            end=end_date,
        )
    except Exception as exc:
        fundamental_errors.append(f"live fundamentals: {exc}")
    repo = _repo()
    if live_fundamentals:
        try:
            repo.upsert_fundamental_snapshots(mode, live_fundamentals)
        except Exception as exc:
            fundamental_errors.append(f"store fundamentals: {exc}")
    try:
        stored_fundamentals = repo.list_fundamental_snapshots(
            provider_mode=mode,
            instrument_ids=instrument_ids,
            start=start_date,
            end=end_date,
        )
    except Exception as exc:
        stored_fundamentals = []
        fundamental_errors.append(f"load fundamentals: {exc}")
    fundamentals = _merge_fundamental_snapshots(stored_fundamentals, live_fundamentals)
    result = run_factor_backtest(
        bars,
        forward_days=forward_days,
        step_days=step_days,
        top_n=top_n,
        fundamentals=fundamentals,
    )
    payload = result.model_dump(mode="json")
    payload["signals"] = [_attach_instrument_label(signal) for signal in payload.get("signals", [])]
    payload["data_health"].update(
        {
            **resolved.data_health,
            "fundamental_live_rows": str(len(live_fundamentals)),
            "fundamental_stored_rows": str(len(stored_fundamentals)),
            "fundamental_point_in_time_rows": str(len(fundamentals)),
            "fundamental_store": "sqlite",
        }
    )
    if fundamental_errors:
        payload["data_health"]["fundamental_errors"] = " | ".join(fundamental_errors[:3])
    return payload


@router.get("/factors/diagnostics")
def factor_diagnostics(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 10,
    top_n: int = 5,
    scan_limit: int | None = None,
    transaction_cost_bps: float = 5.0,
    slippage_bps: float = 5.0,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if step_days <= 0 or step_days > 120:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 120")
    if top_n <= 0 or top_n > 50:
        raise HTTPException(status_code=400, detail="top_n must be between 1 and 50")
    if transaction_cost_bps < 0 or slippage_bps < 0:
        raise HTTPException(status_code=400, detail="cost inputs must be non-negative")
    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=scan_limit is None,
    )
    instrument_ids = resolved.symbols
    start_date, end_date = _factor_backtest_dates(mode, start, end)
    bars = build_market_data_provider(mode).get_daily_bars(
        instrument_ids,
        start_date,
        end_date,
    )
    live_fundamentals: list[FundamentalSnapshot] = []
    fundamental_errors: list[str] = []
    try:
        live_fundamentals = build_strategy_data_provider(mode).get_fundamentals(
            instrument_ids,
            start=start_date,
            end=end_date,
        )
    except Exception as exc:
        fundamental_errors.append(f"live fundamentals: {exc}")
    repo = _repo()
    if live_fundamentals:
        try:
            repo.upsert_fundamental_snapshots(mode, live_fundamentals)
        except Exception as exc:
            fundamental_errors.append(f"store fundamentals: {exc}")
    try:
        stored_fundamentals = repo.list_fundamental_snapshots(
            provider_mode=mode,
            instrument_ids=instrument_ids,
            start=start_date,
            end=end_date,
        )
    except Exception as exc:
        stored_fundamentals = []
        fundamental_errors.append(f"load fundamentals: {exc}")
    fundamentals = _merge_fundamental_snapshots(
        stored_fundamentals,
        live_fundamentals,
    )
    result = run_factor_diagnostics(
        bars,
        fundamentals=fundamentals,
        step_days=step_days,
        top_n=top_n,
        round_trip_cost_bps=2 * (transaction_cost_bps + slippage_bps),
    )
    payload = result.model_dump(mode="json")
    payload["primary"]["signals"] = [
        _attach_instrument_label(signal)
        for signal in payload["primary"].get("signals", [])
    ]
    payload["data_health"].update(
        {
            **resolved.data_health,
            "factor_diagnostics_provider": mode,
            "factor_diagnostics_start": start_date.isoformat(),
            "factor_diagnostics_end": end_date.isoformat(),
            "fundamental_live_rows": str(len(live_fundamentals)),
            "fundamental_stored_rows": str(len(stored_fundamentals)),
            "fundamental_point_in_time_rows": str(len(fundamentals)),
            "fundamental_store": "sqlite",
        }
    )
    if fundamental_errors:
        payload["data_health"]["fundamental_errors"] = " | ".join(
            fundamental_errors[:3]
        )
    return payload


@router.get("/factor-research/experiments")
def factor_research_experiments(limit: int = 20):
    return {
        "experiments": _factor_research_repo().list(limit=min(max(limit, 1), 100)),
        "data_health": {
            "factor_research_recorder": "sqlite",
            "paper_model_isolation": "unchanged",
        },
    }


@router.get("/factor-research/shadow/latest")
def latest_factor_research_shadow(provider: str = "free", top_limit: int = 20):
    run = _factor_research_repo().latest_shadow_run(
        provider,
        top_limit=min(max(top_limit, 1), 100),
    )
    return {
        "run": run,
        "data_health": {
            "factor_shadow_recorder": "sqlite_append_only",
            "factor_shadow_status": "ready" if run is not None else "not_started",
            "paper_model_isolation": "unchanged",
            "paper_order_effect": "none",
        },
    }


@router.get("/factor-research/shadow/evaluation")
def factor_research_shadow_evaluation(
    provider: str = "free",
    as_of_date: date | None = None,
):
    mode = provider.strip().lower()
    effective_as_of = as_of_date or _latest_completed_a_share_session() or date.today()
    evaluation = build_factor_shadow_evaluation(
        create_session_factory(),
        provider_mode=mode,
        as_of_date=effective_as_of,
    )
    return {
        "evaluation": evaluation,
        "data_health": evaluation.data_health,
    }


@router.post("/factor-research/shadow/outcomes/resolve")
def resolve_factor_research_shadow_outcomes(
    provider: str = "free",
    as_of_date: date | None = None,
):
    mode = provider.strip().lower()
    effective_as_of = as_of_date or _latest_completed_a_share_session() or date.today()
    resolution = resolve_factor_shadow_outcomes(
        create_session_factory(),
        provider_mode=mode,
        as_of_date=effective_as_of,
    )
    evaluation = build_factor_shadow_evaluation(
        create_session_factory(),
        provider_mode=mode,
        as_of_date=effective_as_of,
    )
    return {
        "resolution": resolution,
        "evaluation": evaluation,
        "data_health": {
            **resolution.data_health,
            **evaluation.data_health,
        },
    }


@router.get("/factor-research/experiments/{experiment_id}")
def factor_research_experiment(experiment_id: str):
    experiment = _factor_research_repo().get(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="factor research experiment not found")
    return experiment


@router.post("/factor-research/experiments", status_code=202)
def start_factor_research_experiment(request: FactorResearchExperimentRequest):
    initialize_database()
    session_factory = create_session_factory()
    store = FactorResearchRepository(session_factory)
    active = store.active()
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"factor research experiment {active.experiment_id} is already active",
        )
    try:
        config = resolved_config(
            session_factory,
            FactorResearchConfig.model_validate(request.model_dump()),
        )
        revision = current_code_revision()
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    experiment = store.create(
        experiment_name="A-share neutralized factor baseline vs LightGBM",
        provider_mode=config.provider_mode,
        model_family="baseline+lightgbm",
        benchmark_id=config.benchmark_id,
        dataset_revision=int(config.dataset_revision or 0),
        start_date=config.start_date,
        end_date=config.end_date,
        code_revision=revision,
        config=config.model_dump(mode="json"),
    )
    with _factor_research_jobs_lock:
        _submitted_factor_research_jobs.add(experiment.experiment_id)
    future = _factor_research_task_executor.submit(
        execute_factor_research_experiment,
        session_factory,
        experiment.experiment_id,
        config,
    )
    future.add_done_callback(
        lambda _future, experiment_id=experiment.experiment_id: (
            _release_factor_research_submission(experiment_id)
        )
    )
    return experiment


def _merge_fundamental_snapshots(
    *groups: list[FundamentalSnapshot],
) -> list[FundamentalSnapshot]:
    by_point: dict[tuple[str, date], FundamentalSnapshot] = {}
    for group in groups:
        for snapshot in group:
            key = (snapshot.instrument_id, snapshot.as_of_date)
            current = by_point.get(key)
            if current is None or _fundamental_completeness(snapshot) >= _fundamental_completeness(
                current
            ):
                by_point[key] = snapshot
    return sorted(
        by_point.values(),
        key=lambda item: (item.as_of_date, item.instrument_id, item.provider),
    )


def _fundamental_completeness(snapshot: FundamentalSnapshot) -> int:
    return sum(
        value is not None
        for value in (
            snapshot.revenue_growth_pct,
            snapshot.earnings_growth_pct,
            snapshot.gross_margin_pct,
            snapshot.operating_margin_pct,
            snapshot.net_margin_pct,
            snapshot.return_on_equity_pct,
            snapshot.market_cap,
            snapshot.pe_ratio,
            snapshot.forward_pe,
            snapshot.peg_ratio,
            snapshot.price_to_sales,
        )
    )


@router.get("/overview")
def overview(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result, _, _ = _scan(provider, symbols)
    payload = {
        "market_regime": {
            "US": "development_fixture",
            "CN": "development_fixture",
        },
        "top_cards": [card.model_dump(mode="json") for card in result.cards[:5]],
        "strategy_health": [item.model_dump(mode="json") for item in result.strategy_health[:6]],
        "factor_rankings": [item.model_dump(mode="json") for item in result.factor_rankings[:10]],
        "sector_strength": [item.model_dump(mode="json") for item in result.sector_strength[:6]],
        "rotation_radar": _rotation_radar_payload(result.cards, result.sector_strength),
        "portfolio_plan": result.portfolio_plan.model_dump(mode="json"),
        "market_intelligence": result.market_intelligence.model_dump(mode="json")
        if result.market_intelligence
        else None,
        "manual_action_center": result.manual_action_center.model_dump(mode="json")
        if result.manual_action_center
        else None,
        "signal_monitor": result.signal_monitor.model_dump(mode="json")
        if result.signal_monitor
        else None,
        "decision_quality_center": result.decision_quality_center.model_dump(mode="json")
        if result.decision_quality_center
        else None,
        "operational_readiness_center": result.operational_readiness_center.model_dump(mode="json")
        if result.operational_readiness_center
        else None,
        "data_health": result.data_health,
    }
    _attach_signal_hub_payload(payload, cards_key="top_cards")
    _attach_market_intelligence_payload(payload, cards_key="top_cards")
    _attach_recommendation_quality_payload(payload, cards_key="top_cards")
    _attach_probability_forecast_payload(payload, cards_key="top_cards")
    _attach_manual_action_center_payload(payload, cards_key="top_cards")
    _attach_signal_monitor_payload(payload, cards_key="top_cards")
    _attach_decision_quality_payload(payload, cards_key="top_cards")
    _attach_operational_readiness_payload(payload, cards_key="top_cards")
    _attach_alpha_quality_payload(payload, cards_key="top_cards")
    _attach_research_center_payload(payload, cards_key="top_cards")
    return payload


@router.get("/daily-brief")
def daily_brief(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
    fast: bool = False,
    skip_backtest: bool = False,
    scan_limit: int | None = None,
) -> dict[str, object]:
    if fast:
        skip_backtest = True
        include_news = False
    brief = _build_daily_brief_response(
        provider=provider,
        symbols=symbols,
        limit=limit,
        include_news=include_news,
        skip_backtest=skip_backtest,
        scan_limit=scan_limit,
        fast=fast,
    )
    return brief.model_dump(mode="json")


@router.post("/daily-brief/runs")
def save_daily_brief_run(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
    fast: bool = False,
    skip_backtest: bool = False,
    scan_limit: int | None = None,
) -> dict[str, object]:
    if fast:
        skip_backtest = True
        include_news = False
    brief = _build_daily_brief_response(
        provider=provider,
        symbols=symbols,
        limit=limit,
        include_news=include_news,
        skip_backtest=skip_backtest,
        scan_limit=scan_limit,
        fast=fast,
    )
    saved = _repo().save_brief_run(brief)
    return saved.model_dump(mode="json")


@router.get("/daily-brief/runs")
def daily_brief_runs(provider: str | None = None, limit: int = 20) -> dict[str, list[object]]:
    mode = provider.strip().lower() if provider else None
    return {
        "runs": [
            run.model_dump(mode="json")
            for run in _repo().list_brief_runs(limit=limit, provider=mode)
        ]
    }


@router.get("/daily-brief/runs/{brief_id}")
def daily_brief_run(brief_id: str) -> dict[str, object]:
    run = _repo().get_brief_run(brief_id)
    if run is None:
        raise HTTPException(status_code=404, detail="brief run not found")
    return {"run": run.model_dump(mode="json"), "brief": run.payload}


@router.get("/daily-brief/runs/{brief_id}/markdown")
def daily_brief_run_markdown(brief_id: str) -> dict[str, str]:
    run = _repo().get_brief_run(brief_id)
    if run is None:
        raise HTTPException(status_code=404, detail="brief run not found")
    brief = DailyBrief.model_validate(run.payload)
    return {"markdown": render_daily_brief_markdown(brief)}


@router.post("/daily-brief/runs/{brief_id}/deliveries")
def queue_daily_brief_delivery(
    brief_id: str,
    channel: str = "markdown",
    recipient: str | None = None,
) -> dict[str, object]:
    repo = _repo()
    run = repo.get_brief_run(brief_id)
    if run is None:
        raise HTTPException(status_code=404, detail="brief run not found")
    if channel not in {"markdown", "email", "webhook"}:
        raise HTTPException(status_code=400, detail="unsupported delivery channel")
    brief = DailyBrief.model_validate(run.payload)
    delivery = repo.enqueue_brief_delivery(
        brief_run=run,
        channel=channel,
        recipient=recipient,
        markdown=render_daily_brief_markdown(brief),
    )
    return delivery.model_dump(mode="json")


@router.get("/deliveries")
def deliveries(
    status: str | None = None,
    provider: str | None = None,
    limit: int = 20,
) -> dict[str, list[object]]:
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    mode = provider.strip().lower() if provider else None
    return {
        "deliveries": [
            delivery.model_dump(mode="json")
            for delivery in _repo().list_delivery_outbox(status=status, limit=limit, provider=mode)
        ]
    }


@router.post("/deliveries/{delivery_id}/mark-sent")
def mark_delivery_sent(delivery_id: str) -> dict[str, object]:
    delivery = _repo().mark_delivery_sent(delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    return delivery.model_dump(mode="json")


@router.post("/automation/run")
def run_automation(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
    queue_brief: bool = True,
    run_alerts: bool = False,
    queue_alerts: bool = True,
    run_backtest: bool = True,
    recipient: str | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if limit <= 0 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    try:
        result = run_research_automation(
            repo=_repo(),
            provider=build_market_data_provider(mode),
            provider_mode=mode,
            symbols=instrument_ids,
            include_news=False if resolved.is_dynamic else include_news,
            queue_brief=queue_brief,
            run_alerts=run_alerts,
            queue_alerts=queue_alerts,
            run_backtest=run_backtest,
            recipient=recipient,
            limit=limit,
            strategy_data_provider=EmptyStrategyDataProvider() if resolved.is_dynamic else None,
        )
        result.data_health.update(resolved.data_health)
        if resolved.is_dynamic:
            result.data_health["automation_news_scope"] = "skipped_for_dynamic_universe"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump(mode="json")


@router.get("/automation/scheduler")
def automation_scheduler_state() -> dict[str, object]:
    return _automation_scheduler.refresh_if_due(_run_auto_processing_cycle).model_dump(mode="json")


@router.post("/automation/scheduler/run-once")
def run_automation_scheduler_once(
    provider: str = "free",
    symbols: str | None = None,
    interval_seconds: int = 1800,
    include_etfs: bool = True,
    run_scan: bool = True,
    scan_max_age_minutes: int = 240,
    batch_size: int = 200,
    max_symbols: int | None = None,
    sync_if_empty: bool = True,
    seed_paper: bool = True,
    seed_limit: int = 10,
    update_paper: bool = True,
    run_alerts: bool = True,
    queue_alerts: bool = True,
    run_forward_evidence: bool = True,
) -> dict[str, object]:
    settings = _auto_processing_settings(
        provider=provider,
        symbols=symbols,
        interval_seconds=interval_seconds,
        include_etfs=include_etfs,
        run_scan=run_scan,
        scan_max_age_minutes=scan_max_age_minutes,
        batch_size=batch_size,
        max_symbols=max_symbols,
        sync_if_empty=sync_if_empty,
        seed_paper=seed_paper,
        seed_limit=seed_limit,
        update_paper=update_paper,
        run_alerts=run_alerts,
        queue_alerts=queue_alerts,
        run_forward_evidence=run_forward_evidence,
    )
    state = _automation_scheduler.run_once(settings, _run_auto_processing_cycle)
    return state.model_dump(mode="json")


@router.post("/automation/scheduler/start")
def start_automation_scheduler(
    provider: str = "free",
    symbols: str | None = None,
    interval_seconds: int = 1800,
    include_etfs: bool = True,
    run_scan: bool = True,
    scan_max_age_minutes: int = 240,
    batch_size: int = 200,
    max_symbols: int | None = None,
    sync_if_empty: bool = True,
    seed_paper: bool = True,
    seed_limit: int = 10,
    update_paper: bool = True,
    run_alerts: bool = True,
    queue_alerts: bool = True,
    run_forward_evidence: bool = True,
) -> dict[str, object]:
    settings = _auto_processing_settings(
        provider=provider,
        symbols=symbols,
        interval_seconds=interval_seconds,
        include_etfs=include_etfs,
        run_scan=run_scan,
        scan_max_age_minutes=scan_max_age_minutes,
        batch_size=batch_size,
        max_symbols=max_symbols,
        sync_if_empty=sync_if_empty,
        seed_paper=seed_paper,
        seed_limit=seed_limit,
        update_paper=update_paper,
        run_alerts=run_alerts,
        queue_alerts=queue_alerts,
        run_forward_evidence=run_forward_evidence,
    )
    state = _automation_scheduler.start(settings, _run_auto_processing_cycle)
    _persist_automation_scheduler_state(state)
    return state.model_dump(mode="json")


@router.post("/automation/scheduler/stop")
def stop_automation_scheduler() -> dict[str, object]:
    state = _automation_scheduler.stop()
    _persist_automation_scheduler_state(state)
    return state.model_dump(mode="json")


def restore_automation_scheduler_from_storage() -> None:
    repo = _repo()
    saved = repo.get_automation_scheduler_state()
    if saved is None:
        return
    try:
        settings = AutoProcessingSettings.model_validate(saved.settings)
    except ValueError:
        return
    if saved.enabled:
        _automation_scheduler.start(settings, _run_auto_processing_cycle)
    else:
        _automation_scheduler.configure(settings)


def _persist_automation_scheduler_state(state) -> None:
    _repo().save_automation_scheduler_state(
        enabled=state.enabled,
        settings=state.settings.model_dump(mode="json"),
    )


def _auto_processing_settings(
    *,
    provider: str,
    symbols: str | None,
    interval_seconds: int,
    include_etfs: bool,
    run_scan: bool,
    scan_max_age_minutes: int,
    batch_size: int,
    max_symbols: int | None,
    sync_if_empty: bool,
    seed_paper: bool,
    seed_limit: int,
    update_paper: bool,
    run_alerts: bool,
    queue_alerts: bool,
    run_forward_evidence: bool,
) -> AutoProcessingSettings:
    try:
        return AutoProcessingSettings(
            provider=provider.strip().lower(),
            symbols=symbols,
            interval_seconds=interval_seconds,
            include_etfs=include_etfs,
            run_scan=run_scan,
            scan_max_age_minutes=scan_max_age_minutes,
            batch_size=batch_size,
            max_symbols=max_symbols,
            sync_if_empty=sync_if_empty,
            seed_paper=seed_paper,
            seed_limit=seed_limit,
            update_paper=update_paper,
            run_alerts=run_alerts,
            queue_alerts=queue_alerts,
            run_forward_evidence=run_forward_evidence,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run_auto_processing_cycle(settings: AutoProcessingSettings) -> AutoProcessingCycleResult:
    started_at = datetime.now(timezone.utc)
    mode = settings.provider.strip().lower()
    expected_signal_date = (
        _latest_completed_a_share_session()
        if mode == "free" and settings.run_scan
        else None
    )
    repo = _repo()
    paper_repo = _paper_repo()
    errors: list[str] = []
    data_health: dict[str, str] = {
        "automation_scheduler": "enabled",
        "automation_provider": mode,
        "automation_run_scan": str(settings.run_scan).lower(),
        "automation_seed_paper": str(settings.seed_paper).lower(),
        "automation_update_paper": str(settings.update_paper).lower(),
        "automation_run_alerts": str(settings.run_alerts).lower(),
        "automation_run_forward_evidence": str(settings.run_forward_evidence).lower(),
    }
    if expected_signal_date is not None:
        data_health["automation_expected_signal_date"] = (
            expected_signal_date.isoformat()
        )
    scan_status = "disabled"
    scan_started = False
    scan_job_id: str | None = None
    paper_created = 0
    alerts_triggered = 0

    if settings.run_scan:
        try:
            if mode == "fixture":
                resolved = _resolve_symbols(mode, settings.symbols)
                automation = run_research_automation(
                    repo=repo,
                    provider=build_market_data_provider(mode),
                    provider_mode=mode,
                    symbols=resolved.symbols,
                    include_news=False,
                    queue_brief=False,
                    run_alerts=False,
                    run_backtest=False,
                    seed_paper=settings.seed_paper,
                    update_paper=False,
                    limit=settings.seed_limit,
                )
                paper_created += automation.summary.paper_created
                scan_status = "completed"
                data_health.update(automation.data_health)
            else:
                scan_status, scan_started, scan_job_id = _maybe_start_automatic_full_scan(
                    repo,
                    settings,
                )
        except Exception as exc:
            scan_status = "failed"
            errors.append(f"scan: {exc}")

    allow_seed_paper, risk_gate_health = _paper_seed_risk_gate(repo, paper_repo, mode)
    data_health.update(risk_gate_health)
    # A full book still needs to evaluate replacement candidates. The risk
    # gate may prevent direct admission, but it must not bypass the
    # low-quality pending-order replacement path.
    if risk_gate_health.get("paper_risk_gate_action") == "capacity_full":
        allow_seed_paper = True

    if settings.seed_paper and mode != "fixture" and allow_seed_paper:
        try:
            effective_seed_limit = _paper_seed_limit_from_risk_gate(
                settings.seed_limit,
                risk_gate_health,
            )
            effective_active_limit = _paper_seed_active_limit_from_risk_gate(
                paper_repo,
                settings.seed_limit,
                risk_gate_health,
            )
            candidate_pool_limit = _paper_candidate_pool_limit(effective_seed_limit)
            snapshots, seed_health = _paper_seed_snapshots_from_recommendations(
                repo,
                mode=mode,
                include_etfs=settings.include_etfs,
                max_age=timedelta(minutes=max(settings.scan_max_age_minutes, 1)),
                limit=candidate_pool_limit,
                expected_signal_date=expected_signal_date,
            )
            risk_gate_health = _paper_merge_market_risk_gate(
                risk_gate_health,
                seed_health,
            )
            data_health.update(risk_gate_health)
            snapshots, production_health = _ranking_v3_production_seed_scope(
                repo,
                snapshots,
                provider=mode,
            )
            data_health.update(production_health)
            snapshots, strategy_capacity_health = _paper_strategy_capacity_filter(
                paper_repo,
                snapshots,
                provider=mode,
                max_per_strategy=2,
            )
            data_health.update(strategy_capacity_health)
            snapshots, industry_capacity_health = _paper_industry_capacity_filter(
                paper_repo,
                snapshots,
                provider=mode,
                max_per_industry=PAPER_MAX_PER_INDUSTRY,
            )
            data_health.update(industry_capacity_health)
            snapshots, market_probe_health = _paper_market_probe_snapshots(
                paper_repo,
                snapshots,
                provider=mode,
                risk_gate_health=risk_gate_health,
                signal_date=expected_signal_date or _a_share_today(),
            )
            data_health.update(market_probe_health)
            effective_seed_limit = _paper_seed_limit_from_risk_gate(
                settings.seed_limit,
                risk_gate_health,
            )
            effective_active_limit = _paper_seed_active_limit_from_risk_gate(
                paper_repo,
                settings.seed_limit,
                risk_gate_health,
            )
            pool_health = _paper_candidate_pool_health(
                paper_repo=paper_repo,
                snapshots=snapshots,
                provider=mode,
                risk_gate_health=risk_gate_health,
            )
            data_health.update(pool_health)
            replacement_health = _maybe_replace_pending_paper_trade_for_candidate(
                paper_repo=paper_repo,
                snapshots=snapshots,
                provider=mode,
                risk_gate_health=risk_gate_health,
            )
            data_health.update(replacement_health)
            replaced_instrument = replacement_health.get("paper_replacement_replacee")
            if (
                replacement_health.get("paper_replacement_action") == "replaced_pending"
                and replaced_instrument
            ):
                snapshots = [
                    snapshot
                    for snapshot in snapshots
                    if snapshot.instrument_id != replaced_instrument
                ]
                data_health["paper_replacement_excluded_replacee"] = replaced_instrument
                replacement_candidate = replacement_health.get("paper_replacement_candidate")
                if replacement_candidate:
                    snapshots = _prioritize_paper_replacement_candidate(
                        snapshots,
                        replacement_candidate,
                    )
                    data_health["paper_replacement_seed_priority"] = str(replacement_candidate)
            recently_released = _paper_recently_released_instruments(
                paper_repo.list_trades(limit=1000, provider=mode)
            )
            if recently_released:
                before_release_filter = len(snapshots)
                snapshots = [
                    snapshot
                    for snapshot in snapshots
                    if snapshot.instrument_id not in recently_released
                ]
                data_health["paper_recently_released_blocked"] = str(
                    before_release_filter - len(snapshots)
                )
            before_price_filter = len(snapshots)
            snapshots = [
                snapshot
                for snapshot in snapshots
                if _paper_candidate_price_basis_is_consistent(
                    snapshot,
                    latest_value=_paper_snapshot_latest_value(snapshot),
                )
            ]
            data_health["paper_missing_or_inconsistent_price_blocked"] = str(
                before_price_filter - len(snapshots)
            )
            tracking_signal_date = (
                expected_signal_date or _a_share_today()
                if seed_health.get("automation_seed_source") == "latest_recommendation_cache"
                else None
            )
            seed_result = seed_paper_trades_from_snapshots(
                paper_repo,
                snapshots,
                provider=mode,
                max_created=effective_seed_limit,
                max_active_trades=effective_active_limit,
                max_signal_age_days=None,
                signal_date_override=tracking_signal_date,
                notes=(
                    "防守行情研究仓位；合格候选可按剩余仓位批量进入，单笔为正常仓位的 35%。"
                    if risk_gate_health.get("paper_risk_gate_action") == "throttle_new_entries"
                    else ""
                ),
                allocation_multiplier=Decimal(
                    risk_gate_health.get("paper_risk_gate_position_size_multiplier", "1.0")
                    if risk_gate_health.get("paper_risk_gate_action") == "throttle_new_entries"
                    else "1.0"
                ),
                admission_repo=repo,
            )
            if risk_gate_health.get("paper_market_entry_gate") == "throttled":
                _, post_seed_market_probe_health = _paper_market_probe_snapshots(
                    paper_repo,
                    snapshots,
                    provider=mode,
                    risk_gate_health=risk_gate_health,
                    signal_date=expected_signal_date or _a_share_today(),
                )
                data_health.update(post_seed_market_probe_health)
            paper_created += seed_result.created
            data_health["automation_seed_snapshots"] = str(len(snapshots))
            data_health["automation_seed_effective_limit"] = str(effective_seed_limit)
            data_health["automation_seed_active_limit"] = str(effective_active_limit)
            data_health["automation_seed_candidate_pool_limit"] = str(candidate_pool_limit)
            data_health.update(seed_health)
            if snapshots and snapshots[0].signal_date is not None:
                data_health["automation_seed_latest_signal_date"] = snapshots[
                    0
                ].signal_date.isoformat()
        except Exception as exc:
            errors.append(f"paper_seed: {exc}")
    elif settings.seed_paper and mode != "fixture":
        data_health["automation_seed_skipped_by_risk_gate"] = "true"

    paper_total = 0
    paper_closed = 0
    if settings.update_paper:
        try:
            paper_update = update_paper_trades(
                paper_repo,
                provider=build_market_data_provider(mode),
                provider_mode=mode,
            )
            paper_total = paper_update.summary.total
            paper_closed = paper_update.summary.closed
            data_health.update(paper_update.data_health)
        except Exception as exc:
            errors.append(f"paper_update: {exc}")
            summary = summarize_paper_trades(
                paper_repo.list_trades(limit=1000, provider=mode),
                reporting_scope="all",
            )
            paper_total = summary.total
            paper_closed = summary.closed
    else:
        summary = summarize_paper_trades(
            paper_repo.list_trades(limit=1000, provider=mode),
            reporting_scope="all",
        )
        paper_total = summary.total
        paper_closed = summary.closed

    if mode == "free":
        try:
            factor_shadow_as_of = (
                expected_signal_date
                or _latest_completed_a_share_session()
                or date.today()
            )
            shadow_resolution = resolve_factor_shadow_outcomes(
                create_session_factory(),
                provider_mode=mode,
                as_of_date=factor_shadow_as_of,
            )
            data_health.update(shadow_resolution.data_health)
            if shadow_resolution.next_maturity_date is not None:
                data_health["factor_shadow_outcome_next_maturity_date"] = (
                    shadow_resolution.next_maturity_date.isoformat()
                )
        except Exception as exc:
            data_health["factor_shadow_outcome_status"] = "error"
            data_health["factor_shadow_outcome_error"] = str(exc)
            errors.append(f"factor_shadow_outcomes: {exc}")

    if settings.run_alerts:
        try:
            alert_result = run_alert_rules(
                repo=repo,
                provider=build_market_data_provider(mode),
                queue_delivery=settings.queue_alerts,
            )
            alerts_triggered = alert_result.summary.triggered
            data_health.update(alert_result.data_health)
        except Exception as exc:
            errors.append(f"alerts: {exc}")

    if mode == "free" and settings.run_forward_evidence:
        try:
            forward_context = _ranking_v3_forward_context(repo)
            forward_results = (
                _run_ranking_v3_forward_catch_up(
                    repo,
                    build_market_data_provider(mode),
                    forward_context,
                    through_date=_a_share_today(),
                )
                if forward_context is not None
                else []
            )
            forward_state = _ranking_v3_forward_state_payload(repo, forward_context)
            data_health["ranking_v3_forward_state"] = str(forward_state["state"])
            data_health["ranking_v3_forward_processed_sessions"] = str(
                len(forward_results)
            )
            if forward_state.get("validation_run_id"):
                data_health["ranking_v3_forward_run_id"] = str(
                    forward_state["validation_run_id"]
                )
            evaluation = forward_state.get("evaluation")
            metrics = (
                evaluation.get("metrics")
                if isinstance(evaluation, Mapping)
                and isinstance(evaluation.get("metrics"), Mapping)
                else {}
            )
            data_health["ranking_v3_forward_sessions"] = str(
                metrics.get("session_count", 0)
            )
            data_health["ranking_v3_forward_completed_trades"] = str(
                metrics.get("completed_trade_count", 0)
            )
            data_health["ranking_v3_forward_release_proof"] = str(
                bool(forward_state.get("release_proof_available"))
            ).lower()
        except Exception as exc:
            data_health["ranking_v3_forward_state"] = "error"
            data_health["ranking_v3_forward_error"] = str(exc)
            errors.append(f"ranking_v3_forward: {exc}")
    elif mode == "free":
        data_health.update(
            {
                "ranking_v3_forward_state": "disabled",
                "ranking_v3_forward_processed_sessions": "0",
                "ranking_v3_forward_sessions": "0",
                "ranking_v3_forward_completed_trades": "0",
                "ranking_v3_forward_release_proof": "false",
            }
        )

    # Seeding and price updates can change active capacity. Keep the gate that
    # governed this cycle for auditability, then publish the post-cycle gate as
    # the current state consumed by the dashboard and the next scheduler run.
    applied_risk_gate_health = dict(risk_gate_health)
    _, final_risk_gate_health = _paper_seed_risk_gate(repo, paper_repo, mode)
    applied_market_gate_health = {
        key: value
        for key, value in applied_risk_gate_health.items()
        if key.startswith("paper_market_entry_gate")
    }
    final_risk_gate_health = _paper_merge_market_risk_gate(
        final_risk_gate_health,
        applied_market_gate_health,
    )
    if final_risk_gate_health != applied_risk_gate_health:
        for key, value in applied_risk_gate_health.items():
            suffix = key.removeprefix("paper_risk_gate_")
            data_health[f"paper_risk_gate_applied_{suffix}"] = value
    data_health.update(final_risk_gate_health)

    finished_at = datetime.now(timezone.utc)
    data_health.update(
        {
            "automation_scan_status": scan_status,
            "automation_scan_started": str(scan_started).lower(),
            "automation_paper_created": str(paper_created),
            "automation_paper_total": str(paper_total),
            "automation_paper_closed": str(paper_closed),
            "automation_alerts_triggered": str(alerts_triggered),
            "automation_errors": str(len(errors)),
        }
    )
    return AutoProcessingCycleResult(
        provider=mode,
        started_at=started_at,
        finished_at=finished_at,
        scan_status=scan_status,
        scan_started=scan_started,
        scan_job_id=scan_job_id,
        paper_created=paper_created,
        paper_total=paper_total,
        paper_closed=paper_closed,
        alerts_triggered=alerts_triggered,
        errors=errors,
        data_health=data_health,
    )


def _paper_seed_risk_gate(
    repo: QagentRepository,
    paper_repo: PaperTradingRepository,
    mode: str,
) -> tuple[bool, dict[str, str]]:
    all_trades = paper_repo.list_trades(limit=1000, provider=mode)
    if not all_trades:
        return True, {
            "paper_risk_gate_action": "allow_new_entries",
            "paper_risk_gate_reason": "no_paper_history",
            "paper_risk_gate_scope": "no_paper_history",
        }
    current_cohort = repo.get_current_paper_model_cohort(mode)
    cohort_by_snapshot = repo.get_paper_model_cohorts_for_snapshots(
        [trade.source_snapshot_id for trade in all_trades]
    )
    if current_cohort is None:
        trades = all_trades
        scope = "all_history_fallback"
    else:
        trades = [
            trade
            for trade in all_trades
            if (
                cohort := cohort_by_snapshot.get(trade.source_snapshot_id)
            ) is not None
            and cohort.cohort_id == current_cohort.cohort_id
        ]
        scope = "current_model_cohort"
    account = paper_repo.get_account_settings()
    ledger = build_paper_ledger(
        trades,
        initial_capital=account.initial_capital,
        allocation_per_trade_pct=account.allocation_per_trade_pct,
        max_positions=account.max_positions,
        transaction_cost_bps=account.transaction_cost_bps,
        slippage_bps=account.slippage_bps,
        take_profit_pct=account.take_profit_pct,
        reporting_scope="all",
    )
    risk_gate = build_paper_risk_gate_status(ledger)
    summary = ledger.summary
    unclassified_trades = sum(
        cohort_by_snapshot.get(trade.source_snapshot_id) is None for trade in all_trades
    )
    health = {
        "paper_risk_gate_action": risk_gate.action,
        "paper_risk_gate_reason": risk_gate.reason,
        "paper_risk_gate_scope": scope,
        "paper_risk_gate_scope_trades": str(len(trades)),
        "paper_risk_gate_excluded_other_cohort": str(len(all_trades) - len(trades)),
        "paper_risk_gate_unclassified_trades": str(unclassified_trades),
        "paper_risk_gate_recovery_state": risk_gate.recovery_state,
        "paper_risk_gate_recovery_score": f"{risk_gate.recovery_score:.4f}",
        "paper_risk_gate_max_new_entries": str(risk_gate.max_new_entries),
        "paper_risk_gate_position_size_multiplier": f"{risk_gate.position_size_multiplier:.4f}",
        "paper_risk_gate_total_return_pct": f"{summary.total_return_pct:.4f}",
        "paper_risk_gate_max_drawdown_pct": f"{summary.max_drawdown_pct:.4f}",
        "paper_risk_gate_closed_trades": str(summary.closed_trades),
        "paper_risk_gate_stopped_count": str(summary.stopped_count),
        "paper_risk_gate_win_rate": (
            f"{summary.win_rate:.4f}" if summary.win_rate is not None else ""
        ),
    }
    if current_cohort is not None:
        health.update(
            {
                "paper_risk_gate_model_cohort_id": current_cohort.cohort_id,
                "paper_risk_gate_feature_set_version": (
                    current_cohort.feature_set_version
                ),
                "paper_risk_gate_recommendation_policy": (
                    current_cohort.recommendation_policy_entrypoint
                ),
            }
        )
    return risk_gate.can_add_entries, health


def _paper_seed_limit_from_risk_gate(
    configured_limit: int,
    risk_gate_health: dict[str, str],
) -> int:
    if risk_gate_health.get("paper_risk_gate_action") not in {
        "throttle_new_entries",
        "capacity_full",
    }:
        return configured_limit
    try:
        max_new = int(risk_gate_health.get("paper_risk_gate_max_new_entries", "1"))
    except ValueError:
        max_new = 1
    return max(1, min(configured_limit, max_new))


def _paper_seed_active_limit_from_risk_gate(
    paper_repo: PaperTradingRepository,
    configured_limit: int,
    risk_gate_health: dict[str, str],
) -> int:
    del configured_limit, risk_gate_health
    account = paper_repo.get_account_settings()
    return max(1, account.max_positions)


def _paper_candidate_pool_limit(effective_seed_limit: int) -> int:
    return max(30, min(120, max(1, effective_seed_limit) * 12))


def _paper_merge_market_risk_gate(
    risk_gate_health: dict[str, str],
    market_gate_health: dict[str, str],
) -> dict[str, str]:
    merged = {**risk_gate_health, **market_gate_health}
    if market_gate_health.get("paper_market_entry_gate") != "throttled":
        return merged
    if merged.get("paper_risk_gate_action") in {"capacity_full", "pause_new_entries"}:
        return merged

    try:
        existing_max = int(merged.get("paper_risk_gate_max_new_entries", "999"))
    except ValueError:
        existing_max = 999
    try:
        existing_multiplier = Decimal(
            merged.get("paper_risk_gate_position_size_multiplier", "1.0")
        )
    except (ArithmeticError, ValueError):
        existing_multiplier = Decimal("1.0")
    market_reason = market_gate_health.get("paper_market_entry_gate_reason", "")
    performance_reason = risk_gate_health.get("paper_risk_gate_reason", "")
    reasons = [reason for reason in (performance_reason, market_reason) if reason]
    merged.update(
        {
            "paper_risk_gate_action": "throttle_new_entries",
            "paper_risk_gate_reason": "；".join(dict.fromkeys(reasons)),
            "paper_risk_gate_recovery_state": "market_reduced_size",
            "paper_risk_gate_max_new_entries": str(existing_max),
            "paper_risk_gate_position_size_multiplier": f"{min(existing_multiplier, PAPER_RISK_OFF_POSITION_SIZE_MULTIPLIER):.4f}",
        }
    )
    return merged


def _paper_market_probe_snapshots(
    paper_repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    *,
    provider: str,
    risk_gate_health: dict[str, str],
    signal_date: date,
) -> tuple[list[OpportunitySnapshotRecord], dict[str, str]]:
    if risk_gate_health.get("paper_market_entry_gate") != "throttled":
        return snapshots, {}

    # Research paper trading needs observations from every market regime. The
    # regular recommendation, governance, freshness, and concentration gates
    # already apply before this point, so risk-off changes sizing rather than
    # applying a second score cutoff.
    qualified = list(snapshots)
    trades = paper_repo.list_trades(limit=1000, provider=provider)
    research_entries_today = sum(
        1
        for trade in trades
        if trade.signal_date == signal_date
        and (
            "风控恢复探针" in trade.notes
            or "防守行情研究仓位" in trade.notes
        )
    )
    active_count = sum(1 for trade in trades if trade.status in {"pending", "open"})
    account = paper_repo.get_account_settings()
    available_slots = max(account.max_positions - active_count, 0)
    selected = qualified if available_slots > 0 else []
    return selected, {
        "paper_market_probe_policy": "all_eligible_candidates_reduced_size",
        "paper_market_probe_min_priority_score": "disabled_for_research",
        "paper_market_probe_qualified": str(len(qualified)),
        "paper_market_probe_filtered": "0",
        "paper_market_probe_existing_today": str(research_entries_today),
        "paper_market_probe_remaining_today": str(available_slots),
        "paper_market_research_available_slots": str(available_slots),
        "paper_market_research_daily_limit": "account_capacity",
    }


def _paper_market_probe_signal_date(
    snapshots: list[OpportunitySnapshotRecord],
    *,
    provider: str,
) -> date:
    if provider == "fixture":
        signal_dates = [snapshot.signal_date for snapshot in snapshots]
        if signal_dates:
            return max(signal_dates)
    if provider == "free":
        return _latest_completed_a_share_session() or _a_share_today()
    return _a_share_today()


def _paper_candidate_pool_snapshot_items(
    *,
    paper_repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    provider: str,
    risk_gate_health: dict[str, str],
    limit: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    account = paper_repo.get_account_settings()
    trades = paper_repo.list_trades(limit=1000, provider=provider)
    active = [trade for trade in trades if trade.status in {"pending", "open"}]
    active_by_instrument = {trade.instrument_id: trade for trade in active}
    existing_sources = {trade.source_snapshot_id for trade in trades}
    recently_released_by_instrument = {
        trade.instrument_id: trade for trade in trades if _paper_trade_recently_released(trade)
    }
    recently_released = set(recently_released_by_instrument)
    active_count = len(active)
    replacee = _paper_replacement_trade(active)
    active_industry_counts, active_industry_by_instrument, unknown_active_industries = (
        _paper_active_industry_counts(paper_repo, active)
    )
    available_industry_counts = dict(active_industry_counts)
    if replacee is not None and active_count >= account.max_positions:
        replacee_industry = active_industry_by_instrument.get(replacee.instrument_id)
        if replacee_industry is not None:
            available_industry_counts[replacee_industry] = max(
                0,
                available_industry_counts.get(replacee_industry, 0) - 1,
            )
    reserved_industry_counts: dict[str, int] = {}
    replacee_score = _float_value(replacee.rank_score) if replacee else 0.0
    replacee_pressure = _paper_pending_replacement_pressure(replacee) if replacee else 0.0
    risk_action = risk_gate_health.get("paper_risk_gate_action", "")
    market_entry_blocked = risk_gate_health.get("paper_market_entry_gate") == "blocked"
    expected_signal_date = None
    expected_signal_date_value = risk_gate_health.get(
        "paper_candidate_expected_signal_date"
    )
    if expected_signal_date_value:
        try:
            expected_signal_date = date.fromisoformat(expected_signal_date_value)
        except ValueError:
            expected_signal_date = None
    replacement_used = False
    items: list[dict[str, object]] = []
    for snapshot in snapshots:
        if len(items) >= limit:
            break
        score = _paper_snapshot_priority_score(snapshot)
        theme_boost = _paper_theme_boost(snapshot)
        active_trade = active_by_instrument.get(snapshot.instrument_id)
        reference_trade = active_trade or recently_released_by_instrument.get(
            snapshot.instrument_id
        )
        latest_value = _paper_snapshot_latest_value(snapshot, reference_trade)
        entry_gap_pct = _paper_entry_gap_pct(snapshot, latest_value=latest_value)
        price_basis_consistent = _paper_candidate_price_basis_is_consistent(
            snapshot,
            latest_value=latest_value,
        )
        industry = _paper_snapshot_industry(snapshot)
        industry_active_count = active_industry_counts.get(industry, 0) if industry else 0
        industry_occupied = (
            available_industry_counts.get(industry, 0)
            + reserved_industry_counts.get(industry, 0)
            if industry
            else 0
        )
        is_untracked_candidate = (
            active_trade is None
            and snapshot.instrument_id not in recently_released
            and snapshot.snapshot_id not in existing_sources
        )
        signal_date_fresh = (
            expected_signal_date is None
            or snapshot.signal_date == expected_signal_date
        )
        industry_blocked = is_untracked_candidate and (
            industry is None or industry_occupied >= PAPER_MAX_PER_INDUSTRY
        )
        industry_capacity_available = (
            industry is not None and industry_occupied < PAPER_MAX_PER_INDUSTRY
        )
        status = "waiting"
        action = "等待下一轮"
        replacement_target = None
        replacement_pressure = None
        if not price_basis_consistent:
            status = "blocked_by_data"
            action = "价格基准不一致"
        elif active_trade is not None:
            status = "active_in_paper"
            action = "已在模拟盘"
        elif snapshot.instrument_id in recently_released:
            status = "tracked_before"
            action = "刚释放冷却"
        elif snapshot.snapshot_id in existing_sources:
            status = "tracked_before"
            action = "历史已跟踪"
        elif is_untracked_candidate and not signal_date_fresh:
            status = "blocked_by_data"
            action = "数据待刷新：信号日期不是最新交易日"
        elif market_entry_blocked:
            status = "blocked_by_market"
            action = "市场风控暂停入场"
        elif industry is None:
            status = "blocked_by_industry"
            action = "行业数据缺失"
        elif industry_blocked:
            status = "blocked_by_industry"
            action = "行业集中度已达上限"
        elif risk_action == "pause_new_entries":
            status = "paused_by_risk"
            action = "风控暂停新增"
        elif active_count < account.max_positions:
            status = "ready_to_add"
            action = "有空位，下一轮可加入"
        elif (
            replacee is not None
            and not replacement_used
            and _paper_candidate_should_replace(
                candidate_score=score,
                replacee_score=replacee_score,
                replacee_pressure=replacee_pressure,
            )
        ):
            status = "replace_candidate"
            action = "可替换低质量等待单"
            replacement_target = replacee.instrument_id
            replacement_pressure = replacee_pressure
            replacement_used = True
        elif active_count >= account.max_positions:
            status = "waiting_for_slot"
            action = "满额等待"
        if (
            price_basis_consistent
            and signal_date_fresh
            and is_untracked_candidate
            and industry is not None
            and not industry_blocked
        ):
            reserved_industry_counts[industry] = (
                reserved_industry_counts.get(industry, 0) + 1
            )
        items.append(
            {
                "snapshot_id": snapshot.snapshot_id,
                "instrument_id": snapshot.instrument_id,
                "instrument_label": _paper_snapshot_label(snapshot),
                "asset_type": _paper_snapshot_asset_type(snapshot),
                "strategy_id": snapshot.primary_strategy_id,
                "industry": industry,
                "exposure_group": industry,
                "industry_active_count": industry_active_count,
                "industry_capacity_used": industry_occupied,
                "industry_capacity_limit": PAPER_MAX_PER_INDUSTRY,
                "industry_capacity_available": industry_capacity_available,
                "industry_blocked": industry_blocked,
                "signal_date": snapshot.signal_date.isoformat() if snapshot.signal_date else None,
                "signal_date_fresh": signal_date_fresh,
                "rank_score": _float_value(snapshot.rank_score),
                "priority_score": score,
                "market_theme_boost": theme_boost,
                "entry_gap_pct": entry_gap_pct,
                "price_basis_consistent": price_basis_consistent,
                "trigger_price": str(snapshot.trigger_price)
                if snapshot.trigger_price is not None
                else None,
                "latest_close": str(latest_value) if latest_value is not None else None,
                "status": status,
                "action": action,
                "replacement_target": replacement_target,
                "replacement_pressure": replacement_pressure,
                "reason": _paper_candidate_reason(snapshot, theme_boost, entry_gap_pct),
            }
        )
    waiting_count = sum(
        1
        for item in items
        if item["status"] in {"waiting", "waiting_for_slot", "replace_candidate", "ready_to_add"}
    )
    replacement_candidates = sum(1 for item in items if item["status"] == "replace_candidate")
    market_blocked_count = sum(1 for item in items if item["status"] == "blocked_by_market")
    data_blocked_count = sum(1 for item in items if item["status"] == "blocked_by_data")
    industry_blocked_count = sum(1 for item in items if item["industry_blocked"])
    industry_missing_count = sum(
        1 for item in items if item["industry"] is None and item["industry_blocked"]
    )
    summary = {
        "total_candidates": len(snapshots),
        "shown_candidates": len(items),
        "active_count": active_count,
        "max_positions": account.max_positions,
        "waiting_count": waiting_count,
        "replacement_candidates": replacement_candidates,
        "market_blocked_count": market_blocked_count,
        "data_blocked_count": data_blocked_count,
        "industry_capacity_limit": PAPER_MAX_PER_INDUSTRY,
        "industry_blocked_count": industry_blocked_count,
        "industry_missing_count": industry_missing_count,
        "active_industry_unknown_count": unknown_active_industries,
        "active_industry_counts": dict(sorted(active_industry_counts.items())),
        "risk_action": risk_action,
        "entry_calibration_action": _paper_entry_calibration_action(items),
        "market_adaptive_action": (
            "theme_boost_enabled"
            if any(item["market_theme_boost"] for item in items)
            else "theme_boost_idle"
        ),
    }
    return items, summary


def _paper_snapshot_label(snapshot: OpportunitySnapshotRecord) -> str:
    label = snapshot.card.get("instrument_label") or snapshot.card.get("instrument_name")
    return str(label).strip() if label else snapshot.instrument_id


def _paper_snapshot_asset_type(snapshot: OpportunitySnapshotRecord) -> str:
    card = snapshot.card if isinstance(snapshot.card, dict) else {}
    asset_type = str(card.get("asset_type") or "").strip().lower()
    if asset_type:
        return asset_type
    label = _paper_snapshot_label(snapshot)
    return "etf" if "ETF" in label.upper() else "unknown"


def _paper_entry_gap_pct(
    snapshot: OpportunitySnapshotRecord,
    *,
    latest_value: Decimal | None = None,
) -> float | None:
    latest = latest_value if latest_value is not None else snapshot.latest_close
    if not snapshot.trigger_price or not latest or snapshot.trigger_price <= 0:
        return None
    return round(float((snapshot.trigger_price - latest) / snapshot.trigger_price * 100), 2)


def _paper_candidate_price_basis_is_consistent(
    snapshot: OpportunitySnapshotRecord,
    *,
    latest_value: Decimal | None,
    max_gap_ratio: Decimal | None = None,
) -> bool:
    trigger = snapshot.trigger_price
    if trigger is None or latest_value is None or trigger <= 0 or latest_value <= 0:
        return False
    if not paper_snapshot_price_basis_is_consistent(snapshot, max_gap_ratio=max_gap_ratio):
        return False
    gap_limit = max_gap_ratio or paper_price_basis_gap_limit(getattr(snapshot, "instrument_id", ""))
    return abs(trigger - latest_value) / trigger <= gap_limit


def _paper_snapshot_latest_value(
    snapshot: OpportunitySnapshotRecord,
    reference_trade: PaperTradeRecord | None = None,
) -> Decimal | None:
    if reference_trade is not None and reference_trade.latest_price is not None:
        return reference_trade.latest_price
    if snapshot.latest_close is not None:
        return snapshot.latest_close
    trading_status = snapshot.card.get("trading_status")
    if not isinstance(trading_status, dict):
        return None
    try:
        latest = _decimal_or_none(trading_status.get("latest_close"))
    except (ArithmeticError, ValueError):
        return None
    return latest if latest is not None and latest > 0 else None


def _paper_candidate_reason(
    snapshot: OpportunitySnapshotRecord,
    theme_boost: float,
    entry_gap_pct: float | None,
) -> str:
    parts = []
    if theme_boost > 0:
        parts.append("强主题加权")
    if entry_gap_pct is not None:
        if entry_gap_pct > 8:
            parts.append(f"触发价偏远 {entry_gap_pct:.1f}%")
        elif entry_gap_pct >= 0:
            parts.append(f"接近买点 {entry_gap_pct:.1f}%")
        else:
            parts.append(f"已越过触发 {abs(entry_gap_pct):.1f}%")
    if snapshot.signal_date == _a_share_today():
        parts.append("今日信号")
    return " / ".join(parts) if parts else "按综合优先级排队"


def _paper_entry_calibration_action(items: list[dict[str, object]]) -> str:
    far_waiting = [
        item
        for item in items
        if item.get("status") in {"waiting_for_slot", "active_in_paper"}
        and (item.get("entry_gap_pct") or 0) > 8
    ]
    replaceable = [item for item in items if item.get("status") == "replace_candidate"]
    if replaceable:
        return "replace_far_pending"
    if far_waiting:
        return "tighten_far_triggers"
    return "keep_current_trigger"


def _paper_candidate_pool_health(
    *,
    paper_repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    provider: str,
    risk_gate_health: dict[str, str],
) -> dict[str, str]:
    trades = paper_repo.list_trades(limit=1000, provider=provider)
    active_instruments = {
        trade.instrument_id for trade in trades if trade.status in {"pending", "open"}
    }
    existing_sources = {trade.source_snapshot_id for trade in trades}
    recently_released = _paper_recently_released_instruments(trades)
    waiting = [
        snapshot
        for snapshot in snapshots
        if snapshot.instrument_id not in active_instruments
        and snapshot.instrument_id not in recently_released
        and snapshot.snapshot_id not in existing_sources
        and _paper_candidate_price_basis_is_consistent(
            snapshot,
            latest_value=_paper_snapshot_latest_value(snapshot),
        )
    ]
    boosted = [snapshot for snapshot in snapshots if _paper_theme_boost(snapshot) > 0]
    top = waiting[0] if waiting else None
    return {
        "paper_candidate_pool_total": str(len(snapshots)),
        "paper_candidate_pool_waiting_count": str(len(waiting)),
        "paper_candidate_pool_active_count": str(len(active_instruments)),
        "paper_candidate_pool_top": top.instrument_id if top else "",
        "paper_candidate_pool_top_score": f"{_paper_snapshot_priority_score(top):.4f}"
        if top
        else "",
        "paper_market_adaptive_theme_boosted": str(len(boosted)),
        "paper_candidate_pool_risk_action": risk_gate_health.get("paper_risk_gate_action", ""),
    }


def _maybe_replace_pending_paper_trade_for_candidate(
    *,
    paper_repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    provider: str,
    risk_gate_health: dict[str, str],
) -> dict[str, str]:
    if not snapshots:
        return {"paper_replacement_action": "no_candidate"}
    if risk_gate_health.get("paper_risk_gate_action") == "pause_new_entries":
        return {"paper_replacement_action": "paused_by_risk_gate"}

    account = paper_repo.get_account_settings()
    trades = paper_repo.list_trades(limit=1000, provider=provider)
    active = [trade for trade in trades if trade.status in {"pending", "open"}]
    if len(active) < account.max_positions:
        return {
            "paper_replacement_action": "slot_available",
            "paper_replacement_active_count": str(len(active)),
            "paper_replacement_max_positions": str(account.max_positions),
        }

    active_instruments = {trade.instrument_id for trade in active}
    existing_sources = {trade.source_snapshot_id for trade in trades}
    recently_released = _paper_recently_released_instruments(trades)
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot.instrument_id not in active_instruments
        and snapshot.instrument_id not in recently_released
        and snapshot.snapshot_id not in existing_sources
        and _paper_candidate_price_basis_is_consistent(
            snapshot,
            latest_value=_paper_snapshot_latest_value(snapshot),
        )
    ]
    candidates.sort(key=_paper_snapshot_priority_score, reverse=True)
    if not candidates:
        return {
            "paper_replacement_action": "no_new_candidate",
            "paper_replacement_active_count": str(len(active)),
        }
    replacee = _paper_replacement_trade(active)
    if replacee is None:
        return {
            "paper_replacement_action": "no_replaceable_pending",
            "paper_replacement_candidate": candidates[0].instrument_id,
        }
    candidate = candidates[0]
    candidate_score = _paper_snapshot_priority_score(candidate)
    replacee_score = _float_value(replacee.rank_score)
    pressure = _paper_pending_replacement_pressure(replacee)
    if not _paper_candidate_should_replace(
        candidate_score=candidate_score,
        replacee_score=replacee_score,
        replacee_pressure=pressure,
    ):
        return {
            "paper_replacement_action": "kept_existing_pending",
            "paper_replacement_candidate": candidate.instrument_id,
            "paper_replacement_candidate_score": f"{candidate_score:.4f}",
            "paper_replacement_replacee": replacee.instrument_id,
            "paper_replacement_replacee_score": f"{replacee_score:.4f}",
            "paper_replacement_pressure": f"{pressure:.4f}",
        }

    today = _a_share_today()
    note = _append_note_text(
        replacee.notes,
        (
            "候补替换：模拟盘名额已满，"
            f"{candidate.instrument_id} 优先级 {candidate_score:.2f} 高于当前等待单；"
            "原单转入已跟踪，不再占用活跃名额。"
        ),
    )
    paper_repo.update_trade(
        replacee.trade_id,
        status="replaced",
        exit_date=today,
        latest_date=replacee.latest_date or today,
        latest_price=replacee.latest_price,
        realized_return_pct=None,
        notes=note,
    )
    return {
        "paper_replacement_action": "replaced_pending",
        "paper_replacement_candidate": candidate.instrument_id,
        "paper_replacement_candidate_score": f"{candidate_score:.4f}",
        "paper_replacement_replacee": replacee.instrument_id,
        "paper_replacement_replacee_score": f"{replacee_score:.4f}",
        "paper_replacement_pressure": f"{pressure:.4f}",
    }


def _paper_replacement_trade(active: list[PaperTradeRecord]) -> PaperTradeRecord | None:
    pending = [trade for trade in active if trade.status == "pending"]
    if not pending:
        return None
    ranked = sorted(
        pending,
        key=lambda trade: (
            _paper_pending_replacement_pressure(trade),
            -_float_value(trade.rank_score),
        ),
        reverse=True,
    )
    return ranked[0] if ranked else None


def _paper_pending_replacement_pressure(trade: PaperTradeRecord) -> float:
    today = _a_share_today()
    age_days = max((today - trade.signal_date).days, 0) if trade.signal_date else 0
    gap = 0.0
    if trade.trigger_price and trade.latest_price and trade.trigger_price > 0:
        gap = max(0.0, float((trade.trigger_price - trade.latest_price) / trade.trigger_price))
    pressure = 0.0
    if age_days >= 2:
        pressure += 0.18
    if age_days >= 4:
        pressure += 0.18
    if gap >= 0.05:
        pressure += 0.18
    if gap >= 0.15:
        pressure += 0.25
    if gap >= 0.30:
        # A pending breakout that is this far from the live price is usually
        # stale or based on an inconsistent adjustment basis. Do not let it
        # occupy a paper-trading slot for the full waiting window.
        pressure += 0.35
    if _float_value(trade.rank_score) < 0.7:
        pressure += 0.12
    return round(min(1.0, pressure), 4)


def _paper_candidate_should_replace(
    *,
    candidate_score: float,
    replacee_score: float,
    replacee_pressure: float,
) -> bool:
    if candidate_score < 0.68:
        return False
    if replacee_pressure >= 0.5 and candidate_score >= replacee_score + 0.03:
        return True
    if replacee_pressure >= 0.75 and candidate_score >= 0.7:
        return True
    return candidate_score >= replacee_score + 0.12


def _prioritize_paper_replacement_candidate(
    snapshots: list[OpportunitySnapshotRecord],
    instrument_id: str,
) -> list[OpportunitySnapshotRecord]:
    return sorted(
        snapshots,
        key=lambda snapshot: snapshot.instrument_id != instrument_id,
    )


def _paper_snapshot_priority_score(snapshot: OpportunitySnapshotRecord | None) -> float:
    if snapshot is None:
        return 0.0
    base = (
        _float_value(snapshot.rank_score) * 0.72
        + _float_value(snapshot.strategy_score) * 0.18
        + _float_value(snapshot.score) * 0.1
    )
    theme_boost = _paper_theme_boost(snapshot)
    freshness_boost = 0.02 if snapshot.signal_date == _a_share_today() else 0.0
    return round(max(0.0, min(1.0, base + theme_boost + freshness_boost)), 4)


def _paper_theme_boost(snapshot: OpportunitySnapshotRecord) -> float:
    text = " ".join(
        str(value)
        for value in (
            snapshot.instrument_id,
            snapshot.card.get("instrument_label"),
            snapshot.card.get("instrument_name"),
            snapshot.card.get("theme"),
            snapshot.card.get("sector"),
        )
        if value
    )
    if any(keyword in text for keyword in ("科创", "半导体", "芯片", "集成电路", "先进封装")):
        return 0.08
    if any(keyword in text for keyword in ("机器人", "AI", "人工智能")):
        return 0.04
    return 0.0


def _append_note_text(existing: str, text: str) -> str:
    if not existing:
        return text
    if text in existing:
        return existing
    return f"{existing} {text}"


def _paper_recently_released_instruments(trades: list[PaperTradeRecord]) -> set[str]:
    return {trade.instrument_id for trade in trades if _paper_trade_recently_released(trade)}


def _paper_recent_invalidated_instruments(provider: str) -> set[str]:
    try:
        trades = _paper_repo().list_trades(limit=1000, provider=provider)
    except Exception:
        return set()
    return {
        trade.instrument_id
        for trade in trades
        if trade.status == "invalidated" and _paper_trade_recently_released(trade)
    }


def _filter_recent_invalidated_payload_cards(
    payload: dict[str, object],
    *,
    provider: str,
    cards_key: str = "cards",
) -> int:
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return 0
    invalidated = _paper_recent_invalidated_instruments(provider)
    if not invalidated:
        return 0
    filtered = [
        card
        for card in raw_cards
        if not isinstance(card, dict) or card.get("instrument_id") not in invalidated
    ]
    removed = len(raw_cards) - len(filtered)
    payload[cards_key] = filtered
    return removed


def _paper_trade_recently_released(trade: PaperTradeRecord) -> bool:
    today = _a_share_today()
    release_date = trade.exit_date or trade.latest_date or trade.signal_date
    if release_date is None:
        return False
    age_days = (today - release_date).days
    if trade.status == "invalidated":
        return 0 <= age_days <= 7
    if trade.status in {"replaced", "missed_entry"} and "候补替换" in trade.notes:
        return 0 <= age_days <= 3
    return False


def _paper_seed_snapshots_from_recommendations(
    repo: QagentRepository,
    *,
    mode: str,
    include_etfs: bool,
    max_age: timedelta,
    limit: int,
    expected_signal_date: date | None = None,
    include_market_blocked: bool = False,
) -> tuple[list, dict[str, str]]:
    snapshots, health = _paper_seed_snapshots_from_latest_cache(
        repo,
        mode=mode,
        include_etfs=include_etfs,
        max_age=max_age,
        limit=limit,
        expected_signal_date=expected_signal_date,
        include_market_blocked=include_market_blocked,
    )
    if (
        health.get("paper_market_entry_gate") == "blocked"
        and not include_market_blocked
    ):
        return [], health
    if health.get("paper_candidate_freshness_gate") == "blocked":
        return [], health
    cache_is_authoritative = health.get("automation_seed_source") == ("latest_recommendation_cache")
    snapshots, cache_blocked = _filter_governed_paper_snapshots(
        repo,
        snapshots,
        provider=mode,
    )
    if snapshots or cache_is_authoritative:
        return snapshots, {
            **health,
            "paper_strategy_governance_blocked": str(cache_blocked),
        }

    fallback = repo.list_latest_signal_opportunity_snapshots(limit=limit, provider=mode)
    fallback, fallback_blocked = _filter_governed_paper_snapshots(
        repo,
        fallback,
        provider=mode,
    )
    return fallback, {
        "automation_seed_source": "latest_signal_day",
        "automation_seed_rank_profile": "rank_score",
        "paper_strategy_governance_blocked": str(cache_blocked + fallback_blocked),
    }


def _filter_governed_paper_snapshots(
    repo: QagentRepository,
    snapshots: list,
    *,
    provider: str,
) -> tuple[list, int]:
    context = load_strategy_governance_context(repo)
    allowed = []
    blocked = 0
    for snapshot in snapshots:
        if (
            _paper_snapshot_governance_block_reason(
                repo,
                snapshot,
                provider=provider,
                governance_context=context,
            )
            is not None
        ):
            blocked += 1
            continue
        allowed.append(snapshot)
    return allowed, blocked


def _paper_snapshot_governance_block_reason(
    repo: QagentRepository,
    snapshot: OpportunitySnapshotRecord,
    *,
    provider: str,
    admission_mode: str = "automatic",
    governance_context=None,
) -> str | None:
    card = snapshot.card if isinstance(snapshot.card, dict) else {}
    context = governance_context or load_strategy_governance_context(repo)
    strategy_id = snapshot.primary_strategy_id or ""
    runtime = context.strategies.get(strategy_id)
    if runtime is not None and runtime.state in {"research", "disabled"}:
        return (
            f"strategy {strategy_id} is {runtime.state} under policy "
            f"{runtime.policy_version} and is not eligible for paper trading"
        )

    governance = card.get("strategy_governance")
    gate = governance.get("gate_decision") if isinstance(governance, dict) else None
    if isinstance(gate, dict) and gate.get("paper_candidate_eligible") is False:
        return "opportunity is not eligible for paper trading under its authoritative gate"
    decision = card.get("decision")
    if isinstance(decision, dict) and (
        decision.get("risk_status") in {"blocked", "veto"}
        or decision.get("action") in {"avoid", "blocked", "no_trade"}
    ):
        return "opportunity is blocked by its authoritative decision"

    admission = evaluate_paper_snapshot_admission(
        repo,
        snapshot,
        provider=provider,
        mode=admission_mode,
    )
    return admission.reason if not admission.eligible else None


def _paper_seed_snapshots_from_latest_cache(
    repo: QagentRepository,
    *,
    mode: str,
    include_etfs: bool,
    max_age: timedelta,
    limit: int,
    expected_signal_date: date | None = None,
    include_market_blocked: bool = False,
) -> tuple[list, dict[str, str]]:
    cached, cache_freshness = _automation_scan_result_cache(
        repo,
        cache_key=full_market_batch_cache_key(mode, include_etfs),
        max_age=max_age,
        expected_signal_date=expected_signal_date,
    )
    if cached is None:
        health = {
            "automation_seed_source": "fresh_recommendation_cache_unavailable",
            "automation_seed_cache_freshness": cache_freshness,
        }
        if expected_signal_date is not None:
            health.update(
                {
                    "paper_candidate_freshness_gate": "blocked",
                    "paper_candidate_expected_signal_date": (
                        expected_signal_date.isoformat()
                    ),
                }
            )
        return [], health
    market_gate_health = _paper_market_entry_gate_from_cache(cached.payload)
    if (
        market_gate_health.get("paper_market_entry_gate") == "blocked"
        and not include_market_blocked
    ):
        return [], {
            **market_gate_health,
            "automation_seed_source": "latest_recommendation_cache",
            "automation_seed_cache_id": cached.cache_id,
            "automation_seed_cache_freshness": cache_freshness,
        }
    raw_cards = cached.payload.get("cards")
    if not isinstance(raw_cards, list):
        return [], {}

    cards = [
        card
        for card in raw_cards
        if isinstance(card, dict) and _is_trackable_cached_paper_card(card)
    ]
    ranked = sorted(cards, key=_balanced_cached_card_score, reverse=True)
    ranked = ranked[: max(limit * 3, limit)]
    card_ids = [_string_value(card.get("card_id")) for card in ranked]
    snapshots_by_card = {
        snapshot.card_id: snapshot
        for snapshot in repo.list_latest_opportunity_snapshots_by_card_ids(
            card_ids,
            provider=mode,
        )
    }
    selected = []
    seen_snapshot_ids: set[str] = set()
    for card in ranked:
        snapshot = snapshots_by_card.get(_string_value(card.get("card_id")))
        if snapshot is None or snapshot.snapshot_id in seen_snapshot_ids:
            continue
        if snapshot.instrument_id != _string_value(card.get("instrument_id")):
            continue
        cached_latest = _paper_card_latest_value(card)
        updates: dict[str, object] = {"card": deepcopy(card)}
        if snapshot.latest_close is None and cached_latest is not None:
            updates["latest_close"] = cached_latest
        snapshot = snapshot.model_copy(update=updates)
        selected.append(snapshot)
        seen_snapshot_ids.add(snapshot.snapshot_id)
    freshness_health: dict[str, str] = {}
    if expected_signal_date is not None:
        mismatched = [
            snapshot
            for snapshot in selected
            if snapshot.signal_date != expected_signal_date
        ]
        if mismatched:
            selected = [
                snapshot
                for snapshot in selected
                if snapshot.signal_date == expected_signal_date
            ]
            freshness_health = {
                **market_gate_health,
                "automation_seed_source": "latest_recommendation_cache",
                "automation_seed_cache_id": cached.cache_id,
                "automation_seed_cache_freshness": cache_freshness,
                "paper_candidate_freshness_gate": (
                    "filtered" if selected else "blocked"
                ),
                "paper_candidate_expected_signal_date": expected_signal_date.isoformat(),
                "paper_candidate_signal_date_mismatch": str(len(mismatched)),
            }
            if not selected:
                return [], freshness_health
        else:
            freshness_health = {
                "paper_candidate_freshness_gate": "fresh",
                "paper_candidate_expected_signal_date": expected_signal_date.isoformat(),
                "paper_candidate_signal_date_mismatch": "0",
            }
    if not selected:
        if ranked:
            return [], {
                **market_gate_health,
                "automation_seed_source": "latest_recommendation_cache",
                "automation_seed_cache_id": cached.cache_id,
                "automation_seed_cache_freshness": cache_freshness,
                "paper_authoritative_snapshot_missing": str(len(ranked)),
            }
        return [], {}
    return selected, {
        **market_gate_health,
        **freshness_health,
        "automation_seed_source": "latest_recommendation_cache",
        "automation_seed_cache_id": cached.cache_id,
        "automation_seed_cache_freshness": cache_freshness,
        "automation_seed_rank_profile": "balanced",
        "paper_strategy_diversification_limit": "2",
    }


def _ranking_v3_production_seed_scope(
    repo: QagentRepository,
    snapshots: list[OpportunitySnapshotRecord],
    *,
    provider: str,
) -> tuple[list[OpportunitySnapshotRecord], dict[str, str]]:
    signal_dates = sorted(
        {snapshot.signal_date for snapshot in snapshots if snapshot.signal_date is not None}
    )
    if not signal_dates:
        return snapshots, {"ranking_v3_production_state": "no_signal_date"}
    session_date = signal_dates[-1]
    try:
        result = run_ranking_v3_production_day(
            repo,
            session_date=session_date,
            provider=provider,
        )
    except PermissionError as exc:
        if "no current approved release" in str(exc):
            return snapshots, {"ranking_v3_production_state": "inactive"}
        return [], {
            "ranking_v3_production_state": "blocked",
            "ranking_v3_production_error": str(exc),
        }
    except RankingV3ProductionSnapshotUnavailable as exc:
        return [], {
            "ranking_v3_production_state": "waiting_snapshot",
            "ranking_v3_production_error": str(exc),
        }
    except (LookupError, RuntimeError, TypeError, ValueError) as exc:
        return [], {
            "ranking_v3_production_state": "blocked",
            "ranking_v3_production_error": str(exc),
        }

    selected: list[OpportunitySnapshotRecord] = []
    for item in result.batch.selections:
        snapshot = repo.get_opportunity_snapshot(item.source_snapshot_id)
        if snapshot is None:
            return [], {
                "ranking_v3_production_state": "blocked",
                "ranking_v3_production_error": (
                    f"selected source snapshot {item.source_snapshot_id} is missing"
                ),
            }
        selected.append(snapshot)
    return selected, {
        "ranking_v3_production_state": "recorded",
        "ranking_v3_production_session": session_date.isoformat(),
        "ranking_v3_production_selected": str(result.selected_count),
        "ranking_v3_production_batch": result.batch_fact_digest,
        "ranking_v3_production_scan_run": result.source_scan_run_id,
    }


def _paper_market_entry_gate_from_cache(
    payload: dict[str, object],
) -> dict[str, str]:
    trend = payload.get("benchmark_trend")
    if not isinstance(trend, dict):
        return {
            "paper_market_entry_gate": "unknown",
            "paper_market_entry_gate_reason": "benchmark_trend_missing",
        }
    state = _string_value(trend.get("state")) or "unknown"
    entry_allowed = trend.get("entry_allowed")
    hard_blocked = trend.get("hard_block") is True or state in {
        "extreme_risk",
        "market_halt",
    }
    throttled = not hard_blocked and (entry_allowed is False or state == "risk_off")
    gate = "blocked" if hard_blocked else "throttled" if throttled else "allowed"
    max_new_entries = 0 if hard_blocked else None
    reason = _string_value(trend.get("reason"))
    if throttled:
        reason = (
            f"{reason}；研究模拟盘保留当日有效候选，按 35% 仓位补至账户上限。"
            if reason
            else "风险规避行情，研究模拟盘保留当日有效候选，按 35% 仓位补至账户上限。"
        )
    return {
        "paper_market_entry_gate": gate,
        "paper_market_entry_gate_state": state,
        "paper_market_entry_gate_reason": reason,
        "paper_market_entry_gate_max_new_entries": (
            str(max_new_entries) if max_new_entries is not None else ""
        ),
        "paper_market_entry_gate_position_size_multiplier": (
            "0.0000"
            if hard_blocked
            else f"{PAPER_RISK_OFF_POSITION_SIZE_MULTIPLIER:.4f}"
            if throttled
            else "1.0000"
        ),
    }


def _paper_market_entry_gate_from_latest_cache(
    repo: QagentRepository,
    *,
    mode: str,
    include_etfs: bool = True,
) -> dict[str, str]:
    cached, freshness = _automation_scan_result_cache(
        repo,
        cache_key=full_market_batch_cache_key(mode, include_etfs),
        max_age=timedelta(days=7),
    )
    if cached is None:
        return {}
    return {
        **_paper_market_entry_gate_from_cache(cached.payload),
        "paper_market_entry_gate_cache_freshness": freshness,
    }


def _paper_apply_risk_gate_health_to_report(
    report: PaperDailyReport,
    risk_gate_health: dict[str, str],
) -> None:
    if risk_gate_health.get("paper_risk_gate_action") != "throttle_new_entries":
        return
    risk_gate = report.risk_gate
    risk_gate.action = "throttle_new_entries"
    risk_gate.can_add_entries = True
    risk_gate.title = "风险规避期小仓位采样"
    risk_gate.reason = risk_gate_health.get(
        "paper_risk_gate_reason",
        "风险规避行情，合格候选可以小仓位补至账户上限。",
    )
    risk_gate.reasons = list(dict.fromkeys([*risk_gate.reasons, "market_risk_off"]))
    risk_gate.recovery_conditions = list(
        dict.fromkeys(
            [
                *risk_gate.recovery_conditions,
                "市场基准恢复趋势后恢复正常仓位倍率",
            ]
        )
    )
    risk_gate.recovery_state = "market_reduced_size"
    max_new_entries = risk_gate_health.get("paper_risk_gate_max_new_entries")
    if max_new_entries:
        try:
            risk_gate.max_new_entries = int(max_new_entries)
        except ValueError:
            pass
    try:
        risk_gate.position_size_multiplier = float(
            risk_gate_health.get(
                "paper_risk_gate_position_size_multiplier",
                str(PAPER_RISK_OFF_POSITION_SIZE_MULTIPLIER),
            )
        )
    except ValueError:
        risk_gate.position_size_multiplier = float(
            PAPER_RISK_OFF_POSITION_SIZE_MULTIPLIER
        )


def _paper_strategy_capacity_filter(
    paper_repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    *,
    provider: str,
    max_per_strategy: int,
) -> tuple[list[OpportunitySnapshotRecord], dict[str, str]]:
    if max_per_strategy <= 0:
        raise ValueError("max_per_strategy must be positive")
    trades = paper_repo.list_trades(limit=1000, provider=provider)
    active = [trade for trade in trades if trade.status in {"pending", "open"}]
    pending = [trade for trade in active if trade.status == "pending"]
    account = paper_repo.get_account_settings()
    if len(active) >= account.max_positions and pending:
        return snapshots, {
            "paper_strategy_capacity_limit": str(max_per_strategy),
            "paper_strategy_capacity_blocked": "0",
            "paper_strategy_capacity_active": str(len(active)),
            "paper_strategy_capacity_mode": "replacement_only",
        }

    active_counts: dict[str, int] = {}
    active_instruments = {trade.instrument_id for trade in active}
    existing_sources = {trade.source_snapshot_id for trade in trades}
    for trade in active:
        strategy_id = trade.strategy_id or "unclassified"
        active_counts[strategy_id] = active_counts.get(strategy_id, 0) + 1

    allowed: list[OpportunitySnapshotRecord] = []
    blocked = 0
    already_tracked = 0
    selected_counts: dict[str, int] = {}
    for snapshot in snapshots:
        if snapshot.instrument_id in active_instruments or snapshot.snapshot_id in existing_sources:
            already_tracked += 1
            continue
        strategy_id = snapshot.primary_strategy_id or "unclassified"
        occupied = active_counts.get(strategy_id, 0)
        selected = selected_counts.get(strategy_id, 0)
        if occupied + selected >= max_per_strategy:
            blocked += 1
            continue
        allowed.append(snapshot)
        selected_counts[strategy_id] = selected + 1
    return allowed, {
        "paper_strategy_capacity_limit": str(max_per_strategy),
        "paper_strategy_capacity_blocked": str(blocked),
        "paper_strategy_capacity_active": str(sum(active_counts.values())),
        "paper_strategy_capacity_already_tracked": str(already_tracked),
        "paper_strategy_capacity_mode": "new_entries",
    }


def _paper_industry_capacity_filter(
    paper_repo: PaperTradingRepository,
    snapshots: list[OpportunitySnapshotRecord],
    *,
    provider: str,
    max_per_industry: int,
) -> tuple[list[OpportunitySnapshotRecord], dict[str, str]]:
    if max_per_industry <= 0:
        raise ValueError("max_per_industry must be positive")
    trades = paper_repo.list_trades(limit=1000, provider=provider)
    active = [trade for trade in trades if trade.status in {"pending", "open"}]
    active_counts, active_by_instrument, unknown_active = _paper_active_industry_counts(
        paper_repo,
        active,
    )
    account = paper_repo.get_account_settings()
    replacee = (
        _paper_replacement_trade(active)
        if len(active) >= account.max_positions
        else None
    )
    available_counts = dict(active_counts)
    if replacee is not None:
        replacee_industry = active_by_instrument.get(replacee.instrument_id)
        if replacee_industry is not None:
            available_counts[replacee_industry] = max(
                0,
                available_counts.get(replacee_industry, 0) - 1,
            )

    active_instruments = {trade.instrument_id for trade in active}
    existing_sources = {trade.source_snapshot_id for trade in trades}
    selected_counts: dict[str, int] = {}
    allowed: list[OpportunitySnapshotRecord] = []
    blocked = 0
    missing = 0
    already_tracked = 0
    for snapshot in snapshots:
        if snapshot.instrument_id in active_instruments or snapshot.snapshot_id in existing_sources:
            already_tracked += 1
            continue
        if not _paper_candidate_price_basis_is_consistent(
            snapshot,
            latest_value=_paper_snapshot_latest_value(snapshot),
        ):
            # The downstream price filter owns this rejection. Do not let an
            # already-invalid row consume an industry slot ahead of valid rows.
            allowed.append(snapshot)
            continue
        industry = _paper_snapshot_industry(snapshot)
        if industry is None:
            missing += 1
            continue
        occupied = available_counts.get(industry, 0) + selected_counts.get(industry, 0)
        if occupied >= max_per_industry:
            blocked += 1
            continue
        allowed.append(snapshot)
        selected_counts[industry] = selected_counts.get(industry, 0) + 1
    return allowed, {
        "paper_industry_capacity_limit": str(max_per_industry),
        "paper_industry_capacity_blocked": str(blocked),
        "paper_industry_capacity_missing": str(missing),
        "paper_industry_capacity_active_known": str(sum(active_counts.values())),
        "paper_industry_capacity_active_unknown": str(unknown_active),
        "paper_industry_capacity_already_tracked": str(already_tracked),
        "paper_industry_capacity_mode": (
            "replacement_only" if replacee is not None else "new_entries"
        ),
    }


def _paper_active_industry_counts(
    paper_repo: PaperTradingRepository,
    active_trades: list[PaperTradeRecord],
) -> tuple[dict[str, int], dict[str, str | None], int]:
    counts: dict[str, int] = {}
    by_instrument: dict[str, str | None] = {}
    unknown = 0
    for trade in active_trades:
        context = paper_repo.get_trade_source_context(trade.source_snapshot_id)
        context_card = getattr(context, "card", None) if context is not None else None
        industry = _paper_card_exposure_group(
            context_card if isinstance(context_card, dict) else {},
            current_industry=getattr(context, "industry", None),
            instrument_id=trade.instrument_id,
        )
        by_instrument[trade.instrument_id] = industry
        if industry is None:
            unknown += 1
            continue
        counts[industry] = counts.get(industry, 0) + 1
    return counts, by_instrument, unknown


def _paper_snapshot_industry(snapshot: OpportunitySnapshotRecord) -> str | None:
    snapshot_card = getattr(snapshot, "card", None)
    card = snapshot_card if isinstance(snapshot_card, dict) else {}
    market_context = card.get("market_context")
    market_context = market_context if isinstance(market_context, dict) else {}
    return _paper_card_exposure_group(
        card,
        current_industry=(
            market_context.get("industry") or card.get("industry") or card.get("sector")
        ),
        instrument_id=snapshot.instrument_id,
    )


def _paper_card_exposure_group(
    card: dict[str, object],
    *,
    current_industry: object,
    instrument_id: str,
) -> str | None:
    normalized = _normalized_paper_industry(current_industry)
    market_context = card.get("market_context")
    market_context = market_context if isinstance(market_context, dict) else {}
    asset_type = str(card.get("asset_type") or "").strip().lower()
    board = str(market_context.get("board") or "").strip().lower()
    label_value = card.get("instrument_label") or card.get("instrument_name")
    label = str(label_value).strip() if label_value else ""
    is_etf = (
        asset_type == "etf"
        or board == "etf"
        or "ETF" in label.upper()
        or (normalized or "").lower() in {"指数etf", "etf", "未知etf暴露"}
    )
    if not is_etf:
        return normalized
    return infer_etf_exposure_group(label, current_industry=normalized)


def _normalized_paper_industry(value: object) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return None if normalized.lower() in _UNKNOWN_PAPER_INDUSTRIES else normalized


def _is_trackable_cached_paper_card(card: dict[str, object]) -> bool:
    entry_plan = card.get("entry_plan")
    trigger_price = entry_plan.get("trigger_price") if isinstance(entry_plan, dict) else None
    decision = card.get("decision")
    risk_status = decision.get("risk_status") if isinstance(decision, dict) else None
    action = decision.get("action") if isinstance(decision, dict) else None
    return bool(trigger_price) and risk_status != "blocked" and action != "avoid"


def _paper_card_latest_value(card: dict[str, object]) -> Decimal | None:
    trading_status = card.get("trading_status")
    if not isinstance(trading_status, dict):
        return None
    try:
        latest = _decimal_or_none(trading_status.get("latest_close"))
    except (ArithmeticError, ValueError):
        return None
    return latest if latest is not None and latest > 0 else None


def _balanced_cached_card_score(card: dict[str, object]) -> float:
    base = (
        _float_value(card.get("rank_score")) * 0.45
        + _float_value(card.get("factor_score")) * 0.25
        + _float_value(card.get("strategy_score")) * 0.3
    )
    decision = card.get("decision")
    risk_status = decision.get("risk_status") if isinstance(decision, dict) else None
    risk_penalty = 0.45 if risk_status == "blocked" else 0.16 if risk_status == "warning" else 0
    return max(0.0, min(1.0, base - risk_penalty * 0.65))


def _float_value(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(result):
        return 0.0
    return result


def _string_value(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _a_share_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _latest_completed_a_share_session(now: datetime | None = None) -> date | None:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(ZoneInfo("Asia/Shanghai"))
    today = local_now.date()
    sessions = trading_sessions_in_range(today - timedelta(days=14), today)
    if not sessions:
        return None
    if (
        sessions[-1] == today
        and local_now.timetz().replace(tzinfo=None) < time(hour=15, minute=30)
    ):
        sessions = sessions[:-1]
    return sessions[-1] if sessions else None


def _automatic_full_scan_window(now: datetime | None = None) -> tuple[bool, str]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(ZoneInfo("Asia/Shanghai"))
    today = local_now.date()
    sessions = trading_sessions_in_range(today, today)
    local_time = local_now.timetz().replace(tzinfo=None)
    if (
        sessions
        and time(hour=9, minute=15)
        <= local_time
        < PAPER_CANDIDATE_POST_CLOSE_REFRESH_TIME
    ):
        return False, "market_session_open"
    return True, "ready"


def _cached_paper_candidate_signal_date_health(
    repo: QagentRepository,
    cached: ScanResultCacheRecord,
    *,
    provider: str,
    expected_signal_date: date,
) -> dict[str, str]:
    raw_cards = cached.payload.get("cards")
    if not isinstance(raw_cards, list):
        return {"automatic_candidate_freshness_state": "empty"}
    card_ids = list(
        dict.fromkeys(
            _string_value(card.get("card_id"))
            for card in raw_cards
            if isinstance(card, dict) and _is_trackable_cached_paper_card(card)
        )
    )
    card_ids = [card_id for card_id in card_ids if card_id]
    if not card_ids:
        return {
            "automatic_candidate_freshness_state": "empty",
            "automatic_candidate_cache_cards": "0",
        }
    snapshots = repo.list_latest_opportunity_snapshots_by_card_ids(
        card_ids,
        provider=provider,
    )
    current_count = sum(
        1 for snapshot in snapshots if snapshot.signal_date == expected_signal_date
    )
    stale_count = sum(
        1 for snapshot in snapshots if snapshot.signal_date != expected_signal_date
    )
    missing_count = max(len(card_ids) - len(snapshots), 0)
    if current_count == 0:
        state = "stale"
    elif stale_count or missing_count:
        state = "partial"
    else:
        state = "fresh"
    return {
        "automatic_candidate_freshness_state": state,
        "automatic_candidate_expected_signal_date": expected_signal_date.isoformat(),
        "automatic_candidate_cache_cards": str(len(card_ids)),
        "automatic_candidate_snapshots": str(len(snapshots)),
        "automatic_candidate_current_snapshots": str(current_count),
        "automatic_candidate_stale_snapshots": str(stale_count),
        "automatic_candidate_missing_snapshots": str(missing_count),
    }


def _full_market_scan_is_post_close_candidate_refresh(
    job: object,
    *,
    expected_signal_date: date,
) -> bool:
    finished_at = getattr(job, "finished_at", None)
    data_health = getattr(job, "data_health", {})
    if finished_at is None or not isinstance(data_health, Mapping):
        return False
    if data_health.get("full_market_signal_date") != expected_signal_date.isoformat():
        return False
    cutoff = datetime.combine(
        expected_signal_date,
        PAPER_CANDIDATE_POST_CLOSE_REFRESH_TIME,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    ).astimezone(timezone.utc)
    return _as_utc_datetime(finished_at) >= cutoff


def _automatic_candidate_refresh_attempt(job: object) -> int:
    data_health = getattr(job, "data_health", {})
    if not isinstance(data_health, Mapping):
        return 1
    raw_attempt = data_health.get("automatic_candidate_refresh_attempt", "1")
    try:
        return max(int(raw_attempt), 1)
    except (TypeError, ValueError):
        return 1


def _automatic_candidate_settlement_retry_ready(
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_time = current.astimezone(ZoneInfo("Asia/Shanghai")).timetz().replace(
        tzinfo=None
    )
    return local_time >= PAPER_CANDIDATE_SETTLEMENT_RETRY_TIME


def _scan_cache_signal_date(cached: ScanResultCacheRecord) -> date | None:
    payload = cached.payload if isinstance(cached.payload, Mapping) else {}
    health = payload.get("data_health")
    raw_value = (
        health.get("full_market_signal_date")
        if isinstance(health, Mapping)
        else None
    )
    if not isinstance(raw_value, str):
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        return None


def _automation_scan_result_cache(
    repo: QagentRepository,
    *,
    cache_key: str,
    max_age: timedelta,
    expected_signal_date: date | None = None,
) -> tuple[ScanResultCacheRecord | None, str]:
    cached = repo.get_recent_scan_result_cache(cache_key=cache_key, max_age=max_age)
    if cached is not None:
        if (
            expected_signal_date is not None
            and _scan_cache_signal_date(cached) != expected_signal_date
        ):
            return None, "stale_signal_retry_window"
        return cached, "age_window"

    # A scan produced after the current A-share session remains the latest
    # actionable evidence for that entire local date. Do not fall back to a
    # previous signal day merely because the wall-clock TTL expires at night.
    market_day_cache = repo.get_recent_scan_result_cache(
        cache_key=cache_key,
        max_age=max(max_age, timedelta(days=1)),
    )
    if market_day_cache is None:
        return None, "missing"
    if (
        expected_signal_date is not None
        and _scan_cache_signal_date(market_day_cache) != expected_signal_date
    ):
        return None, "stale_signal_date"
    if expected_signal_date is not None:
        # Before the current session closes, yesterday remains the latest
        # completed A-share session. Keep that cache authoritative across
        # midnight instead of launching another scan for the same signal day.
        return market_day_cache, "same_completed_session"
    created_at = market_day_cache.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at.astimezone(ZoneInfo("Asia/Shanghai")).date() == _a_share_today():
        return market_day_cache, "same_market_day"
    return None, "expired"


def _maybe_start_automatic_full_scan(
    repo: QagentRepository,
    settings: AutoProcessingSettings,
) -> tuple[str, bool, str | None]:
    mode = settings.provider.strip().lower()
    latest = repo.get_latest_full_market_scan_job(provider=mode)
    if latest and latest.status in {"queued", "running"}:
        if latest.data_health.get("automatic_scan_aborted") == "true":
            latest = repo.update_full_market_scan_job(
                latest.job_id,
                status="failed",
                message="Aborted full-market scan ignored by automation scheduler",
                data_health=latest.data_health,
            )
        elif not _full_market_scan_job_is_stale(latest, settings):
            return "already_running", False, latest.job_id
        else:
            _terminate_full_market_executor()
            repo.update_full_market_scan_job(
                latest.job_id,
                status="queued",
                message="Stale full-market scan queued for checkpoint resume",
                data_health={
                    **latest.data_health,
                    "full_market_stale_reset": "true",
                    "full_market_stale_reset_at": datetime.now(timezone.utc).isoformat(),
                    "full_market_restart_recovery": "stale_checkpoint_resume",
                },
            )
            submitted = _submit_full_market_scan_job(latest.job_id)
            return "resumed_stale", submitted, latest.job_id
    expected_signal_date = (
        _latest_completed_a_share_session() if mode == "free" else None
    )
    cached, freshness = _automation_scan_result_cache(
        repo,
        cache_key=full_market_batch_cache_key(mode, settings.include_etfs),
        max_age=timedelta(minutes=settings.scan_max_age_minutes),
        expected_signal_date=expected_signal_date,
    )
    candidate_refresh_health: dict[str, str] = {}
    candidate_refresh_needed = False
    candidate_refresh_attempt = 1
    if cached is not None:
        if expected_signal_date is not None:
            candidate_refresh_health = _cached_paper_candidate_signal_date_health(
                repo,
                cached,
                provider=mode,
                expected_signal_date=expected_signal_date,
            )
            candidate_refresh_needed = candidate_refresh_health.get(
                "automatic_candidate_freshness_state"
            ) in {"stale", "partial"}
        if not candidate_refresh_needed:
            return "cache_fresh", False, latest.job_id if latest else None
        if latest and _full_market_scan_is_post_close_candidate_refresh(
            latest,
            expected_signal_date=expected_signal_date,
        ):
            previous_attempt = _automatic_candidate_refresh_attempt(latest)
            if previous_attempt >= PAPER_CANDIDATE_MAX_REFRESH_ATTEMPTS:
                freshness_state = candidate_refresh_health.get(
                    "automatic_candidate_freshness_state"
                )
                status = (
                    "candidate_data_partially_stale_filtered"
                    if freshness_state == "partial"
                    else "candidate_data_stale_after_retry"
                )
                return status, False, latest.job_id
            if not _automatic_candidate_settlement_retry_ready():
                return "waiting_candidate_data_settlement", False, latest.job_id
            candidate_refresh_attempt = previous_attempt + 1
    if freshness == "stale_signal_retry_window":
        return "waiting_market_data", False, latest.job_id if latest else None
    scan_allowed, _ = _automatic_full_scan_window()
    if not scan_allowed:
        return "deferred_market_session", False, latest.job_id if latest else None

    summary = repo.tradable_catalog_summary()
    if settings.sync_if_empty and summary.total_count == 0:
        sync_cn_tradable_catalog(repo=repo, include_full_etfs=settings.include_etfs)
    symbols = build_full_market_batch_symbols(
        repo=repo,
        include_etfs=settings.include_etfs,
        max_symbols=settings.max_symbols,
    )
    if not symbols:
        raise ValueError("tradable catalog is empty")
    job = repo.create_full_market_scan_job(
        provider=mode,
        symbols=symbols,
        batch_size=settings.batch_size,
        include_etfs=settings.include_etfs,
        sync_if_empty=settings.sync_if_empty,
    )
    if candidate_refresh_needed:
        updated = repo.update_full_market_scan_job(
            job.job_id,
            message="Queued post-close refresh for stale paper candidates",
            data_health={
                **candidate_refresh_health,
                "automatic_candidate_refresh": "true",
                "automatic_candidate_refresh_attempt": str(
                    candidate_refresh_attempt
                ),
            },
        )
        if updated is not None:
            job = updated
    _submit_full_market_scan_job(job.job_id)
    return (
        "queued_candidate_refresh" if candidate_refresh_needed else "queued",
        True,
        job.job_id,
    )


def _full_market_scan_job_is_stale(job, settings: AutoProcessingSettings) -> bool:
    if job.status not in {"queued", "running"}:
        return False
    updated_at = _as_utc_datetime(job.updated_at)
    elapsed = datetime.now(timezone.utc) - updated_at
    if (
        job.total_symbols > 0
        and job.scanned_symbols >= job.total_symbols
        and job.total_batches > 0
        and job.completed_batches >= job.total_batches
    ):
        # Global reranking and final policy reconciliation are CPU-heavy for
        # the complete universe. A ten-minute timeout caused the scheduler to
        # repeatedly kill and replay this phase from checkpoints.
        finalizing_stale_after = max(
            timedelta(hours=2),
            timedelta(seconds=max(settings.interval_seconds * 4, 0)),
        )
        return elapsed > finalizing_stale_after
    stale_after = timedelta(seconds=max(settings.interval_seconds * 2, 30 * 60))
    return elapsed > stale_after


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _paper_reporting_scope_value(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"official", "legacy"}:
        raise HTTPException(
            status_code=400,
            detail="reporting_scope must be official or legacy",
        )
    return normalized


def _paper_reporting_trades(
    trades: list[PaperTradeRecord],
    *,
    reporting_scope: str,
) -> tuple[list[PaperTradeRecord], set[str], dict[str, str]]:
    authenticated_ids, authentication_health = authenticated_ranking_v3_paper_trade_ids(
        _repo(),
        trades,
    )
    if reporting_scope == "official":
        selected = [trade for trade in trades if trade.trade_id in authenticated_ids]
    else:
        selected = [trade for trade in trades if trade.trade_id not in authenticated_ids]
    return selected, authenticated_ids, authentication_health


@router.get("/paper-trades")
def paper_trades(
    status: str | None = None,
    limit: int = 100,
    provider: str | None = None,
    reporting_scope: str = "official",
) -> dict[str, object]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    mode = provider.strip().lower() if provider else None
    scope = _paper_reporting_scope_value(reporting_scope)
    trades = _paper_repo().list_trades(status=status, limit=limit, provider=mode)
    reporting_trades, authenticated_ids, authentication_health = _paper_reporting_trades(
        trades,
        reporting_scope=scope,
    )
    return {
        "summary": summarize_paper_trades(
            trades,
            reporting_scope=scope,
            authenticated_trade_ids=authenticated_ids,
        ).model_dump(mode="json"),
        "trades": [trade.model_dump(mode="json") for trade in reporting_trades],
        "data_health": {
            **paper_execution_data_health(),
            **authentication_health,
            "paper_provider_filter": mode or "all",
            "paper_reporting_scope": scope,
            "paper_reporting_returned": str(len(reporting_trades)),
        },
    }


def _paper_account_scope_status(
    trades: list[PaperTradeRecord],
    *,
    scope: str,
    max_positions: int,
    authenticated_trade_ids: set[str],
) -> dict[str, object]:
    summary = summarize_paper_trades(
        trades,
        reporting_scope=scope,
        authenticated_trade_ids=authenticated_trade_ids,
    )
    active = summary.pending + summary.open
    return {
        **summary.model_dump(mode="json"),
        "active": active,
        "remaining": max(0, max_positions - active),
        "max_positions": max_positions,
    }


def _paper_current_model_status(
    repo: QagentRepository,
    trades: list[PaperTradeRecord],
    *,
    provider: str | None,
    as_of_completed_session: date | None = None,
) -> dict[str, object] | None:
    if provider is None:
        return None
    current_cohort = repo.get_current_paper_model_cohort(provider)
    if current_cohort is None:
        return None
    cohorts_by_snapshot = repo.get_paper_model_cohorts_for_snapshots(
        [trade.source_snapshot_id for trade in trades]
    )
    current_trades = [
        trade
        for trade in trades
        if (
            cohort := cohorts_by_snapshot.get(trade.source_snapshot_id)
        ) is not None
        and cohort.cohort_id == current_cohort.cohort_id
    ]
    summary = summarize_paper_trades(current_trades, reporting_scope="all")
    scan_start_date = _paper_model_cohort_scan_start(
        repo,
        provider=provider,
        cohort=current_cohort,
    )
    trade_start_date = min(
        (trade.signal_date for trade in current_trades),
        default=None,
    )
    completed_session = as_of_completed_session or _latest_completed_a_share_session()
    return {
        **summary.model_dump(mode="json"),
        "active": summary.pending + summary.open,
        "cohort_id": current_cohort.cohort_id,
        "feature_set_version": current_cohort.feature_set_version,
        "recommendation_policy": current_cohort.recommendation_policy_entrypoint,
        "scan_start_date": scan_start_date,
        "trade_start_date": trade_start_date,
        "completed_scan_sessions": _paper_completed_session_count(
            scan_start_date,
            completed_session,
        ),
        "completed_trade_sessions": _paper_completed_session_count(
            trade_start_date,
            completed_session,
        ),
        "excluded_other_cohort": len(trades) - len(current_trades),
        "unclassified": sum(
            cohorts_by_snapshot.get(trade.source_snapshot_id) is None
            for trade in trades
        ),
    }


def _paper_model_cohort_scan_start(
    repo: QagentRepository,
    *,
    provider: str,
    cohort: PaperModelCohortRecord,
) -> date | None:
    signal_dates: list[date] = []
    for run in repo.list_scan_runs(limit=500, provider=provider):
        if run.mode != "full_market_batch":
            continue
        run_cohort = paper_model_cohort_from_data_health(run.data_health)
        if run_cohort is None or run_cohort.cohort_id != cohort.cohort_id:
            continue
        raw_signal_date = str(run.data_health.get("full_market_signal_date") or "").strip()
        if not raw_signal_date:
            continue
        try:
            signal_dates.append(date.fromisoformat(raw_signal_date))
        except ValueError:
            continue
    return min(signal_dates, default=None)


def _paper_completed_session_count(start: date | None, end: date | None) -> int:
    if start is None or end is None or end < start:
        return 0
    return len(trading_sessions_in_range(start, end))


def _paper_observation_status(
    account: PaperAccountSettings,
    *,
    as_of_completed_session: date | None,
) -> dict[str, object]:
    today = _a_share_today()
    today_is_session = bool(trading_sessions_in_range(today, today))
    return {
        "account_start_date": account.started_at.date(),
        "account_completed_sessions": _paper_completed_session_count(
            account.started_at.date(),
            as_of_completed_session,
        ),
        "as_of_completed_session": as_of_completed_session,
        "current_session_date": today if today_is_session else None,
        "current_session_in_progress": bool(
            today_is_session and as_of_completed_session != today
        ),
        "calendar": "XSHG",
    }


@router.get("/paper-trades/account-status")
def paper_trade_account_status(provider: str | None = None) -> dict[str, object]:
    mode = provider.strip().lower() if provider else None
    paper_repo = _paper_repo()
    repo = _repo()
    account = paper_repo.get_account_settings()
    trades = paper_repo.list_trades(limit=1000, provider=mode)
    active_by_id = {
        trade.trade_id: trade
        for status in ("pending", "open")
        for trade in paper_repo.list_trades(status=status, limit=5000, provider=mode)
    }
    trades_by_id = {trade.trade_id: trade for trade in trades}
    trades_by_id.update(active_by_id)
    scoped_trades = list(trades_by_id.values())
    completed_session = _latest_completed_a_share_session()
    _, authenticated_ids, authentication_health = _paper_reporting_trades(
        scoped_trades,
        reporting_scope="official",
    )
    return {
        "account": account.model_dump(mode="json"),
        "research": _paper_account_scope_status(
            scoped_trades,
            scope="legacy",
            max_positions=account.max_positions,
            authenticated_trade_ids=authenticated_ids,
        ),
        "official": _paper_account_scope_status(
            scoped_trades,
            scope="official",
            max_positions=account.max_positions,
            authenticated_trade_ids=authenticated_ids,
        ),
        "current_model": _paper_current_model_status(
            repo,
            scoped_trades,
            provider=mode,
            as_of_completed_session=completed_session,
        ),
        "observation": _paper_observation_status(
            account,
            as_of_completed_session=completed_session,
        ),
        "manual": {
            "count": len(repo.list_positions()),
            "uses_paper_capacity": False,
        },
        "data_health": {
            **_paper_account_data_health(account),
            **authentication_health,
            "paper_provider_filter": mode or "all",
            "paper_account_status_trade_limit": "1000",
            "paper_account_status_truncated": str(len(trades) >= 1000).lower(),
            "paper_account_active_trade_limit": "5000_per_status",
            "manual_positions_are_separate": "true",
        },
    }


@router.get(
    "/paper-trades/execution-audit",
    response_model=PaperExecutionRuleAudit,
)
def paper_trade_execution_audit(provider: str | None = None) -> PaperExecutionRuleAudit:
    mode = provider.strip().lower() if provider else None
    paper_repo = _paper_repo()
    return build_paper_execution_rule_audit(
        paper_repo.list_trades(limit=5000, provider=mode),
        paper_repo.get_account_settings(),
    )


@router.post("/paper-trades/seed")
def seed_paper_trades(provider: str = "fixture", limit: int = 50) -> dict[str, object]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    mode = provider.strip().lower()
    repo = _repo()
    paper_repo = _paper_repo()
    allow_seed, risk_gate_health = _paper_seed_risk_gate(repo, paper_repo, mode)
    snapshots, seed_health = _paper_seed_snapshots_from_recommendations(
        repo,
        mode=mode,
        include_etfs=True,
        max_age=timedelta(days=7),
        limit=limit,
    )
    risk_gate_health = _paper_merge_market_risk_gate(risk_gate_health, seed_health)
    if not allow_seed and risk_gate_health.get("paper_risk_gate_action") != "capacity_full":
        snapshots = []
    snapshots, _ = _paper_market_probe_snapshots(
        paper_repo,
        snapshots,
        provider=mode,
        risk_gate_health=risk_gate_health,
        signal_date=_paper_market_probe_signal_date(snapshots, provider=mode),
    )
    recently_released = _paper_recently_released_instruments(
        paper_repo.list_trades(limit=1000, provider=mode)
    )
    snapshots = [
        snapshot for snapshot in snapshots if snapshot.instrument_id not in recently_released
    ]
    account = paper_repo.get_account_settings()
    effective_limit = _paper_seed_limit_from_risk_gate(limit, risk_gate_health)
    throttled = risk_gate_health.get("paper_risk_gate_action") == "throttle_new_entries"
    tracking_signal_date = _paper_market_probe_signal_date(
        snapshots,
        provider=mode,
    )
    result = seed_paper_trades_from_snapshots(
        paper_repo,
        snapshots,
        provider=mode,
        max_created=effective_limit,
        max_active_trades=account.max_positions,
        max_signal_age_days=None,
        signal_date_override=tracking_signal_date if mode != "fixture" else None,
        notes=(
            "防守行情研究仓位；合格候选可按剩余仓位批量进入，单笔为正常仓位的 35%。"
            if throttled
            else ""
        ),
        allocation_multiplier=Decimal(
            risk_gate_health.get("paper_risk_gate_position_size_multiplier", "1.0")
            if throttled
            else "1.0"
        ),
        admission_repo=repo,
    )
    return result.model_dump(mode="json")


@router.post("/paper-trades/update")
def update_paper_trade_status(provider: str = "fixture") -> dict[str, object]:
    mode = provider.strip().lower()
    try:
        result = update_paper_trades(
            _paper_repo(),
            provider=build_market_data_provider(mode),
            provider_mode=mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump(mode="json")


@router.get("/paper-trades/session")
def paper_trade_session(provider: str | None = None) -> dict[str, object]:
    repo = _paper_repo()
    account = repo.get_account_settings()
    mode = provider.strip().lower() if provider else None
    trades = repo.list_trades(limit=1000, provider=mode)
    _, authenticated_ids, authentication_health = _paper_reporting_trades(
        trades,
        reporting_scope="official",
    )
    return {
        "account": account.model_dump(mode="json"),
        "summary": summarize_paper_trades(
            trades,
            authenticated_trade_ids=authenticated_ids,
        ).model_dump(mode="json"),
        "data_health": {
            **_paper_account_data_health(account),
            **authentication_health,
            "paper_provider_filter": mode or "all",
        },
    }


@router.post("/paper-trades/session/start")
def start_paper_trade_session(request: PaperSessionStartRequest) -> dict[str, object]:
    repo = _paper_repo()
    initial_capital = _decimal_or_none(request.initial_capital) or Decimal("0")
    allocation_per_trade_pct = _decimal_or_none(request.allocation_per_trade_pct) or Decimal("0")
    transaction_cost_bps = _decimal_or_none(request.transaction_cost_bps) or Decimal("-1")
    slippage_bps = _decimal_or_none(request.slippage_bps) or Decimal("-1")
    take_profit_pct = _decimal_or_none(request.take_profit_pct) or Decimal("0")
    _validate_paper_account_inputs(
        initial_capital=initial_capital,
        allocation_per_trade_pct=allocation_per_trade_pct,
        max_positions=request.max_positions,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        take_profit_pct=take_profit_pct,
    )
    cleared = repo.clear_trades() if request.reset_existing else 0
    account = repo.start_account_session(
        label=request.label.strip() or "A股研究模拟盘",
        initial_capital=initial_capital,
        allocation_per_trade_pct=allocation_per_trade_pct,
        max_positions=request.max_positions,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        take_profit_pct=take_profit_pct,
    )
    retained_trades = repo.list_trades(limit=1000)
    _, authenticated_ids, authentication_health = _paper_reporting_trades(
        retained_trades,
        reporting_scope="official",
    )
    ledger = build_paper_ledger(
        retained_trades,
        initial_capital=account.initial_capital,
        allocation_per_trade_pct=account.allocation_per_trade_pct,
        max_positions=account.max_positions,
        transaction_cost_bps=account.transaction_cost_bps,
        slippage_bps=account.slippage_bps,
        take_profit_pct=account.take_profit_pct,
        authenticated_trade_ids=authenticated_ids,
    )
    ledger.data_health.update(
        {
            **_paper_account_data_health(account),
            **authentication_health,
        }
    )
    return {
        "account": account.model_dump(mode="json"),
        "cleared_trades": cleared,
        "ledger": ledger.model_dump(mode="json"),
    }


@router.get("/paper-trades/ledger")
def paper_trade_ledger(
    initial_capital: Decimal | None = None,
    allocation_per_trade_pct: Decimal | None = None,
    max_positions: int | None = None,
    transaction_cost_bps: Decimal | None = None,
    slippage_bps: Decimal | None = None,
    take_profit_pct: Decimal | None = None,
    limit: int = 500,
    provider: str | None = None,
    reporting_scope: str = "official",
) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    account = _paper_repo().get_account_settings()
    mode = provider.strip().lower() if provider else None
    scope = _paper_reporting_scope_value(reporting_scope)
    trades = _paper_repo().list_trades(limit=limit, provider=mode)
    _, authenticated_ids, authentication_health = _paper_reporting_trades(
        trades,
        reporting_scope=scope,
    )
    try:
        ledger = build_paper_ledger(
            trades,
            initial_capital=initial_capital or account.initial_capital,
            allocation_per_trade_pct=allocation_per_trade_pct or account.allocation_per_trade_pct,
            max_positions=max_positions or account.max_positions,
            transaction_cost_bps=transaction_cost_bps or account.transaction_cost_bps,
            slippage_bps=slippage_bps or account.slippage_bps,
            take_profit_pct=take_profit_pct or account.take_profit_pct,
            reporting_scope=scope,
            authenticated_trade_ids=authenticated_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ledger.data_health.update(
        {
            **_paper_account_data_health(account),
            **authentication_health,
            "paper_provider_filter": mode or "all",
        }
    )
    return ledger.model_dump(mode="json")


@router.post("/paper-trades/research-baseline/freeze")
def freeze_paper_research_baseline(
    provider: str = "free",
    limit: int = 1000,
) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    mode = provider.strip().lower()
    paper_repo = _paper_repo()
    account = paper_repo.get_account_settings()
    existing = paper_repo.get_research_baseline(
        provider=mode,
        paper_session_id=account.session_id,
    )
    if existing is not None:
        return existing.model_dump(mode="json")
    runs = [
        run
        for run in _repo().list_walk_forward_runs(provider=mode, limit=20)
        if run.status == "succeeded"
    ]
    if not runs:
        raise HTTPException(
            status_code=409,
            detail="a completed walk-forward run is required before freezing the baseline",
        )
    trades = paper_repo.list_trades(limit=limit, provider=mode)
    start_date, definition = build_paper_research_baseline_definition(
        account=account,
        walk_forward_run=runs[0],
        trades=trades,
    )
    try:
        baseline = paper_repo.freeze_research_baseline(
            baseline_id=f"paper-research-{account.session_id}",
            provider=mode,
            paper_session_id=account.session_id,
            walk_forward_run_id=runs[0].run_id,
            start_date=start_date,
            definition=definition,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return baseline.model_dump(mode="json")


@router.get("/paper-trades/forward-comparison")
def paper_trade_forward_comparison(
    provider: str = "free",
    limit: int = 1000,
) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    mode = provider.strip().lower()
    paper_repo = _paper_repo()
    account = paper_repo.get_account_settings()
    baseline = paper_repo.get_research_baseline(
        provider=mode,
        paper_session_id=account.session_id,
    )
    if baseline is None:
        raise HTTPException(
            status_code=404,
            detail="paper research baseline has not been frozen for the active session",
        )
    trades = paper_repo.list_trades(limit=limit, provider=mode)
    ledger = build_paper_ledger(
        trades,
        initial_capital=account.initial_capital,
        allocation_per_trade_pct=account.allocation_per_trade_pct,
        max_positions=account.max_positions,
        transaction_cost_bps=account.transaction_cost_bps,
        slippage_bps=account.slippage_bps,
        take_profit_pct=account.take_profit_pct,
        reporting_scope="legacy",
    )
    validation = build_paper_validation(trades, ledger)
    report_date = _paper_report_date(trades)
    completed_session = _latest_completed_a_share_session()
    benchmark_bars = _market_cache_repo().load_daily_bars(
        mode,
        benchmark_ids() + benchmark_proxy_ids(),
        baseline.start_date,
        report_date,
    )
    cached_dates = (
        {
            value.date() if isinstance(value, datetime) else value
            for value in benchmark_bars["trade_date"].tolist()
            if isinstance(value, date)
        }
        if not benchmark_bars.empty
        else set()
    )
    market_sessions, unexpected_cached_dates = _paper_forward_calendar(
        start_date=baseline.start_date,
        report_date=report_date,
        completed_session=completed_session,
        cached_dates=cached_dates,
    )
    contexts = {
        trade.trade_id: context
        for trade in trades
        if (
            context := paper_repo.get_trade_source_context(trade.source_snapshot_id)
        )
        is not None
    }
    report = build_paper_forward_comparison(
        baseline=baseline,
        ledger=ledger,
        validation=validation,
        trades=trades,
        market_sessions=market_sessions,
        market_calendar_source="exchange_calendars:XSHG",
        source_contexts=contexts,
    )
    if unexpected_cached_dates:
        report.warnings.append(
            "行情缓存包含非交易所交易日日期，检查点已改用 XSHG 日历，异常缓存不会计入进度。"
        )
    report.data_health.update(
        {
            "paper_forward_provider": mode,
            "paper_forward_reporting_scope": "legacy_research_only",
            "paper_forward_source_contexts": str(len(contexts)),
            "paper_forward_official_metrics_excluded": "true",
            "paper_forward_completed_session": (
                completed_session.isoformat() if completed_session is not None else ""
            ),
            "paper_forward_cache_non_session_dates": str(
                len(unexpected_cached_dates)
            ),
            "paper_forward_cache_non_session_date_samples": ",".join(
                value.isoformat() for value in unexpected_cached_dates[:5]
            ),
        }
    )
    return report.model_dump(mode="json")


@router.get("/paper-trades/validation")
def paper_trade_validation(
    limit: int = 500,
    provider: str | None = None,
    reporting_scope: str = "official",
) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    mode = provider.strip().lower() if provider else None
    return _paper_validation_payload(
        limit=limit,
        provider=mode,
        reporting_scope=_paper_reporting_scope_value(reporting_scope),
    )


@router.post("/paper-trades/validation/run")
def run_paper_trade_validation(
    provider: str = "fixture",
    limit: int = 500,
    reporting_scope: str = "official",
) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    mode = provider.strip().lower()
    try:
        update_result = update_paper_trades(
            _paper_repo(),
            provider=build_market_data_provider(mode),
            provider_mode=mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = _paper_validation_payload(
        limit=limit,
        provider=mode,
        reporting_scope=_paper_reporting_scope_value(reporting_scope),
    )
    payload["data_health"].update(
        {
            **update_result.data_health,
            "validation_refreshed": "true",
            "validation_provider": mode,
        }
    )
    return payload


def _paper_validation_payload(
    limit: int = 500,
    provider: str | None = None,
    reporting_scope: str = "official",
) -> dict[str, object]:
    repo = _paper_repo()
    account = repo.get_account_settings()
    trades = repo.list_trades(limit=limit, provider=provider)
    scope = _paper_reporting_scope_value(reporting_scope)
    _, authenticated_ids, authentication_health = _paper_reporting_trades(
        trades,
        reporting_scope=scope,
    )
    ledger = build_paper_ledger(
        trades,
        initial_capital=account.initial_capital,
        allocation_per_trade_pct=account.allocation_per_trade_pct,
        max_positions=account.max_positions,
        transaction_cost_bps=account.transaction_cost_bps,
        slippage_bps=account.slippage_bps,
        take_profit_pct=account.take_profit_pct,
        reporting_scope=scope,
        authenticated_trade_ids=authenticated_ids,
    )
    ledger.data_health.update(
        {
            **_paper_account_data_health(account),
            **authentication_health,
            "paper_provider_filter": provider or "all",
        }
    )
    validation = build_paper_validation(trades, ledger)
    validation.data_health["paper_provider_filter"] = provider or "all"
    return validation.model_dump(mode="json")


def _paper_report_date(trades) -> date:
    dates = [
        value
        for trade in trades
        for value in (trade.latest_date, trade.exit_date, trade.entry_date, trade.signal_date)
        if value is not None
    ]
    return max(dates) if dates else _a_share_today()


def _paper_forward_calendar(
    *,
    start_date: date,
    report_date: date,
    completed_session: date | None,
    cached_dates: set[date],
) -> tuple[list[date], list[date]]:
    calendar_end = min(report_date, completed_session or report_date)
    sessions = trading_sessions_in_range(start_date, calendar_end)
    valid_cached_range = set(trading_sessions_in_range(start_date, report_date))
    return sessions, sorted(cached_dates - valid_cached_range)


@router.get("/paper-trades/daily-report")
def paper_trade_daily_report(
    provider: str = "fixture",
    limit: int = 500,
    reporting_scope: str = "official",
) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    mode = provider.strip().lower()
    scope = _paper_reporting_scope_value(reporting_scope)
    repo = _paper_repo()
    account = repo.get_account_settings()
    trades = repo.list_trades(limit=limit, provider=mode)
    reporting_trades, authenticated_ids, authentication_health = _paper_reporting_trades(
        trades,
        reporting_scope=scope,
    )
    asset_type_by_instrument = _paper_asset_types_for_trades(reporting_trades)
    source_context_by_trade = {
        trade.trade_id: context
        for trade in reporting_trades
        if (context := repo.get_trade_source_context(trade.source_snapshot_id)) is not None
    }
    ledger = build_paper_ledger(
        trades,
        initial_capital=account.initial_capital,
        allocation_per_trade_pct=account.allocation_per_trade_pct,
        max_positions=account.max_positions,
        transaction_cost_bps=account.transaction_cost_bps,
        slippage_bps=account.slippage_bps,
        take_profit_pct=account.take_profit_pct,
        reporting_scope=scope,
        authenticated_trade_ids=authenticated_ids,
    )
    ledger.data_health.update(
        {
            **_paper_account_data_health(account),
            **authentication_health,
            "paper_provider_filter": mode,
        }
    )
    validation = build_paper_validation(trades, ledger)
    report_date = _paper_report_date(reporting_trades)
    benchmark_items: list[dict[str, object]] = []
    benchmark_rows = 0
    if reporting_trades:
        cached_benchmark_ids = benchmark_ids() + benchmark_proxy_ids()
        benchmark_start = min(
            min(trade.signal_date for trade in reporting_trades),
            report_date - timedelta(days=180),
        )
        benchmark_bars = _market_cache_repo().load_daily_bars(
            mode,
            cached_benchmark_ids,
            benchmark_start,
            report_date,
        )
        benchmark_rows = len(benchmark_bars)
        benchmark_items = benchmark_items_for_return_from_bars(
            benchmark_bars=benchmark_bars,
            base_return_pct=ledger.summary.total_return_pct,
        )
    report = build_paper_daily_report(
        trades=trades,
        ledger=ledger,
        validation=validation,
        as_of=report_date,
        benchmark_items=benchmark_items,
        asset_type_by_instrument=asset_type_by_instrument,
        source_context_by_trade=source_context_by_trade,
    )
    _, risk_gate_health = _paper_seed_risk_gate(_repo(), repo, mode)
    market_gate_health = _paper_market_entry_gate_from_latest_cache(
        _repo(),
        mode=mode,
    )
    risk_gate_health = _paper_merge_market_risk_gate(
        risk_gate_health,
        market_gate_health,
    )
    _paper_apply_risk_gate_health_to_report(report, risk_gate_health)
    report.data_health.update(
        {
            "paper_daily_benchmarks_source": "market_cache_only",
            "paper_daily_benchmark_rows": str(benchmark_rows),
            "paper_daily_source_contexts": str(len(source_context_by_trade)),
            **risk_gate_health,
        }
    )
    return report.model_dump(mode="json")


@router.get("/paper-trades/dual-track")
def paper_trade_dual_track(
    provider: str = "free",
    days: int = 180,
    top_n: int = 5,
) -> dict[str, object]:
    if days <= 0 or days > 730:
        raise HTTPException(status_code=400, detail="days must be between 1 and 730")
    if top_n <= 0 or top_n > 20:
        raise HTTPException(status_code=400, detail="top_n must be between 1 and 20")
    mode = provider.strip().lower()
    as_of = _a_share_today()
    snapshot_start = as_of - timedelta(days=days)
    snapshots = _repo().list_top_daily_opportunity_snapshots(
        start=snapshot_start,
        end=as_of,
        top_n=top_n,
        provider=mode,
    )
    selected = select_daily_top_recommendations(snapshots, top_n=top_n, as_of=as_of)
    trades = _paper_repo().list_trades(limit=1000, provider=mode)
    account = _paper_repo().get_account_settings()
    start = min(
        (snapshot.signal_date for snapshot in selected if snapshot.signal_date is not None),
        default=snapshot_start,
    ) - timedelta(days=7)
    selected_ids = sorted({snapshot.instrument_id for snapshot in selected})
    cached_benchmark_ids = benchmark_ids() + benchmark_proxy_ids()
    cached = _market_cache_repo().load_daily_bars(
        mode,
        sorted(set(selected_ids + cached_benchmark_ids)),
        start,
        as_of,
    )
    if cached.empty:
        instrument_bars = cached
        benchmark_bars = cached
    else:
        instrument_bars = cached.loc[cached["instrument_id"].isin(selected_ids)].copy()
        benchmark_bars = cached.loc[cached["instrument_id"].isin(cached_benchmark_ids)].copy()
    report = build_dual_track_report(
        snapshots=selected,
        trades=trades,
        instrument_bars=instrument_bars,
        benchmark_bars=benchmark_bars,
        as_of=as_of,
        top_n=top_n,
        transaction_cost_bps=float(account.transaction_cost_bps),
        slippage_bps=float(account.slippage_bps),
    )
    adjusted_rows = 0
    if not instrument_bars.empty and "adjusted_close" in instrument_bars.columns:
        adjusted_rows = int(instrument_bars["adjusted_close"].notna().sum())
    report.data_health.update(
        {
            "dual_track_provider": mode,
            "dual_track_snapshot_rows": str(len(snapshots)),
            "dual_track_snapshot_start": snapshot_start.isoformat(),
            "dual_track_cache_source": "sqlite_only",
            "dual_track_instruments": str(len(selected_ids)),
            "dual_track_adjusted_rows": str(adjusted_rows),
        }
    )
    return report.model_dump(mode="json")


@router.get("/paper-trades/candidate-pool")
def paper_trade_candidate_pool(
    provider: str = "free",
    include_etfs: bool = True,
    limit: int = 30,
) -> dict[str, object]:
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    mode = provider.strip().lower()
    repo = _repo()
    paper_repo = _paper_repo()
    _, risk_gate_health = _paper_seed_risk_gate(repo, paper_repo, mode)
    snapshots, seed_health = _paper_seed_snapshots_from_recommendations(
        repo,
        mode=mode,
        include_etfs=include_etfs,
        max_age=timedelta(days=7),
        limit=max(limit, _paper_candidate_pool_limit(1)),
        include_market_blocked=True,
    )
    candidate_gate_health = _paper_merge_market_risk_gate(risk_gate_health, seed_health)
    expected_signal_date = (
        _latest_completed_a_share_session() if mode == "free" else None
    )
    if expected_signal_date is not None:
        signal_date_mismatch = sum(
            1
            for snapshot in snapshots
            if snapshot.signal_date != expected_signal_date
        )
        candidate_gate_health.update(
            {
                "paper_candidate_freshness_gate": (
                    "filtered" if signal_date_mismatch else "fresh"
                ),
                "paper_candidate_expected_signal_date": (
                    expected_signal_date.isoformat()
                ),
                "paper_candidate_signal_date_mismatch": str(signal_date_mismatch),
            }
        )
    _, market_probe_health = _paper_market_probe_snapshots(
        paper_repo,
        snapshots,
        provider=mode,
        risk_gate_health=candidate_gate_health,
        signal_date=_paper_market_probe_signal_date(snapshots, provider=mode),
    )
    candidate_gate_health.update(market_probe_health)
    items, summary = _paper_candidate_pool_snapshot_items(
        paper_repo=paper_repo,
        snapshots=snapshots,
        provider=mode,
        risk_gate_health=candidate_gate_health,
        limit=limit,
    )
    data_health = {
        **candidate_gate_health,
        **_paper_candidate_pool_health(
            paper_repo=paper_repo,
            snapshots=snapshots,
            provider=mode,
            risk_gate_health=candidate_gate_health,
        ),
        "paper_candidate_pool_endpoint": "true",
        "paper_candidate_pool_limit": str(limit),
    }
    return {
        "items": items,
        "summary": summary,
        "data_health": data_health,
    }


@router.get("/etf-exposures")
def etf_exposures(
    instrument_ids: str,
    limit: int = 16,
) -> dict[str, object]:
    if limit <= 0 or limit > 24:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 24")
    requested = list(
        dict.fromkeys(
            value.strip().upper()
            for value in instrument_ids.split(",")
            if value.strip()
        )
    )
    if not requested:
        raise HTTPException(status_code=400, detail="instrument_ids must not be empty")
    if len(requested) > limit:
        raise HTTPException(status_code=400, detail=f"instrument_ids exceeds limit {limit}")
    catalog = _repo().list_tradable_instruments(asset_types={"etf"}, limit=20_000)
    names = {
        item.instrument_id: item.name
        for item in catalog
        if item.instrument_id in requested
    }
    instruments = [
        (instrument_id, names[instrument_id])
        for instrument_id in requested
        if instrument_id in names
    ]
    response = _etf_exposure_service.build_response(instruments)
    response.data_health.update(
        {
            "etf_exposure_requested": str(len(requested)),
            "etf_exposure_catalog_matched": str(len(instruments)),
            "etf_exposure_catalog_missing": str(len(requested) - len(instruments)),
            "etf_exposure_cache": "local_disk",
        }
    )
    return response.model_dump(mode="json")


@router.get("/paper-trades/look-through-risk")
def paper_trade_look_through_risk(
    provider: str | None = None,
    reporting_scope: str = "official",
    limit: int = 500,
) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    mode = provider.strip().lower() if provider else None
    scope = _paper_reporting_scope_value(reporting_scope)
    paper_repo = _paper_repo()
    account = paper_repo.get_account_settings()
    trades = paper_repo.list_trades(limit=limit, provider=mode)
    _, authenticated_ids, authentication_health = _paper_reporting_trades(
        trades,
        reporting_scope=scope,
    )
    ledger = build_paper_ledger(
        trades,
        initial_capital=account.initial_capital,
        allocation_per_trade_pct=account.allocation_per_trade_pct,
        max_positions=account.max_positions,
        transaction_cost_bps=account.transaction_cost_bps,
        slippage_bps=account.slippage_bps,
        take_profit_pct=account.take_profit_pct,
        reporting_scope=scope,
        authenticated_trade_ids=authenticated_ids,
    )
    trade_by_id = {trade.trade_id: trade for trade in trades}
    position_ids = {position.instrument_id for position in ledger.positions}
    catalog = {
        item.instrument_id: item
        for item in _repo().list_tradable_instruments(limit=20_000)
        if item.instrument_id in position_ids
    }
    holdings: list[PortfolioLookThroughHolding] = []
    etf_instruments: list[tuple[str, str]] = []
    for position in ledger.positions:
        trade = trade_by_id.get(position.trade_id)
        instrument = catalog.get(position.instrument_id)
        context = (
            paper_repo.get_trade_source_context(trade.source_snapshot_id)
            if trade is not None
            else None
        )
        context_card = getattr(context, "card", None) if context is not None else None
        card = context_card if isinstance(context_card, dict) else {}
        card_asset_type = str(card.get("asset_type") or "").strip().lower()
        asset_type = str(
            getattr(instrument, "asset_type", "") or card_asset_type or "unknown"
        ).strip().lower()
        label = str(
            getattr(instrument, "label", "")
            or card.get("instrument_label")
            or position.instrument_id
        )
        market_context = card.get("market_context")
        market_context = market_context if isinstance(market_context, dict) else {}
        exposure_group = _paper_card_exposure_group(
            card,
            current_industry=(
                market_context.get("industry")
                or card.get("industry")
                or card.get("sector")
            ),
            instrument_id=position.instrument_id,
        )
        holdings.append(
            PortfolioLookThroughHolding(
                trade_id=position.trade_id,
                instrument_id=position.instrument_id,
                instrument_label=label,
                asset_type=asset_type,
                weight_pct=position.weight_pct,
                exposure_group=exposure_group,
            )
        )
        if asset_type == "etf":
            etf_instruments.append(
                (
                    position.instrument_id,
                    str(getattr(instrument, "name", "") or label),
                )
            )

    etf_response = _etf_exposure_service.build_response(etf_instruments)
    response = build_portfolio_lookthrough_risk(
        holdings,
        etf_response.profiles,
        etf_response.overlaps,
    )
    response.data_health.update(
        {
            **authentication_health,
            **etf_response.data_health,
            "paper_provider_filter": mode or "all",
            "paper_reporting_scope": scope,
            "portfolio_lookthrough_catalog_matched": str(len(catalog)),
            "portfolio_lookthrough_position_count": str(len(holdings)),
        }
    )
    return response.model_dump(mode="json")


def _paper_asset_types_for_trades(trades) -> dict[str, str]:
    instrument_ids = {trade.instrument_id for trade in trades}
    if not instrument_ids:
        return {}
    instruments = _repo().list_tradable_instruments(limit=20_000)
    return {
        instrument.instrument_id: instrument.asset_type
        for instrument in instruments
        if instrument.instrument_id in instrument_ids
    }


@router.get("/paper-trades/{trade_id}/events")
def paper_trade_events(trade_id: str) -> dict[str, object]:
    repo = _paper_repo()
    trade = repo.get_trade(trade_id)
    events = repo.list_trade_events(trade_id)
    if trade is None and not events:
        raise HTTPException(status_code=404, detail="paper trade not found")
    return {
        "trade_id": trade_id,
        "instrument_id": trade.instrument_id if trade is not None else events[-1].instrument_id,
        "status": trade.status if trade is not None else events[-1].to_status,
        "events": [event.model_dump(mode="json") for event in events],
        "data_health": {
            "paper_event_ledger": "append_only",
            "paper_event_count": str(len(events)),
        },
    }


@router.delete("/paper-trades/{trade_id}")
def delete_paper_trade(trade_id: str) -> dict[str, object]:
    deleted = _paper_repo().delete_trade(trade_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="paper trade not found")
    return {"deleted": True, "trade_id": trade_id}


@router.post("/paper-trades/from-opportunity")
def create_paper_trade_from_opportunity(
    request: PaperTradeFromOpportunityRequest,
) -> dict[str, object]:
    mode = request.provider.strip().lower()
    card_id = request.card_id.strip()
    if not card_id:
        raise HTTPException(status_code=400, detail="card_id is required")
    opportunity_repo = _repo()
    snapshots = opportunity_repo.list_latest_opportunity_snapshots_by_card_ids(
        [card_id],
        provider=mode,
    )
    if not snapshots:
        raise HTTPException(
            status_code=404,
            detail="authoritative opportunity snapshot not found",
        )
    snapshot = snapshots[0]
    mismatch = _paper_request_snapshot_mismatch(request, snapshot)
    if mismatch is not None:
        raise HTTPException(status_code=400, detail=mismatch)
    governance_reason = _paper_snapshot_governance_block_reason(
        opportunity_repo,
        snapshot,
        provider=mode,
        admission_mode="manual",
    )
    if governance_reason is not None:
        raise HTTPException(status_code=400, detail=governance_reason)
    if snapshot.trigger_price is None:
        raise HTTPException(
            status_code=400,
            detail="authoritative opportunity has no trigger price",
        )
    admission = evaluate_paper_snapshot_admission(
        opportunity_repo,
        snapshot,
        provider=mode,
        mode="manual",
    )
    if not admission.eligible:
        raise HTTPException(status_code=400, detail=admission.reason)

    instrument_id = snapshot.instrument_id
    source_snapshot_id = snapshot.snapshot_id
    repo = _paper_repo()
    existing = repo.get_trade_by_source_snapshot_id(source_snapshot_id)
    if existing is not None:
        return {
            "created": False,
            "trade": existing.model_dump(mode="json"),
            "message": "already_tracking",
        }

    trades = repo.list_trades(limit=1000, provider=mode)
    active_same_instrument = next(
        (
            trade
            for trade in trades
            if trade.instrument_id == instrument_id and trade.status in {"pending", "open"}
        ),
        None,
    )
    if active_same_instrument is not None:
        return {
            "created": False,
            "trade": active_same_instrument.model_dump(mode="json"),
            "message": "already_tracking_instrument",
        }
    invalidated_same_instrument = next(
        (
            trade
            for trade in trades
            if trade.instrument_id == instrument_id
            and trade.status == "invalidated"
            and _paper_trade_recently_released(trade)
        ),
        None,
    )
    if invalidated_same_instrument is not None:
        raise HTTPException(
            status_code=400,
            detail="instrument price data was invalidated; wait for a corrected snapshot",
        )
    active_count = sum(1 for trade in trades if trade.status in {"pending", "open"})
    account = repo.get_account_settings()
    if active_count >= account.max_positions:
        raise HTTPException(
            status_code=409,
            detail=f"paper portfolio is full ({active_count}/{account.max_positions})",
        )

    trade = repo.create_trade(
        source_snapshot_id=source_snapshot_id,
        provider=mode,
        instrument_id=instrument_id,
        strategy_id=snapshot.primary_strategy_id,
        signal_date=snapshot.signal_date
        or (_a_share_today() if instrument_id.startswith("CN:") else date.today()),
        trigger_price=snapshot.trigger_price,
        initial_stop=snapshot.initial_stop,
        target_1=snapshot.target_1,
        rank_score=snapshot.rank_score,
        notes=("从服务端权威机会快照加入模拟跟踪；等待触发价确认后才视为开仓。"),
        admission_proof=PaperTradeAdmissionProof(
            admission_source=admission.admission_source,
            production_identity_digest=admission.production_identity_digest,
            production_batch_fact_digest=admission.production_batch_fact_digest,
            production_selection_item_digest=(
                admission.production_selection_item_digest
            ),
            release_proof_digest=admission.release_proof_digest,
        ),
    )
    return {
        "created": True,
        "trade": trade.model_dump(mode="json"),
        "message": "tracking_created",
    }


def _paper_request_snapshot_mismatch(
    request: PaperTradeFromOpportunityRequest,
    snapshot: OpportunitySnapshotRecord,
) -> str | None:
    if request.instrument_id.strip() != snapshot.instrument_id:
        return "instrument_id does not match the authoritative opportunity"
    if request.strategy_id and request.strategy_id.strip() != (snapshot.primary_strategy_id or ""):
        return "strategy_id does not match the authoritative opportunity"
    numeric_fields = (
        ("trigger_price", request.trigger_price, snapshot.trigger_price),
        ("initial_stop", request.initial_stop, snapshot.initial_stop),
        ("target_1", request.target_1, snapshot.target_1),
        ("rank_score", request.rank_score, snapshot.rank_score),
    )
    for field, requested, authoritative in numeric_fields:
        if requested is None:
            continue
        if _decimal_or_none(requested) != authoritative:
            return f"{field} does not match the authoritative opportunity"
    return None


def _decimal_or_none(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    return Decimal(str(value))


def _validate_paper_account_inputs(
    *,
    initial_capital: Decimal,
    allocation_per_trade_pct: Decimal,
    max_positions: int,
    transaction_cost_bps: Decimal,
    slippage_bps: Decimal,
    take_profit_pct: Decimal,
) -> None:
    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be greater than zero")
    if allocation_per_trade_pct <= 0 or allocation_per_trade_pct > 100:
        raise HTTPException(
            status_code=400,
            detail="allocation_per_trade_pct must be between 0 and 100",
        )
    if max_positions <= 0:
        raise HTTPException(status_code=400, detail="max_positions must be greater than zero")
    if transaction_cost_bps < 0 or slippage_bps < 0:
        raise HTTPException(
            status_code=400,
            detail="transaction_cost_bps and slippage_bps must be non-negative",
        )
    if take_profit_pct <= 0 or take_profit_pct > 100:
        raise HTTPException(status_code=400, detail="take_profit_pct must be between 0 and 100")


def _paper_account_data_health(account: PaperAccountSettings) -> dict[str, str]:
    return {
        "paper_session_id": account.session_id,
        "paper_session_status": account.status,
        "paper_session_label": account.label,
        "paper_initial_capital": str(account.initial_capital),
        "paper_allocation_per_trade_pct": str(account.allocation_per_trade_pct),
        "paper_max_positions": str(account.max_positions),
        "paper_transaction_cost_bps": str(account.transaction_cost_bps),
        "paper_slippage_bps": str(account.slippage_bps),
        "paper_take_profit_pct": str(account.take_profit_pct),
    }


def _build_daily_brief_response(
    provider: str,
    symbols: str | None,
    limit: int,
    include_news: bool,
    skip_backtest: bool,
    scan_limit: int | None,
    fast: bool = False,
):
    mode = provider.strip().lower()
    if limit <= 0 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    if scan_limit is None and fast:
        scan_limit = 80 if mode == "free" else None
    if fast:
        cached = _cached_daily_scan_for_brief(mode)
        if cached is not None:
            scan_result, cached_symbols, cached_health = cached
            invalidated = _paper_recent_invalidated_instruments(mode)
            if invalidated:
                original_count = len(scan_result.cards)
                scan_result.cards = [
                    card for card in scan_result.cards if card.instrument_id not in invalidated
                ]
                cached_health["paper_invalidated_cards_filtered"] = str(
                    original_count - len(scan_result.cards)
                )
            brief_health = {
                "brief_provider": mode,
                "brief_symbols": str(len(cached_symbols)),
                "brief_requested_symbols": symbols or "",
                "brief_news": "skipped",
                "brief_mode": "fast",
                "brief_scan_limit": "cache",
                "brief_backtest": "skipped",
                "brief_skip_backtest": "true",
                **cached_health,
            }
            return build_daily_brief(
                provider=mode,
                symbols=cached_symbols,
                scan_result=scan_result,
                backtest_result=None,
                catalyst_hypotheses=[],
                position_risks=[],
                provider_statuses=build_provider_status(),
                limit=limit,
                data_health=brief_health,
            )

    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=not fast,
    )
    instrument_ids = resolved.symbols
    start_date, end_date = _backtest_dates(mode, None, None)
    try:
        market_provider = build_market_data_provider(mode)
        scan_result = run_daily_scan(
            instrument_ids,
            market_provider,
            mode=mode,
            strategy_data_provider=EmptyStrategyDataProvider() if resolved.is_dynamic else None,
            **_scan_policy_kwargs(mode),
        )
        if skip_backtest:
            backtest_result = None
        else:
            backtest_result = run_historical_backtest(
                instrument_ids=instrument_ids,
                provider=market_provider,
                start=start_date,
                end=end_date,
                step_days=5,
                max_signals=100,
            )
        position_risks = _position_risks(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    catalyst_hypotheses = []
    brief_health = {
        "brief_provider": mode,
        "brief_symbols": str(len(instrument_ids)),
        "brief_news": "skipped",
        "brief_mode": "fast" if fast else "full",
        "brief_scan_limit": str(scan_limit) if scan_limit else "default",
        "brief_backtest": "skipped" if skip_backtest else "run",
        "brief_skip_backtest": str(skip_backtest).lower(),
    }
    brief_health.update(resolved.data_health)
    if resolved.is_dynamic:
        brief_health["strategy_data_skipped"] = "true"
    if include_news:
        news_symbols = [card.instrument_id for card in scan_result.cards[:limit]] or instrument_ids[
            :limit
        ]
        catalyst_provider = FreeCatalystProvider()
        news = catalyst_provider.get_news(news_symbols, limit=limit)
        catalyst_hypotheses = build_catalyst_hypotheses(news)
        brief_health["brief_news"] = str(len(news))
        brief_health["brief_news_symbols"] = str(len(news_symbols))
        brief_health["brief_catalysts"] = str(len(catalyst_hypotheses))
        if catalyst_provider.last_errors:
            brief_health["brief_news_errors"] = " | ".join(catalyst_provider.last_errors[:3])

    brief = build_daily_brief(
        provider=mode,
        symbols=instrument_ids,
        scan_result=scan_result,
        backtest_result=backtest_result,
        catalyst_hypotheses=catalyst_hypotheses,
        position_risks=position_risks,
        provider_statuses=build_provider_status(),
        limit=limit,
        data_health=brief_health,
    )
    return brief


def _cached_daily_scan_for_brief(
    mode: str,
) -> tuple[DailyScanResult, list[str], dict[str, str]] | None:
    repo = _repo()
    cached = repo.get_latest_scan_result_cache_by_modes(
        provider=mode,
        modes={"full_market_scan", "today_scan_fallback", "full_market_batch"},
        max_age=timedelta(days=7),
    )
    if cached is None:
        return None
    payload = deepcopy(cached.payload)
    _hydrate_full_market_batch_payload(payload, repo, mode, cache_ttl_minutes=7 * 24 * 60)
    cards = payload.get("cards")
    if not isinstance(cards, list) or not cards:
        return None
    normalized = {
        "cards": cards,
        "items": payload.get("items") if isinstance(payload.get("items"), list) else [],
        "strategy_health": payload.get("strategy_health")
        if isinstance(payload.get("strategy_health"), list)
        else [],
        "factor_rankings": payload.get("factor_rankings")
        if isinstance(payload.get("factor_rankings"), list)
        else [],
        "sector_strength": payload.get("sector_strength")
        if isinstance(payload.get("sector_strength"), list)
        else [],
        "market_intelligence": payload.get("market_intelligence")
        if isinstance(payload.get("market_intelligence"), dict)
        else None,
        "manual_action_center": payload.get("manual_action_center")
        if isinstance(payload.get("manual_action_center"), dict)
        else None,
        "portfolio_plan": payload.get("portfolio_plan"),
        "data_health": payload.get("data_health")
        if isinstance(payload.get("data_health"), dict)
        else {},
    }
    if not isinstance(normalized["portfolio_plan"], dict):
        normalized["portfolio_plan"] = build_portfolio_plan(
            [OpportunityCard.model_validate(card) for card in cards if isinstance(card, dict)]
        ).model_dump(mode="json")
    scan_result = DailyScanResult.model_validate(normalized)
    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, list):
        raw_symbols = cached.symbols
    symbols = [str(symbol) for symbol in raw_symbols if str(symbol)]
    cache_health = {
        "brief_cache": "hit",
        "brief_cache_id": cached.cache_id,
        "brief_cache_mode": cached.mode,
        "brief_cache_created_at": cached.created_at.isoformat(),
    }
    return scan_result, symbols, cache_health


@router.get("/backtest")
def backtest(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 5,
    limit: int = 100,
    scan_limit: int | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if step_days <= 0 or step_days > 60:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 60")
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=scan_limit is None,
    )
    instrument_ids = resolved.symbols
    start_date, end_date = _backtest_dates(mode, start, end)
    try:
        market_provider = build_market_data_provider(mode)
        result = run_historical_backtest(
            instrument_ids=instrument_ids,
            provider=market_provider,
            start=start_date,
            end=end_date,
            step_days=step_days,
            max_signals=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "summary": result.summary.model_dump(mode="json"),
        "performance": [item.model_dump(mode="json") for item in result.performance],
        "signals": [_model_payload_with_label(item) for item in result.signals],
        "benchmark": result.benchmark.model_dump(mode="json"),
        "environment_breakdown": [
            item.model_dump(mode="json") for item in result.environment_breakdown
        ],
        "temporal_validation": result.temporal_validation.model_dump(mode="json"),
        "data_health": {**result.data_health, **resolved.data_health},
    }


@router.get("/parameter-sensitivity")
def parameter_sensitivity(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 5,
    limit: int = 150,
    scan_limit: int | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if step_days <= 0 or step_days > 60:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 60")
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=scan_limit is None,
    )
    instrument_ids = resolved.symbols
    start_date, end_date = _backtest_dates(mode, start, end)
    try:
        market_provider = build_market_data_provider(mode)
        backtest_result = run_historical_backtest(
            instrument_ids=instrument_ids,
            provider=market_provider,
            start=start_date,
            end=end_date,
            step_days=step_days,
            max_signals=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = build_parameter_sensitivity(backtest_result.signals)
    payload = result.model_dump(mode="json")
    payload["data_health"].update(
        {
            **backtest_result.data_health,
            **resolved.data_health,
        }
    )
    return payload


@router.get("/portfolio-backtest")
def portfolio_backtest(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 5,
    initial_capital: Decimal = Decimal("100000"),
    risk_per_trade_pct: Decimal = Decimal("1"),
    max_positions: int = 5,
    transaction_cost_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("5"),
    scan_limit: int | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if step_days <= 0 or step_days > 60:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 60")
    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be positive")
    if risk_per_trade_pct <= 0 or risk_per_trade_pct > 10:
        raise HTTPException(status_code=400, detail="risk_per_trade_pct must be between 0 and 10")
    if max_positions <= 0 or max_positions > 20:
        raise HTTPException(status_code=400, detail="max_positions must be between 1 and 20")

    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=scan_limit is None,
    )
    instrument_ids = resolved.symbols
    start_date, end_date = _backtest_dates(mode, start, end)
    try:
        market_provider = build_market_data_provider(mode)
        result = run_portfolio_backtest(
            instrument_ids=instrument_ids,
            provider=market_provider,
            start=start_date,
            end=end_date,
            step_days=step_days,
            initial_capital=initial_capital,
            risk_per_trade_pct=risk_per_trade_pct,
            max_positions=max_positions,
            transaction_cost_bps=transaction_cost_bps,
            slippage_bps=slippage_bps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "summary": result.summary.model_dump(mode="json"),
        "trades": [_model_payload_with_label(trade) for trade in result.trades],
        "equity_curve": [point.model_dump(mode="json") for point in result.equity_curve],
        "monthly_returns": [item.model_dump(mode="json") for item in result.monthly_returns],
        "data_health": {**result.data_health, **resolved.data_health},
    }


def _position_risks(provider: str):
    positions = _repo().list_positions()
    if not positions:
        return []
    market_provider = build_market_data_provider(provider)
    snapshot = market_provider.get_snapshot([position.instrument_id for position in positions])
    latest_prices = {
        row["instrument_id"]: Decimal(str(row["close"])) for _, row in snapshot.iterrows()
    }
    risks = []
    for position in positions:
        latest_price = latest_prices.get(position.instrument_id)
        if latest_price is None:
            continue
        risks.append(
            analyze_position_risk(
                PositionInput(**position.model_dump()),
                current_price=latest_price,
            )
        )
    return risks


@router.get("/alerts")
def alerts() -> dict[str, list[object]]:
    return {"alerts": []}


@router.get("/catalysts")
def catalysts(symbols: str | None = None, limit: int = 5) -> dict[str, object]:
    resolved = _resolve_symbols("free", symbols)
    instrument_ids = resolved.symbols[: max(limit, 1)]
    provider = FreeCatalystProvider()
    news = provider.get_news(instrument_ids, limit=limit)
    hypotheses = build_catalyst_hypotheses(news)
    data_health = {
        "provider": "free",
        "scanned": str(len(instrument_ids)),
        "news": str(len(news)),
        "hypotheses": str(len(hypotheses)),
    }
    data_health.update(resolved.data_health)
    if provider.last_errors:
        data_health["errors"] = " | ".join(provider.last_errors[:3])
    return {
        "news": [item.model_dump(mode="json") for item in news],
        "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
        "data_health": data_health,
    }


@router.get("/alert-rules")
def alert_rules() -> dict[str, list[object]]:
    return {"rules": [rule.model_dump(mode="json") for rule in _repo().list_alert_rules()]}


@router.post("/alert-rules")
def upsert_alert_rule(rule: AlertRuleCreate) -> dict[str, object]:
    saved = _repo().upsert_alert_rule(rule)
    return saved.model_dump(mode="json")


@router.get("/universes")
def universes() -> dict[str, list[object]]:
    repo = _repo()
    return {
        "universes": [
            universe.model_dump(mode="json")
            for universe in merge_universes(repo.list_custom_universes())
        ]
    }


@router.post("/universes")
def upsert_universe(universe: UniverseCreate) -> dict[str, object]:
    saved = _repo().upsert_universe(universe)
    return saved.model_dump(mode="json")


@router.get("/universes/{universe_id}")
def universe_detail(universe_id: str) -> dict[str, object]:
    repo = _repo()
    custom = repo.get_universe(universe_id)
    if custom is not None:
        return {"universe": custom.model_dump(mode="json")}
    builtin = next(
        (universe for universe in builtin_universes() if universe.universe_id == universe_id),
        None,
    )
    if builtin is None:
        raise HTTPException(status_code=404, detail="universe not found")
    return {"universe": builtin.model_dump(mode="json")}


@router.get("/instruments/search")
def instrument_search(q: str = "", limit: int = 50) -> dict[str, object]:
    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    catalog = search_cn_tradable_instruments(
        q,
        limit=limit,
        include_full_etfs=False,
        use_cache=True,
    )
    return {
        "items": [item.model_dump(mode="json") for item in catalog.items],
        "data_health": catalog.data_health,
    }


@router.post("/tradable-catalog/sync")
def sync_tradable_catalog(include_full_etfs: bool = True) -> dict[str, object]:
    result = sync_cn_tradable_catalog(repo=_repo(), include_full_etfs=include_full_etfs)
    return result.model_dump(mode="json")


@router.get("/tradable-catalog")
def tradable_catalog(
    q: str = "",
    asset_type: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    result = _repo().search_tradable_instruments(q, asset_type=asset_type, limit=limit)
    return result.model_dump(mode="json")


@router.get("/instruments/labels")
def instrument_labels(symbols: str = "") -> dict[str, object]:
    requested = _normalize_symbol_list(symbols)
    if requested:
        labels: dict[str, str] = {}
        for symbol in requested:
            if symbol in labels:
                continue
            labels[symbol] = format_instrument_label(symbol)
        return {
            "labels": labels,
            "data_health": {"requested": str(len(labels))},
        }

    # Return full tradable map in one shot for UI label hydration.
    instruments = _repo().list_tradable_instruments(limit=20_000)
    labels = {item.instrument_id: item.label for item in instruments}
    if not labels:
        from qagent.market.tradable import load_cn_tradable_instruments

        catalog = load_cn_tradable_instruments(use_cache=True)
        labels = {f"CN:{item.symbol}": item.label for item in catalog.items}
    return {
        "labels": labels,
        "data_health": {"requested": str(len(labels))},
    }


@router.post("/full-market/scan")
def full_market_scan(
    provider: str = "free",
    max_symbols: int = 300,
    include_etfs: bool = True,
    sync_if_empty: bool = True,
) -> dict[str, object]:
    return _full_market_scan_payload(provider, max_symbols, include_etfs, sync_if_empty)


@router.post("/full-market/batch-scan")
def start_full_market_batch_scan(
    provider: str = "free",
    batch_size: int = 200,
    max_symbols: int | None = None,
    include_etfs: bool = True,
    sync_if_empty: bool = True,
    force_restart: bool = False,
) -> dict[str, object]:
    mode = provider.strip().lower()
    _validate_full_market_batch_scan_params(batch_size, max_symbols)
    repo = _repo()
    latest = repo.get_latest_full_market_scan_job(provider=mode)
    latest = _reset_abandoned_full_market_job(repo, latest)
    if latest and latest.status in {"queued", "running"} and not force_restart:
        return _full_market_job_payload(latest)

    summary = repo.tradable_catalog_summary()
    if sync_if_empty and summary.total_count == 0:
        sync_cn_tradable_catalog(repo=repo, include_full_etfs=include_etfs)
    symbols = build_full_market_batch_symbols(
        repo=repo,
        include_etfs=include_etfs,
        max_symbols=max_symbols,
    )
    if not symbols:
        raise HTTPException(status_code=400, detail="tradable catalog is empty")
    job = repo.create_full_market_scan_job(
        provider=mode,
        symbols=symbols,
        batch_size=batch_size,
        include_etfs=include_etfs,
        sync_if_empty=sync_if_empty,
    )
    _submit_full_market_scan_job(job.job_id)
    return _full_market_job_payload(job)


def _normalize_symbol_list(raw: str) -> list[str]:
    if not raw.strip():
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in raw.split(","):
        normalized = value.strip().upper()
        if not normalized:
            continue
        if normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def _validate_historical_data_params(
    provider: str,
    start: date,
    end: date,
    max_symbols: int,
) -> str:
    mode = provider.strip().lower()
    if mode not in {"fixture", "free"}:
        raise HTTPException(status_code=400, detail="provider must be fixture or free")
    if start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    if max_symbols <= 0 or max_symbols > 10_000:
        raise HTTPException(status_code=400, detail="max_symbols must be between 1 and 10000")
    return mode


def _historical_data_symbols(
    provider: str,
    raw_symbols: str | None,
    *,
    max_symbols: int,
    include_etfs: bool,
) -> list[str]:
    if raw_symbols:
        parsed = _normalize_symbol_list(raw_symbols)
        resolved = (
            resolve_symbol_tokens(parsed) if provider == "free" else ResolvedSymbols(symbols=parsed)
        )
        symbols = resolved.symbols[:max_symbols]
    else:
        asset_types = {"stock", "etf"} if include_etfs else {"stock"}
        symbols = [
            item.instrument_id
            for item in _repo().list_tradable_instruments(
                asset_types=asset_types,
                limit=max_symbols,
            )
        ]
    if not symbols:
        raise HTTPException(status_code=400, detail="historical backfill symbol set is empty")
    non_cn = [symbol for symbol in symbols if not symbol.startswith("CN:")]
    if non_cn:
        raise HTTPException(
            status_code=400, detail="historical backfill currently supports A shares only"
        )
    return symbols


def _historical_backfill_job_payload(job) -> dict[str, object]:
    payload = job.model_dump(mode="json")
    if len(job.symbols) > 100:
        payload["symbols"] = job.symbols[:20]
        payload["symbols_truncated"] = True
    else:
        payload["symbols_truncated"] = False
    payload["progress"] = job.progress
    payload["phase"] = job.data_health.get("backfill_phase", job.status)
    return payload


@router.get("/full-market/batch-scan/latest")
def latest_full_market_batch_scan(provider: str = "free") -> dict[str, object]:
    repo = _repo()
    job = repo.get_latest_full_market_scan_job(provider=provider.strip().lower())
    if job is None:
        raise HTTPException(status_code=404, detail="full-market batch scan not found")
    job = _reset_abandoned_full_market_job(repo, job)
    return _full_market_job_payload(job)


@router.get("/full-market/batch-scan/latest-result")
def latest_full_market_batch_scan_result(
    provider: str = "free",
    include_etfs: bool = True,
    cache_ttl_minutes: int = 7 * 24 * 60,
    limit: int = 30,
) -> dict[str, object]:
    _validate_scan_cache_ttl(cache_ttl_minutes)
    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    mode = provider.strip().lower()
    repo = _repo()
    bind = repo.session_factory.kw.get("bind")
    database_identity = str(getattr(bind, "url", "unknown"))
    response_cache_key = (
        database_identity,
        mode,
        include_etfs,
        limit,
        cache_ttl_minutes,
    )
    with _latest_full_market_result_lock:
        now = monotonic()
        cached_response = _latest_full_market_result_cache.get(response_cache_key)
        if cached_response is not None and now - cached_response[0] <= (
            _LATEST_FULL_MARKET_RESULT_CACHE_TTL_SECONDS
        ):
            payload = deepcopy(cached_response[1])
            data_health = payload.setdefault("data_health", {})
            if isinstance(data_health, dict):
                data_health["full_market_response_cache"] = "hit"
            return payload

        cached = repo.get_recent_scan_result_cache(
            cache_key=full_market_batch_cache_key(provider, include_etfs),
            max_age=timedelta(minutes=cache_ttl_minutes),
        )
        if cached is None:
            raise HTTPException(status_code=404, detail="full-market batch result not found")
        payload = deepcopy(cached.payload)
        invalidated_filtered = _filter_recent_invalidated_payload_cards(
            payload,
            provider=mode,
        )
        _limit_full_market_batch_payload(payload, limit=limit)
        _hydrate_full_market_batch_payload(payload, repo, mode, cache_ttl_minutes)
        data_health = payload.setdefault("data_health", {})
        if isinstance(data_health, dict):
            data_health["scan_result_cache"] = "hit"
            data_health["scan_result_cache_id"] = cached.cache_id
            data_health["paper_invalidated_cards_filtered"] = str(invalidated_filtered)
            data_health["full_market_response_cache"] = "miss"
        _latest_full_market_result_cache.clear()
        _latest_full_market_result_cache[response_cache_key] = (
            monotonic(),
            deepcopy(payload),
        )
        return payload


def _limit_full_market_batch_payload(payload: dict[str, object], *, limit: int) -> None:
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
            _limit_full_market_data_health(
                intelligence_health,
                visible_card_ids,
                limit,
            )

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


def _reset_abandoned_full_market_job(repo: QagentRepository, job):
    if job is None or job.status not in {"queued", "running"}:
        return job
    updated_at = _as_utc_datetime(job.updated_at)
    if datetime.now(timezone.utc) - updated_at <= timedelta(hours=4):
        return job
    return repo.update_full_market_scan_job(
        job.job_id,
        status="failed",
        message="Stale full-market scan released after four hours without progress",
        data_health={
            **job.data_health,
            "full_market_stale_reset": "true",
            "full_market_stale_reset_at": datetime.now(timezone.utc).isoformat(),
        },
    )


@router.get("/full-market/batch-scan/{job_id}")
def full_market_batch_scan_job(job_id: str) -> dict[str, object]:
    job = _repo().get_full_market_scan_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="full-market batch scan not found")
    return _full_market_job_payload(job)


@router.post("/scan-tasks/today")
def start_today_scan_task(
    provider: str = "free",
    max_symbols: int = 80,
    include_etfs: bool = True,
    sync_if_empty: bool = True,
    force_refresh: bool = False,
    cache_ttl_minutes: int = 60,
) -> dict[str, object]:
    _validate_full_market_scan_params(max_symbols)
    _validate_scan_cache_ttl(cache_ttl_minutes)
    record = _task_manager.create(
        kind="today_scan",
        message=f"Queued today scan for up to {max_symbols} symbols",
    )
    if not force_refresh:
        cached_payload = _recent_full_market_scan_payload(
            provider=provider,
            max_symbols=max_symbols,
            include_etfs=include_etfs,
            sync_if_empty=sync_if_empty,
            cache_ttl_minutes=cache_ttl_minutes,
        )
        if cached_payload is not None:
            cached_payload["task"] = _task_payload(
                record.task_id,
                record.kind,
                provider,
                max_symbols,
                include_etfs,
                cache="hit",
            )
            _task_manager.mark_succeeded(
                record.task_id,
                cached_payload,
                message="Loaded recent SQLite scan snapshot",
            )
            cached_record = _task_manager.get(record.task_id)
            return (cached_record or record).model_dump(mode="json")

    def work() -> dict[str, object]:
        _task_manager.update(
            record.task_id,
            progress=15,
            message="Building tradable A-share universe",
        )
        payload = _full_market_scan_payload(provider, max_symbols, include_etfs, sync_if_empty)
        payload.setdefault("data_health", {})["scan_result_cache"] = (
            "force_refresh" if force_refresh else "miss"
        )
        payload["task"] = _task_payload(
            record.task_id,
            record.kind,
            provider,
            max_symbols,
            include_etfs,
            cache="refresh" if force_refresh else "miss",
        )
        return payload

    _task_executor.submit(_task_manager.run, record.task_id, work)
    return record.model_dump(mode="json")


@router.get("/scan-tasks")
def scan_tasks(limit: int = 20) -> dict[str, list[object]]:
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    tasks = [
        _enrich_scan_task_result(record.model_dump(mode="json"))
        for record in _task_manager.list(limit)
    ]
    return {"tasks": tasks}


@router.get("/scan-tasks/{task_id}")
def scan_task(task_id: str) -> dict[str, object]:
    record = _task_manager.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="scan task not found")
    return _enrich_scan_task_result(record.model_dump(mode="json"))


def _enrich_scan_task_result(payload: dict[str, object]) -> dict[str, object]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    _relabel_instrument_payload(result)
    _hydrate_legacy_opportunity_cards(result)
    _attach_rotation_radar_payload(result)
    _attach_signal_hub_payload(result)
    _attach_market_intelligence_payload(result)
    _attach_recommendation_quality_payload(result)
    _attach_probability_forecast_payload(result)
    if not _restore_governance_card_payload(result):
        _attach_card_briefs_and_cached_benchmarks(result, _payload_provider_mode(result))
    _attach_manual_action_center_payload(result)
    _attach_signal_monitor_payload(result)
    _attach_decision_quality_payload(result)
    _attach_operational_readiness_payload(result)
    _attach_alpha_quality_payload(result)
    _attach_research_center_payload(result)
    return payload


def _full_market_scan_payload(
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    sync_if_empty: bool,
) -> dict[str, object]:
    _validate_full_market_scan_params(max_symbols)
    mode = provider.strip().lower()
    result = run_full_market_scan(
        repo=_repo(),
        provider_mode=mode,
        max_symbols=max_symbols,
        include_etfs=include_etfs,
        sync_if_empty=sync_if_empty,
    )
    invalidated = _paper_recent_invalidated_instruments(mode)
    cards = [card for card in result.scan.cards if card.instrument_id not in invalidated]
    visible_card_ids = {card.card_id for card in cards}
    governance_audits = [
        audit for audit in result.scan.strategy_governance if audit.card_id in visible_card_ids
    ]
    _repo().save_scan_run(provider=mode, mode=mode, symbols=result.symbols, result=result.scan)
    payload = {
        "symbols": result.symbols,
        "cards": governed_card_payloads(cards, governance_audits),
        "items": [item.model_dump(mode="json") for item in result.scan.items],
        "strategy_health": [item.model_dump(mode="json") for item in result.scan.strategy_health],
        "factor_rankings": [item.model_dump(mode="json") for item in result.scan.factor_rankings],
        "sector_strength": [item.model_dump(mode="json") for item in result.scan.sector_strength],
        "rotation_radar": _rotation_radar_payload(
            cards,
            result.scan.sector_strength,
        ),
        "portfolio_plan": result.scan.portfolio_plan.model_dump(mode="json"),
        "market_intelligence": result.scan.market_intelligence.model_dump(mode="json")
        if result.scan.market_intelligence
        else None,
        "manual_action_center": result.scan.manual_action_center.model_dump(mode="json")
        if result.scan.manual_action_center
        else None,
        "signal_monitor": result.scan.signal_monitor.model_dump(mode="json")
        if result.scan.signal_monitor
        else None,
        "decision_quality_center": result.scan.decision_quality_center.model_dump(mode="json")
        if result.scan.decision_quality_center
        else None,
        "operational_readiness_center": result.scan.operational_readiness_center.model_dump(
            mode="json"
        )
        if result.scan.operational_readiness_center
        else None,
        "strategy_governance": [audit.model_dump(mode="json") for audit in governance_audits],
        "data_health": result.data_health,
    }
    payload["data_health"]["paper_invalidated_cards_filtered"] = str(
        len(result.scan.cards) - len(cards)
    )
    _relabel_instrument_payload(payload)
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    if not _restore_governance_card_payload(payload):
        _attach_card_briefs_and_cached_benchmarks(payload, mode)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_live_paper_health_payload(payload)
    payload.pop("operational_readiness_center", None)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    _attach_live_paper_health_payload(payload)
    _repo().save_scan_result_cache(
        cache_key=_full_market_scan_cache_key(mode, max_symbols, include_etfs, sync_if_empty),
        provider=mode,
        mode="full_market_scan",
        symbols=result.symbols,
        payload=payload,
    )
    return payload


def _validate_full_market_scan_params(max_symbols: int) -> None:
    if max_symbols <= 0 or max_symbols > 1000:
        raise HTTPException(status_code=400, detail="max_symbols must be between 1 and 1000")


def _validate_scan_cache_ttl(cache_ttl_minutes: int) -> None:
    if cache_ttl_minutes < 0 or cache_ttl_minutes > 7 * 24 * 60:
        raise HTTPException(status_code=400, detail="cache_ttl_minutes must be between 0 and 10080")


def _validate_full_market_batch_scan_params(
    batch_size: int,
    max_symbols: int | None,
) -> None:
    if batch_size <= 0 or batch_size > 500:
        raise HTTPException(status_code=400, detail="batch_size must be between 1 and 500")
    if max_symbols is not None and (max_symbols <= 0 or max_symbols > 20_000):
        raise HTTPException(status_code=400, detail="max_symbols must be between 1 and 20000")


def _full_market_job_payload(job) -> dict[str, object]:
    payload = job.model_dump(mode="json")
    symbols = payload.pop("symbols", [])
    if _is_finalizing_full_market_job(job):
        payload["phase"] = "finalizing"
        payload["message"] = "Finalizing full-market rankings and recommendation policy"
    else:
        payload["phase"] = payload["status"]
    payload["progress"] = _full_market_job_progress(job)
    if payload["status"] == "succeeded":
        payload["progress"] = 100
    payload["symbols_preview"] = symbols[:20]
    return payload


def _full_market_job_progress(job) -> int:
    if job.total_symbols <= 0:
        return 0
    if job.status == "succeeded":
        return 100
    return max(0, min(99, int(job.scanned_symbols * 100 / job.total_symbols)))


def _is_finalizing_full_market_job(job) -> bool:
    if job.status != "running":
        return False
    if job.total_symbols <= 0 or job.total_batches <= 0:
        return False
    return job.scanned_symbols >= job.total_symbols and job.completed_batches >= job.total_batches


def _recent_full_market_scan_payload(
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    sync_if_empty: bool,
    cache_ttl_minutes: int,
) -> dict[str, object] | None:
    if cache_ttl_minutes == 0:
        return None
    mode = provider.strip().lower()
    cache_key = _full_market_scan_cache_key(mode, max_symbols, include_etfs, sync_if_empty)
    cached = _repo().get_recent_scan_result_cache(
        cache_key=cache_key,
        max_age=timedelta(minutes=cache_ttl_minutes),
    )
    if cached is not None:
        payload = deepcopy(cached.payload)
        _relabel_instrument_payload(payload)
        _attach_rotation_radar_payload(payload)
        _attach_signal_hub_payload(payload)
        _attach_market_intelligence_payload(payload)
        _attach_recommendation_quality_payload(payload)
        _attach_probability_forecast_payload(payload)
        _attach_card_briefs_and_cached_benchmarks(payload, mode)
        data_health = payload.setdefault("data_health", {})
        if isinstance(data_health, dict):
            data_health["scan_result_cache"] = "hit"
            data_health["scan_result_cache_key"] = cache_key
            data_health["scan_result_cache_id"] = cached.cache_id
        _attach_manual_action_center_payload(payload)
        _attach_signal_monitor_payload(payload)
        _attach_decision_quality_payload(payload)
        _attach_operational_readiness_payload(payload)
        _attach_alpha_quality_payload(payload)
        _attach_research_center_payload(payload)
        return payload

    repo = _repo()
    batch_cached = repo.get_recent_scan_result_cache(
        cache_key=full_market_batch_cache_key(mode, include_etfs),
        max_age=timedelta(minutes=cache_ttl_minutes),
    )
    if batch_cached is not None:
        payload = deepcopy(batch_cached.payload)
        raw_cards = payload.get("cards")
        if isinstance(raw_cards, list):
            payload["cards"] = raw_cards[:max_symbols]
        selected_symbols = list(
            dict.fromkeys(
                str(card.get("instrument_id"))
                for card in payload.get("cards", [])
                if isinstance(card, dict) and card.get("instrument_id")
            )
        )
        selected_ids = set(selected_symbols)
        payload["symbols"] = selected_symbols
        raw_items = payload.get("items")
        if isinstance(raw_items, list):
            payload["items"] = [
                item
                for item in raw_items
                if isinstance(item, dict) and item.get("instrument_id") in selected_ids
            ]
        _hydrate_full_market_batch_payload(payload, repo, mode, cache_ttl_minutes)
        data_health = payload.setdefault("data_health", {})
        if isinstance(data_health, dict):
            data_health["scan_result_cache"] = "full_market_batch_fallback"
            data_health["scan_result_cache_key"] = cache_key
            data_health["scan_result_cache_id"] = batch_cached.cache_id
            data_health["full_market_requested"] = str(max_symbols)
        repo.save_scan_result_cache(
            cache_key=cache_key,
            provider=mode,
            mode="today_scan_fallback",
            symbols=[str(symbol) for symbol in payload.get("symbols", [])],
            payload=payload,
        )
        return payload

    payload = _recent_scan_run_fallback_payload(
        provider=mode,
        max_symbols=max_symbols,
        include_etfs=include_etfs,
        sync_if_empty=sync_if_empty,
        cache_ttl_minutes=cache_ttl_minutes,
    )
    if payload is None:
        return None
    _relabel_instrument_payload(payload)
    _attach_rotation_radar_payload(payload)
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    _attach_card_briefs_and_cached_benchmarks(payload, mode)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    _repo().save_scan_result_cache(
        cache_key=cache_key,
        provider=mode,
        mode="today_scan_fallback",
        symbols=[str(symbol) for symbol in payload.get("symbols", [])],
        payload=payload,
    )
    return payload


def _recent_scan_run_fallback_payload(
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    sync_if_empty: bool,
    cache_ttl_minutes: int,
) -> dict[str, object] | None:
    cache_key = _full_market_scan_cache_key(provider, max_symbols, include_etfs, sync_if_empty)
    bundle = _repo().get_recent_scan_run_with_snapshots(
        provider=provider,
        scanned=max_symbols,
        max_age=timedelta(minutes=cache_ttl_minutes),
    )
    if bundle is None:
        return None
    cards = [snapshot.card for snapshot in bundle.snapshots]
    portfolio_plan = build_portfolio_plan(
        [OpportunityCard.model_validate(card) for card in cards]
    ).model_dump(mode="json")
    data_health = {
        **bundle.run.data_health,
        "scan_result_cache": "scan_run_fallback",
        "scan_result_cache_key": cache_key,
        "scan_result_source_run": bundle.run.run_id,
        "full_market_requested": str(max_symbols),
        "full_market_include_etfs": str(include_etfs).lower(),
        "reconstructed_items": "false",
    }
    payload = {
        "symbols": bundle.run.symbols,
        "cards": cards,
        "items": [],
        "strategy_health": [],
        "factor_rankings": [],
        "sector_strength": [],
        "portfolio_plan": portfolio_plan,
        "data_health": data_health,
    }
    _attach_rotation_radar_payload(payload)
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    _attach_card_briefs_and_cached_benchmarks(payload, provider)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    return payload


def _hydrate_full_market_batch_payload(
    payload: dict[str, object],
    repo: QagentRepository,
    provider: str,
    cache_ttl_minutes: int,
) -> None:
    _relabel_instrument_payload(payload)
    data_health = payload.setdefault("data_health", {})
    if not isinstance(data_health, dict):
        data_health = {}
        payload["data_health"] = data_health
    hydrated_cards = _hydrate_legacy_opportunity_cards(payload)
    if hydrated_cards:
        data_health["legacy_cards_hydrated"] = str(hydrated_cards)
    if not payload.get("strategy_health"):
        recent = repo.get_latest_scan_result_cache_by_modes(
            provider=provider,
            modes={"full_market_scan", "today_scan_fallback"},
            max_age=timedelta(minutes=cache_ttl_minutes),
        )
        if recent and isinstance(recent.payload.get("strategy_health"), list):
            strategy_health = recent.payload.get("strategy_health", [])
            if strategy_health:
                payload["strategy_health"] = deepcopy(strategy_health)
                data_health["strategy_health_source"] = "recent_scan_cache"
                data_health["strategy_health_source_cache_id"] = recent.cache_id
    if not payload.get("strategy_health"):
        strategy_health = _strategy_health_from_card_calibration(payload)
        if strategy_health:
            payload["strategy_health"] = strategy_health
            data_health["strategy_health_source"] = "card_strategy_calibration"
    _attach_rotation_radar_payload(payload)
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    _attach_card_briefs_and_cached_benchmarks(payload, provider)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_live_paper_health_payload(payload)
    payload.pop("operational_readiness_center", None)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    _attach_live_paper_health_payload(payload)


def _hydrate_legacy_opportunity_cards(payload: dict[str, object]) -> int:
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        return 0
    hydrated: list[object] = []
    hydrated_count = 0
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            hydrated.append(raw_card)
            continue
        if raw_card.get("confidence_explanation") and raw_card.get("execution_plan"):
            hydrated.append(raw_card)
            continue
        try:
            card = OpportunityCard.model_validate(raw_card)
        except Exception:
            hydrated.append(raw_card)
            continue
        enrich_opportunity_card(card)
        if _should_refresh_instrument_label(card.instrument_id, card.instrument_label):
            card.instrument_label = format_instrument_label(card.instrument_id)
        hydrated.append(card.model_dump(mode="json"))
        hydrated_count += 1
    if hydrated_count:
        payload["cards"] = hydrated
    return hydrated_count


def _attach_card_briefs_and_cached_benchmarks(
    payload: dict[str, object],
    provider: str | None,
) -> None:
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        return
    cards = _cards_from_payload(raw_cards)
    if not cards:
        return

    data_health = payload.setdefault("data_health", {})
    if not isinstance(data_health, dict):
        data_health = {}
        payload["data_health"] = data_health

    data_health.update(_apply_cached_benchmark_comparisons(cards, provider))
    repo = _repo()
    final_policy = apply_final_recommendation_policy(
        cards,
        paper_report=_latest_paper_feedback_report(provider),
        walk_forward_validation=(
            load_latest_walk_forward_validation(repo, provider) if provider else None
        ),
        governance_context=load_strategy_governance_context(repo),
    )
    cards = sort_recommendation_cards(final_policy.cards)
    data_health.update(final_policy.data_health)
    data_health.update(apply_recommendation_briefs(cards))
    payload["cards"] = governed_card_payloads(cards, final_policy.audits)
    payload["strategy_governance"] = [
        audit.model_dump(mode="json") for audit in final_policy.audits
    ]


def _restore_governance_card_payload(payload: dict[str, object]) -> bool:
    raw_cards = payload.get("cards")
    raw_audits = payload.get("strategy_governance")
    if not isinstance(raw_cards, list) or not isinstance(raw_audits, list):
        return False
    cards = _cards_from_payload(raw_cards)
    audits = []
    for raw_audit in raw_audits:
        try:
            audits.append(CardStrategyGovernance.model_validate(raw_audit))
        except Exception:
            continue
    card_ids = {card.card_id for card in cards}
    audits = [audit for audit in audits if audit.card_id in card_ids]
    if not cards or {audit.card_id for audit in audits} != card_ids:
        return False
    payload["cards"] = governed_card_payloads(cards, audits)
    payload["strategy_governance"] = [audit.model_dump(mode="json") for audit in audits]
    return True


def _latest_paper_feedback_report(
    provider: str | None,
) -> PaperDailyReport | None:
    if not provider:
        return None
    try:
        return PaperDailyReport.model_validate(
            paper_trade_daily_report(provider=provider, limit=500)
        )
    except Exception:
        return None


def _apply_cached_benchmark_comparisons(
    cards: list[OpportunityCard],
    provider: str | None,
) -> dict[str, str]:
    cn_cards = [card for card in cards if card.instrument_id.startswith("CN:")]
    if not provider or not cn_cards:
        return {
            "cached_benchmark_comparison_cards": "0",
            "cached_benchmark_comparison_missing_cards": str(len(cn_cards)),
            "cached_benchmark_comparison_rows": "0",
        }

    start = date.today() - timedelta(days=180)
    end = date.today()
    cached_benchmark_ids = benchmark_ids() + benchmark_proxy_ids()
    instrument_ids = sorted({card.instrument_id for card in cn_cards})
    try:
        cached = _market_cache_repo().load_daily_bars(
            provider,
            sorted(set(instrument_ids + cached_benchmark_ids)),
            start,
            end,
        )
    except Exception:
        return {
            "cached_benchmark_comparison_cards": "0",
            "cached_benchmark_comparison_missing_cards": str(len(cn_cards)),
            "cached_benchmark_comparison_rows": "0",
        }
    if cached.empty:
        return {
            "cached_benchmark_comparison_cards": "0",
            "cached_benchmark_comparison_missing_cards": str(len(cn_cards)),
            "cached_benchmark_comparison_rows": "0",
        }

    frames = {
        str(instrument_id): group.copy()
        for instrument_id, group in cached.groupby("instrument_id", sort=False)
    }
    empty_frame = cached.iloc[0:0].copy()
    benchmark_frames = benchmark_frames_from_bars(cached)
    applied = 0
    missing = 0
    for card in cn_cards:
        comparison = build_benchmark_comparison_for_card(
            card,
            instrument_bars=frames.get(card.instrument_id, empty_frame),
            benchmark_frames=benchmark_frames,
        )
        if comparison is None:
            missing += 1
            continue
        card.benchmark_comparison = comparison
        applied += 1
    return {
        "cached_benchmark_comparison_cards": str(applied),
        "cached_benchmark_comparison_missing_cards": str(missing),
        "cached_benchmark_comparison_rows": str(len(cached)),
    }


def _rotation_radar_payload(
    cards: list[OpportunityCard],
    sector_strength: list[SectorStrength] | None = None,
) -> dict[str, object]:
    return build_rotation_radar(cards, sector_strength or []).model_dump(mode="json")


def _attach_rotation_radar_payload(payload: dict[str, object]) -> None:
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        payload["rotation_radar"] = _rotation_radar_payload([])
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue

    sectors: list[SectorStrength] = []
    raw_sectors = payload.get("sector_strength")
    if isinstance(raw_sectors, list):
        for raw_sector in raw_sectors:
            if not isinstance(raw_sector, dict):
                continue
            try:
                sectors.append(SectorStrength.model_validate(raw_sector))
            except Exception:
                continue

    payload["rotation_radar"] = _rotation_radar_payload(cards, sectors)


def _attach_signal_hub_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    enriched_cards: list[object] = []
    rotation = payload.get("rotation_radar")
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            enriched_cards.append(raw_card)
            continue
        try:
            card = OpportunityCard.model_validate(raw_card)
        except Exception:
            enriched_cards.append(raw_card)
            continue
        rotation_score, rotation_name = _card_rotation_score(card, rotation)
        card.signal_hub = build_signal_hub(
            card,
            rotation_score=rotation_score,
            rotation_name=rotation_name,
        )
        enriched_cards.append(card.model_dump(mode="json"))
    payload[cards_key] = enriched_cards


def _attach_market_intelligence_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("market_intelligence"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return
    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    if not cards:
        return

    raw_health = payload.get("strategy_health")
    strategy_health: list[StrategyHealth] = []
    if isinstance(raw_health, list):
        for item in raw_health:
            if not isinstance(item, dict):
                continue
            try:
                strategy_health.append(StrategyHealth.model_validate(item))
            except Exception:
                continue

    raw_data_health = payload.get("data_health")
    data_health = raw_data_health if isinstance(raw_data_health, dict) else {}
    raw_items = payload.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    center = build_market_intelligence_center(
        cards=cards,
        items=items,
        bars_by_instrument={},
        strategy_health=strategy_health,
        data_health=data_health,
    )
    already_calibrated = str(data_health.get("dynamic_calibration_passes", "0")) == "1"
    if not already_calibrated:
        apply_market_intelligence_to_cards(cards, center)
    payload[cards_key] = [card.model_dump(mode="json") for card in cards]
    payload["market_intelligence"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)
        payload_data_health["dynamic_calibration_reapplied"] = str(not already_calibrated).lower()


def _attach_recommendation_quality_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return
    if raw_cards and all(
        isinstance(raw_card, dict) and isinstance(raw_card.get("recommendation_quality"), dict)
        for raw_card in raw_cards
    ):
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    if not cards:
        return

    apply_recommendation_quality_gate(cards)
    cards = sort_recommendation_cards(cards)
    payload[cards_key] = [card.model_dump(mode="json") for card in cards]
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(recommendation_quality_data_health(cards))


def _attach_probability_forecast_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return
    cards = _cards_from_payload(raw_cards)
    if not cards:
        return
    if raw_cards and all(
        isinstance(raw_card, dict) and isinstance(raw_card.get("probability_forecast"), dict)
        for raw_card in raw_cards
    ):
        payload_data_health = payload.setdefault("data_health", {})
        if isinstance(payload_data_health, dict):
            payload_data_health.update(probability_calibration_data_health(cards))
        return

    apply_probability_calibration(
        cards, _strategy_health_from_payload(payload.get("strategy_health"))
    )
    cards = sort_recommendation_cards(cards)
    payload[cards_key] = [card.model_dump(mode="json") for card in cards]
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(probability_calibration_data_health(cards))


def _attach_manual_action_center_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("manual_action_center"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    if not cards:
        return

    raw_health = payload.get("strategy_health")
    strategy_health: list[StrategyHealth] = []
    if isinstance(raw_health, list):
        for item in raw_health:
            if not isinstance(item, dict):
                continue
            try:
                strategy_health.append(StrategyHealth.model_validate(item))
            except Exception:
                continue

    raw_data_health = payload.get("data_health")
    data_health = raw_data_health if isinstance(raw_data_health, dict) else {}
    center = build_manual_action_center(
        cards=cards,
        market_intelligence=payload.get("market_intelligence")
        if isinstance(payload.get("market_intelligence"), dict)
        else None,
        strategy_health=strategy_health,
        data_health=data_health,
    )
    payload["manual_action_center"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)


def _attach_signal_monitor_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("signal_monitor"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    if not cards:
        return

    provider = _payload_provider_mode(payload)
    bars_by_instrument = _cached_latest_bars_by_instrument(provider, cards)
    center = build_signal_monitor_center(cards, bars_by_instrument=bars_by_instrument)
    payload["signal_monitor"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)
        if provider:
            payload_data_health["signal_monitor_price_source"] = "market_cache"
            payload_data_health["signal_monitor_cached_bars"] = str(len(bars_by_instrument))


def _payload_provider_mode(payload: dict[str, object]) -> str | None:
    data_health = payload.get("data_health")
    if isinstance(data_health, dict):
        provider = data_health.get("provider")
        if isinstance(provider, str) and provider.strip():
            return provider.strip().lower()
    provider = payload.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip().lower()
    return None


def _cached_latest_bars_by_instrument(
    provider: str | None,
    cards: list[OpportunityCard],
) -> dict[str, object]:
    if not provider:
        return {}
    instrument_ids = sorted({card.instrument_id for card in cards})
    if not instrument_ids:
        return {}
    try:
        latest = _market_cache_repo().load_latest_daily_bars(provider, instrument_ids)
    except Exception:
        return {}
    if latest.empty:
        return {}
    return {
        str(instrument_id): group.copy()
        for instrument_id, group in latest.groupby("instrument_id", sort=False)
    }


def _attach_decision_quality_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("decision_quality_center"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    cards = _cards_from_payload(raw_cards)
    if not cards:
        return

    center = build_decision_quality_center(
        cards=cards,
        market_intelligence=_market_intelligence_from_payload(payload.get("market_intelligence")),
        portfolio_plan=_portfolio_plan_from_payload(payload.get("portfolio_plan")),
        signal_monitor=_signal_monitor_from_payload(payload.get("signal_monitor")),
        strategy_health=_strategy_health_from_payload(payload.get("strategy_health")),
        data_health=payload.get("data_health")
        if isinstance(payload.get("data_health"), dict)
        else {},
    )
    payload["decision_quality_center"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)


def _attach_operational_readiness_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("operational_readiness_center"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    cards = _cards_from_payload(raw_cards)
    if not cards:
        return

    alert_rules_count = 0
    try:
        alert_rules_count = len(_repo().list_alert_rules())
    except Exception:
        alert_rules_count = 0

    center = build_operational_readiness_center(
        cards=cards,
        market_intelligence=_market_intelligence_from_payload(payload.get("market_intelligence")),
        decision_quality_center=(
            None
            if not isinstance(payload.get("decision_quality_center"), dict)
            else build_decision_quality_center(
                cards=cards,
                market_intelligence=_market_intelligence_from_payload(
                    payload.get("market_intelligence")
                ),
                portfolio_plan=_portfolio_plan_from_payload(payload.get("portfolio_plan")),
                signal_monitor=_signal_monitor_from_payload(payload.get("signal_monitor")),
                strategy_health=_strategy_health_from_payload(payload.get("strategy_health")),
                data_health=payload.get("data_health")
                if isinstance(payload.get("data_health"), dict)
                else {},
            )
        ),
        signal_monitor=_signal_monitor_from_payload(payload.get("signal_monitor")),
        strategy_health=_strategy_health_from_payload(payload.get("strategy_health")),
        data_health=payload.get("data_health")
        if isinstance(payload.get("data_health"), dict)
        else {},
        alert_rules_count=alert_rules_count,
    )
    payload["operational_readiness_center"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)


def _attach_live_paper_health_payload(payload: dict[str, object]) -> None:
    data_health = payload.setdefault("data_health", {})
    if not isinstance(data_health, dict):
        data_health = {}
        payload["data_health"] = data_health
    try:
        provider = _payload_provider_mode(payload)
        trades = _paper_repo().list_trades(limit=1000, provider=provider)
        _, authenticated_ids, authentication_health = _paper_reporting_trades(
            trades,
            reporting_scope="official",
        )
        summary = summarize_paper_trades(
            trades,
            authenticated_trade_ids=authenticated_ids,
        )
    except Exception:
        return
    data_health.update(
        {
            **authentication_health,
            "paper_total": str(summary.total),
            "paper_pending": str(summary.pending),
            "paper_open": str(summary.open),
            "paper_closed": str(summary.closed),
            "paper_target_hit_count": str(summary.target_hit_count),
            "paper_stopped_count": str(summary.stopped_count),
            "paper_ledger": "true",
            "paper_provider_filter": provider or "all",
        }
    )


def _attach_alpha_quality_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("alpha_quality_center"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return
    cards = _cards_from_payload(raw_cards)
    if not cards:
        return

    center = build_alpha_quality_center(
        cards=cards,
        rotation_radar=_rotation_radar_from_payload(payload.get("rotation_radar")),
        strategy_health=_strategy_health_from_payload(payload.get("strategy_health")),
        data_health=payload.get("data_health")
        if isinstance(payload.get("data_health"), dict)
        else {},
    )
    payload["alpha_quality_center"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)


def _cards_from_payload(raw_cards: list[object]) -> list[OpportunityCard]:
    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    return cards


def _market_intelligence_from_payload(value: object) -> MarketIntelligenceCenter | None:
    if isinstance(value, MarketIntelligenceCenter):
        return value
    if isinstance(value, dict):
        try:
            return MarketIntelligenceCenter.model_validate(value)
        except Exception:
            return None
    return None


def _portfolio_plan_from_payload(value: object) -> PortfolioPlan | None:
    if isinstance(value, PortfolioPlan):
        return value
    if isinstance(value, dict):
        try:
            return PortfolioPlan.model_validate(value)
        except Exception:
            return None
    return None


def _signal_monitor_from_payload(value: object) -> SignalMonitorCenter | None:
    if isinstance(value, SignalMonitorCenter):
        return value
    if isinstance(value, dict):
        try:
            return SignalMonitorCenter.model_validate(value)
        except Exception:
            return None
    return None


def _rotation_radar_from_payload(value: object) -> MarketRotationRadar | None:
    if isinstance(value, MarketRotationRadar):
        return value
    if isinstance(value, dict):
        try:
            return MarketRotationRadar.model_validate(value)
        except Exception:
            return None
    return None


def _strategy_health_from_payload(value: object) -> list[StrategyHealth]:
    if not isinstance(value, list):
        return []
    health: list[StrategyHealth] = []
    for item in value:
        if isinstance(item, StrategyHealth):
            health.append(item)
        elif isinstance(item, dict):
            try:
                health.append(StrategyHealth.model_validate(item))
            except Exception:
                continue
    return health


def _attach_research_center_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        payload["research_center"] = build_research_command_center(cards=[]).model_dump(mode="json")
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue

    data_health = payload.get("data_health")
    center = build_research_command_center(
        cards=cards,
        portfolio_plan=payload.get("portfolio_plan")
        if isinstance(payload.get("portfolio_plan"), dict)
        else None,
        rotation_radar=payload.get("rotation_radar")
        if isinstance(payload.get("rotation_radar"), dict)
        else None,
        strategy_health=payload.get("strategy_health")
        if isinstance(payload.get("strategy_health"), list)
        else [],
        data_health=data_health if isinstance(data_health, dict) else {},
    )
    payload["research_center"] = center.model_dump(mode="json")


def _card_rotation_score(
    card: OpportunityCard,
    rotation: object,
) -> tuple[float | None, str | None]:
    if not isinstance(rotation, dict):
        return None, None
    raw_themes = rotation.get("themes")
    if not isinstance(raw_themes, list):
        return None, None
    matched: list[tuple[float, str]] = []
    card_keys = _card_rotation_keys(card)
    for raw_theme in raw_themes:
        if not isinstance(raw_theme, dict):
            continue
        name = raw_theme.get("name")
        score = raw_theme.get("score")
        if not isinstance(name, str) or not isinstance(score, (int, float)):
            continue
        leaders = raw_theme.get("leaders")
        leader_ids = set()
        if isinstance(leaders, list):
            leader_ids = {
                str(leader.get("instrument_id")) for leader in leaders if isinstance(leader, dict)
            }
        if card.instrument_id in leader_ids or name in card_keys:
            matched.append((float(score), name))
    if not matched:
        return None, None
    return max(matched, key=lambda item: item[0])


def _card_rotation_keys(card: OpportunityCard) -> set[str]:
    keys: set[str] = set()
    if card.asset_type == "ETF" or card.opportunity_bucket == "etf_index":
        keys.add("ETF/指数工具")
    if not card.market_context:
        return keys
    keys.add(card.market_context.industry)
    keys.update(card.market_context.themes)
    for membership in card.market_context.index_memberships:
        keys.add(_normalize_rotation_membership(membership))
    return keys


def _normalize_rotation_membership(value: str) -> str:
    text = value.strip()
    if "科创" in text:
        return "科创板"
    if "创业" in text:
        return "创业板"
    if "沪深300" in text:
        return "沪深300"
    if "中证500" in text:
        return "中证500"
    if "中证1000" in text:
        return "中证1000"
    if "ETF" in text.upper():
        return "ETF/指数工具"
    return text


def _relabel_instrument_payload(payload: dict[str, object]) -> None:
    _refresh_instrument_label(payload, "cards")
    _refresh_instrument_label(payload, "items")
    _refresh_instrument_label(payload, "factor_rankings")
    _refresh_portfolio_labels(payload, "portfolio_plan")
    _refresh_sector_instrument_labels(payload)


def _model_payload_with_label(record) -> dict[str, object]:
    return _attach_instrument_label(record.model_dump(mode="json"))


def _snapshot_payload_with_label(record) -> dict[str, object]:
    payload = _model_payload_with_label(record)
    card = payload.get("card")
    if isinstance(card, dict):
        instrument_id = card.get("instrument_id")
        current_label = card.get("instrument_label")
        if isinstance(instrument_id, str):
            if not isinstance(current_label, str):
                current_label = None
            if _should_refresh_instrument_label(instrument_id, current_label):
                card["instrument_label"] = format_instrument_label(instrument_id)
    return payload


def _attach_instrument_label(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    instrument_id = payload.get("instrument_id")
    if isinstance(instrument_id, str):
        current_label = payload.get("instrument_label")
        if isinstance(current_label, str) and not _should_refresh_instrument_label(
            instrument_id,
            current_label,
        ):
            return payload
        payload["instrument_label"] = format_instrument_label(instrument_id)
    return payload


def _refresh_instrument_label(payload: dict[str, object], key: str) -> None:
    records = payload.get(key)
    if not isinstance(records, list):
        return
    for record in records:
        if not isinstance(record, dict):
            continue
        instrument_id = record.get("instrument_id")
        if not isinstance(instrument_id, str):
            continue
        current_label = record.get("instrument_label")
        if not isinstance(current_label, str):
            current_label = None
        if _should_refresh_instrument_label(instrument_id, current_label):
            record["instrument_label"] = format_instrument_label(instrument_id)


def _refresh_sector_instrument_labels(payload: dict[str, object]) -> None:
    for key in ("leaders", "laggards"):
        sector_records = payload.get("sector_strength")
        if not isinstance(sector_records, list):
            continue
        for sector in sector_records:
            if not isinstance(sector, dict):
                continue
            moves = sector.get(key)
            if not isinstance(moves, list):
                continue
            for move in moves:
                if not isinstance(move, dict):
                    continue
                instrument_id = move.get("instrument_id")
                if not isinstance(instrument_id, str):
                    continue
                current_label = move.get("instrument_label")
                if not isinstance(current_label, str):
                    current_label = None
                if _should_refresh_instrument_label(instrument_id, current_label):
                    move["instrument_label"] = format_instrument_label(instrument_id)


def _refresh_portfolio_labels(payload: dict[str, object], key: str) -> None:
    portfolio = payload.get(key)
    if not isinstance(portfolio, dict):
        return
    for section_key in ("allocations", "watchlist"):
        entries = portfolio.get(section_key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            instrument_id = entry.get("instrument_id")
            if not isinstance(instrument_id, str):
                continue
            current_label = entry.get("instrument_label")
            if not isinstance(current_label, str):
                current_label = None
            if _should_refresh_instrument_label(instrument_id, current_label):
                entry["instrument_label"] = format_instrument_label(instrument_id)


def _should_refresh_instrument_label(instrument_id: str, current_label: str | None) -> bool:
    if not current_label or not current_label.strip():
        return True
    symbol = market_symbol(instrument_id)
    if not symbol:
        return current_label.strip() == instrument_id.strip()

    # 如果是 A 股代码类标的，任意不含中文的展示都要升级为可读中文标签。
    # 旧数据中经常会留下“688059.SH”这类代码形式的标签。
    if symbol.isdigit():
        return not _contains_chinese(current_label)

    if _contains_chinese(current_label):
        return False
    return current_label.strip() == symbol


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _strategy_health_from_card_calibration(payload: dict[str, object]) -> list[dict[str, object]]:
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        return []
    by_strategy: dict[str, dict[str, object]] = {}
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        calibration = raw_card.get("strategy_calibration")
        if not isinstance(calibration, dict):
            continue
        strategy_id = calibration.get("strategy_id")
        if not isinstance(strategy_id, str) or not strategy_id:
            continue
        sample_count = _int_value(calibration.get("sample_count"))
        current = by_strategy.get(strategy_id)
        if current is not None and _int_value(current.get("sample_count")) >= sample_count:
            continue
        name, family = _strategy_identity_from_card(raw_card, strategy_id)
        by_strategy[strategy_id] = {
            "strategy_id": strategy_id,
            "name": name,
            "family": family,
            "readiness": str(calibration.get("readiness") or "limited_sample"),
            "sample_count": sample_count,
            "win_rate_10d": calibration.get("win_rate_10d"),
            "avg_return_10d": calibration.get("avg_return_10d"),
            "avg_return_20d": calibration.get("avg_return_20d"),
            "max_loss_10d": calibration.get("max_loss_10d"),
            "missing_data": [],
            "curve": [],
        }
    return sorted(by_strategy.values(), key=lambda item: str(item["strategy_id"]))


def _strategy_identity_from_card(raw_card: dict[str, object], strategy_id: str) -> tuple[str, str]:
    evaluations = raw_card.get("strategy_evaluations")
    if isinstance(evaluations, list):
        for raw_evaluation in evaluations:
            if not isinstance(raw_evaluation, dict):
                continue
            if raw_evaluation.get("strategy_id") == strategy_id:
                name = raw_evaluation.get("name")
                family = raw_evaluation.get("family")
                return (
                    str(name or strategy_id),
                    str(family or "calibrated_strategy"),
                )
    return strategy_id, "calibrated_strategy"


def _int_value(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _full_market_scan_cache_key(
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    sync_if_empty: bool,
) -> str:
    return (
        f"today_scan:{provider.strip().lower()}:{max_symbols}:"
        f"{str(include_etfs).lower()}:{str(sync_if_empty).lower()}"
    )


def _task_payload(
    task_id: str,
    kind: str,
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    cache: str,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "kind": kind,
        "provider": provider.strip().lower(),
        "max_symbols": max_symbols,
        "include_etfs": include_etfs,
        "cache": cache,
    }


@router.post("/alerts/evaluate")
def evaluate_alerts(request: AlertEvaluationRequest) -> dict[str, list[object]]:
    prices = {instrument_id: Decimal(price) for instrument_id, price in request.prices.items()}
    rules = [
        AlertRule(
            rule_id=rule.rule_id,
            instrument_id=rule.instrument_id,
            kind=rule.kind,
            operator=rule.operator,
            threshold=rule.threshold,
        )
        for rule in _repo().list_alert_rules()
    ]
    alerts = evaluate_snapshot_alerts(prices, rules)
    return {"alerts": [alert.model_dump(mode="json") for alert in alerts]}


@router.post("/alerts/run")
def run_alerts(
    provider: str = "fixture",
    queue: bool = False,
    recipient: str | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    try:
        market_provider = build_market_data_provider(mode)
        result = run_alert_rules(
            repo=_repo(),
            provider=market_provider,
            queue_delivery=queue,
            recipient=recipient,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "summary": result.summary.model_dump(mode="json"),
        "alerts": [alert.model_dump(mode="json") for alert in result.alerts],
        "latest_prices": {key: str(value) for key, value in result.latest_prices.items()},
        "delivery": result.delivery.model_dump(mode="json") if result.delivery else None,
        "data_health": result.data_health,
    }


@router.get("/alert-suggestions")
def alert_suggestions(provider: str | None = None, limit: int = 50) -> dict[str, list[object]]:
    mode = provider.strip().lower() if provider else None
    snapshots = _repo().list_opportunity_snapshots(limit=limit, provider=mode)
    suggestions = suggest_alert_rules(snapshots)
    return {"suggestions": [item.model_dump(mode="json") for item in suggestions]}


@router.get("/scan-runs")
def scan_runs(provider: str | None = None, limit: int = 20) -> dict[str, list[object]]:
    mode = provider.strip().lower() if provider else None
    return {
        "runs": [
            run.model_dump(mode="json")
            for run in _repo().list_scan_runs(limit=limit, provider=mode)
        ]
    }


@router.get("/opportunity-history")
def opportunity_history(
    provider: str | None = None,
    instrument_id: str | None = None,
    limit: int = 50,
) -> dict[str, list[object]]:
    mode = provider.strip().lower() if provider else None
    snapshots = _repo().list_opportunity_snapshots(
        instrument_id=instrument_id,
        limit=limit,
        provider=mode,
    )
    return {"snapshots": [_snapshot_payload_with_label(snapshot) for snapshot in snapshots]}


@router.get("/outcomes")
def outcomes(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    effective_limit = _outcomes_preview_limit(provider, instrument_id, limit)
    replayed, data_health = _replay_outcomes(provider, instrument_id, effective_limit)
    if effective_limit != limit:
        data_health["requested_limit"] = str(limit)
        data_health["preview_limit"] = str(effective_limit)
    return {
        "outcomes": [_model_payload_with_label(outcome) for outcome in replayed],
        "data_health": data_health,
    }


def _replay_outcomes(provider: str, instrument_id: str | None, limit: int):
    repo = _repo()
    snapshots, sample_selection = _validation_snapshots(
        repo,
        provider=provider,
        instrument_id=instrument_id,
        limit=limit,
    )
    dated_snapshots = [snapshot for snapshot in snapshots if snapshot.signal_date is not None]
    if not dated_snapshots:
        replayed = [_pending_snapshot_outcome(snapshot) for snapshot in snapshots]
        return replayed, {
            "provider": provider,
            "snapshots": str(len(snapshots)),
            "outcomes": str(len(replayed)),
            "bar_window": "none",
            "sample_selection": sample_selection,
        }
    instrument_ids = list(dict.fromkeys(snapshot.instrument_id for snapshot in dated_snapshots))
    start, end = _snapshot_replay_window(dated_snapshots)
    all_bars, bar_health = _validation_market_bars(
        provider=provider,
        instrument_ids=instrument_ids,
        start=start,
        end=end,
    )

    replayed = []
    for snapshot in snapshots:
        if snapshot.signal_date is None:
            replayed.append(_pending_snapshot_outcome(snapshot))
            continue
        if not all_bars.empty and "instrument_id" in all_bars.columns:
            bars = all_bars.loc[all_bars["instrument_id"] == snapshot.instrument_id]
        else:
            bars = all_bars
        replayed.append(compute_opportunity_outcome(snapshot, bars))
    data_health = {
        "provider": provider,
        "snapshots": str(len(snapshots)),
        "outcomes": str(len(replayed)),
        "bar_window": f"{start}:{end}",
        "bar_instruments": str(len(instrument_ids)),
        "sample_selection": sample_selection,
        **bar_health,
        "invalid_price_outcomes": str(
            sum(1 for outcome in replayed if outcome.data_quality_issue is not None)
        ),
    }
    return replayed, data_health


@router.get("/strategy-performance")
def strategy_performance(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 100,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    return {
        "performance": [
            item.model_dump(mode="json") for item in summarize_strategy_performance(replayed)
        ],
        "data_health": data_health,
    }


@router.get("/strategy-governance", response_model=StrategyGovernanceResponse)
def strategy_governance(
    strategy_id: str | None = None,
    event_limit: int = 50,
) -> StrategyGovernanceResponse:
    if event_limit <= 0 or event_limit > 500:
        raise HTTPException(status_code=400, detail="event_limit must be between 1 and 500")
    normalized_strategy_id = strategy_id.strip() if strategy_id else None
    if strategy_id is not None and not normalized_strategy_id:
        raise HTTPException(status_code=400, detail="strategy_id must not be blank")
    return StrategyGovernanceResponse.model_validate(
        build_strategy_governance_status(
            _repo(),
            strategy_id=normalized_strategy_id,
            event_limit=event_limit,
        )
    )


@router.get("/strategy-diagnostics")
def strategy_diagnostics(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 100,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    performance = summarize_strategy_performance(replayed)
    diagnostics = diagnose_strategy_performance(performance)
    data_health = {**data_health, "diagnostics": str(len(diagnostics))}
    return {
        "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
        "data_health": data_health,
    }


@router.get("/recommendation-closure")
def recommendation_closure(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 150,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    as_of = max(
        (outcome.signal_date for outcome in replayed if outcome.signal_date is not None),
        default=date.today(),
    )
    summary = summarize_recommendation_closure(replayed, as_of=as_of, windows=(30, 60, 90))
    payload = summary.model_dump(mode="json")
    payload["latest_outcomes"] = [
        _attach_existing_instrument_label(outcome)
        for outcome in payload.get("latest_outcomes", [])
        if isinstance(outcome, dict)
    ]
    payload["completed_outcomes"] = [
        _attach_existing_instrument_label(outcome)
        for outcome in payload.get("completed_outcomes", [])
        if isinstance(outcome, dict)
    ]
    payload["data_health"] = {
        **data_health,
        "closure_windows": "30,60,90",
        "as_of": str(as_of),
        "completed_outcomes": str(len(payload["completed_outcomes"])),
    }
    return payload


@router.get("/recommendation-followthrough")
def recommendation_followthrough(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 150,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    as_of = max(
        (outcome.signal_date for outcome in replayed if outcome.signal_date is not None),
        default=date.today(),
    )
    closure = summarize_recommendation_closure(replayed, as_of=as_of, windows=(30, 60, 90))
    center = build_recommendation_followthrough_center(
        closure,
        data_health=data_health,
    )
    return center.model_dump(mode="json")


@router.get("/recommendation-calibration")
def recommendation_calibration(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 200,
    reporting_scope: str = "official",
) -> dict[str, object]:
    pairs, data_health = _replay_snapshot_outcome_pairs(provider, instrument_id, limit)
    authenticated_sources, authentication_health = authenticated_ranking_v3_snapshot_sources(
        _repo(),
        [snapshot.snapshot_id for snapshot, _ in pairs],
    )
    as_of = max(
        (outcome.signal_date for _, outcome in pairs if outcome.signal_date is not None),
        default=date.today(),
    )
    center = build_recommendation_calibration_center(
        pairs,
        as_of=as_of,
        data_health={**data_health, **authentication_health},
        reporting_scope=_paper_reporting_scope_value(reporting_scope),
        authenticated_admission_sources=authenticated_sources,
    )
    return center.model_dump(mode="json")


def _replay_snapshot_outcome_pairs(
    provider: str,
    instrument_id: str | None,
    limit: int,
):
    repo = _repo()
    snapshots, sample_selection = _validation_snapshots(
        repo,
        provider=provider,
        instrument_id=instrument_id,
        limit=limit,
    )
    dated_snapshots = [snapshot for snapshot in snapshots if snapshot.signal_date is not None]
    if not dated_snapshots:
        pairs = [(snapshot, _pending_snapshot_outcome(snapshot)) for snapshot in snapshots]
        return pairs, {
            "provider": provider,
            "snapshots": str(len(snapshots)),
            "outcomes": str(len(pairs)),
            "bar_window": "none",
            "sample_selection": sample_selection,
        }
    instrument_ids = list(dict.fromkeys(snapshot.instrument_id for snapshot in dated_snapshots))
    start, end = _snapshot_replay_window(dated_snapshots)
    all_bars, bar_health = _validation_market_bars(
        provider=provider,
        instrument_ids=instrument_ids,
        start=start,
        end=end,
    )

    pairs = []
    for snapshot in snapshots:
        if snapshot.signal_date is None:
            pairs.append((snapshot, _pending_snapshot_outcome(snapshot)))
            continue
        if not all_bars.empty and "instrument_id" in all_bars.columns:
            bars = all_bars.loc[all_bars["instrument_id"] == snapshot.instrument_id]
        else:
            bars = all_bars
        pairs.append((snapshot, compute_opportunity_outcome(snapshot, bars)))
    data_health = {
        "provider": provider,
        "snapshots": str(len(snapshots)),
        "outcomes": str(len(pairs)),
        "bar_window": f"{start}:{end}",
        "bar_instruments": str(len(instrument_ids)),
        "sample_selection": sample_selection,
        **bar_health,
        "invalid_price_outcomes": str(
            sum(1 for _, outcome in pairs if outcome.data_quality_issue is not None)
        ),
    }
    return pairs, data_health


def _validation_snapshots(
    repo: QagentRepository,
    *,
    provider: str,
    instrument_id: str | None,
    limit: int,
) -> tuple[list[OpportunitySnapshotRecord], str]:
    if instrument_id:
        return (
            repo.list_opportunity_snapshots(
                instrument_id=instrument_id,
                limit=limit,
                provider=provider,
                require_signal_date=True,
            ),
            "dated_instrument_history",
        )

    # Validation needs independent recommendation dates, not hundreds of
    # cards emitted by a single full-market scan. Five top-ranked names per
    # signal day give the requested limit temporal breadth and exclude legacy
    # snapshots that cannot be replayed because their signal date is missing.
    end = _a_share_today() if provider.strip().lower() == "free" else date.today()
    snapshots = repo.list_top_daily_opportunity_snapshots(
        start=end - timedelta(days=730),
        end=end,
        top_n=5,
        provider=provider,
    )
    return snapshots[: max(limit, 0)], "daily_top_5_dated"


def _validation_market_bars(
    *,
    provider: str,
    instrument_ids: list[str],
    start: date,
    end: date,
):
    mode = provider.strip().lower()
    if mode == "free":
        bars = _market_cache_repo().load_daily_bars(mode, instrument_ids, start, end)
        return bars, {
            "bar_source": "sqlite_market_cache",
            "bar_rows": str(len(bars)),
        }
    try:
        market_provider = build_market_data_provider(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    bars = market_provider.get_daily_bars(instrument_ids, start=start, end=end)
    health = {
        "bar_source": getattr(market_provider, "name", mode),
        "bar_rows": str(len(bars)),
    }
    provider_errors = getattr(market_provider, "last_errors", [])
    if provider_errors:
        health["errors"] = " | ".join(provider_errors[:3])
    return bars, health


def _snapshot_replay_window(snapshots) -> tuple[date, date]:
    signal_dates = [
        snapshot.signal_date for snapshot in snapshots if snapshot.signal_date is not None
    ]
    if not signal_dates:
        today = date.today()
        return today, today
    return min(signal_dates) - timedelta(days=7), max(signal_dates) + timedelta(days=75)


def _pending_snapshot_outcome(snapshot) -> OpportunityOutcome:
    return OpportunityOutcome(
        snapshot_id=snapshot.snapshot_id,
        run_id=snapshot.run_id,
        instrument_id=snapshot.instrument_id,
        instrument_label=_snapshot_card_label(snapshot),
        primary_strategy_id=snapshot.primary_strategy_id,
        signal_date=snapshot.signal_date,
        outcome_status="pending",
        triggered=None,
        trigger_price=snapshot.trigger_price,
        initial_stop=snapshot.initial_stop,
        target_1=snapshot.target_1,
    )


def _snapshot_card_label(snapshot) -> str | None:
    card = snapshot.card
    if not isinstance(card, dict):
        return None
    label = card.get("instrument_label")
    if isinstance(label, str) and label.strip():
        return label
    return None


def _outcomes_preview_limit(provider: str, instrument_id: str | None, limit: int) -> int:
    if instrument_id:
        return limit
    if provider.strip().lower() == "free":
        return min(limit, 30)
    return limit


def _attach_existing_instrument_label(payload: dict[str, object]) -> dict[str, object]:
    current_label = payload.get("instrument_label")
    if isinstance(current_label, str) and current_label.strip():
        return payload
    payload["instrument_label"] = None
    return payload


@router.get("/portfolio")
def portfolio(provider: str = "fixture") -> dict[str, object]:
    positions = _repo().list_positions()
    risks = []
    data_health = {"provider": provider, "positions": str(len(positions)), "risk": "0"}
    if positions:
        try:
            market_provider = build_market_data_provider(provider)
            instrument_ids = [position.instrument_id for position in positions]
            snapshot = market_provider.get_snapshot(instrument_ids)
            latest_prices = {
                row["instrument_id"]: Decimal(str(row["close"])) for _, row in snapshot.iterrows()
            }
            for position in positions:
                latest_price = latest_prices.get(position.instrument_id)
                if latest_price is None:
                    continue
                risks.append(
                    analyze_position_risk(
                        PositionInput(**position.model_dump()),
                        current_price=latest_price,
                    )
                )
            data_health["risk"] = str(len(risks))
            provider_errors = getattr(market_provider, "last_errors", [])
            if provider_errors:
                data_health["errors"] = " | ".join(provider_errors[:3])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "positions": [position.model_dump(mode="json") for position in positions],
        "risk": [risk.model_dump(mode="json") for risk in risks],
        "data_health": data_health,
    }


@router.get("/watchlist")
def watchlist() -> dict[str, list[object]]:
    return {"items": [item.model_dump(mode="json") for item in _repo().list_watchlist_items()]}


@router.post("/watchlist")
def upsert_watchlist_item(item: WatchlistCreate) -> dict[str, object]:
    saved = _repo().upsert_watchlist_item(item)
    return saved.model_dump(mode="json")


@router.get("/positions")
def positions() -> dict[str, list[object]]:
    return {
        "positions": [position.model_dump(mode="json") for position in _repo().list_positions()]
    }


@router.post("/positions")
def upsert_position(position: PositionCreate) -> dict[str, object]:
    saved = _repo().upsert_position(position)
    return saved.model_dump(mode="json")


@router.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: AgentQueryRequest) -> AgentQueryResponse:
    result, mode, _ = _scan(request.provider, request.symbols)
    selected = None
    if request.instrument_id:
        selected = next(
            (card for card in result.cards if card.instrument_id == request.instrument_id), None
        )
    if selected is None and result.cards:
        selected = result.cards[0]
    if selected is None:
        return AgentQueryResponse(answer="No opportunity context is available yet.")
    position_risks = _position_risks(mode)
    selected_position_risk = next(
        (risk for risk in position_risks if risk.instrument_id == selected.instrument_id),
        None,
    )
    selected_paper_trade = _paper_trade_for_instrument(selected.instrument_id, mode)

    answer = answer_question(
        request.question,
        context={
            "instrument_id": selected.instrument_id,
            "instrument_label": selected.instrument_label
            or format_instrument_label(selected.instrument_id),
            "status": selected.status.value,
            "score": selected.score,
            "initial_stop": str(selected.exit_plan.initial_stop),
            "trigger_price": str(selected.entry_plan.trigger_price),
            "target_1": str(selected.exit_plan.target_1),
            "downside_pct": selected.scenario.downside_pct,
            "target_1_pct": selected.scenario.target_1_pct,
            "no_chase_above": str(selected.entry_plan.no_chase_above),
            "signal_summary": _signal_summary(selected),
            "primary_strategy_id": selected.primary_strategy_id,
            "strategy_score": selected.strategy_score,
            "strategy_summary": _strategy_summary(selected),
            "cards": [_agent_card_summary(card) for card in result.cards],
            "position_risk": (
                selected_position_risk.model_dump(mode="json") if selected_position_risk else None
            ),
            "position_risks": [risk.model_dump(mode="json") for risk in position_risks],
            "paper_trade": (
                selected_paper_trade.model_dump(mode="json") if selected_paper_trade else None
            ),
            "provider": mode,
            "data_health": result.data_health,
        },
    )
    return AgentQueryResponse(answer=answer)


def _paper_trade_for_instrument(instrument_id: str, provider: str | None = None):
    return next(
        (
            trade
            for trade in _paper_repo().list_trades(limit=1000, provider=provider)
            if trade.instrument_id == instrument_id
        ),
        None,
    )


def _agent_card_summary(card) -> dict[str, object]:
    decision = card.decision
    return {
        "instrument_id": card.instrument_id,
        "instrument_label": card.instrument_label or format_instrument_label(card.instrument_id),
        "status": card.status.value,
        "score": card.score,
        "rank_score": card.rank_score,
        "factor_score": card.factor_score,
        "factor_rank": card.factor_rank,
        "factor_flags": card.factor_flags,
        "action": decision.action if decision else "watch",
        "conviction_score": decision.conviction_score if decision else None,
        "trigger_price": str(card.entry_plan.trigger_price)
        if card.entry_plan.trigger_price
        else None,
        "initial_stop": str(card.exit_plan.initial_stop) if card.exit_plan.initial_stop else None,
        "target_1": str(card.exit_plan.target_1) if card.exit_plan.target_1 else None,
        "target_2": str(card.exit_plan.target_2) if card.exit_plan.target_2 else None,
        "no_chase_above": str(card.entry_plan.no_chase_above)
        if card.entry_plan.no_chase_above
        else None,
        "risk_reward": card.risk_reward,
        "primary_strategy_id": card.primary_strategy_id,
        "data_caveats": card.data_caveats,
        "tradability_label": card.tradability.label if card.tradability else None,
        "tradability_summary": card.tradability.summary if card.tradability else None,
    }
