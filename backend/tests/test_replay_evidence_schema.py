from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import BigInteger, DateTime, Integer, inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from qagent import db
from qagent.historical_evidence import models
from qagent.storage import tables as _tables


EXPECTED_TABLE_KEYS = {
    "historical_replay_bars": ["provider_mode", "instrument_id", "trade_date"],
    "historical_corporate_actions": ["provider_mode", "instrument_id", "action_id"],
    "historical_universe_manifests": ["provider_mode", "snapshot_date", "source_revision"],
    "historical_replay_universe_members": [
        "provider_mode",
        "snapshot_date",
        "source_revision",
        "instrument_id",
    ],
    "historical_lifecycle_manifests": ["provider_mode", "source_revision"],
    "historical_corporate_action_coverage": [
        "provider_mode",
        "instrument_id",
        "start_date",
        "end_date",
    ],
    "historical_trading_rules": [
        "rule_set_version",
        "limit_rule_key",
        "effective_from",
    ],
    "historical_instrument_rule_metadata": [
        "provider_mode",
        "instrument_id",
        "effective_from",
        "rule_set_version",
        "fee_schedule_version",
    ],
    "historical_fee_rules": [
        "fee_schedule_version",
        "fee_rule_key",
        "effective_from",
        "side",
    ],
    "historical_terminal_settlements": [
        "provider_mode",
        "instrument_id",
        "effective_date",
        "settlement_type",
    ],
    "historical_data_revisions": ["provider_mode"],
    "historical_dataset_leases": ["provider_mode"],
}


EXPECTED_TABLE_COLUMNS = {
    "historical_replay_bars": {
        "provider_mode",
        "instrument_id",
        "trade_date",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "volume",
        "turnover",
        "adjustment_factor",
        "adjustment_mode",
        "source_provider",
        "dataset_revision",
        "fetched_at",
    },
    "historical_corporate_actions": {
        "provider_mode",
        "instrument_id",
        "action_id",
        "announcement_date",
        "record_date",
        "ex_date",
        "effective_date",
        "payable_date",
        "action_type",
        "cash_per_share",
        "share_ratio",
        "rights_ratio",
        "subscription_price",
        "previous_raw_close",
        "ex_right_reference_price",
        "source_provider",
        "dataset_revision",
        "fetched_at",
    },
    "historical_universe_manifests": {
        "provider_mode",
        "snapshot_date",
        "source_revision",
        "status",
        "expected_count",
        "stored_count",
        "error",
        "fetched_at",
    },
    "historical_replay_universe_members": {
        "provider_mode",
        "snapshot_date",
        "source_revision",
        "instrument_id",
        "security_type",
        "listing_date",
        "delisting_date",
        "active",
        "source_provider",
        "fetched_at",
    },
    "historical_lifecycle_manifests": {
        "provider_mode",
        "source_revision",
        "status",
        "expected_count",
        "stored_count",
        "effective_through",
        "error",
        "fetched_at",
    },
    "historical_corporate_action_coverage": {
        "provider_mode",
        "instrument_id",
        "start_date",
        "end_date",
        "status",
        "action_count",
        "source_provider",
        "dataset_revision",
        "fetched_at",
    },
    "historical_trading_rules": {
        "rule_set_version",
        "limit_rule_key",
        "market",
        "board",
        "is_st",
        "security_type",
        "effective_from",
        "effective_to",
        "limit_pct",
        "tick_size",
        "board_lot",
        "settlement_days",
        "ipo_no_limit_sessions",
    },
    "historical_instrument_rule_metadata": {
        "provider_mode",
        "instrument_id",
        "effective_from",
        "effective_to",
        "security_type",
        "market",
        "board",
        "settlement_days",
        "rule_set_version",
        "limit_rule_key",
        "fee_schedule_version",
        "fee_rule_key",
        "source_provider",
        "fetched_at",
    },
    "historical_fee_rules": {
        "fee_schedule_version",
        "fee_rule_key",
        "effective_from",
        "effective_to",
        "side",
        "security_type",
        "exchange",
        "commission_bps",
        "minimum_commission",
        "stamp_duty_bps",
        "transfer_fee_bps",
    },
    "historical_terminal_settlements": {
        "provider_mode",
        "instrument_id",
        "effective_date",
        "settlement_type",
        "cash_per_share",
        "conversion_instrument_id",
        "conversion_ratio",
        "source_provider",
        "dataset_revision",
        "fetched_at",
    },
    "historical_data_revisions": {"provider_mode", "revision", "updated_at"},
    "historical_dataset_leases": {
        "provider_mode",
        "owner_run_id",
        "revision",
        "lease_expires_at",
        "heartbeat_at",
    },
}


