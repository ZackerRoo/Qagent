from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, ROUND_DOWN
from typing import Any

from pydantic import BaseModel

from qagent.execution import (
    AShareExecutionRules,
    Account,
    ExecutionState,
    Fill,
    MarketEvent,
    OrderIntent,
    OrderSide,
    OrderType,
    Position,
    TimeInForce,
    apply_market_event,
    apply_order_intent,
    fee_breakdown,
    round_lot,
    round_to_tick,
)
from qagent.historical_evidence.models import HistoricalFeeRule
from qagent.storage.replay_evidence import (
    ReplayEvidenceRepository,
    ReplayEvidenceUnavailable,
)


class HistoricalExecutionRule(BaseModel):
    instrument_id: str
    trade_date: date
    limit_pct: Decimal | None
    minimum_order_quantity: int
    quantity_step: int
    settlement_days: int
    ipo_no_limit_sessions: int
    listing_date: date | None = None
    buy_fee: HistoricalFeeRule
    sell_fee: HistoricalFeeRule
    rule_set_version: str
    fee_schedule_version: str
    tick_size: Decimal = Decimal("0.01")
    board_lot: int = 100


class VersionedAshareExecutionResolver:
    def __init__(
        self,
        repository: ReplayEvidenceRepository,
        *,
        dataset_revision: int | None = None,
    ):
        self.repository = repository
        self.dataset_revision = dataset_revision or repository.current_revision()
        self._cache: dict[tuple[str, date, bool], HistoricalExecutionRule] = {}
        self._listing_dates: dict[str, date | None] | None = None

    def resolve(
        self,
        instrument_id: str,
        trade_date: date,
        *,
        is_st: bool = False,
    ) -> HistoricalExecutionRule:
        key = (instrument_id, trade_date, is_st)
        if key in self._cache:
            return self._cache[key]
        metadata = self.repository.instrument_rule_metadata_on(instrument_id, trade_date)
        trading_rule = self.repository.trading_rule_for(
            rule_set_version=metadata.rule_set_version,
            market=metadata.market,
            board=metadata.board,
            security_type=metadata.security_type,
            is_st=is_st,
            trade_date=trade_date,
        )
        fees = self.repository.fee_rules_on(
            fee_schedule_version=metadata.fee_schedule_version,
            fee_rule_key=metadata.fee_rule_key,
            trade_date=trade_date,
        )
        by_side = {item.side: item for item in fees}
        if set(by_side) != {"buy", "sell"}:
            raise ValueError(
                f"execution fees for {instrument_id} must contain buy and sell rules"
            )
        resolved = HistoricalExecutionRule(
            instrument_id=instrument_id,
            trade_date=trade_date,
            limit_pct=trading_rule.limit_pct,
            minimum_order_quantity=metadata.minimum_order_quantity,
            quantity_step=metadata.quantity_step,
            settlement_days=metadata.settlement_days,
            ipo_no_limit_sessions=trading_rule.ipo_no_limit_sessions,
            listing_date=self._listing_date(instrument_id),
            buy_fee=by_side["buy"],
            sell_fee=by_side["sell"],
            rule_set_version=metadata.rule_set_version,
            fee_schedule_version=metadata.fee_schedule_version,
            tick_size=trading_rule.tick_size,
            board_lot=metadata.board_lot,
        )
        self._cache[key] = resolved
        return resolved

    def _listing_date(self, instrument_id: str) -> date | None:
        if self._listing_dates is None:
            try:
                inventory = self.repository.lifecycle_inventory(self.dataset_revision)
            except ReplayEvidenceUnavailable:
                inventory = []
            self._listing_dates = {
                item.instrument_id: item.listing_date for item in inventory
            }
        return self._listing_dates.get(instrument_id)


def calculate_round_trip_fees(
    rule: HistoricalExecutionRule,
    *,
    entry_value: Decimal,
    exit_value: Decimal,
    exit_rule: HistoricalExecutionRule | None = None,
) -> Decimal:
    exit_rule = exit_rule or rule
    buy_rules = execution_rules_from_historical(rule, side=OrderSide.BUY)
    sell_rules = execution_rules_from_historical(
        exit_rule,
        side=OrderSide.SELL,
    )
    return fee_breakdown(OrderSide.BUY, entry_value, buy_rules).total + fee_breakdown(
        OrderSide.SELL,
        exit_value,
        sell_rules,
    ).total


def round_order_quantity(value: Decimal, rule: HistoricalExecutionRule) -> Decimal:
    whole_quantity = int(value.to_integral_value(rounding=ROUND_DOWN))
    if whole_quantity < rule.minimum_order_quantity:
        return Decimal("0")
    remainder = whole_quantity - rule.minimum_order_quantity
    return Decimal(
        rule.minimum_order_quantity + round_lot(remainder, rule.quantity_step)
    )


def execution_rules_from_historical(
    rule: HistoricalExecutionRule,
    *,
    side: OrderSide,
    slippage_bps: Decimal = Decimal("0"),
    volume_participation_rate: Decimal = Decimal("1"),
) -> AShareExecutionRules:
    """Adapt one historical side-specific rule to the shared execution contract."""

    side_fee = rule.buy_fee if side == OrderSide.BUY else rule.sell_fee
    return AShareExecutionRules(
        rules_version=rule.rule_set_version,
        fee_schedule_version=rule.fee_schedule_version,
        tick_size=rule.tick_size,
        lot_size=rule.quantity_step,
        settlement_days=rule.settlement_days,
        price_limit_rate=(
            rule.limit_pct / Decimal("100") if rule.limit_pct is not None else None
        ),
        volume_participation_rate=volume_participation_rate,
        commission_bps=side_fee.commission_bps,
        minimum_commission=side_fee.minimum_commission,
        stamp_duty_bps=side_fee.stamp_duty_bps,
        transfer_fee_bps=side_fee.transfer_fee_bps,
        slippage_bps=slippage_bps,
    )


