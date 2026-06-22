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


class AlertSuggestion(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    operator: str
    threshold: Decimal
    source_snapshot_id: str
    rationale: str


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


def suggest_alert_rules(snapshots: list) -> list[AlertSuggestion]:
    suggestions: list[AlertSuggestion] = []
    seen: set[tuple[str, str, Decimal]] = set()
    for snapshot in snapshots:
        rows = [
            (
                "entry_trigger",
                ">=",
                getattr(snapshot, "trigger_price", None),
                "Trigger when price confirms the opportunity entry level.",
            ),
            (
                "stop_guard",
                "<=",
                getattr(snapshot, "initial_stop", None),
                "Warn when price invalidates the stored trade plan.",
            ),
            (
                "target_1_reached",
                ">=",
                getattr(snapshot, "target_1", None),
                "Warn when price reaches the first planned target.",
            ),
        ]
        for kind, operator, threshold, rationale in rows:
            if threshold is None:
                continue
            key = (snapshot.instrument_id, kind, threshold)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(
                AlertSuggestion(
                    rule_id=f"{kind}-{snapshot.instrument_id}-{snapshot.snapshot_id[-8:]}",
                    instrument_id=snapshot.instrument_id,
                    kind=kind,
                    operator=operator,
                    threshold=threshold,
                    source_snapshot_id=snapshot.snapshot_id,
                    rationale=rationale,
                )
            )
    return suggestions
