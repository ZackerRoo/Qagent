from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy.orm import sessionmaker

import qagent.jobs.ranking_v3_forward as ranking_v3_forward_job
from qagent.backtesting.ranking_v3 import (
    RankingV3FeatureVector,
    ResolvedRankingV3Observation,
    build_ranking_v3_frozen_scoring_artifact,
)
from qagent.backtesting.experiment import build_walk_forward_experiment_manifest
from qagent.backtesting.ranking_v3_forward import (
    RankingV3ForwardIdentity,
    RankingV3ForwardSessionInput,
    stable_digest,
)
from qagent.backtesting.ranking_v3_forward_runtime import (
    RankingV3CandidateSnapshotRequest,
    RankingV3ForwardResolutionRequest,
)
from qagent.backtesting.ranking_v3_protocol import build_ranking_v3_protocol
from qagent.backtesting.execution import HistoricalExecutionRule
from qagent.db import create_db_engine
from qagent.historical_evidence.models import HistoricalFeeRule
from qagent.jobs.ranking_v3_forward import (
    QagentRankingV3CandidateLoader,
    QagentRankingV3MarketResolver,
    run_ranking_v3_forward_day,
)
from qagent.market.calendars import trading_day_offset, trading_sessions_in_range
from qagent.storage.ranking_v3_forward import RankingV3ForwardRepository
from qagent.storage.repository import OpportunitySnapshotRecord
from qagent.storage.tables import Base


RUN_ID = "ranking-v3-forward-job"
BENCHMARK_ID = "CN:000300.IDX"
START = date(2026, 7, 27)
NOW = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)


class _Repo:
    def __init__(self, session_factory, run, snapshots=(), snapshot_providers=None):
        self.session_factory = session_factory
        self.run = run
        self.snapshots = {item.snapshot_id: item for item in snapshots}
        self.snapshot_providers = {item.snapshot_id: "free" for item in snapshots} | dict(
            snapshot_providers or {}
        )
        self.list_calls = []

    def get_walk_forward_run(self, run_id):
        return self.run if run_id == self.run.run_id else None

    def get_opportunity_snapshot(self, snapshot_id):
        return self.snapshots.get(snapshot_id)

    def opportunity_snapshots_belong_to_provider(self, snapshot_ids, *, provider):
        return all(
            self.snapshot_providers.get(snapshot_id) == provider for snapshot_id in snapshot_ids
        )

    def list_top_daily_opportunity_snapshots(self, *, start, end, top_n=5, provider=None):
        self.list_calls.append((start, end, top_n, provider))
        return sorted(
            [
                item
                for item in self.snapshots.values()
                if item.signal_date is not None
                and start <= item.signal_date <= end
                and self.snapshot_providers.get(item.snapshot_id) == provider
            ],
            key=lambda item: (-item.rank_score, item.instrument_id),
        )[:top_n]


class _Provider:
    name = "test"

    def __init__(self, frames=None):
        self.frames = dict(frames or {})

    def get_daily_bars(self, instrument_ids, start, end):
        rows = []
        for instrument_id in instrument_ids:
            frame = self.frames.get(instrument_id, pd.DataFrame())
            if frame.empty:
                continue
            rows.append(frame.loc[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)])
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()

    def get_snapshot(self, instrument_ids):
        return pd.DataFrame()

    def get_minute_bars(self, instrument_ids, start, end):
        return pd.DataFrame()


class _StaticExecutionResolver:
    def __init__(self, rule: HistoricalExecutionRule):
        self.rule = rule

    def resolve(self, instrument_id, trade_date, *, is_st=False):
        return self.rule.model_copy(
            update={"instrument_id": instrument_id, "trade_date": trade_date}
        )


def _factory():
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _features(value=0.75):
    return RankingV3FeatureVector(
        strategy_score=value,
        factor_score=value,
        valuation=value,
        size=value,
        quality=value,
        momentum=value,
        trend_quality=value,
        liquidity=value,
        low_risk=value,
        risk_filter=value,
        reversal=value,
        execution_penalty=0.0,
        data_completeness=1.0,
    )


