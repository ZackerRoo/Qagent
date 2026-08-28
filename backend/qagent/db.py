from contextlib import contextmanager
from collections.abc import Generator
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock

from sqlalchemy import BigInteger, DateTime, Numeric, create_engine
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Dialect
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool
from sqlalchemy.types import TypeDecorator

from qagent.config import get_settings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Persist aware datetimes as UTC and restore aware UTC values on reads."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        return dialect.type_descriptor(DateTime(timezone=dialect.name != "sqlite"))

    def process_bind_param(self, value: datetime | None, dialect: Dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        normalized = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(self, value: datetime | None, _dialect: Dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class SQLiteScaledDecimal(TypeDecorator[Decimal]):
    """Use scaled integers on SQLite and native fixed precision elsewhere."""

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int, scale: int):
        self.precision = precision
        self.scale = scale
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(BigInteger())
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value: Decimal | int | str | None, dialect: Dialect):
        if value is None:
            return None
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if not decimal_value.is_finite():
            raise ValueError("SQLiteScaledDecimal requires a finite value")
        scaled_value = decimal_value.scaleb(self.scale)
        if scaled_value != scaled_value.to_integral_value():
            raise ValueError(f"decimal value exceeds scale {self.scale}")
        if dialect.name == "sqlite":
            integer_value = int(scaled_value)
            if not -(2**63) <= integer_value <= 2**63 - 1:
                raise OverflowError("scaled decimal exceeds signed 64-bit SQLite range")
            return integer_value
        return decimal_value

    def process_result_value(self, value, dialect: Dialect):
        if value is None:
            return None
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if dialect.name == "sqlite":
            return decimal_value.scaleb(-self.scale)
        return decimal_value


_schema_lock = Lock()
_initialized_urls: set[str] = set()
_default_engine_lock = Lock()
_default_engine: Engine | None = None
_default_engine_url: str | None = None
_RANKING_V3_FORWARD_TRIGGER_VERSION = 2
_RANKING_V3_PRODUCTION_TRIGGER_VERSION = 2
_RANKING_V4_EVIDENCE_TRIGGER_VERSION = 4
_RUNTIME_SQLITE_POOL_SIZE = 8
_RUNTIME_SQLITE_MAX_OVERFLOW = 4
_RUNTIME_SQLITE_POOL_TIMEOUT_SECONDS = 5


def create_db_engine(database_url: str | None = None):
    settings = get_settings()
    url = database_url or settings.database_url
    if database_url is None:
        return _shared_default_engine(url)
    return _build_db_engine(url)


def _shared_default_engine(url: str) -> Engine:
    """Reuse the runtime engine so request-local repositories do not churn engines."""

    global _default_engine, _default_engine_url
    with _default_engine_lock:
        if _default_engine is not None and _default_engine_url == url:
            return _default_engine
        if _default_engine is not None:
            _default_engine.dispose()
        _default_engine = _build_db_engine(url, runtime_pool=True)
        _default_engine_url = url
        return _default_engine


def _build_db_engine(url: str, *, runtime_pool: bool = False) -> Engine:
    parsed = make_url(url)
    is_file_sqlite = parsed.drivername.startswith("sqlite") and parsed.database not in (
        None,
        "",
        ":memory:",
    )
    engine_kwargs = {}
    if is_file_sqlite:
        Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if runtime_pool:
            # Browser polling creates many short repository sessions. Reusing a
            # bounded set of SQLite connections avoids an open/close storm on
            # large local databases while keeping writer concurrency bounded.
            engine_kwargs.update(
                {
                    "poolclass": QueuePool,
                    "pool_size": _RUNTIME_SQLITE_POOL_SIZE,
                    "max_overflow": _RUNTIME_SQLITE_MAX_OVERFLOW,
                    "pool_timeout": _RUNTIME_SQLITE_POOL_TIMEOUT_SECONDS,
                    "pool_use_lifo": True,
                    "pool_pre_ping": True,
                }
            )
        else:
            # Explicit URLs are used by isolated tests and worker jobs.
            engine_kwargs["poolclass"] = NullPool
    engine = create_engine(url, future=True, **engine_kwargs)
    if is_file_sqlite:
        _configure_sqlite_pragmas(engine)
    return engine


def _configure_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode")
            if str(cursor.fetchone()[0]).lower() != "wal":
                cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


def initialize_database(database_url: str | None = None):
    # Import table mappings before create_all so direct db initialization sees every table.
    from qagent.storage import tables as _tables  # noqa: F401

    url = database_url or get_settings().database_url
    engine = create_db_engine(database_url)
    with _schema_lock:
        if url not in _initialized_urls:
            if engine.dialect.name.startswith("sqlite"):
                _apply_additive_migrations(engine)
            else:
                Base.metadata.create_all(engine)
            _initialized_urls.add(url)
    return engine


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(database_url), expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session_factory = create_session_factory()
    with session_factory() as session:
        yield session


def _apply_additive_migrations(engine: Engine) -> None:
    if not engine.dialect.name.startswith("sqlite"):
        return
    with _exclusive_sqlite_migration(engine) as connection:
        Base.metadata.create_all(connection)
        inspector = inspect(connection)
        _drop_ranking_v3_forward_triggers(connection)
        _drop_ranking_v3_production_triggers(connection)
        _drop_ranking_v4_evidence_triggers(connection)
        _add_missing_columns(
            connection,
            inspector,
            "market_bar_cache",
            {
                "turnover": "NUMERIC(28, 4)",
                "adjusted_open": "NUMERIC(18, 6)",
                "adjusted_high": "NUMERIC(18, 6)",
                "adjusted_low": "NUMERIC(18, 6)",
                "adjusted_close": "NUMERIC(18, 6)",
                "adjustment_factor": "NUMERIC(20, 10)",
                "adjustment_type": "VARCHAR(32)",
            },
        )
        _add_missing_columns(
            connection,
            inspector,
            "scan_runs",
            {
                "started_at": "DATETIME",
                "completed_at": "DATETIME",
            },
        )
        _add_missing_columns(
            connection,
            inspector,
            "paper_trades",
            {
                "allocation_multiplier": "NUMERIC(8, 4) NOT NULL DEFAULT 1.0",
                "admission_source": "VARCHAR(32) DEFAULT 'legacy_unknown'",
                "production_identity_digest": "VARCHAR(64)",
                "production_batch_fact_digest": "VARCHAR(64)",
                "production_selection_item_digest": "VARCHAR(64)",
                "release_proof_digest": "VARCHAR(64)",
            },
        )
        _add_missing_columns(
            connection,
            inspector,
            "automation_scheduler_state",
            {"revision": "INTEGER NOT NULL DEFAULT 0"},
        )
        _add_missing_columns(
            connection,
            inspector,
            "automation_cycles",
            {
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "retry_budget": "INTEGER NOT NULL DEFAULT 4",
                "next_retry_at": "DATETIME",
                "retry_backoff_seconds": "INTEGER",
                "last_error_fingerprint": "VARCHAR(64)",
                "last_error_text": "TEXT",
                "last_error_at": "DATETIME",
                "terminal_reason": "VARCHAR(64)",
            },
        )
        _add_missing_columns(
            connection,
            inspector,
            "automation_cycle_stages",
            {
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "retry_scope": "VARCHAR(160)",
                "next_retry_at": "DATETIME",
                "retry_backoff_seconds": "INTEGER",
                "last_error_fingerprint": "VARCHAR(64)",
                "last_error_kind": "VARCHAR(64)",
                "last_error_retryable": "BOOLEAN",
                "last_error_at": "DATETIME",
            },
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_automation_cycle_stages_retry_scope "
                "ON automation_cycle_stages(retry_scope)"
            )
        )
        _add_missing_columns(
            connection,
            inspector,
            "automation_circuit_breakers",
            {"probe_expires_at": "DATETIME"},
        )
        _add_missing_columns(
            connection,
            inspector,
            "delivery_outbox",
            {
                "idempotency_key": "VARCHAR(160)",
                "payload_digest": "VARCHAR(64)",
            },
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_delivery_outbox_idempotency_key "
                "ON delivery_outbox(idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
        )
        _record_automation_runtime_migration_audit(connection)
        _add_missing_columns(
            connection,
            inspector,
            "ranking_v3_production_selections",
            {
                "source_rank_score": "NUMERIC(8, 4)",
                "trigger_price": "NUMERIC(18, 4)",
                "initial_stop": "NUMERIC(18, 4)",
                "target_1": "NUMERIC(18, 4)",
                "allocation_multiplier": "NUMERIC(8, 4)",
            },
        )
        _backfill_paper_trade_admission_source(connection)
        _add_missing_columns(
            connection,
            inspector,
            "walk_forward_jobs",
            {
                "lease_maintenance_count": "INTEGER NOT NULL DEFAULT 0",
                "lease_recovery_count": "INTEGER NOT NULL DEFAULT 0",
                "last_lease_heartbeat_at": "DATETIME",
            },
        )
        _add_missing_columns(
            connection,
            inspector,
            "fuyao_shadow_outcomes",
            {
                "round_trip_cost_bps": "BIGINT NOT NULL DEFAULT 200000",
            },
        )
        added_event_columns = _add_missing_columns(
            connection,
            inspector,
            "paper_trade_events",
            {
                "instrument_id": "VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN'",
            },
        )
        if "instrument_id" in added_event_columns:
            _backfill_paper_trade_event_instrument_ids(connection)
        _add_missing_columns(
            connection,
            inspector,
            "historical_trading_rules",
            {
                "minimum_order_quantity": "INTEGER NOT NULL DEFAULT 100",
                "quantity_step": "INTEGER NOT NULL DEFAULT 100",
            },
        )
        _add_missing_columns(
            connection,
            inspector,
            "historical_instrument_rule_metadata",
            {
                "board_lot": "INTEGER NOT NULL DEFAULT 100",
                "minimum_order_quantity": "INTEGER NOT NULL DEFAULT 100",
                "quantity_step": "INTEGER NOT NULL DEFAULT 100",
            },
        )
        for table_name in (
            "fundamental_snapshots",
            "historical_tradability",
            "historical_instrument_profiles",
            "historical_industry_snapshots",
            "historical_index_snapshots",
            "historical_index_memberships",
            "historical_replay_bars",
            "historical_corporate_actions",
            "historical_corporate_action_coverage",
        ):
            added_columns = _add_missing_columns(
                connection,
                inspector,
                table_name,
                {"dataset_revision": "INTEGER NOT NULL DEFAULT 0"},
            )
            if "dataset_revision" in added_columns:
                _backfill_dataset_revision(connection, table_name)
        for table_name in (
            "historical_universe_manifests",
            "historical_replay_universe_members",
        ):
            _add_missing_columns(
                connection,
                inspector,
                table_name,
                {"owner_run_id": ("VARCHAR(64) NOT NULL DEFAULT 'legacy-unknown-owner'")},
            )
        _add_strategy_governance_columns(connection, inspector)
        _migrate_ranking_v3_forward_integrity(connection, inspector)
        _rebuild_revision_scoped_tables(connection)
        _rebuild_ranking_v3_forward_gate_evidence_kind(connection)
        _repair_legacy_scoped_index_snapshot_counts(connection)
        _repair_factor_research_model_digest_index(connection)
        _create_missing_metadata_indexes(connection)
        _drop_obsolete_walk_forward_indexes(connection)
        _create_strategy_governance_indexes(connection)
        _create_ranking_v3_production_indexes(connection)
        _create_immutable_strategy_governance_triggers(connection)
        _create_immutable_ranking_v3_forward_triggers(connection)
        _create_ranking_v3_forward_source_snapshot_triggers(connection)
        _record_ranking_v3_forward_trigger_version(connection)
        _create_immutable_ranking_v3_production_triggers(connection)
        _record_ranking_v3_production_trigger_version(connection)
        _create_immutable_ranking_v4_evidence_triggers(connection)
        _record_ranking_v4_evidence_trigger_version(connection)
        _create_immutable_paper_research_baseline_triggers(connection)
        _create_immutable_row_triggers(connection, "factor_research_model_artifacts")
        _create_immutable_row_triggers(connection, "factor_shadow_scores")
        _create_immutable_row_triggers(connection, "factor_shadow_outcomes")
        _create_immutable_row_triggers(connection, "fuyao_research_snapshots")
        _create_immutable_row_triggers(connection, "fuyao_shadow_outcomes")


