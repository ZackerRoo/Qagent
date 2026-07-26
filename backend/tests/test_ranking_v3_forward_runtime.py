from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from qagent.backtesting.ranking_v3 import (
    RankingV3FeatureVector,
    ResolvedRankingV3Observation,
    build_ranking_v3_frozen_scoring_artifact,
)
from qagent.backtesting.experiment import build_walk_forward_experiment_manifest
from qagent.backtesting.ranking_v3_forward_runtime import (
    RankingV3CandidateSnapshotRequest,
    RankingV3ForwardResolutionRequest,
    RankingV3ProductionForwardFactAuthority,
    RankingV3ResolvedForwardDay,
    RankingV3ServerCandidateRecord,
    RankingV3ServerCandidateSnapshot,
)
from qagent.backtesting.ranking_v3_protocol import build_ranking_v3_protocol


RUN_ID = "ranking-v3-production-run"
DATA_REVISION = "revision-authoritative-20260726"
BENCHMARK_ID = "CN:000300.IDX"
SESSION_DATE = date(2026, 7, 27)


def _features(
    strength: float,
    *,
    completeness: float = 1.0,
    complete_vector: bool = True,
) -> RankingV3FeatureVector:
    if not complete_vector:
        return RankingV3FeatureVector(
            strategy_score=strength,
            data_completeness=completeness,
        )
    return RankingV3FeatureVector(
        strategy_score=strength,
        factor_score=strength,
        valuation=strength,
        size=strength,
        quality=strength,
        momentum=strength,
        trend_quality=strength,
        liquidity=strength,
        low_risk=strength,
        risk_filter=strength,
        reversal=strength,
        execution_penalty=0.0,
        data_completeness=completeness,
    )


def _artifact():
    observations = []
    for index in range(120):
        signal_date = date(2025, 1, 2) + timedelta(days=index // 5)
        observations.append(
            ResolvedRankingV3Observation(
                instrument_id=f"CN:{index:06d}",
                signal_date=signal_date,
                available_at=signal_date + timedelta(days=1),
                outcome_status="resolved",
                triggered=True,
                return_pct=1.0,
                benchmark_return_pct=0.2,
                net_excess_return_pct=0.8 if index % 3 else -0.2,
                primary_strategy_id=f"strategy-{index % 4}",
                factor_signals=["quality", "trend"],
                market_regime="balanced",
                asset_type="stock",
                features=_features(0.75 if index % 2 else 0.65),
            )
        )
    protocol = build_ranking_v3_protocol()
    artifact = build_ranking_v3_frozen_scoring_artifact(
        observations,
        cutoff=protocol.prospective_shadow_start,
    )
    assert artifact.model_ready is True
    return protocol, artifact


def _ranking_context():
    protocol, artifact = _artifact()
    experiment_manifest = build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2025, 1, 2),
        end_date=date(2026, 7, 24),
        rebalance_step_sessions=10,
        lookback_days=365,
    )
    ranking_v3 = {
        "status": "forward_validation_pending",
        "model_version": protocol.model_version,
        "protocol": protocol.model_dump(mode="json"),
        "forward_scoring_artifact": artifact.model_dump(mode="json"),
        "forward_scoring_artifact_digest": artifact.stable_digest,
    }
    run = SimpleNamespace(
        run_id=RUN_ID,
        status="succeeded",
        dataset_revision=7,
        payload={
            "ranking_v3": ranking_v3,
            "experiment_manifest": experiment_manifest.model_dump(mode="json"),
        },
    )
    return protocol, artifact, ranking_v3, run


