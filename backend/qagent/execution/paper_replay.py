from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from qagent.execution.engine import apply_market_event, apply_order_intent
from qagent.execution.events import canonical_digest
from qagent.execution.models import (
    AShareExecutionRules,
    Account,
    ExecutionState,
    Fill,
    FrozenModel,
    MarketEvent,
    OrderIntent,
    OrderSide,
    OrderType,
    Position,
    TimeInForce,
)
from qagent.execution.rules import fee_breakdown, is_tick_aligned, money
from qagent.execution.replay_evidence import PaperReplayEvidence


PAPER_EXECUTION_FACTS_PREFIX = "[paper_execution_facts:v1]"


class ReplayVerdict(StrEnum):
    MATCHED = "matched"
    EXPLAINED_DIFFERENCE = "explained_difference"
    UNREPLAYABLE = "unreplayable"


class ReplayEvidenceVerdict(StrEnum):
    MATCHED = "matched"
    EXPLAINED_DIFFERENCE = "explained_difference"
    UNKNOWN_OR_UNREPLAYABLE = "unknown_or_unreplayable"


class MarketGranularity(StrEnum):
    DAILY = "daily"
    MINUTE = "minute"


class PaperReplayLeg(FrozenModel):
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

    @model_validator(mode="after")
    def validate_cash_contract(self):
        if self.gross_amount != self.price * self.quantity:
            raise ValueError("gross_amount must equal price times quantity")
        fees = self.commission + self.stamp_duty + self.transfer_fee
        expected = (
            -(self.gross_amount + fees) if self.side == OrderSide.BUY else self.gross_amount - fees
        )
        if self.cash_flow != expected:
            raise ValueError("cash_flow does not match side, gross amount, and fees")
        return self


class PaperReplayFacts(FrozenModel):
    schema_version: str = "paper-execution-facts-v1"
    allocation: Decimal = Field(gt=0)
    rules: AShareExecutionRules
    entry: PaperReplayLeg
    exit: PaperReplayLeg | None = None

    @model_validator(mode="after")
    def validate_contract(self):
        if self.schema_version != "paper-execution-facts-v1":
            raise ValueError("unsupported paper execution facts schema")
        if self.entry.side != OrderSide.BUY:
            raise ValueError("entry must be a buy")
        if self.exit is not None:
            if self.exit.side != OrderSide.SELL:
                raise ValueError("exit must be a sell")
            if self.exit.quantity != self.entry.quantity:
                raise ValueError("exit must close the frozen entry quantity")
        for leg in (self.entry, self.exit):
            if leg is None:
                continue
            minimum = self.rules.effective_minimum_order_quantity
            step = self.rules.effective_quantity_step
            if leg.quantity < minimum or (leg.quantity - minimum) % step:
                raise ValueError("quantity does not respect frozen lot rules")
            if not is_tick_aligned(leg.price, self.rules.tick_size):
                raise ValueError("price does not respect frozen tick rules")
        return self


class MarketEvidence(FrozenModel):
    granularity: MarketGranularity
    provider_mode: str
    source_provider: str
    cached_at: str
    trade_date: date
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: int = Field(ge=0)


class PaperReplaySample(FrozenModel):
    sample_key: str
    instrument_id: str = Field(exclude=True)
    trade_status: str
    trigger_price: Decimal | None = None
    initial_stop: Decimal | None = None
    target_1: Decimal | None = None
    facts: PaperReplayFacts | None
    entry_market: tuple[MarketEvidence, ...] = ()
    exit_market: tuple[MarketEvidence, ...] = ()
    entry_replay_evidence: PaperReplayEvidence | None = None
    exit_replay_evidence: PaperReplayEvidence | None = None
    load_issues: tuple[str, ...] = ()


class ReplayDifference(FrozenModel):
    code: str
    field: str | None = None
    paper_value: object | None = None
    replay_value: object | None = None


class PerLegComparison(FrozenModel):
    side: OrderSide
    verdict: ReplayVerdict
    paper_digest: str
    replay_digest: str | None = None
    differences: tuple[ReplayDifference, ...] = ()
    evidence: MarketEvidence | None = None


