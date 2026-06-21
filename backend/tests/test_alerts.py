from decimal import Decimal

from qagent.monitoring.alerts import AlertRule, evaluate_price_alert


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
