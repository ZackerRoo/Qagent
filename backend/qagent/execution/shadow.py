from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from qagent.execution.engine import apply_market_event, apply_order_intent
from qagent.execution.events import canonical_digest
from qagent.execution.fees import ResolvedFeeTerms, apply_resolved_fee
from qagent.execution.models import (
    AShareExecutionRules,
    ExecutionState,
    FrozenModel,
    MarketEvent,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
)
from qagent.execution.rules import (
    apply_slippage,
    fee_breakdown,
    is_one_price_limit_blocked,
    is_tick_aligned,
    match_base_price,
    money,
    participation_capacity,
)


class ExecutionRuleCoverageError(ValueError):
    """Raised when a fixture would silently apply rules outside their validity."""


class ShadowDiffCategory(StrEnum):
    ORDER_LIFECYCLE = "order_lifecycle"
    QUANTITY_MODEL = "quantity_model"
    PRICE_MODEL = "price_model"
    FEE_MODEL = "fee_model"
    ACCOUNTING = "accounting"
    AUDIT = "audit"


class FeePolicyAudit(FrozenModel):
    fee_schedule_version: str
    fee_rule_key: str
    account_config_version: str
    rounding_rule: str | None = None
    rounding_applied: bool = False


class ExecutionShadowFixture(FrozenModel):
    """Synthetic, I/O-free input for a paper-versus-unified execution comparison."""

    fixture_id: str = Field(min_length=1)
    initial_state: ExecutionState
    intent: OrderIntent
    pre_market_events: tuple[MarketEvent, ...] = ()
    market_events: tuple[MarketEvent, ...]
    paper_rules: AShareExecutionRules
    candidate_rules: AShareExecutionRules
    candidate_fee_audit: FeePolicyAudit | None = None
    rules_valid_from: date
    rules_valid_to: date

    @model_validator(mode="after")
    def validate_fixture(self):
        if self.rules_valid_from > self.rules_valid_to:
            raise ValueError("rules_valid_from must not be after rules_valid_to")
        if self.intent.account_id != self.initial_state.account.account_id:
            raise ValueError("intent account_id must match initial_state")
        return self


class ExecutionShadowOutcome(FrozenModel):
    status: str
    reason: str | None = None
    filled_quantity: int = 0
    fill_count: int = 0
    average_fill_price: Decimal | None = None
    gross_amount: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    stamp_duty: Decimal = Decimal("0")
    transfer_fee: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    cash: Decimal
    frozen_cash: Decimal = Decimal("0")
    position_quantity: int = 0
    sellable_quantity: int = 0
    processed_market_events: int = 0


class ExecutionShadowDifference(FrozenModel):
    field: str
    paper_value: object
    candidate_value: object
    category: ShadowDiffCategory


class ExecutionShadowReport(FrozenModel):
    fixture_id: str
    input_digest: str
    paper_digest: str
    candidate_digest: str
    paper: ExecutionShadowOutcome
    candidate: ExecutionShadowOutcome
    candidate_fee_audit: FeePolicyAudit | None = None
    differences: tuple[ExecutionShadowDifference, ...]
    classifications: tuple[ShadowDiffCategory, ...]

    @property
    def matched(self) -> bool:
        return not self.differences


def compare_execution_shadow(fixture: ExecutionShadowFixture) -> ExecutionShadowReport:
    """Compare current paper matching semantics with the unified execution kernel.

    The function is deliberately pure: callers provide a synthetic state, intent,
    market events and an explicit validity window.  It has no storage, clock, API,
    scheduler or service dependency.
    """

    _require_rule_coverage(fixture)
    paper = _run_current_paper_semantics(fixture)
    candidate = _run_unified_candidate(fixture)
    differences = _differences(paper, candidate)
    classifications = tuple(sorted({item.category for item in differences}))
    return ExecutionShadowReport(
        fixture_id=fixture.fixture_id,
        input_digest=canonical_digest(fixture),
        paper_digest=canonical_digest(paper),
        candidate_digest=canonical_digest(candidate),
        paper=paper,
        candidate=candidate,
        candidate_fee_audit=fixture.candidate_fee_audit,
        differences=differences,
        classifications=classifications,
    )