def _record_automation_runtime_migration_audit(connection) -> None:
    duplicate_rows = connection.execute(
        text(
            "SELECT provider, instrument_id, COUNT(*) AS duplicate_count "
            "FROM paper_trades WHERE status IN ('pending', 'open') "
            "GROUP BY provider, instrument_id HAVING COUNT(*) > 1"
        )
    ).mappings().all()
    legacy_outbox = connection.execute(
        text("SELECT COUNT(*) FROM delivery_outbox WHERE idempotency_key IS NULL")
    ).scalar_one()
    payload = json.dumps(
        {
            "active_paper_duplicates": [dict(row) for row in duplicate_rows],
            "active_paper_duplicate_groups": len(duplicate_rows),
            "legacy_outbox_without_idempotency": int(legacy_outbox),
            "action": "audit_only_no_historical_deletion",
        },
        sort_keys=True,
    )
    connection.execute(
        text(
            "INSERT INTO automation_migration_audits(audit_key, payload_json, created_at) "
            "VALUES ('automation-runtime-v1', :payload, CURRENT_TIMESTAMP) "
            "ON CONFLICT(audit_key) DO UPDATE SET payload_json = excluded.payload_json"
        ),
        {"payload": payload},
    )


@contextmanager
def _exclusive_sqlite_migration(engine: Engine):
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN EXCLUSIVE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()


def _sqlite_table_columns(connection, table_name: str) -> set[str]:
    quoted_table = connection.dialect.identifier_preparer.quote(table_name)
    return {str(row[1]) for row in connection.exec_driver_sql(f"PRAGMA table_info({quoted_table})")}


def _add_missing_columns(connection, _inspector, table_name: str, additions) -> set[str]:
    if not inspect(connection).has_table(table_name):
        return set()
    added = set()
    for column, sql_type in additions.items():
        if column in _sqlite_table_columns(connection, table_name):
            continue
        quoted_table = connection.dialect.identifier_preparer.quote(table_name)
        quoted_column = connection.dialect.identifier_preparer.quote(column)
        try:
            connection.execute(
                text(f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} {sql_type}")
            )
        except OperationalError as exc:
            if "duplicate column name" not in str(
                exc
            ).lower() or column not in _sqlite_table_columns(connection, table_name):
                raise
        added.add(column)
    return added


def _backfill_dataset_revision(connection, table_name: str) -> None:
    if not inspect(connection).has_table(table_name):
        return
    connection.execute(
        text(
            f"UPDATE {table_name} SET dataset_revision = COALESCE(("
            "SELECT revision FROM historical_data_revisions "
            f"WHERE historical_data_revisions.provider_mode = {table_name}.provider_mode"
            "), 0) WHERE dataset_revision = 0"
        )
    )


def _create_immutable_paper_research_baseline_triggers(connection) -> None:
    table_name = "paper_research_baselines"
    if not inspect(connection).has_table(table_name):
        return
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS trg_paper_research_baselines_immutable_update "
        f"BEFORE UPDATE ON {table_name} BEGIN "
        "SELECT RAISE(ABORT, 'paper research baselines are immutable'); END"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS trg_paper_research_baselines_immutable_delete "
        f"BEFORE DELETE ON {table_name} BEGIN "
        "SELECT RAISE(ABORT, 'paper research baselines are immutable'); END"
    )