def _run():
    protocol = build_ranking_v3_protocol()
    experiment_manifest = build_walk_forward_experiment_manifest(
        provider_mode="free",
        dataset_revision=7,
        start_date=date(2025, 1, 2),
        end_date=date(2026, 7, 24),
        rebalance_step_sessions=10,
        lookback_days=365,
    )
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
                net_excess_return_pct=0.8,
                primary_strategy_id=f"s{index % 3}",
                factor_signals=["quality"],
                market_regime="balanced",
                asset_type="stock",
                features=_features(),
            )
        )
    artifact = build_ranking_v3_frozen_scoring_artifact(
        observations,
        cutoff=protocol.prospective_shadow_start,
    )
    return SimpleNamespace(
        run_id=RUN_ID,
        provider="free",
        status="succeeded",
        dataset_revision=7,
        reproducibility_digest="reproducible",
        updated_at=NOW,
        payload={
            "experiment_manifest": experiment_manifest.model_dump(mode="json"),
            "ranking_v3": {
                "status": "forward_validation_pending",
                "model_version": protocol.model_version,
                "protocol": protocol.model_dump(mode="json"),
                "forward_scoring_artifact": artifact.model_dump(mode="json"),
                "forward_scoring_artifact_digest": artifact.stable_digest,
                "criteria": [],
            },
        },
    )


def _card(instrument_id, rank_score, *, strategy="trend", industry="芯片"):
    exposures = [
        {
            "factor_id": factor_id,
            "label": factor_id,
            "score": rank_score,
            "weight": 0.1,
            "explanation": factor_id,
        }
        for factor_id in (
            "valuation",
            "size",
            "quality",
            "momentum",
            "trend_quality",
            "liquidity",
            "low_risk",
            "risk_filter",
            "reversal",
        )
    ]
    return {
        "card_id": f"card-{instrument_id}",
        "instrument_id": instrument_id,
        "market": "CN",
        "asset_type": "stock",
        "status": "watch",
        "thesis": "test",
        "score": rank_score,
        "entry_plan": {
            "entry_type": "breakout",
            "confirmation": "close",
            "trigger_price": "10",
            "no_chase_above": "11",
        },
        "exit_plan": {
            "invalidation": "stop",
            "trailing_rule": "none",
            "time_stop": "20",
            "initial_stop": "9",
            "target_1": "12",
        },
        "scenario": {
            "downside_pct": -10,
            "target_1_pct": 20,
            "no_chase_pct": 10,
            "summary": "test",
        },
        "primary_strategy_id": strategy,
        "strategy_score": rank_score,
        "rank_score": rank_score,
        "factor_score": rank_score,
        "factor_flags": ["quality"],
        "factor_exposures": exposures,
        "data_quality_audit": {
            "status": "ready",
            "score": 1.0,
            "can_recommend": True,
            "issues": [],
            "summary": "complete test evidence",
        },
        "market_context": {
            "board": "主板",
            "industry": industry,
            "themes": ["AI"],
            "index_memberships": ["沪深300"],
            "summary": "balanced",
        },
    }


def _snapshot(instrument_id, rank_score, *, signal_date=START):
    card = _card(instrument_id, rank_score)
    return OpportunitySnapshotRecord(
        snapshot_id=f"snapshot-{signal_date}-{instrument_id}",
        run_id="scan",
        card_id=card["card_id"],
        instrument_id=instrument_id,
        market="CN",
        status="watch",
        signal_date=signal_date,
        latest_close=Decimal("10"),
        primary_strategy_id=card["primary_strategy_id"],
        score=Decimal(str(rank_score)),
        strategy_score=Decimal(str(rank_score)),
        rank_score=Decimal(str(rank_score)),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("12"),
        card=card,
    )


def _bars(instrument_id, start, end, *, benchmark=False):
    sessions = trading_sessions_in_range(start, end)
    rows = []
    for index, session in enumerate(sessions):
        close = Decimal("100") + Decimal(index) if benchmark else Decimal("10.2")
        high = close + (Decimal("0.1") if benchmark else Decimal("2.2"))
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": session,
                "open": close,
                "high": high,
                "low": close - Decimal("0.2"),
                "close": close,
                "volume": 10_000_000,
                "provider": "test",
            }
        )
    return pd.DataFrame(rows)


