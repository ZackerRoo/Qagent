import json
import hashlib
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, sessionmaker

from qagent.execution.models import AShareExecutionRules, OrderSide
from qagent.execution.replay_evidence import (
    PAPER_REPLAY_EVIDENCE_NOTE_PREFIX,
    PaperReplayEvidence,
    stable_replay_digest,
)
from qagent.execution.rules import is_tick_aligned
from qagent.storage.tables import (
    OpportunitySnapshotRow,
    PaperAccountSettingsRow,
    PaperResearchBaselineRow,
    PaperTradeEventRow,
    PaperTradeRow,
    ScanRunRow,
    utc_now,
)
from qagent.recommendations.strategy_configuration import parse_paper_strategy_configuration


PAPER_EXECUTION_FACTS_NOTE_PREFIX = "[paper_execution_facts:v1]"
PAPER_REPLAY_EVIDENCE_STATUS_NOTE_PREFIX = "[paper_replay_evidence_status:v1]"
PAPER_SOURCE_CONTEXT_NOTE_PREFIX = "[paper_source_context:v1]"
PAPER_TRADE_TERMINAL_STATUSES = frozenset(
    {
        "target_1_hit",
        "stopped",
        "time_exit",
        "missed_entry",
        "replaced",
        "invalidated",
    }
)
PAPER_TRADE_EXECUTED_TERMINAL_STATUSES = frozenset({"target_1_hit", "stopped", "time_exit"})
PAPER_TRADE_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset(
        {
            "open",
            "target_1_hit",
            "stopped",
            "time_exit",
            "missed_entry",
            "replaced",
            "invalidated",
        }
    ),
    "open": PAPER_TRADE_EXECUTED_TERMINAL_STATUSES,
    "target_1_hit": frozenset(),
    "stopped": frozenset(),
    "time_exit": frozenset(),
    "missed_entry": frozenset({"replaced"}),
    "replaced": frozenset(),
    "invalidated": frozenset(),
}
PAPER_TRADE_EVENT_FIELDS = frozenset(
    {
        "status",
        "entry_date",
        "entry_price",
        "exit_date",
        "exit_price",
        "latest_date",
        "latest_price",
        "unrealized_return_pct",
        "realized_return_pct",
        "holding_days",
        "allocation_multiplier",
    }
)


class PaperExecutionLegFacts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market_event_id: str
    side: OrderSide
    trade_date: date
    base_price: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    quantity: int = Field(gt=0)
    gross_amount: Decimal = Field(gt=0)
    commission: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0"), ge=0)
    transfer_fee: Decimal = Field(default=Decimal("0"), ge=0)
    slippage: Decimal = Field(default=Decimal("0"), ge=0)
    cash_flow: Decimal
    source: str = "unified_execution"

    @property
    def total_fees(self) -> Decimal:
        return self.commission + self.stamp_duty + self.transfer_fee

    @model_validator(mode="after")
    def validate_cash_contract(self):
        if self.gross_amount != self.price * self.quantity:
            raise ValueError("gross_amount must equal price times quantity")
        expected = (
            -(self.gross_amount + self.total_fees)
            if self.side == OrderSide.BUY
            else self.gross_amount - self.total_fees
        )
        if self.cash_flow != expected:
            raise ValueError("cash_flow must match side, gross amount, and fees")
        return self


class PaperExecutionFacts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "paper-execution-facts-v1"
    allocation: Decimal = Field(gt=0)
    rules: AShareExecutionRules
    entry: PaperExecutionLegFacts
    exit: PaperExecutionLegFacts | None = None

    @model_validator(mode="after")
    def validate_execution_contract(self):
        if self.entry.side != OrderSide.BUY:
            raise ValueError("entry execution fact must be a buy")
        legs = (self.entry,) if self.exit is None else (self.entry, self.exit)
        for leg in legs:
            minimum = self.rules.effective_minimum_order_quantity
            step = self.rules.effective_quantity_step
            if leg.quantity < minimum or (leg.quantity - minimum) % step != 0:
                raise ValueError("execution fact quantity must respect the frozen lot size")
            if not is_tick_aligned(leg.price, self.rules.tick_size):
                raise ValueError("execution fact price must respect the frozen tick size")
        if self.exit is not None:
            if self.exit.side != OrderSide.SELL:
                raise ValueError("exit execution fact must be a sell")
            if self.exit.quantity != self.entry.quantity:
                raise ValueError("exit execution fact must close the frozen position")
        return self


class PaperTradeRecord(BaseModel):
    trade_id: str
    source_snapshot_id: str
    provider: str
    instrument_id: str
    strategy_id: str | None
    status: str
    signal_date: date
    trigger_price: Decimal
    initial_stop: Decimal | None
    target_1: Decimal | None
    rank_score: Decimal | None
    allocation_multiplier: Decimal = Decimal("1.0")
    entry_date: date | None
    entry_price: Decimal | None
    exit_date: date | None
    exit_price: Decimal | None
    latest_date: date | None
    latest_price: Decimal | None
    unrealized_return_pct: float | None
    realized_return_pct: float | None
    holding_days: int
    notes: str
    admission_source: str = "legacy_unknown"
    production_identity_digest: str | None = None
    production_batch_fact_digest: str | None = None
    production_selection_item_digest: str | None = None
    release_proof_digest: str | None = None
    execution_facts: PaperExecutionFacts | None = Field(default=None, exclude=True)