def _backfill_paper_trade_event_instrument_ids(connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("paper_trade_events") or not inspector.has_table("paper_trades"):
        return
    connection.execute(
        text(
            "UPDATE paper_trade_events SET instrument_id = COALESCE(("
            "SELECT paper_trades.instrument_id FROM paper_trades "
            "WHERE paper_trades.trade_id = paper_trade_events.trade_id"
            "), 'UNKNOWN') WHERE instrument_id IS NULL OR instrument_id = 'UNKNOWN'"
        )
    )


def _backfill_paper_trade_admission_source(connection) -> None:
    if not inspect(connection).has_table("paper_trades"):
        return
    if "admission_source" not in _sqlite_table_columns(connection, "paper_trades"):
        return
    connection.execute(
        text(
            "UPDATE paper_trades SET admission_source = 'legacy_unknown' "
            "WHERE admission_source IS NULL OR trim(admission_source) = ''"
        )
    )


def _migrate_ranking_v3_forward_integrity(connection, inspector) -> None:
    candidate_columns = _add_missing_columns(
        connection,
        inspector,
        "ranking_v3_forward_candidates",
        {
            "source_snapshot_id": "VARCHAR(192) NOT NULL DEFAULT ''",
            "integrity_status": "VARCHAR(32) NOT NULL DEFAULT 'verified'",
            "quarantine_reason": "TEXT NOT NULL DEFAULT ''",
        },
    )
    _add_missing_columns(
        connection,
        inspector,
        "ranking_v3_forward_ledgers",
        {
            "integrity_status": "VARCHAR(32) NOT NULL DEFAULT 'verified'",
            "quarantine_reason": "TEXT NOT NULL DEFAULT ''",
        },
    )
    if not (
        inspect(connection).has_table("ranking_v3_forward_candidates")
        and inspect(connection).has_table("ranking_v3_forward_ledgers")
    ):
        return

    reason = "legacy/quarantined: unverifiable Ranking V3 candidate source facts"
    connection.execute(
        text(
            "UPDATE ranking_v3_forward_candidates "
            "SET integrity_status = 'legacy_quarantined', quarantine_reason = :reason "
            "WHERE integrity_status = 'verified' AND ("
            "source_snapshot_id IS NULL OR trim(source_snapshot_id) = '' "
            "OR fact_digest IS NULL OR length(trim(fact_digest)) <> 64 "
            "OR selection_digest IS NULL OR length(trim(selection_digest)) <> 64"
            ")"
        ),
        {"reason": reason},
    )
    connection.execute(
        text(
            "UPDATE ranking_v3_forward_ledgers "
            "SET integrity_status = 'legacy_quarantined', "
            "quarantine_reason = :reason, status = 'rejected', "
            "rejection_reasons_json = :reasons, "
            "current_release_proof_digest = NULL, revision = revision + 1, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE integrity_status = 'verified' AND EXISTS ("
            "SELECT 1 FROM ranking_v3_forward_candidates AS candidates "
            "WHERE candidates.protocol_id = ranking_v3_forward_ledgers.protocol_id "
            "AND candidates.protocol_digest = ranking_v3_forward_ledgers.protocol_digest "
            "AND candidates.model_version = ranking_v3_forward_ledgers.model_version "
            "AND candidates.integrity_status = 'legacy_quarantined'"
            ")"
        ),
        {
            "reason": reason,
            "reasons": '["legacy/quarantined: unverifiable Ranking V3 candidate source facts"]',
        },
    )
    if "source_snapshot_id" in candidate_columns:
        connection.execute(
            text(
                "UPDATE ranking_v3_forward_candidates "
                "SET quarantine_reason = :reason "
                "WHERE integrity_status = 'legacy_quarantined' "
                "AND (quarantine_reason IS NULL OR quarantine_reason = '')"
            ),
            {"reason": reason},
        )


def _add_strategy_governance_columns(connection, inspector) -> None:
    _add_missing_columns(
        connection,
        inspector,
        "strategy_versions",
        {
            "definition_digest": "VARCHAR(64) NOT NULL DEFAULT ''",
            "definition_json": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
    )
    _add_missing_columns(
        connection,
        inspector,
        "policy_deployments",
        {
            "strategy_version": "VARCHAR(96) NOT NULL DEFAULT 'legacy'",
            "factor_version": "VARCHAR(96) NOT NULL DEFAULT 'legacy'",
            "parameter_version": "VARCHAR(96) NOT NULL DEFAULT 'legacy'",
            "universe_version": "VARCHAR(96) NOT NULL DEFAULT 'legacy'",
            "data_revision": "VARCHAR(128) NOT NULL DEFAULT 'unversioned'",
            "policy_digest": "VARCHAR(64) NOT NULL DEFAULT ''",
            "policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "previous_deployment_id": "VARCHAR(96)",
            "created_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
    )
    _add_missing_columns(
        connection,
        inspector,
        "strategy_states",
        {
            "current_deployment_id": "VARCHAR(96)",
            "previous_deployment_id": "VARCHAR(96)",
            "current_policy_version": "VARCHAR(96)",
            "previous_policy_version": "VARCHAR(96)",
            "effective_weight": "NUMERIC(12, 10) NOT NULL DEFAULT 0",
            "revision": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
            "updated_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
    )
    event_columns = _add_missing_columns(
        connection,
        inspector,
        "strategy_state_events",
        {
            "sequence": "INTEGER NOT NULL DEFAULT 0",
            "idempotency_key": "VARCHAR(160)",
            "event_type": "VARCHAR(64) NOT NULL DEFAULT 'legacy'",
            "action": "VARCHAR(64) NOT NULL DEFAULT 'legacy'",
            "deployment_id": "VARCHAR(96)",
            "previous_deployment_id": "VARCHAR(96)",
            "policy_version": "VARCHAR(96)",
            "effective_weight": "NUMERIC(12, 10) NOT NULL DEFAULT 0",
            "reason": "TEXT NOT NULL DEFAULT ''",
            "evidence_json": "TEXT NOT NULL DEFAULT '{}'",
            "decision_json": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
    )
    if "idempotency_key" in event_columns:
        connection.execute(
            text(
                "UPDATE strategy_state_events "
                "SET idempotency_key = 'legacy-governance-event-' || rowid "
                "WHERE idempotency_key IS NULL OR idempotency_key = ''"
            )
        )
    if "sequence" in event_columns:
        connection.execute(
            text(
                "UPDATE strategy_state_events AS current SET sequence = ("
                "SELECT COUNT(*) FROM strategy_state_events AS earlier "
                "WHERE earlier.strategy_id = current.strategy_id "
                "AND earlier.rowid <= current.rowid)"
            )
        )


def _create_strategy_governance_indexes(connection) -> None:
    inspector = inspect(connection)
    if inspector.has_table("policy_deployments"):
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_policy_deployments_strategy_policy_version "
                "ON policy_deployments (strategy_id, policy_version)"
            )
        )
    if inspector.has_table("strategy_state_events"):
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_strategy_state_events_idempotency_key "
                "ON strategy_state_events (idempotency_key)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_strategy_state_events_strategy_sequence "
                "ON strategy_state_events (strategy_id, sequence)"
            )
        )


def _create_ranking_v3_production_indexes(connection) -> None:
    inspector = inspect(connection)
    if inspector.has_table("paper_trades"):
        for column in (
            "production_identity_digest",
            "production_batch_fact_digest",
            "production_selection_item_digest",
            "release_proof_digest",
        ):
            if column not in _sqlite_table_columns(connection, "paper_trades"):
                continue
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_paper_trades_{column} "
                    f"ON paper_trades ({column})"
                )
            )


def _create_immutable_strategy_governance_triggers(connection) -> None:
    inspector = inspect(connection)
    for table_name in ("strategy_versions", "policy_deployments"):
        if not inspector.has_table(table_name):
            continue
        for operation in ("UPDATE", "DELETE"):
            trigger_name = f"trg_{table_name}_immutable_{operation.lower()}"
            connection.execute(
                text(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger_name} "
                    f"BEFORE {operation} ON {table_name} "
                    "BEGIN "
                    f"SELECT RAISE(ABORT, '{table_name} rows are immutable'); "
                    "END"
                )
            )


def _drop_ranking_v3_forward_triggers(connection) -> None:
    names = connection.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND ("
            "name LIKE 'trg_ranking_v3_forward_%' "
            "OR name LIKE 'trg_opportunity_snapshots_forward_reference_%'"
            ")"
        )
    ).scalars()
    for name in names:
        if not (
            name.startswith("trg_ranking_v3_forward_")
            or name.startswith("trg_opportunity_snapshots_forward_reference_")
        ):
            continue
        connection.execute(text(f'DROP TRIGGER IF EXISTS "{name}"'))


def _drop_ranking_v3_production_triggers(connection) -> None:
    names = connection.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND ("
            "name LIKE 'trg_ranking_v3_production_%' "
            "OR name LIKE 'trg_opportunity_snapshots_production_reference_%' "
            "OR name LIKE 'trg_scan_runs_production_reference_%' "
            "OR name LIKE 'trg_paper_trades_ranking_v3_production_%'"
            ")"
        )
    ).scalars()
    for name in names:
        if not (
            name.startswith("trg_ranking_v3_production_")
            or name.startswith("trg_opportunity_snapshots_production_reference_")
            or name.startswith("trg_scan_runs_production_reference_")
            or name.startswith("trg_paper_trades_ranking_v3_production_")
        ):
            continue
        connection.execute(text(f'DROP TRIGGER IF EXISTS "{name}"'))


def _drop_ranking_v4_evidence_triggers(connection) -> None:
    names = connection.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND name LIKE 'trg_ranking_v4_evidence_%'"
        )
    ).scalars()
    for name in names:
        if name.startswith("trg_ranking_v4_evidence_"):
            connection.execute(text(f'DROP TRIGGER IF EXISTS "{name}"'))


def _record_ranking_v3_forward_trigger_version(connection) -> None:
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS qagent_schema_components ("
            "component VARCHAR(96) PRIMARY KEY, "
            "version INTEGER NOT NULL, "
            "applied_at DATETIME NOT NULL"
            ")"
        )
    )
    connection.execute(
        text(
            "INSERT INTO qagent_schema_components (component, version, applied_at) "
            "VALUES ('ranking_v3_forward_triggers', :version, CURRENT_TIMESTAMP) "
            "ON CONFLICT(component) DO UPDATE SET "
            "version = excluded.version, applied_at = excluded.applied_at"
        ),
        {"version": _RANKING_V3_FORWARD_TRIGGER_VERSION},
    )


