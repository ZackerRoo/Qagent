from decimal import Decimal

from qagent.monitoring.alerts import AlertRule, evaluate_price_alert
from qagent.monitoring.alerts import suggest_alert_rules
from qagent.storage.repository import OpportunitySnapshotRecord


def test_entry_alert_triggers_when_price_crosses_above_level():
    rule = AlertRule(
        rule_id="r1",
        instrument_id="US:TEST",
        kind="entry_trigger",
        operator=">=",
        threshold=Decimal("100"),
    )
    alert = evaluate_price_alert(rule, Decimal("101"))
    assert alert is not None
    assert alert.status == "triggered"


def test_stop_alert_does_not_trigger_above_threshold():
    rule = AlertRule(
        rule_id="r2",
        instrument_id="US:TEST",
        kind="stop",
        operator="<=",
        threshold=Decimal("90"),
    )
    assert evaluate_price_alert(rule, Decimal("91")) is None


def test_suggest_alert_rules_from_opportunity_snapshot():
    snapshot = OpportunitySnapshotRecord(
        snapshot_id="snap-1",
        run_id="run-1",
        card_id="card-1",
        instrument_id="US:TEST",
        market="US",
        status="setup_ready",
        signal_date=None,
        latest_close=Decimal("82.00"),
        primary_strategy_id="pead_earnings_drift",
        score=Decimal("0.92"),
        strategy_score=Decimal("0.94"),
        rank_score=Decimal("0.88"),
        trigger_price=Decimal("83.20"),
        initial_stop=Decimal("80.90"),
        target_1=Decimal("89.76"),
        card={"instrument_id": "US:TEST"},
    )

    suggestions = suggest_alert_rules([snapshot])

    by_kind = {item.kind: item for item in suggestions}
    assert by_kind["entry_trigger"].operator == ">="
    assert by_kind["entry_trigger"].threshold == Decimal("83.20")
    assert by_kind["stop_guard"].operator == "<="
    assert by_kind["stop_guard"].threshold == Decimal("80.90")
    assert by_kind["target_1_reached"].threshold == Decimal("89.76")
    assert by_kind["entry_trigger"].source_snapshot_id == "snap-1"