def test_fresh_database_contains_replay_evidence_tables_columns_and_keys(tmp_path):
    engine = db.initialize_database(f"sqlite:///{tmp_path / 'replay-schema.db'}")
    inspector = inspect(engine)

    assert EXPECTED_TABLE_KEYS.keys() <= set(inspector.get_table_names())
    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        assert columns == expected_columns
        assert inspector.get_pk_constraint(table_name)["constrained_columns"] == (
            EXPECTED_TABLE_KEYS[table_name]
        )


def test_replay_models_preserve_decimal_and_evidence_metadata():
    fetched_at = datetime(2025, 1, 3, 8, 0, tzinfo=timezone.utc)
    replay_bar_type = getattr(models, "HistoricalReplayBar", None)
    corporate_action_type = getattr(models, "HistoricalCorporateAction", None)
    universe_manifest_type = getattr(models, "HistoricalUniverseManifest", None)
    lifecycle_manifest_type = getattr(models, "HistoricalLifecycleManifest", None)
    trading_rule_type = getattr(models, "HistoricalTradingRule", None)
    instrument_rule_type = getattr(models, "HistoricalInstrumentRuleMetadata", None)
    fee_rule_type = getattr(models, "HistoricalFeeRule", None)
    terminal_settlement_type = getattr(models, "HistoricalTerminalSettlement", None)

    assert all(
        model_type is not None
        for model_type in (
            replay_bar_type,
            corporate_action_type,
            universe_manifest_type,
            lifecycle_manifest_type,
            trading_rule_type,
            instrument_rule_type,
            fee_rule_type,
            terminal_settlement_type,
        )
    )

    replay_bar = replay_bar_type(
        provider_mode="free",
        instrument_id="CN:000001",
        trade_date=date(2025, 1, 2),
        raw_open="10.01",
        raw_high="10.30",
        raw_low="9.98",
        raw_close="10.20",
        adjusted_open="9.01",
        adjusted_high="9.30",
        adjusted_low="8.98",
        adjusted_close="9.20",
        volume="1000",
        turnover="10200.00",
        adjustment_factor="0.9019607843",
        adjustment_mode="qfq",
        source_provider="akshare",
        dataset_revision=7,
        fetched_at=fetched_at,
    )
    corporate_action = corporate_action_type(
        provider_mode="free",
        instrument_id="CN:000001",
        action_id="dividend-2025",
        announcement_date=date(2025, 1, 1),
        record_date=date(2025, 1, 2),
        ex_date=date(2025, 1, 3),
        effective_date=date(2025, 1, 3),
        payable_date=date(2025, 1, 8),
        action_type="cash_dividend",
        cash_per_share="0.25",
        share_ratio=None,
        rights_ratio=None,
        subscription_price=None,
        previous_raw_close="10.20",
        ex_right_reference_price="9.95",
        source_provider="akshare",
        dataset_revision=7,
        fetched_at=fetched_at,
    )
    universe_manifest = universe_manifest_type(
        provider_mode="free",
        snapshot_date=date(2025, 1, 2),
        source_revision=7,
        status="ready",
        expected_count=1,
        stored_count=1,
        error=None,
        fetched_at=fetched_at,
    )
    lifecycle_manifest = lifecycle_manifest_type(
        provider_mode="free",
        source_revision=7,
        status="ready",
        expected_count=1,
        stored_count=1,
        effective_through=date(2025, 1, 2),
        error=None,
        fetched_at=fetched_at,
    )
    trading_rule = trading_rule_type(
        rule_set_version="a-share-rules-v1",
        limit_rule_key="cn-main-stock",
        market="CN",
        board="main",
        is_st=False,
        security_type="stock",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        limit_pct="10",
        tick_size="0.01",
        board_lot=100,
        settlement_days=1,
        ipo_no_limit_sessions=5,
    )
    instrument_rule = instrument_rule_type(
        provider_mode="free",
        instrument_id="CN:000001",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        security_type="stock",
        market="CN",
        board="main",
        settlement_days=1,
        rule_set_version="a-share-rules-v1",
        limit_rule_key="cn-main-stock",
        fee_schedule_version="a-share-fees-v1",
        fee_rule_key="cn-stock",
        source_provider="exchange",
        fetched_at=fetched_at,
    )
    fee_rule = fee_rule_type(
        fee_schedule_version="a-share-fees-v1",
        fee_rule_key="cn-stock",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        side="sell",
        security_type="stock",
        exchange="SSE",
        commission_bps="3",
        minimum_commission="5",
        stamp_duty_bps="5",
        transfer_fee_bps="0.1",
    )
    terminal_settlement = terminal_settlement_type(
        provider_mode="free",
        instrument_id="CN:000001",
        effective_date=date(2025, 1, 3),
        settlement_type="cash",
        cash_per_share="9.95",
        conversion_instrument_id=None,
        conversion_ratio=None,
        source_provider="exchange",
        dataset_revision=7,
        fetched_at=fetched_at,
    )

    assert isinstance(replay_bar.raw_close, Decimal)
    assert isinstance(replay_bar.adjusted_close, Decimal)
    assert isinstance(replay_bar.turnover, Decimal)
    assert isinstance(replay_bar.adjustment_factor, Decimal)
    assert isinstance(corporate_action.cash_per_share, Decimal)
    assert isinstance(corporate_action.ex_right_reference_price, Decimal)
    assert universe_manifest.source_revision == 7
    assert lifecycle_manifest.effective_through == date(2025, 1, 2)
    assert isinstance(trading_rule.limit_pct, Decimal)
    assert isinstance(trading_rule.tick_size, Decimal)
    assert trading_rule.limit_rule_key == instrument_rule.limit_rule_key
    assert trading_rule.rule_set_version == instrument_rule.rule_set_version
    assert fee_rule.fee_rule_key == instrument_rule.fee_rule_key
    assert fee_rule.fee_schedule_version == instrument_rule.fee_schedule_version
    assert instrument_rule.source_provider == "exchange"
    assert instrument_rule.fetched_at == fetched_at
    assert isinstance(fee_rule.minimum_commission, Decimal)
    assert isinstance(fee_rule.stamp_duty_bps, Decimal)
    assert isinstance(terminal_settlement.cash_per_share, Decimal)
    assert terminal_settlement.dataset_revision == 7


