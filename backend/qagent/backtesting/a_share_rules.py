from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from qagent.historical_evidence.models import (
    HistoricalFeeRule,
    HistoricalInstrumentProfile,
    HistoricalInstrumentRuleMetadata,
    HistoricalTradingRule,
)


RULES_PATH = Path(__file__).with_name("a_share_rules_v1.json")
SUPPORTED_T0_ETF_CATEGORIES = frozenset(
    {"bond", "gold", "money_market", "cross_border", "commodity_futures"}
)


class BrokerFeeRequest(BaseModel):
    commission_bps: Decimal = Field(ge=0)
    minimum_commission: Decimal = Field(ge=0)


class FeeTemplate(BaseModel):
    fee_rule_key: str
    effective_from: date
    effective_to: date
    side: Literal["buy", "sell"]
    security_type: Literal["stock", "etf"]
    exchange: str
    stamp_duty_bps: Decimal
    transfer_fee_bps: Decimal


class AShareRuleSchedule(BaseModel):
    rule_set_version: str
    fee_schedule_version: str
    valid_from: date
    valid_to: date
    trading_rules: list[HistoricalTradingRule]
    fee_templates: list[FeeTemplate]

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.valid_from != date(2021, 11, 1) or self.valid_to != date(2025, 12, 31):
            raise ValueError("a-share-rules-v1 must cover the declared validation window")
        if any(item.rule_set_version != self.rule_set_version for item in self.trading_rules):
            raise ValueError("trading rule version does not match schedule")
        return self

    def fee_rules(self, request: BrokerFeeRequest) -> list[HistoricalFeeRule]:
        return [
            HistoricalFeeRule(
                fee_schedule_version=self.fee_schedule_version,
                commission_bps=request.commission_bps,
                minimum_commission=request.minimum_commission,
                **item.model_dump(),
            )
            for item in self.fee_templates
        ]

    def trading_rule(
        self,
        *,
        trade_date: date,
        market: str,
        board: str,
        security_type: str,
        is_st: bool = False,
    ) -> HistoricalTradingRule:
        matches = [
            item
            for item in self.trading_rules
            if item.market == market
            and item.board == board
            and item.security_type == security_type
            and item.is_st == is_st
            and item.effective_from <= trade_date
            and (item.effective_to is None or item.effective_to >= trade_date)
        ]
        if len(matches) != 1:
            raise LookupError(
                f"expected one A-share rule for {market}/{board}/{security_type}/"
                f"st={is_st} on {trade_date}, found {len(matches)}"
            )
        return matches[0]


class EtfRuleMetadata(BaseModel):
    price_limit_pct: Literal[10, 20] = 10
    board_lot: int = Field(default=100, gt=0)
    t0_category: str | None = None

    @property
    def settlement_days(self) -> int:
        if self.t0_category is None:
            return 1
        if self.t0_category not in SUPPORTED_T0_ETF_CATEGORIES:
            raise ValueError(f"unsupported T+0 ETF category: {self.t0_category}")
        return 0


def load_a_share_rule_schedule(path: Path = RULES_PATH) -> AShareRuleSchedule:
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload["rule_set_version"]
    payload["trading_rules"] = [
        {"rule_set_version": version, **item} for item in payload["trading_rules"]
    ]
    return AShareRuleSchedule.model_validate(payload)


def build_instrument_rule_metadata(
    profile: HistoricalInstrumentProfile,
    *,
    effective_from: date,
    schedule: AShareRuleSchedule,
    etf: EtfRuleMetadata | None = None,
    source_provider: str = "qagent_checked_in_rules",
) -> HistoricalInstrumentRuleMetadata:
    symbol = profile.instrument_id.split(":", 1)[-1].split(".", 1)[0]
    security_type = profile.security_type or ""
    if security_type == "etf":
        etf = etf or EtfRuleMetadata()
        market = "CN"
        board = f"etf_{etf.price_limit_pct}"
        if etf.settlement_days == 0:
            board += "_t0"
        fee_rule_key = "cn-etf"
    elif security_type == "stock":
        etf = None
        market, board = _stock_market_and_board(symbol)
        fee_rule_key = "cn-stock"
    else:
        raise ValueError(f"unsupported historical security type: {security_type}")
    rule = schedule.trading_rule(
        trade_date=effective_from,
        market=market,
        board=board,
        security_type=security_type,
    )
    return HistoricalInstrumentRuleMetadata(
        provider_mode="free",
        instrument_id=profile.instrument_id,
        effective_from=effective_from,
        effective_to=rule.effective_to,
        security_type=security_type,
        market=market,
        board=board,
        settlement_days=rule.settlement_days,
        board_lot=etf.board_lot if etf is not None else rule.board_lot,
        minimum_order_quantity=(etf.board_lot if etf is not None else rule.minimum_order_quantity),
        quantity_step=etf.board_lot if etf is not None else rule.quantity_step,
        rule_set_version=schedule.rule_set_version,
        limit_rule_key=rule.limit_rule_key,
        fee_schedule_version=schedule.fee_schedule_version,
        fee_rule_key=fee_rule_key,
        source_provider=source_provider,
        fetched_at=datetime.now(timezone.utc),
    )


def build_instrument_rule_metadata_schedule(
    profile: HistoricalInstrumentProfile,
    *,
    start: date,
    end: date,
    schedule: AShareRuleSchedule,
    etf: EtfRuleMetadata | None = None,
    source_provider: str = "qagent_checked_in_rules",
) -> list[HistoricalInstrumentRuleMetadata]:
    if start > end:
        raise ValueError("start must be on or before end")
    effective_start = max(
        start,
        schedule.valid_from,
        profile.listing_date or schedule.valid_from,
    )
    effective_end = min(end, schedule.valid_to)
    if profile.delisting_date is not None:
        effective_end = min(effective_end, profile.delisting_date)
    if effective_start > effective_end:
        return []

    probe = build_instrument_rule_metadata(
        profile,
        effective_from=effective_start,
        schedule=schedule,
        etf=etf,
        source_provider=source_provider,
    )
    transition_dates = {
        item.effective_from
        for item in schedule.trading_rules
        if item.market == probe.market
        and item.board == probe.board
        and item.security_type == probe.security_type
        and item.is_st is False
        and effective_start < item.effective_from <= effective_end
    }
    return [
        build_instrument_rule_metadata(
            profile,
            effective_from=effective_from,
            schedule=schedule,
            etf=etf,
            source_provider=source_provider,
        )
        for effective_from in sorted({effective_start, *transition_dates})
    ]


def _stock_market_and_board(symbol: str) -> tuple[str, str]:
    if symbol.startswith("688"):
        return "SSE", "star"
    if symbol.startswith(("300", "301")):
        return "SZSE", "chinext"
    if symbol.startswith(("4", "8", "920")):
        return "BSE", "bse"
    if symbol.startswith(("5", "6", "9")):
        return "SSE", "main"
    return "SZSE", "main"
