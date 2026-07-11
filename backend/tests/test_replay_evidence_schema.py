from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import inspect

from qagent import db
from qagent.historical_evidence import models
from qagent.storage import tables as _tables  # noqa: F401


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
        "market",
        "board",
        "is_st",
        "security_type",
        "effective_from",
    ],
    "historical_instrument_rule_metadata": [
        "provider_mode",
        "instrument_id",
        "effective_from",
    ],
    "historical_fee_rules": ["fee_rule_key", "effective_from", "side"],
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
        "limit_rule_key",
        "fee_rule_key",
        "source_provider",
        "fetched_at",
    },
    "historical_fee_rules": {
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
        limit_rule_key="cn-main-stock",
        fee_rule_key="cn-stock",
        source_provider="exchange",
        fetched_at=fetched_at,
    )
    fee_rule = fee_rule_type(
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
    assert instrument_rule.source_provider == "exchange"
    assert instrument_rule.fetched_at == fetched_at
    assert isinstance(fee_rule.minimum_commission, Decimal)
    assert isinstance(fee_rule.stamp_duty_bps, Decimal)
    assert isinstance(terminal_settlement.cash_per_share, Decimal)
    assert terminal_settlement.dataset_revision == 7
