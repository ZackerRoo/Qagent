from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from qagent.backtesting.a_share_rules import (
    BrokerFeeRequest,
    EtfRuleMetadata,
    build_instrument_rule_metadata,
    build_instrument_rule_metadata_schedule,
    load_a_share_rule_schedule,
    load_a_share_rule_schedule_version,
)
from qagent.backtesting.execution import VersionedAshareExecutionResolver
from qagent.db import Base, create_db_engine
from qagent.historical_evidence.models import (
    HistoricalInstrumentProfile,
    HistoricalTerminalSettlement,
)
from qagent.storage import tables as _tables  # noqa: F401
from qagent.storage.replay_evidence import (
    ImmutableRevisionConflict,
    ReplayEvidenceRepository,
)
from sqlalchemy.orm import sessionmaker


def _profile(instrument_id: str, security_type: str = "stock"):
    return HistoricalInstrumentProfile(
        instrument_id=instrument_id,
        snapshot_date=date(2025, 12, 31),
        listing_date=date(2020, 1, 1),
        security_type=security_type,
        listing_status="active",
        provider="fixture",
    )


def _repository(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'rules.db'}")
    Base.metadata.create_all(engine)
    return ReplayEvidenceRepository(sessionmaker(bind=engine), "free")


def test_checked_in_schedule_covers_board_and_registration_boundaries():
    schedule = load_a_share_rule_schedule()

    pre_registration = schedule.trading_rule(
        trade_date=date(2021, 11, 2),
        market="SSE",
        board="main",
        security_type="stock",
    )
    legacy = schedule.trading_rule(
        trade_date=date(2023, 4, 9),
        market="SSE",
        board="main",
        security_type="stock",
    )
    registration = schedule.trading_rule(
        trade_date=date(2023, 4, 10),
        market="SSE",
        board="main",
        security_type="stock",
    )
    st = schedule.trading_rule(
        trade_date=date(2025, 12, 31),
        market="SZSE",
        board="main",
        security_type="stock",
        is_st=True,
    )
    star = schedule.trading_rule(
        trade_date=date(2025, 12, 31),
        market="SSE",
        board="star",
        security_type="stock",
    )
    bse = schedule.trading_rule(
        trade_date=date(2025, 12, 31),
        market="BSE",
        board="bse",
        security_type="stock",
    )

    assert pre_registration.limit_rule_key == "sse-main-pre-2023"
    assert pre_registration.ipo_no_limit_sessions == 1
    assert legacy.ipo_no_limit_sessions == 1
    assert registration.ipo_no_limit_sessions == 5
    assert st.limit_pct == Decimal("5")
    assert star.limit_pct == Decimal("20")
    assert star.ipo_no_limit_sessions == 5
    assert bse.limit_pct == Decimal("30")
    assert bse.minimum_order_quantity == 100
    assert bse.quantity_step == 1


def test_2026_schedule_changes_only_main_board_risk_limit_at_july_boundary():
    schedule = load_a_share_rule_schedule_version("a-share-rules-v2")

    for market in ("SSE", "SZSE"):
        before = schedule.trading_rule(
            trade_date=date(2026, 7, 5),
            market=market,
            board="main",
            security_type="stock",
            is_st=True,
        )
        after = schedule.trading_rule(
            trade_date=date(2026, 7, 6),
            market=market,
            board="main",
            security_type="stock",
            is_st=True,
        )
        assert before.limit_pct == Decimal("5")
        assert after.limit_pct == Decimal("10")

    assert schedule.trading_rule(
        trade_date=date(2026, 1, 1),
        market="SSE",
        board="star",
        security_type="stock",
        is_st=True,
    ).limit_pct == Decimal("20")
    assert schedule.trading_rule(
        trade_date=date(2026, 7, 6),
        market="BSE",
        board="bse",
        security_type="stock",
    ).limit_pct == Decimal("30")
    assert (
        schedule.trading_rule(
            trade_date=date(2026, 7, 6),
            market="CN",
            board="etf_20_t0",
            security_type="etf",
        ).settlement_days
        == 0
    )


def test_legacy_loader_still_has_no_2026_coverage_and_v2_expires_for_runtime_use():
    legacy = load_a_share_rule_schedule()
    assert legacy.rule_set_version == "a-share-rules-v1"
    with pytest.raises(LookupError, match="outside schedule validity"):
        legacy.trading_rule(
            trade_date=date(2026, 1, 1),
            market="SSE",
            board="main",
            security_type="stock",
        )

    current = load_a_share_rule_schedule_version("a-share-rules-v2")
    with pytest.raises(LookupError, match="mandatory review date"):
        current.trading_rule(
            trade_date=date(2027, 1, 1),
            market="SSE",
            board="main",
            security_type="stock",
        )


