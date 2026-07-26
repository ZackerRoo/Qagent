from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardIdentity,
    RankingV3ForwardValidator,
    stable_digest,
)
from qagent.backtesting.ranking_v3_forward_runtime import (
    RankingV3CandidateSnapshotRequest,
    RankingV3ProductionForwardFactAuthority,
    RankingV3ServerCandidateRecord,
    RankingV3ServerCandidateSnapshot,
)
from qagent.backtesting.ranking_v3_production import (
    RankingV3ProductionBatch,
    RankingV3ProductionBatchInput,
    RankingV3ProductionIdentity,
    RankingV3ProductionReleaseValidation,
    RankingV3ProductionSelectionItem,
    RankingV3ProductionSelectionValidation,
    RankingV3ProductionSelectionService,
)
from qagent.backtesting.ranking_v3_protocol import (
    RankingV3Protocol,
    build_ranking_v3_protocol,
)
from qagent.backtesting.ranking_v3_evidence import (
    RankingV3RepositoryEvidenceAuthority,
)
from qagent.jobs.ranking_v3_forward import _candidate_from_snapshot
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository
from qagent.storage.ranking_v3_production import RankingV3ProductionRepository
from qagent.storage.repository import ScanRunSnapshotBundle


class RankingV3ProductionSnapshotUnavailable(RuntimeError):
    """Raised until a complete authoritative scan exists for the session."""


class RankingV3ProductionOpportunityRepository(Protocol):
    session_factory: sessionmaker[Session]

    def get_walk_forward_run(self, run_id: str) -> object | None: ...

    def get_latest_complete_daily_scan_with_snapshots(
        self,
        *,
        provider: str,
        signal_date: date,
        minimum_scanned: int,
    ) -> ScanRunSnapshotBundle | None: ...


class RankingV3ProductionDayResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: str
    session_date: date
    validation_run_id: str
    release_proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_fact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_count: int = Field(ge=0)
    source_scan_run_id: str
    batch: RankingV3ProductionBatch


class QagentRankingV3ProductionCandidateLoader:
    """Load one complete post-release cross-section from exactly one scan run."""

    def __init__(
        self,
        repository: RankingV3ProductionOpportunityRepository,
        *,
        provider: str,
        minimum_scanned: int,
        protocol: RankingV3Protocol,
    ):
        self.repository = repository
        self.provider = provider
        self.minimum_scanned = minimum_scanned
        self.protocol = protocol
        self.source_scan_run_id: str | None = None

    def load_candidate_snapshot(
        self,
        request: RankingV3CandidateSnapshotRequest,
    ) -> RankingV3ServerCandidateSnapshot:
        bundle = self.repository.get_latest_complete_daily_scan_with_snapshots(
            provider=self.provider,
            signal_date=request.session_date,
            minimum_scanned=self.minimum_scanned,
        )
        if bundle is None:
            raise RankingV3ProductionSnapshotUnavailable(
                "no complete authoritative full-market scan exists for the session"
            )
        self.source_scan_run_id = bundle.run.run_id
        candidates: list[RankingV3ServerCandidateRecord] = []
        seen_snapshots: set[str] = set()
        seen_instruments: set[str] = set()
        for snapshot in bundle.snapshots:
            if snapshot.signal_date != request.session_date:
                raise ValueError("production scan contains a different signal date")
            if snapshot.snapshot_id in seen_snapshots:
                raise ValueError("production scan contains a duplicate source snapshot")
            if snapshot.instrument_id in seen_instruments:
                raise ValueError("production scan contains duplicate instruments")
            seen_snapshots.add(snapshot.snapshot_id)
            seen_instruments.add(snapshot.instrument_id)
            candidate = _candidate_from_snapshot(snapshot)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(
            key=lambda item: (
                -item.baseline_rank_score,
                item.instrument_id,
                item.source_snapshot_id,
            )
        )
        return RankingV3ServerCandidateSnapshot.create(
            request=request,
            benchmark_id=(
                self.protocol.benchmark_definition.forward_release_benchmark_id
            ),
            candidates=candidates[: self.protocol.candidate_pool_limit],
        )