class PerTradeReplayReport(FrozenModel):
    sample_key: str
    verdict: ReplayVerdict
    input_digest: str
    replay_digest: str
    entry: PerLegComparison | None
    exit: PerLegComparison | None = None
    classifications: tuple[str, ...] = ()


class PaperReplayBatchSummary(FrozenModel):
    sample_count: int
    matched: int
    explained_difference: int
    unreplayable: int
    classification_counts: dict[str, int]
    batch_digest: str


class PaperReplayEvidenceReport(FrozenModel):
    """Independent kernel comparison for one immutable forward-evidence sample."""

    evidence_digest: str
    side: OrderSide
    verdict: ReplayEvidenceVerdict
    classifications: tuple[str, ...] = ()
    differences: tuple[ReplayDifference, ...] = ()
    expected_fill_digest: str
    replay_fill_digest: str | None = None
    report_digest: str


class _DigestPayload(FrozenModel):
    value: object


def parse_execution_facts_payload(payload: str) -> PaperReplayFacts:
    """Parse an explicit JSON payload without touching paper storage or a database."""

    value = json.loads(payload)
    return PaperReplayFacts.model_validate(value)


def replay_paper_evidence(evidence: PaperReplayEvidence) -> PaperReplayEvidenceReport:
    """Replay exactly one evidence item without storage, clocks, or paper-ledger state.

    Buy samples get deliberately ample cash. Sell samples get a pre-existing
    prior-session position and advance into the evidence session, so A-share T+1
    settlement is performed by the kernel without depending on another sample.
    """

    expected = evidence.expected_fill
    account_id = "offline-paper-evidence-replay"
    if evidence.order.side == OrderSide.BUY:
        account = Account(account_id=account_id, cash=Decimal("1000000000000"))
        state = ExecutionState(account=account)
    else:
        position = Position(
            account_id=account_id,
            instrument_id=evidence.order.instrument_id,
            quantity=evidence.order.quantity,
            sellable_quantity=0,
            average_cost=expected.base_price,
            cost_basis=money(expected.base_price * evidence.order.quantity),
            last_fill_at=evidence.market.occurred_at - timedelta(days=1),
        )
        account = Account(
            account_id=account_id,
            cash=Decimal("0"),
            positions={evidence.order.instrument_id: position},
        )
        state = ExecutionState(
            account=account,
            session_date=evidence.market.trading_date - timedelta(days=1),
        )
    paper = PaperReplayLeg(
        **expected.model_dump(exclude={"instrument_id"}),
        source="paper_replay_evidence",
    )
    sample = PaperReplaySample(
        sample_key=evidence.evidence_digest,
        instrument_id=evidence.order.instrument_id,
        trade_status="time_exit" if evidence.order.side == OrderSide.SELL else "open",
        facts=None,
    )
    comparison, _ = _replay_leg(
        state,
        sample,
        paper,
        _market_evidence_from_explicit(evidence),
        _explicit_order_contract(evidence),
        rules=evidence.rules,
        explicit_evidence=evidence,
    )
    verdict = {
        ReplayVerdict.MATCHED: ReplayEvidenceVerdict.MATCHED,
        ReplayVerdict.EXPLAINED_DIFFERENCE: ReplayEvidenceVerdict.EXPLAINED_DIFFERENCE,
        ReplayVerdict.UNREPLAYABLE: ReplayEvidenceVerdict.UNKNOWN_OR_UNREPLAYABLE,
    }[comparison.verdict]
    classifications = tuple(sorted({item.code for item in comparison.differences}))
    digest_payload = {
        "evidence_digest": evidence.evidence_digest,
        "side": evidence.order.side,
        "verdict": verdict,
        "classifications": classifications,
        "differences": comparison.differences,
        "expected_fill_digest": evidence.expected_fill_digest,
        "replay_fill_digest": comparison.replay_digest,
    }
    return PaperReplayEvidenceReport(
        **digest_payload,
        report_digest=_digest(digest_payload),
    )