def _record_ranking_v3_production_trigger_version(connection) -> None:
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS qagent_schema_components ("
            "component VARCHAR(96) PRIMARY KEY, "
            "version INTEGER NOT NULL, "
            "applied_at DATETIME NOT NULL"
            ")"
        )
    )
    connection.execute(
        text(
            "INSERT INTO qagent_schema_components (component, version, applied_at) "
            "VALUES ('ranking_v3_production_triggers', :version, CURRENT_TIMESTAMP) "
            "ON CONFLICT(component) DO UPDATE SET "
            "version = excluded.version, applied_at = excluded.applied_at"
        ),
        {"version": _RANKING_V3_PRODUCTION_TRIGGER_VERSION},
    )


def _record_ranking_v4_evidence_trigger_version(connection) -> None:
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS qagent_schema_components ("
            "component VARCHAR(96) PRIMARY KEY, "
            "version INTEGER NOT NULL, "
            "applied_at DATETIME NOT NULL"
            ")"
        )
    )
    connection.execute(
        text(
            "INSERT INTO qagent_schema_components (component, version, applied_at) "
            "VALUES ('ranking_v4_evidence_triggers', :version, CURRENT_TIMESTAMP) "
            "ON CONFLICT(component) DO UPDATE SET "
            "version = excluded.version, applied_at = excluded.applied_at"
        ),
        {"version": _RANKING_V4_EVIDENCE_TRIGGER_VERSION},
    )