class QagentRankingV3ProductionReleaseAuthority:
    """Resolve the one current approved release from the authoritative ledger."""

    def __init__(
        self,
        repository: RankingV3ProductionOpportunityRepository,
        forward_repository: RankingV3ForwardRepository,
        protocol: RankingV3Protocol,
        *,
        provider: str,
    ):
        self.repository = repository
        self.forward_repository = forward_repository
        self.protocol = protocol
        self.provider = provider

    def validate_current_release(
        self,
        identity: RankingV3ProductionIdentity,
    ) -> RankingV3ProductionReleaseValidation:
        validator = RankingV3ForwardValidator(
            self.forward_repository,
            self.protocol,
            evidence_authority=RankingV3RepositoryEvidenceAuthority(self.repository),
        )
        ledger = self.forward_repository.load_snapshot(validator.identity)
        if ledger is None or ledger.ledger.status != "approved":
            return _invalid_release("the current Ranking V3 ledger is not approved")
        proof = ledger.release_proof
        if proof is None or ledger.ledger.current_release_proof_digest != proof.proof_digest:
            return _invalid_release("the current Ranking V3 release proof is missing")
        validation = validator.validate_release_proof(
            proof.proof_digest,
            expected_data_revision=proof.data_revision,
        )
        if not validation.valid or validation.proof is None:
            return _invalid_release(
                f"the current Ranking V3 release proof is invalid: {validation.reason}"
            )
        historical_evidence = next(
            (
                item
                for item in reversed(ledger.evidence)
                if item.evidence_kind == "historical_gates"
            ),
            None,
        )
        run_id = (
            str(historical_evidence.payload.get("validation_run_id") or "").strip()
            if historical_evidence is not None
            else ""
        )
        run = self.repository.get_walk_forward_run(run_id) if run_id else None
        if (
            run is None
            or str(getattr(run, "provider", "")).strip().lower()
            != self.provider.strip().lower()
        ):
            return _invalid_release("the approved release provider is not authoritative")
        expected_identity = RankingV3ProductionIdentity.from_release_proof(
            validation.proof,
            validation_run_id=run_id,
        )
        if expected_identity != identity:
            return _invalid_release("the approved release identity has changed")
        return RankingV3ProductionReleaseValidation(
            valid=True,
            current=True,
            status="approved",
            reason="current approved Ranking V3 release",
            release_proof_digest=proof.proof_digest,
            validation_run_id=run_id,
            data_revision=proof.data_revision,
            protocol_identity=proof.identity,
            approved_at=proof.generated_at,
        )


class QagentRankingV3ProductionSelectionAuthority:
    """Bind persistence to the exact batch recomputed by the production job."""

    def __init__(
        self,
        identity: RankingV3ProductionIdentity,
        batch: RankingV3ProductionBatchInput,
    ):
        self.identity = identity
        self.batch = batch

    def validate_selection(
        self,
        identity: RankingV3ProductionIdentity,
        item: RankingV3ProductionBatchInput,
    ) -> RankingV3ProductionSelectionValidation:
        if identity != self.identity or item != self.batch:
            return RankingV3ProductionSelectionValidation(
                authorized=False,
                reason=(
                    "production selection does not match the server-recomputed "
                    "full-market batch"
                ),
            )
        return RankingV3ProductionSelectionValidation(
            authorized=True,
            reason="exact server-recomputed full-market production batch",
            identity_digest=identity.identity_digest,
            selection_batch_digest=item.selection_batch_digest,
        )