def _corporate_action_data(**overrides):
    data = {
        "provider_mode": "free",
        "instrument_id": "CN:000001",
        "action_id": "action-1",
        "announcement_date": date(2025, 1, 1),
        "record_date": date(2025, 1, 2),
        "ex_date": date(2025, 1, 3),
        "effective_date": date(2025, 1, 3),
        "payable_date": None,
        "action_type": "split",
        "cash_per_share": None,
        "share_ratio": "1",
        "rights_ratio": None,
        "subscription_price": None,
        "previous_raw_close": "10.20",
        "ex_right_reference_price": "5.10",
        "source_provider": "exchange",
        "dataset_revision": 7,
        "fetched_at": datetime(2025, 1, 3, 8, 0, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "action_type": "cash_dividend",
            "payable_date": date(2025, 1, 8),
            "cash_per_share": "0.25",
            "share_ratio": None,
        },
        {"action_type": "split", "share_ratio": "1"},
        {"action_type": "bonus", "share_ratio": "0.5"},
    ],
)
def test_corporate_action_accepts_supported_complete_types(overrides):
    action = models.HistoricalCorporateAction(**_corporate_action_data(**overrides))

    assert action.action_type == overrides["action_type"]


def test_corporate_action_declares_announcement_date_required():
    schema = models.HistoricalCorporateAction.model_json_schema()

    assert "announcement_date" in schema["required"]
    assert models.HistoricalCorporateAction.model_fields["announcement_date"].is_required()


