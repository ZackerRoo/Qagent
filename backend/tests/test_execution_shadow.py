from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from qagent.backtesting.a_share_rules import load_a_share_rule_schedule_version
from qagent.execution import (
    AShareExecutionRules,
    Account,
    ExecutionState,
    MarketEvent,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from qagent.execution.shadow import (
    ExecutionRuleCoverageError,
    ExecutionShadowFixture,
    ShadowDiffCategory,
    compare_execution_shadow,
    with_resolved_candidate_fee,
)
from qagent.execution.fees import (
    BrokerFeeTerms,
    FeePolicyRequest,
    VersionedAshareFeePolicy,
)


DAY_1 = date(2025, 12, 30)
DAY_2 = date(2025, 12, 31)


def _at(day: date, hour: int = 9, minute: int = 30) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def _rules(
    *,
    version: str = "shadow-rules-2025",
    fee_version: str = "shadow-fees-2025",
    tick: str = "0.01",
    lot: int = 100,
    minimum: int | None = None,
    step: int | None = None,
    settlement_days: int = 1,
    participation: str = "1",
    commission: str = "3",
    minimum_commission: str = "5",
    stamp: str = "5",
    transfer: str = "0.1",
    slippage: str = "0",
) -> AShareExecutionRules:
    return AShareExecutionRules(
        rules_version=version,
        fee_schedule_version=fee_version,
        tick_size=Decimal(tick),
        lot_size=lot,
        minimum_order_quantity=minimum,
        quantity_step=step,
        settlement_days=settlement_days,
        price_limit_rate=Decimal("0.10"),
        volume_participation_rate=Decimal(participation),
        commission_bps=Decimal(commission),
        minimum_commission=Decimal(minimum_commission),
        stamp_duty_bps=Decimal(stamp),
        transfer_fee_bps=Decimal(transfer),
        slippage_bps=Decimal(slippage),
    )


def _paper_rules(**overrides) -> AShareExecutionRules:
    return _rules(
        version="paper-a-share-execution-v1",
        fee_version="paper-account-cost-v1",
        minimum_commission="0",
        stamp="0",
        transfer="0",
        **overrides,
    )


def _state(
    *,
    cash: str = "100000",
    instrument_id: str = "CN:600000",
    quantity: int = 0,
    sellable: int = 0,
    session_date: date | None = None,
) -> ExecutionState:
    positions = {}
    if quantity:
        positions[instrument_id] = Position(
            account_id="paper-shadow",
            instrument_id=instrument_id,
            quantity=quantity,
            sellable_quantity=sellable,
            average_cost=Decimal("10"),
            cost_basis=Decimal("10") * quantity,
            last_fill_at=_at(session_date or DAY_1, 15),
        )
    return ExecutionState(
        account=Account(
            account_id="paper-shadow",
            cash=Decimal(cash),
            positions=positions,
        ),
        session_date=session_date,
    )


def _intent(
    fixture_id: str,
    *,
    instrument_id: str = "CN:600000",
    side: OrderSide = OrderSide.BUY,
    quantity: int = 100,
    day: date = DAY_1,
    limit: str = "10.00",
) -> OrderIntent:
    return OrderIntent(
        intent_id=fixture_id,
        account_id="paper-shadow",
        instrument_id=instrument_id,
        side=side,
        quantity=quantity,
        submitted_at=_at(day),
        order_type=OrderType.LIMIT,
        limit_price=Decimal(limit),
        estimated_price=Decimal(limit),
    )


def _bar(
    event_id: str,
    *,
    instrument_id: str = "CN:600000",
    day: date = DAY_1,
    open_price: str = "10.00",
    high: str = "10.10",
    low: str = "9.90",
    close: str = "10.00",
    volume: int = 10_000,
    previous_close: str = "10.00",
    suspended: bool = False,
) -> MarketEvent:
    return MarketEvent(
        event_id=event_id,
        instrument_id=instrument_id,
        occurred_at=_at(day, 15),
        trading_date=day,
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
        previous_close=Decimal(previous_close),
        suspended=suspended,
    )


def _fixture(
    fixture_id: str,
    *,
    state: ExecutionState | None = None,
    intent: OrderIntent | None = None,
    bars: tuple[MarketEvent, ...] | None = None,
    pre_bars: tuple[MarketEvent, ...] = (),
    paper_rules: AShareExecutionRules | None = None,
    candidate_rules: AShareExecutionRules | None = None,
    valid_to: date = DAY_2,
) -> ExecutionShadowFixture:
    return ExecutionShadowFixture(
        fixture_id=fixture_id,
        initial_state=state or _state(),
        intent=intent or _intent(fixture_id),
        pre_market_events=pre_bars,
        market_events=bars or (_bar(f"bar:{fixture_id}"),),
        paper_rules=paper_rules or _paper_rules(),
        candidate_rules=candidate_rules or _paper_rules(),
        rules_valid_from=date(2025, 1, 1),
        rules_valid_to=valid_to,
    )


def test_normal_buy_is_field_equal_and_digests_are_stable():
    fixture = _fixture("normal-buy")

    first = compare_execution_shadow(fixture)
    second = compare_execution_shadow(fixture)

    assert first.matched
    assert first.paper.status == OrderStatus.FILLED
    assert first.paper.filled_quantity == 100
    assert first.input_digest == second.input_digest
    assert first.paper_digest == second.paper_digest
    assert first.candidate_digest == second.candidate_digest


def test_normal_sell_exposes_minimum_commission_stamp_and_transfer_fee_delta():
    instrument_id = "CN:600000"
    report = compare_execution_shadow(
        _fixture(
            "normal-sell-fees",
            state=_state(
                cash="1000",
                instrument_id=instrument_id,
                quantity=100,
                sellable=100,
                session_date=DAY_1,
            ),
            intent=_intent(
                "normal-sell-fees",
                instrument_id=instrument_id,
                side=OrderSide.SELL,
            ),
            paper_rules=_paper_rules(),
            candidate_rules=_rules(),
        )
    )

    assert report.paper.status == report.candidate.status == OrderStatus.FILLED
    assert report.paper.commission == Decimal("0.30")
    assert report.candidate.commission == Decimal("5.00")
    assert report.candidate.stamp_duty == Decimal("0.50")
    assert report.candidate.transfer_fee == Decimal("0.01")
    assert ShadowDiffCategory.FEE_MODEL in report.classifications
    assert ShadowDiffCategory.ACCOUNTING in report.classifications


@pytest.mark.parametrize(
    ("fixture_id", "instrument_id", "quantity", "rules", "limit"),
    [
        ("main-board-lot", "CN:600000", 200, _paper_rules(), "10.00"),
        (
            "star-200-plus-one",
            "CN:688001",
            201,
            _paper_rules(lot=1, minimum=200, step=1),
            "10.00",
        ),
        (
            "etf-mill-tick",
            "CN:510300",
            100,
            _paper_rules(tick="0.001"),
            "1.234",
        ),
    ],
)
def test_integer_lot_star_200_plus_one_and_etf_tick_match(
    fixture_id,
    instrument_id,
    quantity,
    rules,
    limit,
):
    report = compare_execution_shadow(
        _fixture(
            fixture_id,
            state=_state(instrument_id=instrument_id),
            intent=_intent(
                fixture_id,
                instrument_id=instrument_id,
                quantity=quantity,
                limit=limit,
            ),
            bars=(
                _bar(
                    f"bar:{fixture_id}",
                    instrument_id=instrument_id,
                    open_price=limit,
                    high=limit,
                    low=limit,
                    close=limit,
                    previous_close=limit,
                ),
            ),
            paper_rules=rules,
            candidate_rules=rules,
        )
    )

    assert report.matched
    assert report.candidate.filled_quantity == quantity
    assert report.candidate.average_fill_price == Decimal(limit)


def test_t_plus_one_rejects_same_day_sell_and_allows_next_session():
    instrument_id = "CN:600000"
    state = _state(
        instrument_id=instrument_id,
        quantity=100,
        sellable=0,
        session_date=DAY_1,
    )
    same_day = compare_execution_shadow(
        _fixture(
            "t1-same-day",
            state=state,
            intent=_intent("t1-same-day", instrument_id=instrument_id, side=OrderSide.SELL),
        )
    )
    next_day = compare_execution_shadow(
        _fixture(
            "t1-next-day",
            state=state,
            intent=_intent(
                "t1-next-day",
                instrument_id=instrument_id,
                side=OrderSide.SELL,
                day=DAY_2,
            ),
            pre_bars=(
                _bar(
                    "session-advance",
                    instrument_id="CN:000001",
                    day=DAY_2,
                ),
            ),
            bars=(_bar("t1-sell-bar", instrument_id=instrument_id, day=DAY_2),),
        )
    )

    assert same_day.candidate.status == OrderStatus.REJECTED
    assert same_day.candidate.reason == "insufficient_sellable_quantity"
    assert same_day.paper.status == OrderStatus.REJECTED
    assert same_day.classifications == (ShadowDiffCategory.AUDIT,)
    assert next_day.matched
    assert next_day.candidate.status == OrderStatus.FILLED


@pytest.mark.parametrize(
    ("fixture_id", "bar", "reason"),
    [
        ("suspended", _bar("suspended", suspended=True), "suspended"),
        (
            "one-price-limit",
            _bar(
                "one-price-limit",
                open_price="11.00",
                high="11.00",
                low="11.00",
                close="11.00",
                previous_close="10.00",
            ),
            "one_price_limit",
        ),
    ],
)
def test_suspension_and_one_price_limit_remain_unfilled(fixture_id, bar, reason):
    intent = _intent(fixture_id, limit="11.00" if fixture_id == "one-price-limit" else "10")
    report = compare_execution_shadow(_fixture(fixture_id, intent=intent, bars=(bar,)))

    assert report.paper.filled_quantity == report.candidate.filled_quantity == 0
    assert report.paper.reason == reason


def test_gap_and_volume_participation_produce_same_partial_fill():
    rules = _paper_rules(participation="0.10")
    report = compare_execution_shadow(
        _fixture(
            "gap-partial",
            intent=_intent("gap-partial", quantity=1000, limit="10.00"),
            bars=(
                _bar(
                    "gap-partial",
                    open_price="9.50",
                    high="10.00",
                    low="9.40",
                    close="9.80",
                    volume=5_000,
                ),
            ),
            paper_rules=rules,
            candidate_rules=rules,
        )
    )

    assert report.classifications == (ShadowDiffCategory.ACCOUNTING,)
    assert report.candidate.status == OrderStatus.PARTIALLY_FILLED
    assert report.candidate.filled_quantity == 500
    assert report.candidate.average_fill_price == Decimal("9.50")
    assert report.paper.frozen_cash == 0
    assert report.candidate.frozen_cash == Decimal("5001.50")


def test_cash_that_covers_notional_but_not_fees_exposes_lifecycle_delta():
    report = compare_execution_shadow(
        _fixture(
            "cash-excludes-fees",
            state=_state(cash="1000"),
            candidate_rules=_rules(),
            paper_rules=_paper_rules(),
        )
    )

    assert report.paper.status == OrderStatus.FILLED
    assert report.paper.cash == Decimal("-0.30")
    assert report.candidate.status == OrderStatus.REJECTED
    assert report.candidate.reason == "insufficient_cash"
    assert ShadowDiffCategory.ORDER_LIFECYCLE in report.classifications
    assert ShadowDiffCategory.ACCOUNTING in report.classifications


def test_duplicate_market_event_is_idempotent_and_does_not_double_fill():
    bar = _bar("duplicate-event")
    report = compare_execution_shadow(_fixture("idempotent", bars=(bar, bar)))

    assert report.matched
    assert report.candidate.fill_count == 1
    assert report.candidate.filled_quantity == 100
    assert report.candidate.processed_market_events == 1


def test_2026_fixture_fails_when_checked_in_rule_window_ends_in_2025():
    trade_date = date(2026, 1, 5)
    fixture = _fixture(
        "no-2026-rules",
        intent=_intent("no-2026-rules", day=trade_date),
        bars=(_bar("no-2026-rules", day=trade_date),),
        valid_to=date(2025, 12, 31),
    )

    with pytest.raises(ExecutionRuleCoverageError, match="no valid execution rules for 2026-01-05"):
        compare_execution_shadow(fixture)


def test_resolved_fee_builder_is_pure_auditable_and_digest_stable():
    trade_date = date(2026, 9, 1)
    base = _fixture(
        "fee-policy-audit",
        intent=_intent("fee-policy-audit", day=trade_date),
        bars=(_bar("fee-policy-audit", day=trade_date),),
        valid_to=date(2026, 12, 31),
    ).model_copy(update={"rules_valid_from": date(2026, 1, 1)})
    resolved = VersionedAshareFeePolicy(
        load_a_share_rule_schedule_version("a-share-rules-v2")
    ).resolve(
        FeePolicyRequest(
            trade_date=trade_date,
            security_type="stock",
            side="buy",
            exchange="SSE",
            broker_terms=BrokerFeeTerms(
                commission_bps="2.5",
                minimum_commission="5",
                account_config_version="shadow-account-v3",
                rounding_rule="audit-only",
            ),
        )
    )

    fixture = with_resolved_candidate_fee(base, resolved)
    first = compare_execution_shadow(fixture)
    second = compare_execution_shadow(fixture)

    assert base.candidate_rules.fee_schedule_version == "paper-account-cost-v1"
    assert fixture.candidate_rules.fee_schedule_version == resolved.fee_schedule_version
    assert first.candidate_fee_audit is not None
    assert first.candidate_fee_audit.fee_rule_key == "cn-stock-secondary"
    assert first.candidate_fee_audit.account_config_version == "shadow-account-v3"
    assert first.candidate_fee_audit.rounding_rule == "audit-only"
    assert first.candidate_fee_audit.rounding_applied is False
    assert first.input_digest == second.input_digest
    assert first.candidate_digest == second.candidate_digest


def test_shadow_and_fee_policy_do_not_import_production_paper_dependencies():
    execution_dir = Path(__file__).parents[1] / "qagent" / "execution"
    forbidden = (
        "qagent.paper_trading",
        "qagent.db",
        "qagent.api",
        "qagent.scheduler",
        "qagent.services",
    )
    imported: set[str] = set()
    for name in ("shadow.py", "fees.py"):
        tree = ast.parse((execution_dir / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)

    assert not any(name.startswith(forbidden) for name in imported)
