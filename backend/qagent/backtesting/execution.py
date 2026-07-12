from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from pydantic import BaseModel

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
    return _side_fee(rule.buy_fee, entry_value) + _side_fee(
        exit_rule.sell_fee, exit_value
    )


def round_order_quantity(value: Decimal, rule: HistoricalExecutionRule) -> Decimal:
    minimum = Decimal(rule.minimum_order_quantity)
    step = Decimal(rule.quantity_step)
    if value < minimum:
        return Decimal("0")
    increments = ((value - minimum) / step).to_integral_value(rounding=ROUND_DOWN)
    return minimum + increments * step


def _side_fee(rule: HistoricalFeeRule, traded_value: Decimal) -> Decimal:
    variable = traded_value * (
        rule.commission_bps + rule.stamp_duty_bps + rule.transfer_fee_bps
    ) / Decimal("10000")
    commission = max(
        traded_value * rule.commission_bps / Decimal("10000"),
        rule.minimum_commission,
    )
    non_commission = variable - (
        traded_value * rule.commission_bps / Decimal("10000")
    )
    return (commission + non_commission).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
