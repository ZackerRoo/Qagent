from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from multiprocessing import get_context
from threading import Event
from types import SimpleNamespace

import pandas as pd
from sqlalchemy.orm import sessionmaker

from qagent.backtesting import walk_forward
from qagent.backtesting.a_share_rules import (
    BrokerFeeRequest,
    build_instrument_rule_metadata,
    load_a_share_rule_schedule,
)
from qagent.backtesting.replay_provider import (
    ReplayMarketDataProvider,
    ReplayStrategyDataProvider,
)
from qagent.backtesting.walk_forward import (
    ELIGIBLE_UNIVERSE_BENCHMARK_ID,
    WalkForwardEvidenceMetric,
    WalkForwardGateCriterion,
    WalkForwardSnapshot,
    _DatasetLeaseHeartbeat,
    _combined_validation_gate,
    _cross_section_coverage,
    _equal_weight_eligible_return,
    _equal_weight_eligible_return_from_stream,
    _enforce_release_gate_on_positive_evidence,
    _execution_challenger_gate_outcome,
    _adjusted_prefilter_bars,
    _paper_eligible_card_ids,
    _trade_temporal_validation,
    _walk_forward_candidates,
    run_full_market_walk_forward_selection,
)
from qagent.db import Base, create_db_engine
from qagent.historical_evidence.models import (
    HistoricalEvidenceBundle,
    HistoricalInstrumentProfile,
    HistoricalLifecycleManifest,
    HistoricalReplayBar,
    HistoricalTradabilityPoint,
)
from qagent.historical_evidence.providers import REQUIRED_BENCHMARK_IDS
from qagent.market.calendars import trading_sessions_in_range
from qagent.storage import tables as _tables  # noqa: F401
from qagent.storage.replay_evidence import ReplayEvidenceRepository
from qagent.storage.repository import QagentRepository
from qagent.strategy_data.models import FundamentalSnapshot


def test_walk_forward_execution_admission_uses_final_policy_audit():
    audits = [
        SimpleNamespace(
            card_id="eligible",
            gate_decision=SimpleNamespace(paper_candidate_eligible=True),
        ),
        SimpleNamespace(
            card_id="blocked",
            gate_decision=SimpleNamespace(paper_candidate_eligible=False),
        ),
    ]

    assert _paper_eligible_card_ids(audits) == {"eligible"}
    assert _paper_eligible_card_ids([]) is None


def test_execution_challenger_gate_rejects_known_failure_before_forward_shadow():
    status, headline = _execution_challenger_gate_outcome(
        [
            WalkForwardGateCriterion(
                key="out_of_sample_return",
                label="样本外净收益",
                status="pass",
                value="+1.20%",
                requirement="> 0% 且优于原执行",
            ),
            WalkForwardGateCriterion(
                key="cost_stress",
                label="压力成本后收益",
                status="fail",
                value="-0.30%",
                requirement="> 0%",
            ),
        ]
    )

    assert status == "rejected"
    assert "保持影子实验" in headline


def test_execution_challenger_gate_accepts_only_when_every_criterion_passes():
    status, headline = _execution_challenger_gate_outcome(
        [
            WalkForwardGateCriterion(
                key=key,
                label=key,
                status="pass",
                value="ready",
                requirement="pass",
            )
            for key in (
                "market_coverage",
                "fundamental_coverage",
                "sample_count",
                "out_of_sample_return",
                "full_period_return",
                "benchmark_excess",
                "cost_stress",
                "stop_rate",
                "payoff_quality",
                "max_drawdown",
            )
        ]
    )

    assert status == "accepted"
    assert "20 个交易日" in headline


def test_snapshot_computation_defers_cyclic_gc_until_checkpoint_end(monkeypatch):
    observed_gc_states = []
    collected = []
    sentinel = object()

    def compute_without_gc(*_args, **_kwargs):
        observed_gc_states.append(walk_forward.gc.isenabled())
        return sentinel

    monkeypatch.setattr(
        walk_forward,
        "_compute_walk_forward_snapshot_without_gc",
        compute_without_gc,
    )
    monkeypatch.setattr(walk_forward.gc, "collect", lambda: collected.append(True))

    assert walk_forward.gc.isenabled()
    result = walk_forward._compute_walk_forward_snapshot(
        object(),
        lookback_days=400,
        repository=object(),
        market_provider=object(),
        strategy_provider=object(),
    )

    assert result is sentinel
    assert observed_gc_states == [False]
    assert collected == [True]
    assert walk_forward.gc.isenabled()