def _create_immutable_ranking_v4_evidence_triggers(connection) -> None:
    inspector = inspect(connection)
    tables = (
        "ranking_v4_evidence_definitions",
        "ranking_v4_evidence_inventories",
        "ranking_v4_evidence_returns",
        "ranking_v4_evidence_proofs",
        "ranking_v4_prospective_release_policies",
        "ranking_v4_prospective_execution_summaries",
        "ranking_v4_prospective_release_proofs",
    )
    for table_name in tables:
        if inspector.has_table(table_name):
            _create_immutable_row_triggers(connection, table_name)

    if inspector.has_table("ranking_v4_evidence_definitions"):
        connection.execute(
            text(
                "CREATE TRIGGER trg_ranking_v4_evidence_definitions_shape_insert "
                "BEFORE INSERT ON ranking_v4_evidence_definitions "
                "WHEN length(NEW.definition_digest) <> 64 "
                "OR length(NEW.protocol_digest) <> 64 "
                "OR length(NEW.code_revision) <> 40 "
                "OR length(NEW.experiment_registry_digest) <> 64 "
                "OR NEW.dataset_revision < 1 "
                "OR NEW.collection_mode <> 'prospective_only_no_backfill' "
                "OR NEW.release_scope <> 'shadow_only' "
                "OR date(NEW.evidence_start_date) <= date(NEW.frozen_at) "
                "OR json_valid(NEW.payload_json) <> 1 "
                "OR json_valid(NEW.attestation_json) <> 1 "
                "BEGIN "
                "SELECT RAISE(ABORT, 'Ranking V4 evidence definition is invalid'); "
                "END"
            )
        )

    if inspector.has_table("ranking_v4_evidence_inventories"):
        connection.execute(
            text(
                "CREATE TRIGGER trg_ranking_v4_evidence_inventories_chain_insert "
                "BEFORE INSERT ON ranking_v4_evidence_inventories "
                "WHEN NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_evidence_definitions AS definition "
                "WHERE definition.definition_digest = NEW.definition_digest"
                ") "
                "OR NEW.sequence <> COALESCE(("
                "SELECT MAX(existing.sequence) + 1 "
                "FROM ranking_v4_evidence_inventories AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest"
                "), 1) "
                "OR (NEW.sequence = 1 AND NEW.previous_inventory_digest IS NOT NULL) "
                "OR (NEW.sequence > 1 AND NEW.previous_inventory_digest IS NOT ("
                "SELECT existing.inventory_digest "
                "FROM ranking_v4_evidence_inventories AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest "
                "ORDER BY existing.sequence DESC LIMIT 1"
                ")) "
                "OR json_valid(NEW.payload_json) <> 1 "
                "OR json_valid(NEW.attestation_json) <> 1 "
                "BEGIN "
                "SELECT RAISE(ABORT, 'Ranking V4 inventory chain is invalid'); "
                "END"
            )
        )

    if inspector.has_table("ranking_v4_evidence_returns"):
        connection.execute(
            text(
                "CREATE TRIGGER trg_ranking_v4_evidence_returns_chain_insert "
                "BEFORE INSERT ON ranking_v4_evidence_returns "
                "WHEN NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_evidence_definitions AS definition "
                "WHERE definition.definition_digest = NEW.definition_digest "
                "AND definition.dataset_revision <= NEW.dataset_revision "
                "AND date(NEW.rebalance_date) >= date(definition.evidence_start_date)"
                ") "
                "OR NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_evidence_inventories AS inventory "
                "WHERE inventory.definition_digest = NEW.definition_digest"
                ") "
                "OR NEW.sequence <> COALESCE(("
                "SELECT MAX(existing.sequence) + 1 "
                "FROM ranking_v4_evidence_returns AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest"
                "), 1) "
                "OR (NEW.sequence = 1 AND NEW.previous_record_digest IS NOT NULL) "
                "OR (NEW.sequence > 1 AND NEW.previous_record_digest IS NOT ("
                "SELECT existing.record_digest "
                "FROM ranking_v4_evidence_returns AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest "
                "ORDER BY existing.sequence DESC LIMIT 1"
                ")) "
                "OR EXISTS ("
                "SELECT 1 FROM ranking_v4_evidence_returns AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest "
                "AND date(existing.rebalance_date) >= date(NEW.rebalance_date)"
                ") "
                "OR EXISTS ("
                "SELECT 1 FROM ranking_v4_evidence_returns AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest "
                "AND existing.dataset_revision > NEW.dataset_revision"
                ") "
                "OR NEW.model_count < 1 "
                "OR json_valid(NEW.payload_json) <> 1 "
                "OR COALESCE(length(json_extract("
                "NEW.payload_json, '$.source_result_digest'"
                ")), 0) < 1 "
                "OR COALESCE(json_array_length(json_extract("
                "NEW.payload_json, '$.model_returns'"
                ")), -1) "
                "<> NEW.model_count "
                "OR json_valid(NEW.attestation_json) <> 1 "
                "BEGIN "
                "SELECT RAISE(ABORT, 'Ranking V4 return chain is invalid'); "
                "END"
            )
        )

    if inspector.has_table("ranking_v4_evidence_proofs"):
        connection.execute(
            text(
                "CREATE TRIGGER trg_ranking_v4_evidence_proofs_shape_insert "
                "BEFORE INSERT ON ranking_v4_evidence_proofs "
                "WHEN NEW.release_scope <> 'shadow_only' "
                "OR NEW.official_release_allowed <> 0 "
                "OR NEW.return_record_count <> ("
                "SELECT COUNT(*) FROM ranking_v4_evidence_returns AS evidence_return "
                "WHERE evidence_return.definition_digest = NEW.definition_digest"
                ") "
                "OR NEW.inventory_digest IS NOT ("
                "SELECT inventory.inventory_digest "
                "FROM ranking_v4_evidence_inventories AS inventory "
                "WHERE inventory.definition_digest = NEW.definition_digest "
                "ORDER BY inventory.sequence DESC LIMIT 1"
                ") "
                "OR json_valid(NEW.payload_json) <> 1 "
                "OR json_valid(NEW.attestation_json) <> 1 "
                "BEGIN "
                "SELECT RAISE(ABORT, 'Ranking V4 evidence proof is invalid'); "
                "END"
            )
        )

    if inspector.has_table("ranking_v4_prospective_release_policies"):
        connection.execute(
            text(
                "CREATE TRIGGER trg_ranking_v4_evidence_release_policies_shape_insert "
                "BEFORE INSERT ON ranking_v4_prospective_release_policies "
                "WHEN length(NEW.policy_digest) <> 64 "
                "OR length(NEW.model_protocol_digest) <> 64 "
                "OR length(NEW.experiment_registry_digest) <> 64 "
                "OR length(NEW.preregistration_commit) <> 40 "
                "OR length(NEW.preregistration_document_sha256) <> 64 "
                "OR NEW.maximum_checkpoint_common_date_count <> 112 "
                "OR NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_evidence_definitions AS definition "
                "WHERE definition.definition_digest = NEW.definition_digest "
                "AND definition.protocol_digest = NEW.model_protocol_digest "
                "AND definition.experiment_registry_digest = "
                "NEW.experiment_registry_digest "
                "AND datetime(NEW.registered_at) >= datetime(definition.frozen_at)"
                ") "
                "OR json_valid(NEW.payload_json) <> 1 "
                "OR json_valid(NEW.attestation_json) <> 1 "
                "BEGIN "
                "SELECT RAISE(ABORT, 'Ranking V4 prospective release policy is invalid'); "
                "END"
            )
        )

    if inspector.has_table("ranking_v4_prospective_execution_summaries"):
        connection.execute(
            text(
                "CREATE TRIGGER "
                "trg_ranking_v4_evidence_execution_summaries_chain_insert "
                "BEFORE INSERT ON ranking_v4_prospective_execution_summaries "
                "WHEN length(NEW.summary_digest) <> 64 "
                "OR NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_prospective_release_policies AS policy "
                "JOIN ranking_v4_evidence_definitions AS definition "
                "ON definition.definition_digest = policy.definition_digest "
                "WHERE policy.policy_digest = NEW.policy_digest "
                "AND policy.definition_digest = NEW.definition_digest "
                "AND definition.dataset_revision <= NEW.dataset_revision "
                "AND date(NEW.execution_start_date) = "
                "date(definition.evidence_start_date)"
                ") "
                "OR date(NEW.execution_end_date) < date(NEW.execution_start_date) "
                "OR date(NEW.latest_mature_rebalance_date) "
                "NOT BETWEEN date(NEW.execution_start_date) "
                "AND date(NEW.execution_end_date) "
                "OR NEW.sequence <> COALESCE(("
                "SELECT MAX(existing.sequence) + 1 "
                "FROM ranking_v4_prospective_execution_summaries AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest"
                "), 1) "
                "OR (NEW.sequence = 1 AND NEW.previous_summary_digest IS NOT NULL) "
                "OR (NEW.sequence > 1 AND NEW.previous_summary_digest IS NOT ("
                "SELECT existing.summary_digest "
                "FROM ranking_v4_prospective_execution_summaries AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest "
                "ORDER BY existing.sequence DESC LIMIT 1"
                ")) "
                "OR NEW.common_date_count <> ("
                "SELECT COUNT(*) FROM ranking_v4_evidence_returns AS evidence_return "
                "WHERE evidence_return.definition_digest = NEW.definition_digest"
                ") "
                "OR date(NEW.latest_mature_rebalance_date) IS NOT ("
                "SELECT MAX(evidence_return.rebalance_date) "
                "FROM ranking_v4_evidence_returns AS evidence_return "
                "WHERE evidence_return.definition_digest = NEW.definition_digest"
                ") "
                "OR NEW.source_result_digest IS NOT ("
                "SELECT json_extract(evidence_return.payload_json, "
                "'$.source_result_digest') "
                "FROM ranking_v4_evidence_returns AS evidence_return "
                "WHERE evidence_return.definition_digest = NEW.definition_digest "
                "ORDER BY evidence_return.sequence DESC LIMIT 1"
                ") "
                "OR NEW.dataset_revision IS NOT ("
                "SELECT evidence_return.dataset_revision "
                "FROM ranking_v4_evidence_returns AS evidence_return "
                "WHERE evidence_return.definition_digest = NEW.definition_digest "
                "ORDER BY evidence_return.sequence DESC LIMIT 1"
                ") "
                "OR NEW.benchmark_evidence_complete <> 1 "
                "OR NEW.cost_evidence_complete <> 1 "
                "OR NEW.capital_constraint_evidence_complete <> 1 "
                "OR NEW.terminal_force_close_used <> 0 "
                "OR EXISTS ("
                "SELECT 1 FROM ranking_v4_prospective_execution_summaries AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest "
                "AND (existing.dataset_revision > NEW.dataset_revision "
                "OR date(existing.execution_end_date) > "
                "date(NEW.execution_end_date) "
                "OR date(existing.latest_mature_rebalance_date) > "
                "date(NEW.latest_mature_rebalance_date) "
                "OR existing.common_date_count > NEW.common_date_count "
                "OR existing.completed_trade_count > NEW.completed_trade_count "
                "OR existing.valid_outcome_count > NEW.valid_outcome_count "
                "OR existing.expected_outcome_count > NEW.expected_outcome_count "
                "OR datetime(existing.recorded_at) > datetime(NEW.recorded_at))"
                ") "
                "OR json_valid(NEW.payload_json) <> 1 "
                "OR json_valid(NEW.attestation_json) <> 1 "
                "BEGIN "
                "SELECT RAISE(ABORT, 'Ranking V4 prospective execution summary is invalid'); "
                "END"
            )
        )

    if inspector.has_table("ranking_v4_prospective_release_proofs"):
        connection.execute(
            text(
                "CREATE TRIGGER "
                "trg_ranking_v4_evidence_release_proofs_shape_insert "
                "BEFORE INSERT ON ranking_v4_prospective_release_proofs "
                "WHEN length(NEW.release_proof_digest) <> 64 "
                "OR NEW.checkpoint_common_date_count NOT IN (80, 96, 112) "
                "OR NEW.checkpoint_common_date_count <> ("
                "SELECT COUNT(*) FROM ranking_v4_evidence_returns AS evidence_return "
                "WHERE evidence_return.definition_digest = NEW.definition_digest"
                ") "
                "OR NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_evidence_definitions AS definition "
                "JOIN ranking_v4_prospective_release_policies AS policy "
                "ON policy.definition_digest = definition.definition_digest "
                "WHERE definition.definition_digest = NEW.definition_digest "
                "AND policy.policy_digest = NEW.policy_digest "
                "AND definition.code_revision = NEW.code_revision "
                "AND definition.protocol_digest = NEW.model_protocol_digest "
                "AND definition.experiment_registry_digest = "
                "NEW.experiment_registry_digest"
                ") "
                "OR NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_evidence_proofs AS evidence_proof "
                "WHERE evidence_proof.proof_digest = NEW.evidence_proof_digest "
                "AND evidence_proof.definition_digest = NEW.definition_digest "
                "AND evidence_proof.inventory_digest = NEW.inventory_digest "
                "AND evidence_proof.return_record_count = "
                "NEW.checkpoint_common_date_count "
                "AND evidence_proof.returns_chain_digest = "
                "NEW.returns_chain_digest"
                ") "
                "OR NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_prospective_execution_summaries "
                "AS summary "
                "WHERE summary.summary_digest = NEW.execution_summary_digest "
                "AND summary.definition_digest = NEW.definition_digest "
                "AND summary.policy_digest = NEW.policy_digest "
                "AND summary.dataset_revision = NEW.dataset_revision "
                "AND summary.common_date_count = "
                "NEW.checkpoint_common_date_count "
                "AND summary.completed_trade_count = NEW.completed_trade_count"
                ") "
                "OR NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_evidence_returns AS evidence_return "
                "WHERE evidence_return.record_digest = "
                "NEW.latest_return_record_digest "
                "AND evidence_return.definition_digest = NEW.definition_digest "
                "AND evidence_return.sequence = NEW.checkpoint_common_date_count "
                "AND evidence_return.dataset_revision = NEW.dataset_revision"
                ") "
                "OR NEW.checkpoint_common_date_count <> CASE "
                "WHEN NOT EXISTS ("
                "SELECT 1 FROM ranking_v4_prospective_release_proofs AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest"
                ") THEN 80 "
                "WHEN (SELECT MAX(existing.checkpoint_common_date_count) "
                "FROM ranking_v4_prospective_release_proofs AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest) = 80 "
                "THEN 96 "
                "WHEN (SELECT MAX(existing.checkpoint_common_date_count) "
                "FROM ranking_v4_prospective_release_proofs AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest) = 96 "
                "THEN 112 ELSE -1 END "
                "OR EXISTS ("
                "SELECT 1 FROM ranking_v4_prospective_release_proofs AS existing "
                "WHERE existing.definition_digest = NEW.definition_digest "
                "AND existing.evaluation_status IN ('approved', 'rejected')"
                ") "
                "OR (NEW.official_release_allowed = 1 AND EXISTS ("
                "SELECT 1 FROM json_each(NEW.payload_json, '$.gates') AS gate "
                "WHERE json_extract(gate.value, '$.status') <> 'pass'"
                ")) "
                "OR (NEW.official_release_allowed = 0 AND NOT EXISTS ("
                "SELECT 1 FROM json_each(NEW.payload_json, '$.gates') AS gate "
                "WHERE json_extract(gate.value, '$.status') <> 'pass'"
                ")) "
                "OR json_array_length(json_extract(NEW.payload_json, '$.gates')) "
                "<> 13 "
                "OR json_valid(NEW.payload_json) <> 1 "
                "OR json_valid(NEW.attestation_json) <> 1 "
                "BEGIN "
                "SELECT RAISE(ABORT, 'Ranking V4 prospective release proof is invalid'); "
                "END"
            )
        )


