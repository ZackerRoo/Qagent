from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from qagent.backtesting.ranking_v3 import (
    RankingV3Candidate,
    RankingV3FeatureVector,
    RankingV3FrozenScoringArtifact,
    score_ranking_v3_candidates_from_artifact,
)
from qagent.backtesting.experiment import (
    WalkForwardExperimentManifest,
    build_walk_forward_experiment_manifest,
    walk_forward_manifests_semantically_compatible,
)
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardIdentity,
    RankingV3ForwardLedgerSnapshot,
    RankingV3ForwardPortfolioInput,
    RankingV3ForwardSelectionBatchInput,
    forward_candidate_selection_digest,
    stable_digest,
)
from qagent.backtesting.ranking_v3_forward_service import (
    RankingV3ForwardCandidateFact,
    RankingV3ForwardDayFacts,
    RankingV3ForwardOutcomeFact,
)
from qagent.backtesting.ranking_v3_protocol import (
    RankingV3Protocol,
    ranking_v3_protocol_digest_is_valid,
)


RANKING_V3_FORWARD_RUNTIME_SCHEMA_VERSION = "ranking-v3-forward-runtime-v2"
_ETF_ASSET_TYPES = frozenset({"etf", "fund", "index_fund"})
_STOCK_ASSET_TYPES = frozenset({"stock", "equity", "a_share"})
_UNKNOWN_LABELS = frozenset({"", "unknown", "未知", "综合", "指数etf", "etf"})


class RankingV3CandidateSnapshotRequest(BaseModel):
    """Server-only request used to load one immutable candidate snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_run_id: str = Field(min_length=1, max_length=128)
    data_revision: str = Field(min_length=1, max_length=128)
    protocol_id: str = Field(min_length=1, max_length=128)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_version: str = Field(min_length=1, max_length=160)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_date: date


class RankingV3ServerCandidateRecord(BaseModel):
    """Raw point-in-time candidate produced by a server snapshot pipeline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_snapshot_id: str = Field(min_length=1, max_length=192)
    observed_on: date
    instrument_id: str = Field(min_length=1, max_length=32)
    baseline_rank_score: float
    primary_strategy_id: str = Field(min_length=1, max_length=96)
    factor_signals: tuple[str, ...] = ()
    market_regime: str = Field(min_length=1, max_length=96)
    asset_type: str = Field(min_length=1, max_length=32)
    industry: str | None = Field(default=None, max_length=96)
    index_memberships: tuple[str, ...] = ()
    features: RankingV3FeatureVector
    incumbent: bool = False

    @model_validator(mode="after")
    def validate_identity(self):
        if not math.isfinite(self.baseline_rank_score):
            raise ValueError("candidate baseline score must be finite")
        if not 0.0 <= self.baseline_rank_score <= 1.0:
            raise ValueError("candidate baseline score must be between zero and one")
        if len(self.factor_signals) != len(set(self.factor_signals)):
            raise ValueError("candidate factor signals must be unique")
        if len(self.index_memberships) != len(set(self.index_memberships)):
            raise ValueError("candidate index memberships must be unique")
        return self

    def to_ranking_candidate(self) -> RankingV3Candidate:
        return RankingV3Candidate(
            instrument_id=self.instrument_id,
            baseline_rank_score=self.baseline_rank_score,
            primary_strategy_id=self.primary_strategy_id,
            factor_signals=list(self.factor_signals),
            market_regime=self.market_regime,
            asset_type=self.asset_type,
            industry=self.industry,
            index_memberships=list(self.index_memberships),
            features=self.features,
            incumbent=self.incumbent,
        )