def test_dataset_lease_heartbeat_renews_during_long_snapshot():
    renewed = Event()
    heartbeat_at = datetime.now(timezone.utc)

    class FakeRepository:
        provider_mode = "free"

        def maintain_dataset_lease(self, *, expected_revision):
            assert expected_revision == 7
            renewed.set()
            return SimpleNamespace(
                action="renewed",
                lease=SimpleNamespace(heartbeat_at=heartbeat_at),
            )

    heartbeat = _DatasetLeaseHeartbeat(
        FakeRepository(),
        expected_revision=7,
        interval_seconds=0.01,
    )
    heartbeat.start()
    try:
        assert renewed.wait(timeout=0.5)
        heartbeat.raise_if_failed()
    finally:
        heartbeat.stop()

    assert heartbeat.maintenance_count >= 1
    assert heartbeat.recovery_count == 0
    assert heartbeat.last_heartbeat_at == heartbeat_at


def test_dataset_lease_heartbeat_records_atomic_expiry_recovery():
    heartbeat_at = datetime.now(timezone.utc)
    callback_records = []

    class FakeRepository:
        provider_mode = "free"

        def maintain_dataset_lease(self, *, expected_revision):
            assert expected_revision == 11
            return SimpleNamespace(
                action="expired_reacquired",
                lease=SimpleNamespace(heartbeat_at=heartbeat_at),
            )

    heartbeat = _DatasetLeaseHeartbeat(
        FakeRepository(),
        expected_revision=11,
        maintenance_callback=lambda *values: callback_records.append(values),
    )

    heartbeat.maintain_now()

    assert heartbeat.maintenance_count == 1
    assert heartbeat.recovery_count == 1
    assert heartbeat.last_heartbeat_at == heartbeat_at
    assert callback_records == [(1, 1, heartbeat_at)]


def test_dataset_lease_heartbeat_ignores_telemetry_callback_failure():
    heartbeat_at = datetime.now(timezone.utc)

    class FakeRepository:
        provider_mode = "free"

        def maintain_dataset_lease(self, *, expected_revision):
            return SimpleNamespace(
                action="renewed",
                lease=SimpleNamespace(heartbeat_at=heartbeat_at),
            )

    heartbeat = _DatasetLeaseHeartbeat(
        FakeRepository(),
        expected_revision=17,
        maintenance_callback=lambda *_: (_ for _ in ()).throw(
            RuntimeError("telemetry store unavailable")
        ),
    )

    heartbeat.maintain_now()
    heartbeat.raise_if_failed()

    assert heartbeat.maintenance_count == 1
    assert heartbeat.last_heartbeat_at == heartbeat_at


def test_dataset_lease_heartbeat_continues_persisted_counters_after_restart():
    previous_heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    heartbeat_at = datetime.now(timezone.utc)

    class FakeRepository:
        provider_mode = "free"

        def maintain_dataset_lease(self, *, expected_revision):
            return SimpleNamespace(
                action="expired_reacquired",
                lease=SimpleNamespace(heartbeat_at=heartbeat_at),
            )

    heartbeat = _DatasetLeaseHeartbeat(
        FakeRepository(),
        expected_revision=19,
        initial_maintenance_count=8,
        initial_recovery_count=2,
        initial_heartbeat_at=previous_heartbeat_at,
    )

    heartbeat.maintain_now()

    assert heartbeat.maintenance_count == 9
    assert heartbeat.recovery_count == 3
    assert heartbeat.last_heartbeat_at == heartbeat_at


def test_dataset_lease_heartbeat_retries_transient_failure():
    heartbeat_at = datetime.now(timezone.utc)
    attempts = 0

    class FakeRepository:
        provider_mode = "free"

        def maintain_dataset_lease(self, *, expected_revision):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary database lock")
            return SimpleNamespace(
                action="renewed",
                lease=SimpleNamespace(heartbeat_at=heartbeat_at),
            )

    heartbeat = _DatasetLeaseHeartbeat(
        FakeRepository(),
        expected_revision=13,
        retry_interval_seconds=0.01,
        max_attempts=2,
    )

    heartbeat.maintain_now()

    assert attempts == 2
    assert heartbeat.maintenance_count == 1
    assert heartbeat.recovery_count == 0


