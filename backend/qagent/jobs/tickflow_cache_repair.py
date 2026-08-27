"""Repair legacy TickFlow cache rows that were stored one calendar day early.

The command is a dry-run unless ``--execute`` is supplied. Execution creates a
small SQLite sidecar containing only affected bars, their coverage spans, and
repair metadata before opening the write transaction. Legacy source names are
rewritten to the Shanghai-normalized source names, making repeated runs
idempotent and keeping newly ingested, already-correct rows out of scope.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from qagent.config import DEFAULT_DATA_DIR
from qagent.providers.tickflow_free import LEGACY_TICKFLOW_SOURCE_PROVIDERS


BAR_TABLE = "market_bar_cache"
COVERAGE_TABLE = "market_data_cache_spans"
REPAIR_VERSION = "tickflow-shanghai-date-v1"
REQUIRED_BAR_COLUMNS = {
    "provider_mode",
    "instrument_id",
    "trade_date",
    "source_provider",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "cached_at",
    "updated_at",
}
REQUIRED_COVERAGE_COLUMNS = {
    "provider_mode",
    "instrument_id",
    "start_date",
    "end_date",
}
BAR_COLUMNS = (
    "provider_mode",
    "instrument_id",
    "trade_date",
    "source_provider",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "cached_at",
    "updated_at",
    "adjusted_close",
    "adjustment_factor",
    "adjustment_type",
    "turnover",
    "adjusted_open",
    "adjusted_high",
    "adjusted_low",
)


@dataclass(frozen=True)
class TickFlowCacheRepairReport:
    database: str
    provider_mode: str
    dry_run: bool
    affected_rows: int
    affected_instruments: int
    target_conflicts: int
    non_tickflow_target_conflicts: int
    legacy_tickflow_target_conflicts: int
    rows_moved: int
    rows_discarded_for_preferred_target: int
    coverage_spans_invalidated: int
    backup_path: str | None = None


def repair_tickflow_cache_dates(
    database: Path | str,
    *,
    provider_mode: str = "free",
    execute: bool = False,
    backup_path: Path | str | None = None,
) -> TickFlowCacheRepairReport:
    database_path = Path(database).expanduser().resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")
    normalized_mode = provider_mode.strip().lower()
    if not normalized_mode:
        raise ValueError("provider_mode must not be empty")

    with _connect(database_path, query_only=not execute) as connection:
        _validate_schema(connection)
        preview = _repair_counts(connection, normalized_mode)

    if not execute or preview["affected_rows"] == 0:
        return _report(
            database_path,
            normalized_mode,
            dry_run=not execute,
            counts=preview,
            backup_path=None,
        )

    resolved_backup = (
        Path(backup_path).expanduser().resolve()
        if backup_path is not None
        else _default_backup_path(database_path)
    )
    _backup_affected_rows(
        database_path,
        resolved_backup,
        provider_mode=normalized_mode,
        expected_counts=preview,
    )

    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            _validate_schema(connection)
            counts = _repair_counts(connection, normalized_mode)
            if any(
                counts[key] != preview[key]
                for key in ("affected_rows", "affected_instruments")
            ):
                raise RuntimeError(
                    "repair candidates changed after sidecar backup; no rows were modified"
                )
            if counts["affected_rows"]:
                _create_repair_plan(connection, normalized_mode)
                coverage_invalidated = connection.execute(
                    f"""
                    DELETE FROM {COVERAGE_TABLE}
                    WHERE EXISTS (
                        SELECT 1
                        FROM tickflow_date_repair_plan AS plan
                        WHERE plan.provider_mode = {COVERAGE_TABLE}.provider_mode
                          AND plan.instrument_id = {COVERAGE_TABLE}.instrument_id
                    )
                    """
                ).rowcount
                connection.execute(
                    f"""
                    DELETE FROM {BAR_TABLE}
                    WHERE EXISTS (
                        SELECT 1
                        FROM tickflow_date_repair_plan AS plan
                        WHERE plan.provider_mode = {BAR_TABLE}.provider_mode
                          AND plan.instrument_id = {BAR_TABLE}.instrument_id
                          AND plan.original_trade_date = {BAR_TABLE}.trade_date
                    )
                    """
                )
                rows_moved = _insert_repaired_rows(connection)
                counts["coverage_spans_invalidated"] = coverage_invalidated
                counts["rows_moved"] = rows_moved
                counts["rows_discarded_for_preferred_target"] = (
                    counts["affected_rows"] - rows_moved
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return _report(
        database_path,
        normalized_mode,
        dry_run=False,
        counts=counts,
        backup_path=resolved_backup,
    )


def _connect(database: Path, *, query_only: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if query_only:
        connection.execute("PRAGMA query_only = ON")
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    bar_columns = _table_columns(connection, BAR_TABLE)
    coverage_columns = _table_columns(connection, COVERAGE_TABLE)
    missing_bars = sorted(REQUIRED_BAR_COLUMNS - bar_columns)
    missing_coverage = sorted(REQUIRED_COVERAGE_COLUMNS - coverage_columns)
    if missing_bars or missing_coverage:
        details = []
        if missing_bars:
            details.append(f"{BAR_TABLE} missing {','.join(missing_bars)}")
        if missing_coverage:
            details.append(f"{COVERAGE_TABLE} missing {','.join(missing_coverage)}")
        raise ValueError("incompatible market cache schema: " + "; ".join(details))


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _repair_counts(connection: sqlite3.Connection, provider_mode: str) -> dict[str, int]:
    legacy_sources = tuple(LEGACY_TICKFLOW_SOURCE_PROVIDERS)
    placeholders = ",".join("?" for _ in legacy_sources)
    row = connection.execute(
        f"""
        SELECT
            COUNT(*) AS affected_rows,
            COUNT(DISTINCT source.instrument_id) AS affected_instruments,
            SUM(CASE WHEN target.source_provider IS NOT NULL THEN 1 ELSE 0 END)
                AS target_conflicts,
            SUM(CASE
                WHEN target.source_provider IS NOT NULL
                 AND target.source_provider NOT IN ({placeholders}) THEN 1 ELSE 0 END)
                AS non_tickflow_target_conflicts,
            SUM(CASE
                WHEN target.source_provider IN ({placeholders}) THEN 1 ELSE 0 END)
                AS legacy_tickflow_target_conflicts
        FROM {BAR_TABLE} AS source
        LEFT JOIN {BAR_TABLE} AS target
          ON target.provider_mode = source.provider_mode
         AND target.instrument_id = source.instrument_id
         AND target.trade_date = date(source.trade_date, '+1 day')
        WHERE source.provider_mode = ?
          AND source.source_provider IN ({placeholders})
        """,
        (*legacy_sources, *legacy_sources, provider_mode, *legacy_sources),
    ).fetchone()
    affected = int(row["affected_rows"] or 0)
    non_tickflow_conflicts = int(row["non_tickflow_target_conflicts"] or 0)
    return {
        "affected_rows": affected,
        "affected_instruments": int(row["affected_instruments"] or 0),
        "target_conflicts": int(row["target_conflicts"] or 0),
        "non_tickflow_target_conflicts": non_tickflow_conflicts,
        "legacy_tickflow_target_conflicts": int(
            row["legacy_tickflow_target_conflicts"] or 0
        ),
        "rows_moved": affected - non_tickflow_conflicts,
        "rows_discarded_for_preferred_target": non_tickflow_conflicts,
        "coverage_spans_invalidated": 0,
    }


def _create_repair_plan(connection: sqlite3.Connection, provider_mode: str) -> None:
    legacy_sources = tuple(LEGACY_TICKFLOW_SOURCE_PROVIDERS)
    placeholders = ",".join("?" for _ in legacy_sources)
    source_case = " ".join(
        "WHEN source_provider = ? THEN ?"
        for _ in LEGACY_TICKFLOW_SOURCE_PROVIDERS.items()
    )
    source_parameters = tuple(
        value
        for pair in LEGACY_TICKFLOW_SOURCE_PROVIDERS.items()
        for value in pair
    )
    connection.execute("DROP TABLE IF EXISTS temp.tickflow_date_repair_plan")
    connection.execute(
        f"""
        CREATE TEMP TABLE tickflow_date_repair_plan AS
        SELECT
            provider_mode,
            instrument_id,
            trade_date AS original_trade_date,
            date(trade_date, '+1 day') AS trade_date,
            CASE {source_case} ELSE source_provider END AS source_provider,
            open,
            high,
            low,
            close,
            volume,
            cached_at,
            updated_at,
            adjusted_close,
            adjustment_factor,
            adjustment_type,
            turnover,
            adjusted_open,
            adjusted_high,
            adjusted_low
        FROM {BAR_TABLE}
        WHERE provider_mode = ?
          AND source_provider IN ({placeholders})
        """,
        (*source_parameters, provider_mode, *legacy_sources),
    )
    connection.execute(
        """
        CREATE INDEX tickflow_date_repair_plan_instrument
        ON tickflow_date_repair_plan (provider_mode, instrument_id)
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX tickflow_date_repair_plan_original_row
        ON tickflow_date_repair_plan (
            provider_mode,
            instrument_id,
            original_trade_date
        )
        """
    )


def _insert_repaired_rows(connection: sqlite3.Connection) -> int:
    columns = ",".join(BAR_COLUMNS)
    repaired_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    select_columns = ",".join(
        "? AS updated_at" if column == "updated_at" else column for column in BAR_COLUMNS
    )
    return connection.execute(
        f"""
        INSERT OR IGNORE INTO {BAR_TABLE} ({columns})
        SELECT {select_columns}
        FROM tickflow_date_repair_plan
        ORDER BY instrument_id, trade_date
        """,
        (repaired_at,),
    ).rowcount


def _backup_affected_rows(
    database: Path,
    backup: Path,
    *,
    provider_mode: str,
    expected_counts: dict[str, int],
) -> None:
    if backup == database:
        raise ValueError("backup path must differ from database path")
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    if not backup.parent.is_dir():
        raise FileNotFoundError(f"backup directory not found: {backup.parent}")
    legacy_sources = tuple(LEGACY_TICKFLOW_SOURCE_PROVIDERS)
    placeholders = ",".join("?" for _ in legacy_sources)
    created_at = datetime.now(timezone.utc).isoformat()
    restore_note = (
        "Restore market_bar_cache_backup before rebuilding coverage; "
        "market_data_cache_spans_backup contains the invalidated pre-repair spans."
    )
    try:
        with _connect(database, query_only=False) as connection:
            _validate_schema(connection)
            connection.execute("ATTACH DATABASE ? AS repair_backup", (str(backup),))
            try:
                try:
                    connection.execute(
                        """
                        CREATE TABLE repair_backup.tickflow_cache_repair_metadata (
                            repair_version TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            source_database TEXT NOT NULL,
                            provider_mode TEXT NOT NULL,
                            affected_rows INTEGER NOT NULL,
                            affected_instruments INTEGER NOT NULL,
                            restore_note TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        f"""
                        CREATE TABLE repair_backup.market_bar_cache_backup AS
                        SELECT *
                        FROM main.{BAR_TABLE}
                        WHERE provider_mode = ?
                          AND source_provider IN ({placeholders})
                        """,
                        (provider_mode, *legacy_sources),
                    )
                    connection.execute(
                        f"""
                        CREATE TABLE repair_backup.market_data_cache_spans_backup AS
                        SELECT span.*
                        FROM main.{COVERAGE_TABLE} AS span
                        WHERE span.provider_mode = ?
                          AND EXISTS (
                              SELECT 1
                              FROM main.{BAR_TABLE} AS bar
                              WHERE bar.provider_mode = span.provider_mode
                                AND bar.instrument_id = span.instrument_id
                                AND bar.source_provider IN ({placeholders})
                          )
                        """,
                        (provider_mode, *legacy_sources),
                    )
                    backup_rows = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM repair_backup.market_bar_cache_backup"
                        ).fetchone()[0]
                    )
                    backup_instruments = int(
                        connection.execute(
                            """
                            SELECT COUNT(DISTINCT instrument_id)
                            FROM repair_backup.market_bar_cache_backup
                            """
                        ).fetchone()[0]
                    )
                    if (
                        backup_rows != expected_counts["affected_rows"]
                        or backup_instruments != expected_counts["affected_instruments"]
                    ):
                        raise RuntimeError(
                            "backup candidate counts changed before execution: "
                            f"expected rows={expected_counts['affected_rows']} "
                            f"instruments={expected_counts['affected_instruments']}, "
                            f"backed up rows={backup_rows} instruments={backup_instruments}"
                        )
                    connection.execute(
                        """
                        INSERT INTO repair_backup.tickflow_cache_repair_metadata
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            REPAIR_VERSION,
                            created_at,
                            str(database),
                            provider_mode,
                            backup_rows,
                            backup_instruments,
                            restore_note,
                        ),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            finally:
                connection.execute("DETACH DATABASE repair_backup")
    except Exception:
        backup.unlink(missing_ok=True)
        raise


def _default_backup_path(database: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return database.with_name(
        f"{database.name}.tickflow-date-repair-{timestamp}.sidecar.sqlite3"
    )


def _report(
    database: Path,
    provider_mode: str,
    *,
    dry_run: bool,
    counts: dict[str, int],
    backup_path: Path | None,
) -> TickFlowCacheRepairReport:
    return TickFlowCacheRepairReport(
        database=str(database),
        provider_mode=provider_mode,
        dry_run=dry_run,
        affected_rows=counts["affected_rows"],
        affected_instruments=counts["affected_instruments"],
        target_conflicts=counts["target_conflicts"],
        non_tickflow_target_conflicts=counts["non_tickflow_target_conflicts"],
        legacy_tickflow_target_conflicts=counts["legacy_tickflow_target_conflicts"],
        rows_moved=counts["rows_moved"],
        rows_discarded_for_preferred_target=counts[
            "rows_discarded_for_preferred_target"
        ],
        coverage_spans_invalidated=counts["coverage_spans_invalidated"],
        backup_path=str(backup_path) if backup_path is not None else None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair legacy TickFlow market_bar_cache dates (dry-run by default)."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATA_DIR / "qagent.db",
        help="SQLite database path (default: Qagent data/qagent.db)",
    )
    parser.add_argument("--provider-mode", default="free")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Create a scoped sidecar backup and apply the repair. Otherwise only report counts.",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="Explicit scoped sidecar path. The path must not already exist.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = repair_tickflow_cache_dates(
        args.database,
        provider_mode=args.provider_mode,
        execute=args.execute,
        backup_path=args.backup,
    )
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
