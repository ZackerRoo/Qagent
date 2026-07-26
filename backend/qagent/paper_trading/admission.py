from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from qagent.backtesting.ranking_v3_evidence import (
    RankingV3RepositoryEvidenceAuthority,
)
from qagent.backtesting.ranking_v3_forward import RankingV3ForwardValidator
from qagent.backtesting.ranking_v3_production import (
    RankingV3ProductionAuthorizationError,
    RankingV3ProductionIdentity,
    require_current_ranking_v3_production_batch,
)
from qagent.backtesting.ranking_v3_protocol import (
    RANKING_V3_MODEL_VERSION,
    build_ranking_v3_protocol,
)
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository
from qagent.storage.ranking_v3_production import RankingV3ProductionRepository
from qagent.storage.repository import OpportunitySnapshotRecord


_RANKING_V3_SELECTION_SOURCES = frozenset(
    {
        "ranking_v3",
        "ranking-v3",
        "rank_v3",
        "rank-v3",
    }
)
@dataclass(frozen=True)
class PaperAdmissionDecision:
    eligible: bool
    reason: str | None
    selection_source: str
    model_version: str | None
    deployment_scope: str | None
    release_run_id: str | None = None
    admission_source: str = "legacy_unknown"
    production_identity_digest: str | None = None
    production_batch_fact_digest: str | None = None
    production_selection_item_digest: str | None = None
    release_proof_digest: str | None = None


def evaluate_paper_snapshot_admission(
    repo: object,
    snapshot: OpportunitySnapshotRecord,
    *,
    provider: str,
    mode: str = "automatic",
    allocation_multiplier: Decimal = Decimal("1"),
) -> PaperAdmissionDecision:
    """Admit only post-approval production selections when Ranking V3 is active."""

    if mode not in {"automatic", "manual"}:
        raise ValueError("paper admission mode must be automatic or manual")
    metadata = _selection_metadata(snapshot.card)
    selection_source = _normalized(metadata.get("selection_source"))
    model_version = _optional_string(metadata.get("model_version"))
    deployment_scope = _optional_string(metadata.get("deployment_scope"))
    claims_ranking_v3 = (
        selection_source in _RANKING_V3_SELECTION_SOURCES
        or model_version == RANKING_V3_MODEL_VERSION
        or isinstance(snapshot.card.get("ranking_v3"), Mapping)
    )
    legacy_source = "legacy_manual" if mode == "manual" else "legacy_unknown"
    protocol = build_ranking_v3_protocol()
    session_factory = getattr(repo, "session_factory", None)
    if session_factory is None:
        if claims_ranking_v3:
            return _blocked(
                "ranking_v3 authoritative stores are unavailable",
                selection_source=selection_source,
                model_version=model_version,
                deployment_scope=deployment_scope,
            )
        return _legacy_allowed(
            selection_source=selection_source,
            model_version=model_version,
            deployment_scope=deployment_scope,
            admission_source=legacy_source,
        )

    forward_repository = RankingV3ForwardRepository(session_factory)
    validator = RankingV3ForwardValidator(
        forward_repository,
        protocol,
        evidence_authority=RankingV3RepositoryEvidenceAuthority(repo),
    )
    try:
        ledger_snapshot = forward_repository.load_snapshot(validator.identity)
    except (TypeError, ValueError):
        ledger_snapshot = None
    release_proof = (
        ledger_snapshot.release_proof
        if ledger_snapshot is not None
        and ledger_snapshot.ledger.status == "approved"
        else None
    )
    if release_proof is None:
        if claims_ranking_v3:
            return _blocked(
                "ranking_v3 has no current approved release",
                selection_source=selection_source,
                model_version=model_version,
                deployment_scope=deployment_scope,
            )
        return _legacy_allowed(
            selection_source=selection_source,
            model_version=model_version,
            deployment_scope=deployment_scope,
            admission_source=legacy_source,
        )

    validation = validator.validate_release_proof(
        release_proof.proof_digest,
        expected_data_revision=release_proof.data_revision,
    )
    if not validation.valid or validation.proof is None:
        return _blocked(
            f"ranking_v3 authoritative release proof is invalid: {validation.reason}",
            selection_source=selection_source,
            model_version=model_version,
            deployment_scope=deployment_scope,
        )

    historical_evidence = next(
        (
            item
            for item in reversed(ledger_snapshot.evidence)
            if item.evidence_kind == "historical_gates"
        ),
        None,
    )
    release_run_id = (
        _optional_string(historical_evidence.payload.get("validation_run_id"))
        if historical_evidence is not None
        else None
    )
    run = _walk_forward_run(repo, release_run_id) if release_run_id else None
    if run is None or _normalized(getattr(run, "provider", "")) != _normalized(provider):
        return _blocked(
            "ranking_v3 release proof provider does not match the opportunity",
            selection_source=selection_source,
            model_version=model_version,
            deployment_scope=deployment_scope,
            release_run_id=release_run_id,
        )

    identity = RankingV3ProductionIdentity.from_release_proof(
        validation.proof,
        validation_run_id=release_run_id,
    )
    production_repository = RankingV3ProductionRepository(session_factory)
    binding = production_repository.get_selection_by_source_snapshot(
        identity,
        snapshot.snapshot_id,
    )
    if binding is None:
        return _blocked(
            "opportunity is not an exact member of the approved production batch",
            selection_source=selection_source or "ranking_v3",
            model_version=model_version or protocol.model_version,
            deployment_scope=deployment_scope or "paper",
            release_run_id=release_run_id,
        )

    expected_strategy = snapshot.primary_strategy_id or ""
    if (
        binding.instrument_id != snapshot.instrument_id
        or binding.source_snapshot_id != snapshot.snapshot_id
        or binding.strategy_id != expected_strategy
        or snapshot.signal_date is None
        or binding.session_date != snapshot.signal_date
        or binding.release_proof_digest != validation.proof.proof_digest
        or binding.trigger_price != snapshot.trigger_price
        or binding.initial_stop != snapshot.initial_stop
        or binding.target_1 != snapshot.target_1
        or binding.source_rank_score != snapshot.rank_score
        or binding.allocation_multiplier != allocation_multiplier
    ):
        return _blocked(
            "production selection does not exactly match the opportunity facts",
            selection_source="ranking_v3",
            model_version=protocol.model_version,
            deployment_scope="paper",
            release_run_id=release_run_id,
        )
    batch = production_repository.get_batch_by_fact_digest(
        identity,
        binding.batch_fact_digest,
    )
    if batch is None or batch.identity != identity:
        return _blocked(
            "production selection references a missing or mismatched immutable batch",
            selection_source="ranking_v3",
            model_version=protocol.model_version,
            deployment_scope="paper",
            release_run_id=release_run_id,
        )
    try:
        require_current_ranking_v3_production_batch(batch)
    except RankingV3ProductionAuthorizationError as exc:
        return _blocked(
            str(exc),
            selection_source="ranking_v3",
            model_version=protocol.model_version,
            deployment_scope="paper",
            release_run_id=release_run_id,
        )

    return PaperAdmissionDecision(
        eligible=True,
        reason=None,
        selection_source="ranking_v3",
        model_version=protocol.model_version,
        deployment_scope="paper",
        release_run_id=release_run_id,
        admission_source="ranking_v3_production",
        production_identity_digest=binding.identity_digest,
        production_batch_fact_digest=binding.batch_fact_digest,
        production_selection_item_digest=binding.selection_item_digest,
        release_proof_digest=binding.release_proof_digest,
    )


