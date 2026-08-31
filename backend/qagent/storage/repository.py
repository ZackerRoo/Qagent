from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, sessionmaker

from qagent.domain.models import OpportunityCard
from qagent.historical_evidence.models import (
    HistoricalEvidenceBundle,
    HistoricalIndexCoverageStats,
    HistoricalInstrumentProfile,
    HistoricalInstrumentEvidenceStats,
    normalize_historical_security_type,
)
from qagent.market.universes import UniverseCreate, UniverseRecord, normalize_symbols
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.storage.tables import (
    AlertRuleRow,
    AutomationSchedulerStateRow,
    BriefRunRow,
    DeliveryOutboxRow,
    FullMarketScanJobRow,
    FundamentalSnapshotRow,
    HistoricalBackfillJobRow,
    HistoricalIndexMembershipRow,
    HistoricalIndexSnapshotRow,
    HistoricalIndustrySnapshotRow,
    HistoricalInstrumentProfileRow,
    HistoricalTradabilityRow,
    OpportunitySnapshotRow,
    PolicyDeploymentRow,
    PositionRow,
    ScanResultCacheRow,
    ScanRunRow,
    StrategyStateEventRow,
    StrategyStateRow,
    StrategyVersionRow,
    TradableInstrumentRow,
    TradableUniverseSnapshotRow,
    UniverseRow,
    WatchlistItemRow,
    WalkForwardRunRow,
    WalkForwardJobRow,
)
from qagent.strategies.governance import strategy_policy_digest
from qagent.strategies.models import StrategyDefinition, StrategyPolicy, StrategyState
from qagent.strategies.registry import default_strategy_registry
from qagent.strategy_data.models import FundamentalSnapshot


WALK_FORWARD_CHECKPOINT_STORAGE_SCHEMA = "walk-forward-checkpoints-v2"
WALK_FORWARD_RUN_STORAGE_SCHEMA = "walk-forward-run-storage-v2"
WALK_FORWARD_RUN_STORAGE_SCHEMA_KEY = "_qagent_walk_forward_storage_schema"


def _full_market_checkpoint_job_id(cache_key: str, *, prefix: str) -> str | None:
    if not cache_key.startswith(prefix):
        return None
    remainder = cache_key[len(prefix) :]
    job_id, separator, batch_index = remainder.rpartition(":")
    if not separator or not job_id or not batch_index.isdigit():
        return None
    return job_id


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _walk_forward_job_execution_plan(row: WalkForwardJobRow) -> dict[str, object]:
    return {
        "provider": row.provider,
        "start_date": row.start_date.isoformat(),
        "end_date": row.end_date.isoformat(),
        "dataset_revision": row.dataset_revision,
        "rebalance_step_sessions": row.rebalance_step_sessions,
        "lookback_days": row.lookback_days,
        "total_snapshots": row.total_snapshots,
    }


def _current_walk_forward_manifest(
    payload: object,
):
    if not isinstance(payload, dict):
        return None
    from qagent.backtesting.experiment import (
        EXPERIMENT_SCHEMA_VERSION,
        WalkForwardExperimentManifest,
        walk_forward_manifest_digest_is_valid,
    )

    if payload.get("schema_version") != EXPERIMENT_SCHEMA_VERSION:
        return None
    try:
        manifest = WalkForwardExperimentManifest.model_validate(payload)
    except ValueError as exc:
        raise ValueError("current walk-forward manifest is malformed") from exc
    if not walk_forward_manifest_digest_is_valid(manifest):
        raise ValueError("current walk-forward manifest failed integrity validation")
    return manifest


def _validate_walk_forward_job_manifest_plan(
    row: WalkForwardJobRow,
    manifest,
) -> None:
    expected = _walk_forward_job_execution_plan(row)
    manifest_plan = {
        "provider": manifest.provider_mode,
        "start_date": manifest.start_date.isoformat(),
        "end_date": manifest.end_date.isoformat(),
        "dataset_revision": manifest.dataset_revision,
        "rebalance_step_sessions": manifest.rebalance_step_sessions,
        "lookback_days": manifest.lookback_days,
        "total_snapshots": row.total_snapshots,
    }
    if expected != manifest_plan:
        raise ValueError("walk-forward job execution plan does not match its manifest")


def _checkpoint_chain_digest(
    *,
    job_id: str,
    experiment_digest: str,
    execution_digest: str,
    execution_plan: dict[str, object],
    checkpoints: list[dict[str, object]],
) -> str:
    previous = _canonical_digest(
        {
            "schema_version": WALK_FORWARD_CHECKPOINT_STORAGE_SCHEMA,
            "job_id": job_id,
            "experiment_digest": experiment_digest,
            "execution_digest": execution_digest,
            "execution_plan": execution_plan,
        }
    )
    for index, checkpoint in enumerate(checkpoints):
        previous = _canonical_digest(
            {
                "previous_digest": previous,
                "index": index,
                "checkpoint": checkpoint,
            }
        )
    return previous


def _encode_walk_forward_checkpoints(
    row: WalkForwardJobRow,
    *,
    manifest_payload: dict[str, object],
    checkpoints: list[dict[str, object]],
) -> str:
    manifest = _current_walk_forward_manifest(manifest_payload)
    if manifest is None:
        return json.dumps(checkpoints, ensure_ascii=True, sort_keys=True)
    _validate_walk_forward_job_manifest_plan(row, manifest)
    execution_plan = _walk_forward_job_execution_plan(row)
    envelope: dict[str, object] = {
        "schema_version": WALK_FORWARD_CHECKPOINT_STORAGE_SCHEMA,
        "job_id": row.job_id,
        "experiment_digest": manifest.experiment_digest,
        "execution_digest": manifest.execution_digest,
        "execution_plan": execution_plan,
        "checkpoint_count": len(checkpoints),
        "checkpoint_chain_digest": _checkpoint_chain_digest(
            job_id=row.job_id,
            experiment_digest=manifest.experiment_digest,
            execution_digest=manifest.execution_digest,
            execution_plan=execution_plan,
            checkpoints=checkpoints,
        ),
        "checkpoints": checkpoints,
    }
    envelope["envelope_digest"] = _canonical_digest(envelope)
    return json.dumps(envelope, ensure_ascii=True, sort_keys=True)


def _decode_walk_forward_checkpoints(
    row: WalkForwardJobRow,
    *,
    manifest_payload: dict[str, object],
) -> list[dict[str, object]]:
    try:
        stored = json.loads(row.checkpoints_json or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"walk-forward job {row.job_id} checkpoint JSON is invalid") from exc
    manifest = _current_walk_forward_manifest(manifest_payload)
    if manifest is None:
        if not isinstance(stored, list) or not all(isinstance(item, dict) for item in stored):
            raise ValueError(f"legacy walk-forward job {row.job_id} checkpoints are malformed")
        return stored
    _validate_walk_forward_job_manifest_plan(row, manifest)
    if not isinstance(stored, dict):
        raise ValueError(
            f"walk-forward job {row.job_id} current checkpoints lack integrity envelope"
        )
    expected_keys = {
        "schema_version",
        "job_id",
        "experiment_digest",
        "execution_digest",
        "execution_plan",
        "checkpoint_count",
        "checkpoint_chain_digest",
        "checkpoints",
        "envelope_digest",
    }
    if set(stored) != expected_keys:
        raise ValueError(
            f"walk-forward job {row.job_id} checkpoint envelope fields are invalid"
        )
    checkpoints = stored.get("checkpoints")
    if not isinstance(checkpoints, list) or not all(isinstance(item, dict) for item in checkpoints):
        raise ValueError(f"walk-forward job {row.job_id} checkpoints are malformed")
    execution_plan = _walk_forward_job_execution_plan(row)
    bindings_match = (
        stored.get("schema_version") == WALK_FORWARD_CHECKPOINT_STORAGE_SCHEMA
        and stored.get("job_id") == row.job_id
        and stored.get("experiment_digest") == manifest.experiment_digest
        and stored.get("execution_digest") == manifest.execution_digest
        and stored.get("execution_plan") == execution_plan
        and stored.get("checkpoint_count") == len(checkpoints)
    )
    if not bindings_match:
        raise ValueError(f"walk-forward job {row.job_id} checkpoint binding is invalid")
    expected_chain_digest = _checkpoint_chain_digest(
        job_id=row.job_id,
        experiment_digest=manifest.experiment_digest,
        execution_digest=manifest.execution_digest,
        execution_plan=execution_plan,
        checkpoints=checkpoints,
    )
    if not hmac.compare_digest(
        str(stored.get("checkpoint_chain_digest", "")),
        expected_chain_digest,
    ):
        raise ValueError(f"walk-forward job {row.job_id} checkpoint chain is invalid")
    digest_payload = dict(stored)
    stored_envelope_digest = str(digest_payload.pop("envelope_digest", ""))
    if not hmac.compare_digest(stored_envelope_digest, _canonical_digest(digest_payload)):
        raise ValueError(f"walk-forward job {row.job_id} checkpoint envelope is invalid")
    return checkpoints


class WatchlistCreate(BaseModel):
    instrument_id: str
    thesis: str | None = None
    status: str = "watch"
    tags: list[str] = Field(default_factory=list)


class WatchlistItem(BaseModel):
    instrument_id: str
    thesis: str | None
    status: str
    tags: list[str]


class PositionCreate(BaseModel):
    instrument_id: str
    shares: Decimal
    entry_price: Decimal
    entry_date: date
    strategy_tag: str | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    target_2: Decimal | None = None
    thesis: str | None = None


class Position(BaseModel):
    instrument_id: str
    shares: Decimal
    entry_price: Decimal
    entry_date: date
    strategy_tag: str | None
    initial_stop: Decimal | None
    target_1: Decimal | None
    target_2: Decimal | None
    thesis: str | None