def _fee(side: str) -> HistoricalFeeRule:
    return HistoricalFeeRule(
        fee_schedule_version="a-share-fees-v1",
        fee_rule_key="cn-stock",
        effective_from=date(2023, 8, 28),
        effective_to=date(2027, 12, 31),
        side=side,
        security_type="stock",
        exchange="ALL",
        commission_bps="3",
        minimum_commission="5",
        stamp_duty_bps="5" if side == "sell" else "0",
        transfer_fee_bps="0.1",
    )


def _execution_rule() -> HistoricalExecutionRule:
    return HistoricalExecutionRule(
        instrument_id="CN:600001",
        trade_date=START,
        limit_pct="10",
        minimum_order_quantity=100,
        quantity_step=100,
        settlement_days=1,
        ipo_no_limit_sessions=0,
        buy_fee=_fee("buy"),
        sell_fee=_fee("sell"),
        rule_set_version="a-share-rules-v1",
        fee_schedule_version="a-share-fees-v1",
    )


def _request(run, session_date):
    ranking = run.payload["ranking_v3"]
    protocol = build_ranking_v3_protocol()
    artifact = ranking["forward_scoring_artifact"]
    return RankingV3CandidateSnapshotRequest(
        validation_run_id=RUN_ID,
        data_revision="walk-forward-v3:free:7:reproducible",
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.protocol_digest,
        model_version=protocol.model_version,
        artifact_digest=artifact["stable_digest"],
        session_date=session_date,
    )


def _run_after_start(repo, provider, end):
    result = None
    for session_date in trading_sessions_in_range(trading_day_offset(START, 1), end):
        result = run_ranking_v3_forward_day(repo, provider, RUN_ID, session_date)
    return result


def _seed_forward_sessions(store, protocol, count):
    identity = RankingV3ForwardIdentity.from_protocol(protocol)
    revision = "walk-forward-v3:free:7:reproducible"
    store.ensure_ledger(identity, revision)
    sessions = []
    for offset in range(count):
        session_date = trading_day_offset(START, offset)
        item = RankingV3ForwardSessionInput(
            session_date=session_date,
            benchmark_id=BENCHMARK_ID,
            benchmark_return_pct=Decimal("0"),
            portfolio_equity=Decimal("100000"),
            stress_portfolio_equity=Decimal("100000"),
            benchmark_equity=Decimal("100000"),
            data_revision=revision,
        )
        store.record_session(
            identity,
            item,
            idempotency_key=f"seed-session-{session_date}",
            fact_digest=stable_digest(item),
        )
        sessions.append(session_date)
    return sessions


def test_first_empty_ledger_starts_at_100000():
    factory = _factory()
    repo = _Repo(factory, _run())

    result = run_ranking_v3_forward_day(repo, _Provider(), RUN_ID, START)

    assert result.evaluation.metrics.session_count == 1
    store = RankingV3ForwardRepository(factory)
    snapshot = store.load_snapshot(result.evaluation.identity)
    assert snapshot.sessions[0].portfolio_equity == Decimal("100000")
    assert snapshot.sessions[0].benchmark_equity == Decimal("100000")


def test_loader_preserves_source_ids_and_runtime_sorts_candidates():
    factory = _factory()
    run = _run()
    low = _snapshot("CN:600001", 0.60)
    high = _snapshot("CN:600002", 0.90)
    repo = _Repo(factory, run, (low, high))

    result = run_ranking_v3_forward_day(repo, _Provider(), RUN_ID, START)

    store = RankingV3ForwardRepository(factory)
    snapshot = store.load_snapshot(result.evaluation.identity)
    assert [item.instrument_id for item in snapshot.candidates] == [
        "CN:600002",
        "CN:600001",
    ]
    assert {item.source_snapshot_id for item in snapshot.candidates} == {
        high.snapshot_id,
        low.snapshot_id,
    }
    assert repo.list_calls == [(START, START, 50, "free")]