class RankingV3ServerCandidateSnapshot(BaseModel):
    """Digest-protected server snapshot. It never contains a selection decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = RANKING_V3_FORWARD_RUNTIME_SCHEMA_VERSION
    validation_run_id: str = Field(min_length=1, max_length=128)
    data_revision: str = Field(min_length=1, max_length=128)
    protocol_id: str = Field(min_length=1, max_length=128)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_version: str = Field(min_length=1, max_length=160)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_date: date
    benchmark_id: str = Field(min_length=1, max_length=64)
    candidates: tuple[RankingV3ServerCandidateRecord, ...] = ()
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_snapshot_digest(self):
        instruments = [item.instrument_id for item in self.candidates]
        if len(instruments) != len(set(instruments)):
            raise ValueError("candidate snapshot instruments must be unique")
        expected = _candidate_snapshot_digest(
            self.model_dump(mode="python", exclude={"snapshot_digest"})
        )
        if self.snapshot_digest != expected:
            raise ValueError("Ranking V3 candidate snapshot digest mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        request: RankingV3CandidateSnapshotRequest,
        benchmark_id: str,
        candidates: Sequence[RankingV3ServerCandidateRecord],
    ) -> RankingV3ServerCandidateSnapshot:
        payload = {
            "schema_version": RANKING_V3_FORWARD_RUNTIME_SCHEMA_VERSION,
            **request.model_dump(mode="python"),
            "benchmark_id": benchmark_id,
            "candidates": tuple(candidates),
        }
        return cls(
            **payload,
            snapshot_digest=_candidate_snapshot_digest(payload),
        )


class RankingV3ServerCandidateSnapshotLoader(Protocol):
    """Implemented by the server snapshot repository, never by an API caller."""

    def load_candidate_snapshot(
        self,
        request: RankingV3CandidateSnapshotRequest,
    ) -> RankingV3ServerCandidateSnapshot: ...


class RankingV3ForwardResolutionRequest(BaseModel):
    """Fully bound request issued by the authority to the market fact resolver."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_run_id: str = Field(min_length=1, max_length=128)
    data_revision: str = Field(min_length=1, max_length=128)
    protocol_id: str = Field(min_length=1, max_length=128)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_version: str = Field(min_length=1, max_length=160)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_batch_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_date: date
    benchmark_id: str = Field(min_length=1, max_length=64)
    selected_candidates: tuple[RankingV3ForwardCandidateFact, ...] = ()