def _replay_repository(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'walk-forward.db'}")
    Base.metadata.create_all(engine)
    repository = ReplayEvidenceRepository(sessionmaker(bind=engine), "free")
    decision_date = date(2025, 1, 10)
    fetched_at = datetime(2025, 1, 11, tzinfo=timezone.utc)
    profile = HistoricalInstrumentProfile(
        instrument_id="CN:000001",
        name="平安银行",
        snapshot_date=decision_date,
        listing_date=date(1991, 4, 3),
        security_type="stock",
        listing_status="active",
        provider="fixture_inventory",
    )
    repository.upsert_lifecycle_inventory(
        [profile],
        HistoricalLifecycleManifest(
            provider_mode="free",
            source_revision=1,
            status="ready",
            expected_count=1,
            stored_count=0,
            effective_through=date(2025, 1, 13),
            fetched_at=fetched_at,
        ),
    )
    sessions = trading_sessions_in_range(date(2024, 1, 2), date(2025, 1, 13))
    repository.upsert_replay_bars(
        [
            HistoricalReplayBar(
                provider_mode="free",
                instrument_id=instrument_id,
                trade_date=trade_date,
                raw_open=Decimal("10") + Decimal(index) / Decimal("100"),
                raw_high=Decimal("10.2") + Decimal(index) / Decimal("100"),
                raw_low=Decimal("9.8") + Decimal(index) / Decimal("100"),
                raw_close=Decimal("10.1") + Decimal(index) / Decimal("100"),
                adjusted_open=Decimal("10") + Decimal(index) / Decimal("100"),
                adjusted_high=Decimal("10.2") + Decimal(index) / Decimal("100"),
                adjusted_low=Decimal("9.8") + Decimal(index) / Decimal("100"),
                adjusted_close=Decimal("10.1") + Decimal(index) / Decimal("100"),
                volume=Decimal("1000000"),
                turnover=Decimal("10000000"),
                adjustment_factor=Decimal("1"),
                adjustment_mode=("qfq" if instrument_id == "CN:000001" else "none"),
                source_provider="fixture_paired",
                dataset_revision=2,
                fetched_at=fetched_at,
            )
            for instrument_id in ["CN:000001", *REQUIRED_BENCHMARK_IDS]
            for index, trade_date in enumerate(sessions)
        ],
        revision=2,
    )
    repository.upsert_fundamentals(
        [
            FundamentalSnapshot(
                instrument_id="CN:000001",
                as_of_date=date(2024, 12, 31),
                market_cap=Decimal("10000000000"),
                pe_ratio=Decimal("10"),
                provider="fixture_fundamental",
            ),
            FundamentalSnapshot(
                instrument_id="CN:000001",
                as_of_date=date(2025, 1, 11),
                market_cap=Decimal("20000000000"),
                pe_ratio=Decimal("20"),
                provider="fixture_fundamental",
            ),
        ],
        revision=3,
    )
    repository.upsert_point_in_time_evidence(
        HistoricalEvidenceBundle(
            tradability=[
                HistoricalTradabilityPoint(
                    instrument_id="CN:000001",
                    trade_date=trade_date,
                    trading_status="trading",
                    is_st=False,
                    provider="fixture_tradability",
                )
                for trade_date in [decision_date, date(2025, 1, 13)]
            ]
        ),
        revision=4,
    )
    schedule = load_a_share_rule_schedule()
    repository.upsert_trading_rules(schedule.trading_rules)
    repository.upsert_fee_rules(
        schedule.fee_rules(BrokerFeeRequest(commission_bps="3", minimum_commission="5"))
    )
    repository.upsert_instrument_rule_metadata(
        [
            build_instrument_rule_metadata(
                profile,
                effective_from=date(2023, 4, 10),
                schedule=schedule,
            )
        ]
    )
    return repository, decision_date


def _run_nested_parallel_walk_forward(database_url: str) -> dict[str, str]:
    repository = ReplayEvidenceRepository(
        sessionmaker(bind=create_db_engine(database_url)),
        "free",
    )
    result = run_full_market_walk_forward_selection(
        repository,
        owner_run_id="walk-forward-nested-process",
        start=date(2025, 1, 10),
        end=date(2025, 1, 13),
        rebalance_step_sessions=1,
        snapshot_workers=2,
    )
    return {
        "workers": result.data_health["walk_forward_snapshot_workers"],
        "snapshots": result.data_health["walk_forward_snapshots"],
    }


def test_replay_adapters_enforce_date_cutoffs(tmp_path):
    repository, decision_date = _replay_repository(tmp_path)
    revision = repository.current_revision()
    market = ReplayMarketDataProvider(repository, revision)
    strategy = ReplayStrategyDataProvider(repository, revision)

    bars = market.get_daily_bars(["CN:000001"], date(2024, 1, 1), decision_date)
    fundamentals = strategy.get_fundamentals(["CN:000001"], date(2024, 1, 1), decision_date)

    assert max(bars["trade_date"]) == decision_date
    assert bars.iloc[-1]["adjusted_close"] is not None
    assert len(fundamentals) == 1
    assert fundamentals[0].as_of_date == date(2024, 12, 31)
    assert fundamentals[0].pe_ratio == Decimal("10")


