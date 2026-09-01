from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from qagent.backtesting.a_share_rules import (
    AShareRuleSchedule,
    FeeTemplate,
    load_a_share_rule_schedule_version,
)
from qagent.execution.fees import (
    BrokerFeeTerms,
    FeePolicyCoverageError,
    FeePolicyRequest,
    VersionedAshareFeePolicy,
    apply_resolved_fee,
)
from qagent.execution.models import AShareExecutionRules


def _broker(version: str = "broker-a-v1", commission: str = "2.5") -> BrokerFeeTerms:
    return BrokerFeeTerms(
        commission_bps=commission,
        minimum_commission="5",
        account_config_version=version,
        rounding_rule="broker_statement_rule_unapplied",
    )


def _resolve(
    *,
    security_type: str,
    side: str,
    exchange: str = "SSE",
    trade_date: date = date(2026, 9, 1),
    broker: BrokerFeeTerms | None = None,
):
    schedule = load_a_share_rule_schedule_version("a-share-rules-v2")
    return VersionedAshareFeePolicy(schedule).resolve(
        FeePolicyRequest(
            trade_date=trade_date,
            security_type=security_type,
            side=side,
            exchange=exchange,
            broker_terms=broker or _broker(),
        )
    )


@pytest.mark.parametrize(
    ("security_type", "side", "stamp", "transfer"),
    [
        ("stock", "buy", "0", "0.1"),
        ("stock", "sell", "5", "0.1"),
        ("etf", "buy", "0", "0"),
        ("etf", "sell", "0", "0"),
    ],
)
def test_statutory_stock_and_etf_buy_sell_matrix(
    security_type,
    side,
    stamp,
    transfer,
):
    resolved = _resolve(security_type=security_type, side=side)

    assert resolved.fee_schedule_version == "cn-cash-fees-2023-08-28-v1"
    assert resolved.stamp_duty_bps == Decimal(stamp)
    assert resolved.transfer_fee_bps == Decimal(transfer)
    assert resolved.commission_bps == Decimal("2.5")
    assert resolved.minimum_commission == Decimal("5")
    assert resolved.rounding_rule == "broker_statement_rule_unapplied"
    assert resolved.rounding_applied is False


def test_broker_terms_change_without_changing_statutory_schedule_identity():
    first = _resolve(security_type="stock", side="sell")
    second = _resolve(
        security_type="stock",
        side="sell",
        broker=_broker("broker-b-v7", "1.8"),
    )

    assert first.fee_schedule_version == second.fee_schedule_version
    assert first.fee_rule_key == second.fee_rule_key
    assert first.stamp_duty_bps == second.stamp_duty_bps
    assert first.account_config_version == "broker-a-v1"
    assert second.account_config_version == "broker-b-v7"
    assert first.commission_bps == Decimal("2.5")
    assert second.commission_bps == Decimal("1.8")


def test_statutory_schedule_identity_and_market_sources_do_not_drift():
    schedule = load_a_share_rule_schedule_version("a-share-rules-v2")

    assert schedule.fee_schedule_version == "cn-cash-fees-2023-08-28-v1"
    assert {item.fee_rule_key for item in schedule.fee_templates} == {
        "cn-stock-secondary",
        "cn-etf-secondary",
    }
    authorities = {source.authority for source in schedule.sources}
    assert {
        "ChinaClear Shanghai fee schedule",
        "ChinaClear Shenzhen fee schedule",
        "ChinaClear Beijing fee schedule",
    } <= authorities


def test_apply_resolved_fee_updates_only_candidate_fee_fields():
    original = AShareExecutionRules(
        rules_version="candidate-rules-v2",
        fee_schedule_version="placeholder",
        tick_size="0.001",
        lot_size=100,
        settlement_days=0,
        price_limit_rate="0.20",
        commission_bps="0",
        minimum_commission="0",
        stamp_duty_bps="0",
        transfer_fee_bps="0",
    )
    resolved = _resolve(security_type="etf", side="buy")

    applied = apply_resolved_fee(original, resolved)

    assert applied.rules_version == original.rules_version
    assert applied.tick_size == original.tick_size
    assert applied.settlement_days == original.settlement_days
    assert applied.fee_schedule_version == resolved.fee_schedule_version
    assert applied.commission_bps == resolved.commission_bps


@pytest.mark.parametrize(
    ("security_type", "side", "exchange"),
    [
        ("bond", "buy", "SSE"),
        ("stock", "hold", "SSE"),
        ("stock", "buy", "NYSE"),
        ("etf", "buy", "BSE"),
    ],
)
def test_unknown_fee_dimensions_fail_closed(security_type, side, exchange):
    with pytest.raises(FeePolicyCoverageError):
        _resolve(security_type=security_type, side=side, exchange=exchange)


def test_after_review_date_fails_closed_without_nearest_version_fallback():
    with pytest.raises(FeePolicyCoverageError, match="mandatory review date"):
        _resolve(
            security_type="stock",
            side="buy",
            trade_date=date(2027, 1, 1),
        )


def test_schedule_validator_rejects_fee_overlap_and_trading_gap():
    schedule = load_a_share_rule_schedule_version("a-share-rules-v2")
    overlap = schedule.model_dump(mode="json")
    overlap["fee_templates"].append(dict(overlap["fee_templates"][0]))
    with pytest.raises(ValidationError, match="open interval|intervals overlap"):
        AShareRuleSchedule.model_validate(overlap)

    gap = schedule.model_dump(mode="json")
    for item in gap["trading_rules"]:
        if item["limit_rule_key"] == "sse-main-risk-20260706":
            item["effective_from"] = "2026-07-07"
    with pytest.raises(ValidationError, match="coverage gap"):
        AShareRuleSchedule.model_validate(gap)


def test_policy_rejects_ambiguous_specific_and_all_exchange_rules():
    schedule = load_a_share_rule_schedule_version("a-share-rules-v2")
    duplicate = FeeTemplate.model_validate(
        {**schedule.fee_templates[0].model_dump(), "exchange": "SSE"}
    )
    ambiguous = schedule.model_copy(update={"fee_templates": [*schedule.fee_templates, duplicate]})

    with pytest.raises(FeePolicyCoverageError, match="found 2"):
        VersionedAshareFeePolicy(ambiguous).resolve(
            FeePolicyRequest(
                trade_date=date(2026, 9, 1),
                security_type="stock",
                side="buy",
                exchange="SSE",
                broker_terms=_broker(),
            )
        )


def test_policy_rejects_zero_matching_rules_without_zero_fee_fallback():
    schedule = load_a_share_rule_schedule_version("a-share-rules-v2")
    missing = schedule.model_copy(
        update={
            "fee_templates": [
                item
                for item in schedule.fee_templates
                if not (item.security_type == "stock" and item.side == "buy")
            ]
        }
    )

    with pytest.raises(FeePolicyCoverageError, match="found 0"):
        VersionedAshareFeePolicy(missing).resolve(
            FeePolicyRequest(
                trade_date=date(2026, 9, 1),
                security_type="stock",
                side="buy",
                exchange="SSE",
                broker_terms=_broker(),
            )
        )
