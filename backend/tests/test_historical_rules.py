from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from qagent.backtesting.a_share_rules import (
    BrokerFeeRequest,
    EtfRuleMetadata,
    build_instrument_rule_metadata,
    load_a_share_rule_schedule,
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

    assert legacy.ipo_no_limit_sessions == 1
    assert registration.ipo_no_limit_sessions == 5
    assert st.limit_pct == Decimal("5")
    assert star.limit_pct == Decimal("20")
    assert star.ipo_no_limit_sessions == 5
    assert bse.limit_pct == Decimal("30")
    assert bse.minimum_order_quantity == 100
    assert bse.quantity_step == 1


def test_fee_schedule_requires_broker_terms_and_switches_stamp_duty():
    schedule = load_a_share_rule_schedule()
    rules = schedule.fee_rules(BrokerFeeRequest(commission_bps="2.5", minimum_commission="5"))

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


def test_execution_resolver_selects_st_rule_and_date_specific_stamp_duty(tmp_path):
    repo = _repository(tmp_path)
    schedule = load_a_share_rule_schedule()
    repo.upsert_trading_rules(schedule.trading_rules)
    repo.upsert_fee_rules(
        schedule.fee_rules(
            BrokerFeeRequest(commission_bps="3", minimum_commission="5")
        )
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