def _create_immutable_ranking_v3_forward_triggers(connection) -> None:
    inspector = inspect(connection)
    if inspector.has_table("ranking_v3_forward_ledgers"):
        _create_ranking_v3_forward_ledger_triggers(connection)
    for table_name in (
        "ranking_v3_forward_sessions",
        "ranking_v3_forward_gate_evidence",
        "ranking_v3_forward_release_proofs",
    ):
        if not inspector.has_table(table_name):
            continue
        _create_immutable_row_triggers(connection, table_name)

    candidate_table = "ranking_v3_forward_candidates"
    if inspector.has_table(candidate_table):
        _create_ranking_v3_forward_candidate_triggers(connection)

    snapshot_table = "opportunity_snapshots"
    if inspector.has_table(snapshot_table) and inspector.has_table(candidate_table):
        _create_referenced_opportunity_snapshot_triggers(connection)


def _create_ranking_v3_forward_ledger_triggers(connection) -> None:
    table_name = "ranking_v3_forward_ledgers"
    immutable_columns = (
        "protocol_id",
        "protocol_digest",
        "model_version",
        "data_revision",
        "created_at",
        "integrity_status",
        "quarantine_reason",
    )
    immutable_changed = " OR ".join(
        f"OLD.{column} IS NOT NEW.{column}" for column in immutable_columns
    )
    connection.execute(
        text(
            f"CREATE TRIGGER trg_{table_name}_shape_insert "
            f"BEFORE INSERT ON {table_name} "
            "WHEN NEW.status <> 'pending' OR NEW.revision <> 0 "
            "OR NEW.current_release_proof_digest IS NOT NULL "
            "OR NEW.rejection_reasons_json <> '[]' "
            "OR NEW.integrity_status <> 'verified' "
            "OR NEW.quarantine_reason <> '' "
            "BEGIN "
            "SELECT RAISE(ABORT, 'Ranking V3 forward ledger insert shape is invalid'); "
            "END"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER trg_{table_name}_terminal_immutable_update "
            f"BEFORE UPDATE ON {table_name} "
            "WHEN OLD.status <> 'pending' "
            "BEGIN "
            "SELECT RAISE(ABORT, 'Ranking V3 forward terminal ledger is immutable'); "
            "END"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER trg_{table_name}_identity_immutable_update "
            f"BEFORE UPDATE ON {table_name} "
            f"WHEN {immutable_changed} "
            "BEGIN "
            "SELECT RAISE(ABORT, 'Ranking V3 forward ledger identity is immutable'); "
            "END"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER trg_{table_name}_revision_update "
            f"BEFORE UPDATE ON {table_name} "
            "WHEN NEW.revision <> OLD.revision + 1 "
            "BEGIN "
            "SELECT RAISE(ABORT, 'Ranking V3 forward ledger revision must increment by one'); "
            "END"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER trg_{table_name}_transition_update "
            f"BEFORE UPDATE ON {table_name} "
            "WHEN NOT ("
            "(NEW.status = 'pending' "
            "AND NEW.current_release_proof_digest IS NULL "
            "AND NEW.rejection_reasons_json = '[]') "
            "OR (NEW.status = 'approved' "
            "AND NEW.current_release_proof_digest IS NOT NULL "
            "AND length(trim(NEW.current_release_proof_digest)) = 64 "
            "AND NEW.rejection_reasons_json = '[]' "
            "AND EXISTS ("
            "SELECT 1 FROM ranking_v3_forward_release_proofs AS proof "
            "WHERE proof.proof_digest = NEW.current_release_proof_digest "
            "AND proof.protocol_id = NEW.protocol_id "
            "AND proof.protocol_digest = NEW.protocol_digest "
            "AND proof.model_version = NEW.model_version "
            "AND proof.data_revision = NEW.data_revision "
            "AND proof.ledger_revision = OLD.revision"
            ")) "
            "OR (NEW.status = 'rejected' "
            "AND NEW.current_release_proof_digest IS NULL "
            "AND json_valid(NEW.rejection_reasons_json) = 1 "
            "AND json_type(NEW.rejection_reasons_json) = 'array' "
            "AND json_array_length(NEW.rejection_reasons_json) > 0)"
            ") "
            "BEGIN "
            "SELECT RAISE(ABORT, 'Ranking V3 forward ledger transition is invalid'); "
            "END"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER trg_{table_name}_immutable_delete "
            f"BEFORE DELETE ON {table_name} "
            "BEGIN "
            "SELECT RAISE(ABORT, 'Ranking V3 forward ledgers cannot be deleted'); "
            "END"
        )
    )


def _create_immutable_row_triggers(connection, table_name: str) -> None:
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"trg_{table_name}_immutable_{operation.lower()}"
        connection.execute(
            text(
                f"CREATE TRIGGER IF NOT EXISTS {trigger_name} "
                f"BEFORE {operation} ON {table_name} "
                "BEGIN "
                f"SELECT RAISE(ABORT, '{table_name} rows are immutable'); "
                "END"
            )
        )


def _create_ranking_v3_forward_candidate_triggers(connection) -> None:
    table_name = "ranking_v3_forward_candidates"
    selection_columns = (
        "protocol_id",
        "protocol_digest",
        "model_version",
        "candidate_id",
        "source_snapshot_id",
        "session_date",
        "maturity_session_date",
        "instrument_id",
        "strategy_id",
        "rank",
        "score",
        "benchmark_id",
        "data_revision",
        "selection_digest",
        "idempotency_key",
        "fact_digest",
        "integrity_status",
        "quarantine_reason",
        "created_at",
    )
    selection_changed = " OR ".join(
        f"OLD.{column} IS NOT NEW.{column}" for column in selection_columns
    )
    connection.execute(
        text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_selection_immutable_update "
            f"BEFORE UPDATE ON {table_name} "
            f"WHEN {selection_changed} "
            "BEGIN "
            "SELECT RAISE(ABORT, "
            "'Ranking V3 forward candidate selection facts are immutable'); "
            "END"
        )
    )

    outcome_columns = (
        "outcome_digest",
        "outcome_idempotency_key",
        "resolved_on",
        "gross_return_pct",
        "transaction_cost_pct",
        "stress_transaction_cost_pct",
        "net_return_pct",
        "stress_net_return_pct",
        "benchmark_return_pct",
        "benchmark_excess_pct",
        "stress_benchmark_excess_pct",
        "max_drawdown_pct",
        "outcome_reason",
        "updated_at",
    )
    pending_outcome_changed = " OR ".join(
        f"OLD.{column} IS NOT NEW.{column}" for column in outcome_columns
    )
    connection.execute(
        text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_pending_outcome_guard_update "
            f"BEFORE UPDATE ON {table_name} "
            "WHEN OLD.outcome_status = 'pending' "
            "AND NEW.outcome_status = 'pending' "
            f"AND ({pending_outcome_changed}) "
            "BEGIN "
            "SELECT RAISE(ABORT, "
            "'Ranking V3 forward candidate pending outcome cannot be partially written'); "
            "END"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_terminal_shape_update "
            f"BEFORE UPDATE ON {table_name} "
            "WHEN OLD.outcome_status = 'pending' "
            "AND NEW.outcome_status <> 'pending' "
            "AND (NEW.outcome_digest IS NULL OR trim(NEW.outcome_digest) = '' "
            "OR NEW.outcome_idempotency_key IS NULL "
            "OR trim(NEW.outcome_idempotency_key) = '' "
            "OR NEW.resolved_on IS NULL) "
            "BEGIN "
            "SELECT RAISE(ABORT, "
            "'Ranking V3 forward candidate terminal outcome is incomplete'); "
            "END"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_terminal_immutable_update "
            f"BEFORE UPDATE ON {table_name} "
            "WHEN OLD.outcome_status <> 'pending' "
            "BEGIN "
            "SELECT RAISE(ABORT, "
            "'Ranking V3 forward candidate terminal outcome is immutable'); "
            "END"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_immutable_delete "
            f"BEFORE DELETE ON {table_name} "
            "BEGIN "
            "SELECT RAISE(ABORT, 'Ranking V3 forward candidates cannot be deleted'); "
            "END"
        )
    )


