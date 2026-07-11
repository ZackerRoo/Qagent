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

    migrated = initialize_database(database_url)
    columns = {column["name"] for column in inspect(migrated).get_columns("market_bar_cache")}

    assert {"adjusted_close", "adjustment_factor", "adjustment_type"}.issubset(columns)