class AlertRuleCreate(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    operator: str
    threshold: Decimal


class StoredAlertRule(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    operator: str
    threshold: Decimal


class ScanRunRecord(BaseModel):
    run_id: str
    provider: str
    mode: str
    symbols: list[str]
    scanned: int
    cards: int
    data_health: dict[str, str]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class PaperModelCohortRecord(BaseModel):
    cohort_id: str
    feature_set_version: str
    recommendation_policy_entrypoint: str
    calibration_merge_policy: str


def paper_model_cohort_from_data_health(
    data_health: dict[str, str],
) -> PaperModelCohortRecord | None:
    feature_set_version = str(data_health.get("feature_set_version") or "").strip()
    recommendation_policy_entrypoint = str(
        data_health.get("recommendation_policy_entrypoint") or ""
    ).strip()
    if not feature_set_version or not recommendation_policy_entrypoint:
        return None
    calibration_merge_policy = str(
        data_health.get("dynamic_calibration_merge_policy") or "unspecified"
    ).strip()
    identity = {
        "schema_version": "paper-model-cohort-v1",
        "feature_set_version": feature_set_version,
        "recommendation_policy_entrypoint": recommendation_policy_entrypoint,
        "calibration_merge_policy": calibration_merge_policy,
    }
    return PaperModelCohortRecord(
        cohort_id=_canonical_digest(identity),
        feature_set_version=feature_set_version,
        recommendation_policy_entrypoint=recommendation_policy_entrypoint,
        calibration_merge_policy=calibration_merge_policy,
    )


class ScanResultCacheRecord(BaseModel):
    cache_id: str
    cache_key: str
    provider: str
    mode: str
    symbols: list[str]
    payload: dict[str, object]
    created_at: datetime


class ScanCheckpointMaintenanceReport(BaseModel):
    schema_version: str = "scan-checkpoint-maintenance-v1"
    dry_run: bool
    retention_days: int
    cutoff: datetime
    active_job_ids: list[str]
    total_checkpoint_rows: int
    protected_active_rows: int
    protected_recent_rows: int
    protected_unrecognized_rows: int
    eligible_rows: int
    eligible_succeeded_rows: int
    eligible_expired_terminal_rows: int
    eligible_payload_bytes: int
    deleted_rows: int
    deleted_payload_bytes: int
    sqlite_page_size: int
    sqlite_page_count: int
    sqlite_freelist_count: int
    sqlite_reusable_bytes: int
    protected_evidence_domains: list[str] = Field(
        default_factory=lambda: [
            "paper_trades_and_events",
            "walk_forward_runs_and_evidence",
            "historical_replay_and_tradability",
            "opportunity_and_scan_run_snapshots",
        ]
    )


class ScanRunSnapshotBundle(BaseModel):
    run: ScanRunRecord
    snapshots: list[OpportunitySnapshotRecord]


class FullMarketScanJobRecord(BaseModel):
    job_id: str
    provider: str
    status: str
    batch_size: int
    total_symbols: int
    scanned_symbols: int
    total_batches: int
    completed_batches: int
    cards: int
    errors: int
    include_etfs: bool
    sync_if_empty: bool
    symbols: list[str]
    message: str
    data_health: dict[str, str]
    result_cache_key: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def progress(self) -> int:
        if self.total_symbols <= 0:
            return 0
        if self.status == "succeeded":
            return 100
        return max(0, min(99, int(self.scanned_symbols * 100 / self.total_symbols)))


class HistoricalBackfillJobRecord(BaseModel):
    job_id: str
    provider: str
    status: str
    start_date: date
    end_date: date
    symbols: list[str]
    total_symbols: int
    processed_symbols: int
    succeeded_symbols: int
    failed_symbols: int
    rows_written: int
    fundamental_rows_written: int
    current_instrument: str | None
    errors: list[str]
    data_health: dict[str, str]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def progress(self) -> int:
        if self.total_symbols <= 0:
            return 0
        if "backfill_phase" not in self.data_health:
            return max(
                0,
                min(100, int(self.processed_symbols * 100 / self.total_symbols)),
            )
        phase = self.data_health.get("backfill_phase", self.status)
        if phase == "complete" or self.status in {"succeeded", "succeeded_with_errors"}:
            return 100
        price_ratio = min(max(self.processed_symbols / self.total_symbols, 0), 1)
        fundamental_processed = int(
            self.data_health.get("backfill_fundamental_processed", "0") or 0
        )
        evidence_processed = int(self.data_health.get("backfill_evidence_processed", "0") or 0)
        phase_progress = {
            "queued": 0,
            "inventory": 1,
            "trading_rules": 2,
            "corporate_actions": 3,
            "terminal_settlements": 4,
            "replay_prices": int(5 + price_ratio * 55),
            "price_retry": 60,
            "benchmark_prices": 61,
            "fundamentals": int(
                62 + min(max(fundamental_processed / self.total_symbols, 0), 1) * 18
            ),
            "historical_evidence": int(
                81 + min(max(evidence_processed / self.total_symbols, 0), 1) * 16
            ),
            "replay_coverage": 98,
            "failed": int(5 + price_ratio * 55),
        }
        return max(0, min(99, phase_progress.get(phase, int(price_ratio * 60))))


class WalkForwardRunRecord(BaseModel):
    run_id: str
    provider: str
    status: str
    start_date: date
    end_date: date
    dataset_revision: int
    rebalance_step_sessions: int
    lookback_days: int
    snapshot_count: int
    top_5_trade_count: int
    top_10_trade_count: int
    top_5_return_pct: float
    top_10_return_pct: float
    top_5_oos_trades: int
    top_10_oos_trades: int
    top_5_oos_gate: str
    top_10_oos_gate: str
    reproducibility_digest: str
    payload: dict[str, object]
    data_health: dict[str, str]
    created_at: datetime
    updated_at: datetime


class WalkForwardJobRecord(BaseModel):
    job_id: str
    provider: str
    status: str
    phase: str
    start_date: date
    end_date: date
    dataset_revision: int
    rebalance_step_sessions: int
    lookback_days: int
    total_snapshots: int
    processed_snapshots: int
    current_date: date | None
    lease_maintenance_count: int
    lease_recovery_count: int
    last_lease_heartbeat_at: datetime | None
    checkpoints: list[dict[str, object]]
    experiment_manifest: dict[str, object]
    result_run_id: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    def _portfolio_channel_selection_progress(self) -> int:
        phase_start = 92
        next_phase_start = 95
        if self.current_date is None:
            return phase_start

        decision_dates: list[date] = []
        for checkpoint in self.checkpoints:
            raw_date = checkpoint.get("decision_date")
            if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
                decision_date = raw_date
            elif isinstance(raw_date, str):
                try:
                    decision_date = date.fromisoformat(raw_date)
                except ValueError:
                    return phase_start
            else:
                return phase_start
            decision_dates.append(decision_date)

        ordered_dates = sorted(set(decision_dates))
        if len(ordered_dates) != self.total_snapshots or self.current_date not in ordered_dates:
            return phase_start

        processed_dates = ordered_dates.index(self.current_date) + 1
        phase_progress = phase_start + (
            processed_dates * (next_phase_start - phase_start) // len(ordered_dates)
        )
        return min(next_phase_start - 1, phase_progress)

    @property
    def progress(self) -> int:
        if self.status == "succeeded":
            return 100
        if self.status == "failed":
            return (
                max(
                    0,
                    min(
                        100,
                        int(self.processed_snapshots * 100 / self.total_snapshots),
                    ),
                )
                if self.total_snapshots
                else 0
            )
        if self.total_snapshots <= 0:
            return 0
        if self.status == "running":
            if self.phase == "preparing_historical_replay":
                return 1
            if self.phase == "historical_replay":
                return max(
                    2,
                    min(
                        80,
                        int(self.processed_snapshots * 80 / self.total_snapshots),
                    ),
                )
            final_phase_progress = {
                "portfolio_simulation": 82,
                "portfolio_baseline": 82,
                "candidate_outcomes": 85,
                "candidate_outcomes_stress": 88,
                "ranking_models": 90,
                "portfolio_channel_backtests": 95,
                "validation_and_benchmarks": 97,
            }
            if self.phase == "portfolio_channel_selection":
                return self._portfolio_channel_selection_progress()
            if self.phase in final_phase_progress:
                return final_phase_progress[self.phase]
        return max(
            0,
            min(99, int(self.processed_snapshots * 100 / self.total_snapshots)),
        )


class AutomationSchedulerStateRecord(BaseModel):
    enabled: bool
    settings: dict[str, object]
    runtime: dict[str, object] = Field(default_factory=dict)
    updated_at: datetime
    revision: int = 0


class OpportunitySnapshotRecord(BaseModel):
    snapshot_id: str
    run_id: str
    card_id: str
    instrument_id: str
    market: str
    status: str
    signal_date: date | None
    latest_close: Decimal | None
    primary_strategy_id: str | None
    score: Decimal
    strategy_score: Decimal
    rank_score: Decimal
    trigger_price: Decimal | None
    initial_stop: Decimal | None
    target_1: Decimal | None
    card: dict[str, object]


class BriefRunRecord(BaseModel):
    brief_id: str
    provider: str
    symbols: list[str]
    headline: str
    opportunity_count: int
    entry_watch_count: int
    risk_alert_count: int
    catalyst_count: int
    validation_count: int
    data_health: dict[str, str]
    payload: dict[str, object]
    created_at: datetime


class DeliveryOutboxRecord(BaseModel):
    delivery_id: str
    brief_id: str | None
    channel: str
    recipient: str | None
    subject: str
    markdown: str
    payload: dict[str, object]
    idempotency_key: str | None = None
    payload_digest: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None


class DeliveryIdempotencyConflictError(ValueError):
    pass


class StoredTradableInstrument(BaseModel):
    instrument_id: str
    symbol: str
    name: str
    label: str
    asset_type: str
    exchange: str
    source: str
    tags: list[str] = Field(default_factory=list)
    synced_at: datetime | None = None


class TradableCatalogSummary(BaseModel):
    total_count: int
    stock_count: int
    etf_count: int
    other_count: int
    exchanges: dict[str, int] = Field(default_factory=dict)
    last_synced_at: datetime | None = None


class TradableCatalogSearchResult(BaseModel):
    items: list[StoredTradableInstrument]
    summary: TradableCatalogSummary
    data_health: dict[str, str] = Field(default_factory=dict)


class StrategyVersionRecord(BaseModel):
    strategy_id: str
    strategy_version: str
    definition_digest: str
    definition: dict[str, object]
    created_at: datetime


class PolicyDeploymentRecord(BaseModel):
    deployment_id: str
    strategy_id: str
    policy_version: str
    strategy_version: str
    factor_version: str
    parameter_version: str
    universe_version: str
    data_revision: str
    policy_digest: str
    policy: StrategyPolicy
    previous_deployment_id: str | None
    created_at: datetime


class StrategyStateRecord(BaseModel):
    strategy_id: str
    state: StrategyState
    current_deployment_id: str | None
    previous_deployment_id: str | None
    current_policy_version: str | None
    previous_policy_version: str | None
    effective_weight: float
    revision: int
    created_at: datetime
    updated_at: datetime

    @property
    def current_state(self) -> StrategyState:
        return self.state


class StrategyStateEventRecord(BaseModel):
    event_id: str
    strategy_id: str
    sequence: int
    idempotency_key: str
    event_type: str
    action: str
    from_state: StrategyState | None
    to_state: StrategyState
    deployment_id: str | None
    previous_deployment_id: str | None
    policy_version: str | None
    effective_weight: float
    reason: str
    evidence: dict[str, object]
    decision: dict[str, object]
    created_at: datetime

    @property
    def state(self) -> StrategyState:
        return self.to_state


def _serialize_tags(tags: list[str]) -> str:
    return ",".join(tag.strip() for tag in tags if tag.strip())


def _parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    return [tag for tag in value.split(",") if tag]


class QagentRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def replay_evidence(
        self,
        provider_mode: str,
        *,
        owner_run_id: str | None = None,
        run_status_lookup: Callable[[str], str | None] | None = None,
    ) -> ReplayEvidenceRepository:
        return ReplayEvidenceRepository(
            self.session_factory,
            provider_mode=provider_mode,
            owner_run_id=owner_run_id,
            run_status_lookup=run_status_lookup,
        )

    def initialize_strategy_governance_defaults(
        self,
        defaults: list[StrategyDefinition] | list[StrategyPolicy] | None = None,
        *,
        definitions: list[StrategyDefinition] | None = None,
        policies: list[StrategyPolicy] | None = None,
        strategy_version: str = "builtin-v1",
    ) -> list[StrategyStateRecord]:
        """Add missing built-in strategy records without changing live state."""

        resolved_definitions, resolved_policies = _resolve_governance_defaults(
            defaults,
            definitions=definitions,
            policies=policies,
        )
        version = strategy_version.strip()
        if not version:
            raise ValueError("strategy_version must not be blank")
        definitions_by_id: dict[str, StrategyDefinition] = {}
        for definition in resolved_definitions:
            existing = definitions_by_id.get(definition.strategy_id)
            if existing is not None and _canonical_json(existing) != _canonical_json(definition):
                raise ValueError(f"conflicting defaults for strategy {definition.strategy_id}")
            definitions_by_id[definition.strategy_id] = definition
        policies_by_identity: dict[tuple[str, str], StrategyPolicy] = {}
        for policy in resolved_policies:
            identity = (policy.strategy_id, policy.policy_version)
            existing = policies_by_identity.get(identity)
            if existing is not None and _canonical_json(existing) != _canonical_json(policy):
                raise ValueError(
                    f"conflicting defaults for policy {policy.strategy_id}:{policy.policy_version}"
                )
            policies_by_identity[identity] = policy
        current_policies: dict[str, StrategyPolicy] = {}
        for policy in resolved_policies:
            current_policies[policy.strategy_id] = policy

        with self.session_factory() as session:
            _begin_governance_write(session)
            try:
                ensured: dict[tuple[str, str], PolicyDeploymentRow] = {}
                visiting: set[tuple[str, str]] = set()

                def ensure_policy(policy: StrategyPolicy) -> PolicyDeploymentRow:
                    identity = (policy.strategy_id, policy.policy_version)
                    if identity in ensured:
                        return ensured[identity]
                    if identity in visiting:
                        raise ValueError("policy rollback chain contains a cycle")
                    visiting.add(identity)
                    definition = definitions_by_id.get(policy.strategy_id)
                    _ensure_strategy_version(
                        session,
                        strategy_id=policy.strategy_id,
                        strategy_version=policy.strategy_version,
                        definition=_strategy_definition_payload(policy, definition),
                    )
                    previous = None
                    if policy.rollback_policy_version is not None:
                        rollback_identity = (
                            policy.strategy_id,
                            policy.rollback_policy_version,
                        )
                        rollback_policy = policies_by_identity.get(rollback_identity)
                        previous = (
                            ensure_policy(rollback_policy)
                            if rollback_policy is not None
                            else _find_policy_deployment(
                                session,
                                policy.strategy_id,
                                policy.rollback_policy_version,
                            )
                        )
                    deployment = _ensure_policy_deployment(
                        session,
                        policy,
                        previous_deployment_id=(
                            previous.deployment_id if previous is not None else None
                        ),
                    )
                    visiting.remove(identity)
                    ensured[identity] = deployment
                    return deployment

                policy_strategy_ids = {policy.strategy_id for policy in resolved_policies}
                for definition in resolved_definitions:
                    if definition.strategy_id not in policy_strategy_ids:
                        _ensure_strategy_version(
                            session,
                            strategy_id=definition.strategy_id,
                            strategy_version=version,
                            definition=definition.model_dump(mode="json"),
                        )
                for policy in resolved_policies:
                    ensure_policy(policy)

                now = datetime.now(timezone.utc)
                strategy_ids = set(definitions_by_id) | set(current_policies)
                for strategy_id in sorted(strategy_ids):
                    if session.get(StrategyStateRow, strategy_id) is not None:
                        continue
                    policy = current_policies.get(strategy_id)
                    deployment = ensure_policy(policy) if policy is not None else None
                    previous = (
                        session.get(PolicyDeploymentRow, deployment.previous_deployment_id)
                        if deployment is not None and deployment.previous_deployment_id is not None
                        else None
                    )
                    state = policy.state if policy is not None else StrategyState.RESEARCH
                    session.add(
                        StrategyStateRow(
                            strategy_id=strategy_id,
                            state=state.value,
                            current_deployment_id=(
                                deployment.deployment_id if deployment is not None else None
                            ),
                            previous_deployment_id=(
                                previous.deployment_id if previous is not None else None
                            ),
                            current_policy_version=(
                                deployment.policy_version if deployment is not None else None
                            ),
                            previous_policy_version=(
                                previous.policy_version if previous is not None else None
                            ),
                            effective_weight=Decimal(str(_policy_effective_weight(policy, state))),
                            revision=0,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                session.commit()
            except Exception:
                session.rollback()
                raise
        return self.list_strategy_states()

    def initialize_strategy_defaults(
        self,
        defaults: list[StrategyDefinition] | list[StrategyPolicy] | None = None,
        **kwargs: object,
    ) -> list[StrategyStateRecord]:
        return self.initialize_strategy_governance_defaults(defaults, **kwargs)

    def initialize_governance_defaults(
        self,
        defaults: list[StrategyDefinition] | list[StrategyPolicy] | None = None,
        **kwargs: object,
    ) -> list[StrategyStateRecord]:
        return self.initialize_strategy_governance_defaults(defaults, **kwargs)

    def list_strategy_states(
        self,
        state: StrategyState | str | None = None,
    ) -> list[StrategyStateRecord]:
        with self.session_factory() as session:
            query = session.query(StrategyStateRow)
            if state is not None:
                query = query.filter(StrategyStateRow.state == StrategyState(state).value)
            rows = query.order_by(StrategyStateRow.strategy_id).all()
            return [self._strategy_state_from_row(row) for row in rows]

    def get_strategy_state(self, strategy_id: str) -> StrategyStateRecord | None:
        with self.session_factory() as session:
            row = session.get(StrategyStateRow, strategy_id)
            return self._strategy_state_from_row(row) if row is not None else None

    def list_strategy_versions(
        self,
        strategy_id: str | None = None,
    ) -> list[StrategyVersionRecord]:
        with self.session_factory() as session:
            query = session.query(StrategyVersionRow)
            if strategy_id is not None:
                query = query.filter(StrategyVersionRow.strategy_id == strategy_id)
            rows = query.order_by(
                StrategyVersionRow.strategy_id,
                StrategyVersionRow.created_at,
                StrategyVersionRow.strategy_version,
            ).all()
            return [self._strategy_version_from_row(row) for row in rows]

    def list_policy_deployments(
        self,
        strategy_id: str | None = None,
    ) -> list[PolicyDeploymentRecord]:
        with self.session_factory() as session:
            query = session.query(PolicyDeploymentRow)
            if strategy_id is not None:
                query = query.filter(PolicyDeploymentRow.strategy_id == strategy_id)
            rows = query.order_by(
                PolicyDeploymentRow.created_at,
                PolicyDeploymentRow.deployment_id,
            ).all()
            return [self._policy_deployment_from_row(row) for row in rows]

    def get_policy_deployment(self, deployment_id: str) -> PolicyDeploymentRecord | None:
        with self.session_factory() as session:
            row = session.get(PolicyDeploymentRow, deployment_id)
            return self._policy_deployment_from_row(row) if row is not None else None

    def list_strategy_state_events(
        self,
        strategy_id: str | None = None,
        limit: int = 100,
    ) -> list[StrategyStateEventRecord]:
        with self.session_factory() as session:
            query = session.query(StrategyStateEventRow)
            if strategy_id is not None:
                query = query.filter(StrategyStateEventRow.strategy_id == strategy_id)
            rows = (
                query.order_by(
                    StrategyStateEventRow.created_at,
                    StrategyStateEventRow.event_id,
                )
                .limit(max(0, limit))
                .all()
            )
            return [self._strategy_state_event_from_row(row) for row in rows]

    def record_governance_decision(
        self,
        policy: StrategyPolicy,
        decision: BaseModel | dict[str, object],
        evidence: BaseModel | dict[str, object] | None = None,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
        strategy_definition: StrategyDefinition | None = None,
        effective_weight: float | None = None,
        event_type: str = "governance_decision",
    ) -> StrategyStateEventRecord:
        policy = StrategyPolicy.model_validate(policy)
        key = _required_text(idempotency_key, "idempotency_key")
        decision_json = _canonical_json(decision)
        evidence_json = _canonical_json(evidence or {})
        decision_payload = _json_object(decision_json)
        event_reason = _required_text(
            reason if reason is not None else decision_payload.get("reason"),
            "reason",
        )
        _validate_decision_identity(policy, decision_payload)
        declared_from = _optional_strategy_state(decision_payload.get("from_state"))
        target_state = _decision_target_state(decision_payload)
        action = _decision_action(decision_payload)
        event_name = _required_text(event_type, "event_type")

        with self.session_factory() as session:
            _begin_governance_write(session)
            try:
                replay = _find_idempotent_governance_event(session, key)
                if replay is not None:
                    _validate_decision_replay(
                        replay,
                        policy=policy,
                        decision_json=decision_json,
                        evidence_json=evidence_json,
                        reason=event_reason,
                    )
                    return self._strategy_state_event_from_row(replay)

                state_row = session.get(StrategyStateRow, policy.strategy_id)
                if state_row is None:
                    initial_state = declared_from or policy.state
                    now = datetime.now(timezone.utc)
                    state_row = StrategyStateRow(
                        strategy_id=policy.strategy_id,
                        state=initial_state.value,
                        effective_weight=Decimal("0"),
                        revision=0,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(state_row)
                    session.flush()
                current_state = StrategyState(state_row.state)
                if declared_from is not None and declared_from is not current_state:
                    raise ValueError(
                        "stale governance decision: "
                        f"stored state is {current_state.value}, decision expects "
                        f"{declared_from.value}"
                    )

                definition_payload = _strategy_definition_payload(
                    policy,
                    strategy_definition or _default_strategy_definition(policy.strategy_id),
                )
                _ensure_strategy_version(
                    session,
                    strategy_id=policy.strategy_id,
                    strategy_version=policy.strategy_version,
                    definition=definition_payload,
                )
                old_deployment = (
                    session.get(PolicyDeploymentRow, state_row.current_deployment_id)
                    if state_row.current_deployment_id is not None
                    else None
                )
                configured_previous = (
                    _find_policy_deployment(
                        session,
                        policy.strategy_id,
                        policy.rollback_policy_version,
                    )
                    if policy.rollback_policy_version is not None
                    else None
                )
                previous_for_snapshot = configured_previous or (
                    old_deployment
                    if old_deployment is not None
                    and old_deployment.policy_version != policy.policy_version
                    else None
                )
                deployment = _ensure_policy_deployment(
                    session,
                    policy,
                    previous_deployment_id=(
                        previous_for_snapshot.deployment_id
                        if previous_for_snapshot is not None
                        else None
                    ),
                )

                if state_row.current_deployment_id != deployment.deployment_id:
                    previous_deployment_id = (
                        state_row.current_deployment_id or deployment.previous_deployment_id
                    )
                else:
                    previous_deployment_id = state_row.previous_deployment_id
                previous_policy_version = _deployment_policy_version(
                    session,
                    previous_deployment_id,
                )
                resolved_weight = _decision_effective_weight(
                    policy,
                    decision_payload,
                    target_state,
                    override=effective_weight,
                )
                now = datetime.now(timezone.utc)
                sequence = _next_strategy_event_sequence(session, policy.strategy_id)
                state_row.state = target_state.value
                state_row.current_deployment_id = deployment.deployment_id
                state_row.previous_deployment_id = previous_deployment_id
                state_row.current_policy_version = deployment.policy_version
                state_row.previous_policy_version = previous_policy_version
                state_row.effective_weight = Decimal(str(resolved_weight))
                state_row.revision = max(state_row.revision + 1, sequence)
                state_row.updated_at = now
                event_row = StrategyStateEventRow(
                    event_id=f"strategy-event-{uuid4().hex}",
                    strategy_id=policy.strategy_id,
                    sequence=sequence,
                    idempotency_key=key,
                    event_type=event_name,
                    action=action,
                    from_state=current_state.value,
                    to_state=target_state.value,
                    deployment_id=deployment.deployment_id,
                    previous_deployment_id=previous_deployment_id,
                    policy_version=deployment.policy_version,
                    effective_weight=Decimal(str(resolved_weight)),
                    reason=event_reason,
                    evidence_json=evidence_json,
                    decision_json=decision_json,
                    created_at=now,
                )
                session.add(event_row)
                session.commit()
                return self._strategy_state_event_from_row(event_row)
            except IntegrityError as error:
                session.rollback()
                replay = self._load_idempotent_governance_event(key)
                if replay is None:
                    raise error
                _validate_decision_replay_record(
                    replay,
                    policy=policy,
                    decision_json=decision_json,
                    evidence_json=evidence_json,
                    reason=event_reason,
                )
                return replay
            except Exception:
                session.rollback()
                raise

    def rollback_policy_deployment(
        self,
        strategy_id: str,
        idempotency_key: str | None = None,
        reason: str | None = None,
        *,
        target_deployment_id: str | None = None,
        target_policy_version: str | None = None,
        evidence: BaseModel | dict[str, object] | None = None,
        to_state: StrategyState | str = StrategyState.RESEARCH,
    ) -> StrategyStateEventRecord:
        strategy_key = _required_text(strategy_id, "strategy_id")
        key = _required_text(idempotency_key, "idempotency_key")
        evidence_json = _canonical_json(evidence or {})
        target_state = StrategyState(to_state)

        with self.session_factory() as session:
            _begin_governance_write(session)
            try:
                replay = _find_idempotent_governance_event(session, key)
                if replay is not None:
                    _validate_rollback_replay(
                        replay,
                        strategy_id=strategy_key,
                        target_deployment_id=target_deployment_id,
                        target_policy_version=target_policy_version,
                        evidence_json=evidence_json,
                        reason=reason,
                    )
                    return self._strategy_state_event_from_row(replay)

                state_row = session.get(StrategyStateRow, strategy_key)
                if state_row is None or state_row.current_deployment_id is None:
                    raise LookupError(f"strategy {strategy_key!r} has no active deployment")
                current = session.get(PolicyDeploymentRow, state_row.current_deployment_id)
                if current is None:
                    raise LookupError("current policy deployment does not exist")
                target = _resolve_rollback_target(
                    session,
                    current=current,
                    target_deployment_id=target_deployment_id,
                    target_policy_version=target_policy_version,
                )
                if target is None:
                    raise LookupError(f"strategy {strategy_key!r} has no previous deployment")
                if target.strategy_id != strategy_key:
                    raise ValueError("rollback target belongs to a different strategy")
                if target.deployment_id == current.deployment_id:
                    raise ValueError("rollback target must differ from current deployment")

                event_reason = _required_text(
                    reason
                    or (
                        f"Rollback strategy {strategy_key} from policy "
                        f"{current.policy_version} to {target.policy_version}."
                    ),
                    "reason",
                )
                target_policy = StrategyPolicy.model_validate_json(target.policy_json)
                resolved_weight = _policy_effective_weight(target_policy, target_state)
                decision_payload: dict[str, object] = {
                    "action": "rollback",
                    "from_state": state_row.state,
                    "to_state": target_state.value,
                    "from_deployment_id": current.deployment_id,
                    "deployment_id": target.deployment_id,
                    "current_policy_version": current.policy_version,
                    "rollback_to_policy_version": target.policy_version,
                }
                decision_json = _canonical_json(decision_payload)
                previous_deployment_id = current.deployment_id
                previous_policy_version = current.policy_version
                now = datetime.now(timezone.utc)
                sequence = _next_strategy_event_sequence(session, strategy_key)
                from_state = StrategyState(state_row.state)
                state_row.state = target_state.value
                state_row.current_deployment_id = target.deployment_id
                state_row.previous_deployment_id = previous_deployment_id
                state_row.current_policy_version = target.policy_version
                state_row.previous_policy_version = previous_policy_version
                state_row.effective_weight = Decimal(str(resolved_weight))
                state_row.revision = max(state_row.revision + 1, sequence)
                state_row.updated_at = now
                event_row = StrategyStateEventRow(
                    event_id=f"strategy-event-{uuid4().hex}",
                    strategy_id=strategy_key,
                    sequence=sequence,
                    idempotency_key=key,
                    event_type="deployment_rollback",
                    action="rollback",
                    from_state=from_state.value,
                    to_state=target_state.value,
                    deployment_id=target.deployment_id,
                    previous_deployment_id=previous_deployment_id,
                    policy_version=target.policy_version,
                    effective_weight=Decimal(str(resolved_weight)),
                    reason=event_reason,
                    evidence_json=evidence_json,
                    decision_json=decision_json,
                    created_at=now,
                )
                session.add(event_row)
                session.commit()
                return self._strategy_state_event_from_row(event_row)
            except IntegrityError as error:
                session.rollback()
                replay = self._load_idempotent_governance_event(key)
                if replay is None:
                    raise error
                return replay
            except Exception:
                session.rollback()
                raise

    def rollback_deployment(
        self,
        strategy_id: str,
        idempotency_key: str | None = None,
        reason: str | None = None,
        **kwargs: object,
    ) -> StrategyStateEventRecord:
        return self.rollback_policy_deployment(
            strategy_id,
            idempotency_key,
            reason,
            **kwargs,
        )

    def rollback_strategy_deployment(
        self,
        strategy_id: str,
        idempotency_key: str | None = None,
        reason: str | None = None,
        **kwargs: object,
    ) -> StrategyStateEventRecord:
        return self.rollback_policy_deployment(
            strategy_id,
            idempotency_key,
            reason,
            **kwargs,
        )

    def _load_idempotent_governance_event(
        self,
        idempotency_key: str,
    ) -> StrategyStateEventRecord | None:
        with self.session_factory() as session:
            row = _find_idempotent_governance_event(session, idempotency_key)
            return self._strategy_state_event_from_row(row) if row is not None else None

    def save_automation_scheduler_state(
        self,
        *,
        enabled: bool,
        settings: dict[str, object],
        runtime: dict[str, object] | None = None,
        expected_revision: int | None = None,
        control_plane: bool = False,
    ) -> AutomationSchedulerStateRecord:
        with self.session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            now = datetime.now(timezone.utc)
            row = session.get(AutomationSchedulerStateRow, "default")
            runtime_payload = runtime or {}
            if row is not None:
                try:
                    stored_payload = json.loads(row.settings_json or "{}")
                except json.JSONDecodeError:
                    stored_payload = {}
                stored_runtime = stored_payload.get("runtime", {})
                if (
                    isinstance(stored_runtime, dict)
                    and int(stored_runtime.get("run_count") or 0)
                    > int(runtime_payload.get("run_count") or 0)
                ):
                    runtime_payload = stored_runtime
                if expected_revision is not None and row.revision != expected_revision:
                    if not control_plane:
                        return self._automation_scheduler_state_from_row(row)
                    # Explicit start/stop/settings changes own the control
                    # plane, but never roll runtime evidence backwards.
                    if isinstance(stored_runtime, dict):
                        runtime_payload = stored_runtime
            state_json = json.dumps(
                {"runtime": runtime_payload, "settings": settings},
                sort_keys=True,
            )
            if row is None:
                row = AutomationSchedulerStateRow(
                    state_id="default",
                    enabled=enabled,
                    settings_json=state_json,
                    created_at=now,
                    updated_at=now,
                    revision=1,
                )
                session.add(row)
            else:
                row.enabled = enabled
                row.settings_json = state_json
                row.updated_at = now
                row.revision += 1
            session.commit()
            session.refresh(row)
            return self._automation_scheduler_state_from_row(row)

    def get_automation_scheduler_state(self) -> AutomationSchedulerStateRecord | None:
        with self.session_factory() as session:
            row = session.get(AutomationSchedulerStateRow, "default")
            return self._automation_scheduler_state_from_row(row) if row is not None else None

    def upsert_watchlist_item(self, item: WatchlistCreate) -> WatchlistItem:
        with self.session_factory() as session:
            row = session.get(WatchlistItemRow, item.instrument_id)
            if row is None:
                row = WatchlistItemRow(instrument_id=item.instrument_id)
                session.add(row)
            row.thesis = item.thesis
            row.status = item.status
            row.tags = _serialize_tags(item.tags)
            session.commit()
            session.refresh(row)
            return self._watchlist_from_row(row)

    def list_watchlist_items(self) -> list[WatchlistItem]:
        with self.session_factory() as session:
            rows = session.query(WatchlistItemRow).order_by(WatchlistItemRow.instrument_id).all()
            return [self._watchlist_from_row(row) for row in rows]

    def upsert_position(self, position: PositionCreate) -> Position:
        with self.session_factory() as session:
            row = session.get(PositionRow, position.instrument_id)
            if row is None:
                row = PositionRow(instrument_id=position.instrument_id)
                session.add(row)
            row.shares = position.shares
            row.entry_price = position.entry_price
            row.entry_date = position.entry_date
            row.strategy_tag = position.strategy_tag
            row.initial_stop = position.initial_stop
            row.target_1 = position.target_1
            row.target_2 = position.target_2
            row.thesis = position.thesis
            session.commit()
            session.refresh(row)
            return self._position_from_row(row)

    def list_positions(self) -> list[Position]:
        with self.session_factory() as session:
            rows = session.query(PositionRow).order_by(PositionRow.instrument_id).all()
            return [self._position_from_row(row) for row in rows]

    def upsert_alert_rule(self, rule: AlertRuleCreate) -> StoredAlertRule:
        with self.session_factory() as session:
            row = session.get(AlertRuleRow, rule.rule_id)
            if row is None:
                row = AlertRuleRow(rule_id=rule.rule_id)
                session.add(row)
            row.instrument_id = rule.instrument_id
            row.kind = rule.kind
            row.operator = rule.operator
            row.threshold = rule.threshold
            session.commit()
            session.refresh(row)
            return self._alert_rule_from_row(row)

    def list_alert_rules(self) -> list[StoredAlertRule]:
        with self.session_factory() as session:
            rows = session.query(AlertRuleRow).order_by(AlertRuleRow.rule_id).all()
            return [self._alert_rule_from_row(row) for row in rows]

    def upsert_universe(self, universe: UniverseCreate) -> UniverseRecord:
        with self.session_factory() as session:
            row = session.get(UniverseRow, universe.universe_id)
            if row is None:
                row = UniverseRow(universe_id=universe.universe_id)
                session.add(row)
            row.name = universe.name
            row.description = universe.description
            row.market_scope = universe.market_scope
            row.tags = _serialize_tags(universe.tags)
            row.symbols = json.dumps(normalize_symbols(universe.symbols))
            row.source = "custom"
            session.commit()
            session.refresh(row)
            return self._universe_from_row(row)

    def list_custom_universes(self) -> list[UniverseRecord]:
        with self.session_factory() as session:
            rows = session.query(UniverseRow).order_by(UniverseRow.name).all()
            return [self._universe_from_row(row) for row in rows]

    def get_universe(self, universe_id: str) -> UniverseRecord | None:
        with self.session_factory() as session:
            row = session.get(UniverseRow, universe_id)
            if row is None:
                return None
            return self._universe_from_row(row)

    def replace_tradable_instruments(
        self,
        instruments: list,
        data_health: dict[str, str] | None = None,
    ) -> TradableCatalogSummary:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            session.query(TradableInstrumentRow).delete()
            for instrument in instruments:
                tags = _instrument_tags(instrument)
                session.add(
                    TradableInstrumentRow(
                        instrument_id=instrument.instrument_id,
                        symbol=instrument.symbol,
                        name=instrument.name,
                        label=instrument.label,
                        asset_type=instrument.asset_type,
                        exchange=instrument.exchange,
                        source=instrument.source,
                        tags=_serialize_tags(tags),
                        synced_at=now,
                    )
                )
            session.commit()
        return self.tradable_catalog_summary()

    def tradable_catalog_summary(self) -> TradableCatalogSummary:
        with self.session_factory() as session:
            rows = session.query(TradableInstrumentRow).all()
            return _tradable_summary(rows)

    def search_tradable_instruments(
        self,
        query: str = "",
        asset_type: str | None = None,
        limit: int = 50,
    ) -> TradableCatalogSearchResult:
        normalized_query = query.strip().upper()
        normalized_asset = asset_type.strip().lower() if asset_type else None
        with self.session_factory() as session:
            rows = session.query(TradableInstrumentRow).all()
        filtered = []
        for row in rows:
            if normalized_asset and row.asset_type.lower() != normalized_asset:
                continue
            if normalized_query and not _matches_tradable_row(row, normalized_query):
                continue
            filtered.append(row)
        if normalized_query:
            filtered.sort(key=lambda row: _tradable_match_rank(row, normalized_query))
        else:
            filtered.sort(key=lambda row: (_asset_browse_rank(row.asset_type), row.symbol))
        capped = filtered[: max(limit, 0)]
        return TradableCatalogSearchResult(
            items=[self._tradable_instrument_from_row(row) for row in capped],
            summary=_tradable_summary(rows),
            data_health={
                "tradable_catalog": "sqlite",
                "tradable_matched": str(len(filtered)),
                "tradable_returned": str(len(capped)),
            },
        )

    def list_tradable_instruments(
        self,
        asset_types: set[str] | None = None,
        limit: int = 500,
    ) -> list[StoredTradableInstrument]:
        normalized_types = {item.lower() for item in asset_types or set()}
        with self.session_factory() as session:
            rows = session.query(TradableInstrumentRow).all()
        if normalized_types:
            rows = [row for row in rows if row.asset_type.lower() in normalized_types]
        rows.sort(key=lambda row: (_asset_browse_rank(row.asset_type), row.symbol))
        return [self._tradable_instrument_from_row(row) for row in rows[: max(limit, 0)]]

    def capture_tradable_universe_snapshot(self, as_of_date: date) -> int:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            instruments = session.query(TradableInstrumentRow).all()
            for instrument in instruments:
                key = (as_of_date, instrument.instrument_id)
                row = session.get(TradableUniverseSnapshotRow, key)
                if row is None:
                    row = TradableUniverseSnapshotRow(
                        as_of_date=as_of_date,
                        instrument_id=instrument.instrument_id,
                    )
                    session.add(row)
                row.symbol = instrument.symbol
                row.name = instrument.name
                row.asset_type = instrument.asset_type
                row.exchange = instrument.exchange
                row.source = instrument.source
                row.active = True
                row.captured_at = now
            session.commit()
        return len(instruments)

    def upsert_historical_universe_snapshots(
        self,
        profiles: list[HistoricalInstrumentProfile],
        snapshot_dates: list[date],
    ) -> int:
        now = datetime.now(timezone.utc)
        records: list[dict[str, object]] = []
        for snapshot_date in sorted(set(snapshot_dates)):
            for profile in profiles:
                asset_type = normalize_historical_security_type(profile.security_type)
                if asset_type is None:
                    continue
                if profile.listing_date is not None and profile.listing_date > snapshot_date:
                    continue
                if profile.delisting_date is not None and profile.delisting_date < snapshot_date:
                    continue
                symbol = profile.instrument_id.split(":", 1)[-1]
                records.append(
                    {
                        "as_of_date": snapshot_date,
                        "instrument_id": profile.instrument_id,
                        "symbol": symbol,
                        "name": profile.name or symbol,
                        "asset_type": asset_type,
                        "exchange": ("SH" if symbol.startswith(("5", "6", "9")) else "SZ"),
                        "source": profile.provider,
                        "active": True,
                        "captured_at": now,
                    }
                )
        with self.session_factory() as session:
            _sqlite_upsert_chunks(
                session,
                TradableUniverseSnapshotRow,
                records,
                ["as_of_date", "instrument_id"],
            )
            session.commit()
        return len(records)

    def count_tradable_universe_snapshots(
        self,
        as_of_date: date | None = None,
        instrument_ids: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> int:
        with self.session_factory() as session:
            query = session.query(TradableUniverseSnapshotRow)
            if as_of_date is not None:
                query = query.filter(TradableUniverseSnapshotRow.as_of_date == as_of_date)
            if instrument_ids:
                query = query.filter(TradableUniverseSnapshotRow.instrument_id.in_(instrument_ids))
            if start is not None:
                query = query.filter(TradableUniverseSnapshotRow.as_of_date >= start)
            if end is not None:
                query = query.filter(TradableUniverseSnapshotRow.as_of_date <= end)
            return query.count()

    def tradable_universe_snapshot_stats(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> dict[str, tuple[int, date | None, date | None]]:
        if not instrument_ids:
            return {}
        with self.session_factory() as session:
            rows = (
                session.query(
                    TradableUniverseSnapshotRow.instrument_id,
                    func.count(TradableUniverseSnapshotRow.instrument_id),
                    func.min(TradableUniverseSnapshotRow.as_of_date),
                    func.max(TradableUniverseSnapshotRow.as_of_date),
                )
                .filter(
                    TradableUniverseSnapshotRow.instrument_id.in_(instrument_ids),
                    TradableUniverseSnapshotRow.as_of_date <= end,
                )
                .group_by(TradableUniverseSnapshotRow.instrument_id)
                .all()
            )
        return {
            instrument_id: (int(count), first_date, last_date)
            for instrument_id, count, first_date, last_date in rows
        }

    def upsert_fundamental_snapshots(
        self,
        provider_mode: str,
        snapshots: list[FundamentalSnapshot],
    ) -> int:
        deduplicated = {
            (
                snapshot.instrument_id,
                snapshot.as_of_date,
                (snapshot.provider or "unknown").strip().lower(),
            ): snapshot
            for snapshot in snapshots
        }
        return self.replay_evidence(provider_mode).upsert_fundamentals(list(deduplicated.values()))

    def list_fundamental_snapshots(
        self,
        provider_mode: str,
        instrument_ids: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 50_000,
        max_dataset_revision: int | None = None,
    ) -> list[FundamentalSnapshot]:
        normalized_mode = provider_mode.strip().lower()
        with self.session_factory() as session:
            row_alias, revision_rank = _latest_revision_alias(
                FundamentalSnapshotRow,
                ("provider_mode", "instrument_id", "as_of_date", "source_provider"),
                max_dataset_revision=max_dataset_revision,
            )
            query = session.query(row_alias).filter(
                row_alias.provider_mode == normalized_mode,
                revision_rank == 1,
            )
            if instrument_ids:
                query = query.filter(row_alias.instrument_id.in_(instrument_ids))
            if start is not None:
                query = query.filter(row_alias.as_of_date >= start)
            if end is not None:
                query = query.filter(row_alias.as_of_date <= end)
            rows = (
                query.order_by(
                    row_alias.as_of_date.asc(),
                    row_alias.instrument_id.asc(),
                    row_alias.source_provider.asc(),
                )
                .limit(max(limit, 0))
                .all()
            )
            return [self._fundamental_snapshot_from_row(row) for row in rows]

    def fundamental_snapshot_stats(
        self,
        provider_mode: str,
        instrument_ids: list[str],
        end: date,
    ) -> dict[str, tuple[int, date | None, date | None]]:
        if not instrument_ids:
            return {}
        normalized_mode = provider_mode.strip().lower()
        with self.session_factory() as session:
            row_alias, revision_rank = _latest_revision_alias(
                FundamentalSnapshotRow,
                ("provider_mode", "instrument_id", "as_of_date", "source_provider"),
            )
            rows = (
                session.query(
                    row_alias.instrument_id,
                    func.count(row_alias.instrument_id),
                    func.min(row_alias.as_of_date),
                    func.max(row_alias.as_of_date),
                )
                .filter(
                    row_alias.provider_mode == normalized_mode,
                    row_alias.instrument_id.in_(instrument_ids),
                    row_alias.as_of_date <= end,
                    revision_rank == 1,
                )
                .group_by(row_alias.instrument_id)
                .all()
            )
        return {
            instrument_id: (int(count), first_date, last_date)
            for instrument_id, count, first_date, last_date in rows
        }

    def upsert_historical_evidence(
        self,
        provider_mode: str,
        bundle: HistoricalEvidenceBundle,
    ) -> dict[str, int]:
        return self.replay_evidence(provider_mode).upsert_point_in_time_evidence(bundle)

    def historical_evidence_stats(
        self,
        provider_mode: str,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> dict[str, HistoricalInstrumentEvidenceStats]:
        if not instrument_ids:
            return {}
        mode = provider_mode.strip().lower()
        result = {
            instrument_id: HistoricalInstrumentEvidenceStats() for instrument_id in instrument_ids
        }
        with self.session_factory() as session:
            tradability_alias, tradability_rank = _latest_revision_alias(
                HistoricalTradabilityRow,
                ("provider_mode", "instrument_id", "trade_date", "source_provider"),
            )
            tradability_rows = (
                session.query(
                    tradability_alias.instrument_id,
                    func.count(tradability_alias.trade_date),
                    func.min(tradability_alias.trade_date),
                    func.max(tradability_alias.trade_date),
                    func.sum(
                        case(
                            (tradability_alias.trading_status == "suspended", 1),
                            else_=0,
                        )
                    ),
                    func.sum(case((tradability_alias.is_st.is_(True), 1), else_=0)),
                )
                .filter(
                    tradability_alias.provider_mode == mode,
                    tradability_alias.instrument_id.in_(instrument_ids),
                    tradability_alias.trade_date >= start,
                    tradability_alias.trade_date <= end,
                    tradability_rank == 1,
                )
                .group_by(tradability_alias.instrument_id)
                .all()
            )
            profiles = (
                session.query(HistoricalInstrumentProfileRow)
                .filter(
                    HistoricalInstrumentProfileRow.provider_mode == mode,
                    HistoricalInstrumentProfileRow.instrument_id.in_(instrument_ids),
                    HistoricalInstrumentProfileRow.snapshot_date >= start,
                    HistoricalInstrumentProfileRow.snapshot_date <= end,
                )
                .order_by(
                    HistoricalInstrumentProfileRow.snapshot_date.asc(),
                    HistoricalInstrumentProfileRow.dataset_revision.desc(),
                )
                .all()
            )
            latest_profiles = {}
            for row in profiles:
                latest_profiles.setdefault((row.instrument_id, row.snapshot_date), row)
            profiles = list(latest_profiles.values())
            industry_alias, industry_rank = _latest_revision_alias(
                HistoricalIndustrySnapshotRow,
                ("provider_mode", "instrument_id", "snapshot_date", "source_provider"),
            )
            industry_rows = (
                session.query(
                    industry_alias.instrument_id,
                    func.count(industry_alias.snapshot_date),
                    func.min(industry_alias.snapshot_date),
                    func.max(industry_alias.snapshot_date),
                )
                .filter(
                    industry_alias.provider_mode == mode,
                    industry_alias.instrument_id.in_(instrument_ids),
                    industry_alias.snapshot_date >= start,
                    industry_alias.snapshot_date <= end,
                    industry_rank == 1,
                )
                .group_by(industry_alias.instrument_id)
                .all()
            )
            industry_names = (
                session.query(
                    industry_alias.instrument_id,
                    industry_alias.industry,
                )
                .filter(
                    industry_alias.provider_mode == mode,
                    industry_alias.instrument_id.in_(instrument_ids),
                    industry_alias.snapshot_date >= start,
                    industry_alias.snapshot_date <= end,
                    industry_rank == 1,
                )
                .distinct()
                .all()
            )
            membership_snapshot_alias, membership_snapshot_rank = _latest_revision_alias(
                HistoricalIndexSnapshotRow,
                ("provider_mode", "index_id", "snapshot_date", "source_provider"),
            )
            membership_rows = (
                session.query(
                    HistoricalIndexMembershipRow.instrument_id,
                    func.count(HistoricalIndexMembershipRow.index_id),
                )
                .join(
                    membership_snapshot_alias,
                    (
                        HistoricalIndexMembershipRow.provider_mode
                        == membership_snapshot_alias.provider_mode
                    )
                    & (HistoricalIndexMembershipRow.index_id == membership_snapshot_alias.index_id)
                    & (
                        HistoricalIndexMembershipRow.snapshot_date
                        == membership_snapshot_alias.snapshot_date
                    )
                    & (
                        HistoricalIndexMembershipRow.source_provider
                        == membership_snapshot_alias.source_provider
                    )
                    & (
                        HistoricalIndexMembershipRow.dataset_revision
                        == membership_snapshot_alias.dataset_revision
                    ),
                )
                .filter(
                    HistoricalIndexMembershipRow.provider_mode == mode,
                    HistoricalIndexMembershipRow.instrument_id.in_(instrument_ids),
                    HistoricalIndexMembershipRow.snapshot_date >= start,
                    HistoricalIndexMembershipRow.snapshot_date <= end,
                    membership_snapshot_alias.status == "ready",
                    membership_snapshot_rank == 1,
                )
                .group_by(HistoricalIndexMembershipRow.instrument_id)
                .all()
            )
            membership_ids = (
                session.query(
                    HistoricalIndexMembershipRow.instrument_id,
                    HistoricalIndexMembershipRow.index_id,
                )
                .join(
                    membership_snapshot_alias,
                    (
                        HistoricalIndexMembershipRow.provider_mode
                        == membership_snapshot_alias.provider_mode
                    )
                    & (HistoricalIndexMembershipRow.index_id == membership_snapshot_alias.index_id)
                    & (
                        HistoricalIndexMembershipRow.snapshot_date
                        == membership_snapshot_alias.snapshot_date
                    )
                    & (
                        HistoricalIndexMembershipRow.source_provider
                        == membership_snapshot_alias.source_provider
                    )
                    & (
                        HistoricalIndexMembershipRow.dataset_revision
                        == membership_snapshot_alias.dataset_revision
                    ),
                )
                .filter(
                    HistoricalIndexMembershipRow.provider_mode == mode,
                    HistoricalIndexMembershipRow.instrument_id.in_(instrument_ids),
                    HistoricalIndexMembershipRow.snapshot_date >= start,
                    HistoricalIndexMembershipRow.snapshot_date <= end,
                    membership_snapshot_alias.status == "ready",
                    membership_snapshot_rank == 1,
                )
                .distinct()
                .all()
            )

        for instrument_id, count, first_date, last_date, suspended, st_count in tradability_rows:
            stats = result[instrument_id]
            stats.tradability_rows = int(count)
            stats.first_tradability_date = first_date
            stats.last_tradability_date = last_date
            stats.suspended_rows = int(suspended or 0)
            stats.st_rows = int(st_count or 0)
        for row in profiles:
            stats = result[row.instrument_id]
            stats.profile_rows += 1
            stats.listing_date = row.listing_date
            stats.delisting_date = row.delisting_date
            stats.listing_status = row.listing_status
        for instrument_id, count, first_date, last_date in industry_rows:
            stats = result[instrument_id]
            stats.industry_rows = int(count)
            stats.first_industry_date = first_date
            stats.last_industry_date = last_date
        for instrument_id, industry in industry_names:
            result[instrument_id].industries.append(industry)
        for instrument_id, count in membership_rows:
            result[instrument_id].benchmark_membership_rows = int(count)
        for instrument_id, index_id in membership_ids:
            result[instrument_id].benchmark_ids.append(index_id)
        for stats in result.values():
            stats.industries.sort()
            stats.benchmark_ids.sort()
        return result

    def historical_index_snapshot_stats(
        self,
        provider_mode: str,
        start: date,
        end: date,
    ) -> HistoricalIndexCoverageStats:
        mode = provider_mode.strip().lower()
        with self.session_factory() as session:
            snapshot_alias, snapshot_rank = _latest_revision_alias(
                HistoricalIndexSnapshotRow,
                ("provider_mode", "index_id", "snapshot_date", "source_provider"),
            )
            row = (
                session.query(
                    func.count(snapshot_alias.index_id),
                    func.sum(case((snapshot_alias.status == "ready", 1), else_=0)),
                    func.sum(case((snapshot_alias.status == "failed", 1), else_=0)),
                    func.min(snapshot_alias.snapshot_date),
                    func.max(snapshot_alias.snapshot_date),
                )
                .filter(
                    snapshot_alias.provider_mode == mode,
                    snapshot_alias.snapshot_date >= start,
                    snapshot_alias.snapshot_date <= end,
                    snapshot_rank == 1,
                )
                .one()
            )
            index_ids = [
                value
                for (value,) in (
                    session.query(snapshot_alias.index_id)
                    .filter(
                        snapshot_alias.provider_mode == mode,
                        snapshot_alias.snapshot_date >= start,
                        snapshot_alias.snapshot_date <= end,
                        snapshot_rank == 1,
                    )
                    .distinct()
                    .all()
                )
            ]
        return HistoricalIndexCoverageStats(
            total_snapshots=int(row[0] or 0),
            ready_snapshots=int(row[1] or 0),
            failed_snapshots=int(row[2] or 0),
            first_snapshot_date=row[3],
            last_snapshot_date=row[4],
            index_ids=sorted(index_ids),
        )

    def save_scan_run(
        self,
        provider: str,
        mode: str,
        symbols: list[str],
        result,
        snapshot_items: list[object] | None = None,
    ) -> ScanRunRecord:
        persisted_at = datetime.now(timezone.utc)
        run_id = f"scan-{persisted_at.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        item_source = snapshot_items if snapshot_items is not None else result.items
        item_by_instrument = {item.instrument_id: item for item in item_source}
        scanned = len(result.items)
        if mode == "full_market_batch":
            try:
                scanned = int(result.data_health["full_market_scanned_symbols"])
                total_symbols = int(result.data_health["full_market_total_symbols"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "full_market_batch ScanRun requires explicit scan-count evidence"
                ) from exc
            if scanned != total_symbols or total_symbols != len(symbols):
                raise ValueError(
                    "full_market_batch scan counts must equal the persisted symbol universe"
                )
        with self.session_factory() as session:
            started_at = _aware_health_datetime(
                result.data_health.get("full_market_scan_started_at")
            )
            completed_at = _aware_health_datetime(
                result.data_health.get("full_market_scan_completed_at")
            )
            if mode == "full_market_batch" and (
                started_at is None or completed_at is None
            ):
                raise ValueError(
                    "full_market_batch ScanRun requires scan start and completion timestamps"
                )
            started_at = started_at or persisted_at
            completed_at = completed_at or persisted_at
            if started_at > completed_at or completed_at > persisted_at:
                raise ValueError("ScanRun timestamps must be ordered and not in the future")
            run_row = ScanRunRow(
                run_id=run_id,
                provider=provider,
                mode=mode,
                symbols=json.dumps(symbols),
                scanned=scanned,
                cards=len(result.cards),
                data_health=json.dumps(result.data_health, sort_keys=True),
                started_at=started_at,
                completed_at=completed_at,
                created_at=persisted_at,
            )
            session.add(run_row)
            for card in result.cards:
                item = item_by_instrument.get(card.instrument_id)
                session.add(self._snapshot_row_from_card(run_id, card, item))
            session.commit()
            session.refresh(run_row)
            return self._scan_run_from_row(run_row)

    def list_scan_runs(self, limit: int = 20, provider: str | None = None) -> list[ScanRunRecord]:
        with self.session_factory() as session:
            query = session.query(ScanRunRow)
            if provider:
                query = query.filter(ScanRunRow.provider == provider)
            rows = (
                query.order_by(ScanRunRow.created_at.desc(), ScanRunRow.run_id.desc())
                .limit(limit)
                .all()
            )
            return [self._scan_run_from_row(row) for row in rows]

    def get_current_paper_model_cohort(
        self,
        provider: str,
    ) -> PaperModelCohortRecord | None:
        with self.session_factory() as session:
            rows = (
                session.query(ScanRunRow)
                .filter(
                    ScanRunRow.provider == provider,
                    ScanRunRow.mode == "full_market_batch",
                )
                .order_by(ScanRunRow.created_at.desc(), ScanRunRow.run_id.desc())
                .limit(50)
                .all()
            )
            for row in rows:
                try:
                    health = json.loads(row.data_health or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                if health.get("full_market_scan_complete") != "true":
                    continue
                cohort = paper_model_cohort_from_data_health(health)
                if cohort is not None:
                    return cohort
        return None

    def get_paper_model_cohorts_for_snapshots(
        self,
        snapshot_ids: Sequence[str],
    ) -> dict[str, PaperModelCohortRecord | None]:
        unique_ids = sorted(set(snapshot_ids))
        result: dict[str, PaperModelCohortRecord | None] = {
            snapshot_id: None for snapshot_id in unique_ids
        }
        if not unique_ids:
            return result
        with self.session_factory() as session:
            rows = (
                session.query(OpportunitySnapshotRow.snapshot_id, ScanRunRow.data_health)
                .join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                .filter(OpportunitySnapshotRow.snapshot_id.in_(unique_ids))
                .all()
            )
            for snapshot_id, raw_health in rows:
                try:
                    health = json.loads(raw_health or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                result[snapshot_id] = paper_model_cohort_from_data_health(health)
        return result

    def save_scan_result_cache(
        self,
        cache_key: str,
        provider: str,
        mode: str,
        symbols: list[str],
        payload: dict[str, object],
    ) -> ScanResultCacheRecord:
        cache_id = (
            f"scan-cache-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        )
        with self.session_factory() as session:
            row = ScanResultCacheRow(
                cache_id=cache_id,
                cache_key=cache_key,
                provider=provider,
                mode=mode,
                symbols=json.dumps(symbols),
                payload_json=json.dumps(payload, sort_keys=True),
            )
            session.add(row)
            session.flush()
            stale_ids = [
                cache_id
                for (cache_id,) in (
                    session.query(ScanResultCacheRow.cache_id)
                    .filter(ScanResultCacheRow.cache_key == cache_key)
                    .order_by(
                        ScanResultCacheRow.created_at.desc(),
                        ScanResultCacheRow.cache_id.desc(),
                    )
                    .offset(3)
                    .all()
                )
            ]
            if stale_ids:
                session.query(ScanResultCacheRow).filter(
                    ScanResultCacheRow.cache_id.in_(stale_ids)
                ).delete(synchronize_session=False)
            session.commit()
            session.refresh(row)
            return self._scan_result_cache_from_row(row)

    def get_recent_scan_result_cache(
        self,
        cache_key: str,
        max_age: timedelta,
    ) -> ScanResultCacheRecord | None:
        earliest = datetime.now(timezone.utc) - max_age
        with self.session_factory() as session:
            row = (
                session.query(ScanResultCacheRow)
                .filter(
                    ScanResultCacheRow.cache_key == cache_key,
                    ScanResultCacheRow.created_at >= earliest,
                )
                .order_by(ScanResultCacheRow.created_at.desc(), ScanResultCacheRow.cache_id.desc())
                .first()
            )
            if row is None:
                return None
            return self._scan_result_cache_from_row(row)

    def maintain_full_market_scan_checkpoints(
        self,
        *,
        retention_days: int = 14,
        dry_run: bool = True,
        now: datetime | None = None,
    ) -> ScanCheckpointMaintenanceReport:
        if retention_days < 1 or retention_days > 90:
            raise ValueError("retention_days must be between 1 and 90")
        current = now or datetime.now(timezone.utc)
        cutoff = current - timedelta(days=retention_days)
        prefix = "full_market_batch_checkpoint:"
        terminal_statuses = {"succeeded", "failed", "cancelled"}

        with self.session_factory() as session:
            job_statuses = {
                job_id: status
                for job_id, status in session.query(
                    FullMarketScanJobRow.job_id,
                    FullMarketScanJobRow.status,
                ).all()
            }
            active_job_ids = sorted(
                job_id
                for job_id, status in job_statuses.items()
                if status in {"queued", "running"}
            )
            rows = (
                session.query(
                    ScanResultCacheRow.cache_id,
                    ScanResultCacheRow.cache_key,
                    ScanResultCacheRow.created_at,
                    func.length(ScanResultCacheRow.payload_json),
                    func.length(ScanResultCacheRow.symbols),
                )
                .filter(ScanResultCacheRow.mode == "full_market_batch_checkpoint")
                .all()
            )

            protected_active_rows = 0
            protected_recent_rows = 0
            protected_unrecognized_rows = 0
            eligible_ids: list[str] = []
            eligible_succeeded_rows = 0
            eligible_expired_terminal_rows = 0
            eligible_payload_bytes = 0
            for cache_id, cache_key, created_at, payload_bytes, symbols_bytes in rows:
                job_id = _full_market_checkpoint_job_id(cache_key, prefix=prefix)
                status = job_statuses.get(job_id) if job_id is not None else None
                if status in {"queued", "running"}:
                    protected_active_rows += 1
                    continue
                if status == "succeeded":
                    eligible_succeeded_rows += 1
                    eligible_ids.append(cache_id)
                    eligible_payload_bytes += int(payload_bytes or 0) + int(symbols_bytes or 0)
                    continue
                cache_created_at = (
                    created_at.replace(tzinfo=timezone.utc)
                    if created_at.tzinfo is None or created_at.utcoffset() is None
                    else created_at.astimezone(timezone.utc)
                )
                if cache_created_at >= cutoff:
                    protected_recent_rows += 1
                    continue
                if status not in terminal_statuses:
                    protected_unrecognized_rows += 1
                    continue
                eligible_expired_terminal_rows += 1
                eligible_ids.append(cache_id)
                eligible_payload_bytes += int(payload_bytes or 0) + int(symbols_bytes or 0)

            deleted_rows = 0
            deleted_payload_bytes = 0
            if not dry_run and eligible_ids:
                deleted_rows = (
                    session.query(ScanResultCacheRow)
                    .filter(ScanResultCacheRow.cache_id.in_(eligible_ids))
                    .delete(synchronize_session=False)
                )
                deleted_payload_bytes = eligible_payload_bytes
                session.commit()

            page_size = int(session.execute(text("PRAGMA page_size")).scalar_one())
            page_count = int(session.execute(text("PRAGMA page_count")).scalar_one())
            freelist_count = int(session.execute(text("PRAGMA freelist_count")).scalar_one())
            return ScanCheckpointMaintenanceReport(
                dry_run=dry_run,
                retention_days=retention_days,
                cutoff=cutoff,
                active_job_ids=active_job_ids,
                total_checkpoint_rows=len(rows),
                protected_active_rows=protected_active_rows,
                protected_recent_rows=protected_recent_rows,
                protected_unrecognized_rows=protected_unrecognized_rows,
                eligible_rows=len(eligible_ids),
                eligible_succeeded_rows=eligible_succeeded_rows,
                eligible_expired_terminal_rows=eligible_expired_terminal_rows,
                eligible_payload_bytes=eligible_payload_bytes,
                deleted_rows=deleted_rows,
                deleted_payload_bytes=deleted_payload_bytes,
                sqlite_page_size=page_size,
                sqlite_page_count=page_count,
                sqlite_freelist_count=freelist_count,
                sqlite_reusable_bytes=page_size * freelist_count,
            )

    def delete_succeeded_full_market_scan_checkpoints(self, job_id: str) -> int:
        """Discard recovery-only checkpoints after the final result is durable."""

        with self.session_factory() as session:
            job = session.get(FullMarketScanJobRow, job_id)
            if job is None or job.status != "succeeded":
                return 0
            deleted = (
                session.query(ScanResultCacheRow)
                .filter(
                    ScanResultCacheRow.mode == "full_market_batch_checkpoint",
                    ScanResultCacheRow.cache_key.like(
                        f"full_market_batch_checkpoint:{job_id}:%"
                    ),
                )
                .delete(synchronize_session=False)
            )
            session.commit()
            return int(deleted)

    def update_scan_result_cache_payload(
        self,
        cache_id: str,
        payload: dict[str, object],
    ) -> ScanResultCacheRecord | None:
        """Replace derived cache content without changing its market-data age."""

        with self.session_factory() as session:
            row = session.get(ScanResultCacheRow, cache_id)
            if row is None:
                return None
            row.payload_json = json.dumps(payload, sort_keys=True)
            session.commit()
            session.refresh(row)
            return self._scan_result_cache_from_row(row)

    def get_latest_scan_result_cache_by_modes(
        self,
        provider: str,
        modes: set[str],
        max_age: timedelta,
    ) -> ScanResultCacheRecord | None:
        earliest = datetime.now(timezone.utc) - max_age
        normalized_modes = {mode.strip() for mode in modes if mode.strip()}
        if not normalized_modes:
            return None
        with self.session_factory() as session:
            row = (
                session.query(ScanResultCacheRow)
                .filter(
                    ScanResultCacheRow.provider == provider,
                    ScanResultCacheRow.mode.in_(normalized_modes),
                    ScanResultCacheRow.created_at >= earliest,
                )
                .order_by(ScanResultCacheRow.created_at.desc(), ScanResultCacheRow.cache_id.desc())
                .first()
            )
            if row is None:
                return None
            return self._scan_result_cache_from_row(row)

    def get_recent_scan_run_with_snapshots(
        self,
        provider: str,
        scanned: int,
        max_age: timedelta,
    ) -> ScanRunSnapshotBundle | None:
        earliest = datetime.now(timezone.utc) - max_age
        with self.session_factory() as session:
            run_row = (
                session.query(ScanRunRow)
                .filter(
                    ScanRunRow.provider == provider,
                    ScanRunRow.scanned == scanned,
                    ScanRunRow.created_at >= earliest,
                )
                .order_by(ScanRunRow.created_at.desc(), ScanRunRow.run_id.desc())
                .first()
            )
            if run_row is None:
                return None
            snapshot_rows = (
                session.query(OpportunitySnapshotRow)
                .filter(OpportunitySnapshotRow.run_id == run_row.run_id)
                .order_by(
                    OpportunitySnapshotRow.rank_score.desc(),
                    OpportunitySnapshotRow.score.desc(),
                    OpportunitySnapshotRow.snapshot_id.desc(),
                )
                .all()
            )
            return ScanRunSnapshotBundle(
                run=self._scan_run_from_row(run_row),
                snapshots=[self._opportunity_snapshot_from_row(row) for row in snapshot_rows],
            )

    def get_latest_complete_daily_scan_with_snapshots(
        self,
        *,
        provider: str,
        signal_date: date,
        minimum_scanned: int,
    ) -> ScanRunSnapshotBundle | None:
        """Return one strictly complete full-market batch for one signal date."""

        if minimum_scanned < 1:
            raise ValueError("minimum_scanned must be positive")
        with self.session_factory() as session:
            run_rows = (
                session.query(ScanRunRow)
                .filter(
                    ScanRunRow.provider == provider,
                    ScanRunRow.mode == "full_market_batch",
                    ScanRunRow.scanned >= minimum_scanned,
                )
                .order_by(ScanRunRow.created_at.desc(), ScanRunRow.run_id.desc())
                .all()
            )
            run_row = None
            for candidate in run_rows:
                try:
                    symbols = json.loads(candidate.symbols or "[]")
                    health = json.loads(candidate.data_health or "{}")
                    total_symbols = int(health["full_market_total_symbols"])
                    scanned_symbols = int(health["full_market_scanned_symbols"])
                    total_batches = int(health["full_market_total_batches"])
                    completed_batches = int(health["full_market_completed_batches"])
                    error_count = int(health["full_market_error_count"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if (
                    not isinstance(symbols, list)
                    or not symbols
                    or not all(isinstance(value, str) and value for value in symbols)
                    or len(set(symbols)) != len(symbols)
                    or not isinstance(health, dict)
                    or health.get("full_market_scan_mode") != "full_market_batch"
                    or health.get("full_market_batches_complete") != "true"
                    or health.get("full_market_scan_complete") != "true"
                    or health.get("full_market_signal_date") != signal_date.isoformat()
                    or candidate.scanned != len(symbols)
                    or scanned_symbols != len(symbols)
                    or total_symbols != len(symbols)
                    or total_symbols < minimum_scanned
                    or total_batches < 1
                    or completed_batches != total_batches
                    or error_count != 0
                    or candidate.started_at is None
                    or candidate.completed_at is None
                    or candidate.started_at > candidate.completed_at
                    or candidate.completed_at > candidate.created_at
                ):
                    continue
                observed_dates = [
                    value
                    for (value,) in (
                        session.query(OpportunitySnapshotRow.signal_date)
                        .filter(OpportunitySnapshotRow.run_id == candidate.run_id)
                        .distinct()
                        .all()
                    )
                ]
                if any(value is None for value in observed_dates):
                    continue
                if observed_dates and set(observed_dates) != {signal_date}:
                    continue
                snapshot_count = (
                    session.query(func.count(OpportunitySnapshotRow.snapshot_id))
                    .filter(OpportunitySnapshotRow.run_id == candidate.run_id)
                    .scalar()
                )
                if int(snapshot_count or 0) != candidate.cards:
                    continue
                run_row = candidate
                break
            if run_row is None:
                return None
            snapshot_rows = (
                session.query(OpportunitySnapshotRow)
                .filter(
                    OpportunitySnapshotRow.run_id == run_row.run_id,
                    OpportunitySnapshotRow.signal_date == signal_date,
                )
                .order_by(
                    OpportunitySnapshotRow.rank_score.desc(),
                    OpportunitySnapshotRow.strategy_score.desc(),
                    OpportunitySnapshotRow.score.desc(),
                    OpportunitySnapshotRow.snapshot_id.desc(),
                )
                .all()
            )
            return ScanRunSnapshotBundle(
                run=self._scan_run_from_row(run_row),
                snapshots=[self._opportunity_snapshot_from_row(row) for row in snapshot_rows],
            )

    def create_full_market_scan_job(
        self,
        provider: str,
        symbols: list[str],
        batch_size: int,
        include_etfs: bool,
        sync_if_empty: bool,
    ) -> FullMarketScanJobRecord:
        # A new job supersedes recovery data from successful jobs. Failed and
        # cancelled jobs retain their checkpoints for the normal diagnosis window.
        self.maintain_full_market_scan_checkpoints(retention_days=14, dry_run=False)
        now = datetime.now(timezone.utc)
        total_symbols = len(symbols)
        total_batches = (total_symbols + batch_size - 1) // batch_size if batch_size > 0 else 0
        job_id = f"full-scan-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        with self.session_factory() as session:
            row = FullMarketScanJobRow(
                job_id=job_id,
                provider=provider,
                status="queued",
                batch_size=batch_size,
                total_symbols=total_symbols,
                scanned_symbols=0,
                total_batches=total_batches,
                completed_batches=0,
                cards=0,
                errors=0,
                include_etfs=include_etfs,
                sync_if_empty=sync_if_empty,
                symbols=json.dumps(symbols),
                message="Queued full-market batch scan",
                data_health=json.dumps({}),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._full_market_scan_job_from_row(row)

    def update_full_market_scan_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        scanned_symbols: int | None = None,
        completed_batches: int | None = None,
        cards: int | None = None,
        errors: int | None = None,
        message: str | None = None,
        data_health: dict[str, str] | None = None,
        result_cache_key: str | None = None,
    ) -> FullMarketScanJobRecord | None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            row = session.get(FullMarketScanJobRow, job_id)
            if row is None:
                return None
            if status is not None:
                row.status = status
                if status in {"queued", "running"}:
                    row.finished_at = None
                if status == "running" and row.started_at is None:
                    row.started_at = now
                if status in {"succeeded", "failed", "cancelled"}:
                    row.finished_at = now
            if scanned_symbols is not None:
                row.scanned_symbols = scanned_symbols
            if completed_batches is not None:
                row.completed_batches = completed_batches
            if cards is not None:
                row.cards = cards
            if errors is not None:
                row.errors = errors
            if message is not None:
                row.message = message
            if data_health is not None:
                row.data_health = json.dumps(data_health, sort_keys=True)
            if result_cache_key is not None:
                row.result_cache_key = result_cache_key
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._full_market_scan_job_from_row(row)

    def get_full_market_scan_job(self, job_id: str) -> FullMarketScanJobRecord | None:
        with self.session_factory() as session:
            row = session.get(FullMarketScanJobRow, job_id)
            if row is None:
                return None
            return self._full_market_scan_job_from_row(row)

    def get_latest_full_market_scan_job(
        self,
        provider: str | None = None,
    ) -> FullMarketScanJobRecord | None:
        with self.session_factory() as session:
            query = session.query(FullMarketScanJobRow)
            if provider:
                query = query.filter(FullMarketScanJobRow.provider == provider)
            row = query.order_by(
                FullMarketScanJobRow.created_at.desc(),
                FullMarketScanJobRow.job_id.desc(),
            ).first()
            if row is None:
                return None
            return self._full_market_scan_job_from_row(row)

    def get_latest_succeeded_full_market_scan_job(
        self,
        provider: str | None = None,
    ) -> FullMarketScanJobRecord | None:
        with self.session_factory() as session:
            query = session.query(FullMarketScanJobRow).filter(
                FullMarketScanJobRow.status == "succeeded"
            )
            if provider:
                query = query.filter(FullMarketScanJobRow.provider == provider)
            row = query.order_by(
                FullMarketScanJobRow.created_at.desc(),
                FullMarketScanJobRow.job_id.desc(),
            ).first()
            if row is None:
                return None
            return self._full_market_scan_job_from_row(row)

    def create_historical_backfill_job(
        self,
        provider: str,
        symbols: list[str],
        start: date,
        end: date,
        *,
        data_health: dict[str, str] | None = None,
    ) -> HistoricalBackfillJobRecord:
        now = datetime.now(timezone.utc)
        job_id = f"history-backfill-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        with self.session_factory() as session:
            row = HistoricalBackfillJobRow(
                job_id=job_id,
                provider=provider,
                status="queued",
                start_date=start,
                end_date=end,
                symbols=json.dumps(symbols),
                total_symbols=len(symbols),
                processed_symbols=0,
                succeeded_symbols=0,
                failed_symbols=0,
                rows_written=0,
                fundamental_rows_written=0,
                errors_json="[]",
                data_health=json.dumps(data_health or {}, sort_keys=True),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._historical_backfill_job_from_row(row)

    def update_historical_backfill_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        processed_symbols: int | None = None,
        succeeded_symbols: int | None = None,
        failed_symbols: int | None = None,
        rows_written: int | None = None,
        fundamental_rows_written: int | None = None,
        current_instrument: str | None = None,
        symbols: list[str] | None = None,
        total_symbols: int | None = None,
        errors: list[str] | None = None,
        data_health: dict[str, str] | None = None,
    ) -> HistoricalBackfillJobRecord | None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            row = session.get(HistoricalBackfillJobRow, job_id)
            if row is None:
                return None
            if status is not None:
                row.status = status
                if status == "running" and row.started_at is None:
                    row.started_at = now
                if status in {"succeeded", "succeeded_with_errors", "failed", "cancelled"}:
                    row.finished_at = now
            if processed_symbols is not None:
                row.processed_symbols = processed_symbols
            if succeeded_symbols is not None:
                row.succeeded_symbols = succeeded_symbols
            if failed_symbols is not None:
                row.failed_symbols = failed_symbols
            if rows_written is not None:
                row.rows_written = rows_written
            if fundamental_rows_written is not None:
                row.fundamental_rows_written = fundamental_rows_written
            if current_instrument is not None:
                row.current_instrument = current_instrument
            if symbols is not None:
                row.symbols = json.dumps(symbols)
            if total_symbols is not None:
                row.total_symbols = total_symbols
            if errors is not None:
                row.errors_json = json.dumps(errors)
            if data_health is not None:
                row.data_health = json.dumps(data_health, sort_keys=True)
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._historical_backfill_job_from_row(row)

    def get_historical_backfill_job(
        self,
        job_id: str,
    ) -> HistoricalBackfillJobRecord | None:
        with self.session_factory() as session:
            row = session.get(HistoricalBackfillJobRow, job_id)
            return self._historical_backfill_job_from_row(row) if row is not None else None

    def get_latest_historical_backfill_job(
        self,
        provider: str | None = None,
    ) -> HistoricalBackfillJobRecord | None:
        with self.session_factory() as session:
            query = session.query(HistoricalBackfillJobRow)
            if provider:
                query = query.filter(HistoricalBackfillJobRow.provider == provider)
            row = query.order_by(
                HistoricalBackfillJobRow.created_at.desc(),
                HistoricalBackfillJobRow.job_id.desc(),
            ).first()
            return self._historical_backfill_job_from_row(row) if row is not None else None

    def save_walk_forward_run(
        self,
        result,
        *,
        status: str = "succeeded",
    ) -> WalkForwardRunRecord:
        payload = result.model_dump(mode="json")
        manifest_payload = payload.get("experiment_manifest")
        current_manifest = _current_walk_forward_manifest(manifest_payload)
        is_current_result = (
            current_manifest is not None
            or
            payload.get("result_digest_schema") == "walk-forward-result-digest-v2"
            or str(getattr(result, "reproducibility_digest", "")).startswith("v2")
        )
        if is_current_result:
            from qagent.backtesting.experiment import walk_forward_manifest_digest_is_valid
            from qagent.backtesting.walk_forward import (
                walk_forward_selection_result_digest_is_valid,
            )

            if not walk_forward_manifest_digest_is_valid(result.experiment_manifest):
                raise ValueError("walk-forward experiment manifest integrity check failed")
            if not walk_forward_selection_result_digest_is_valid(payload):
                raise ValueError("walk-forward result reproducibility digest check failed")
        data_health = dict(result.data_health)
        stored_data_health = dict(data_health)
        if is_current_result:
            stored_data_health[WALK_FORWARD_RUN_STORAGE_SCHEMA_KEY] = (
                WALK_FORWARD_RUN_STORAGE_SCHEMA
            )
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            row = session.get(WalkForwardRunRow, result.owner_run_id)
            values = {
                "provider": result.provider_mode,
                "status": status,
                "start_date": result.start_date,
                "end_date": result.end_date,
                "dataset_revision": result.dataset_revision,
                "rebalance_step_sessions": result.rebalance_step_sessions,
                "lookback_days": int(data_health.get("walk_forward_lookback_days", 0) or 0),
                "snapshot_count": len(result.snapshots),
                "top_5_trade_count": result.top_5_metrics.trade_count,
                "top_10_trade_count": result.top_10_metrics.trade_count,
                "top_5_return_pct": result.top_5_metrics.total_return_pct,
                "top_10_return_pct": result.top_10_metrics.total_return_pct,
                "top_5_oos_trades": int(data_health.get("walk_forward_top_5_oos_trades", 0) or 0),
                "top_10_oos_trades": int(data_health.get("walk_forward_top_10_oos_trades", 0) or 0),
                "top_5_oos_gate": data_health.get("walk_forward_top_5_oos_gate", "insufficient"),
                "top_10_oos_gate": data_health.get("walk_forward_top_10_oos_gate", "insufficient"),
                "reproducibility_digest": result.reproducibility_digest,
                "payload_json": json.dumps(payload, ensure_ascii=True, sort_keys=True),
                "data_health": json.dumps(
                    stored_data_health,
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                "updated_at": now,
            }
            if row is None:
                row = WalkForwardRunRow(
                    run_id=result.owner_run_id,
                    created_at=now,
                    **values,
                )
                session.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            session.commit()
            session.refresh(row)
            return self._walk_forward_run_from_row(row)

    def create_walk_forward_job(
        self,
        *,
        job_id: str,
        provider: str,
        start: date,
        end: date,
        dataset_revision: int,
        rebalance_step_sessions: int,
        lookback_days: int,
        total_snapshots: int,
        experiment_manifest: dict[str, object],
    ) -> WalkForwardJobRecord:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            row = WalkForwardJobRow(
                job_id=job_id,
                provider=provider,
                status="queued",
                phase="queued",
                start_date=start,
                end_date=end,
                dataset_revision=dataset_revision,
                rebalance_step_sessions=rebalance_step_sessions,
                lookback_days=lookback_days,
                total_snapshots=total_snapshots,
                processed_snapshots=0,
                checkpoints_json="[]",
                experiment_manifest_json=json.dumps(
                    experiment_manifest,
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                created_at=now,
                updated_at=now,
            )
            row.checkpoints_json = _encode_walk_forward_checkpoints(
                row,
                manifest_payload=experiment_manifest,
                checkpoints=[],
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._walk_forward_job_from_row(row)

    def update_walk_forward_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        processed_snapshots: int | None = None,
        current_date: date | None = None,
        lease_maintenance_count: int | None = None,
        lease_recovery_count: int | None = None,
        last_lease_heartbeat_at: datetime | None = None,
        checkpoints: list[dict[str, object]] | None = None,
        experiment_manifest: dict[str, object] | None = None,
        result_run_id: str | None = None,
        error: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        clear_terminal_state: bool = False,
    ) -> WalkForwardJobRecord:
        with self.session_factory() as session:
            row = session.get(WalkForwardJobRow, job_id)
            if row is None:
                raise ValueError(f"walk-forward job not found: {job_id}")
            stored_manifest_payload = json.loads(row.experiment_manifest_json or "{}")
            stored_checkpoints = _decode_walk_forward_checkpoints(
                row,
                manifest_payload=stored_manifest_payload,
            )
            next_manifest_payload = (
                experiment_manifest
                if experiment_manifest is not None
                else stored_manifest_payload
            )
            stored_current_manifest = _current_walk_forward_manifest(
                stored_manifest_payload
            )
            next_current_manifest = _current_walk_forward_manifest(next_manifest_payload)
            if (
                experiment_manifest is not None
                and stored_checkpoints
                and (stored_current_manifest is not None or next_current_manifest is not None)
            ):
                from qagent.backtesting.experiment import (
                    walk_forward_selection_manifests_semantically_compatible,
                )

                if (
                    stored_current_manifest is None
                    or next_current_manifest is None
                    or not walk_forward_selection_manifests_semantically_compatible(
                        stored_current_manifest,
                        next_current_manifest,
                    )
                ):
                    raise ValueError(
                        "walk-forward checkpoints cannot be rebound to a different selection plan"
                    )
            values = {
                "status": status,
                "phase": phase,
                "processed_snapshots": processed_snapshots,
                "current_date": current_date,
                "lease_maintenance_count": lease_maintenance_count,
                "lease_recovery_count": lease_recovery_count,
                "last_lease_heartbeat_at": last_lease_heartbeat_at,
                "result_run_id": result_run_id,
                "error": error,
                "started_at": started_at,
                "finished_at": finished_at,
            }
            for key, value in values.items():
                if value is not None:
                    setattr(row, key, value)
            if clear_terminal_state:
                row.result_run_id = None
                row.error = None
                row.finished_at = None
            if experiment_manifest is not None:
                row.experiment_manifest_json = json.dumps(
                    experiment_manifest,
                    ensure_ascii=True,
                    sort_keys=True,
                )
            if checkpoints is not None or experiment_manifest is not None:
                row.checkpoints_json = _encode_walk_forward_checkpoints(
                    row,
                    manifest_payload=next_manifest_payload,
                    checkpoints=(
                        checkpoints if checkpoints is not None else stored_checkpoints
                    ),
                )
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return self._walk_forward_job_from_row(row)

    def get_walk_forward_job(self, job_id: str) -> WalkForwardJobRecord | None:
        with self.session_factory() as session:
            row = session.get(WalkForwardJobRow, job_id)
            return self._walk_forward_job_from_row(row) if row is not None else None

    def fail_walk_forward_job_integrity(
        self,
        job_id: str,
        *,
        error: str,
    ) -> None:
        """Fail a corrupt job without deserializing its untrusted checkpoint payload."""

        with self.session_factory() as session:
            row = session.get(WalkForwardJobRow, job_id)
            if row is None or row.status == "cancelled":
                return
            row.status = "failed"
            row.phase = "failed"
            row.error = error
            row.finished_at = datetime.now(timezone.utc)
            row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def list_walk_forward_jobs(
        self,
        *,
        provider: str | None = None,
        limit: int = 20,
    ) -> list[WalkForwardJobRecord]:
        bounded_limit = max(1, min(limit, 100))
        with self.session_factory() as session:
            query = session.query(WalkForwardJobRow)
            if provider:
                query = query.filter(WalkForwardJobRow.provider == provider)
            rows = (
                query.order_by(
                    WalkForwardJobRow.created_at.desc(),
                    WalkForwardJobRow.job_id.desc(),
                )
                .limit(bounded_limit)
                .all()
            )
            return [self._walk_forward_job_from_row(row) for row in rows]

    def get_walk_forward_run(self, run_id: str) -> WalkForwardRunRecord | None:
        with self.session_factory() as session:
            row = session.get(WalkForwardRunRow, run_id)
            return self._walk_forward_run_from_row(row) if row is not None else None

    def list_walk_forward_runs(
        self,
        *,
        provider: str | None = None,
        limit: int = 20,
    ) -> list[WalkForwardRunRecord]:
        bounded_limit = max(1, min(limit, 100))
        with self.session_factory() as session:
            query = session.query(WalkForwardRunRow)
            if provider:
                query = query.filter(WalkForwardRunRow.provider == provider)
            rows = (
                query.order_by(
                    WalkForwardRunRow.created_at.desc(),
                    WalkForwardRunRow.run_id.desc(),
                )
                .limit(bounded_limit)
                .all()
            )
            return [self._walk_forward_run_from_row(row) for row in rows]

    def list_opportunity_snapshots(
        self,
        instrument_id: str | None = None,
        limit: int = 50,
        provider: str | None = None,
        require_signal_date: bool = False,
    ) -> list[OpportunitySnapshotRecord]:
        with self.session_factory() as session:
            query = session.query(OpportunitySnapshotRow)
            if instrument_id:
                query = query.filter(OpportunitySnapshotRow.instrument_id == instrument_id)
            if require_signal_date:
                query = query.filter(OpportunitySnapshotRow.signal_date.isnot(None))
            if provider:
                query = query.join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                query = query.filter(ScanRunRow.provider == provider)
            rows = (
                query.order_by(
                    OpportunitySnapshotRow.created_at.desc(),
                    OpportunitySnapshotRow.snapshot_id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [self._opportunity_snapshot_from_row(row) for row in rows]

    def get_opportunity_snapshot(
        self,
        snapshot_id: str,
    ) -> OpportunitySnapshotRecord | None:
        with self.session_factory() as session:
            row = session.get(OpportunitySnapshotRow, snapshot_id)
            return self._opportunity_snapshot_from_row(row) if row is not None else None

    def opportunity_snapshots_belong_to_provider(
        self,
        snapshot_ids: Sequence[str],
        *,
        provider: str,
    ) -> bool:
        unique_ids = sorted(set(snapshot_ids))
        if len(unique_ids) != len(snapshot_ids):
            return False
        if not unique_ids:
            return True
        with self.session_factory() as session:
            matching = (
                session.query(func.count(OpportunitySnapshotRow.snapshot_id))
                .join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                .filter(
                    OpportunitySnapshotRow.snapshot_id.in_(unique_ids),
                    ScanRunRow.provider == provider,
                )
                .scalar()
            )
            return int(matching or 0) == len(unique_ids)

    def list_top_daily_opportunity_snapshots(
        self,
        *,
        start: date,
        end: date,
        top_n: int = 5,
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]:
        bounded_top_n = max(1, min(top_n, 500))
        with self.session_factory() as session:
            per_instrument = session.query(
                OpportunitySnapshotRow.snapshot_id.label("snapshot_id"),
                OpportunitySnapshotRow.signal_date.label("signal_date"),
                OpportunitySnapshotRow.instrument_id.label("instrument_id"),
                OpportunitySnapshotRow.rank_score.label("rank_score"),
                OpportunitySnapshotRow.strategy_score.label("strategy_score"),
                OpportunitySnapshotRow.score.label("score"),
                OpportunitySnapshotRow.created_at.label("created_at"),
                func.row_number()
                .over(
                    partition_by=(
                        OpportunitySnapshotRow.signal_date,
                        OpportunitySnapshotRow.instrument_id,
                    ),
                    order_by=(
                        OpportunitySnapshotRow.rank_score.desc(),
                        OpportunitySnapshotRow.strategy_score.desc(),
                        OpportunitySnapshotRow.score.desc(),
                        OpportunitySnapshotRow.created_at.desc(),
                        OpportunitySnapshotRow.snapshot_id.desc(),
                    ),
                )
                .label("instrument_rank"),
            ).filter(
                OpportunitySnapshotRow.signal_date.isnot(None),
                OpportunitySnapshotRow.signal_date >= start,
                OpportunitySnapshotRow.signal_date <= end,
            )
            if provider:
                per_instrument = per_instrument.join(
                    ScanRunRow,
                    OpportunitySnapshotRow.run_id == ScanRunRow.run_id,
                ).filter(ScanRunRow.provider == provider)
            unique_instruments = per_instrument.subquery()
            per_day = (
                session.query(
                    unique_instruments.c.snapshot_id,
                    unique_instruments.c.signal_date,
                    func.row_number()
                    .over(
                        partition_by=unique_instruments.c.signal_date,
                        order_by=(
                            unique_instruments.c.rank_score.desc(),
                            unique_instruments.c.strategy_score.desc(),
                            unique_instruments.c.score.desc(),
                            unique_instruments.c.created_at.desc(),
                            unique_instruments.c.snapshot_id.desc(),
                        ),
                    )
                    .label("daily_rank"),
                )
                .filter(unique_instruments.c.instrument_rank == 1)
                .subquery()
            )
            rows = (
                session.query(OpportunitySnapshotRow)
                .join(per_day, OpportunitySnapshotRow.snapshot_id == per_day.c.snapshot_id)
                .filter(per_day.c.daily_rank <= bounded_top_n)
                .order_by(
                    OpportunitySnapshotRow.signal_date.desc(),
                    OpportunitySnapshotRow.rank_score.desc(),
                )
                .all()
            )
            return [self._opportunity_snapshot_from_row(row) for row in rows]

    def list_latest_signal_opportunity_snapshots(
        self,
        limit: int = 50,
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]:
        with self.session_factory() as session:
            latest_query = session.query(func.max(OpportunitySnapshotRow.signal_date)).filter(
                OpportunitySnapshotRow.signal_date.isnot(None),
                OpportunitySnapshotRow.trigger_price.isnot(None),
            )
            if provider:
                latest_query = latest_query.join(
                    ScanRunRow,
                    OpportunitySnapshotRow.run_id == ScanRunRow.run_id,
                ).filter(ScanRunRow.provider == provider)
            latest_signal_date = latest_query.scalar()
            if latest_signal_date is None:
                return []

            query = session.query(OpportunitySnapshotRow).filter(
                OpportunitySnapshotRow.signal_date == latest_signal_date,
                OpportunitySnapshotRow.trigger_price.isnot(None),
            )
            if provider:
                query = query.join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                query = query.filter(ScanRunRow.provider == provider)
            rows = query.order_by(
                OpportunitySnapshotRow.rank_score.desc(),
                OpportunitySnapshotRow.score.desc(),
                OpportunitySnapshotRow.created_at.desc(),
                OpportunitySnapshotRow.snapshot_id.desc(),
            ).all()
            snapshots: list[OpportunitySnapshotRecord] = []
            seen_instruments: set[str] = set()
            for row in rows:
                if row.instrument_id in seen_instruments:
                    continue
                snapshots.append(self._opportunity_snapshot_from_row(row))
                seen_instruments.add(row.instrument_id)
                if len(snapshots) >= limit:
                    break
            return snapshots

    def list_latest_opportunity_snapshots_by_card_ids(
        self,
        card_ids: list[str],
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]:
        ordered_ids = [card_id for card_id in _dedupe_strings(card_ids) if card_id]
        if not ordered_ids:
            return []
        with self.session_factory() as session:
            query = session.query(OpportunitySnapshotRow).filter(
                OpportunitySnapshotRow.card_id.in_(ordered_ids),
                OpportunitySnapshotRow.trigger_price.isnot(None),
            )
            if provider:
                query = query.join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                query = query.filter(ScanRunRow.provider == provider)
            rows = query.order_by(
                OpportunitySnapshotRow.created_at.desc(),
                OpportunitySnapshotRow.snapshot_id.desc(),
            ).all()
            latest_by_card_id: dict[str, OpportunitySnapshotRecord] = {}
            for row in rows:
                if row.card_id not in latest_by_card_id:
                    latest_by_card_id[row.card_id] = self._opportunity_snapshot_from_row(row)
            return [
                latest_by_card_id[card_id]
                for card_id in ordered_ids
                if card_id in latest_by_card_id
            ]

    def list_latest_opportunity_snapshots_by_instruments(
        self,
        instrument_ids: list[str],
        provider: str | None = None,
    ) -> list[OpportunitySnapshotRecord]:
        ordered_ids = [
            instrument_id for instrument_id in _dedupe_strings(instrument_ids) if instrument_id
        ]
        if not ordered_ids:
            return []
        with self.session_factory() as session:
            query = session.query(OpportunitySnapshotRow).filter(
                OpportunitySnapshotRow.instrument_id.in_(ordered_ids),
                OpportunitySnapshotRow.trigger_price.isnot(None),
            )
            if provider:
                query = query.join(ScanRunRow, OpportunitySnapshotRow.run_id == ScanRunRow.run_id)
                query = query.filter(ScanRunRow.provider == provider)
            rows = query.order_by(
                OpportunitySnapshotRow.created_at.desc(),
                OpportunitySnapshotRow.snapshot_id.desc(),
            ).all()
            latest_by_instrument: dict[str, OpportunitySnapshotRecord] = {}
            for row in rows:
                if row.instrument_id not in latest_by_instrument:
                    latest_by_instrument[row.instrument_id] = self._opportunity_snapshot_from_row(
                        row
                    )
            return [
                latest_by_instrument[instrument_id]
                for instrument_id in ordered_ids
                if instrument_id in latest_by_instrument
            ]

    def save_brief_run(self, brief) -> BriefRunRecord:
        brief_id = f"brief-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        payload = brief.model_dump(mode="json")
        with self.session_factory() as session:
            row = BriefRunRow(
                brief_id=brief_id,
                provider=brief.provider,
                symbols=json.dumps(brief.symbols),
                headline=brief.headline,
                opportunity_count=len(brief.top_opportunities),
                entry_watch_count=len(brief.entry_watch),
                risk_alert_count=len(brief.risk_alerts),
                catalyst_count=len(brief.catalyst_watch),
                validation_count=len(brief.strategy_validation),
                data_health=json.dumps(brief.data_health, sort_keys=True),
                brief_json=json.dumps(payload, sort_keys=True),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._brief_run_from_row(row)

    def list_brief_runs(self, limit: int = 20, provider: str | None = None) -> list[BriefRunRecord]:
        with self.session_factory() as session:
            query = session.query(BriefRunRow)
            if provider:
                query = query.filter(BriefRunRow.provider == provider)
            rows = (
                query.order_by(BriefRunRow.created_at.desc(), BriefRunRow.brief_id.desc())
                .limit(limit)
                .all()
            )
            return [self._brief_run_from_row(row) for row in rows]

    def get_brief_run(self, brief_id: str) -> BriefRunRecord | None:
        with self.session_factory() as session:
            row = session.get(BriefRunRow, brief_id)
            if row is None:
                return None
            return self._brief_run_from_row(row)

    def enqueue_brief_delivery(
        self,
        brief_run: BriefRunRecord,
        channel: str = "markdown",
        recipient: str | None = None,
        markdown: str = "",
        idempotency_key: str | None = None,
    ) -> DeliveryOutboxRecord:
        delivery_id = (
            f"delivery-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        )
        payload = {
            "brief_id": brief_run.brief_id,
            "provider": brief_run.provider,
            "symbols": brief_run.symbols,
            "opportunity_count": brief_run.opportunity_count,
            "entry_watch_count": brief_run.entry_watch_count,
            "risk_alert_count": brief_run.risk_alert_count,
            "catalyst_count": brief_run.catalyst_count,
            "validation_count": brief_run.validation_count,
        }
        return self.enqueue_delivery(
            subject=brief_run.headline,
            markdown=markdown,
            channel=channel,
            recipient=recipient,
            payload=payload,
            brief_id=brief_run.brief_id,
            idempotency_key=idempotency_key,
            delivery_id=delivery_id,
        )

    def enqueue_delivery(
        self,
        subject: str,
        markdown: str,
        channel: str = "markdown",
        recipient: str | None = None,
        payload: dict[str, object] | None = None,
        brief_id: str | None = None,
        idempotency_key: str | None = None,
        delivery_id: str | None = None,
    ) -> DeliveryOutboxRecord:
        delivery_id = delivery_id or (
            f"delivery-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        )
        normalized_key = (idempotency_key or "").strip() or None
        payload_value = payload or {}
        payload_json = json.dumps(payload_value, sort_keys=True)
        digest_facts = {
            "brief_id": brief_id,
            "channel": channel,
            "recipient": recipient,
            "subject": subject,
            "markdown": markdown,
            "payload": payload_value,
        }
        payload_digest = hashlib.sha256(
            json.dumps(
                digest_facts,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        with self.session_factory() as session:
            if normalized_key is not None:
                existing = (
                    session.query(DeliveryOutboxRow)
                    .filter(DeliveryOutboxRow.idempotency_key == normalized_key)
                    .one_or_none()
                )
                if existing is not None:
                    if existing.payload_digest != payload_digest:
                        raise DeliveryIdempotencyConflictError(
                            "delivery idempotency key is bound to different facts"
                        )
                    return self._delivery_outbox_from_row(existing)
            row = DeliveryOutboxRow(
                delivery_id=delivery_id,
                brief_id=brief_id or "",
                channel=channel,
                recipient=recipient,
                subject=subject,
                markdown=markdown,
                payload_json=payload_json,
                idempotency_key=normalized_key,
                payload_digest=payload_digest,
                status="queued",
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if normalized_key is None:
                    raise
                existing = (
                    session.query(DeliveryOutboxRow)
                    .filter(DeliveryOutboxRow.idempotency_key == normalized_key)
                    .one_or_none()
                )
                if existing is None:
                    raise
                if existing.payload_digest != payload_digest:
                    raise DeliveryIdempotencyConflictError(
                        "delivery idempotency key is bound to different facts"
                    )
                return self._delivery_outbox_from_row(existing)
            session.refresh(row)
            return self._delivery_outbox_from_row(row)

    def list_delivery_outbox(
        self,
        status: str | None = None,
        limit: int = 20,
        provider: str | None = None,
    ) -> list[DeliveryOutboxRecord]:
        with self.session_factory() as session:
            query = session.query(DeliveryOutboxRow)
            if status:
                query = query.filter(DeliveryOutboxRow.status == status)
            if provider:
                query = query.join(BriefRunRow, DeliveryOutboxRow.brief_id == BriefRunRow.brief_id)
                query = query.filter(BriefRunRow.provider == provider)
            rows = (
                query.order_by(
                    DeliveryOutboxRow.created_at.desc(),
                    DeliveryOutboxRow.delivery_id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [self._delivery_outbox_from_row(row) for row in rows]

    def mark_delivery_sent(self, delivery_id: str) -> DeliveryOutboxRecord | None:
        with self.session_factory() as session:
            row = session.get(DeliveryOutboxRow, delivery_id)
            if row is None:
                return None
            row.status = "sent"
            row.sent_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return self._delivery_outbox_from_row(row)

    @staticmethod
    def _strategy_version_from_row(row: StrategyVersionRow) -> StrategyVersionRecord:
        return StrategyVersionRecord(
            strategy_id=row.strategy_id,
            strategy_version=row.strategy_version,
            definition_digest=row.definition_digest,
            definition=_json_object(row.definition_json),
            created_at=row.created_at,
        )

    @staticmethod
    def _policy_deployment_from_row(row: PolicyDeploymentRow) -> PolicyDeploymentRecord:
        return PolicyDeploymentRecord(
            deployment_id=row.deployment_id,
            strategy_id=row.strategy_id,
            policy_version=row.policy_version,
            strategy_version=row.strategy_version,
            factor_version=row.factor_version,
            parameter_version=row.parameter_version,
            universe_version=row.universe_version,
            data_revision=row.data_revision,
            policy_digest=row.policy_digest,
            policy=StrategyPolicy.model_validate_json(row.policy_json),
            previous_deployment_id=row.previous_deployment_id,
            created_at=row.created_at,
        )

    @staticmethod
    def _strategy_state_from_row(row: StrategyStateRow) -> StrategyStateRecord:
        return StrategyStateRecord(
            strategy_id=row.strategy_id,
            state=StrategyState(row.state),
            current_deployment_id=row.current_deployment_id,
            previous_deployment_id=row.previous_deployment_id,
            current_policy_version=row.current_policy_version,
            previous_policy_version=row.previous_policy_version,
            effective_weight=float(row.effective_weight),
            revision=row.revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _strategy_state_event_from_row(
        row: StrategyStateEventRow,
    ) -> StrategyStateEventRecord:
        return StrategyStateEventRecord(
            event_id=row.event_id,
            strategy_id=row.strategy_id,
            sequence=row.sequence,
            idempotency_key=row.idempotency_key,
            event_type=row.event_type,
            action=row.action,
            from_state=(StrategyState(row.from_state) if row.from_state is not None else None),
            to_state=StrategyState(row.to_state),
            deployment_id=row.deployment_id,
            previous_deployment_id=row.previous_deployment_id,
            policy_version=row.policy_version,
            effective_weight=float(row.effective_weight),
            reason=row.reason,
            evidence=_json_object(row.evidence_json),
            decision=_json_object(row.decision_json),
            created_at=row.created_at,
        )

    @staticmethod
    def _watchlist_from_row(row: WatchlistItemRow) -> WatchlistItem:
        return WatchlistItem(
            instrument_id=row.instrument_id,
            thesis=row.thesis,
            status=row.status,
            tags=_parse_tags(row.tags),
        )

    @staticmethod
    def _position_from_row(row: PositionRow) -> Position:
        return Position(
            instrument_id=row.instrument_id,
            shares=row.shares,
            entry_price=row.entry_price,
            entry_date=row.entry_date,
            strategy_tag=row.strategy_tag,
            initial_stop=row.initial_stop,
            target_1=row.target_1,
            target_2=row.target_2,
            thesis=row.thesis,
        )

    @staticmethod
    def _alert_rule_from_row(row: AlertRuleRow) -> StoredAlertRule:
        return StoredAlertRule(
            rule_id=row.rule_id,
            instrument_id=row.instrument_id,
            kind=row.kind,
            operator=row.operator,
            threshold=row.threshold,
        )

    @staticmethod
    def _universe_from_row(row: UniverseRow) -> UniverseRecord:
        return UniverseRecord(
            universe_id=row.universe_id,
            name=row.name,
            description=row.description,
            market_scope=row.market_scope,
            tags=_parse_tags(row.tags),
            symbols=json.loads(row.symbols or "[]"),
            source=row.source,
        )

    @staticmethod
    def _tradable_instrument_from_row(row: TradableInstrumentRow) -> StoredTradableInstrument:
        return StoredTradableInstrument(
            instrument_id=row.instrument_id,
            symbol=row.symbol,
            name=row.name,
            label=row.label,
            asset_type=row.asset_type,
            exchange=row.exchange,
            source=row.source,
            tags=_parse_tags(row.tags),
            synced_at=row.synced_at,
        )

    @staticmethod
    def _fundamental_snapshot_from_row(row: FundamentalSnapshotRow) -> FundamentalSnapshot:
        return FundamentalSnapshot(
            instrument_id=row.instrument_id,
            as_of_date=row.as_of_date,
            revenue_growth_pct=row.revenue_growth_pct,
            earnings_growth_pct=row.earnings_growth_pct,
            gross_margin_pct=row.gross_margin_pct,
            operating_margin_pct=row.operating_margin_pct,
            net_margin_pct=row.net_margin_pct,
            return_on_equity_pct=row.return_on_equity_pct,
            market_cap=row.market_cap,
            pe_ratio=row.pe_ratio,
            forward_pe=row.forward_pe,
            peg_ratio=row.peg_ratio,
            price_to_sales=row.price_to_sales,
            provider=row.source_provider,
        )

    @staticmethod
    def _snapshot_row_from_card(
        run_id: str,
        card: OpportunityCard,
        item,
    ) -> OpportunitySnapshotRow:
        signal_date = getattr(item, "latest_trade_date", None) if item else None
        latest_close = _decimal_or_none(getattr(item, "latest_close", None) if item else None)
        return OpportunitySnapshotRow(
            snapshot_id=f"{run_id}:{card.card_id}",
            run_id=run_id,
            card_id=card.card_id,
            instrument_id=card.instrument_id,
            market=card.market.value,
            status=card.status.value,
            signal_date=signal_date,
            latest_close=latest_close,
            primary_strategy_id=card.primary_strategy_id,
            score=Decimal(str(card.score)),
            strategy_score=Decimal(str(card.strategy_score)),
            rank_score=Decimal(str(card.rank_score)),
            trigger_price=card.entry_plan.trigger_price,
            initial_stop=card.exit_plan.initial_stop,
            target_1=card.exit_plan.target_1,
            card_json=json.dumps(card.model_dump(mode="json"), sort_keys=True),
        )

    @staticmethod
    def _scan_run_from_row(row: ScanRunRow) -> ScanRunRecord:
        return ScanRunRecord(
            run_id=row.run_id,
            provider=row.provider,
            mode=row.mode,
            symbols=json.loads(row.symbols or "[]"),
            scanned=row.scanned,
            cards=row.cards,
            data_health=json.loads(row.data_health or "{}"),
            started_at=row.started_at,
            completed_at=row.completed_at,
            created_at=row.created_at,
        )

    @staticmethod
    def _scan_result_cache_from_row(row: ScanResultCacheRow) -> ScanResultCacheRecord:
        return ScanResultCacheRecord(
            cache_id=row.cache_id,
            cache_key=row.cache_key,
            provider=row.provider,
            mode=row.mode,
            symbols=json.loads(row.symbols or "[]"),
            payload=json.loads(row.payload_json),
            created_at=row.created_at,
        )

    @staticmethod
    def _full_market_scan_job_from_row(row: FullMarketScanJobRow) -> FullMarketScanJobRecord:
        return FullMarketScanJobRecord(
            job_id=row.job_id,
            provider=row.provider,
            status=row.status,
            batch_size=row.batch_size,
            total_symbols=row.total_symbols,
            scanned_symbols=row.scanned_symbols,
            total_batches=row.total_batches,
            completed_batches=row.completed_batches,
            cards=row.cards,
            errors=row.errors,
            include_etfs=bool(row.include_etfs),
            sync_if_empty=bool(row.sync_if_empty),
            symbols=json.loads(row.symbols or "[]"),
            message=row.message or "",
            data_health=json.loads(row.data_health or "{}"),
            result_cache_key=row.result_cache_key,
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _historical_backfill_job_from_row(
        row: HistoricalBackfillJobRow,
    ) -> HistoricalBackfillJobRecord:
        return HistoricalBackfillJobRecord(
            job_id=row.job_id,
            provider=row.provider,
            status=row.status,
            start_date=row.start_date,
            end_date=row.end_date,
            symbols=json.loads(row.symbols or "[]"),
            total_symbols=row.total_symbols,
            processed_symbols=row.processed_symbols,
            succeeded_symbols=row.succeeded_symbols,
            failed_symbols=row.failed_symbols,
            rows_written=row.rows_written,
            fundamental_rows_written=row.fundamental_rows_written,
            current_instrument=row.current_instrument,
            errors=json.loads(row.errors_json or "[]"),
            data_health=json.loads(row.data_health or "{}"),
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _walk_forward_run_from_row(row: WalkForwardRunRow) -> WalkForwardRunRecord:
        payload = json.loads(row.payload_json)
        stored_data_health = json.loads(row.data_health)
        if not isinstance(stored_data_health, dict):
            raise ValueError(f"walk-forward run {row.run_id} data health is malformed")
        storage_schema = stored_data_health.get(WALK_FORWARD_RUN_STORAGE_SCHEMA_KEY)
        data_health = {
            key: value
            for key, value in stored_data_health.items()
            if key != WALK_FORWARD_RUN_STORAGE_SCHEMA_KEY
        }
        is_current_result = (
            storage_schema == WALK_FORWARD_RUN_STORAGE_SCHEMA
            or payload.get("result_digest_schema") == "walk-forward-result-digest-v2"
            or str(row.reproducibility_digest).startswith("v2")
        )
        if storage_schema not in {None, WALK_FORWARD_RUN_STORAGE_SCHEMA}:
            raise ValueError(
                f"walk-forward run {row.run_id} has unknown storage integrity schema"
            )
        if is_current_result:
            from qagent.backtesting.experiment import (
                WalkForwardExperimentManifest,
                walk_forward_manifest_digest_is_valid,
            )
            from qagent.backtesting.walk_forward import (
                WalkForwardSelectionResult,
                walk_forward_selection_result_digest_is_valid,
            )

            try:
                manifest = WalkForwardExperimentManifest.model_validate(
                    payload.get("experiment_manifest")
                )
                result = WalkForwardSelectionResult.model_validate(payload)
            except ValueError as exc:
                raise ValueError(
                    f"walk-forward run {row.run_id} contains an invalid current-schema payload"
                ) from exc
            if not walk_forward_manifest_digest_is_valid(manifest):
                raise ValueError(
                    f"walk-forward run {row.run_id} failed manifest integrity validation"
                )
            if not walk_forward_selection_result_digest_is_valid(payload):
                raise ValueError(
                    f"walk-forward run {row.run_id} failed result digest validation"
                )
            expected = {
                "run_id": result.owner_run_id,
                "provider": result.provider_mode,
                "start_date": result.start_date,
                "end_date": result.end_date,
                "dataset_revision": result.dataset_revision,
                "rebalance_step_sessions": result.rebalance_step_sessions,
                "lookback_days": result.experiment_manifest.lookback_days,
                "snapshot_count": len(result.snapshots),
                "top_5_trade_count": result.top_5_metrics.trade_count,
                "top_10_trade_count": result.top_10_metrics.trade_count,
                "top_5_oos_trades": int(
                    data_health.get("walk_forward_top_5_oos_trades", 0) or 0
                ),
                "top_10_oos_trades": int(
                    data_health.get("walk_forward_top_10_oos_trades", 0) or 0
                ),
                "top_5_oos_gate": data_health.get(
                    "walk_forward_top_5_oos_gate",
                    "insufficient",
                ),
                "top_10_oos_gate": data_health.get(
                    "walk_forward_top_10_oos_gate",
                    "insufficient",
                ),
                "reproducibility_digest": result.reproducibility_digest,
            }
            mismatches = [
                field
                for field, expected_value in expected.items()
                if getattr(row, field) != expected_value
            ]
            if round(float(row.top_5_return_pct), 6) != round(
                result.top_5_metrics.total_return_pct,
                6,
            ):
                mismatches.append("top_5_return_pct")
            if round(float(row.top_10_return_pct), 6) != round(
                result.top_10_metrics.total_return_pct,
                6,
            ):
                mismatches.append("top_10_return_pct")
            if payload.get("data_health") != data_health:
                mismatches.append("data_health")
            if mismatches:
                fields = ", ".join(sorted(set(mismatches)))
                raise ValueError(
                    f"walk-forward run {row.run_id} row/payload integrity mismatch: {fields}"
                )
        return WalkForwardRunRecord(
            run_id=row.run_id,
            provider=row.provider,
            status=row.status,
            start_date=row.start_date,
            end_date=row.end_date,
            dataset_revision=row.dataset_revision,
            rebalance_step_sessions=row.rebalance_step_sessions,
            lookback_days=row.lookback_days,
            snapshot_count=row.snapshot_count,
            top_5_trade_count=row.top_5_trade_count,
            top_10_trade_count=row.top_10_trade_count,
            top_5_return_pct=float(row.top_5_return_pct),
            top_10_return_pct=float(row.top_10_return_pct),
            top_5_oos_trades=row.top_5_oos_trades,
            top_10_oos_trades=row.top_10_oos_trades,
            top_5_oos_gate=row.top_5_oos_gate,
            top_10_oos_gate=row.top_10_oos_gate,
            reproducibility_digest=row.reproducibility_digest,
            payload=payload,
            data_health=data_health,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _walk_forward_job_from_row(row: WalkForwardJobRow) -> WalkForwardJobRecord:
        experiment_manifest = json.loads(row.experiment_manifest_json or "{}")
        checkpoints = _decode_walk_forward_checkpoints(
            row,
            manifest_payload=experiment_manifest,
        )
        return WalkForwardJobRecord(
            job_id=row.job_id,
            provider=row.provider,
            status=row.status,
            phase=row.phase,
            start_date=row.start_date,
            end_date=row.end_date,
            dataset_revision=row.dataset_revision,
            rebalance_step_sessions=row.rebalance_step_sessions,
            lookback_days=row.lookback_days,
            total_snapshots=row.total_snapshots,
            processed_snapshots=row.processed_snapshots,
            current_date=row.current_date,
            lease_maintenance_count=row.lease_maintenance_count,
            lease_recovery_count=row.lease_recovery_count,
            last_lease_heartbeat_at=row.last_lease_heartbeat_at,
            checkpoints=checkpoints,
            experiment_manifest=experiment_manifest,
            result_run_id=row.result_run_id,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _opportunity_snapshot_from_row(row: OpportunitySnapshotRow) -> OpportunitySnapshotRecord:
        return OpportunitySnapshotRecord(
            snapshot_id=row.snapshot_id,
            run_id=row.run_id,
            card_id=row.card_id,
            instrument_id=row.instrument_id,
            market=row.market,
            status=row.status,
            signal_date=row.signal_date,
            latest_close=row.latest_close,
            primary_strategy_id=row.primary_strategy_id,
            score=row.score,
            strategy_score=row.strategy_score,
            rank_score=row.rank_score,
            trigger_price=row.trigger_price,
            initial_stop=row.initial_stop,
            target_1=row.target_1,
            card=json.loads(row.card_json),
        )

    @staticmethod
    def _brief_run_from_row(row: BriefRunRow) -> BriefRunRecord:
        return BriefRunRecord(
            brief_id=row.brief_id,
            provider=row.provider,
            symbols=json.loads(row.symbols or "[]"),
            headline=row.headline,
            opportunity_count=row.opportunity_count,
            entry_watch_count=row.entry_watch_count,
            risk_alert_count=row.risk_alert_count,
            catalyst_count=row.catalyst_count,
            validation_count=row.validation_count,
            data_health=json.loads(row.data_health or "{}"),
            payload=json.loads(row.brief_json),
            created_at=row.created_at,
        )

    @staticmethod
    def _delivery_outbox_from_row(row: DeliveryOutboxRow) -> DeliveryOutboxRecord:
        return DeliveryOutboxRecord(
            delivery_id=row.delivery_id,
            brief_id=row.brief_id or None,
            channel=row.channel,
            recipient=row.recipient,
            subject=row.subject,
            markdown=row.markdown,
            payload=json.loads(row.payload_json or "{}"),
            idempotency_key=row.idempotency_key,
            payload_digest=row.payload_digest,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
            sent_at=row.sent_at,
        )

    @staticmethod
    def _automation_scheduler_state_from_row(
        row: AutomationSchedulerStateRow,
    ) -> AutomationSchedulerStateRecord:
        try:
            payload = json.loads(row.settings_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("settings"), dict):
            settings = payload["settings"]
            runtime = payload.get("runtime")
        else:
            # Records written before runtime checkpoints stored settings directly.
            settings = payload
            runtime = {}
        return AutomationSchedulerStateRecord(
            enabled=row.enabled,
            settings=settings if isinstance(settings, dict) else {},
            runtime=runtime if isinstance(runtime, dict) else {},
            updated_at=row.updated_at,
            revision=row.revision,
        )


def _resolve_governance_defaults(
    defaults: list[StrategyDefinition] | list[StrategyPolicy] | None,
    *,
    definitions: list[StrategyDefinition] | None,
    policies: list[StrategyPolicy] | None,
) -> tuple[list[StrategyDefinition], list[StrategyPolicy]]:
    if defaults is not None and (definitions is not None or policies is not None):
        raise ValueError("defaults cannot be combined with definitions or policies")
    if defaults is not None:
        if all(isinstance(item, StrategyDefinition) for item in defaults):
            definitions = [StrategyDefinition.model_validate(item) for item in defaults]
            policies = []
        elif all(isinstance(item, StrategyPolicy) for item in defaults):
            policies = [StrategyPolicy.model_validate(item) for item in defaults]
            definitions = None
        else:
            raise TypeError("defaults must contain only StrategyDefinition or StrategyPolicy")

    resolved_policies = [StrategyPolicy.model_validate(item) for item in (policies or [])]
    if definitions is not None:
        resolved_definitions = [StrategyDefinition.model_validate(item) for item in definitions]
    else:
        registry_definitions = default_strategy_registry().all()
        if policies is None and defaults is None:
            resolved_definitions = registry_definitions
        else:
            policy_ids = {policy.strategy_id for policy in resolved_policies}
            resolved_definitions = [
                definition
                for definition in registry_definitions
                if definition.strategy_id in policy_ids
            ]
    return resolved_definitions, resolved_policies


def _begin_governance_write(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _strategy_definition_payload(
    policy: StrategyPolicy,
    definition: StrategyDefinition | None,
) -> dict[str, object]:
    if definition is not None:
        if definition.strategy_id != policy.strategy_id:
            raise ValueError("strategy definition and policy strategy_id differ")
        return definition.model_dump(mode="json")
    return {
        "strategy_id": policy.strategy_id,
        "strategy_version": policy.strategy_version,
        "source": "policy_snapshot",
    }


def _default_strategy_definition(strategy_id: str) -> StrategyDefinition | None:
    try:
        return default_strategy_registry().get(strategy_id)
    except KeyError:
        return None


def _ensure_strategy_version(
    session: Session,
    *,
    strategy_id: str,
    strategy_version: str,
    definition: dict[str, object],
) -> StrategyVersionRow:
    definition_json = _canonical_json(definition)
    definition_digest = hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
    row = session.get(StrategyVersionRow, (strategy_id, strategy_version))
    if row is not None:
        if row.definition_digest != definition_digest or row.definition_json != definition_json:
            raise ValueError(f"strategy version {strategy_id}:{strategy_version} is immutable")
        return row
    row = StrategyVersionRow(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        definition_digest=definition_digest,
        definition_json=definition_json,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


def _ensure_policy_deployment(
    session: Session,
    policy: StrategyPolicy,
    *,
    previous_deployment_id: str | None,
) -> PolicyDeploymentRow:
    policy_json = _canonical_json(policy)
    policy_digest = strategy_policy_digest(policy)
    row = _find_policy_deployment(session, policy.strategy_id, policy.policy_version)
    if row is not None:
        if row.policy_digest != policy_digest or row.policy_json != policy_json:
            raise ValueError(
                f"policy snapshot {policy.strategy_id}:{policy.policy_version} is immutable"
            )
        return row
    row = PolicyDeploymentRow(
        deployment_id=f"policy-deployment-{policy_digest[:24]}",
        strategy_id=policy.strategy_id,
        policy_version=policy.policy_version,
        strategy_version=policy.strategy_version,
        factor_version=policy.factor_version,
        parameter_version=policy.parameter_version,
        universe_version=policy.universe_version,
        data_revision=str(policy.data_revision),
        policy_digest=policy_digest,
        policy_json=policy_json,
        previous_deployment_id=previous_deployment_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


def _find_policy_deployment(
    session: Session,
    strategy_id: str,
    policy_version: str,
) -> PolicyDeploymentRow | None:
    return (
        session.query(PolicyDeploymentRow)
        .filter(
            PolicyDeploymentRow.strategy_id == strategy_id,
            PolicyDeploymentRow.policy_version == policy_version,
        )
        .one_or_none()
    )


def _deployment_policy_version(
    session: Session,
    deployment_id: str | None,
) -> str | None:
    if deployment_id is None:
        return None
    row = session.get(PolicyDeploymentRow, deployment_id)
    return row.policy_version if row is not None else None


def _find_idempotent_governance_event(
    session: Session,
    idempotency_key: str,
) -> StrategyStateEventRow | None:
    return (
        session.query(StrategyStateEventRow)
        .filter(StrategyStateEventRow.idempotency_key == idempotency_key)
        .one_or_none()
    )


def _next_strategy_event_sequence(session: Session, strategy_id: str) -> int:
    latest = (
        session.query(func.max(StrategyStateEventRow.sequence))
        .filter(StrategyStateEventRow.strategy_id == strategy_id)
        .scalar()
    )
    return int(latest or 0) + 1


def _resolve_rollback_target(
    session: Session,
    *,
    current: PolicyDeploymentRow,
    target_deployment_id: str | None,
    target_policy_version: str | None,
) -> PolicyDeploymentRow | None:
    by_id = (
        session.get(PolicyDeploymentRow, target_deployment_id)
        if target_deployment_id is not None
        else None
    )
    by_version = (
        _find_policy_deployment(session, current.strategy_id, target_policy_version)
        if target_policy_version is not None
        else None
    )
    if target_deployment_id is not None and by_id is None:
        raise LookupError(f"policy deployment {target_deployment_id!r} does not exist")
    if target_policy_version is not None and by_version is None:
        raise LookupError(f"policy {current.strategy_id}:{target_policy_version} does not exist")
    if (
        by_id is not None
        and by_version is not None
        and by_id.deployment_id != by_version.deployment_id
    ):
        raise ValueError("rollback deployment and policy targets differ")
    if by_id is not None or by_version is not None:
        return by_id or by_version

    if current.previous_deployment_id is not None:
        return session.get(PolicyDeploymentRow, current.previous_deployment_id)
    current_policy = StrategyPolicy.model_validate_json(current.policy_json)
    if current_policy.rollback_policy_version is None:
        return None
    return _find_policy_deployment(
        session,
        current.strategy_id,
        current_policy.rollback_policy_version,
    )


def _validate_decision_identity(
    policy: StrategyPolicy,
    decision: dict[str, object],
) -> None:
    strategy_id = decision.get("strategy_id")
    if strategy_id is not None and str(strategy_id) != policy.strategy_id:
        raise ValueError("decision and policy strategy_id differ")
    for field_name in ("policy_version", "current_policy_version"):
        value = decision.get(field_name)
        if value is not None and str(value) != policy.policy_version:
            raise ValueError(f"decision {field_name} and policy version differ")


def _decision_target_state(decision: dict[str, object]) -> StrategyState:
    value = decision.get("effective_state", decision.get("to_state"))
    if value is None:
        raise ValueError("governance decision must include to_state or effective_state")
    return StrategyState(str(value))


def _optional_strategy_state(value: object) -> StrategyState | None:
    return StrategyState(str(value)) if value is not None else None


def _decision_action(decision: dict[str, object]) -> str:
    action = decision.get("action")
    if action is not None:
        return str(action)
    if decision.get("admitted") is True:
        return "admit"
    if decision.get("admitted") is False or decision.get("allowed") is False:
        return "hold"
    return "transition"


def _decision_effective_weight(
    policy: StrategyPolicy,
    decision: dict[str, object],
    state: StrategyState,
    *,
    override: float | None,
) -> float:
    if override is not None:
        value = float(override)
    elif decision.get("effective_weight") is not None:
        value = float(decision["effective_weight"])
    else:
        value = _policy_effective_weight(policy, state)
    if not 0.0 <= value <= 1.0:
        raise ValueError("effective_weight must be between 0 and 1")
    return value


def _policy_effective_weight(
    policy: StrategyPolicy | None,
    state: StrategyState,
) -> float:
    if policy is None:
        return 0.0
    if state is StrategyState.ADMITTED:
        return policy.base_weight
    if state is StrategyState.THROTTLED:
        return round(
            policy.base_weight * policy.breach_policy.throttle_multiplier,
            10,
        )
    return 0.0


def _validate_decision_replay(
    row: StrategyStateEventRow,
    *,
    policy: StrategyPolicy,
    decision_json: str,
    evidence_json: str,
    reason: str,
) -> None:
    if (
        row.strategy_id != policy.strategy_id
        or row.policy_version != policy.policy_version
        or row.decision_json != decision_json
        or row.evidence_json != evidence_json
        or row.reason != reason
    ):
        raise ValueError("idempotency_key is already used by a different decision")


def _validate_decision_replay_record(
    record: StrategyStateEventRecord,
    *,
    policy: StrategyPolicy,
    decision_json: str,
    evidence_json: str,
    reason: str,
) -> None:
    if (
        record.strategy_id != policy.strategy_id
        or record.policy_version != policy.policy_version
        or _canonical_json(record.decision) != decision_json
        or _canonical_json(record.evidence) != evidence_json
        or record.reason != reason
    ):
        raise ValueError("idempotency_key is already used by a different decision")


def _validate_rollback_replay(
    row: StrategyStateEventRow,
    *,
    strategy_id: str,
    target_deployment_id: str | None,
    target_policy_version: str | None,
    evidence_json: str,
    reason: str | None,
) -> None:
    mismatched = (
        row.strategy_id != strategy_id
        or row.action != "rollback"
        or row.evidence_json != evidence_json
        or (target_deployment_id is not None and row.deployment_id != target_deployment_id)
        or (target_policy_version is not None and row.policy_version != target_policy_version)
        or (reason is not None and row.reason != reason.strip())
    )
    if mismatched:
        raise ValueError("idempotency_key is already used by a different rollback")


def _required_text(value: object, field_name: str) -> str:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _canonical_json(value: object) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _json_object(value: str) -> dict[str, object]:
    payload = json.loads(value or "{}")
    if not isinstance(payload, dict):
        raise ValueError("stored governance JSON must contain an object")
    return payload


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _instrument_tags(instrument) -> list[str]:
    tags = [instrument.asset_type, instrument.exchange]
    name = instrument.name
    if "ETF" in name.upper():
        tags.extend(["etf", "index_tool"])
    if "半导体" in name or "芯片" in name:
        tags.extend(["semiconductor", "chip"])
    if "科创" in name:
        tags.append("star_market")
    return tags


def _tradable_summary(rows: list[TradableInstrumentRow]) -> TradableCatalogSummary:
    exchanges: dict[str, int] = {}
    last_synced_at = None
    for row in rows:
        exchanges[row.exchange] = exchanges.get(row.exchange, 0) + 1
        if row.synced_at and (last_synced_at is None or row.synced_at > last_synced_at):
            last_synced_at = row.synced_at
    stock_count = sum(1 for row in rows if row.asset_type == "stock")
    etf_count = sum(1 for row in rows if row.asset_type == "etf")
    return TradableCatalogSummary(
        total_count=len(rows),
        stock_count=stock_count,
        etf_count=etf_count,
        other_count=len(rows) - stock_count - etf_count,
        exchanges=exchanges,
        last_synced_at=last_synced_at,
    )


def _matches_tradable_row(row: TradableInstrumentRow, query: str) -> bool:
    haystack = " ".join(
        [
            row.instrument_id,
            row.symbol,
            row.name,
            row.label,
            row.asset_type,
            row.exchange,
            row.tags,
            f"{row.symbol}.{row.exchange}",
        ]
    ).upper()
    return query in haystack


def _tradable_match_rank(row: TradableInstrumentRow, query: str) -> tuple[int, int, int, str]:
    symbol = row.symbol.upper()
    name = row.name.upper()
    label = row.label.upper()
    token = row.instrument_id.upper()
    exchange_label = f"{symbol}.{row.exchange}".upper()
    asset_rank = _asset_sort_rank(row.asset_type)
    if query in {symbol, exchange_label, token}:
        return (0, asset_rank, 0, symbol)
    if query in {name, label}:
        return (1, asset_rank, len(name), symbol)
    if symbol.startswith(query):
        return (2, asset_rank, len(symbol), symbol)
    if name.startswith(query):
        return (3, asset_rank, len(name), symbol)
    if label.startswith(query):
        return (4, asset_rank, len(label), symbol)
    if query in name:
        return (5, asset_rank, name.index(query), symbol)
    if query in label:
        return (6, asset_rank, label.index(query), symbol)
    return (9, asset_rank, len(label), symbol)


def _asset_sort_rank(asset_type: str) -> int:
    return {"etf": 0, "stock": 1}.get(asset_type, 2)


def _asset_browse_rank(asset_type: str) -> int:
    return {"stock": 0, "etf": 1}.get(asset_type, 2)


def _latest_revision_alias(
    model,
    identity_columns: tuple[str, ...],
    *,
    max_dataset_revision: int | None = None,
):
    statement = select(
        model,
        func.row_number()
        .over(
            partition_by=tuple(getattr(model, key) for key in identity_columns),
            order_by=model.dataset_revision.desc(),
        )
        .label("revision_rank"),
    )
    if max_dataset_revision is not None:
        statement = statement.where(model.dataset_revision <= max_dataset_revision)
    ranked = statement.subquery()
    return aliased(model, ranked), ranked.c.revision_rank


def _sqlite_upsert_chunks(
    session: Session,
    model,
    records: list[dict[str, object]],
    index_elements: list[str],
    chunk_size: int = 400,
) -> None:
    if not records:
        return
    update_columns = [key for key in records[0] if key not in index_elements]
    for offset in range(0, len(records), chunk_size):
        statement = sqlite_insert(model).values(records[offset : offset + chunk_size])
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[getattr(model, key) for key in index_elements],
            set_={key: getattr(excluded, key) for key in update_columns},
        )
        session.execute(statement)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result


def _aware_health_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("scan timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("scan timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)