def replay_paper_sample(sample: PaperReplaySample) -> PerTradeReplayReport:
    if sample.load_issues:
        return _unreplayable_report(sample, sample.load_issues[0])
    if sample.facts is None:
        return _unreplayable_report(sample, "execution_facts_unavailable")
    facts = sample.facts
    if _is_legacy_inferred(facts.entry):
        return _unreplayable_report(sample, "legacy_inferred_execution_unreplayable")
    entry_evidence = sample.entry_replay_evidence
    if entry_evidence is not None:
        entry_issue = _explicit_evidence_issue(
            entry_evidence, facts.entry, sample.instrument_id, "entry"
        )
        if entry_issue is not None:
            return _unreplayable_report(sample, entry_issue)
        entry_market = _market_evidence_from_explicit(entry_evidence)
        entry_order = _explicit_order_contract(entry_evidence)
        entry_rules = entry_evidence.rules
    else:
        entry_market, entry_issue = _single_market(sample.entry_market, "entry")
        if entry_issue is not None:
            return _unreplayable_report(sample, entry_issue)
        assert entry_market is not None
        entry_granularity_issue = _market_granularity_issue(facts.entry, entry_market, "entry")
        if entry_granularity_issue is not None:
            return _unreplayable_report(sample, entry_granularity_issue)
        entry_order = _order_contract(sample, facts.entry)
        if entry_order is None:
            return _unreplayable_report(sample, "entry_order_contract_missing")
        entry_rules = facts.rules

    required = money(
        facts.entry.base_price * facts.entry.quantity
        + fee_breakdown(
            OrderSide.BUY,
            facts.entry.base_price * facts.entry.quantity,
            entry_rules,
        ).total
    )
    initial_cash = max(facts.allocation, required, -facts.entry.cash_flow)
    state = ExecutionState(account=Account(account_id="offline-paper-replay", cash=initial_cash))
    entry_comparison, state = _replay_leg(
        state,
        sample,
        facts.entry,
        entry_market,
        entry_order,
        rules=entry_rules,
        explicit_evidence=entry_evidence,
    )
    classifications = [difference.code for difference in entry_comparison.differences]
    if facts.allocation < required:
        entry_comparison = entry_comparison.model_copy(
            update={
                "verdict": ReplayVerdict.EXPLAINED_DIFFERENCE,
                "differences": entry_comparison.differences
                + (
                    ReplayDifference(
                        code="paper_allocation_omits_unified_cash_reservation",
                        field="allocation",
                        paper_value=facts.allocation,
                        replay_value=required,
                    ),
                ),
            }
        )
        classifications.append("paper_allocation_omits_unified_cash_reservation")

    exit_comparison: PerLegComparison | None = None
    if facts.exit is None:
        classifications.append("open_trade_exit_facts_absent")
    elif entry_comparison.verdict != ReplayVerdict.UNREPLAYABLE:
        if _is_legacy_inferred(facts.exit):
            exit_comparison = _unreplayable_leg(
                facts.exit, "legacy_inferred_execution_unreplayable"
            )
            classifications.append("legacy_inferred_execution_unreplayable")
        else:
            exit_evidence = sample.exit_replay_evidence
            if exit_evidence is not None:
                exit_issue = _explicit_evidence_issue(
                    exit_evidence, facts.exit, sample.instrument_id, "exit"
                )
                exit_market = _market_evidence_from_explicit(exit_evidence)
                exit_order = _explicit_order_contract(exit_evidence)
                exit_rules = exit_evidence.rules
            else:
                exit_market, exit_issue = _single_market(sample.exit_market, "exit")
                exit_order = _order_contract(sample, facts.exit)
                exit_rules = facts.rules
            if exit_issue is not None:
                exit_comparison = _unreplayable_leg(facts.exit, exit_issue)
            elif exit_order is None:
                exit_comparison = _unreplayable_leg(facts.exit, "exit_order_contract_missing")
            else:
                assert exit_market is not None
                exit_granularity_issue = (
                    None
                    if exit_evidence is not None
                    else _market_granularity_issue(facts.exit, exit_market, "exit")
                )
                if exit_granularity_issue is not None:
                    exit_comparison = _unreplayable_leg(facts.exit, exit_granularity_issue)
                else:
                    exit_comparison, state = _replay_leg(
                        state,
                        sample,
                        facts.exit,
                        exit_market,
                        exit_order,
                        rules=exit_rules,
                        explicit_evidence=exit_evidence,
                    )
        assert exit_comparison is not None
        classifications.extend(item.code for item in exit_comparison.differences)
        if facts.exit.trade_date != facts.entry.trade_date and (
            sample.entry_replay_evidence is None or sample.exit_replay_evidence is None
        ):
            classifications.append("v1_single_rules_snapshot_cross_date")

    legacy_inferred = facts.entry.source == "legacy_inferred" or (
        facts.exit is not None and facts.exit.source == "legacy_inferred"
    )
    if facts.entry.source != "unified_execution" or (
        facts.exit is not None and facts.exit.source != "unified_execution"
    ):
        classifications.append("non_unified_or_inferred_audit_source")
    if legacy_inferred:
        classifications.append("legacy_inferred_execution_unreplayable")

    classifications = sorted(set(classifications))
    legs = (entry_comparison,) if exit_comparison is None else (entry_comparison, exit_comparison)
    if (
        facts.exit is None
        or legacy_inferred
        or any(leg.verdict == ReplayVerdict.UNREPLAYABLE for leg in legs)
    ):
        verdict = ReplayVerdict.UNREPLAYABLE
    elif classifications or any(leg.verdict == ReplayVerdict.EXPLAINED_DIFFERENCE for leg in legs):
        verdict = ReplayVerdict.EXPLAINED_DIFFERENCE
    else:
        verdict = ReplayVerdict.MATCHED
    report_payload = {
        "sample_key": sample.sample_key,
        "entry": entry_comparison,
        "exit": exit_comparison,
        "classifications": classifications,
    }
    return PerTradeReplayReport(
        sample_key=sample.sample_key,
        verdict=verdict,
        input_digest=canonical_digest(sample),
        replay_digest=_digest(report_payload),
        entry=entry_comparison,
        exit=exit_comparison,
        classifications=tuple(classifications),
    )


