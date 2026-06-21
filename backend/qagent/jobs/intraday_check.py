from decimal import Decimal

from qagent.monitoring.alerts import Alert, AlertRule, evaluate_price_alert


def evaluate_snapshot_alerts(
    prices: dict[str, Decimal], rules: list[AlertRule]
) -> list[Alert]:
    alerts: list[Alert] = []
    for rule in rules:
        latest_price = prices.get(rule.instrument_id)
        if latest_price is None:
            continue
        alert = evaluate_price_alert(rule, latest_price)
        if alert is not None:
            alerts.append(alert)
    return alerts