def execute_daily_bar_order(
    *,
    instrument_id: str,
    row: Any,
    previous: Any | None,
    side: OrderSide,
    quantity: int,
    order_type: OrderType,
    rules: AShareExecutionRules,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    intent_id: str | None = None,
) -> Fill | None:
    """Run one daily-bar order through the shared deterministic execution engine."""

    trade_date = _trade_date(_row_value(row, "trade_date"))
    occurred_at = datetime.combine(trade_date, time(15, 0))
    market = MarketEvent(
        event_id=(
            f"portfolio:{instrument_id}:{trade_date.isoformat()}:{side.value}:"
            f"{order_type.value}:{intent_id or 'order'}"
        ),
        instrument_id=instrument_id,
        occurred_at=occurred_at,
        trading_date=trade_date,
        open=_required_decimal(_row_value(row, "open"), "open"),
        high=_required_decimal(_row_value(row, "high"), "high"),
        low=_required_decimal(_row_value(row, "low"), "low"),
        close=_required_decimal(_row_value(row, "close"), "close"),
        volume=_volume(_row_value(row, "volume", 0)),
        previous_close=_previous_close(row, previous),
        suspended=_row_is_suspended(row),
        limit_up_price=_optional_decimal(_row_value(row, "limit_up_price", None)),
        limit_down_price=_optional_decimal(
            _row_value(row, "limit_down_price", None)
        ),
        price_limit_rate=_price_limit_rate(
            _row_value(row, "price_limit_rate", None)
        ),
    )
    account_id = "portfolio-backtest-adapter"
    positions = {}
    cash = Decimal("1000000000000000")
    if side == OrderSide.SELL:
        cash = Decimal("0")
        positions[instrument_id] = Position(
            account_id=account_id,
            instrument_id=instrument_id,
            quantity=quantity,
            sellable_quantity=quantity,
            average_cost=market.open,
            cost_basis=market.open * quantity,
        )
    state = ExecutionState(
        account=Account(account_id=account_id, cash=cash, positions=positions),
        session_date=trade_date if side == OrderSide.SELL else None,
    )
    estimated_price = None
    if side == OrderSide.BUY and order_type not in {
        OrderType.LIMIT,
        OrderType.STOP_LIMIT,
    }:
        estimated_price = round_to_tick(market.open, rules.tick_size)
    intent = OrderIntent(
        intent_id=intent_id or market.event_id,
        account_id=account_id,
        instrument_id=instrument_id,
        side=side,
        quantity=quantity,
        submitted_at=datetime.combine(trade_date, time(9, 0)),
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        estimated_price=estimated_price,
        time_in_force=TimeInForce.GTC,
    )
    submitted = apply_order_intent(state, intent, rules)
    matched = apply_market_event(submitted.state, market)
    return matched.state.fills[-1] if matched.state.fills else None


def _side_fee(rule: HistoricalFeeRule, traded_value: Decimal) -> Decimal:
    side = OrderSide(rule.side)
    execution_rules = AShareExecutionRules(
        lot_size=1,
        settlement_days=0,
        price_limit_rate=None,
        commission_bps=rule.commission_bps,
        minimum_commission=rule.minimum_commission,
        stamp_duty_bps=rule.stamp_duty_bps,
        transfer_fee_bps=rule.transfer_fee_bps,
        slippage_bps=Decimal("0"),
    )
    return fee_breakdown(side, traded_value, execution_rules).total


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    getter = getattr(row, "get", None)
    if getter is not None:
        return getter(name, default)
    try:
        return row[name]
    except (KeyError, TypeError):
        return default


def _trade_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed.date()


def _required_decimal(value: Any, field: str) -> Decimal:
    parsed = _optional_decimal(value)
    if parsed is None:
        raise ValueError(f"daily bar {field} is required")
    return parsed


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or _is_missing(value):
        return None
    return Decimal(str(value))


def _previous_close(row: Any, previous: Any | None) -> Decimal | None:
    explicit = _optional_decimal(_row_value(row, "previous_close", None))
    if explicit is not None:
        return explicit
    if previous is None:
        return None
    return _optional_decimal(_row_value(previous, "close", None))


def _price_limit_rate(value: Any) -> Decimal | None:
    rate = _optional_decimal(value)
    if rate is not None and rate > 1:
        return rate / Decimal("100")
    return rate


def _volume(value: Any) -> int:
    parsed = _optional_decimal(value)
    if parsed is None or parsed <= 0:
        return 0
    return int(parsed.to_integral_value(rounding=ROUND_DOWN))


def _row_is_suspended(row: Any) -> bool:
    if _as_bool(_row_value(row, "suspended", False)) or _as_bool(
        _row_value(row, "is_suspended", False)
    ):
        return True
    status_value = _row_value(row, "trading_status", "trading")
    if status_value is None or _is_missing(status_value):
        return False
    status = str(status_value).strip().lower()
    return status not in {"", "trading", "normal", "active"}


def _as_bool(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _is_missing(value: Any) -> bool:
    if str(value).strip().lower() in {"", "<na>", "nan", "nat", "none"}:
        return True
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return False