def with_resolved_candidate_fee(
    fixture: ExecutionShadowFixture,
    resolved: ResolvedFeeTerms,
) -> ExecutionShadowFixture:
    """Build a pure shadow fixture whose candidate fees came from a fee policy."""

    if resolved.trade_date != fixture.intent.submitted_at.date():
        raise ValueError("resolved fee trade_date must match fixture intent date")
    if resolved.side != fixture.intent.side:
        raise ValueError("resolved fee side must match fixture intent side")
    return fixture.model_copy(
        update={
            "candidate_rules": apply_resolved_fee(fixture.candidate_rules, resolved),
            "candidate_fee_audit": FeePolicyAudit(
                fee_schedule_version=resolved.fee_schedule_version,
                fee_rule_key=resolved.fee_rule_key,
                account_config_version=resolved.account_config_version,
                rounding_rule=resolved.rounding_rule,
                rounding_applied=resolved.rounding_applied,
            ),
        }
    )


def _require_rule_coverage(fixture: ExecutionShadowFixture) -> None:
    relevant_dates = {
        fixture.intent.submitted_at.date(),
        *(event.trading_date for event in fixture.pre_market_events),
        *(event.trading_date for event in fixture.market_events),
    }
    uncovered = sorted(
        trade_date
        for trade_date in relevant_dates
        if trade_date is None
        or not fixture.rules_valid_from <= trade_date <= fixture.rules_valid_to
    )
    if uncovered:
        rendered = ", ".join(str(item) for item in uncovered)
        raise ExecutionRuleCoverageError(
            f"fixture {fixture.fixture_id!r} has no valid execution rules for {rendered}; "
            f"declared window is {fixture.rules_valid_from}..{fixture.rules_valid_to}"
        )


def _run_unified_candidate(fixture: ExecutionShadowFixture) -> ExecutionShadowOutcome:
    state = fixture.initial_state
    for market in fixture.pre_market_events:
        state = apply_market_event(state, market).state
    state = apply_order_intent(state, fixture.intent, fixture.candidate_rules).state
    for market in fixture.market_events:
        state = apply_market_event(state, market).state
    order = next(
        order for order in state.orders.values() if order.intent_id == fixture.intent.intent_id
    )
    return _outcome_from_state(state, order)