def _candidate(
    instrument_id: str,
    strength: float,
    *,
    strategy: str,
    industry: str | None,
    asset_type: str = "stock",
    memberships: tuple[str, ...] = (),
    observed_on: date = SESSION_DATE,
    features: RankingV3FeatureVector | None = None,
) -> RankingV3ServerCandidateRecord:
    return RankingV3ServerCandidateRecord(
        source_snapshot_id=f"server-candidate-source:{observed_on.isoformat()}:{instrument_id}",
        observed_on=observed_on,
        instrument_id=instrument_id,
        baseline_rank_score=strength,
        primary_strategy_id=strategy,
        factor_signals=("quality", "trend"),
        market_regime="balanced",
        asset_type=asset_type,
        industry=industry,
        index_memberships=memberships,
        features=features or _features(strength),
    )


def _candidate_pool():
    return (
        _candidate("CN:600001", 0.95, strategy="s1", industry="芯片"),
        _candidate("CN:600002", 0.94, strategy="s1", industry="芯片"),
        _candidate("CN:600003", 0.93, strategy="s1", industry="机器人"),
        _candidate("CN:600004", 0.92, strategy="s2", industry="芯片"),
        _candidate("CN:600005", 0.91, strategy="s2", industry="电力"),
        _candidate(
            "CN:588000",
            0.90,
            strategy="s3",
            industry="ETF",
            asset_type="etf",
            memberships=("科创50",),
        ),
        _candidate(
            "CN:588001",
            0.89,
            strategy="s4",
            industry="ETF",
            asset_type="etf",
            memberships=("科创50",),
        ),
        _candidate("CN:600006", 0.88, strategy="s4", industry="医药"),
    )


class _CandidateLoader:
    def __init__(
        self,
        candidates=(),
        *,
        snapshot_factory=None,
    ):
        self.candidates = tuple(candidates)
        self.snapshot_factory = snapshot_factory
        self.requests: list[RankingV3CandidateSnapshotRequest] = []

    def load_candidate_snapshot(self, request):
        self.requests.append(request)
        if self.snapshot_factory is not None:
            return self.snapshot_factory(request)
        return RankingV3ServerCandidateSnapshot.create(
            request=request,
            benchmark_id=BENCHMARK_ID,
            candidates=self.candidates,
        )


class _Resolver:
    def __init__(self, *, overrides=None, resolved_factory=None):
        self.overrides = dict(overrides or {})
        self.resolved_factory = resolved_factory
        self.requests: list[RankingV3ForwardResolutionRequest] = []

    def resolve_forward_day(self, request):
        self.requests.append(request)
        if self.resolved_factory is not None:
            return self.resolved_factory(request)
        return RankingV3ResolvedForwardDay.create(
            request=request,
            validation_run_id=self.overrides.get("validation_run_id"),
            data_revision=self.overrides.get("data_revision"),
            session_date=self.overrides.get("session_date"),
            benchmark_return_pct=Decimal("0.25"),
            portfolio_equity=Decimal("100100"),
            stress_portfolio_equity=Decimal("100080"),
            benchmark_equity=Decimal("100020"),
        )

    def recompute_portfolio_evidence(self, request, ledger):
        raise AssertionError("portfolio recomputation is not expected in this test")


def _build(authority, *, ranking_v3=None, run=None):
    protocol, _artifact_value, default_ranking_v3, default_run = _ranking_context()
    active_ranking_v3 = ranking_v3 or default_ranking_v3
    active_run = run or default_run
    return authority.build_day_facts(
        validation_run_id=RUN_ID,
        session_date=SESSION_DATE,
        run=active_run,
        ranking_v3=active_ranking_v3,
        protocol=protocol,
        data_revision=DATA_REVISION,
    )