def _create_referenced_opportunity_snapshot_triggers(connection) -> None:
    table_name = "opportunity_snapshots"
    reference_check = (
        "EXISTS (SELECT 1 FROM ranking_v3_forward_candidates "
        "WHERE source_snapshot_id = OLD.snapshot_id)"
    )
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"trg_{table_name}_forward_reference_{operation.lower()}"
        connection.execute(
            text(
                f"CREATE TRIGGER IF NOT EXISTS {trigger_name} "
                f"BEFORE {operation} ON {table_name} "
                f"WHEN {reference_check} "
                "BEGIN "
                "SELECT RAISE(ABORT, "
                "'opportunity snapshot referenced by Ranking V3 forward evidence is immutable'); "
                "END"
            )
        )


def _create_immutable_ranking_v3_production_triggers(connection) -> None:
    from qagent.backtesting.ranking_v3_production import (
        PRODUCTION_BATCH_SCHEMA_VERSION,
        PRODUCTION_SELECTION_SCHEMA_VERSION,
    )

    inspector = inspect(connection)
    batch_table = "ranking_v3_production_batches"
    selection_table = "ranking_v3_production_selections"
    alias_table = "ranking_v3_production_idempotency_keys"
    for table_name in (batch_table, selection_table, alias_table):
        if inspector.has_table(table_name):
            _create_immutable_row_triggers(connection, table_name)

    if inspector.has_table(batch_table) and inspector.has_table(selection_table):
        connection.execute(
            text(
                f"CREATE TRIGGER trg_{selection_table}_batch_reference_insert "
                f"BEFORE INSERT ON {selection_table} "
                "WHEN NOT EXISTS ("
                f"SELECT 1 FROM {batch_table} AS batch "
                "WHERE batch.fact_digest = NEW.batch_fact_digest "
                "AND batch.identity_digest = NEW.identity_digest "
                "AND batch.selected_count >= NEW.rank "
                "AND json_valid(batch.payload_json) = 1 "
                "AND json_extract(batch.payload_json, '$.fact_digest') = batch.fact_digest "
                "AND json_extract(batch.payload_json, '$.identity.identity_digest') "
                "= batch.identity_digest "
                "AND json_extract("
                "batch.payload_json, "
                "'$.selections[' || (NEW.rank - 1) || '].item_digest'"
                ") = NEW.item_digest"
                ") "
                "BEGIN "
                "SELECT RAISE(ABORT, "
                "'Ranking V3 production selection must reference its immutable batch'); "
                "END"
            )
        )

    snapshot_table = "opportunity_snapshots"
    if inspector.has_table(snapshot_table) and inspector.has_table(selection_table):
        connection.execute(
            text(
                f"CREATE TRIGGER trg_{selection_table}_snapshot_reference_insert "
                f"BEFORE INSERT ON {selection_table} "
                "WHEN NOT EXISTS ("
                f"SELECT 1 FROM {snapshot_table} AS snapshot "
                "WHERE snapshot.snapshot_id = NEW.source_snapshot_id "
                "AND snapshot.instrument_id = NEW.instrument_id "
                "AND snapshot.primary_strategy_id = NEW.strategy_id "
                "AND snapshot.trigger_price = NEW.trigger_price "
                "AND snapshot.initial_stop IS NEW.initial_stop "
                "AND snapshot.target_1 IS NEW.target_1 "
                "AND snapshot.rank_score = NEW.source_rank_score "
                "AND NEW.trigger_price IS NOT NULL "
                "AND NEW.allocation_multiplier IS NOT NULL "
                "AND NEW.allocation_multiplier > 0 "
                "AND NEW.allocation_multiplier <= 1 "
                "AND json_valid(NEW.payload_json) = 1 "
                "AND json_extract(NEW.payload_json, '$.schema_version') "
                f"= '{PRODUCTION_SELECTION_SCHEMA_VERSION}' "
                "AND EXISTS ("
                f"SELECT 1 FROM {batch_table} AS batch "
                "WHERE batch.fact_digest = NEW.batch_fact_digest "
                "AND json_valid(batch.payload_json) = 1 "
                "AND json_extract(batch.payload_json, '$.source_scan_run_id') "
                "= snapshot.run_id"
                ")"
                ") "
                "BEGIN "
                "SELECT RAISE(ABORT, "
                "'Ranking V3 production selection must reference an opportunity snapshot'); "
                "END"
            )
        )
        reference_check = (
            f"EXISTS (SELECT 1 FROM {selection_table} WHERE source_snapshot_id = OLD.snapshot_id)"
        )
        for operation in ("UPDATE", "DELETE"):
            connection.execute(
                text(
                    f"CREATE TRIGGER "
                    f"trg_{snapshot_table}_production_reference_{operation.lower()} "
                    f"BEFORE {operation} ON {snapshot_table} "
                    f"WHEN {reference_check} "
                    "BEGIN "
                    "SELECT RAISE(ABORT, "
                    "'opportunity snapshot referenced by Ranking V3 production "
                    "selection is immutable'); "
                    "END"
                )
            )

    scan_table = "scan_runs"
    if (
        inspector.has_table(scan_table)
        and inspector.has_table(snapshot_table)
        and inspector.has_table(selection_table)
        and inspector.has_table(batch_table)
    ):
        reference_check = (
            f"EXISTS (SELECT 1 FROM {selection_table} AS selection "
            f"JOIN {snapshot_table} AS snapshot "
            "ON snapshot.snapshot_id = selection.source_snapshot_id "
            "WHERE snapshot.run_id = OLD.run_id)"
        )
        for operation in ("UPDATE", "DELETE"):
            connection.execute(
                text(
                    f"CREATE TRIGGER trg_{scan_table}_production_reference_{operation.lower()} "
                    f"BEFORE {operation} ON {scan_table} "
                    f"WHEN {reference_check} "
                    "BEGIN "
                    "SELECT RAISE(ABORT, "
                    "'scan run referenced by Ranking V3 production selection is immutable'); "
                    "END"
                )
            )

    if inspector.has_table(batch_table) and inspector.has_table(alias_table):
        connection.execute(
            text(
                f"CREATE TRIGGER trg_{alias_table}_batch_reference_insert "
                f"BEFORE INSERT ON {alias_table} "
                "WHEN NOT EXISTS ("
                f"SELECT 1 FROM {batch_table} AS batch "
                "WHERE batch.fact_digest = NEW.batch_fact_digest "
                "AND batch.identity_digest = NEW.identity_digest"
                ") "
                "BEGIN "
                "SELECT RAISE(ABORT, "
                "'Ranking V3 production idempotency alias must reference its batch'); "
                "END"
            )
        )

    paper_table = "paper_trades"
    required_tables = {batch_table, selection_table, paper_table}
    if required_tables.issubset(set(inspector.get_table_names())):
        source_plan_invalid = (
            "NOT EXISTS ("
            "SELECT 1 FROM opportunity_snapshots AS snapshot "
            "JOIN scan_runs AS scan ON scan.run_id = snapshot.run_id "
            "WHERE snapshot.snapshot_id = NEW.source_snapshot_id "
            "AND scan.provider = NEW.provider "
            "AND snapshot.instrument_id = NEW.instrument_id "
            "AND snapshot.primary_strategy_id = NEW.strategy_id "
            "AND snapshot.signal_date = NEW.signal_date "
            "AND snapshot.trigger_price = NEW.trigger_price "
            "AND snapshot.initial_stop IS NEW.initial_stop "
            "AND snapshot.target_1 IS NEW.target_1 "
            "AND snapshot.rank_score IS NEW.rank_score"
            ")"
        )
        production_binding_invalid = (
            "NEW.admission_source = 'ranking_v3_production' AND ("
            "NEW.production_identity_digest IS NULL "
            "OR length(trim(NEW.production_identity_digest)) <> 64 "
            "OR NEW.production_batch_fact_digest IS NULL "
            "OR length(trim(NEW.production_batch_fact_digest)) <> 64 "
            "OR NEW.production_selection_item_digest IS NULL "
            "OR length(trim(NEW.production_selection_item_digest)) <> 64 "
            "OR NEW.release_proof_digest IS NULL "
            "OR length(trim(NEW.release_proof_digest)) <> 64 "
            "OR NEW.strategy_id IS NULL OR trim(NEW.strategy_id) = '' "
            "OR NOT EXISTS ("
            f"SELECT 1 FROM {selection_table} AS selection "
            f"JOIN {batch_table} AS batch "
            "ON batch.fact_digest = selection.batch_fact_digest "
            "WHERE batch.fact_digest = NEW.production_batch_fact_digest "
            "AND batch.identity_digest = NEW.production_identity_digest "
            "AND batch.release_proof_digest = NEW.release_proof_digest "
            "AND batch.session_date = NEW.signal_date "
            "AND json_valid(batch.payload_json) = 1 "
            "AND json_extract(batch.payload_json, '$.schema_version') "
            f"= '{PRODUCTION_BATCH_SCHEMA_VERSION}' "
            "AND selection.item_digest = NEW.production_selection_item_digest "
            "AND selection.source_snapshot_id = NEW.source_snapshot_id "
            "AND selection.instrument_id = NEW.instrument_id "
            "AND selection.strategy_id = NEW.strategy_id "
            "AND selection.trigger_price = NEW.trigger_price "
            "AND selection.initial_stop IS NEW.initial_stop "
            "AND selection.target_1 IS NEW.target_1 "
            "AND selection.source_rank_score = NEW.rank_score "
            "AND selection.allocation_multiplier = NEW.allocation_multiplier "
            "AND json_valid(selection.payload_json) = 1 "
            "AND json_extract(selection.payload_json, '$.schema_version') "
            f"= '{PRODUCTION_SELECTION_SCHEMA_VERSION}'"
            ") "
            f"OR {source_plan_invalid} "
            "OR NEW.allocation_multiplier <= 0 "
            "OR NEW.allocation_multiplier > 1"
            ")"
        )
        for operation in ("INSERT", "UPDATE"):
            connection.execute(
                text(
                    f"CREATE TRIGGER "
                    f"trg_{paper_table}_ranking_v3_production_{operation.lower()} "
                    f"BEFORE {operation} ON {paper_table} "
                    f"WHEN {production_binding_invalid} "
                    "BEGIN "
                    "SELECT RAISE(ABORT, "
                    "'paper trade Ranking V3 production admission proof is invalid'); "
                    "END"
                )
            )
        immutable_production_plan = " OR ".join(
            f"NEW.{column} IS NOT OLD.{column}"
            for column in (
                "source_snapshot_id",
                "provider",
                "instrument_id",
                "strategy_id",
                "admission_source",
                "production_identity_digest",
                "production_batch_fact_digest",
                "production_selection_item_digest",
                "release_proof_digest",
                "signal_date",
                "trigger_price",
                "initial_stop",
                "target_1",
                "rank_score",
                "allocation_multiplier",
            )
        )
        connection.execute(
            text(
                f"CREATE TRIGGER trg_{paper_table}_ranking_v3_production_immutable_update "
                f"BEFORE UPDATE ON {paper_table} "
                "WHEN OLD.admission_source = 'ranking_v3_production' "
                f"AND ({immutable_production_plan}) "
                "BEGIN "
                "SELECT RAISE(ABORT, "
                "'paper trade Ranking V3 production plan is immutable'); "
                "END"
            )
        )