@pytest.mark.parametrize("action_type", ["rights", "merger", "conversion", "other"])
def test_unsupported_corporate_action_without_economics_persists(
    tmp_path, action_type
):
    database_url = f"sqlite:///{tmp_path / f'unsupported-{action_type}.db'}"
    db.initialize_database(database_url)
    session_factory = db.create_session_factory(database_url)
    action = models.HistoricalCorporateAction(
        **_corporate_action_data(
            action_id=f"{action_type}-1",
            action_type=action_type,
            record_date=None,
            ex_date=None,
            effective_date=date(2025, 1, 3),
            share_ratio=None,
            rights_ratio=None,
            subscription_price=None,
            previous_raw_close=None,
            ex_right_reference_price=None,
        )
    )

    with session_factory() as session:
        session.add(_tables.HistoricalCorporateActionRow(**action.model_dump()))
        session.commit()
        stored = session.get(
            _tables.HistoricalCorporateActionRow,
            ("free", "CN:000001", f"{action_type}-1"),
        )

        assert stored is not None
        assert stored.action_type == action_type
        assert stored.cash_per_share is None
        assert stored.share_ratio is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"action_type": "unknown"},
        {"announcement_date": None},
        {
            "action_type": "other",
            "effective_date": None,
            "ex_date": None,
            "payable_date": None,
        },
        {
            "action_type": "cash_dividend",
            "payable_date": date(2025, 1, 8),
            "cash_per_share": None,
            "share_ratio": None,
        },
        {"action_type": "split", "share_ratio": "0"},
        {"action_type": "bonus", "record_date": None},
    ],
)
def test_corporate_action_rejects_unsupported_or_incomplete_evidence(overrides):
    with pytest.raises(ValidationError):
        models.HistoricalCorporateAction(**_corporate_action_data(**overrides))


def _terminal_settlement_data(**overrides):
    data = {
        "provider_mode": "free",
        "instrument_id": "CN:000001",
        "effective_date": date(2025, 1, 3),
        "settlement_type": "cash",
        "cash_per_share": "9.95",
        "conversion_instrument_id": None,
        "conversion_ratio": None,
        "source_provider": "exchange",
        "dataset_revision": 7,
        "fetched_at": datetime(2025, 1, 3, 8, 0, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return data


def test_terminal_settlement_accepts_complete_cash_and_conversion_types():
    cash = models.HistoricalTerminalSettlement(**_terminal_settlement_data())
    conversion = models.HistoricalTerminalSettlement(
        **_terminal_settlement_data(
            settlement_type="conversion",
            cash_per_share=None,
            conversion_instrument_id="CN:600000",
            conversion_ratio="1.25",
        )
    )

    assert cash.cash_per_share == Decimal("9.95")
    assert conversion.conversion_ratio == Decimal("1.25")


@pytest.mark.parametrize(
    "overrides",
    [
        {"settlement_type": "unknown"},
        {"cash_per_share": None},
        {
            "settlement_type": "conversion",
            "cash_per_share": None,
            "conversion_instrument_id": None,
            "conversion_ratio": "1.25",
        },
        {
            "settlement_type": "conversion",
            "cash_per_share": None,
            "conversion_instrument_id": "CN:600000",
            "conversion_ratio": "0",
        },
    ],
)
def test_terminal_settlement_rejects_incomplete_type_specific_evidence(overrides):
    with pytest.raises(ValidationError):
        models.HistoricalTerminalSettlement(**_terminal_settlement_data(**overrides))


def test_database_rejects_impossible_corporate_action(tmp_path):
    engine = db.initialize_database(f"sqlite:///{tmp_path / 'invalid-action.db'}")

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO historical_corporate_actions (
                    provider_mode, instrument_id, action_id, announcement_date,
                    record_date, effective_date, payable_date, action_type,
                    cash_per_share, source_provider, dataset_revision, fetched_at
                ) VALUES (
                    'free', 'CN:000001', 'bad-dividend', '2025-01-01',
                    '2025-01-02', '2025-01-03', '2025-01-08', 'cash_dividend', '-0.25',
                    'exchange', 7, '2025-01-03 08:00:00'
                )
                """
            )
        )


def test_database_rejects_impossible_terminal_settlement(tmp_path):
    engine = db.initialize_database(f"sqlite:///{tmp_path / 'invalid-settlement.db'}")

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO historical_terminal_settlements (
                    provider_mode, instrument_id, effective_date, settlement_type,
                    cash_per_share, source_provider, dataset_revision, fetched_at
                ) VALUES (
                    'free', 'CN:000001', '2025-01-03', 'cash',
                    '-9.95', 'exchange', 7, '2025-01-03 08:00:00'
                )
                """
            )
        )