def test_production_authority_scores_deterministically_and_applies_all_constraints():
    candidates = _candidate_pool()
    first_loader = _CandidateLoader(candidates)
    first_resolver = _Resolver()
    first = _build(RankingV3ProductionForwardFactAuthority(first_loader, first_resolver))
    reversed_loader = _CandidateLoader(tuple(reversed(candidates)))
    reversed_resolver = _Resolver()
    reversed_result = _build(
        RankingV3ProductionForwardFactAuthority(
            reversed_loader,
            reversed_resolver,
        )
    )

    assert first == reversed_result
    assert len(first.candidates) == 5
    assert [item.rank for item in first.candidates] == [1, 2, 3, 4, 5]
    assert all(item.benchmark_id == BENCHMARK_ID for item in first.candidates)
    assert first_loader.requests[0].artifact_digest
    assert (
        first_resolver.requests[0].candidate_snapshot_digest
        == reversed_resolver.requests[0].candidate_snapshot_digest
    )
    assert (
        first_resolver.requests[0].selection_batch_digest
        == reversed_resolver.requests[0].selection_batch_digest
    )
    assert first.candidate_snapshot_digest == first_resolver.requests[0].candidate_snapshot_digest
    assert first.selection_batch_digest == first_resolver.requests[0].selection_batch_digest
    assert first.selected_candidate_count == len(first.candidates)
    assert len({item.selection_digest for item in first.candidates}) == 5
    assert all(item.source_snapshot_id for item in first.candidates)
    source = {item.instrument_id: item for item in candidates}
    strategies = Counter(
        source[item.instrument_id].primary_strategy_id for item in first.candidates
    )
    industries = Counter(
        source[item.instrument_id].industry
        for item in first.candidates
        if source[item.instrument_id].asset_type == "stock"
    )
    memberships = Counter(
        membership
        for item in first.candidates
        for membership in source[item.instrument_id].index_memberships
    )
    assert max(strategies.values()) <= 2
    assert max(industries.values()) <= 2
    assert max(memberships.values()) <= 1
    assert first_resolver.requests[0].selected_candidates == first.candidates


def test_candidate_snapshot_rejects_consistent_non_protocol_benchmark():
    loader = _CandidateLoader(
        _candidate_pool(),
        snapshot_factory=lambda request: RankingV3ServerCandidateSnapshot.create(
            request=request,
            benchmark_id="CN:000905.IDX",
            candidates=_candidate_pool(),
        ),
    )

    with pytest.raises(ValueError, match="benchmark does not match"):
        _build(RankingV3ProductionForwardFactAuthority(loader, _Resolver()))


def test_production_authority_requires_a_stored_experiment_manifest():
    protocol, _artifact_value, ranking_v3, run = _ranking_context()
    missing = SimpleNamespace(
        **{
            **vars(run),
            "payload": {"ranking_v3": ranking_v3},
        }
    )
    authority = RankingV3ProductionForwardFactAuthority(
        _CandidateLoader(_candidate_pool()),
        _Resolver(),
    )

    with pytest.raises(ValueError, match="no experiment manifest"):
        authority.build_day_facts(
            validation_run_id=RUN_ID,
            session_date=SESSION_DATE,
            run=missing,
            ranking_v3=ranking_v3,
            protocol=protocol,
            data_revision=DATA_REVISION,
        )


def test_production_authority_rejects_semantically_changed_strategy_registry():
    protocol, _artifact_value, ranking_v3, run = _ranking_context()
    manifest = dict(run.payload["experiment_manifest"])
    manifest["strategy_ids"] = ["forged-strategy-registry"]
    incompatible = SimpleNamespace(
        **{
            **vars(run),
            "payload": {
                "ranking_v3": ranking_v3,
                "experiment_manifest": manifest,
            },
        }
    )
    authority = RankingV3ProductionForwardFactAuthority(
        _CandidateLoader(_candidate_pool()),
        _Resolver(),
    )

    with pytest.raises(ValueError, match="incompatible with current research inputs"):
        authority.build_day_facts(
            validation_run_id=RUN_ID,
            session_date=SESSION_DATE,
            run=incompatible,
            ranking_v3=ranking_v3,
            protocol=protocol,
            data_revision=DATA_REVISION,
        )