def test_candidate_collection_stops_after_frozen_collection_window():
    factory = _factory()
    run = _run()
    protocol = build_ranking_v3_protocol()
    store = RankingV3ForwardRepository(factory)
    sessions = _seed_forward_sessions(
        store,
        protocol,
        protocol.thresholds.minimum_forward_shadow_sessions,
    )
    collection_end = sessions[-1]
    liquidation_date = trading_day_offset(collection_end, 1)
    replay_source = _snapshot(
        "CN:600001",
        0.9,
        signal_date=collection_end,
    )
    late_source = _snapshot(
        "CN:600002",
        0.9,
        signal_date=liquidation_date,
    )
    repo = _Repo(factory, run, (replay_source, late_source))
    loader = QagentRankingV3CandidateLoader(repo, store)

    replay = loader.load_candidate_snapshot(_request(run, collection_end))
    liquidation = loader.load_candidate_snapshot(_request(run, liquidation_date))

    assert [item.source_snapshot_id for item in replay.candidates] == [replay_source.snapshot_id]
    assert liquidation.candidates == ()


def test_portfolio_evidence_waits_for_full_collection_and_liquidation_window():
    protocol = build_ranking_v3_protocol()
    run = _run()
    required = (
        protocol.thresholds.minimum_forward_shadow_sessions
        + protocol.statistics_definition.entry_wait_sessions
        + protocol.statistics_definition.holding_sessions
    )
    candidate = SimpleNamespace(
        candidate_id="candidate-1",
        outcome_status="completed",
    )

    def ready_with(prior_session_count):
        sessions = [
            SimpleNamespace(session_date=trading_day_offset(START, offset))
            for offset in range(prior_session_count)
        ]
        session_date = trading_day_offset(START, prior_session_count)
        candidate_request = _request(run, session_date)
        resolution_request = RankingV3ForwardResolutionRequest(
            **candidate_request.model_dump(mode="python"),
            candidate_snapshot_digest="1" * 64,
            selection_batch_digest="2" * 64,
            benchmark_id=BENCHMARK_ID,
        )
        ledger = SimpleNamespace(sessions=sessions, candidates=[candidate])
        return QagentRankingV3MarketResolver._portfolio_is_ready(
            ledger,
            resolution_request,
            protocol,
            (),
        )

    assert ready_with(required - 2) is False
    assert ready_with(required - 1) is True


def test_full_forward_window_builds_capital_constrained_portfolio_evidence(
    monkeypatch,
):
    factory = _factory()
    run = _run()
    protocol = build_ranking_v3_protocol()
    source = _snapshot("CN:600001", 0.90)
    required = (
        protocol.thresholds.minimum_forward_shadow_sessions
        + protocol.statistics_definition.entry_wait_sessions
        + protocol.statistics_definition.holding_sessions
    )
    end = trading_day_offset(START, required - 1)
    provider = _Provider(
        {
            source.instrument_id: _bars(source.instrument_id, START, end),
            BENCHMARK_ID: _bars(BENCHMARK_ID, START, end, benchmark=True),
        }
    )
    repo = _Repo(factory, run, (source,))
    resolver = _StaticExecutionResolver(_execution_rule().model_copy(update={"limit_pct": None}))
    candidate_execution_resolvers = set()
    portfolio_execution_resolvers = set()
    actual_candidate_resolver = ranking_v3_forward_job.resolve_candidate_outcome_ledger
    actual_portfolio_resolver = ranking_v3_forward_job.run_signal_portfolio_backtest

    def capture_candidate_resolver(**kwargs):
        candidate_execution_resolvers.add(id(kwargs["execution_rule_resolver"]))
        return actual_candidate_resolver(**kwargs)

    def capture_portfolio_resolver(**kwargs):
        portfolio_execution_resolvers.add(id(kwargs["execution_rule_resolver"]))
        return actual_portfolio_resolver(**kwargs)

    monkeypatch.setattr(
        "qagent.jobs.ranking_v3_forward.VersionedAshareExecutionResolver",
        lambda *args, **kwargs: resolver,
    )
    monkeypatch.setattr(
        "qagent.jobs.ranking_v3_forward.resolve_candidate_outcome_ledger",
        capture_candidate_resolver,
    )
    monkeypatch.setattr(
        "qagent.jobs.ranking_v3_forward.run_signal_portfolio_backtest",
        capture_portfolio_resolver,
    )

    result = None
    for session_date in trading_sessions_in_range(START, end):
        result = run_ranking_v3_forward_day(
            repo,
            provider,
            RUN_ID,
            session_date,
        )

    assert result is not None
    metrics = result.evaluation.metrics
    assert metrics.session_count == required
    assert metrics.pending_candidate_count == 0
    assert metrics.portfolio_completed_trade_count == 1
    assert metrics.portfolio_net_return_pct is not None
    assert metrics.portfolio_stress_net_return_pct is not None
    assert metrics.portfolio_benchmark_return_pct is not None
    assert metrics.portfolio_benchmark_excess_pct is not None
    assert metrics.maximum_drawdown_pct is not None

    store = RankingV3ForwardRepository(factory)
    ledger = store.load_snapshot(result.evaluation.identity)
    portfolio_evidence = [
        item
        for item in ledger.evidence
        if item.evidence_kind == "portfolio"
        and item.payload.get("schema_version")
        == "ranking-v3-forward-portfolio-verification-v1"
    ]
    assert len(portfolio_evidence) == 1
    assert portfolio_evidence[0].payload["evidence"]["completed_trade_count"] == 1
    assert portfolio_evidence[0].payload["evidence"]["source_candidate_digest"]
    assert candidate_execution_resolvers == {id(resolver)}
    assert portfolio_execution_resolvers == {id(resolver)}