def test_fee_schedule_requires_broker_terms_and_switches_stamp_duty():
    schedule = load_a_share_rule_schedule()
    rules = schedule.fee_rules(BrokerFeeRequest(commission_bps="2.5", minimum_commission="5"))

    pre_transfer_fee_cut = next(
        item
        for item in rules
        if item.security_type == "stock"
        and item.side == "buy"
        and item.effective_from == date(2021, 11, 1)
    )
    post_transfer_fee_cut = next(
        item
        for item in rules
        if item.security_type == "stock"
        and item.side == "buy"
        and item.effective_from == date(2022, 4, 29)
    )
    old_sell = next(
        item
        for item in rules
        if item.security_type == "stock"
        and item.side == "sell"
        and item.effective_to == date(2023, 8, 27)
    )
    new_sell = next(
        item
        for item in rules
        if item.security_type == "stock"
        and item.side == "sell"
        and item.effective_from == date(2023, 8, 28)
    )
    etf_rules = [item for item in rules if item.security_type == "etf"]

    assert old_sell.stamp_duty_bps == Decimal("10")
    assert new_sell.stamp_duty_bps == Decimal("5")
    assert pre_transfer_fee_cut.transfer_fee_bps == Decimal("0.2")
    assert post_transfer_fee_cut.transfer_fee_bps == Decimal("0.1")
    assert new_sell.transfer_fee_bps == Decimal("0.1")
    assert new_sell.commission_bps == Decimal("2.5")
    assert all(item.stamp_duty_bps == 0 for item in etf_rules)
    assert all(item.transfer_fee_bps == 0 for item in etf_rules)


def test_instrument_metadata_uses_product_specific_etf_rules():
    schedule = load_a_share_rule_schedule()
    metadata = build_instrument_rule_metadata(
        _profile("CN:513500", "etf"),
        effective_from=date(2025, 1, 1),
        schedule=schedule,
        etf=EtfRuleMetadata(
            price_limit_pct=20,
            board_lot=1000,
            t0_category="cross_border",
        ),
    )

    assert metadata.limit_rule_key == "cn-etf-20-t0"
    assert metadata.settlement_days == 0
    assert metadata.board_lot == 1000
    assert metadata.minimum_order_quantity == 1000
    assert metadata.quantity_step == 1000

    with pytest.raises(ValueError, match="unsupported T\\+0 ETF category"):
        build_instrument_rule_metadata(
            _profile("CN:510300", "etf"),
            effective_from=date(2025, 1, 1),
            schedule=schedule,
            etf=EtfRuleMetadata(t0_category="domestic_equity"),
        )


def test_instrument_metadata_schedule_covers_every_rule_transition():
    schedule = load_a_share_rule_schedule()
    metadata = build_instrument_rule_metadata_schedule(
        _profile("CN:600012"),
        start=date(2021, 11, 1),
        end=date(2025, 12, 31),
        schedule=schedule,
    )

    assert [item.effective_from for item in metadata] == [
        date(2021, 11, 1),
        date(2023, 1, 3),
        date(2023, 4, 10),
    ]
    assert [item.limit_rule_key for item in metadata] == [
        "sse-main-pre-2023",
        "sse-main-legacy",
        "sse-main-registration",
    ]


def test_rule_repository_is_idempotent_and_resolves_date_specific_rules(tmp_path):
    repo = _repository(tmp_path)
    schedule = load_a_share_rule_schedule()
    fee_rules = schedule.fee_rules(BrokerFeeRequest(commission_bps="3", minimum_commission="5"))
    metadata = build_instrument_rule_metadata(
        _profile("CN:000001"),
        effective_from=date(2023, 4, 10),
        schedule=schedule,
    )

    assert repo.upsert_trading_rules(schedule.trading_rules) == len(schedule.trading_rules)
    assert repo.upsert_trading_rules(schedule.trading_rules) == len(schedule.trading_rules)
    assert repo.upsert_fee_rules(fee_rules) == len(fee_rules)
    assert repo.upsert_instrument_rule_metadata([metadata]) == 1

    stored_metadata = repo.instrument_rule_metadata_on("CN:000001", date(2025, 1, 2))
    stored_rule = repo.trading_rule_on(
        rule_set_version=stored_metadata.rule_set_version,
        limit_rule_key=stored_metadata.limit_rule_key,
        trade_date=date(2025, 1, 2),
    )
    stored_fees = repo.fee_rules_on(
        fee_schedule_version=stored_metadata.fee_schedule_version,
        fee_rule_key=stored_metadata.fee_rule_key,
        trade_date=date(2025, 1, 2),
    )

    assert stored_rule.limit_pct == Decimal("10")
    assert {item.side for item in stored_fees} == {"buy", "sell"}
    assert next(item for item in stored_fees if item.side == "sell").stamp_duty_bps == 5

    changed = schedule.trading_rules[0].model_copy(update={"limit_pct": Decimal("11")})
    with pytest.raises(ImmutableRevisionConflict):
        repo.upsert_trading_rules([changed])