def summarize_paper_replays(
    reports: tuple[PerTradeReplayReport, ...],
) -> PaperReplayBatchSummary:
    verdicts = Counter(report.verdict for report in reports)
    classifications = Counter(
        classification for report in reports for classification in report.classifications
    )
    digest_payload = tuple(
        (report.sample_key, report.verdict, report.input_digest, report.replay_digest)
        for report in reports
    )
    return PaperReplayBatchSummary(
        sample_count=len(reports),
        matched=verdicts[ReplayVerdict.MATCHED],
        explained_difference=verdicts[ReplayVerdict.EXPLAINED_DIFFERENCE],
        unreplayable=verdicts[ReplayVerdict.UNREPLAYABLE],
        classification_counts=dict(sorted(classifications.items())),
        batch_digest=_digest(digest_payload),
    )


def _single_market(
    evidence: tuple[MarketEvidence, ...], phase: str
) -> tuple[MarketEvidence | None, str | None]:
    if not evidence:
        return None, f"{phase}_market_evidence_missing"
    if len(evidence) != 1:
        return None, f"{phase}_market_provider_ambiguous"
    return evidence[0], None


def _market_granularity_issue(
    leg: PaperReplayLeg, evidence: MarketEvidence, phase: str
) -> str | None:
    expected = _fact_granularity(leg)
    if expected is None:
        return f"{phase}_market_granularity_unknown"
    if evidence.granularity != expected:
        return f"{phase}_market_granularity_insufficient"
    return None


def _fact_granularity(leg: PaperReplayLeg) -> MarketGranularity | None:
    if ":minute:" in leg.market_event_id:
        return MarketGranularity.MINUTE
    if ":daily:" in leg.market_event_id:
        return MarketGranularity.DAILY
    return None