def test_loader_uses_frozen_protocol_pool_and_can_load_more_than_twenty():
    run = _run()
    protocol = build_ranking_v3_protocol()
    snapshots = tuple(
        _snapshot(f"CN:{600000 + index:06d}", 1 - index / 1000)
        for index in range(protocol.candidate_pool_limit + 5)
    )
    repo = _Repo(_factory(), run, snapshots)

    loaded = QagentRankingV3CandidateLoader(repo).load_candidate_snapshot(_request(run, START))

    assert len(loaded.candidates) == protocol.candidate_pool_limit
    assert len(loaded.candidates) > 20
    assert repo.list_calls == [(START, START, protocol.candidate_pool_limit, "free")]
    assert {item.source_snapshot_id for item in loaded.candidates} == {
        item.snapshot_id for item in snapshots[: protocol.candidate_pool_limit]
    }


def test_loader_fails_closed_if_non_free_snapshot_bypasses_query_filter():
    run = _run()
    fixture = _snapshot("CN:600999", 0.99)

    class _LeakyRepo(_Repo):
        def list_top_daily_opportunity_snapshots(self, **kwargs):
            self.list_calls.append(
                (
                    kwargs["start"],
                    kwargs["end"],
                    kwargs["top_n"],
                    kwargs["provider"],
                )
            )
            return [fixture]

    repo = _LeakyRepo(
        _factory(),
        run,
        (fixture,),
        snapshot_providers={fixture.snapshot_id: "fixture"},
    )

    with pytest.raises(ValueError, match="outside free scan runs"):
        QagentRankingV3CandidateLoader(repo).load_candidate_snapshot(_request(run, START))