def _run_current_paper_semantics(fixture: ExecutionShadowFixture) -> ExecutionShadowOutcome:
    """Pure adapter for the matching/accounting semantics in paper_trading.engine.

    Paper matching sizes with minimum+step, checks buy affordability against price
    before fees, applies fees after a fill, and treats duplicate market event IDs as
    idempotent.  Keeping this adapter here avoids importing storage-backed paper code.
    """

    rules = fixture.paper_rules
    account = fixture.initial_state.account
    cash = account.cash
    position = account.positions.get(fixture.intent.instrument_id)
    position_quantity = position.quantity if position is not None else 0
    sellable_quantity = position.sellable_quantity if position is not None else 0
    session_date = fixture.initial_state.session_date
    seen: dict[str, str] = {}
    processed = 0

    def advance(market: MarketEvent) -> None:
        nonlocal session_date, sellable_quantity
        assert market.trading_date is not None
        if session_date is None or market.trading_date > session_date:
            session_date = market.trading_date
            sellable_quantity = position_quantity

    for market in fixture.pre_market_events:
        digest = canonical_digest(market)
        previous = seen.get(market.event_id)
        if previous is not None and previous != digest:
            raise ValueError(f"conflicting paper market event {market.event_id!r}")
        if previous is None:
            advance(market)
            seen[market.event_id] = digest
            processed += 1

    rejection = _paper_order_rejection(
        fixture.intent,
        rules,
        sellable_quantity=sellable_quantity,
    )
    if rejection is not None:
        return ExecutionShadowOutcome(
            status=OrderStatus.REJECTED,
            reason=rejection,
            cash=cash,
            position_quantity=position_quantity,
            sellable_quantity=sellable_quantity,
            processed_market_events=processed,
        )

    order = Order(
        order_id=f"paper-shadow:{fixture.intent.intent_id}",
        intent_id=fixture.intent.intent_id,
        account_id=fixture.intent.account_id,
        instrument_id=fixture.intent.instrument_id,
        side=fixture.intent.side,
        quantity=fixture.intent.quantity,
        submitted_at=fixture.intent.submitted_at,
        order_type=fixture.intent.order_type,
        limit_price=fixture.intent.limit_price,
        stop_price=fixture.intent.stop_price,
        estimated_price=fixture.intent.estimated_price,
        time_in_force=fixture.intent.time_in_force,
        expires_at=fixture.intent.expires_at,
        status=OrderStatus.ACTIVE,
        updated_at=fixture.intent.submitted_at,
        rules=rules,
    )
    filled_quantity = 0
    fills: list[tuple[int, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]] = []
    last_reason: str | None = None
    for market in fixture.market_events:
        digest = canonical_digest(market)
        previous = seen.get(market.event_id)
        if previous is not None:
            if previous != digest:
                raise ValueError(f"conflicting paper market event {market.event_id!r}")
            continue
        seen[market.event_id] = digest
        processed += 1
        advance(market)
        if market.suspended:
            last_reason = "suspended"
            continue
        if market.volume == 0:
            last_reason = "zero_volume"
            continue
        if is_one_price_limit_blocked(order.side, market, rules):
            last_reason = "one_price_limit"
            continue
        base_price = match_base_price(order, market)
        if base_price is None:
            last_reason = None
            continue
        remaining = fixture.intent.quantity - filled_quantity
        capacity = max(participation_capacity(market.volume, rules), 0)
        quantity = _paper_round_quantity(min(remaining, capacity), rules)
        if order.side == OrderSide.SELL:
            quantity = _paper_round_quantity(min(quantity, sellable_quantity), rules)
        if quantity <= 0:
            last_reason = "insufficient_round_lot_volume"
            continue
        price = apply_slippage(order, market, base_price)
        if order.side == OrderSide.BUY:
            affordable = _paper_round_quantity(int(cash / price), rules)
            quantity = min(quantity, affordable)
            if quantity <= 0:
                last_reason = "quantity_below_round_lot"
                continue
        gross = price * quantity
        fees = fee_breakdown(order.side, gross, rules)
        cash += -(gross + fees.total) if order.side == OrderSide.BUY else gross - fees.total
        if order.side == OrderSide.BUY:
            position_quantity += quantity
            if rules.settlement_days == 0:
                sellable_quantity += quantity
        else:
            position_quantity -= quantity
            sellable_quantity -= quantity
        fills.append(
            (
                quantity,
                base_price,
                price,
                gross,
                fees.commission,
                fees.stamp_duty,
                fees.transfer_fee,
            )
        )
        filled_quantity += quantity
        last_reason = None
        if filled_quantity == fixture.intent.quantity:
            break

    status = (
        OrderStatus.FILLED
        if filled_quantity == fixture.intent.quantity
        else OrderStatus.PARTIALLY_FILLED
        if filled_quantity
        else OrderStatus.ACTIVE
    )
    gross_total = sum((item[3] for item in fills), Decimal("0"))
    average = gross_total / filled_quantity if filled_quantity else None
    return ExecutionShadowOutcome(
        status=status,
        reason=last_reason,
        filled_quantity=filled_quantity,
        fill_count=len(fills),
        average_fill_price=average,
        gross_amount=gross_total,
        commission=sum((item[4] for item in fills), Decimal("0")),
        stamp_duty=sum((item[5] for item in fills), Decimal("0")),
        transfer_fee=sum((item[6] for item in fills), Decimal("0")),
        slippage=sum(
            (money(abs(item[2] - item[1]) * item[0]) for item in fills),
            Decimal("0"),
        ),
        cash=money(cash),
        position_quantity=position_quantity,
        sellable_quantity=sellable_quantity,
        processed_market_events=processed,
    )


