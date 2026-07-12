from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from qagent.backtesting.a_share_rules import (
    BrokerFeeRequest,
    build_instrument_rule_metadata,
    load_a_share_rule_schedule,
)
from qagent.backtesting.replay_provider import (
    ReplayMarketDataProvider,
    ReplayStrategyDataProvider,
)
from qagent.backtesting.walk_forward import run_full_market_walk_forward_selection
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
from qagent.strategy_data.models import FundamentalSnapshot


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
            effective_through=decision_date,
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
                adjustment_mode=(
                    "qfq" if instrument_id == "CN:000001" else "none"
                ),
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
                    trade_date=decision_date,
                    trading_status="trading",
                    is_st=False,
                    provider="fixture_tradability",
                )
            ]
        ),
        revision=4,
    )
    schedule = load_a_share_rule_schedule()
    repository.upsert_trading_rules(schedule.trading_rules)
    repository.upsert_fee_rules(
        schedule.fee_rules(
            BrokerFeeRequest(commission_bps="3", minimum_commission="5")
        )
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


def test_replay_adapters_enforce_date_cutoffs(tmp_path):
    repository, decision_date = _replay_repository(tmp_path)
    revision = repository.current_revision()
    market = ReplayMarketDataProvider(repository, revision)
    strategy = ReplayStrategyDataProvider(repository, revision)

    bars = market.get_daily_bars(
        ["CN:000001"], date(2024, 1, 1), decision_date
    )
    fundamentals = strategy.get_fundamentals(
        ["CN:000001"], date(2024, 1, 1), decision_date
    )

    assert max(bars["trade_date"]) == decision_date
    assert bars.iloc[-1]["adjusted_close"] is not None
    assert len(fundamentals) == 1
    assert fundamentals[0].as_of_date == date(2024, 12, 31)
    assert fundamentals[0].pe_ratio == Decimal("10")


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
    assert first.reproducibility_digest == second.reproducibility_digest
    assert first.snapshots == second.snapshots
    assert first.top_5_portfolio == second.top_5_portfolio
    assert first.top_10_portfolio == second.top_10_portfolio
    assert len(first.benchmarks) == 4
    assert all(item.status == "ready" for item in first.benchmarks)
    assert first.data_health["walk_forward_benchmarks_ready"] == "4/4"
    assert (
        first.data_health["walk_forward_future_data_guard"]
        == "revision_lease_and_decision_date_cutoff"
    )