def test_batch_tradability_matches_single_date_queries(tmp_path):
    repository, decision_date = _replay_repository(tmp_path)
    revision = repository.current_revision()
    dates = [decision_date, date(2025, 1, 13)]

    batched = repository.tradability_on_dates(
        ["CN:000001"],
        dates,
        revision,
    )

    assert batched == {
        current_date: repository.tradability_on(
            ["CN:000001"],
            current_date,
            revision,
        )
        for current_date in dates
    }


def test_replay_provider_batches_tradability_for_multiple_bar_dates(tmp_path):
    repository, decision_date = _replay_repository(tmp_path)
    provider = ReplayMarketDataProvider(repository, repository.current_revision())

    bars = provider.get_daily_bars(
        ["CN:000001"],
        decision_date,
        date(2025, 1, 13),
    )

    assert len(bars) == 2
    assert provider.tradability_query_count == 1
    assert set(bars["trading_status"]) == {"trading"}


def test_replay_market_provider_reuses_rolling_window(tmp_path, monkeypatch):
    repository, _ = _replay_repository(tmp_path)
    revision = repository.current_revision()
    provider = ReplayMarketDataProvider(repository, revision)
    original = repository.replay_bar_rows
    calls = []

    def tracked_replay_bar_rows(instrument_ids, start, end, dataset_revision):
        calls.append((list(instrument_ids), start, end, dataset_revision))
        return original(instrument_ids, start, end, dataset_revision)

    monkeypatch.setattr(repository, "replay_bar_rows", tracked_replay_bar_rows)

    first = provider.get_daily_bars(
        ["CN:000001"],
        date(2024, 1, 2),
        date(2025, 1, 6),
    )
    second = provider.get_daily_bars(
        ["CN:000001"],
        date(2024, 1, 8),
        date(2025, 1, 10),
    )

    assert len(calls) == 2
    assert calls[0][1:3] == (date(2024, 1, 2), date(2025, 1, 6))
    assert calls[1][1:3] == (date(2025, 1, 7), date(2025, 1, 10))
    assert min(second["trade_date"]) >= date(2024, 1, 8)
    assert max(second["trade_date"]) == date(2025, 1, 10)
    assert len(second) < len(first) + len(calls)
    assert provider.full_window_queries == 1
    assert provider.incremental_queries == 1


def test_factor_prefilter_bars_match_legacy_adjusted_close_volume_semantics(
    tmp_path,
):
    repository, decision_date = _replay_repository(tmp_path)
    revision = repository.current_revision()
    start = date(2024, 1, 2)
    legacy_provider = ReplayMarketDataProvider(repository, revision)
    lightweight_provider = ReplayMarketDataProvider(repository, revision)

    legacy = _adjusted_prefilter_bars(
        legacy_provider.get_daily_bars(
            ["CN:000001"],
            start,
            decision_date,
        )
    )[["instrument_id", "trade_date", "close", "volume"]]
    lightweight = lightweight_provider.get_factor_prefilter_bars(
        ["CN:000001"],
        start,
        decision_date,
    )

    pd.testing.assert_frame_equal(
        legacy.sort_values(["instrument_id", "trade_date"]).reset_index(drop=True),
        lightweight.sort_values(["instrument_id", "trade_date"]).reset_index(drop=True),
    )
    assert lightweight_provider.query_count == 0
    assert lightweight_provider.tradability_query_count == 0
    assert lightweight_provider.factor_prefilter_query_count == 1
    assert lightweight_provider.factor_prefilter_rows_loaded == len(lightweight)