def _selection_metadata(card: Mapping[str, object]) -> dict[str, object]:
    nested_sources = [
        value
        for key in ("paper_admission", "recommendation_provenance", "ranking_v3")
        if isinstance((value := card.get(key)), Mapping)
    ]
    sources: list[Mapping[str, object]] = [*nested_sources, card]
    metadata: dict[str, object] = {}
    for key in (
        "selection_source",
        "model_version",
        "deployment_scope",
        "official_release_allowed",
        "candidate_id",
        "source_snapshot_id",
        "selection_digest",
        "proof_digest",
        "release_proof",
    ):
        for source in sources:
            if key in source:
                metadata[key] = source[key]
                break
    if isinstance(card.get("ranking_v3"), Mapping):
        metadata.setdefault("selection_source", "ranking_v3")
    return metadata


def _walk_forward_run(repo: object, run_id: str) -> object | None:
    getter = getattr(repo, "get_walk_forward_run", None)
    if not callable(getter):
        return None
    return getter(run_id)


def _legacy_allowed(
    *,
    selection_source: str,
    model_version: str | None,
    deployment_scope: str | None,
    admission_source: str,
    release_run_id: str | None = None,
) -> PaperAdmissionDecision:
    return PaperAdmissionDecision(
        eligible=True,
        reason=None,
        selection_source=selection_source or "legacy",
        model_version=model_version,
        deployment_scope=deployment_scope,
        release_run_id=release_run_id,
        admission_source=admission_source,
    )


def _blocked(
    reason: str,
    *,
    selection_source: str,
    model_version: str | None,
    deployment_scope: str | None,
    release_run_id: str | None = None,
) -> PaperAdmissionDecision:
    return PaperAdmissionDecision(
        eligible=False,
        reason=reason,
        selection_source=selection_source or "ranking_v3",
        model_version=model_version,
        deployment_scope=deployment_scope,
        release_run_id=release_run_id,
        admission_source="ranking_v3_production",
    )


def _normalized(value: object) -> str:
    return str(value).strip().lower() if value is not None else ""


def _optional_string(value: object) -> str | None:
    result = str(value).strip() if value is not None else ""
    return result or None
