from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel


class AlertRule(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    operator: str
    threshold: Decimal


class Alert(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    status: str
    triggered_at: datetime
    message: str


def evaluate_price_alert(rule: AlertRule, latest_price: Decimal) -> Alert | None:
    if rule.operator == ">=":
        triggered = latest_price >= rule.threshold
    elif rule.operator == "<=":
        triggered = latest_price <= rule.threshold
    else:
        raise ValueError(f"unsupported operator: {rule.operator}")

    if not triggered:
        return None

    return Alert(
        rule_id=rule.rule_id,
        instrument_id=rule.instrument_id,
        kind=rule.kind,
        status="triggered",
        triggered_at=datetime.now(timezone.utc),
        message=f"{rule.kind} triggered at {latest_price}",
    )