def test_factor_prefilter_matches_mixed_adjustment_semantics(tmp_path):
    repository, decision_date = _replay_repository(tmp_path)
    fetched_at = datetime(2025, 1, 11, tzinfo=timezone.utc)
    revision = repository.current_revision() + 1
    cases = [
        ("CN:100001", "qfq", ("9.8", "10.2", "9.6", "10")),
        ("CN:100002", "none", (None, None, None, None)),
        ("CN:100003", "qfq", (None, None, None, "30")),
        ("CN:100004", "none", (None, None, None, "40")),
    ]
    repository.upsert_replay_bars(
        [
            HistoricalReplayBar(
                provider_mode="free",
                instrument_id=instrument_id,
                trade_date=decision_date,
                raw_open=Decimal("19"),
                raw_high=Decimal("21"),
                raw_low=Decimal("18"),
                raw_close=Decimal("20"),
                adjusted_open=(
                    Decimal(adjusted[0]) if adjusted[0] is not None else None
                ),
                adjusted_high=(
                    Decimal(adjusted[1]) if adjusted[1] is not None else None
                ),
                adjusted_low=(
                    Decimal(adjusted[2]) if adjusted[2] is not None else None
                ),
                adjusted_close=(
                    Decimal(adjusted[3]) if adjusted[3] is not None else None
                ),
                volume=Decimal("1000000"),
                adjustment_factor=Decimal("0.5"),
                adjustment_mode=adjustment_mode,
                source_provider="fixture_mixed_adjustment",
                dataset_revision=revision,
                fetched_at=fetched_at,
            )
            for instrument_id, adjustment_mode, adjusted in cases
        ],
        revision=revision,
    )
    instrument_ids = [item[0] for item in cases]
    legacy_provider = ReplayMarketDataProvider(repository, revision)
    lightweight_provider = ReplayMarketDataProvider(repository, revision)

    legacy = _adjusted_prefilter_bars(
        legacy_provider.get_daily_bars(
            instrument_ids,
            decision_date,
            decision_date,
        )
    )[["instrument_id", "trade_date", "close", "volume"]]
    lightweight = lightweight_provider.get_factor_prefilter_bars(
        instrument_ids,
        decision_date,
        decision_date,
    )

    pd.testing.assert_frame_equal(
        legacy.sort_values(["instrument_id", "trade_date"]).reset_index(drop=True),
        lightweight.sort_values(["instrument_id", "trade_date"]).reset_index(drop=True),
    )
    assert set(lightweight["instrument_id"]) == {"CN:100001", "CN:100004"}
    assert lightweight.set_index("instrument_id").loc["CN:100001", "close"] == 10.0
    assert lightweight.set_index("instrument_id").loc["CN:100004", "close"] == 40.0
    assert any("CN:100003" in error for error in lightweight_provider.last_errors)

    raw_only = lightweight_provider.get_factor_prefilter_bars(
        ["CN:100002"],
        decision_date,
        decision_date,
    )
    assert raw_only.iloc[0]["close"] == 20.0


def test_factor_prefilter_does_not_fall_back_past_latest_incomplete_revision(
    tmp_path,
):
    repository, decision_date = _replay_repository(tmp_path)
    fetched_at = datetime(2025, 1, 11, tzinfo=timezone.utc)
    instrument_id = "CN:100005"
    complete_revision = repository.current_revision() + 1
    repository.upsert_replay_bars(
        [
            HistoricalReplayBar(
                provider_mode="free",
                instrument_id=instrument_id,
                trade_date=decision_date,
                raw_open=Decimal("19"),
                raw_high=Decimal("21"),
                raw_low=Decimal("18"),
                raw_close=Decimal("20"),
                adjusted_open=Decimal("9.5"),
                adjusted_high=Decimal("10.5"),
                adjusted_low=Decimal("9"),
                adjusted_close=Decimal("10"),
                volume=Decimal("1000000"),
                adjustment_factor=Decimal("0.5"),
                adjustment_mode="qfq",
                source_provider="fixture_complete_revision",
                dataset_revision=complete_revision,
                fetched_at=fetched_at,
            )
        ],
        revision=complete_revision,
    )
    incomplete_revision = complete_revision + 1
    repository.upsert_replay_bars(
        [
            HistoricalReplayBar(
                provider_mode="free",
                instrument_id=instrument_id,
                trade_date=decision_date,
                raw_open=Decimal("20"),
                raw_high=Decimal("22"),
                raw_low=Decimal("19"),
                raw_close=Decimal("21"),
                adjusted_close=Decimal("10.5"),
                volume=Decimal("1100000"),
                adjustment_factor=Decimal("0.5"),
                adjustment_mode="qfq",
                source_provider="fixture_incomplete_revision",
                dataset_revision=incomplete_revision,
                fetched_at=fetched_at,
            )
        ],
        revision=incomplete_revision,
    )
    legacy_provider = ReplayMarketDataProvider(repository, incomplete_revision)
    lightweight_provider = ReplayMarketDataProvider(repository, incomplete_revision)

    legacy = _adjusted_prefilter_bars(
        legacy_provider.get_daily_bars(
            [instrument_id],
            decision_date,
            decision_date,
        )
    )
    lightweight = lightweight_provider.get_factor_prefilter_bars(
        [instrument_id],
        decision_date,
        decision_date,
    )

    assert legacy.empty
    assert lightweight.empty
    assert lightweight_provider.factor_prefilter_rows_loaded == 1
    assert any(instrument_id in error for error in lightweight_provider.last_errors)