def _paper_order_rejection(
    intent: OrderIntent,
    rules: AShareExecutionRules,
    *,
    sellable_quantity: int,
) -> str | None:
    minimum = rules.effective_minimum_order_quantity
    step = rules.effective_quantity_step
    if intent.quantity < minimum or (intent.quantity - minimum) % step:
        return "quantity_must_follow_minimum_and_step"
    for price, missing, off_tick in (
        (intent.limit_price, "limit_price_required", "limit_price_not_on_tick"),
        (intent.stop_price, "stop_price_required", "stop_price_not_on_tick"),
    ):
        required = (
            price is intent.limit_price and intent.order_type.value in {"limit", "stop_limit"}
        ) or (price is intent.stop_price and intent.order_type.value in {"stop", "stop_limit"})
        if required and price is None:
            return missing
        if required and price is not None and not is_tick_aligned(price, rules.tick_size):
            return off_tick
    if intent.side == OrderSide.SELL and intent.quantity > sellable_quantity:
        return "insufficient_sellable_quantity"
    return None


def _paper_round_quantity(quantity: int, rules: AShareExecutionRules) -> int:
    minimum = rules.effective_minimum_order_quantity
    step = rules.effective_quantity_step
    if quantity < minimum:
        return 0
    return minimum + ((quantity - minimum) // step) * step


def _outcome_from_state(state: ExecutionState, order: Order) -> ExecutionShadowOutcome:
    fills = [fill for fill in state.fills if fill.order_id == order.order_id]
    position = state.account.positions.get(order.instrument_id)
    return ExecutionShadowOutcome(
        status=order.status,
        reason=order.status_reason,
        filled_quantity=order.filled_quantity,
        fill_count=len(fills),
        average_fill_price=order.average_fill_price,
        gross_amount=sum((fill.gross_amount for fill in fills), Decimal("0")),
        commission=sum((fill.commission for fill in fills), Decimal("0")),
        stamp_duty=sum((fill.stamp_duty for fill in fills), Decimal("0")),
        transfer_fee=sum((fill.transfer_fee for fill in fills), Decimal("0")),
        slippage=sum((fill.slippage for fill in fills), Decimal("0")),
        cash=state.account.cash,
        frozen_cash=state.account.frozen_cash,
        position_quantity=position.quantity if position is not None else 0,
        sellable_quantity=position.sellable_quantity if position is not None else 0,
        processed_market_events=len(state.processed_market_events),
    )


_FIELD_CATEGORIES = {
    "status": ShadowDiffCategory.ORDER_LIFECYCLE,
    "reason": ShadowDiffCategory.ORDER_LIFECYCLE,
    "filled_quantity": ShadowDiffCategory.QUANTITY_MODEL,
    "position_quantity": ShadowDiffCategory.QUANTITY_MODEL,
    "sellable_quantity": ShadowDiffCategory.QUANTITY_MODEL,
    "average_fill_price": ShadowDiffCategory.PRICE_MODEL,
    "gross_amount": ShadowDiffCategory.PRICE_MODEL,
    "slippage": ShadowDiffCategory.PRICE_MODEL,
    "commission": ShadowDiffCategory.FEE_MODEL,
    "stamp_duty": ShadowDiffCategory.FEE_MODEL,
    "transfer_fee": ShadowDiffCategory.FEE_MODEL,
    "cash": ShadowDiffCategory.ACCOUNTING,
    "frozen_cash": ShadowDiffCategory.ACCOUNTING,
    "fill_count": ShadowDiffCategory.AUDIT,
    "processed_market_events": ShadowDiffCategory.AUDIT,
}


def _differences(
    paper: ExecutionShadowOutcome,
    candidate: ExecutionShadowOutcome,
) -> tuple[ExecutionShadowDifference, ...]:
    # Compare Decimal values numerically rather than treating harmless scale
    # differences (``10.00`` versus ``10.000000``) as execution drift.
    paper_values = paper.model_dump()
    candidate_values = candidate.model_dump()
    return tuple(
        ExecutionShadowDifference(
            field=field,
            paper_value=paper_values[field],
            candidate_value=candidate_values[field],
            category=_FIELD_CATEGORIES[field],
        )
        for field in paper_values
        if paper_values[field] != candidate_values[field]
    )