@pytest.mark.parametrize(
    ("status", "action_count"),
    [("invalid", 0), ("ready", 0), ("ready_none", 1)],
)
def test_database_rejects_invalid_corporate_action_coverage_semantics(
    tmp_path, status, action_count
):
    engine = db.initialize_database(f"sqlite:///{tmp_path / f'coverage-{status}.db'}")

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO historical_corporate_action_coverage (
                    provider_mode, instrument_id, start_date, end_date, status,
                    action_count, source_provider, dataset_revision, fetched_at
                ) VALUES (
                    'free', 'CN:000001', '2025-01-01', '2025-01-31', :status,
                    :action_count, 'exchange', 7, '2025-01-03 08:00:00'
                )
                """
            ),
            {"status": status, "action_count": action_count},
        )


@pytest.mark.parametrize(
    ("status", "action_count"),
    [("ready", 1), ("ready_none", 0), ("partial", 0), ("unsupported", 0)],
)
def test_database_accepts_valid_corporate_action_coverage_semantics(
    tmp_path, status, action_count
):
    engine = db.initialize_database(f"sqlite:///{tmp_path / f'valid-{status}.db'}")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO historical_corporate_action_coverage (
                    provider_mode, instrument_id, start_date, end_date, status,
                    action_count, source_provider, dataset_revision, fetched_at
                ) VALUES (
                    'free', 'CN:000001', '2025-01-01', '2025-01-31', :status,
                    :action_count, 'exchange', 7, '2025-01-03 08:00:00'
                )
                """
            ),
            {"status": status, "action_count": action_count},
        )


def test_dataset_lease_timestamps_round_trip_as_aware_utc(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'utc-lease.db'}"
    db.initialize_database(database_url)
    session_factory = db.create_session_factory(database_url)
    now = datetime.now(timezone.utc)
    china_timezone = timezone(timedelta(hours=8))
    expires_at = (now + timedelta(hours=1)).astimezone(china_timezone)
    heartbeat_at = now.astimezone(china_timezone)

    with session_factory() as session:
        session.add(
            _tables.HistoricalDatasetLeaseRow(
                provider_mode="free",
                owner_run_id="run-1",
                revision=7,
                lease_expires_at=expires_at,
                heartbeat_at=heartbeat_at,
            )
        )
        revision = _tables.HistoricalDataRevisionRow(provider_mode="free")
        session.add(revision)
        session.commit()
        session.expire_all()

        lease = session.get(_tables.HistoricalDatasetLeaseRow, "free")
        stored_revision = session.get(_tables.HistoricalDataRevisionRow, "free")

        assert lease is not None
        assert stored_revision is not None
        assert lease.lease_expires_at.utcoffset() == timedelta(0)
        assert lease.heartbeat_at.utcoffset() == timedelta(0)
        assert lease.lease_expires_at == expires_at.astimezone(timezone.utc)
        assert lease.heartbeat_at == heartbeat_at.astimezone(timezone.utc)
        assert lease.lease_expires_at > datetime.now(timezone.utc)
        assert stored_revision.revision == 0
        assert stored_revision.updated_at.utcoffset() == timedelta(0)