class PaperTradeAdmissionProof(BaseModel):
    """Immutable provenance attached to a paper trade at creation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    admission_source: str = Field(min_length=1, max_length=48)
    production_identity_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    production_batch_fact_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    production_selection_item_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    release_proof_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_binding(self):
        production_values = (
            self.production_identity_digest,
            self.production_batch_fact_digest,
            self.production_selection_item_digest,
            self.release_proof_digest,
        )
        if self.admission_source == "ranking_v3_production":
            if any(value is None for value in production_values):
                raise ValueError("Ranking V3 paper admission requires a complete production proof")
        elif any(value is not None for value in production_values):
            raise ValueError("non-production paper admission cannot carry production proof fields")
        return self


class PaperAccountSettings(BaseModel):
    account_id: str
    session_id: str
    label: str
    status: str
    initial_capital: Decimal
    allocation_per_trade_pct: Decimal
    max_positions: int
    transaction_cost_bps: Decimal
    slippage_bps: Decimal
    take_profit_pct: Decimal
    started_at: datetime


class PaperResearchBaseline(BaseModel):
    model_config = ConfigDict(frozen=True)

    baseline_id: str
    provider: str
    paper_session_id: str
    walk_forward_run_id: str
    start_date: date
    definition_digest: str
    definition: dict[str, object]
    created_at: datetime


class PaperTradeSourceContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "paper-source-context-v2"
    source_snapshot_id: str
    source_run_id: str | None = None
    created_at: datetime
    signal_date: date | None = None
    latest_close: Decimal | None = None
    industry: str = "unknown"
    themes: list[str] = Field(default_factory=list)
    market_regime: str = "unknown"
    factor_ids: list[str] = Field(default_factory=list)
    source_status: str = "unknown"
    strategy_configuration: dict[str, object] = Field(default_factory=dict)
    strategy_configuration_digest: str | None = None
    strategy_configuration_status: str = "legacy_unfrozen"
    card: dict[str, object]


class PaperTradeEventMetadata(BaseModel):
    idempotency_key: str | None = None
    occurred_at: datetime | None = None
    trade_date: date | None = None
    price: Decimal | None = None
    reason_code: str | None = None
    note: str = ""
    source: str = "paper_repository"
    execution_facts: PaperExecutionFacts | None = None
    replay_evidence: PaperReplayEvidence | tuple[PaperReplayEvidence, ...] | None = None
    replay_evidence_error: str | None = None


class PaperTradeEventRecord(BaseModel):
    event_id: str
    trade_id: str
    instrument_id: str
    sequence: int
    idempotency_key: str
    event_type: str
    from_status: str | None
    to_status: str
    occurred_at: datetime
    trade_date: date | None
    price: Decimal | None
    reason_code: str | None
    note: str
    source: str
    created_at: datetime
    execution_facts: PaperExecutionFacts | None = None
    replay_evidence: tuple[PaperReplayEvidence, ...] = ()


class PaperReplayEvidenceAuditRecord(BaseModel):
    """Read-only replay input or a fail-closed audit item from an event note."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_id: str
    trade_id: str
    occurred_at: datetime
    evidence: PaperReplayEvidence | None = None
    issue_code: str | None = None
    issue_detail: str | None = None

    @model_validator(mode="after")
    def validate_audit_item(self):
        if (self.evidence is None) == (self.issue_code is None):
            raise ValueError("audit item must contain exactly one of evidence or issue")
        return self


class PaperTradingRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create_trade(
        self,
        source_snapshot_id: str,
        provider: str,
        instrument_id: str,
        strategy_id: str | None,
        signal_date: date,
        trigger_price: Decimal,
        initial_stop: Decimal | None,
        target_1: Decimal | None,
        rank_score: Decimal | None = None,
        allocation_multiplier: Decimal = Decimal("1.0"),
        notes: str = "",
        *,
        admission_proof: PaperTradeAdmissionProof | None = None,
        event_metadata: PaperTradeEventMetadata | None = None,
    ) -> PaperTradeRecord:
        proof = admission_proof or PaperTradeAdmissionProof(admission_source="legacy_unknown")
        with self.session_factory() as session:
            snapshot = session.get(OpportunitySnapshotRow, source_snapshot_id)
            if proof.admission_source == "ranking_v3_production":
                _require_production_trade_matches_snapshot(
                    session,
                    snapshot,
                    provider=provider,
                    instrument_id=instrument_id,
                    strategy_id=strategy_id,
                    signal_date=signal_date,
                    trigger_price=trigger_price,
                    initial_stop=initial_stop,
                    target_1=target_1,
                    rank_score=rank_score,
                    allocation_multiplier=allocation_multiplier,
                )
            existing = (
                session.query(PaperTradeRow)
                .filter(PaperTradeRow.source_snapshot_id == source_snapshot_id)
                .one_or_none()
            )
            if existing is not None:
                if _paper_admission_tuple(existing) != _paper_admission_tuple(proof):
                    raise ValueError(
                        "source snapshot is already bound to a different paper admission proof"
                    )
                if proof.admission_source == "ranking_v3_production":
                    _require_existing_production_plan_matches(
                        existing,
                        provider=provider,
                        instrument_id=instrument_id,
                        strategy_id=strategy_id,
                        signal_date=signal_date,
                        trigger_price=trigger_price,
                        initial_stop=initial_stop,
                        target_1=target_1,
                        rank_score=rank_score,
                        allocation_multiplier=allocation_multiplier,
                    )
                if self._ensure_initial_trade_event(session, existing):
                    session.commit()
                return self._trade_from_row(
                    existing,
                    self._latest_execution_facts(session, existing.trade_id),
                )
            source_context = _source_context_from_snapshot(
                snapshot,
                source_snapshot_id=source_snapshot_id,
                signal_date=signal_date,
                source_status="frozen" if snapshot is not None else "unknown",
                strategy_configuration=_strategy_configuration_from_snapshot(session, snapshot),
                fallback_market_regime=_market_regime_from_snapshot_run(
                    session,
                    snapshot,
                ),
            )
            row = PaperTradeRow(
                trade_id=f"paper-{uuid4().hex[:12]}",
                source_snapshot_id=source_snapshot_id,
                provider=provider,
                instrument_id=instrument_id,
                strategy_id=strategy_id,
                status="pending",
                signal_date=signal_date,
                trigger_price=trigger_price,
                initial_stop=initial_stop,
                target_1=target_1,
                rank_score=rank_score,
                allocation_multiplier=allocation_multiplier,
                notes=notes,
                admission_source=proof.admission_source,
                production_identity_digest=proof.production_identity_digest,
                production_batch_fact_digest=proof.production_batch_fact_digest,
                production_selection_item_digest=proof.production_selection_item_digest,
                release_proof_digest=proof.release_proof_digest,
            )
            session.add(row)
            self._append_trade_event(
                session,
                row,
                sequence=1,
                event_type="created",
                from_status=None,
                to_status="pending",
                default_trade_date=signal_date,
                default_price=trigger_price,
                default_note=notes,
                metadata=event_metadata,
                source_context=source_context,
            )
            session.commit()
            session.refresh(row)
            return self._trade_from_row(row)

    def create_trade_if_capacity(
        self,
        source_snapshot_id: str,
        provider: str,
        instrument_id: str,
        strategy_id: str | None,
        signal_date: date,
        trigger_price: Decimal,
        initial_stop: Decimal | None,
        target_1: Decimal | None,
        *,
        max_active_trades: int,
        rank_score: Decimal | None = None,
        allocation_multiplier: Decimal = Decimal("1.0"),
        notes: str = "",
        admission_proof: PaperTradeAdmissionProof | None = None,
        event_metadata: PaperTradeEventMetadata | None = None,
    ) -> PaperTradeRecord | None:
        """Atomically recheck the single paper ledger before admitting a trade."""

        if max_active_trades <= 0:
            return None
        proof = admission_proof or PaperTradeAdmissionProof(admission_source="legacy_unknown")
        engine = self.session_factory.kw.get("bind")
        if engine is None:
            raise TypeError("paper repository requires an engine-bound session factory")
        with engine.connect() as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            session = Session(bind=connection, expire_on_commit=False)
            try:
                snapshot = session.get(OpportunitySnapshotRow, source_snapshot_id)
                if proof.admission_source == "ranking_v3_production":
                    _require_production_trade_matches_snapshot(
                        session,
                        snapshot,
                        provider=provider,
                        instrument_id=instrument_id,
                        strategy_id=strategy_id,
                        signal_date=signal_date,
                        trigger_price=trigger_price,
                        initial_stop=initial_stop,
                        target_1=target_1,
                        rank_score=rank_score,
                        allocation_multiplier=allocation_multiplier,
                    )
                existing = (
                    session.query(PaperTradeRow)
                    .filter(PaperTradeRow.source_snapshot_id == source_snapshot_id)
                    .one_or_none()
                )
                if existing is not None:
                    connection.commit()
                    return None
                active_query = session.query(PaperTradeRow).filter(
                    PaperTradeRow.status.in_(("pending", "open"))
                )
                if active_query.count() >= max_active_trades:
                    connection.commit()
                    return None
                if (
                    active_query.filter(
                        PaperTradeRow.provider == provider,
                        PaperTradeRow.instrument_id == instrument_id,
                    ).first()
                    is not None
                ):
                    connection.commit()
                    return None
                source_context = _source_context_from_snapshot(
                    snapshot,
                    source_snapshot_id=source_snapshot_id,
                    signal_date=signal_date,
                    source_status="frozen" if snapshot is not None else "unknown",
                    strategy_configuration=_strategy_configuration_from_snapshot(session, snapshot),
                    fallback_market_regime=_market_regime_from_snapshot_run(session, snapshot),
                )
                row = PaperTradeRow(
                    trade_id=f"paper-{uuid4().hex[:12]}",
                    source_snapshot_id=source_snapshot_id,
                    provider=provider,
                    instrument_id=instrument_id,
                    strategy_id=strategy_id,
                    status="pending",
                    signal_date=signal_date,
                    trigger_price=trigger_price,
                    initial_stop=initial_stop,
                    target_1=target_1,
                    rank_score=rank_score,
                    allocation_multiplier=allocation_multiplier,
                    notes=notes,
                    admission_source=proof.admission_source,
                    production_identity_digest=proof.production_identity_digest,
                    production_batch_fact_digest=proof.production_batch_fact_digest,
                    production_selection_item_digest=(proof.production_selection_item_digest),
                    release_proof_digest=proof.release_proof_digest,
                )
                session.add(row)
                self._append_trade_event(
                    session,
                    row,
                    sequence=1,
                    event_type="created",
                    from_status=None,
                    to_status="pending",
                    default_trade_date=signal_date,
                    default_price=trigger_price,
                    default_note=notes,
                    metadata=event_metadata,
                    source_context=source_context,
                )
                session.flush()
                connection.commit()
                return self._trade_from_row(row)
            except Exception:
                connection.rollback()
                raise
            finally:
                session.close()

    def get_research_baseline(
        self,
        *,
        provider: str,
        paper_session_id: str | None = None,
    ) -> PaperResearchBaseline | None:
        with self.session_factory() as session:
            query = session.query(PaperResearchBaselineRow).filter(
                PaperResearchBaselineRow.provider == provider
            )
            if paper_session_id:
                query = query.filter(PaperResearchBaselineRow.paper_session_id == paper_session_id)
            row = query.order_by(
                PaperResearchBaselineRow.created_at.desc(),
                PaperResearchBaselineRow.baseline_id.desc(),
            ).first()
            return self._research_baseline_from_row(row) if row is not None else None

    def freeze_research_baseline(
        self,
        *,
        baseline_id: str,
        provider: str,
        paper_session_id: str,
        walk_forward_run_id: str,
        start_date: date,
        definition: dict[str, object],
    ) -> PaperResearchBaseline:
        encoded = json.dumps(
            definition,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        definition_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.session_factory() as session:
            existing = session.get(PaperResearchBaselineRow, baseline_id)
            if existing is not None:
                if (
                    existing.provider != provider
                    or existing.paper_session_id != paper_session_id
                    or existing.walk_forward_run_id != walk_forward_run_id
                    or existing.start_date != start_date
                    or existing.definition_digest != definition_digest
                    or existing.definition_json != encoded
                ):
                    raise ValueError(
                        "paper research baseline already exists with a different definition"
                    )
                return self._research_baseline_from_row(existing)
            row = PaperResearchBaselineRow(
                baseline_id=baseline_id,
                provider=provider,
                paper_session_id=paper_session_id,
                walk_forward_run_id=walk_forward_run_id,
                start_date=start_date,
                definition_digest=definition_digest,
                definition_json=encoded,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._research_baseline_from_row(row)

    def get_trade_by_source_snapshot_id(
        self,
        source_snapshot_id: str,
    ) -> PaperTradeRecord | None:
        with self.session_factory() as session:
            row = (
                session.query(PaperTradeRow)
                .filter(PaperTradeRow.source_snapshot_id == source_snapshot_id)
                .one_or_none()
            )
            return (
                self._trade_from_row(
                    row,
                    self._latest_execution_facts(session, row.trade_id),
                )
                if row is not None
                else None
            )

    @staticmethod
    def _research_baseline_from_row(
        row: PaperResearchBaselineRow,
    ) -> PaperResearchBaseline:
        definition = json.loads(row.definition_json)
        if not isinstance(definition, dict):
            raise ValueError(f"paper research baseline {row.baseline_id} is malformed")
        return PaperResearchBaseline(
            baseline_id=row.baseline_id,
            provider=row.provider,
            paper_session_id=row.paper_session_id,
            walk_forward_run_id=row.walk_forward_run_id,
            start_date=row.start_date,
            definition_digest=row.definition_digest,
            definition=definition,
            created_at=row.created_at,
        )

    def get_trade(self, trade_id: str) -> PaperTradeRecord | None:
        with self.session_factory() as session:
            row = session.get(PaperTradeRow, trade_id)
            return (
                self._trade_from_row(
                    row,
                    self._latest_execution_facts(session, row.trade_id),
                )
                if row is not None
                else None
            )

    def get_trade_source_context(
        self,
        source_snapshot_id: str,
    ) -> PaperTradeSourceContext | None:
        with self.session_factory() as session:
            trade = (
                session.query(PaperTradeRow)
                .filter(PaperTradeRow.source_snapshot_id == source_snapshot_id)
                .one_or_none()
            )
            if trade is not None:
                event_notes = (
                    session.query(PaperTradeEventRow.note)
                    .filter(PaperTradeEventRow.trade_id == trade.trade_id)
                    .order_by(PaperTradeEventRow.sequence, PaperTradeEventRow.event_id)
                    .all()
                )
                for (note,) in event_notes:
                    context = parse_paper_source_context(note)
                    if context is not None:
                        return context
                return _source_context_from_snapshot(
                    None,
                    source_snapshot_id=source_snapshot_id,
                    signal_date=trade.signal_date,
                    source_status="unknown",
                    fallback_created_at=trade.created_at,
                )

            row = session.get(OpportunitySnapshotRow, source_snapshot_id)
            if row is None:
                return None
            return _source_context_from_snapshot(
                row,
                source_snapshot_id=source_snapshot_id,
                signal_date=None,
                source_status="snapshot_only",
                strategy_configuration=_strategy_configuration_from_snapshot(session, row),
            )

    def list_trades(
        self,
        status: str | None = None,
        limit: int = 100,
        provider: str | None = None,
    ) -> list[PaperTradeRecord]:
        with self.session_factory() as session:
            query = session.query(PaperTradeRow)
            if status:
                query = query.filter(PaperTradeRow.status == status)
            if provider:
                query = query.filter(PaperTradeRow.provider == provider)
            rows = (
                query.order_by(PaperTradeRow.created_at.desc(), PaperTradeRow.trade_id.desc())
                .limit(limit)
                .all()
            )
            facts_by_trade = self._execution_facts_by_trade(
                session,
                [row.trade_id for row in rows],
            )
            return [self._trade_from_row(row, facts_by_trade.get(row.trade_id)) for row in rows]

    def list_trade_events(self, trade_id: str) -> list[PaperTradeEventRecord]:
        with self.session_factory() as session:
            trade = session.get(PaperTradeRow, trade_id)
            if trade is not None and self._ensure_initial_trade_event(session, trade):
                session.commit()
            rows = (
                session.query(PaperTradeEventRow)
                .filter(PaperTradeEventRow.trade_id == trade_id)
                .order_by(PaperTradeEventRow.sequence, PaperTradeEventRow.event_id)
                .all()
            )
            return [self._event_from_row(row) for row in rows]

    def list_replay_evidence_audit(self) -> list[PaperReplayEvidenceAuditRecord]:
        """List forward replay evidence and failures without mutating the session.

        The query is intentionally limited to ``paper_trade_events.note``. Invalid
        payloads, same-phase conflicts, and explicit build-status lines are kept as
        audit failures instead of being dropped from readiness denominators.
        """

        with self.session_factory() as session:
            rows = (
                session.query(PaperTradeEventRow)
                .filter(
                    or_(
                        PaperTradeEventRow.note.contains(PAPER_REPLAY_EVIDENCE_NOTE_PREFIX),
                        PaperTradeEventRow.note.contains(PAPER_REPLAY_EVIDENCE_STATUS_NOTE_PREFIX),
                    )
                )
                .order_by(
                    PaperTradeEventRow.occurred_at,
                    PaperTradeEventRow.event_id,
                )
                .all()
            )
            result: list[PaperReplayEvidenceAuditRecord] = []
            seen_evidence_digests: set[str] = set()
            for row in rows:
                result.extend(
                    _replay_evidence_audit_records(
                        row,
                        seen_evidence_digests=seen_evidence_digests,
                    )
                )
            return result

    def update_trade(
        self,
        trade_id: str,
        *,
        event_metadata: PaperTradeEventMetadata | None = None,
        **changes: object,
    ) -> PaperTradeRecord | None:
        with self.session_factory() as session:
            row = session.get(PaperTradeRow, trade_id)
            if row is None:
                return None
            initialized_event = self._ensure_initial_trade_event(session, row)

            actual_changes: dict[str, object] = {}
            for key, value in changes.items():
                if not hasattr(PaperTradeRow, key):
                    raise ValueError(f"Unknown paper trade field: {key}")
                if getattr(row, key) != value:
                    actual_changes[key] = value

            if not actual_changes:
                if initialized_event:
                    session.commit()
                return self._trade_from_row(
                    row,
                    self._latest_execution_facts(session, trade_id),
                )

            from_status = row.status
            to_status = str(actual_changes.get("status", from_status))
            status_corrected = False
            if from_status != to_status:
                allowed = PAPER_TRADE_STATUS_TRANSITIONS.get(from_status, frozenset())
                status_corrected = self._is_invalid_date_status_correction(
                    row,
                    to_status,
                    actual_changes,
                )
                if to_status not in allowed and not status_corrected:
                    raise ValueError(
                        f"Illegal paper trade status transition: {from_status} -> {to_status}"
                    )

            for key, value in actual_changes.items():
                setattr(row, key, value)

            event_fields = PAPER_TRADE_EVENT_FIELDS.intersection(actual_changes)
            if event_fields:
                trade_date, price = self._event_execution_values(
                    row,
                    to_status=to_status,
                    changed_fields=event_fields,
                )
                if from_status != to_status:
                    event_type = "status_corrected" if status_corrected else "status_changed"
                else:
                    event_type = "execution_updated"
                self._append_trade_event(
                    session,
                    row,
                    sequence=self._next_event_sequence(session, trade_id),
                    event_type=event_type,
                    from_status=from_status,
                    to_status=to_status,
                    default_trade_date=trade_date,
                    default_price=price,
                    default_note=str(actual_changes.get("notes", "")),
                    metadata=event_metadata,
                )
            session.commit()
            session.refresh(row)
            return self._trade_from_row(
                row,
                self._latest_execution_facts(session, trade_id),
            )

    def delete_trade(self, trade_id: str) -> bool:
        with self.session_factory() as session:
            row = session.get(PaperTradeRow, trade_id)
            if row is None:
                return False
            self._ensure_initial_trade_event(session, row)
            self._append_trade_event(
                session,
                row,
                sequence=self._next_event_sequence(session, trade_id),
                event_type="deleted",
                from_status=row.status,
                to_status="deleted",
                default_trade_date=row.latest_date or row.signal_date,
                default_price=row.latest_price or row.trigger_price,
                default_note="Paper trade removed from the active ledger.",
                metadata=PaperTradeEventMetadata(source="paper_repository"),
            )
            session.delete(row)
            session.commit()
            return True

    def clear_trades(self) -> int:
        with self.session_factory() as session:
            rows = session.query(PaperTradeRow).all()
            for row in rows:
                self._ensure_initial_trade_event(session, row)
                self._append_trade_event(
                    session,
                    row,
                    sequence=self._next_event_sequence(session, row.trade_id),
                    event_type="session_reset",
                    from_status=row.status,
                    to_status="deleted",
                    default_trade_date=row.latest_date or row.signal_date,
                    default_price=row.latest_price or row.trigger_price,
                    default_note="Paper account reset removed the trade from the active ledger.",
                    metadata=PaperTradeEventMetadata(source="paper_repository"),
                )
                session.delete(row)
            count = len(rows)
            session.commit()
            return int(count)

    def get_account_settings(self) -> PaperAccountSettings:
        with self.session_factory() as session:
            row = session.get(PaperAccountSettingsRow, "default")
            if row is None:
                return self._default_account_settings()
            return self._account_from_row(row)

    def start_account_session(
        self,
        *,
        label: str,
        initial_capital: Decimal,
        allocation_per_trade_pct: Decimal,
        max_positions: int,
        transaction_cost_bps: Decimal,
        slippage_bps: Decimal,
        take_profit_pct: Decimal,
    ) -> PaperAccountSettings:
        with self.session_factory() as session:
            now = utc_now()
            row = session.get(PaperAccountSettingsRow, "default")
            if row is None:
                row = PaperAccountSettingsRow(
                    account_id="default",
                    session_id=f"paper-session-{uuid4().hex[:12]}",
                    label=label,
                    status="active",
                    initial_capital=initial_capital,
                    allocation_per_trade_pct=allocation_per_trade_pct,
                    max_positions=max_positions,
                    transaction_cost_bps=transaction_cost_bps,
                    slippage_bps=slippage_bps,
                    take_profit_pct=take_profit_pct,
                    started_at=now,
                )
                session.add(row)
            else:
                row.session_id = f"paper-session-{uuid4().hex[:12]}"
                row.label = label
                row.status = "active"
                row.initial_capital = initial_capital
                row.allocation_per_trade_pct = allocation_per_trade_pct
                row.max_positions = max_positions
                row.transaction_cost_bps = transaction_cost_bps
                row.slippage_bps = slippage_bps
                row.take_profit_pct = take_profit_pct
                row.started_at = now
            session.commit()
            session.refresh(row)
            return self._account_from_row(row)

    @staticmethod
    def _trade_from_row(
        row: PaperTradeRow,
        execution_facts: PaperExecutionFacts | None = None,
    ) -> PaperTradeRecord:
        return PaperTradeRecord(
            trade_id=row.trade_id,
            source_snapshot_id=row.source_snapshot_id,
            provider=row.provider,
            instrument_id=row.instrument_id,
            strategy_id=row.strategy_id,
            status=row.status,
            signal_date=row.signal_date,
            trigger_price=row.trigger_price,
            initial_stop=row.initial_stop,
            target_1=row.target_1,
            rank_score=row.rank_score,
            allocation_multiplier=row.allocation_multiplier or Decimal("1.0"),
            entry_date=row.entry_date,
            entry_price=row.entry_price,
            exit_date=row.exit_date,
            exit_price=row.exit_price,
            latest_date=row.latest_date,
            latest_price=row.latest_price,
            unrealized_return_pct=_float_or_none(row.unrealized_return_pct),
            realized_return_pct=_float_or_none(row.realized_return_pct),
            holding_days=row.holding_days,
            notes=row.notes,
            admission_source=row.admission_source or "legacy_unknown",
            production_identity_digest=row.production_identity_digest,
            production_batch_fact_digest=row.production_batch_fact_digest,
            production_selection_item_digest=row.production_selection_item_digest,
            release_proof_digest=row.release_proof_digest,
            execution_facts=execution_facts,
        )

    @staticmethod
    def _event_from_row(row: PaperTradeEventRow) -> PaperTradeEventRecord:
        return PaperTradeEventRecord(
            event_id=row.event_id,
            trade_id=row.trade_id,
            instrument_id=row.instrument_id,
            sequence=row.sequence,
            idempotency_key=row.idempotency_key,
            event_type=row.event_type,
            from_status=row.from_status,
            to_status=row.to_status,
            occurred_at=row.occurred_at,
            trade_date=row.trade_date,
            price=row.price,
            reason_code=row.reason_code,
            note=strip_paper_source_context(row.note),
            source=row.source,
            created_at=row.created_at,
            execution_facts=parse_paper_execution_facts(row.note),
            replay_evidence=parse_paper_replay_evidences(row.note),
        )

    @staticmethod
    def _next_event_sequence(session: Session, trade_id: str) -> int:
        latest = (
            session.query(func.max(PaperTradeEventRow.sequence))
            .filter(PaperTradeEventRow.trade_id == trade_id)
            .scalar()
        )
        return int(latest or 0) + 1

    @staticmethod
    def _append_trade_event(
        session: Session,
        row: PaperTradeRow,
        *,
        sequence: int,
        event_type: str,
        from_status: str | None,
        to_status: str,
        default_trade_date: date | None,
        default_price: Decimal | None,
        default_note: str,
        metadata: PaperTradeEventMetadata | None,
        source_context: PaperTradeSourceContext | None = None,
    ) -> None:
        event_id = f"paper-event-{uuid4().hex}"
        details = metadata or PaperTradeEventMetadata()
        note = details.note or default_note
        if source_context is not None:
            note = encode_paper_source_context(note, source_context)
        if details.execution_facts is not None:
            note = encode_paper_execution_facts(note, details.execution_facts)
        if details.replay_evidence is not None:
            evidences = (
                details.replay_evidence
                if isinstance(details.replay_evidence, tuple)
                else (details.replay_evidence,)
            )
            for evidence in evidences:
                note = encode_paper_replay_evidence(note, evidence)
        if details.replay_evidence_error is not None:
            note = encode_paper_replay_evidence_status(
                note,
                status="build_failed_trade_continued",
                reason=details.replay_evidence_error,
            )
        session.add(
            PaperTradeEventRow(
                event_id=event_id,
                trade_id=row.trade_id,
                instrument_id=row.instrument_id,
                sequence=sequence,
                idempotency_key=details.idempotency_key or event_id,
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                occurred_at=details.occurred_at or utc_now(),
                trade_date=(
                    details.trade_date if details.trade_date is not None else default_trade_date
                ),
                price=details.price if details.price is not None else default_price,
                reason_code=(details.reason_code or f"paper_trade.{event_type}.{to_status}"),
                note=note,
                source=details.source,
            )
        )

    @staticmethod
    def _execution_facts_by_trade(
        session: Session,
        trade_ids: list[str],
    ) -> dict[str, PaperExecutionFacts]:
        if not trade_ids:
            return {}
        rows = (
            session.query(PaperTradeEventRow.trade_id, PaperTradeEventRow.note)
            .filter(PaperTradeEventRow.trade_id.in_(trade_ids))
            .filter(PaperTradeEventRow.note.contains(PAPER_EXECUTION_FACTS_NOTE_PREFIX))
            .order_by(PaperTradeEventRow.sequence, PaperTradeEventRow.event_id)
            .all()
        )
        result: dict[str, PaperExecutionFacts] = {}
        for trade_id, note in rows:
            facts = parse_paper_execution_facts(note)
            if facts is not None:
                result[str(trade_id)] = facts
        return result

    @classmethod
    def _latest_execution_facts(
        cls,
        session: Session,
        trade_id: str,
    ) -> PaperExecutionFacts | None:
        return cls._execution_facts_by_trade(session, [trade_id]).get(trade_id)

    def _ensure_initial_trade_event(
        self,
        session: Session,
        row: PaperTradeRow,
    ) -> bool:
        exists = (
            session.query(PaperTradeEventRow.event_id)
            .filter(PaperTradeEventRow.trade_id == row.trade_id)
            .first()
        )
        if exists is not None:
            return False
        trade_date, price = self._event_execution_values(
            row,
            to_status=row.status,
            changed_fields=PAPER_TRADE_EVENT_FIELDS,
        )
        self._append_trade_event(
            session,
            row,
            sequence=1,
            event_type="legacy_snapshot",
            from_status=None,
            to_status=row.status,
            default_trade_date=trade_date or row.signal_date,
            default_price=price or row.trigger_price,
            default_note="Existing paper trade imported into the event ledger.",
            metadata=PaperTradeEventMetadata(
                reason_code="paper_trade.legacy_snapshot",
                source="schema_backfill",
            ),
        )
        return True

    @staticmethod
    def _event_execution_values(
        row: PaperTradeRow,
        *,
        to_status: str,
        changed_fields: frozenset[str],
    ) -> tuple[date | None, Decimal | None]:
        if to_status == "open" and changed_fields.intersection(
            {"status", "entry_date", "entry_price"}
        ):
            return row.entry_date, row.entry_price
        if to_status in PAPER_TRADE_TERMINAL_STATUSES and changed_fields.intersection(
            {"status", "exit_date", "exit_price"}
        ):
            return (
                row.exit_date or row.latest_date,
                row.exit_price if row.exit_price is not None else row.latest_price,
            )
        if changed_fields.intersection({"latest_date", "latest_price"}):
            return row.latest_date, row.latest_price
        if changed_fields.intersection({"exit_date", "exit_price"}):
            return row.exit_date, row.exit_price
        if changed_fields.intersection({"entry_date", "entry_price"}):
            return row.entry_date, row.entry_price
        return row.latest_date, row.latest_price

    @staticmethod
    def _is_invalid_date_status_correction(
        row: PaperTradeRow,
        to_status: str,
        changes: dict[str, object],
    ) -> bool:
        cleared_fields = {"exit_date", "exit_price", "realized_return_pct"}
        return (
            row.status in PAPER_TRADE_EXECUTED_TERMINAL_STATUSES
            and to_status == "open"
            and row.entry_date is not None
            and row.entry_price is not None
            and row.exit_date is not None
            and row.exit_date < row.entry_date
            and cleared_fields.issubset(changes)
            and all(changes[field] is None for field in cleared_fields)
        )

    @staticmethod
    def _default_account_settings() -> PaperAccountSettings:
        return PaperAccountSettings(
            account_id="default",
            session_id="paper-session-default",
            label="默认模拟盘",
            status="draft",
            initial_capital=Decimal("100000"),
            allocation_per_trade_pct=Decimal("10"),
            max_positions=5,
            transaction_cost_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            take_profit_pct=Decimal("100"),
            started_at=utc_now(),
        )

    @staticmethod
    def _account_from_row(row: PaperAccountSettingsRow) -> PaperAccountSettings:
        return PaperAccountSettings(
            account_id=row.account_id,
            session_id=row.session_id,
            label=row.label,
            status=row.status,
            initial_capital=row.initial_capital,
            allocation_per_trade_pct=row.allocation_per_trade_pct,
            max_positions=row.max_positions,
            transaction_cost_bps=row.transaction_cost_bps,
            slippage_bps=row.slippage_bps,
            take_profit_pct=row.take_profit_pct,
            started_at=row.started_at,
        )


def _float_or_none(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def encode_paper_execution_facts(
    note: str,
    facts: PaperExecutionFacts,
) -> str:
    payload = json.dumps(
        facts.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence = f"{PAPER_EXECUTION_FACTS_NOTE_PREFIX}{payload}"
    return f"{note.rstrip()}\n{evidence}" if note.strip() else evidence


def parse_paper_execution_facts(note: str | None) -> PaperExecutionFacts | None:
    if not note:
        return None
    for line in reversed(note.splitlines()):
        if not line.startswith(PAPER_EXECUTION_FACTS_NOTE_PREFIX):
            continue
        payload = line[len(PAPER_EXECUTION_FACTS_NOTE_PREFIX) :]
        try:
            return PaperExecutionFacts.model_validate_json(payload)
        except (ValueError, TypeError):
            return None
    return None


def encode_paper_replay_evidence(
    note: str,
    evidence: PaperReplayEvidence,
) -> str:
    payload = json.dumps(
        evidence.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    line = f"{PAPER_REPLAY_EVIDENCE_NOTE_PREFIX}{payload}"
    return f"{note.rstrip()}\n{line}" if note.strip() else line


def parse_paper_replay_evidence(note: str | None) -> PaperReplayEvidence | None:
    evidences = parse_paper_replay_evidences(note)
    return evidences[0] if len(evidences) == 1 else None


def parse_paper_replay_evidences(note: str | None) -> tuple[PaperReplayEvidence, ...]:
    if not note:
        return ()
    payloads = [
        line[len(PAPER_REPLAY_EVIDENCE_NOTE_PREFIX) :]
        for line in note.splitlines()
        if line.startswith(PAPER_REPLAY_EVIDENCE_NOTE_PREFIX)
    ]
    if not payloads:
        return ()
    parsed: list[PaperReplayEvidence] = []
    for payload in payloads:
        try:
            parsed.append(PaperReplayEvidence.model_validate_json(payload))
        except (ValueError, TypeError):
            return ()
    by_phase: dict[str, PaperReplayEvidence] = {}
    for item in parsed:
        existing = by_phase.get(item.phase)
        if existing is not None and existing.evidence_digest != item.evidence_digest:
            return ()
        by_phase[item.phase] = item
    return tuple(by_phase[phase] for phase in ("entry", "exit") if phase in by_phase)


def _replay_evidence_audit_records(
    row: PaperTradeEventRow,
    *,
    seen_evidence_digests: set[str],
) -> list[PaperReplayEvidenceAuditRecord]:
    note = row.note or ""
    evidence_payloads = [
        line[len(PAPER_REPLAY_EVIDENCE_NOTE_PREFIX) :]
        for line in note.splitlines()
        if line.startswith(PAPER_REPLAY_EVIDENCE_NOTE_PREFIX)
    ]
    status_payloads = [
        line[len(PAPER_REPLAY_EVIDENCE_STATUS_NOTE_PREFIX) :]
        for line in note.splitlines()
        if line.startswith(PAPER_REPLAY_EVIDENCE_STATUS_NOTE_PREFIX)
    ]
    records: list[PaperReplayEvidenceAuditRecord] = []

    for index, payload in enumerate(status_payloads):
        issue_code = "replay_evidence_status_invalid"
        issue_detail = payload[:160]
        try:
            value = json.loads(payload)
            status = str(value.get("status") or "unknown")
            issue_code = f"replay_evidence_status:{status}"
            issue_detail = str(value.get("reason") or status)[:160]
        except (ValueError, TypeError, AttributeError):
            pass
        records.append(
            _replay_evidence_issue_record(
                row,
                issue_code=issue_code,
                issue_detail=issue_detail,
                discriminator=f"status:{index}:{payload}",
            )
        )

    parsed: list[PaperReplayEvidence] = []
    invalid_payload = False
    for payload in evidence_payloads:
        try:
            parsed.append(PaperReplayEvidence.model_validate_json(payload))
        except (ValueError, TypeError):
            invalid_payload = True
    if invalid_payload:
        records.append(
            _replay_evidence_issue_record(
                row,
                issue_code="replay_evidence_corrupt",
                issue_detail="one or more replay evidence payloads failed validation",
                discriminator="corrupt_evidence",
            )
        )
        return records

    by_phase: dict[str, PaperReplayEvidence] = {}
    conflicting_phases: set[str] = set()
    for evidence in parsed:
        existing = by_phase.get(evidence.phase)
        if existing is not None and existing.evidence_digest != evidence.evidence_digest:
            conflicting_phases.add(evidence.phase)
        else:
            by_phase[evidence.phase] = evidence
    if conflicting_phases:
        for phase in sorted(conflicting_phases):
            records.append(
                _replay_evidence_issue_record(
                    row,
                    issue_code="replay_evidence_conflict",
                    issue_detail=f"conflicting {phase} evidence payloads",
                    discriminator=f"conflict:{phase}",
                )
            )
        return records

    for phase in ("entry", "exit"):
        evidence = by_phase.get(phase)
        if evidence is None or evidence.evidence_digest in seen_evidence_digests:
            continue
        seen_evidence_digests.add(evidence.evidence_digest)
        records.append(
            PaperReplayEvidenceAuditRecord(
                audit_digest=stable_replay_digest(
                    {
                        "event_id": row.event_id,
                        "evidence_digest": evidence.evidence_digest,
                    }
                ),
                event_id=row.event_id,
                trade_id=row.trade_id,
                occurred_at=row.occurred_at,
                evidence=evidence,
            )
        )
    return records


def _replay_evidence_issue_record(
    row: PaperTradeEventRow,
    *,
    issue_code: str,
    issue_detail: str,
    discriminator: str,
) -> PaperReplayEvidenceAuditRecord:
    return PaperReplayEvidenceAuditRecord(
        audit_digest=stable_replay_digest(
            {
                "event_id": row.event_id,
                "issue_code": issue_code,
                "discriminator": discriminator,
            }
        ),
        event_id=row.event_id,
        trade_id=row.trade_id,
        occurred_at=row.occurred_at,
        issue_code=issue_code,
        issue_detail=issue_detail,
    )


def encode_paper_replay_evidence_status(note: str, *, status: str, reason: str) -> str:
    payload = json.dumps(
        {"reason": reason[:160], "status": status},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    line = f"{PAPER_REPLAY_EVIDENCE_STATUS_NOTE_PREFIX}{payload}"
    return f"{note.rstrip()}\n{line}" if note.strip() else line


def encode_paper_source_context(
    note: str,
    context: PaperTradeSourceContext,
) -> str:
    payload = json.dumps(
        context.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence = f"{PAPER_SOURCE_CONTEXT_NOTE_PREFIX}{payload}"
    return f"{note.rstrip()}\n{evidence}" if note.strip() else evidence


def parse_paper_source_context(note: str | None) -> PaperTradeSourceContext | None:
    if not note:
        return None
    for line in reversed(note.splitlines()):
        if not line.startswith(PAPER_SOURCE_CONTEXT_NOTE_PREFIX):
            continue
        payload = line[len(PAPER_SOURCE_CONTEXT_NOTE_PREFIX) :]
        try:
            return PaperTradeSourceContext.model_validate_json(payload)
        except (ValueError, TypeError):
            return None
    return None


def strip_paper_source_context(note: str | None) -> str:
    if not note:
        return ""
    return "\n".join(
        line for line in note.splitlines() if not line.startswith(PAPER_SOURCE_CONTEXT_NOTE_PREFIX)
    ).strip()


def _source_context_from_snapshot(
    snapshot: OpportunitySnapshotRow | None,
    *,
    source_snapshot_id: str,
    signal_date: date | None,
    source_status: str,
    strategy_configuration: tuple[dict[str, object], str] | None = None,
    fallback_market_regime: str | None = None,
    fallback_created_at: datetime | None = None,
) -> PaperTradeSourceContext:
    card = _snapshot_card(snapshot)
    market_context = _object_mapping(card.get("market_context"))
    industry = _normalized_dimension_value(
        market_context.get("industry") or card.get("industry") or card.get("sector")
    )
    themes = _normalized_text_list(
        market_context.get("themes") or card.get("themes") or card.get("theme")
    )
    return PaperTradeSourceContext(
        source_snapshot_id=source_snapshot_id,
        source_run_id=snapshot.run_id if snapshot is not None else None,
        created_at=(
            snapshot.created_at if snapshot is not None else fallback_created_at or utc_now()
        ),
        signal_date=signal_date or (snapshot.signal_date if snapshot is not None else None),
        latest_close=snapshot.latest_close if snapshot is not None else None,
        industry=industry,
        themes=themes,
        market_regime=_source_market_regime(
            card,
            fallback=fallback_market_regime,
        ),
        factor_ids=_source_factor_ids(card),
        source_status=source_status,
        strategy_configuration=(strategy_configuration[0] if strategy_configuration else {}),
        strategy_configuration_digest=(
            strategy_configuration[1] if strategy_configuration else None
        ),
        strategy_configuration_status=("frozen" if strategy_configuration else "legacy_unfrozen"),
        card=card,
    )


def _strategy_configuration_from_snapshot(
    session: Session,
    snapshot: OpportunitySnapshotRow | None,
) -> tuple[dict[str, object], str] | None:
    if snapshot is None:
        return None
    run = session.get(ScanRunRow, snapshot.run_id)
    if run is None:
        return None
    try:
        health = json.loads(run.data_health or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(health, dict):
        return None
    return parse_paper_strategy_configuration(
        health.get("paper_strategy_configuration_json"),
        health.get("paper_strategy_configuration_digest"),
    )


def _market_regime_from_snapshot_run(
    session: Session,
    snapshot: OpportunitySnapshotRow | None,
) -> str | None:
    if snapshot is None:
        return None
    run = session.get(ScanRunRow, snapshot.run_id)
    if run is None:
        return None
    try:
        health = json.loads(run.data_health or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(health, dict):
        return None
    raw_regime = health.get("market_intelligence_regime")
    if not isinstance(raw_regime, str):
        return None
    regime = _normalized_dimension_value(raw_regime)
    return regime if regime in {"risk_on", "constructive", "mixed", "risk_off", "thin"} else None


def _snapshot_card(snapshot: OpportunitySnapshotRow | None) -> dict[str, object]:
    if snapshot is None:
        return {}
    try:
        value = json.loads(snapshot.card_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _paper_admission_tuple(
    value: PaperTradeRow | PaperTradeAdmissionProof,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    return (
        str(getattr(value, "admission_source", None) or "legacy_unknown"),
        getattr(value, "production_identity_digest", None),
        getattr(value, "production_batch_fact_digest", None),
        getattr(value, "production_selection_item_digest", None),
        getattr(value, "release_proof_digest", None),
    )


def _require_production_trade_matches_snapshot(
    session: Session,
    snapshot: OpportunitySnapshotRow | None,
    *,
    provider: str,
    instrument_id: str,
    strategy_id: str | None,
    signal_date: date,
    trigger_price: Decimal,
    initial_stop: Decimal | None,
    target_1: Decimal | None,
    rank_score: Decimal | None,
    allocation_multiplier: Decimal,
) -> None:
    if snapshot is None:
        raise ValueError("Ranking V3 production trade requires its immutable source snapshot")
    run = session.get(ScanRunRow, snapshot.run_id)
    if run is None:
        raise ValueError("Ranking V3 production trade requires its authoritative scan run")
    facts_match = (
        run.provider == provider
        and snapshot.instrument_id == instrument_id
        and snapshot.primary_strategy_id == strategy_id
        and snapshot.signal_date == signal_date
        and _decimal_equal(snapshot.trigger_price, trigger_price)
        and _decimal_equal(snapshot.initial_stop, initial_stop)
        and _decimal_equal(snapshot.target_1, target_1)
        and _decimal_equal(snapshot.rank_score, rank_score)
    )
    if not facts_match:
        raise ValueError(
            "Ranking V3 production trade plan does not match its immutable source snapshot"
        )
    if allocation_multiplier <= 0 or allocation_multiplier > 1:
        raise ValueError("Ranking V3 production allocation multiplier must be between zero and one")


def _require_existing_production_plan_matches(
    existing: PaperTradeRow,
    *,
    provider: str,
    instrument_id: str,
    strategy_id: str | None,
    signal_date: date,
    trigger_price: Decimal,
    initial_stop: Decimal | None,
    target_1: Decimal | None,
    rank_score: Decimal | None,
    allocation_multiplier: Decimal,
) -> None:
    facts_match = (
        existing.provider == provider
        and existing.instrument_id == instrument_id
        and existing.strategy_id == strategy_id
        and existing.signal_date == signal_date
        and _decimal_equal(existing.trigger_price, trigger_price)
        and _decimal_equal(existing.initial_stop, initial_stop)
        and _decimal_equal(existing.target_1, target_1)
        and _decimal_equal(existing.rank_score, rank_score)
        and _decimal_equal(existing.allocation_multiplier, allocation_multiplier)
    )
    if not facts_match:
        raise ValueError(
            "source snapshot is already bound to a different immutable production plan"
        )


def _decimal_equal(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return Decimal(left) == Decimal(right)


def _source_market_regime(
    card: dict[str, object],
    *,
    fallback: str | None = None,
) -> str:
    candidates = [
        card.get("market_regime"),
        card.get("market_environment"),
        card.get("market_state"),
        fallback,
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("regime") or candidate.get("state") or candidate.get("CN")
            if isinstance(candidate, dict):
                candidate = candidate.get("regime") or candidate.get("state")
        normalized = _normalized_dimension_value(candidate)
        if normalized != "unknown":
            return normalized
    return "unknown"


def _source_factor_ids(card: dict[str, object]) -> list[str]:
    factor_ids = _normalized_text_list(card.get("factor_flags"))
    exposures = card.get("factor_exposures")
    if isinstance(exposures, list):
        factor_ids.extend(
            str(exposure.get("factor_id")).strip()
            for exposure in exposures
            if isinstance(exposure, dict) and exposure.get("factor_id")
        )
    enhanced = _object_mapping(card.get("a_share_enhanced"))
    factor_ids.extend(_normalized_text_list(enhanced.get("signals")))
    return sorted(set(item for item in factor_ids if item and item != "unknown"))


def _object_mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _normalized_text_list(value: object) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    normalized = []
    for item in values:
        text = str(item).strip()
        if text and text.lower() not in {"none", "null", "unknown", "未分类"}:
            normalized.append(text)
    return sorted(set(normalized))


def _normalized_dimension_value(value: object) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text if text and text.lower() not in {"none", "null", "unknown", "未分类"} else "unknown"
