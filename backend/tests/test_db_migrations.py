from sqlalchemy import inspect, text

from qagent.db import create_db_engine, initialize_database
from qagent.storage import tables as _tables  # noqa: F401


def test_initialize_database_adds_adjustment_columns_to_legacy_market_cache(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy-cache.db'}"
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE market_bar_cache (
                    provider_mode VARCHAR(32) NOT NULL,
                    instrument_id VARCHAR(32) NOT NULL,
                    trade_date DATE NOT NULL,
                    source_provider VARCHAR(64) NOT NULL DEFAULT '',
                    open NUMERIC(18, 6) NOT NULL,
                    high NUMERIC(18, 6) NOT NULL,
                    low NUMERIC(18, 6) NOT NULL,
                    close NUMERIC(18, 6) NOT NULL,
                    volume NUMERIC(24, 4) NOT NULL,
                    cached_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (provider_mode, instrument_id, trade_date)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO market_bar_cache (
                    provider_mode, instrument_id, trade_date, source_provider,
                    open, high, low, close, volume, cached_at, updated_at
                ) VALUES (
                    'free', 'CN:000001', '2025-01-02', 'legacy-provider',
                    10.0, 10.5, 9.8, 10.2, 1000, '2025-01-03', '2025-01-03'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tradable_universe_snapshots (
                    as_of_date DATE NOT NULL,
                    instrument_id VARCHAR(32) NOT NULL,
                    symbol VARCHAR(16) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    asset_type VARCHAR(32) NOT NULL,
                    exchange VARCHAR(16) NOT NULL,
                    source VARCHAR(96) NOT NULL DEFAULT '',
                    active BOOLEAN NOT NULL DEFAULT 1,
                    captured_at DATETIME NOT NULL,
                    PRIMARY KEY (as_of_date, instrument_id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO tradable_universe_snapshots (
                    as_of_date, instrument_id, symbol, name, asset_type,
                    exchange, source, active, captured_at
                ) VALUES (
                    '2025-01-02', 'CN:000001', '000001', 'Legacy Bank',
                    'stock', 'SZSE', 'legacy-catalog', 1, '2025-01-03'
                )
                """
            )
        )

    migrated = initialize_database(database_url)
    columns = {column["name"] for column in inspect(migrated).get_columns("market_bar_cache")}

    assert {"adjusted_close", "adjustment_factor", "adjustment_type"}.issubset(columns)
    assert inspect(migrated).get_pk_constraint("tradable_universe_snapshots")[
        "constrained_columns"
    ] == ["as_of_date", "instrument_id"]
    with migrated.connect() as connection:
        market_row = connection.execute(
            text(
                "SELECT source_provider, close FROM market_bar_cache "
                "WHERE provider_mode = 'free' AND instrument_id = 'CN:000001'"
            )
        ).one()
        universe_row = connection.execute(
            text(
                "SELECT name, source FROM tradable_universe_snapshots "
                "WHERE as_of_date = '2025-01-02' AND instrument_id = 'CN:000001'"
            )
        ).one()

    assert market_row == ("legacy-provider", 10.2)
    assert universe_row == ("Legacy Bank", "legacy-catalog")
    assert "historical_replay_universe_members" in inspect(migrated).get_table_names()
    assert inspect(migrated).get_pk_constraint("historical_trading_rules")[
        "constrained_columns"
    ] == ["rule_set_version", "limit_rule_key", "effective_from"]
    assert inspect(migrated).get_pk_constraint("historical_fee_rules")[
        "constrained_columns"
    ] == ["fee_schedule_version", "fee_rule_key", "effective_from", "side"]