def test_tampered_forward_artifact_is_rejected_before_loading_candidates():
    protocol, _artifact_value, ranking_v3, original_run = _ranking_context()
    tampered = deepcopy(ranking_v3)
    tampered["forward_scoring_artifact"]["training_observation_count"] += 1
    run = SimpleNamespace(
        **{
            **vars(original_run),
            "payload": {
                **original_run.payload,
                "ranking_v3": tampered,
            },
        }
    )
    loader = _CandidateLoader(_candidate_pool())
    authority = RankingV3ProductionForwardFactAuthority(loader, _Resolver())

    with pytest.raises(ValueError, match="artifact digest mismatch"):
        authority.build_day_facts(
            validation_run_id=RUN_ID,
            session_date=SESSION_DATE,
            run=run,
            ranking_v3=tampered,
            protocol=protocol,
            data_revision=DATA_REVISION,
        )

    assert loader.requests == []


def test_missing_forward_artifact_fails_closed():
    protocol, _artifact_value, ranking_v3, original_run = _ranking_context()
    missing = deepcopy(ranking_v3)
    missing.pop("forward_scoring_artifact")
    run = SimpleNamespace(
        **{
            **vars(original_run),
            "payload": {
                **original_run.payload,
                "ranking_v3": missing,
            },
        }
    )
    loader = _CandidateLoader(_candidate_pool())

    with pytest.raises(ValueError, match="no frozen forward scoring artifact"):
        RankingV3ProductionForwardFactAuthority(loader, _Resolver()).build_day_facts(
            validation_run_id=RUN_ID,
            session_date=SESSION_DATE,
            run=run,
            ranking_v3=missing,
            protocol=protocol,
            data_revision=DATA_REVISION,
        )

    assert loader.requests == []


@pytest.mark.parametrize(
    "observed_on",
    [
        SESSION_DATE - timedelta(days=1),
        SESSION_DATE + timedelta(days=1),
    ],
)
def test_past_or_future_dated_candidates_are_rejected(observed_on):
    loader = _CandidateLoader(
        (
            _candidate(
                "CN:600001",
                0.9,
                strategy="s1",
                industry="芯片",
                observed_on=observed_on,
            ),
        )
    )
    resolver = _Resolver()

    with pytest.raises(ValueError, match="observation date"):
        _build(RankingV3ProductionForwardFactAuthority(loader, resolver))

    assert resolver.requests == []


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        (
            _candidate(
                "CN:600001",
                0.9,
                strategy="s1",
                industry="芯片",
                features=_features(0.9, complete_vector=False),
            ),
            "feature vector is incomplete",
        ),
        (
            _candidate(
                "CN:600001",
                0.9,
                strategy="s1",
                industry="芯片",
                features=_features(0.9, completeness=0.67),
            ),
            "data completeness",
        ),
        (
            _candidate(
                "CN:600001",
                0.9,
                strategy="s1",
                industry=None,
            ),
            "industry is incomplete",
        ),
        (
            _candidate(
                "CN:588000",
                0.9,
                strategy="s1",
                industry="ETF",
                asset_type="etf",
            ),
            "index memberships are incomplete",
        ),
    ],
)
def test_incomplete_candidate_data_fails_closed(candidate, message):
    resolver = _Resolver()
    authority = RankingV3ProductionForwardFactAuthority(
        _CandidateLoader((candidate,)),
        resolver,
    )

    with pytest.raises(ValueError, match=message):
        _build(authority)

    assert resolver.requests == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"validation_run_id": "wrong-run"}, "validation run"),
        ({"data_revision": "wrong-revision"}, "data revision"),
        (
            {"session_date": SESSION_DATE + timedelta(days=1)},
            "session date",
        ),
    ],
)
def test_resolver_run_revision_or_date_mismatch_is_rejected(overrides, message):
    resolver = _Resolver(overrides=overrides)
    authority = RankingV3ProductionForwardFactAuthority(
        _CandidateLoader(_candidate_pool()),
        resolver,
    )

    with pytest.raises(ValueError, match=message):
        _build(authority)

    assert len(resolver.requests) == 1