def run_ranking_v3_production_day(
    repository: RankingV3ProductionOpportunityRepository,
    *,
    session_date: date,
    provider: str = "free",
    protocol: RankingV3Protocol | None = None,
) -> RankingV3ProductionDayResult:
    """Freeze the current approved model's complete production selection for one day."""

    frozen_protocol = protocol or build_ranking_v3_protocol()
    forward_repository = RankingV3ForwardRepository(repository.session_factory)
    validator = RankingV3ForwardValidator(
        forward_repository,
        frozen_protocol,
        evidence_authority=RankingV3RepositoryEvidenceAuthority(repository),
    )
    ledger = forward_repository.load_snapshot(
        RankingV3ForwardIdentity.from_protocol(frozen_protocol)
    )
    if ledger is None or ledger.ledger.status != "approved" or ledger.release_proof is None:
        raise PermissionError("Ranking V3 has no current approved release")
    proof_validation = validator.validate_release_proof(
        ledger.release_proof.proof_digest,
        expected_data_revision=ledger.release_proof.data_revision,
    )
    if not proof_validation.valid or proof_validation.proof is None:
        raise PermissionError(
            f"Ranking V3 release proof is invalid: {proof_validation.reason}"
        )
    historical_evidence = next(
        (
            item
            for item in reversed(ledger.evidence)
            if item.evidence_kind == "historical_gates"
        ),
        None,
    )
    validation_run_id = (
        str(historical_evidence.payload.get("validation_run_id") or "").strip()
        if historical_evidence is not None
        else ""
    )
    if not validation_run_id:
        raise ValueError("approved Ranking V3 release has no validation run binding")
    run = repository.get_walk_forward_run(validation_run_id)
    if run is None:
        raise LookupError("approved Ranking V3 validation run does not exist")
    payload = getattr(run, "payload", None)
    ranking_v3 = payload.get("ranking_v3") if isinstance(payload, Mapping) else None
    if not isinstance(ranking_v3, Mapping):
        raise ValueError("approved validation run has no Ranking V3 payload")

    identity = RankingV3ProductionIdentity.from_release_proof(
        proof_validation.proof,
        validation_run_id=validation_run_id,
    )
    RankingV3ProductionForwardFactAuthority._validate_authoritative_context(
        validation_run_id=validation_run_id,
        run=run,
        ranking_v3=ranking_v3,
        protocol=frozen_protocol,
    )
    artifact = RankingV3ProductionForwardFactAuthority._restore_artifact(
        ranking_v3,
        frozen_protocol,
        session_date,
    )
    request = RankingV3CandidateSnapshotRequest(
        validation_run_id=validation_run_id,
        data_revision=identity.data_revision,
        protocol_id=frozen_protocol.protocol_id,
        protocol_digest=frozen_protocol.protocol_digest,
        model_version=frozen_protocol.model_version,
        artifact_digest=artifact.stable_digest,
        session_date=session_date,
    )
    loader = QagentRankingV3ProductionCandidateLoader(
        repository,
        provider=provider,
        minimum_scanned=frozen_protocol.candidate_pool_limit,
        protocol=frozen_protocol,
    )
    snapshot = loader.load_candidate_snapshot(request)
    RankingV3ProductionForwardFactAuthority._validate_candidate_snapshot(
        snapshot,
        request,
        frozen_protocol,
    )
    selected, runtime_selection_digest = (
        RankingV3ProductionForwardFactAuthority._rank_and_select(
            snapshot=snapshot,
            artifact=artifact,
            protocol=frozen_protocol,
        )
    )
    selection_items = tuple(
        RankingV3ProductionSelectionItem.create(
            candidate_id=_production_candidate_id(
                identity,
                session_date=session_date,
                source_snapshot_id=item.source_snapshot_id,
                runtime_selection_digest=runtime_selection_digest,
            ),
            instrument_id=item.instrument_id,
            source_snapshot_id=item.source_snapshot_id,
            strategy_id=item.strategy_id,
            rank=item.rank,
            score=item.score,
        )
        for item in selected
    )
    batch_input = RankingV3ProductionBatchInput.create(
        session_date=session_date,
        candidate_snapshot_digest=snapshot.snapshot_digest,
        selections=selection_items,
    )
    production_repository = RankingV3ProductionRepository(repository.session_factory)
    service = RankingV3ProductionSelectionService(
        production_repository,
        QagentRankingV3ProductionReleaseAuthority(
            repository,
            forward_repository,
            frozen_protocol,
            provider=provider,
        ),
        selection_authority=QagentRankingV3ProductionSelectionAuthority(
            identity,
            batch_input,
        ),
    )
    batch = service.record_batch(
        identity,
        batch_input,
        idempotency_key=(
            f"ranking-v3-production:{identity.identity_digest}:{session_date.isoformat()}"
        ),
    )
    return RankingV3ProductionDayResult(
        state="recorded",
        session_date=session_date,
        validation_run_id=validation_run_id,
        release_proof_digest=identity.release_proof_digest,
        production_identity_digest=identity.identity_digest,
        candidate_snapshot_digest=snapshot.snapshot_digest,
        batch_fact_digest=batch.fact_digest,
        selected_count=batch.selected_count,
        source_scan_run_id=loader.source_scan_run_id or "",
        batch=batch,
    )


def _production_candidate_id(
    identity: RankingV3ProductionIdentity,
    *,
    session_date: date,
    source_snapshot_id: str,
    runtime_selection_digest: str,
) -> str:
    digest = stable_digest(
        {
            "schema_version": "ranking-v3-production-candidate-v1",
            "identity_digest": identity.identity_digest,
            "session_date": session_date,
            "source_snapshot_id": source_snapshot_id,
            "runtime_selection_digest": runtime_selection_digest,
        }
    )
    return f"prod-{digest[:48]}"


def _invalid_release(reason: str) -> RankingV3ProductionReleaseValidation:
    return RankingV3ProductionReleaseValidation(
        valid=False,
        current=False,
        status="missing",
        reason=reason,
    )