def test_factor_prefilter_reuses_incremental_window_and_prunes(
    tmp_path,
    monkeypatch,
):
    repository, _ = _replay_repository(tmp_path)
    provider = ReplayMarketDataProvider(repository, repository.current_revision())
    original = repository.replay_factor_bar_rows
    calls = []

    def tracked_factor_rows(instrument_ids, start, end, dataset_revision):
        calls.append((list(instrument_ids), start, end, dataset_revision))
        return original(instrument_ids, start, end, dataset_revision)

    monkeypatch.setattr(
        repository,
        "replay_factor_bar_rows",
        tracked_factor_rows,
    )

    first = provider.get_factor_prefilter_bars(
        ["CN:000001"],
        date(2024, 1, 2),
        date(2025, 1, 6),
    )
    second = provider.get_factor_prefilter_bars(
        ["CN:000001"],
        date(2024, 1, 8),
        date(2025, 1, 10),
    )

    assert len(calls) == 2
    assert calls[0][1:3] == (date(2024, 1, 2), date(2025, 1, 6))
    assert calls[1][1:3] == (date(2025, 1, 7), date(2025, 1, 10))
    assert min(second["trade_date"]) >= date(2024, 1, 8)
    assert max(second["trade_date"]) == date(2025, 1, 10)
    assert min(provider._factor_bars["trade_date"]) >= date(2024, 1, 8)
    assert len(second) < len(first) + provider.factor_prefilter_rows_loaded
    assert provider.factor_prefilter_full_window_queries == 1
    assert provider.factor_prefilter_incremental_queries == 1
    assert provider.query_count == 0
    assert provider.tradability_query_count == 0


def test_replay_market_prefetch_avoids_per_instrument_queries(tmp_path, monkeypatch):
    repository, _ = _replay_repository(tmp_path)
    provider = ReplayMarketDataProvider(repository, repository.current_revision())
    original = repository.replay_bar_rows
    calls = []

    def tracked_replay_bar_rows(instrument_ids, start, end, dataset_revision):
        calls.append(list(instrument_ids))
        return original(instrument_ids, start, end, dataset_revision)

    monkeypatch.setattr(repository, "replay_bar_rows", tracked_replay_bar_rows)
    instrument_ids = ["CN:000001", "CN:000300.IDX"]
    provider.prefetch_daily_bars(
        instrument_ids,
        date(2024, 1, 2),
        date(2025, 1, 10),
    )

    for instrument_id in instrument_ids:
        frame = provider.get_daily_bars(
            [instrument_id],
            date(2024, 1, 2),
            date(2025, 1, 10),
        )
        assert not frame.empty

    assert calls == [instrument_ids]


def test_walk_forward_prefilter_reserves_non_stock_candidates():
    eligible = [f"CN:{index:06d}" for index in range(8)]
    non_stocks = eligible[-2:]
    rankings = [SimpleNamespace(instrument_id=instrument_id) for instrument_id in eligible]

    candidates = _walk_forward_candidates(
        eligible=eligible,
        eligible_non_stocks=non_stocks,
        rankings=rankings,
        limit=5,
    )

    assert len(candidates) == 5
    assert candidates[:4] == eligible[:4]
    assert candidates[-1] == non_stocks[0]