def test_rule_repository_reports_metadata_coverage_gaps(tmp_path):
    repo = _repository(tmp_path)
    schedule = load_a_share_rule_schedule()
    profile = _profile("CN:600012")
    metadata = build_instrument_rule_metadata_schedule(
        profile,
        start=date(2023, 1, 3),
        end=date(2025, 12, 31),
        schedule=schedule,
    )
    repo.upsert_instrument_rule_metadata(metadata)

    assert (
        repo.instrument_rule_metadata_gaps(
            [profile],
            date(2023, 1, 3),
            date(2025, 12, 31),
        )
        == []
    )
    assert repo.instrument_rule_metadata_gaps(
        [profile],
        date(2021, 11, 1),
        date(2025, 12, 31),
    ) == [("CN:600012", date(2021, 11, 1), date(2023, 1, 2))]


def test_execution_resolver_selects_st_rule_and_date_specific_stamp_duty(tmp_path):
    repo = _repository(tmp_path)
    schedule = load_a_share_rule_schedule()
    repo.upsert_trading_rules(schedule.trading_rules)
    repo.upsert_fee_rules(
        schedule.fee_rules(BrokerFeeRequest(commission_bps="3", minimum_commission="5"))
    )
    repo.upsert_instrument_rule_metadata(
        [
            build_instrument_rule_metadata(
                _profile("CN:000001"),
                effective_from=date(2023, 4, 10),
                schedule=schedule,
            )
        ]
    )
    resolver = VersionedAshareExecutionResolver(repo)

    before = resolver.resolve("CN:000001", date(2023, 8, 27), is_st=True)
    after = resolver.resolve("CN:000001", date(2023, 8, 28), is_st=True)

    assert before.limit_pct == Decimal("5")
    assert before.sell_fee.stamp_duty_bps == Decimal("10")
    assert after.sell_fee.stamp_duty_bps == Decimal("5")


def test_execution_resolver_supports_pre_2023_validation_dates(tmp_path):
    repo = _repository(tmp_path)
    schedule = load_a_share_rule_schedule()
    profile = _profile("CN:600012")
    repo.upsert_trading_rules(schedule.trading_rules)
    repo.upsert_fee_rules(
        schedule.fee_rules(BrokerFeeRequest(commission_bps="3", minimum_commission="5"))
    )
    repo.upsert_instrument_rule_metadata(
        build_instrument_rule_metadata_schedule(
            profile,
            start=date(2021, 11, 1),
            end=date(2025, 12, 31),
            schedule=schedule,
        )
    )

    resolved = VersionedAshareExecutionResolver(repo).resolve(
        "CN:600012",
        date(2021, 11, 2),
    )

    assert resolved.limit_pct == Decimal("10")
    assert resolved.sell_fee.stamp_duty_bps == Decimal("10")
    assert resolved.sell_fee.transfer_fee_bps == Decimal("0.2")


def test_terminal_settlements_are_revision_scoped(tmp_path):
    repo = _repository(tmp_path)
    fetched_at = datetime(2025, 1, 3, tzinfo=timezone.utc)
    settlement = HistoricalTerminalSettlement(
        provider_mode="free",
        instrument_id="CN:000001",
        effective_date=date(2025, 1, 3),
        settlement_type="cash",
        cash_per_share="9.95",
        source_provider="exchange",
        dataset_revision=1,
        fetched_at=fetched_at,
    )

    assert repo.upsert_terminal_settlements([settlement]) == 1
    assert repo.upsert_terminal_settlements([settlement]) == 1
    stored = repo.terminal_settlements(
        ["CN:000001"], date(2025, 1, 1), date(2025, 1, 31), revision=1
    )

    assert len(stored) == 1
    assert stored[0].cash_per_share == Decimal("9.95")