def _is_legacy_inferred(leg: PaperReplayLeg) -> bool:
    return leg.source == "legacy_inferred" or ":legacy-entry" in leg.market_event_id


def _explicit_evidence_issue(
    evidence: PaperReplayEvidence,
    leg: PaperReplayLeg,
    instrument_id: str,
    phase: str,
) -> str | None:
    if evidence.phase != phase:
        return f"{phase}_replay_evidence_phase_mismatch"
    if evidence.market.instrument_id != instrument_id:
        return f"{phase}_replay_evidence_instrument_mismatch"
    expected = evidence.expected_fill
    fields = (
        "market_event_id",
        "side",
        "trade_date",
        "base_price",
        "price",
        "quantity",
        "gross_amount",
        "commission",
        "stamp_duty",
        "transfer_fee",
        "slippage",
        "cash_flow",
    )
    if any(getattr(expected, field) != getattr(leg, field) for field in fields):
        return f"{phase}_replay_evidence_fill_mismatch"
    return None


def _market_evidence_from_explicit(evidence: PaperReplayEvidence) -> MarketEvidence:
    market = evidence.market
    granularity = (
        MarketGranularity.MINUTE if ":minute:" in market.event_id else MarketGranularity.DAILY
    )
    return MarketEvidence(
        granularity=granularity,
        provider_mode="paper_replay_evidence",
        source_provider="unified_execution",
        cached_at=market.occurred_at.isoformat(),
        trade_date=market.trading_date,
        open=market.open,
        high=market.high,
        low=market.low,
        close=market.close,
        volume=market.volume,
    )


def _explicit_order_contract(
    evidence: PaperReplayEvidence,
) -> tuple[OrderType, Decimal | None, Decimal | None]:
    order = evidence.order
    return order.order_type, order.limit_price, order.stop_price


def _order_contract(
    sample: PaperReplaySample, leg: PaperReplayLeg
) -> tuple[OrderType, Decimal | None, Decimal | None] | None:
    if leg.side == OrderSide.BUY:
        if sample.trigger_price is None:
            return None
        return OrderType.STOP, None, sample.trigger_price
    if sample.trade_status == "stopped" and sample.initial_stop is not None:
        return OrderType.STOP, None, sample.initial_stop
    if sample.trade_status == "target_1_hit" and sample.target_1 is not None:
        return OrderType.LIMIT, sample.target_1, None
    if sample.trade_status == "time_exit":
        return OrderType.MARKET, None, None
    return None