def test_tampered_candidate_snapshot_digest_is_rejected_before_resolver():
    def snapshot_factory(request):
        valid = RankingV3ServerCandidateSnapshot.create(
            request=request,
            benchmark_id=BENCHMARK_ID,
            candidates=_candidate_pool(),
        )
        payload = {
            name: getattr(valid, name) for name in RankingV3ServerCandidateSnapshot.model_fields
        }
        payload["snapshot_digest"] = "0" * 64
        return RankingV3ServerCandidateSnapshot.model_construct(**payload)

    resolver = _Resolver()
    authority = RankingV3ProductionForwardFactAuthority(
        _CandidateLoader(snapshot_factory=snapshot_factory),
        resolver,
    )

    with pytest.raises(ValueError, match="candidate snapshot digest mismatch"):
        _build(authority)

    assert resolver.requests == []


def test_source_snapshot_tampering_is_detected_and_changes_all_selection_digests():
    candidates = _candidate_pool()
    original_loader = _CandidateLoader(candidates)
    original_resolver = _Resolver()
    original = _build(
        RankingV3ProductionForwardFactAuthority(
            original_loader,
            original_resolver,
        )
    )
    changed_candidates = list(candidates)
    changed_candidates[0] = changed_candidates[0].model_copy(
        update={"source_snapshot_id": "server-candidate-source:replacement"}
    )
    changed_loader = _CandidateLoader(tuple(changed_candidates))
    changed_resolver = _Resolver()
    changed = _build(
        RankingV3ProductionForwardFactAuthority(
            changed_loader,
            changed_resolver,
        )
    )

    assert (
        original_resolver.requests[0].candidate_snapshot_digest
        != changed_resolver.requests[0].candidate_snapshot_digest
    )
    assert (
        original_resolver.requests[0].selection_batch_digest
        != changed_resolver.requests[0].selection_batch_digest
    )
    original_by_instrument = {item.instrument_id: item for item in original.candidates}
    changed_by_instrument = {item.instrument_id: item for item in changed.candidates}
    assert (
        original_by_instrument["CN:600001"].selection_digest
        != changed_by_instrument["CN:600001"].selection_digest
    )
    assert (
        changed_by_instrument["CN:600001"].source_snapshot_id
        == "server-candidate-source:replacement"
    )

    valid = RankingV3ServerCandidateSnapshot.create(
        request=original_loader.requests[0],
        benchmark_id=BENCHMARK_ID,
        candidates=candidates,
    )
    tampered_candidates = list(valid.candidates)
    tampered_candidates[0] = tampered_candidates[0].model_copy(
        update={"source_snapshot_id": "server-candidate-source:tampered"}
    )
    tampered = valid.model_copy(update={"candidates": tuple(tampered_candidates)})
    with pytest.raises(ValueError, match="candidate snapshot digest mismatch"):
        RankingV3ServerCandidateSnapshot.model_validate(tampered.model_dump(mode="python"))


def test_tampered_resolver_digest_is_rejected():
    def resolved_factory(request):
        valid = RankingV3ResolvedForwardDay.create(
            request=request,
            benchmark_return_pct=Decimal("0.25"),
            portfolio_equity=Decimal("100100"),
            stress_portfolio_equity=Decimal("100080"),
            benchmark_equity=Decimal("100020"),
        )
        payload = {name: getattr(valid, name) for name in RankingV3ResolvedForwardDay.model_fields}
        payload["resolution_digest"] = "0" * 64
        return RankingV3ResolvedForwardDay.model_construct(**payload)

    authority = RankingV3ProductionForwardFactAuthority(
        _CandidateLoader(_candidate_pool()),
        _Resolver(resolved_factory=resolved_factory),
    )

    with pytest.raises(ValueError, match="resolved day digest mismatch"):
        _build(authority)