def test_full_market_walk_forward_selection_is_reproducible(tmp_path):
    repository, decision_date = _replay_repository(tmp_path)

    first = run_full_market_walk_forward_selection(
        repository,
        owner_run_id="walk-forward-fixture",
        start=decision_date,
        end=decision_date,
        rebalance_step_sessions=1,
    )
    second = run_full_market_walk_forward_selection(
        repository,
        owner_run_id="walk-forward-fixture",
        start=decision_date,
        end=decision_date,
        rebalance_step_sessions=1,
    )

    assert first.dataset_revision == 4
    assert len(first.snapshots) == 1
    assert first.snapshots[0].historical_universe_size == 1
    assert first.snapshots[0].eligible_size == 1
    assert first.snapshots[0].evaluated_size == 1
    assert first.snapshots[0].prefilter_ranked_size == 1
    assert first.reproducibility_digest == second.reproducibility_digest
    assert first.experiment_manifest.dataset_revision == repository.current_revision()
    assert first.experiment_manifest.strategy_registry_digest
    assert first.snapshots == second.snapshots
    assert first.top_5_portfolio == second.top_5_portfolio
    assert first.data_health["walk_forward_fundamental_fallback_queries"] == "0"
    assert first.data_health["walk_forward_fundamental_prefetches"] == "1"
    assert first.data_health["walk_forward_factor_prefilter_queries"] == "1"
    assert first.data_health["walk_forward_factor_prefilter_full_queries"] == "1"
    assert first.data_health["walk_forward_factor_prefilter_incremental_queries"] == "0"
    assert int(first.data_health["walk_forward_factor_prefilter_rows_loaded"]) > 0
    assert int(first.data_health["walk_forward_replay_cache_queries"]) > 0
    assert int(first.data_health["walk_forward_replay_cache_rows_loaded"]) > 0
    assert first.top_10_portfolio == second.top_10_portfolio
    assert len(first.benchmarks) == 5
    assert all(item.status == "ready" for item in first.benchmarks[:4])
    assert first.benchmarks[-1].benchmark_id == ELIGIBLE_UNIVERSE_BENCHMARK_ID
    assert first.benchmarks[-1].status == "missing"
    assert first.top_5_temporal_validation.return_horizon_days == 20
    assert first.top_5_temporal_validation.embargo_days == 20
    assert first.data_health["walk_forward_top_5_oos_gate"] == "insufficient"
    assert first.data_health["walk_forward_validation_scope"] == "full_market"
    assert first.data_health["walk_forward_market_coverage_gate"] == "ready"
    assert first.data_health["walk_forward_cross_section_coverage_pct"] == "100.0"
    assert first.data_health["walk_forward_fundamental_coverage_gate"] == "ready"
    assert first.data_health["walk_forward_fundamental_coverage_pct"] == "100.0"
    assert first.data_health["walk_forward_median_evaluated_instruments"] == "1"
    assert first.data_health["walk_forward_top_5_validation_gate"] == "insufficient"
    assert [item.key for item in first.cost_sensitivity] == [
        "base",
        "elevated",
        "stress",
    ]
    assert first.cost_sensitivity[-1].slippage_bps == Decimal("20")
    assert first.cost_sensitivity[-1].fee_multiplier == Decimal("2")
    assert first.data_health["walk_forward_cost_scenarios"] == "3"
    assert first.data_health["walk_forward_benchmarks_ready"] == "4/5"
    assert first.data_health["walk_forward_equal_weight_benchmark"] == "missing"
    assert (
        first.data_health["walk_forward_future_data_guard"]
        == "revision_lease_and_decision_date_cutoff"
    )
    assert first.strategy_validation.status == "insufficient"
    assert {item.key for item in first.strategy_validation.criteria} == {
        "statistical_control",
        "market_coverage",
        "fundamental_coverage",
        "out_of_sample_count",
        "out_of_sample_return",
        "benchmark_excess",
        "cost_stress",
        "max_drawdown",
    }
    assert all(
        item.action == "observe"
        for item in [
            *first.strategy_validation.strategies,
            *first.strategy_validation.factors,
        ]
    )


def test_parallel_walk_forward_matches_serial_results(tmp_path):
    repository, decision_date = _replay_repository(tmp_path)

    serial = run_full_market_walk_forward_selection(
        repository,
        owner_run_id="walk-forward-serial",
        start=decision_date,
        end=date(2025, 1, 13),
        rebalance_step_sessions=1,
        snapshot_workers=1,
    )
    parallel = run_full_market_walk_forward_selection(
        repository,
        owner_run_id="walk-forward-parallel",
        start=decision_date,
        end=date(2025, 1, 13),
        rebalance_step_sessions=1,
        snapshot_workers=2,
    )

    assert len(serial.snapshots) == 2
    assert parallel.snapshots == serial.snapshots
    assert parallel.reproducibility_digest == serial.reproducibility_digest
    assert parallel.top_5_portfolio == serial.top_5_portfolio
    assert parallel.top_10_portfolio == serial.top_10_portfolio
    assert parallel.data_health["walk_forward_snapshot_workers"] == "2"


def test_parallel_walk_forward_runs_inside_background_process(tmp_path):
    repository, _ = _replay_repository(tmp_path)
    database_url = repository.session_factory.kw["bind"].url.render_as_string(hide_password=False)

    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=get_context("spawn"),
    ) as executor:
        result = executor.submit(
            _run_nested_parallel_walk_forward,
            database_url,
        ).result(timeout=30)

    assert result == {"workers": "2", "snapshots": "2"}


def test_walk_forward_market_coverage_gate_marks_small_replay_as_pilot():
    coverage = _cross_section_coverage(
        [
            WalkForwardSnapshot(
                decision_date=date(2025, 1, 10),
                historical_universe_size=5600,
                eligible_size=20,
                suspended_count=0,
                st_excluded_count=0,
                missing_tradability_count=5580,
            )
        ]
    )

    assert round(float(coverage["ratio"]) * 100, 2) == 0.36
    assert coverage["median_covered"] == 20
    assert coverage["median_universe"] == 5600
    assert _combined_validation_gate("ready", "insufficient") == ("insufficient_market_coverage")


def test_walk_forward_positive_evidence_waits_for_overall_release_gate():
    metric = WalkForwardEvidenceMetric(
        dimension="factor",
        key="momentum",
        label="动量",
        trade_count=120,
        out_of_sample_count=40,
        win_rate=0.58,
        average_return_pct=2.1,
        worst_return_pct=-4.2,
        profit_factor=1.8,
        max_consecutive_losses=5,
        out_of_sample_verdict="positive",
        action="increase",
        suggested_weight_delta=0.04,
        reason="样本外结果为正。",
    )

    _enforce_release_gate_on_positive_evidence([metric], release_status="insufficient")

    assert metric.action == "observe"
    assert metric.suggested_weight_delta == 0.0
    assert "整体上线门禁尚未通过" in metric.reason


