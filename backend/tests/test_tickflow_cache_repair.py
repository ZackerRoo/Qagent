from pathlib import Path
import sqlite3

import pytest

from qagent.jobs.tickflow_cache_repair import REPAIR_VERSION, repair_tickflow_cache_dates
from qagent.providers.tickflow_free import TICKFLOW_PAIRED_SOURCE_PROVIDER


def _create_cache_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE market_bar_cache (
                provider_mode TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                trade_date DATE NOT NULL,
                source_provider TEXT NOT NULL,
                open NUMERIC NOT NULL,
                high NUMERIC NOT NULL,
                low NUMERIC NOT NULL,
                close NUMERIC NOT NULL,
                volume NUMERIC NOT NULL,
                cached_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                adjusted_close NUMERIC,
                adjustment_factor NUMERIC,
                adjustment_type TEXT,
                turnover NUMERIC,
                adjusted_open NUMERIC,
                adjusted_high NUMERIC,
                adjusted_low NUMERIC,
                PRIMARY KEY (provider_mode, instrument_id, trade_date)
            );
            CREATE TABLE market_data_cache_spans (
                provider_mode TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                row_count INTEGER NOT NULL,
                cached_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (provider_mode, instrument_id, start_date, end_date)
            );
            """
        )
        _insert_bar(
            connection,
            instrument_id="CN:000710",
            trade_date="2026-08-16",
            source_provider="tickflow_free_paired",
            close=9.06,
        )
        _insert_bar(
            connection,
            instrument_id="CN:000710",
            trade_date="2026-08-17",
            source_provider="tickflow_free_paired",
            close=8.92,
        )
        _insert_bar(
            connection,
            instrument_id="CN:000710",
            trade_date="2026-08-18",
            source_provider="baostock_paired",
            close=8.92,
        )
        _insert_bar(
            connection,
            instrument_id="CN:000001",
            trade_date="2026-08-17",
            source_provider="baostock_paired",
            close=11.1,
        )
        connection.executemany(
            """
            INSERT INTO market_data_cache_spans
                (provider_mode, instrument_id, start_date, end_date, row_count,
                 cached_at, updated_at)
            VALUES ('free', ?, '2026-01-01', '2026-08-18', 100, ?, ?)
            """,
            [
                ("CN:000710", "2026-08-18 00:00:00", "2026-08-18 00:00:00"),
                ("CN:000001", "2026-08-18 00:00:00", "2026-08-18 00:00:00"),
            ],
        )


def _insert_bar(
    connection: sqlite3.Connection,
    *,
    instrument_id: str,
    trade_date: str,
    source_provider: str,
    close: float,
) -> None:
    connection.execute(
        """
        INSERT INTO market_bar_cache (
            provider_mode, instrument_id, trade_date, source_provider,
            open, high, low, close, volume, cached_at, updated_at,
            adjusted_close, adjustment_factor, adjustment_type, turnover,
            adjusted_open, adjusted_high, adjusted_low
        ) VALUES (
            'free', ?, ?, ?, ?, ?, ?, ?, 1000,
            '2026-08-18 00:00:00', '2026-08-18 00:00:00',
            ?, 1, 'forward', 10000, ?, ?, ?
        )
        """,
        (
            instrument_id,
            trade_date,
            source_provider,
            close,
            close,
            close,
            close,
            close,
            close,
            close,
            close,
        ),
    )


def test_tickflow_cache_repair_defaults_to_dry_run(tmp_path):
    database = tmp_path / "qagent.db"
    _create_cache_database(database)

    report = repair_tickflow_cache_dates(database)

    assert report.dry_run is True
    assert report.affected_rows == 2
    assert report.affected_instruments == 1
    assert report.target_conflicts == 2
    assert report.legacy_tickflow_target_conflicts == 1
    assert report.non_tickflow_target_conflicts == 1
    assert report.rows_moved == 1
    assert report.rows_discarded_for_preferred_target == 1
    assert report.backup_path is None
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM market_bar_cache WHERE source_provider = ?",
            ("tickflow_free_paired",),
        ).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM market_data_cache_spans").fetchone()[0] == 2


def test_tickflow_cache_repair_backs_up_prefers_non_tickflow_and_is_idempotent(tmp_path):
    database = tmp_path / "qagent.db"
    backup = tmp_path / "qagent-before-tickflow-repair.db"
    _create_cache_database(database)

    report = repair_tickflow_cache_dates(database, execute=True, backup_path=backup)

    assert report.dry_run is False
    assert report.affected_rows == 2
    assert report.rows_moved == 1
    assert report.rows_discarded_for_preferred_target == 1
    assert report.coverage_spans_invalidated == 1
    assert report.backup_path == str(backup)
    assert backup.is_file()

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT trade_date, source_provider, close
            FROM market_bar_cache
            WHERE instrument_id = 'CN:000710'
            ORDER BY trade_date
            """
        ).fetchall()
        assert rows == [
            ("2026-08-17", TICKFLOW_PAIRED_SOURCE_PROVIDER, 9.06),
            ("2026-08-18", "baostock_paired", 8.92),
        ]
        spans = connection.execute(
            "SELECT instrument_id FROM market_data_cache_spans ORDER BY instrument_id"
        ).fetchall()
        assert spans == [("CN:000001",)]

    with sqlite3.connect(backup) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM market_bar_cache_backup WHERE source_provider = ?",
            ("tickflow_free_paired",),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM market_data_cache_spans_backup"
        ).fetchone()[0] == 1
        metadata = connection.execute(
            """
            SELECT repair_version, source_database, provider_mode,
                   affected_rows, affected_instruments, restore_note
            FROM tickflow_cache_repair_metadata
            """
        ).fetchone()
        assert metadata[:5] == (REPAIR_VERSION, str(database), "free", 2, 1)
        assert "Restore market_bar_cache_backup" in metadata[5]

    second = repair_tickflow_cache_dates(database, execute=True, backup_path=backup)
    assert second.affected_rows == 0
    assert second.rows_moved == 0
    assert second.coverage_spans_invalidated == 0
    assert second.backup_path is None


def test_tickflow_cache_repair_refuses_to_overwrite_sidecar(tmp_path):
    database = tmp_path / "qagent.db"
    backup = tmp_path / "existing-sidecar.db"
    _create_cache_database(database)
    backup.write_text("keep me", encoding="utf-8")

    with pytest.raises(FileExistsError, match="backup already exists"):
        repair_tickflow_cache_dates(database, execute=True, backup_path=backup)

    assert backup.read_text(encoding="utf-8") == "keep me"