def test_schema_declares_financial_types_nullability_indexes_and_constraints(tmp_path):
    engine = db.initialize_database(f"sqlite:///{tmp_path / 'schema-invariants.db'}")
    inspector = inspect(engine)
    replay_columns = {
        column["name"]: column for column in inspector.get_columns("historical_replay_bars")
    }
    action_columns = {
        column["name"]: column
        for column in inspector.get_columns("historical_corporate_actions")
    }
    lease_columns = {
        column["name"]: column
        for column in inspector.get_columns("historical_dataset_leases")
    }

    assert isinstance(replay_columns["raw_close"]["type"], BigInteger)
    assert isinstance(replay_columns["volume"]["type"], BigInteger)
    declared_exact_decimals = {
        _tables.HistoricalReplayBarRow.__table__.c.raw_close.type: (20, 8),
        _tables.HistoricalReplayBarRow.__table__.c.volume.type: (28, 4),
        _tables.HistoricalReplayBarRow.__table__.c.turnover.type: (28, 4),
        _tables.HistoricalCorporateActionRow.__table__.c.rights_ratio.type: (24, 12),
        _tables.HistoricalTradingRuleRow.__table__.c.tick_size.type: (18, 8),
        _tables.HistoricalFeeRuleRow.__table__.c.commission_bps.type: (18, 8),
        _tables.HistoricalTerminalSettlementRow.__table__.c.conversion_ratio.type: (24, 12),
    }
    for sql_type, precision_scale in declared_exact_decimals.items():
        assert isinstance(sql_type, db.SQLiteScaledDecimal)
        assert (sql_type.precision, sql_type.scale) == precision_scale
    assert isinstance(replay_columns["dataset_revision"]["type"], Integer)
    assert isinstance(lease_columns["lease_expires_at"]["type"], DateTime)
    assert replay_columns["raw_close"]["nullable"] is False
    assert replay_columns["adjusted_close"]["nullable"] is True
    assert action_columns["announcement_date"]["nullable"] is False
    assert action_columns["cash_per_share"]["nullable"] is True
    assert lease_columns["lease_expires_at"]["nullable"] is False
    assert lease_columns["heartbeat_at"]["nullable"] is False

    expected_indexes = {
        "historical_replay_bars": {
            ("instrument_id",),
            ("trade_date",),
            ("dataset_revision",),
        },
        "historical_corporate_actions": {
            ("instrument_id",),
            ("ex_date",),
            ("effective_date",),
            ("action_type",),
            ("dataset_revision",),
        },
        "historical_instrument_rule_metadata": {
            ("instrument_id",),
            ("limit_rule_key",),
            ("fee_rule_key",),
        },
        "historical_dataset_leases": {("owner_run_id",), ("lease_expires_at",)},
    }
    for table_name, expected in expected_indexes.items():
        indexes = {
            tuple(index["column_names"]) for index in inspector.get_indexes(table_name)
        }
        assert expected <= indexes
        assert all(not index["unique"] for index in inspector.get_indexes(table_name))

    expected_checks = {
        "historical_corporate_actions": {
            "ck_historical_corporate_actions_type",
            "ck_historical_corporate_actions_evidence",
        },
        "historical_corporate_action_coverage": {
            "ck_historical_corporate_action_coverage_status",
            "ck_historical_corporate_action_coverage_count",
        },
        "historical_terminal_settlements": {
            "ck_historical_terminal_settlements_type",
            "ck_historical_terminal_settlements_evidence",
        },
    }
    for table_name, expected in expected_checks.items():
        constraints = {
            constraint["name"] for constraint in inspector.get_check_constraints(table_name)
        }
        assert constraints == expected