def test_resolver_delegates_entry_wait_to_shared_historical_default(monkeypatch):
    captured = {}
    sentinel = object()

    def _fake_resolver(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(outcomes=[sentinel])

    monkeypatch.setattr(
        "qagent.jobs.ranking_v3_forward.resolve_candidate_outcome_ledger",
        _fake_resolver,
    )
    resolver = QagentRankingV3MarketResolver(
        _Repo(_factory(), _run()),
        RankingV3ForwardRepository(_factory()),
        _Provider(),
    )

    outcome = resolver._resolve_signal(
        SimpleNamespace(signal_date=START),
        session_date=START,
        entry_wait_sessions=5,
        holding_sessions=20,
        slippage_bps=Decimal("0"),
        fee_multiplier=Decimal("1"),
        transaction_cost_bps=Decimal("0"),
        execution_resolver=sentinel,
    )

    assert outcome is sentinel
    assert captured["max_entry_wait_days"] == 5
    assert captured["execution_rule_resolver"] is sentinel


def test_loader_fails_closed_on_non_session_snapshot():
    run = _run()
    wrong = _snapshot("CN:600001", 0.8, signal_date=START - timedelta(days=1))

    class _BadRepo(_Repo):
        def list_top_daily_opportunity_snapshots(self, **kwargs):
            return [wrong]

    with pytest.raises(ValueError, match="non-requested session"):
        QagentRankingV3CandidateLoader(_BadRepo(_factory(), run, (wrong,))).load_candidate_snapshot(
            _request(run, START)
        )


def test_mature_candidate_resolves_and_updates_equity(monkeypatch):
    factory = _factory()
    run = _run()
    source = _snapshot("CN:600001", 0.90)
    maturity = trading_day_offset(START, 25)
    provider = _Provider(
        {
            source.instrument_id: _bars(source.instrument_id, START, maturity),
            BENCHMARK_ID: _bars(BENCHMARK_ID, START, maturity, benchmark=True),
        }
    )
    repo = _Repo(factory, run, (source,))
    execution_resolver = _StaticExecutionResolver(
        _execution_rule().model_copy(update={"limit_pct": None})
    )
    monkeypatch.setattr(
        "qagent.jobs.ranking_v3_forward.VersionedAshareExecutionResolver",
        lambda *args, **kwargs: execution_resolver,
    )
    first = run_ranking_v3_forward_day(repo, provider, RUN_ID, START)
    repo.snapshots = {source.snapshot_id: source}

    resolved = _run_after_start(repo, provider, maturity)

    store = RankingV3ForwardRepository(factory)
    ledger = store.load_snapshot(first.evaluation.identity)
    candidate = ledger.candidates[0]
    assert candidate.outcome_status == "completed"
    assert candidate.resolved_on == maturity
    assert resolved.finalized_candidate_ids == (candidate.candidate_id,)
    assert ledger.sessions[-1].portfolio_equity == Decimal("100000")


def test_restart_replays_same_day_idempotently():
    factory = _factory()
    run = _run()
    repo = _Repo(factory, run)
    first = run_ranking_v3_forward_day(repo, _Provider(), RUN_ID, START)

    restarted_repo = _Repo(factory, run)
    repeated = run_ranking_v3_forward_day(restarted_repo, _Provider(), RUN_ID, START)

    assert repeated == first
    store = RankingV3ForwardRepository(factory)
    assert len(store.load_snapshot(first.evaluation.identity).sessions) == 1


def test_missing_source_snapshot_fails_closed_at_maturity():
    factory = _factory()
    run = _run()
    source = _snapshot("CN:600001", 0.90)
    repo = _Repo(factory, run, (source,))
    first = run_ranking_v3_forward_day(repo, _Provider(), RUN_ID, START)
    repo.snapshots.clear()
    maturity = trading_day_offset(START, 25)

    provider = _Provider({BENCHMARK_ID: _bars(BENCHMARK_ID, START, maturity, benchmark=True)})
    for session_date in trading_sessions_in_range(
        trading_day_offset(START, 1),
        trading_day_offset(maturity, -1),
    ):
        run_ranking_v3_forward_day(repo, provider, RUN_ID, session_date)
    with pytest.raises(LookupError, match="source opportunity snapshot"):
        run_ranking_v3_forward_day(repo, provider, RUN_ID, maturity)

    store = RankingV3ForwardRepository(factory)
    ledger = store.load_snapshot(first.evaluation.identity)
    assert ledger.sessions[-1].session_date == trading_day_offset(maturity, -1)
    assert ledger.candidates[0].outcome_status == "pending"


def test_missing_candidate_market_data_is_censored():
    factory = _factory()
    run = _run()
    source = _snapshot("CN:600001", 0.90)
    maturity = trading_day_offset(START, 25)
    provider = _Provider({BENCHMARK_ID: _bars(BENCHMARK_ID, START, maturity, benchmark=True)})
    repo = _Repo(factory, run, (source,))
    first = run_ranking_v3_forward_day(repo, provider, RUN_ID, START)

    _run_after_start(repo, provider, maturity)

    store = RankingV3ForwardRepository(factory)
    candidate = store.load_snapshot(first.evaluation.identity).candidates[0]
    assert candidate.outcome_status == "censored"
    assert candidate.outcome_reason == "authoritative_trading_calendar_rows_missing"


def test_missing_suspension_session_row_is_censored_at_frozen_maturity():
    factory = _factory()
    run = _run()
    source = _snapshot("CN:600001", 0.90)
    maturity = trading_day_offset(START, 25)
    complete = _bars(source.instrument_id, START, maturity)
    missing_session = trading_day_offset(START, 8)
    incomplete = complete.loc[complete["trade_date"] != missing_session].reset_index(drop=True)
    provider = _Provider(
        {
            source.instrument_id: incomplete,
            BENCHMARK_ID: _bars(BENCHMARK_ID, START, maturity, benchmark=True),
        }
    )
    repo = _Repo(factory, run, (source,))
    first = run_ranking_v3_forward_day(repo, provider, RUN_ID, START)

    _run_after_start(repo, provider, maturity)

    store = RankingV3ForwardRepository(factory)
    candidate = store.load_snapshot(first.evaluation.identity).candidates[0]
    assert candidate.outcome_status == "censored"
    assert candidate.resolved_on == maturity
    assert candidate.outcome_reason == "authoritative_trading_calendar_rows_missing"


def test_unavailable_authoritative_calendar_is_censored_not_extended(monkeypatch):
    factory = _factory()
    run = _run()
    source = _snapshot("CN:600001", 0.90)
    maturity = trading_day_offset(START, 25)
    provider = _Provider(
        {
            source.instrument_id: _bars(source.instrument_id, START, maturity),
            BENCHMARK_ID: _bars(BENCHMARK_ID, START, maturity, benchmark=True),
        }
    )
    repo = _Repo(factory, run, (source,))
    first = run_ranking_v3_forward_day(repo, provider, RUN_ID, START)

    def unavailable_calendar(*args, **kwargs):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(
        "qagent.jobs.ranking_v3_forward.trading_sessions_in_range",
        unavailable_calendar,
    )
    _run_after_start(repo, provider, maturity)

    candidate = (
        RankingV3ForwardRepository(factory).load_snapshot(first.evaluation.identity).candidates[0]
    )
    assert candidate.outcome_status == "censored"
    assert candidate.resolved_on == maturity
    assert candidate.outcome_reason == "authoritative_trading_calendar_unavailable"


def test_resolver_rejects_missing_benchmark_session_bars():
    factory = _factory()
    run = _run()
    protocol = build_ranking_v3_protocol()
    store = RankingV3ForwardRepository(factory)
    identity = RankingV3ForwardIdentity.from_protocol(protocol)
    revision = "walk-forward-v3:free:7:reproducible"
    store.ensure_ledger(identity, revision)
    store.record_session(
        identity,
        RankingV3ForwardSessionInput(
            session_date=START,
            benchmark_id=BENCHMARK_ID,
            benchmark_return_pct=Decimal("0"),
            portfolio_equity=Decimal("100000"),
            stress_portfolio_equity=Decimal("100000"),
            benchmark_equity=Decimal("100000"),
            data_revision=revision,
        ),
        idempotency_key="session-start",
        fact_digest=stable_digest(
            RankingV3ForwardSessionInput(
                session_date=START,
                benchmark_id=BENCHMARK_ID,
                benchmark_return_pct=Decimal("0"),
                portfolio_equity=Decimal("100000"),
                stress_portfolio_equity=Decimal("100000"),
                benchmark_equity=Decimal("100000"),
                data_revision=revision,
            )
        ),
    )
    session_date = trading_day_offset(START, 1)
    request = _request(run, session_date)
    resolution_request = RankingV3ForwardResolutionRequest(
        **request.model_dump(mode="python"),
        candidate_snapshot_digest="1" * 64,
        selection_batch_digest="2" * 64,
        benchmark_id=BENCHMARK_ID,
    )
    resolver = QagentRankingV3MarketResolver(
        _Repo(factory, run),
        store,
        _Provider(),
    )

    with pytest.raises(ValueError, match="CSI 300"):
        resolver.resolve_forward_day(resolution_request)