def _replay_leg(
    state: ExecutionState,
    sample: PaperReplaySample,
    paper: PaperReplayLeg,
    evidence: MarketEvidence,
    order_contract: tuple[OrderType, Decimal | None, Decimal | None],
    *,
    rules: AShareExecutionRules,
    explicit_evidence: PaperReplayEvidence | None = None,
) -> tuple[PerLegComparison, ExecutionState]:
    order_type, limit_price, stop_price = order_contract
    occurred_at = (
        explicit_evidence.market.occurred_at
        if explicit_evidence is not None
        else datetime.combine(paper.trade_date, time(15, 0), tzinfo=timezone.utc)
    )
    submitted_at = (
        explicit_evidence.order.submitted_at
        if explicit_evidence is not None
        else occurred_at - timedelta(seconds=1)
    )
    intent = OrderIntent(
        intent_id=f"offline:{sample.sample_key}:{paper.side.value}:{paper.trade_date}",
        account_id=state.account.account_id,
        instrument_id=sample.instrument_id,
        side=paper.side,
        quantity=(
            explicit_evidence.order.quantity if explicit_evidence is not None else paper.quantity
        ),
        submitted_at=submitted_at,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        estimated_price=(
            explicit_evidence.order.estimated_price
            if explicit_evidence is not None
            else paper.base_price
        ),
        time_in_force=(
            explicit_evidence.order.time_in_force
            if explicit_evidence is not None
            else TimeInForce.DAY
        ),
    )
    market = (
        explicit_evidence.market
        if explicit_evidence is not None
        else MarketEvent(
            event_id=f"offline-market:{sample.sample_key}:{paper.side.value}:{paper.trade_date}",
            instrument_id=sample.instrument_id,
            occurred_at=occurred_at,
            trading_date=paper.trade_date,
            open=evidence.open,
            high=evidence.high,
            low=evidence.low,
            close=evidence.close,
            volume=evidence.volume,
        )
    )
    try:
        if (
            paper.side == OrderSide.SELL
            and state.session_date is not None
            and state.session_date < paper.trade_date
        ):
            session_market = market.model_copy(
                update={
                    "event_id": f"{market.event_id}:session-open",
                    "occurred_at": datetime.combine(
                        paper.trade_date, time(9, 0), tzinfo=timezone.utc
                    ),
                }
            )
            state = apply_market_event(state, session_market).state
        state = apply_order_intent(state, intent, rules).state
        before = len(state.fills)
        state = apply_market_event(state, market).state
    except (ValueError, AssertionError) as exc:
        return _unreplayable_leg(paper, f"kernel_rejected_{type(exc).__name__}"), state
    if len(state.fills) == before:
        return _unreplayable_leg(paper, "kernel_did_not_fill"), state
    fill = state.fills[-1]
    differences = _compare_fill(paper, fill)
    unexplained = any(
        item.code not in {"paper_fee_model_difference", "paper_cash_flow_difference"}
        for item in differences
    )
    verdict = (
        ReplayVerdict.UNREPLAYABLE
        if unexplained
        else ReplayVerdict.EXPLAINED_DIFFERENCE
        if differences
        else ReplayVerdict.MATCHED
    )
    return (
        PerLegComparison(
            side=paper.side,
            verdict=verdict,
            paper_digest=canonical_digest(paper),
            replay_digest=canonical_digest(fill),
            differences=differences,
            evidence=evidence,
        ),
        state,
    )


def _compare_fill(paper: PaperReplayLeg, fill: Fill) -> tuple[ReplayDifference, ...]:
    fields = (
        "base_price",
        "price",
        "quantity",
        "gross_amount",
        "commission",
        "stamp_duty",
        "transfer_fee",
        "slippage",
    )
    differences: list[ReplayDifference] = []
    for field in fields:
        paper_value = getattr(paper, field)
        replay_value = getattr(fill, field)
        if paper_value == replay_value:
            continue
        code = (
            "paper_fee_model_difference"
            if field in {"commission", "stamp_duty", "transfer_fee"}
            else "execution_difference_unexplained"
        )
        differences.append(
            ReplayDifference(
                code=code,
                field=field,
                paper_value=paper_value,
                replay_value=replay_value,
            )
        )
    if paper.cash_flow != fill.net_cash_flow:
        differences.append(
            ReplayDifference(
                code="paper_cash_flow_difference",
                field="cash_flow",
                paper_value=paper.cash_flow,
                replay_value=fill.net_cash_flow,
            )
        )
    return tuple(differences)


def _unreplayable_leg(paper: PaperReplayLeg, code: str) -> PerLegComparison:
    return PerLegComparison(
        side=paper.side,
        verdict=ReplayVerdict.UNREPLAYABLE,
        paper_digest=canonical_digest(paper),
        differences=(ReplayDifference(code=code),),
    )


def _unreplayable_report(sample: PaperReplaySample, code: str) -> PerTradeReplayReport:
    entry = _unreplayable_leg(sample.facts.entry, code) if sample.facts is not None else None
    payload = {"sample_key": sample.sample_key, "code": code}
    return PerTradeReplayReport(
        sample_key=sample.sample_key,
        verdict=ReplayVerdict.UNREPLAYABLE,
        input_digest=canonical_digest(sample),
        replay_digest=_digest(payload),
        entry=entry,
        classifications=(code,),
    )


def _digest(value: object) -> str:
    return canonical_digest(_DigestPayload(value=value))