def test_walk_forward_resumes_saved_rebalance_snapshots(tmp_path, monkeypatch):
    repository, decision_date = _replay_repository(tmp_path)
    progress = []
    first = run_full_market_walk_forward_selection(
        repository,
        owner_run_id="walk-forward-checkpoint-source",
        start=decision_date,
        end=decision_date,
        rebalance_step_sessions=1,
        progress_callback=progress.append,
    )
    snapshots = [item.snapshot for item in progress if item.snapshot is not None]
    assert len(snapshots) == 1

    def unexpected_scan(*args, **kwargs):
        raise AssertionError("completed rebalance snapshot should not be rescanned")

    monkeypatch.setattr("qagent.backtesting.walk_forward.run_daily_scan", unexpected_scan)
    resumed = run_full_market_walk_forward_selection(
        repository,
        owner_run_id="walk-forward-checkpoint-resume",
        start=decision_date,
        end=decision_date,
        rebalance_step_sessions=1,
        experiment_manifest=first.experiment_manifest,
        resume_snapshots=snapshots,
    )

    assert resumed.snapshots == first.snapshots
    assert resumed.reproducibility_digest == first.reproducibility_digest


def test_walk_forward_trade_validation_uses_embargoed_chronological_windows():
    start = date(2024, 1, 1)
    trades = [
        SimpleNamespace(
            signal_date=start + timedelta(days=index),
            return_pct=1.0 if index % 3 else -0.5,
        )
        for index in range(240)
    ]

    first = _trade_temporal_validation(trades)
    second = _trade_temporal_validation(trades)
    windows = {window.key: window for window in first.windows}

    assert set(windows) == {"train", "validation", "out_of_sample"}
    assert windows["train"].end_date < windows["validation"].start_date
    assert windows["validation"].end_date < windows["out_of_sample"].start_date
    assert windows["out_of_sample"].sample_count >= 30
    assert first.model_dump() == second.model_dump()


def test_equal_weight_benchmark_uses_each_historical_eligible_universe(tmp_path):
    repository, _ = _replay_repository(tmp_path)
    provider = ReplayMarketDataProvider(repository, repository.current_revision())

    result = _equal_weight_eligible_return(
        provider,
        [
            (date(2025, 1, 2), ["CN:000001"]),
            (date(2025, 1, 8), ["CN:000001"]),
        ],
        end=date(2025, 1, 13),
    )

    assert result is not None
    assert result > 0
    assert provider.adjusted_close_stream_queries == 1
    assert provider.query_count == 0


def test_streamed_equal_weight_benchmark_preserves_period_boundaries_and_membership():
    result = _equal_weight_eligible_return_from_stream(
        [
            ("CN:000001", date(2025, 1, 3), 100.0),
            ("CN:000001", date(2025, 1, 8), 110.0),
            ("CN:000001", date(2025, 1, 9), 130.0),
            ("CN:000002", date(2025, 1, 3), 200.0),
            ("CN:000002", date(2025, 1, 5), 220.0),
            ("CN:000002", date(2025, 1, 8), 230.0),
            ("CN:000002", date(2025, 1, 9), 230.0),
            ("CN:000002", date(2025, 1, 10), 207.0),
        ],
        [
            (date(2025, 1, 2), ["CN:000001", "CN:000002"]),
            (date(2025, 1, 8), ["CN:000002"]),
        ],
        end=date(2025, 1, 13),
    )

    assert result == 1.25


def test_walk_forward_result_persists_and_round_trips_complete_payload(tmp_path):
    repository, decision_date = _replay_repository(tmp_path)
    result = run_full_market_walk_forward_selection(
        repository,
        owner_run_id="persisted-walk-forward",
        start=decision_date,
        end=decision_date,
        rebalance_step_sessions=1,
    )
    storage = QagentRepository(repository.session_factory)

    saved = storage.save_walk_forward_run(result)
    loaded = storage.get_walk_forward_run("persisted-walk-forward")
    listed = storage.list_walk_forward_runs(provider="free", limit=5)

    assert loaded is not None
    assert loaded.run_id == saved.run_id
    assert loaded.dataset_revision == result.dataset_revision
    assert loaded.top_5_return_pct == result.top_5_metrics.total_return_pct
    assert loaded.payload["reproducibility_digest"] == result.reproducibility_digest
    assert loaded.payload["cost_sensitivity"]
    assert loaded.data_health["walk_forward_top_5_oos_gate"] == "insufficient"
    assert listed[0].run_id == "persisted-walk-forward"
