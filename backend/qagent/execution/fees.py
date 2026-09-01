"""Fail-closed, offline fee resolution for execution shadow fixtures.

Statutory rates come from a checked-in A-share schedule. Broker commission terms
are supplied separately per account. Rounding metadata is retained for audit but
is intentionally not applied because the current execution model has no rounding
policy contract; this module is not wired to paper trading.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from pydantic import Field

from qagent.backtesting.a_share_rules import AShareRuleSchedule
from qagent.execution.models import AShareExecutionRules, FrozenModel, OrderSide


class FeePolicyCoverageError(LookupError):
    """Raised rather than guessing or falling back when fee coverage is ambiguous."""


class BrokerFeeTerms(FrozenModel):
    commission_bps: Decimal = Field(ge=0)
    minimum_commission: Decimal = Field(ge=0)
    account_config_version: str = Field(min_length=1)
    rounding_rule: str | None = None


class FeePolicyRequest(FrozenModel):
    trade_date: date
    security_type: str
    side: str
    exchange: str
    broker_terms: BrokerFeeTerms


class ResolvedFeeTerms(FrozenModel):
    fee_schedule_version: str
    fee_rule_key: str
    account_config_version: str
    trade_date: date
    security_type: str
    side: OrderSide
    exchange: str
    commission_bps: Decimal = Field(ge=0)
    minimum_commission: Decimal = Field(ge=0)
    stamp_duty_bps: Decimal = Field(ge=0)
    transfer_fee_bps: Decimal = Field(ge=0)
    rounding_rule: str | None = None
    rounding_applied: bool = False


class FeePolicy(Protocol):
    def resolve(self, request: FeePolicyRequest) -> ResolvedFeeTerms: ...


class VersionedAshareFeePolicy:
    _STOCK_EXCHANGES = frozenset({"SSE", "SZSE", "BSE"})
    _ETF_EXCHANGES = frozenset({"SSE", "SZSE"})

    def __init__(self, schedule: AShareRuleSchedule):
        self._schedule = schedule

    def resolve(self, request: FeePolicyRequest) -> ResolvedFeeTerms:
        try:
            side = OrderSide(request.side)
        except ValueError as exc:
            raise FeePolicyCoverageError(f"unknown fee side: {request.side!r}") from exc

        supported_exchanges = {
            "stock": self._STOCK_EXCHANGES,
            "etf": self._ETF_EXCHANGES,
        }.get(request.security_type)
        if supported_exchanges is None:
            raise FeePolicyCoverageError(f"unknown fee security type: {request.security_type!r}")
        if request.exchange not in supported_exchanges:
            raise FeePolicyCoverageError(
                f"unknown {request.security_type} exchange: {request.exchange!r}"
            )
        try:
            self._schedule.require_runtime_date(request.trade_date)
        except LookupError as exc:
            raise FeePolicyCoverageError(str(exc)) from exc

        matches = [
            item
            for item in self._schedule.fee_templates
            if item.security_type == request.security_type
            and item.side == side.value
            and item.exchange in {"ALL", request.exchange}
            and item.effective_from <= request.trade_date
            and (item.effective_to is None or item.effective_to >= request.trade_date)
        ]
        if len(matches) != 1:
            raise FeePolicyCoverageError(
                "expected exactly one statutory fee rule for "
                f"{request.security_type}/{request.exchange}/{side.value} on "
                f"{request.trade_date}, found {len(matches)}"
            )
        statutory = matches[0]
        broker = request.broker_terms
        return ResolvedFeeTerms(
            fee_schedule_version=self._schedule.fee_schedule_version,
            fee_rule_key=statutory.fee_rule_key,
            account_config_version=broker.account_config_version,
            trade_date=request.trade_date,
            security_type=request.security_type,
            side=side,
            exchange=request.exchange,
            commission_bps=broker.commission_bps,
            minimum_commission=broker.minimum_commission,
            stamp_duty_bps=statutory.stamp_duty_bps,
            transfer_fee_bps=statutory.transfer_fee_bps,
            rounding_rule=broker.rounding_rule,
            rounding_applied=False,
        )


def apply_resolved_fee(
    rules: AShareExecutionRules,
    resolved: ResolvedFeeTerms,
) -> AShareExecutionRules:
    """Return a candidate-only rule snapshot with resolved fee terms applied."""

    return rules.model_copy(
        update={
            "fee_schedule_version": resolved.fee_schedule_version,
            "commission_bps": resolved.commission_bps,
            "minimum_commission": resolved.minimum_commission,
            "stamp_duty_bps": resolved.stamp_duty_bps,
            "transfer_fee_bps": resolved.transfer_fee_bps,
        }
    )