def _create_ranking_v3_forward_source_snapshot_triggers(connection) -> None:
    inspector = inspect(connection)
    table_name = "ranking_v3_forward_candidates"
    if not inspector.has_table(table_name):
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "source_snapshot_id" not in columns:
        return
    for operation in ("INSERT", "UPDATE"):
        trigger_name = f"trg_{table_name}_source_snapshot_required_{operation.lower()}"
        connection.execute(
            text(
                f"CREATE TRIGGER IF NOT EXISTS {trigger_name} "
                f"BEFORE {operation} ON {table_name} "
                "WHEN NEW.source_snapshot_id IS NULL "
                "OR trim(NEW.source_snapshot_id) = '' "
                "BEGIN "
                "SELECT RAISE(ABORT, "
                "'Ranking V3 forward candidate source snapshot is required'); "
                "END"
            )
        )


def _rebuild_revision_scoped_tables(connection) -> None:
    expected_keys = {
        "fundamental_snapshots": [
            "provider_mode",
            "instrument_id",
            "as_of_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_tradability": [
            "provider_mode",
            "instrument_id",
            "trade_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_instrument_profiles": [
            "provider_mode",
            "instrument_id",
            "snapshot_date",
            "dataset_revision",
        ],
        "historical_industry_snapshots": [
            "provider_mode",
            "instrument_id",
            "snapshot_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_index_snapshots": [
            "provider_mode",
            "index_id",
            "snapshot_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_index_memberships": [
            "provider_mode",
            "index_id",
            "snapshot_date",
            "instrument_id",
            "source_provider",
            "dataset_revision",
        ],
        "historical_replay_bars": [
            "provider_mode",
            "instrument_id",
            "trade_date",
            "source_provider",
            "dataset_revision",
        ],
        "historical_corporate_actions": [
            "provider_mode",
            "instrument_id",
            "action_id",
            "source_provider",
            "dataset_revision",
        ],
        "historical_corporate_action_coverage": [
            "provider_mode",
            "instrument_id",
            "start_date",
            "end_date",
            "source_provider",
            "dataset_revision",
        ],
    }
    for table_name, expected_key in expected_keys.items():
        _rebuild_table_primary_key(connection, table_name, expected_key)


def _rebuild_ranking_v3_forward_gate_evidence_kind(connection) -> None:
    table_name = "ranking_v3_forward_gate_evidence"
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return
    table_sql = connection.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    ).scalar_one_or_none()
    if table_sql is None or "'portfolio'" in table_sql:
        return

    for operation in ("update", "delete"):
        connection.execute(text(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_{operation}"))
    table = Base.metadata.tables[table_name]
    column_names = [column.name for column in table.columns]
    columns_sql = ", ".join(column_names)
    backup_name = f"{table_name}_migration_backup"
    connection.execute(
        text(f"CREATE TEMP TABLE {backup_name} AS SELECT {columns_sql} FROM {table_name}")
    )
    connection.execute(text(f"DROP TABLE {table_name}"))
    table.create(connection)
    connection.execute(
        text(f"INSERT INTO {table_name} ({columns_sql}) SELECT {columns_sql} FROM {backup_name}")
    )
    connection.execute(text(f"DROP TABLE {backup_name}"))


def _rebuild_table_primary_key(connection, table_name: str, expected_key: list[str]) -> None:
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return
    current_key = inspector.get_pk_constraint(table_name)["constrained_columns"]
    if current_key == expected_key:
        return
    table = Base.metadata.tables[table_name]
    column_names = [column.name for column in table.columns]
    columns_sql = ", ".join(column_names)
    backup_name = f"{table_name}_migration_backup"
    connection.execute(
        text(f"CREATE TEMP TABLE {backup_name} AS SELECT {columns_sql} FROM {table_name}")
    )
    connection.execute(text(f"DROP TABLE {table_name}"))
    table.create(connection)
    connection.execute(
        text(f"INSERT INTO {table_name} ({columns_sql}) SELECT {columns_sql} FROM {backup_name}")
    )
    connection.execute(text(f"DROP TABLE {backup_name}"))


def _create_missing_metadata_indexes(connection) -> None:
    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        for index in table.indexes:
            index.create(connection, checkfirst=True)


def _repair_factor_research_model_digest_index(connection) -> None:
    table_name = "factor_research_model_artifacts"
    index_name = "ix_factor_research_model_artifacts_model_digest"
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return
    current = next(
        (item for item in inspector.get_indexes(table_name) if item["name"] == index_name),
        None,
    )
    if current is None or not current.get("unique"):
        return
    quoted_index = connection.dialect.identifier_preparer.quote(index_name)
    connection.execute(text(f"DROP INDEX {quoted_index}"))


def _repair_legacy_scoped_index_snapshot_counts(connection) -> None:
    """Align legacy BaoStock snapshot metadata with its stored replay scope."""

    inspector = inspect(connection)
    required = {
        "historical_index_snapshots",
        "historical_index_memberships",
    }
    if not all(inspector.has_table(table_name) for table_name in required):
        return
    connection.execute(
        text(
            "UPDATE historical_index_snapshots AS snapshots "
            "SET member_count = ("
            "SELECT COUNT(*) FROM historical_index_memberships AS memberships "
            "WHERE memberships.provider_mode = snapshots.provider_mode "
            "AND memberships.index_id = snapshots.index_id "
            "AND memberships.snapshot_date = snapshots.snapshot_date "
            "AND memberships.source_provider = snapshots.source_provider "
            "AND memberships.dataset_revision = snapshots.dataset_revision"
            ") "
            "WHERE snapshots.source_provider = 'baostock' "
            "AND snapshots.dataset_revision = 0 "
            "AND snapshots.status = 'ready' "
            "AND (snapshots.error IS NULL OR snapshots.error = '') "
            "AND snapshots.member_count <> ("
            "SELECT COUNT(*) FROM historical_index_memberships AS memberships "
            "WHERE memberships.provider_mode = snapshots.provider_mode "
            "AND memberships.index_id = snapshots.index_id "
            "AND memberships.snapshot_date = snapshots.snapshot_date "
            "AND memberships.source_provider = snapshots.source_provider "
            "AND memberships.dataset_revision = snapshots.dataset_revision"
            ")"
        )
    )


def _drop_obsolete_walk_forward_indexes(connection) -> None:
    for index_name in (
        "ix_historical_replay_bars_lookup",
        "ix_historical_tradability_replay_lookup",
    ):
        connection.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