def test_realistic_financial_decimals_round_trip_exactly(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'decimal-round-trip.db'}"
    db.initialize_database(database_url)
    session_factory = db.create_session_factory(database_url)
    trade_date = date(2025, 1, 2)
    price = Decimal("99999999.12345678")
    volume = Decimal("99999999999.1234")
    turnover = Decimal("99999999999999.1234")
    adjustment_factor = Decimal("999999.123456789012")
    share_ratio = Decimal("999999.123456789012")
    subscription_price = Decimal("99999999.12345678")
    commission_bps = Decimal("999999.12345678")
    minimum_commission = Decimal("999999.12345678")
    stamp_duty_bps = Decimal("999999.12345678")
    transfer_fee_bps = Decimal("999999.12345678")

    with session_factory() as session:
        session.add_all(
            [
                _tables.HistoricalReplayBarRow(
                    provider_mode="free",
                    instrument_id="CN:000001",
                    trade_date=trade_date,
                    raw_open=price,
                    raw_high=price,
                    raw_low=price,
                    raw_close=price,
                    adjusted_open=price,
                    adjusted_high=price,
                    adjusted_low=price,
                    adjusted_close=price,
                    volume=volume,
                    turnover=turnover,
                    adjustment_factor=adjustment_factor,
                    adjustment_mode="qfq",
                    source_provider="akshare",
                    dataset_revision=7,
                ),
                _tables.HistoricalCorporateActionRow(
                    provider_mode="free",
                    instrument_id="CN:000001",
                    action_id="rights-1",
                    announcement_date=date(2024, 12, 20),
                    record_date=trade_date,
                    ex_date=date(2025, 1, 3),
                    effective_date=date(2025, 1, 3),
                    action_type="rights",
                    rights_ratio=share_ratio,
                    subscription_price=subscription_price,
                    source_provider="exchange",
                    dataset_revision=7,
                ),
                _tables.HistoricalFeeRuleRow(
                    fee_schedule_version="a-share-fees-v1",
                    fee_rule_key="cn-stock",
                    effective_from=trade_date,
                    side="sell",
                    security_type="stock",
                    exchange="SSE",
                    commission_bps=commission_bps,
                    minimum_commission=minimum_commission,
                    stamp_duty_bps=stamp_duty_bps,
                    transfer_fee_bps=transfer_fee_bps,
                ),
                _tables.HistoricalTerminalSettlementRow(
                    provider_mode="free",
                    instrument_id="CN:000002",
                    effective_date=trade_date,
                    settlement_type="conversion",
                    conversion_instrument_id="CN:600000",
                    conversion_ratio=share_ratio,
                    source_provider="exchange",
                    dataset_revision=7,
                ),
            ]
        )
        session.commit()
        session.expire_all()

        bar = session.get(
            _tables.HistoricalReplayBarRow, ("free", "CN:000001", trade_date)
        )
        action = session.get(
            _tables.HistoricalCorporateActionRow,
            ("free", "CN:000001", "rights-1"),
        )
        fee = session.get(
            _tables.HistoricalFeeRuleRow,
            ("a-share-fees-v1", "cn-stock", trade_date, "sell"),
        )
        settlement = session.get(
            _tables.HistoricalTerminalSettlementRow,
            ("free", "CN:000002", trade_date, "conversion"),
        )

        assert bar is not None and bar.fetched_at is not None
        assert action is not None and action.fetched_at is not None
        assert fee is not None
        assert settlement is not None and settlement.fetched_at is not None
        assert bar.raw_close == price
        assert bar.adjusted_close == price
        assert bar.volume == volume
        assert bar.turnover == turnover
        assert bar.adjustment_factor == adjustment_factor
        assert action.rights_ratio == share_ratio
        assert action.subscription_price == subscription_price
        assert fee.commission_bps == commission_bps
        assert fee.minimum_commission == minimum_commission
        assert fee.stamp_duty_bps == stamp_duty_bps
        assert fee.transfer_fee_bps == transfer_fee_bps
        assert settlement.conversion_ratio == share_ratio


def _priced_replay_bar(trade_date, close):
    return _tables.HistoricalReplayBarRow(
        provider_mode="free",
        instrument_id="CN:000001",
        trade_date=trade_date,
        raw_open=close,
        raw_high=close,
        raw_low=close,
        raw_close=close,
        volume=Decimal("1"),
        adjustment_mode="raw",
        source_provider="fixture",
        dataset_revision=1,
    )


def test_scaled_decimals_preserve_sql_comparison_and_ordering(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'decimal-ordering.db'}"
    db.initialize_database(database_url)
    session_factory = db.create_session_factory(database_url)
    values = [Decimal("9"), Decimal("9.5"), Decimal("10")]

    with session_factory() as session:
        session.add_all(
            [
                _priced_replay_bar(date(2025, 1, day), value)
                for day, value in enumerate(values, start=1)
            ]
        )
        session.commit()

        greater = list(
            session.scalars(
                select(_tables.HistoricalReplayBarRow.raw_close)
                .where(_tables.HistoricalReplayBarRow.raw_close > Decimal("9.5"))
                .order_by(_tables.HistoricalReplayBarRow.raw_close)
            )
        )
        lower = list(
            session.scalars(
                select(_tables.HistoricalReplayBarRow.raw_close)
                .where(_tables.HistoricalReplayBarRow.raw_close < Decimal("9.5"))
                .order_by(_tables.HistoricalReplayBarRow.raw_close)
            )
        )
        ordered = list(
            session.scalars(
                select(_tables.HistoricalReplayBarRow.raw_close).order_by(
                    _tables.HistoricalReplayBarRow.raw_close
                )
            )
        )

        assert greater == [Decimal("10")]
        assert lower == [Decimal("9")]
        assert ordered == values


def test_scaled_decimal_rejects_sqlite_integer_overflow(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'decimal-overflow.db'}"
    db.initialize_database(database_url)
    session_factory = db.create_session_factory(database_url)
    overflow_price = Decimal("100000000000.00000000")

    with session_factory() as session, pytest.raises(
        StatementError, match="signed 64-bit SQLite range"
    ):
        session.add(_priced_replay_bar(date(2025, 1, 1), overflow_price))
        session.commit()
