from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from qagent.historical_evidence.models import (
    HistoricalFeeRule,
    HistoricalInstrumentProfile,
    HistoricalInstrumentRuleMetadata,
    HistoricalTradingRule,
)


RULES_PATH = Path(__file__).with_name("a_share_rules_v1.json")
RULES_V2_PATH = Path(__file__).with_name("a_share_rules_v2.json")
SUPPORTED_T0_ETF_CATEGORIES = frozenset(
    {"bond", "gold", "money_market", "cross_border", "commodity_futures"}
)


class BrokerFeeRequest(BaseModel):
    commission_bps: Decimal = Field(ge=0)
    minimum_commission: Decimal = Field(ge=0)


class FeeTemplate(BaseModel):
    fee_rule_key: str
    effective_from: date
    effective_to: date | None
    side: Literal["buy", "sell"]
    security_type: Literal["stock", "etf"]
    exchange: str
    stamp_duty_bps: Decimal
    transfer_fee_bps: Decimal


class RuleSource(BaseModel):
    authority: str
    url: str


class RuleScope(BaseModel):
    included: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()


class AShareRuleSchedule(BaseModel):
    rule_set_version: str
    fee_schedule_version: str
    valid_from: date
    valid_to: date | None
    as_of: date | None = None
    review_after: date | None = None
    sources: tuple[RuleSource, ...] = ()
    scope: RuleScope | None = None
    trading_rules: list[HistoricalTradingRule]
    fee_templates: list[FeeTemplate]

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        if self.valid_to is not None and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        if self.review_after is not None and self.as_of is None:
            raise ValueError("review_after requires as_of")
        if self.as_of is not None and self.review_after is not None:
            if self.as_of > self.review_after:
                raise ValueError("as_of must not be after review_after")
        if any(item.rule_set_version != self.rule_set_version for item in self.trading_rules):
            raise ValueError("trading rule version does not match schedule")
        _validate_trading_rule_matrix(self)
        _validate_fee_template_matrix(self)
        return self

    def require_runtime_date(self, trade_date: date) -> None:
        if trade_date < self.valid_from or (
            self.valid_to is not None and trade_date > self.valid_to
        ):
            raise LookupError(f"trade date {trade_date} is outside schedule validity")
        if self.review_after is not None and trade_date > self.review_after:
            raise LookupError(
                f"trade date {trade_date} is after mandatory review date {self.review_after}"
            )

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
        self.require_runtime_date(trade_date)
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


def load_a_share_rule_schedule_version(version: str) -> AShareRuleSchedule:
    """Load a checked-in schedule without changing the legacy no-argument contract."""

    catalog = {
        "a-share-rules-v1": RULES_PATH,
        "a-share-rules-v2": RULES_V2_PATH,
    }
    try:
        path = catalog[version]
    except KeyError as exc:
        raise LookupError(f"unknown A-share rule schedule: {version}") from exc
    return load_a_share_rule_schedule(path)


_CRITICAL_TRADING_MATRIX = frozenset(
    {
        ("SSE", "main", "stock", False),
        ("SSE", "main", "stock", True),
        ("SZSE", "main", "stock", False),
        ("SZSE", "main", "stock", True),
        ("SSE", "star", "stock", False),
        ("SSE", "star", "stock", True),
        ("SZSE", "chinext", "stock", False),
        ("SZSE", "chinext", "stock", True),
        ("BSE", "bse", "stock", False),
        ("BSE", "bse", "stock", True),
        ("CN", "etf_10", "etf", False),
        ("CN", "etf_20", "etf", False),
        ("CN", "etf_10_t0", "etf", False),
        ("CN", "etf_20_t0", "etf", False),
    }
)
_CRITICAL_FEE_MATRIX = frozenset(
    {
        ("stock", "buy", "ALL"),
        ("stock", "sell", "ALL"),
        ("etf", "buy", "ALL"),
        ("etf", "sell", "ALL"),
    }
)


def _validate_trading_rule_matrix(schedule: AShareRuleSchedule) -> None:
    grouped: dict[tuple[str, str, str, bool], list[HistoricalTradingRule]] = {}
    for item in schedule.trading_rules:
        key = (item.market, item.board, item.security_type, item.is_st)
        grouped.setdefault(key, []).append(item)
    missing = _CRITICAL_TRADING_MATRIX - grouped.keys()
    if missing:
        raise ValueError(f"trading rule matrix has missing keys: {sorted(missing)!r}")
    for key in grouped:
        _validate_contiguous_intervals(
            grouped[key],
            schedule=schedule,
            label=f"trading rule {key!r}",
            coverage_start=max(
                schedule.valid_from,
                date(2021, 11, 15) if key[0] == "BSE" else schedule.valid_from,
            ),
        )


def _validate_fee_template_matrix(schedule: AShareRuleSchedule) -> None:
    grouped: dict[tuple[str, str, str], list[FeeTemplate]] = {}
    for item in schedule.fee_templates:
        key = (item.security_type, item.side, item.exchange)
        grouped.setdefault(key, []).append(item)
    missing = _CRITICAL_FEE_MATRIX - grouped.keys()
    if missing:
        raise ValueError(f"fee template matrix has missing keys: {sorted(missing)!r}")
    for key, items in grouped.items():
        _validate_contiguous_intervals(items, schedule=schedule, label=f"fee template {key!r}")
    for index, left in enumerate(schedule.fee_templates):
        for right in schedule.fee_templates[index + 1 :]:
            if left.security_type != right.security_type or left.side != right.side:
                continue
            if left.exchange != right.exchange and "ALL" not in {
                left.exchange,
                right.exchange,
            }:
                continue
            left_end = left.effective_to or date.max
            right_end = right.effective_to or date.max
            if left.effective_from <= right_end and right.effective_from <= left_end:
                if left.exchange != right.exchange:
                    raise ValueError("fee template exchange selectors overlap")


def _validate_contiguous_intervals(
    items: list[HistoricalTradingRule] | list[FeeTemplate],
    *,
    schedule: AShareRuleSchedule,
    label: str,
    coverage_start: date | None = None,
) -> None:
    ordered = sorted(items, key=lambda item: item.effective_from)
    expected_start = coverage_start or schedule.valid_from
    if ordered[0].effective_from != expected_start:
        raise ValueError(f"{label} starts with a coverage gap")
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.effective_to is None:
            raise ValueError(f"{label} has an open interval before another interval")
        if current.effective_from <= previous.effective_to:
            raise ValueError(f"{label} intervals overlap")
        if current.effective_from != previous.effective_to + timedelta(days=1):
            raise ValueError(f"{label} has a coverage gap")
    final_to = ordered[-1].effective_to
    if schedule.valid_to is None:
        if final_to is not None:
            raise ValueError(f"{label} must remain open for an open-ended schedule")
    elif final_to != schedule.valid_to:
        raise ValueError(f"{label} ends with a coverage gap")


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
    effective_end = min(end, schedule.valid_to or end)
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