class RankingV3ResolvedForwardDay(BaseModel):
    """Digest-protected outcomes and equity facts resolved from server data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = RANKING_V3_FORWARD_RUNTIME_SCHEMA_VERSION
    validation_run_id: str = Field(min_length=1, max_length=128)
    data_revision: str = Field(min_length=1, max_length=128)
    protocol_id: str = Field(min_length=1, max_length=128)
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_version: str = Field(min_length=1, max_length=160)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_batch_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_date: date
    benchmark_id: str = Field(min_length=1, max_length=64)
    benchmark_return_pct: Decimal
    portfolio_equity: Decimal = Field(gt=0)
    stress_portfolio_equity: Decimal = Field(gt=0)
    benchmark_equity: Decimal = Field(gt=0)
    mature_outcomes: tuple[RankingV3ForwardOutcomeFact, ...] = ()
    portfolio_evidence: RankingV3ForwardPortfolioInput | None = None
    resolution_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_resolution(self):
        decimals = (
            self.benchmark_return_pct,
            self.portfolio_equity,
            self.stress_portfolio_equity,
            self.benchmark_equity,
        )
        if any(not value.is_finite() for value in decimals):
            raise ValueError("resolved forward equity facts must be finite")
        outcome_ids = [item.candidate_id for item in self.mature_outcomes]
        if len(outcome_ids) != len(set(outcome_ids)):
            raise ValueError("resolved mature outcome ids must be unique")
        expected = _resolved_day_digest(
            self.model_dump(mode="python", exclude={"resolution_digest"})
        )
        if self.resolution_digest != expected:
            raise ValueError("Ranking V3 resolved day digest mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        request: RankingV3ForwardResolutionRequest,
        benchmark_return_pct: Decimal,
        portfolio_equity: Decimal,
        stress_portfolio_equity: Decimal,
        benchmark_equity: Decimal,
        mature_outcomes: Sequence[RankingV3ForwardOutcomeFact] = (),
        portfolio_evidence: RankingV3ForwardPortfolioInput | None = None,
        validation_run_id: str | None = None,
        data_revision: str | None = None,
        session_date: date | None = None,
    ) -> RankingV3ResolvedForwardDay:
        payload = {
            "schema_version": RANKING_V3_FORWARD_RUNTIME_SCHEMA_VERSION,
            "validation_run_id": validation_run_id or request.validation_run_id,
            "data_revision": data_revision or request.data_revision,
            "protocol_id": request.protocol_id,
            "protocol_digest": request.protocol_digest,
            "model_version": request.model_version,
            "artifact_digest": request.artifact_digest,
            "candidate_snapshot_digest": request.candidate_snapshot_digest,
            "selection_batch_digest": request.selection_batch_digest,
            "session_date": session_date or request.session_date,
            "benchmark_id": request.benchmark_id,
            "benchmark_return_pct": benchmark_return_pct,
            "portfolio_equity": portfolio_equity,
            "stress_portfolio_equity": stress_portfolio_equity,
            "benchmark_equity": benchmark_equity,
            "mature_outcomes": tuple(mature_outcomes),
            "portfolio_evidence": portfolio_evidence,
        }
        return cls(
            **payload,
            resolution_digest=_resolved_day_digest(payload),
        )


class RankingV3ServerForwardResolver(Protocol):
    """Implemented by server market/outcome/equity infrastructure."""

    def resolve_forward_day(
        self,
        request: RankingV3ForwardResolutionRequest,
    ) -> RankingV3ResolvedForwardDay: ...

    def recompute_portfolio_evidence(
        self,
        request: RankingV3ForwardResolutionRequest,
        ledger: RankingV3ForwardLedgerSnapshot,
    ) -> RankingV3ForwardPortfolioInput: ...


class RankingV3ProductionForwardFactAuthority:
    """Build immutable daily facts from frozen scoring and server-owned sources."""

    def __init__(
        self,
        candidate_snapshot_loader: RankingV3ServerCandidateSnapshotLoader,
        resolver: RankingV3ServerForwardResolver,
    ):
        if candidate_snapshot_loader is None or resolver is None:
            raise ValueError("production forward authority requires server data providers")
        self.candidate_snapshot_loader = candidate_snapshot_loader
        self.resolver = resolver

    def build_day_facts(
        self,
        *,
        validation_run_id: str,
        session_date: date,
        run: object,
        ranking_v3: Mapping[str, object],
        protocol: RankingV3Protocol,
        data_revision: str,
    ) -> RankingV3ForwardDayFacts:
        self._validate_authoritative_context(
            validation_run_id=validation_run_id,
            run=run,
            ranking_v3=ranking_v3,
            protocol=protocol,
        )
        artifact = self._restore_artifact(ranking_v3, protocol, session_date)
        request = RankingV3CandidateSnapshotRequest(
            validation_run_id=validation_run_id,
            data_revision=data_revision,
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.protocol_digest,
            model_version=protocol.model_version,
            artifact_digest=artifact.stable_digest,
            session_date=session_date,
        )
        snapshot = self.candidate_snapshot_loader.load_candidate_snapshot(request)
        self._validate_candidate_snapshot(snapshot, request, protocol)
        selected, selection_batch_digest = self._rank_and_select(
            snapshot=snapshot,
            artifact=artifact,
            protocol=protocol,
        )
        resolution_request = RankingV3ForwardResolutionRequest(
            **request.model_dump(mode="python"),
            candidate_snapshot_digest=snapshot.snapshot_digest,
            selection_batch_digest=selection_batch_digest,
            benchmark_id=snapshot.benchmark_id,
            selected_candidates=selected,
        )
        resolved = self.resolver.resolve_forward_day(resolution_request)
        self._validate_resolved_day(resolved, resolution_request)
        return RankingV3ForwardDayFacts(
            validation_run_id=validation_run_id,
            session_date=session_date,
            benchmark_id=snapshot.benchmark_id,
            benchmark_return_pct=resolved.benchmark_return_pct,
            portfolio_equity=resolved.portfolio_equity,
            stress_portfolio_equity=resolved.stress_portfolio_equity,
            benchmark_equity=resolved.benchmark_equity,
            candidate_snapshot_digest=snapshot.snapshot_digest,
            selection_batch_digest=selection_batch_digest,
            selected_candidate_count=len(selected),
            candidates=selected,
            mature_outcomes=resolved.mature_outcomes,
            portfolio_evidence=resolved.portfolio_evidence,
        )

    def recompute_portfolio_evidence(
        self,
        *,
        validation_run_id: str,
        session_date: date,
        run: object,
        ranking_v3: Mapping[str, object],
        protocol: RankingV3Protocol,
        data_revision: str,
        ledger: RankingV3ForwardLedgerSnapshot,
        submitted: RankingV3ForwardPortfolioInput,
        frozen_batch: RankingV3ForwardSelectionBatchInput,
    ) -> RankingV3ForwardPortfolioInput | None:
        self._validate_authoritative_context(
            validation_run_id=validation_run_id,
            run=run,
            ranking_v3=ranking_v3,
            protocol=protocol,
        )
        if (
            ledger.ledger.identity != RankingV3ForwardIdentity.from_protocol(protocol)
            or ledger.ledger.data_revision != data_revision
            or submitted.validation_run_id != validation_run_id
            or submitted.data_revision != data_revision
            or submitted.as_of_session_date != session_date
            or submitted.benchmark_id
            != protocol.benchmark_definition.forward_release_benchmark_id
        ):
            return None
        artifact = self._restore_artifact(ranking_v3, protocol, session_date)
        request = RankingV3ForwardResolutionRequest(
            validation_run_id=validation_run_id,
            data_revision=data_revision,
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.protocol_digest,
            model_version=protocol.model_version,
            artifact_digest=artifact.stable_digest,
            candidate_snapshot_digest=frozen_batch.candidate_snapshot_digest,
            selection_batch_digest=frozen_batch.selection_batch_digest,
            session_date=session_date,
            benchmark_id=frozen_batch.benchmark_id,
            selected_candidates=tuple(
                RankingV3ForwardCandidateFact(
                    source_snapshot_id=item.source_snapshot_id,
                    instrument_id=item.instrument_id,
                    strategy_id=item.strategy_id,
                    rank=item.rank,
                    score=item.score,
                    benchmark_id=item.benchmark_id,
                    selection_digest=item.selection_digest,
                )
                for item in frozen_batch.candidates
            ),
        )
        recomputed = self.resolver.recompute_portfolio_evidence(request, ledger)
        if not isinstance(recomputed, RankingV3ForwardPortfolioInput):
            raise TypeError("forward resolver returned invalid recomputed portfolio evidence")
        return RankingV3ForwardPortfolioInput.model_validate(
            recomputed.model_dump(mode="python")
        )

    @staticmethod
    def _validate_authoritative_context(
        *,
        validation_run_id: str,
        run: object,
        ranking_v3: Mapping[str, object],
        protocol: RankingV3Protocol,
    ) -> None:
        if _text(getattr(run, "run_id", None)) != validation_run_id:
            raise ValueError("production authority received a mismatched walk-forward run")
        if _text(getattr(run, "status", None)) != "succeeded":
            raise ValueError("production authority requires a successful walk-forward run")
        payload = getattr(run, "payload", None)
        authoritative = payload.get("ranking_v3") if isinstance(payload, Mapping) else None
        if not isinstance(authoritative, Mapping):
            raise ValueError("successful walk-forward run has no Ranking V3 payload")
        manifest_payload = (
            payload.get("experiment_manifest") if isinstance(payload, Mapping) else None
        )
        if not isinstance(manifest_payload, Mapping):
            raise ValueError("successful walk-forward run has no experiment manifest")
        try:
            stored_manifest = WalkForwardExperimentManifest.model_validate(manifest_payload)
            current_manifest = build_walk_forward_experiment_manifest(
                provider_mode=stored_manifest.provider_mode,
                dataset_revision=stored_manifest.dataset_revision,
                start_date=stored_manifest.start_date,
                end_date=stored_manifest.end_date,
                rebalance_step_sessions=stored_manifest.rebalance_step_sessions,
                lookback_days=stored_manifest.lookback_days,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("walk-forward experiment manifest is invalid") from exc
        if not walk_forward_manifests_semantically_compatible(
            stored_manifest,
            current_manifest,
        ):
            raise ValueError(
                "walk-forward experiment manifest is incompatible with current research inputs"
            )
        if stable_digest(authoritative) != stable_digest(ranking_v3):
            raise ValueError("Ranking V3 payload does not belong to the authoritative run")
        if not ranking_v3_protocol_digest_is_valid(protocol):
            raise ValueError("production authority received an invalid frozen protocol")
        protocol_payload = ranking_v3.get("protocol")
        if not isinstance(protocol_payload, Mapping):
            raise ValueError("Ranking V3 payload has no frozen protocol")
        payload_protocol = RankingV3Protocol.model_validate(protocol_payload)
        if payload_protocol.protocol_digest != protocol.protocol_digest:
            raise ValueError("Ranking V3 payload protocol does not match the service protocol")
        if _text(ranking_v3.get("model_version")) != protocol.model_version:
            raise ValueError("Ranking V3 payload model version does not match the protocol")
        if _text(ranking_v3.get("status")) not in {
            "forward_validation_pending",
            "shadow_candidate",
        }:
            raise ValueError("Ranking V3 historical gates have not admitted forward validation")

    @staticmethod
    def _restore_artifact(
        ranking_v3: Mapping[str, object],
        protocol: RankingV3Protocol,
        session_date: date,
    ) -> RankingV3FrozenScoringArtifact:
        payload = ranking_v3.get("forward_scoring_artifact")
        if isinstance(payload, RankingV3FrozenScoringArtifact):
            artifact = RankingV3FrozenScoringArtifact.model_validate(
                payload.model_dump(mode="json")
            )
        elif isinstance(payload, Mapping):
            artifact = RankingV3FrozenScoringArtifact.model_validate(payload)
        else:
            raise ValueError("Ranking V3 payload has no frozen forward scoring artifact")
        declared_digest = ranking_v3.get("forward_scoring_artifact_digest")
        if declared_digest is not None and _text(declared_digest) != artifact.stable_digest:
            raise ValueError("declared forward scoring artifact digest is invalid")
        if artifact.model_version != protocol.model_version:
            raise ValueError("forward scoring artifact model version does not match the protocol")
        if artifact.cutoff != protocol.prospective_shadow_start:
            raise ValueError("forward scoring artifact cutoff does not match the frozen protocol")
        if session_date < artifact.cutoff:
            raise ValueError("forward scoring cannot run before the frozen artifact cutoff")
        if not artifact.model_ready:
            raise ValueError("forward scoring artifact does not contain enough training evidence")
        return artifact

    @classmethod
    def _validate_candidate_snapshot(
        cls,
        snapshot: RankingV3ServerCandidateSnapshot,
        request: RankingV3CandidateSnapshotRequest,
        protocol: RankingV3Protocol,
    ) -> None:
        if not isinstance(snapshot, RankingV3ServerCandidateSnapshot):
            raise TypeError("candidate loader returned an invalid server snapshot")
        RankingV3ServerCandidateSnapshot.model_validate(snapshot.model_dump(mode="python"))
        bindings = (
            ("validation run", snapshot.validation_run_id, request.validation_run_id),
            ("data revision", snapshot.data_revision, request.data_revision),
            ("protocol id", snapshot.protocol_id, request.protocol_id),
            ("protocol digest", snapshot.protocol_digest, request.protocol_digest),
            ("model version", snapshot.model_version, request.model_version),
            ("artifact digest", snapshot.artifact_digest, request.artifact_digest),
            ("session date", snapshot.session_date, request.session_date),
        )
        for label, actual, expected in bindings:
            if actual != expected:
                raise ValueError(f"candidate snapshot {label} does not match the authority request")
        if (
            snapshot.benchmark_id
            != protocol.benchmark_definition.forward_release_benchmark_id
        ):
            raise ValueError(
                "candidate snapshot benchmark does not match the frozen protocol"
            )
        if len(snapshot.candidates) > protocol.candidate_pool_limit:
            raise ValueError("candidate snapshot exceeds the frozen candidate pool limit")
        for item in snapshot.candidates:
            if item.observed_on != request.session_date:
                raise ValueError("candidate observation date does not match the requested session")
            cls._validate_source_candidate(
                item,
                minimum_data_completeness=(
                    protocol.ranking_definition.minimum_candidate_data_completeness
                ),
            )

    @staticmethod
    def _validate_source_candidate(
        item: RankingV3ServerCandidateRecord,
        *,
        minimum_data_completeness: float,
    ) -> None:
        if item.instrument_id != item.instrument_id.strip():
            raise ValueError("candidate instrument id is not canonical")
        if not item.primary_strategy_id.strip():
            raise ValueError("candidate primary strategy is missing")
        if not item.market_regime.strip():
            raise ValueError("candidate market regime is missing")
        asset_type = item.asset_type.strip().lower()
        if asset_type not in _ETF_ASSET_TYPES | _STOCK_ASSET_TYPES:
            raise ValueError("candidate asset type is unsupported or incomplete")
        feature_fields = set(RankingV3FeatureVector.model_fields)
        if set(item.features.model_fields_set) != feature_fields:
            raise ValueError("candidate feature vector is incomplete")
        if item.features.data_completeness < minimum_data_completeness:
            raise ValueError("candidate data completeness is below the production threshold")
        if asset_type in _STOCK_ASSET_TYPES:
            industry = (item.industry or "").strip().lower()
            if industry in _UNKNOWN_LABELS:
                raise ValueError("stock candidate industry is incomplete")
        if asset_type in _ETF_ASSET_TYPES and not item.index_memberships:
            raise ValueError("ETF candidate index memberships are incomplete")
        if any(not value.strip() for value in item.factor_signals):
            raise ValueError("candidate factor signal is incomplete")
        if any(not value.strip() for value in item.index_memberships):
            raise ValueError("candidate index membership is incomplete")

    @classmethod
    def _rank_and_select(
        cls,
        *,
        snapshot: RankingV3ServerCandidateSnapshot,
        artifact: RankingV3FrozenScoringArtifact,
        protocol: RankingV3Protocol,
    ) -> tuple[tuple[RankingV3ForwardCandidateFact, ...], str]:
        source_by_instrument = {item.instrument_id: item for item in snapshot.candidates}
        decision = score_ranking_v3_candidates_from_artifact(
            [item.to_ranking_candidate() for item in snapshot.candidates],
            artifact,
            decision_date=snapshot.session_date,
        )
        selected_scores = []
        for score in decision.candidates:
            trial = [*selected_scores, score]
            if not cls._constraints_hold(
                trial,
                source_by_instrument=source_by_instrument,
                protocol=protocol,
            ):
                continue
            selected_scores.append(score)
            if len(selected_scores) >= protocol.max_positions:
                break
        ranking_payload = [
            {
                "instrument_id": item.instrument_id,
                "source_snapshot_id": source_by_instrument[item.instrument_id].source_snapshot_id,
                "v3_position": item.v3_position,
                "v3_score": item.v3_score,
                "baseline_position": item.baseline_position,
            }
            for item in decision.candidates
        ]
        selected_payload = [
            {
                "instrument_id": item.instrument_id,
                "source_snapshot_id": source_by_instrument[item.instrument_id].source_snapshot_id,
                "rank": rank,
                "strategy_id": source_by_instrument[item.instrument_id].primary_strategy_id,
                "score": item.v3_score,
            }
            for rank, item in enumerate(selected_scores, start=1)
        ]
        selection_batch_digest = stable_digest(
            {
                "schema_version": RANKING_V3_FORWARD_RUNTIME_SCHEMA_VERSION,
                "validation_run_id": snapshot.validation_run_id,
                "data_revision": snapshot.data_revision,
                "protocol_id": snapshot.protocol_id,
                "protocol_digest": snapshot.protocol_digest,
                "model_version": snapshot.model_version,
                "artifact_digest": artifact.stable_digest,
                "candidate_snapshot_digest": snapshot.snapshot_digest,
                "session_date": snapshot.session_date,
                "benchmark_id": snapshot.benchmark_id,
                "ranking": ranking_payload,
                "selected": selected_payload,
            }
        )
        selected = tuple(
            RankingV3ForwardCandidateFact(
                source_snapshot_id=source_by_instrument[item.instrument_id].source_snapshot_id,
                instrument_id=item.instrument_id,
                strategy_id=source_by_instrument[item.instrument_id].primary_strategy_id,
                rank=rank,
                score=Decimal(str(item.v3_score)),
                benchmark_id=snapshot.benchmark_id,
                selection_digest=forward_candidate_selection_digest(
                    selection_batch_digest=selection_batch_digest,
                    source_snapshot_id=source_by_instrument[
                        item.instrument_id
                    ].source_snapshot_id,
                    instrument_id=item.instrument_id,
                    strategy_id=source_by_instrument[
                        item.instrument_id
                    ].primary_strategy_id,
                    rank=rank,
                    score=Decimal(str(item.v3_score)),
                ),
            )
            for rank, item in enumerate(selected_scores, start=1)
        )
        return selected, selection_batch_digest

    @staticmethod
    def _constraints_hold(
        scores: Sequence[object],
        *,
        source_by_instrument: Mapping[str, RankingV3ServerCandidateRecord],
        protocol: RankingV3Protocol,
    ) -> bool:
        strategy_counts: dict[str, int] = {}
        industry_counts: dict[str, int] = {}
        etf_overlap_counts: dict[str, int] = {}
        for score in scores:
            instrument_id = _text(getattr(score, "instrument_id", None))
            source = source_by_instrument[instrument_id]
            strategy = source.primary_strategy_id.strip()
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
            if strategy_counts[strategy] > protocol.max_per_strategy:
                return False
            asset_type = source.asset_type.strip().lower()
            if asset_type in _ETF_ASSET_TYPES:
                for membership in source.index_memberships:
                    key = membership.strip()
                    etf_overlap_counts[key] = etf_overlap_counts.get(key, 0) + 1
                    if etf_overlap_counts[key] > protocol.max_etf_index_overlap:
                        return False
                continue
            industry = (source.industry or "").strip()
            industry_counts[industry] = industry_counts.get(industry, 0) + 1
            if industry_counts[industry] > protocol.max_per_industry:
                return False
        return True

    @staticmethod
    def _validate_resolved_day(
        resolved: RankingV3ResolvedForwardDay,
        request: RankingV3ForwardResolutionRequest,
    ) -> None:
        if not isinstance(resolved, RankingV3ResolvedForwardDay):
            raise TypeError("forward resolver returned an invalid resolved day")
        RankingV3ResolvedForwardDay.model_validate(resolved.model_dump(mode="python"))
        bindings = (
            ("validation run", resolved.validation_run_id, request.validation_run_id),
            ("data revision", resolved.data_revision, request.data_revision),
            ("protocol id", resolved.protocol_id, request.protocol_id),
            ("protocol digest", resolved.protocol_digest, request.protocol_digest),
            ("model version", resolved.model_version, request.model_version),
            ("artifact digest", resolved.artifact_digest, request.artifact_digest),
            (
                "candidate snapshot digest",
                resolved.candidate_snapshot_digest,
                request.candidate_snapshot_digest,
            ),
            (
                "selection batch digest",
                resolved.selection_batch_digest,
                request.selection_batch_digest,
            ),
            ("session date", resolved.session_date, request.session_date),
            ("benchmark", resolved.benchmark_id, request.benchmark_id),
        )
        for label, actual, expected in bindings:
            if actual != expected:
                raise ValueError(f"resolved forward {label} does not match the authority request")
        if resolved.portfolio_evidence is not None and (
            resolved.portfolio_evidence.validation_run_id != request.validation_run_id
            or resolved.portfolio_evidence.data_revision != request.data_revision
            or resolved.portfolio_evidence.as_of_session_date != request.session_date
        ):
            raise ValueError("resolved forward portfolio evidence does not match the request")


def _candidate_snapshot_digest(payload: Mapping[str, object]) -> str:
    canonical = dict(payload)
    candidates = canonical.get("candidates", ())
    canonical["candidates"] = sorted(
        candidates,
        key=lambda item: _text(
            item.instrument_id
            if isinstance(item, RankingV3ServerCandidateRecord)
            else item.get("instrument_id")
        ),
    )
    return stable_digest(canonical)


def _resolved_day_digest(payload: Mapping[str, object]) -> str:
    canonical = dict(payload)
    outcomes = canonical.get("mature_outcomes", ())
    canonical["mature_outcomes"] = sorted(
        outcomes,
        key=lambda item: _text(
            item.candidate_id
            if isinstance(item, RankingV3ForwardOutcomeFact)
            else item.get("candidate_id")
        ),
    )
    return stable_digest(canonical)


def _text(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()
